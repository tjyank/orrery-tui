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
from datetime import datetime, timezone
from pathlib import Path

from .bodies import PLANETS, Body, approximate_orbit
from .kepler import Orbit, orbit_from_state

try:  # Use the OS certificate store (needed behind TLS-inspecting proxies on Windows).
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
CACHE_FILE = Path(os.environ.get("ORRERY_CACHE", Path.home() / ".cache" / "orrery" / "horizons.json"))
CACHE_MAX_AGE_DAYS = 45  # cached osculating elements are fine within ~a month


def jd_now() -> float:
    return time.time() / 86400.0 + 2440587.5


def jd_to_datetime(jd: float) -> datetime:
    return datetime.fromtimestamp((jd - 2440587.5) * 86400.0, tz=timezone.utc)


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
        }[self.source]


def _fetch_state(body: Body, jd: float, timeout: float, retries: int = 3) -> tuple[tuple, tuple]:
    params = {
        "format": "json",
        "COMMAND": f"'{body.horizons_id}'",
        "EPHEM_TYPE": "VECTORS",
        "CENTER": "'500@10'",  # Sun body center
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
        raise RuntimeError(f"{body.name}: {payload['error']}")
    text = payload["result"]
    try:
        row = text.split("$$SOE", 1)[1].split("$$EOE", 1)[0].strip().splitlines()[0]
    except IndexError:
        raise RuntimeError(f"{body.name}: unexpected Horizons response") from None
    cols = [c.strip() for c in row.split(",")]
    x, y, z, vx, vy, vz = (float(c) for c in cols[2:8])
    return (x, y, z), (vx, vy, vz)


def fetch_horizons(jd: float, timeout: float = 20.0, progress=None) -> Snapshot:
    # Sequential on purpose: Horizons rejects concurrent requests from one client with 503s.
    states = {}
    for n, body in enumerate(PLANETS):
        if progress:
            progress(n, len(PLANETS))
        states[body.key] = _fetch_state(body, jd, timeout)
    _save_cache(jd, states)
    orbits = {b.key: orbit_from_state(*states[b.key], epoch=jd, mu=b.mu) for b in PLANETS}
    return Snapshot(orbits, jd, "live")


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
        orbits = {
            b.key: orbit_from_state(tuple(data["states"][b.key][0]), tuple(data["states"][b.key][1]), epoch, b.mu)
            for b in PLANETS
        }
        mtime = datetime.fromtimestamp(CACHE_FILE.stat().st_mtime, tz=timezone.utc)
        return Snapshot(orbits, epoch, "cache", fetched_at=mtime)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def offline_snapshot(jd: float) -> Snapshot:
    return Snapshot({b.key: approximate_orbit(b, jd) for b in PLANETS}, jd, "offline")


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


class EphemerisService:
    """Fetches snapshots on a background thread so the UI never blocks on the network."""

    def __init__(self, allow_network: bool = True):
        self.allow_network = allow_network
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
            snap = best_snapshot(jd, self.allow_network, self._progress)
        except Exception as exc:  # never let the worker die silently
            snap = offline_snapshot(jd)
            snap.error = str(exc)
        with self._lock:
            self._latest = snap
            self._busy = False

    def take(self) -> Snapshot | None:
        """Return a newly arrived snapshot once, else None."""
        with self._lock:
            snap, self._latest = self._latest, None
        return snap
