"""Live ephemeris from NASA/JPL Horizons, with an on-disk cache and an offline fallback."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .bodies import BY_KEY, ORBITING, approximate_orbit
from .kepler import Orbit, orbit_from_state
from .moons import MOONS_OF, mean_moon_orbit

try:  # Use the OS certificate store (needed behind TLS-inspecting proxies on Windows).
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"


def _default_cache_dir(os_name: str = os.name, env=os.environ) -> Path:
    if os_name == "nt" and env.get("LOCALAPPDATA"):
        return Path(env["LOCALAPPDATA"]) / "orrery"  # where Windows apps keep caches
    return Path(env.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "orrery"


CACHE_FILE = Path(os.environ.get("ORRERY_CACHE", _default_cache_dir() / "horizons.json"))
CACHE_MAX_AGE_DAYS = 45  # cached osculating elements are fine within ~a month
MOON_CACHE_FILE = CACHE_FILE.with_name("horizons-moons.json")
MOON_CACHE_MAX_AGE_DAYS = 5  # moon orbits are perturbed much faster than planetary ones
# Dwarf planets, asteroids and comets move slowly and their orbits change little: re-fetch them only
# once simulated time is this far from their data. Routine refreshes then fetch just the 9 planets.
SMALL_BODY_REFRESH_DAYS = 10

# Horizons answers simultaneous requests from one client with HTTP 503, and the planets and each
# moon system fetch on their own threads. One lock per request makes them take turns, request by
# request, so no fetch waits for another's whole batch.
_HORIZONS_LOCK = threading.Lock()
_MOON_CACHE_LOCK = threading.Lock()  # its read-modify-write must not interleave between threads


def _write_json_atomic(path: Path, data) -> None:
    """Write whole or not at all: a crash or a concurrent reader never sees half a file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)
    except OSError:
        pass  # a cache we can't write is no reason to fail


def jd_now() -> float:
    return time.time() / 86400.0 + 2440587.5


_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def jd_to_datetime(jd: float) -> datetime:
    # Not datetime.fromtimestamp: on Windows that rejects dates before 1970.
    return _UNIX_EPOCH + timedelta(days=jd - 2440587.5)


@dataclass
class Snapshot:
    orbits: dict[str, Orbit]
    epoch: float
    source: str  # "live" | "cache" | "offline"
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: str | None = None

    @property
    def label(self) -> str:
        return {
            "live": "LIVE  JPL Horizons",
            "cache": "CACHED JPL Horizons",
            "offline": "OFFLINE Kepler model",
            "analytic": "ANALYTIC lunar theory (ELP/Meeus)",
        }[self.source]


def _fetch_state(horizons_id: str, name: str, jd: float, timeout: float, center: str = "10",
                 retries: int = 3) -> tuple[tuple, tuple]:
    params = {
        "format": "json",
        "COMMAND": f"'{horizons_id}'",
        "EPHEM_TYPE": "VECTORS",
        "CENTER": f"'500@{center}'",  # body centre (the Sun unless asked otherwise)
        "REF_PLANE": "ECLIPTIC",
        "REF_SYSTEM": "J2000",
        "TLIST": f"'{jd:.6f}'",
        "TLIST_TYPE": "JD",
        "VEC_TABLE": "2",
        "OUT_UNITS": "AU-D",
        "CSV_FORMAT": "YES",
        "OBJ_DATA": "NO",
        "MAKE_EPHEM": "YES",
    }
    url = f"{HORIZONS_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "orrery-tui/0.1"})
    for attempt in range(retries + 1):
        try:
            with _HORIZONS_LOCK, urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.load(resp)
            break
        except urllib.error.HTTPError as exc:  # Horizons answers 503 when busy
            if exc.code != 503 or attempt == retries:
                raise
            time.sleep(1.5 * (attempt + 1))
    return parse_vectors(payload, name)


def parse_vectors(payload: dict, name: str) -> tuple[tuple, tuple]:
    """Position (au) and velocity (au/day) from a Horizons VECTORS reply (JSON, CSV table)."""
    if "error" in payload:
        raise RuntimeError(f"{name}: {payload['error']}")
    text = payload.get("result")
    try:
        row = text.split("$$SOE", 1)[1].split("$$EOE", 1)[0].strip().splitlines()[0]
        cols = [c.strip() for c in row.split(",")]
        x, y, z, vx, vy, vz = (float(c) for c in cols[2:8])
    except (AttributeError, IndexError, ValueError):
        raise RuntimeError(f"{name}: unexpected Horizons response") from None
    return (x, y, z), (vx, vy, vz)


PLANET_BODIES = [b for b in ORBITING if not b.small]
SMALL_BODIES = [b for b in ORBITING if b.small]


def fetch_horizons(jd: float, timeout: float = 20.0, progress=None, publish=None) -> Snapshot:
    """Planets first; then, if their cached data is old for `jd`, the small bodies.

    `publish(snapshot)`, if given, receives the planets as soon as they're in (with the small bodies
    from the cache or the offline model), so the view updates before the slower second stage.
    """
    small_cached = _load_small_states()
    need_small = small_cached is None or abs(jd - small_cached[0]) > SMALL_BODY_REFRESH_DAYS
    total = len(PLANET_BODIES) + (len(SMALL_BODIES) if need_small else 0)
    states = {}
    for n, body in enumerate(PLANET_BODIES):
        if progress:
            progress(n, total)
        states[body.key] = _fetch_state(body.horizons_id, body.name, jd, timeout, body.center)
    if not need_small:
        _save_cache(jd, states, *small_cached)
        return Snapshot(_orbits_from_states(states, jd, *small_cached), jd, "live")

    if publish:
        publish(Snapshot(_orbits_from_states(states, jd, *(small_cached or (jd, {}))), jd, "live"))
    small_states = {}
    for n, body in enumerate(SMALL_BODIES, len(PLANET_BODIES)):
        if progress:
            progress(n, total)
        try:
            small_states[body.key] = _fetch_state(body.horizons_id, body.name, jd, timeout, body.center)
        except Exception:
            pass  # one small body Horizons can't resolve shouldn't sink the rest: its offline orbit fills in
    _save_cache(jd, states, jd, small_states)
    return Snapshot(_orbits_from_states(states, jd, jd, small_states), jd, "live")


def _orbits_from_states(states: dict, epoch: float, small_epoch: float, small_states: dict) -> dict[str, Orbit]:
    """Osculating orbits: planets from `states` at `epoch` (all required), small bodies from
    `small_states` at their own epoch, each falling back to its offline orbit when missing."""
    orbits = {}
    for b in PLANET_BODIES:
        r, v = states[b.key]
        orbits[b.key] = orbit_from_state(tuple(r), tuple(v), epoch, b.mu)
    for b in SMALL_BODIES:
        try:
            r, v = small_states[b.key]
            orbits[b.key] = orbit_from_state(tuple(r), tuple(v), small_epoch, b.mu)
        except (KeyError, ValueError, TypeError):
            orbits[b.key] = approximate_orbit(b, epoch)
    return orbits


def _save_cache(jd: float, states: dict, small_epoch: float, small_states: dict) -> None:
    _write_json_atomic(CACHE_FILE, {"epoch": jd, "states": states,
                                    "small": {"epoch": small_epoch, "states": small_states}})


def _read_cache() -> dict | None:
    try:
        data = json.loads(CACHE_FILE.read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _load_small_states() -> tuple[float, dict] | None:
    """The cached small-body states and their epoch, if any."""
    data = _read_cache()
    try:
        if "small" in data:
            return float(data["small"]["epoch"]), dict(data["small"]["states"])
        # Older caches kept everything together, at the planets' epoch.
        small = {b.key: data["states"][b.key] for b in SMALL_BODIES if b.key in data["states"]}
        return (float(data["epoch"]), small) if small else None
    except (KeyError, ValueError, TypeError):
        return None


def load_cache(jd: float) -> Snapshot | None:
    data = _read_cache()
    try:
        epoch = float(data["epoch"])
        if abs(jd - epoch) > CACHE_MAX_AGE_DAYS:
            return None
        small_epoch, small_states = _load_small_states() or (epoch, {})
        if abs(jd - small_epoch) > CACHE_MAX_AGE_DAYS:
            small_states = {}  # too old to trust: the offline orbits do better
        orbits = _orbits_from_states(data["states"], epoch, small_epoch, small_states)
        mtime = datetime.fromtimestamp(CACHE_FILE.stat().st_mtime, tz=timezone.utc)
        return Snapshot(orbits, epoch, "cache", fetched_at=mtime)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def offline_snapshot(jd: float) -> Snapshot:
    return Snapshot({b.key: approximate_orbit(b, jd) for b in ORBITING}, jd, "offline")


def best_snapshot(jd: float, allow_network: bool = True, progress=None, publish=None) -> Snapshot:
    """Live data if reachable, else a recent cache, else the analytic model."""
    error = None
    if allow_network:
        try:
            return fetch_horizons(jd, progress=progress, publish=publish)
        except Exception as exc:  # network, TLS, HTTP or parse failure
            error = str(exc) or type(exc).__name__
    snap = load_cache(jd) or offline_snapshot(jd)
    snap.error = error
    return snap


# ---- moons: planetocentric ecliptic J2000 orbits, one planetary system at a time --------------

def fetch_moons(planet: str, jd: float, timeout: float = 20.0, progress=None) -> Snapshot:
    moons = MOONS_OF[planet]
    center = BY_KEY[planet].horizons_id
    states = {}
    for n, moon in enumerate(moons):
        if progress:
            progress(n, len(moons))
        states[moon.key] = _fetch_state(moon.horizons_id, moon.name, jd, timeout, moon.center or center)
    _save_moon_cache(planet, jd, states)
    orbits = {m.key: orbit_from_state(*states[m.key], epoch=jd, mu=m.mu) for m in moons}
    return Snapshot(orbits, jd, "live")


def _read_moon_cache() -> dict:
    try:
        data = json.loads(MOON_CACHE_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_moon_cache(planet: str, jd: float, states: dict) -> None:
    # Every planet's moons share one file: update it under the lock, or two systems finishing
    # together could each write back a copy missing the other's entry.
    with _MOON_CACHE_LOCK:
        data = _read_moon_cache()
        data[planet] = {"epoch": jd, "states": states}
        _write_json_atomic(MOON_CACHE_FILE, data)


def load_moon_cache(planet: str, jd: float) -> Snapshot | None:
    try:
        entry = _read_moon_cache()[planet]
        epoch = float(entry["epoch"])
        if abs(jd - epoch) > MOON_CACHE_MAX_AGE_DAYS:
            return None
        orbits = {
            m.key: orbit_from_state(tuple(entry["states"][m.key][0]), tuple(entry["states"][m.key][1]), epoch, m.mu)
            for m in MOONS_OF[planet]
        }
        mtime = datetime.fromtimestamp(MOON_CACHE_FILE.stat().st_mtime, tz=timezone.utc)
        return Snapshot(orbits, epoch, "cache", fetched_at=mtime)
    except (KeyError, ValueError, TypeError, OSError):
        return None


def offline_moon_snapshot(planet: str, jd: float) -> Snapshot:
    return Snapshot({m.key: mean_moon_orbit(m) for m in MOONS_OF[planet]}, jd, "offline")


def best_moon_snapshot(planet: str, jd: float, allow_network: bool = True, progress=None, publish=None) -> Snapshot:
    error = None
    if allow_network and MOONS_OF[planet]:
        try:
            return fetch_moons(planet, jd, progress=progress)
        except Exception as exc:
            error = str(exc) or type(exc).__name__
    snap = load_moon_cache(planet, jd) or offline_moon_snapshot(planet, jd)
    snap.error = error
    return snap


class EphemerisService:
    """Fetches snapshots on a background thread so the UI never blocks on the network.

    `loader(jd, allow_network, progress, publish)` produces the snapshot (by default, the planets'),
    and may hand over an early partial result through `publish` while it carries on.
    """

    def __init__(self, allow_network: bool = True, loader=best_snapshot):
        self.allow_network = allow_network
        self.loader = loader
        self._lock = threading.Lock()
        self._latest: Snapshot | None = None
        self._busy = False
        self.progress = (0, 0)

    @property
    def busy(self) -> bool:
        return self._busy

    def _progress(self, done: int, total: int) -> None:
        self.progress = (done, total)

    def request(self, jd: float) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True
        threading.Thread(target=self._run, args=(jd,), daemon=True).start()
        return True

    def _publish(self, snap: Snapshot) -> None:
        with self._lock:
            self._latest = snap

    def _run(self, jd: float) -> None:
        try:
            snap = self.loader(jd, self.allow_network, self._progress, self._publish)
        except Exception as exc:  # never let the worker die silently
            snap = self.loader(jd, False, None, None)  # cache or analytic model
            snap.error = str(exc)
        with self._lock:
            self._latest = snap
            self._busy = False

    def take(self) -> Snapshot | None:
        """Return a newly arrived snapshot once, else None."""
        with self._lock:
            snap, self._latest = self._latest, None
        return snap
