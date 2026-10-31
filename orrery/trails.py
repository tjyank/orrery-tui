"""Trails: where each body has been recently, as dots spaced evenly in time.

Even spacing in time makes speed visible (a body racing through perihelion leaves its dots far
apart), and a trail drawn relative to the body being followed shows motion as seen from it: follow
Earth and the outer planets trace their retrograde loops.
"""
from __future__ import annotations

from typing import Callable

from .kepler import Vec3

TRAIL_REAL_SECONDS = 15.0  # a trail covers the simulated time of this many real seconds at the current speed
TRAIL_SAMPLES = 60
MAX_TRAIL_DAYS = 3 * 365.25  # bounds the work when time runs fastest
ORBIT_FRACTION = 0.95  # without a moving frame, stop short of a full orbit so a trail never laps itself


def trail_span_days(rate: float) -> float:
    """Signed: negative when time runs forward, so the trail reaches back into the past."""
    span = min(abs(rate) * TRAIL_REAL_SECONDS / 86400.0, MAX_TRAIL_DAYS)
    return -span if rate >= 0 else span


def build_trails(keys, now: float, rate: float, position: Callable[[str, float], Vec3],
                 period_days: Callable[[str], float | None],
                 frame: Callable[[float], Vec3] | None = None) -> dict[str, list[Vec3]]:
    """Trail points in display space for each body, oldest first.

    `position(key, t)` gives a body's display-space position at time t. `frame(t)`, if given, is the
    display position of the body being followed: trails are then drawn in its moving frame, pinned
    to where it is now.
    """
    span = trail_span_days(rate)
    if span == 0:
        return {}
    here = frame(now) if frame else None
    out = {}
    for key in keys:
        s = span
        period = period_days(key)
        if frame is None and period:
            s = max(-ORBIT_FRACTION * period, min(ORBIT_FRACTION * period, s))
        times = [now + s * (1 - k / TRAIL_SAMPLES) for k in range(TRAIL_SAMPLES)]  # oldest first, excluding now
        pts = []
        for t in times:
            p = position(key, t)
            if frame is not None:
                f = frame(t)
                p = (p[0] - f[0] + here[0], p[1] - f[1] + here[1], p[2] - f[2] + here[2])
            pts.append(p)
        out[key] = pts
    return out
