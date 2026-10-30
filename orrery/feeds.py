"""Background data feeds for the moon view: moon orbits, and upcoming moon events."""
from __future__ import annotations

import math
import threading
import time
from functools import partial

from .ephemeris import EphemerisService, Snapshot, best_moon_snapshot, jd_now, load_moon_cache, offline_moon_snapshot
from .events import EARTH_HORIZON_DAYS, Event, MoonTrack, SYSTEM_HORIZON_DAYS, system_events
from .kepler import Orbit, Vec3, orbit_from_state
from .lunar import earth_heliocentric, moon_geocentric, moon_state
from .moons import MOONS_OF, MOON_BY_KEY


REANCHOR_DRIFT_DAYS = 20  # re-fetch Horizons once simulated time drifts this far from the data epoch
REFRESH_EVERY_S = 15 * 60  # refresh live data this often while tracking the present
MIN_FETCH_GAP_S = 30  # never hit JPL more often than this (except on manual refresh)
MOON_REANCHOR_DRIFT_DAYS = 2  # moon orbits are perturbed faster, so re-fetch sooner


class MoonFeed:
    """Moon orbits for one planet: Horizons in the background, cache or mean elements meanwhile.

    Earth's Moon is the exception: it comes from the analytic lunar theory, which beats an
    osculating orbit for anything beyond a few days, and needs no network.
    """

    def __init__(self, planet: str, jd: float, allow_network: bool):
        self.planet = planet
        self.analytic = planet == "earth"
        self.service = EphemerisService(allow_network, loader=partial(best_moon_snapshot, planet))
        if self.analytic:
            self.snapshot = Snapshot({}, jd, "analytic")
        else:
            self.snapshot: Snapshot = load_moon_cache(planet, jd) or offline_moon_snapshot(planet, jd)
        self.last_request = -1e9
        self.last_live = -1e9

    def request(self, jd: float, force: bool = False) -> None:
        now = time.monotonic()
        if self.analytic or not MOONS_OF[self.planet]:
            return
        if force or now - self.last_request >= MIN_FETCH_GAP_S:
            if self.service.request(jd):
                self.last_request = now

    def poll(self, jd: float) -> Snapshot | None:
        """Take a newly arrived snapshot (returned once) and re-fetch when the data goes stale."""
        if self.analytic:
            return None
        snap = self.service.take()
        if snap is not None:
            self.snapshot = snap
            if snap.source == "live":
                self.last_live = time.monotonic()
        tracking_now = abs(jd - jd_now()) < 1
        stale = time.monotonic() - self.last_live > REFRESH_EVERY_S
        drifted = abs(jd - self.snapshot.epoch) > MOON_REANCHOR_DRIFT_DAYS
        if not self.service.busy and (drifted or (tracking_now and stale)):
            self.request(jd)
        return snap

    def reset(self, jd: float) -> None:
        """After a long time jump: the offline orbits until Horizons answers."""
        if not self.analytic:
            self.snapshot = offline_moon_snapshot(self.planet, jd)

    def orbits(self, jd: float) -> dict[str, Orbit]:
        if self.analytic:
            r, v = moon_state(jd)
            return {"moon": orbit_from_state(r, v, jd, MOON_BY_KEY["moon"].mu)}
        return self.snapshot.orbits

    def positions(self, jd: float) -> dict[str, Vec3]:
        if self.analytic:
            return {"moon": moon_geocentric(jd)}
        return {k: o.position(jd) for k, o in self.snapshot.orbits.items()}

    def tracks(self) -> list[MoonTrack]:
        out = []
        for m in MOONS_OF[self.planet]:
            fn = moon_geocentric if self.analytic else self.snapshot.orbits[m.key].position
            out.append(MoonTrack(m.key, m.name, m.radius_km, m.period_days, fn))
        return out


class EventFeed:
    """Upcoming moon events for the open system, searched on a background thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self.key = None  # what the current list was computed for
        self.t0 = 0.0
        self.horizon = 0.0
        self.events: list[Event] = []
        self.busy = False

    def wanted(self, key, jd: float) -> bool:
        """Does the list need (re)computing for this system and data at this time?"""
        if self.busy:
            return False
        if key != self.key:
            return True
        return jd < self.t0 or jd > self.t0 + self.horizon / 2

    def request(self, key, planet: str, radius_km: float, planet_pos, tracks: list[MoonTrack], jd: float,
                wait: bool = False) -> None:
        horizon = EARTH_HORIZON_DAYS if planet == "earth" else SYSTEM_HORIZON_DAYS
        with self._lock:
            if self.busy:
                return
            self.busy = True

        def run():
            try:
                found = system_events(planet, radius_km, planet_pos, earth_heliocentric, tracks, jd, horizon)
            except Exception:  # an event search must never take the UI down
                found = []
            with self._lock:
                self.key, self.t0, self.horizon, self.events, self.busy = key, jd, horizon, found, False

        if wait:
            run()
        else:
            threading.Thread(target=run, daemon=True).start()

    def upcoming(self, jd: float, key=None) -> list[Event]:
        """Events not yet over at `jd`, soonest first; empty if computed for other data than `key`."""
        with self._lock:
            if key is not None and key != self.key:
                return []
            return [e for e in self.events if (e.end if e.end is not None else math.inf) >= jd]
