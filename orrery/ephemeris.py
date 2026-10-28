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
CACHE_FILE = Path(os.environ.get("ORRERY_CACHE", Path.home() / ".cache" / "orrery" / "horizons.json"))
CACHE_MAX_AGE_DAYS = 45  # cached osculating elements are fine within ~a month
MOON_CACHE_FILE = CACHE_FILE.with_name("horizons-moons.json")
MOON_CACHE_MAX_AGE_DAYS = 5  # moon orbits are perturbed much faster than planetary ones


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
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.load(resp)
            break
        except urllib.error.HTTPError as exc:  # Horizons answers 503 when busy or hit concurrently
            if exc.code != 503 or attempt == retries:
                raise
            time.sleep(1.5 * (attempt + 1))
    if "error" in payload:
        raise RuntimeError(f"{name}: {payload['error']}")
    text = payload["result"]
    try:
        row = text.split("$$SOE", 1)[1].split("$$EOE", 1)[0].strip().splitlines()[0]
    except IndexError:
        raise RuntimeError(f"{name}: unexpected Horizons response") from None
    cols = [c.strip() for c in row.split(",")]
    x, y, z, vx, vy, vz = (float(c) for c in cols[2:8])
    return (x, y, z), (vx, vy, vz)


def fetch_horizons(jd: float, timeout: float = 20.0, progress=None) -> Snapshot:
    # Sequential on purpose: Horizons rejects concurrent requests from one client with 503s.
    states = {}
    for n, body in enumerate(ORBITING):
        if progress:
            progress(n, len(ORBITING))
        try:
            states[body.key] = _fetch_state(body.horizons_id, body.name, jd, timeout, body.center)
        except Exception:
            if not body.small:
                raise
            # A small body Horizons can't resolve shouldn't sink the planets: the offline orbit fills in.
    _save_cache(jd, states)
    return Snapshot(_orbits_from_states(states, jd), jd, "live")


def _orbits_from_states(states: dict, epoch: float) -> dict[str, Orbit]:
    """Osculating orbits for every body; any body without a usable state gets its offline orbit."""
    orbits = {}
    for b in ORBITING:
        try:
            r, v = states[b.key]
            orbits[b.key] = orbit_from_state(tuple(r), tuple(v), epoch, b.mu)
        except (KeyError, ValueError, TypeError):
            if not b.small:
                raise KeyError(b.key)
            orbits[b.key] = approximate_orbit(b, epoch)
    return orbits


def _save_cache(jd: float, states: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({"epoch": jd, "states": states}))
    except OSError:
        pass


def load_cache(jd: float) -> Snapshot | None:
    try:
        data = json.loads(CACHE_FILE.read_text())
        epoch = float(data["epoch"])
        if abs(jd - epoch) > CACHE_MAX_AGE_DAYS:
            return None
        orbits = _orbits_from_states(data["states"], epoch)
        mtime = datetime.fromtimestamp(CACHE_FILE.stat().st_mtime, tz=timezone.utc)
        return Snapshot(orbits, epoch, "cache", fetched_at=mtime)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def offline_snapshot(jd: float) -> Snapshot:
    return Snapshot({b.key: approximate_orbit(b, jd) for b in ORBITING}, jd, "offline")


def best_snapshot(jd: float, allow_network: bool = True, progress=None) -> Snapshot:
    """Live data if reachable, else a recent cache, else the analytic model."""
    error = None
    if allow_network:
        try:
            return fetch_horizons(jd, progress=progress)
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
    data = _read_moon_cache()
    data[planet] = {"epoch": jd, "states": states}
    try:
        MOON_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        MOON_CACHE_FILE.write_text(json.dumps(data))
    except OSError:
        pass


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


def best_moon_snapshot(planet: str, jd: float, allow_network: bool = True, progress=None) -> Snapshot:
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

    `loader(jd, allow_network, progress)` produces the snapshot; by default the planets.
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

    def _run(self, jd: float) -> None:
        try:
            snap = self.loader(jd, self.allow_network, self._progress)
        except Exception as exc:  # never let the worker die silently
            snap = self.loader(jd, False, None)  # cache or analytic model
            snap.error = str(exc)
        with self._lock:
            self._latest = snap
            self._busy = False

    def take(self) -> Snapshot | None:
        """Return a newly arrived snapshot once, else None."""
        with self._lock:
            snap, self._latest = self._latest, None
        return snap
