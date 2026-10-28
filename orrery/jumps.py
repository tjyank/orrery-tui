"""Jump targets: typed dates, and preset moments worth seeing (eclipses, oppositions, flybys)."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .events import earth_eclipses, windows
from .kepler import DAY_S, Orbit, Vec3
from .lunar import earth_heliocentric, moon_geocentric

PosFn = Callable[[str, float], Vec3]  # (body key, Julian date) -> heliocentric position, au


def datetime_to_jd(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() / DAY_S + 2440587.5


_RELATIVE = re.compile(r"^([+-])\s*(\d+(?:\.\d+)?)\s*([hdwmy])$")
_UNIT_DAYS = {"h": 1 / 24, "d": 1, "w": 7, "m": 30.436875, "y": 365.25}


def parse_when(text: str, now_jd: float) -> float:
    """A UTC date/time ('2061-07-28', '1989-08-25 03:56') or an offset from now ('+30d', '-2y').

    Raises ValueError if it can't be read.
    """
    text = text.strip()
    m = _RELATIVE.match(text.lower())
    if m:
        sign = 1 if m.group(1) == "+" else -1
        return now_jd + sign * float(m.group(2)) * _UNIT_DAYS[m.group(3)]
    return datetime_to_jd(datetime.fromisoformat(text.replace("/", "-")))


def _wrap(a: float) -> float:
    return math.remainder(a, math.tau)


def next_opposition(pos: PosFn, planet: str, t0: float, years: float = 3.0) -> float | None:
    """When the Earth next passes between the Sun and a planet (equal heliocentric longitudes)."""
    def dlon(t):
        p, e = pos(planet, t), earth_heliocentric(t)
        return _wrap(math.atan2(p[1], p[0]) - math.atan2(e[1], e[0]))

    # |Δλ| dips to zero once per synodic period: treat |Δλ| - tiny as a window and take its centre.
    hits = windows(lambda t: abs(dlon(t)) - 1e-6, t0 + 1e-3, t0 + years * 365.25, 5.0)
    return hits[0][2] if hits else None


def next_perihelion(orbit: Orbit, t0: float) -> float:
    m = (orbit.m0 + orbit.mean_motion * (t0 - orbit.epoch)) % math.tau
    return t0 + (math.tau - m) / orbit.mean_motion


def next_eclipse(kind: str, t0: float, total_only: bool = False, years: float = 4.0) -> float | None:
    """Peak of the next lunar-eclipse / solar-eclipse (any umbral one, or only total)."""
    chunk = 400.0
    t = t0 + 1e-3
    while t < t0 + years * 365.25:  # in chunks, so near eclipses come back fast
        for ev in earth_eclipses(earth_heliocentric, moon_geocentric, t, chunk):
            if ev.kind == kind and ev.peak > t0 + 1e-3 and (not total_only or ev.detail == "total"):
                return ev.peak
        t += chunk
    return None


@dataclass(frozen=True)
class Preset:
    label: str
    when: Callable[[float], float | None]  # current sim JD -> target JD (None: none found)
    select: str | None = None  # body to select
    system: str | None = None  # planet whose moon system to open
    rate: int | None = None  # simulated seconds per real second after jumping


def presets(pos: PosFn, orbits: dict[str, Orbit]) -> list[Preset]:
    fixed = lambda iso: (lambda _t: datetime_to_jd(datetime.fromisoformat(iso)))  # noqa: E731
    return [
        Preset("Next total solar eclipse", lambda t: next_eclipse("solar-eclipse", t, True), "earth", "earth", 60),
        Preset("Next lunar eclipse", lambda t: next_eclipse("lunar-eclipse", t), "earth", "earth", 60),
        Preset("Next Mars opposition", lambda t: next_opposition(pos, "mars", t), "mars"),
        Preset("Next Jupiter opposition", lambda t: next_opposition(pos, "jupiter", t), "jupiter"),
        Preset("Halley's next perihelion", lambda t: next_perihelion(orbits["halley"], t), "halley"),
        Preset("Apollo 11 lands  1969-07-20", fixed("1969-07-20T20:17:40"), "earth", "earth"),
        Preset("Voyager 2 at Neptune  1989-08-25", fixed("1989-08-25T03:56:00"), "neptune", "neptune"),
        Preset("Comet SL9 hits Jupiter  1994-07-16", fixed("1994-07-16T20:13:00"), "jupiter", "jupiter"),
        Preset("Great conjunction  2020-12-21", fixed("2020-12-21T18:20:00"), "jupiter"),
        Preset("Total solar eclipse  2024-04-08", fixed("2024-04-08T18:17:00"), "earth", "earth", 60),
    ]
