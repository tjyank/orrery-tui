"""Predicting moon events: eclipses in the planet's shadow, transits and occultations seen from
Earth, and, for Earth's own system, solar and lunar eclipses.

Every event is a window where some smooth separation function f(t) dips below zero (for example,
a moon's distance from the axis of its planet's shadow, minus the shadow's radius). We sample f
on a grid fine enough for the shortest event, refine any dip that sits between samples, then
bisect the contacts. Times are geometric (light travel time is not added).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from .kepler import AU_KM, Vec3

SUN_RADIUS_KM = 695_700.0
ATMOSPHERE = 1.02  # Earth's atmosphere enlarges its shadow by about 2% (the Danjon rule)
SYSTEM_HORIZON_DAYS = 10.0
EARTH_HORIZON_DAYS = 400.0  # long enough to hold the next eclipse of each kind

PosFn = Callable[[float], Vec3]  # Julian date -> position (au)


@dataclass(frozen=True)
class Event:
    kind: str  # eclipse | transit | occultation | lunar-eclipse | solar-eclipse
    body: str  # the moon's key
    name: str  # the moon's name
    start: float | None  # None: already under way when the search began
    end: float | None  # None: still going at the end of the search window
    peak: float  # time of greatest depth
    detail: str = ""  # total | partial | annular, for eclipses of and by the Moon

    def title(self, planet_name: str) -> str:
        if self.kind == "eclipse":
            return f"{self.name} in {planet_name}'s shadow"
        if self.kind == "transit":
            return f"{self.name} transits {planet_name}"
        if self.kind == "occultation":
            return f"{self.name} behind {planet_name}"
        return f"{self.detail.capitalize()} {'lunar' if self.kind == 'lunar-eclipse' else 'solar'} eclipse"

    def begins(self) -> float:
        return self.start if self.start is not None else self.peak


# ---- the root finder ----------------------------------------------------------------------

def _golden_min(f, a: float, b: float, iters: int = 40) -> tuple[float, float]:
    g = (math.sqrt(5) - 1) / 2
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(iters):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = f(d)
    t = (a + b) / 2
    return t, f(t)


def _bisect(f, a: float, b: float, iters: int = 40) -> float:
    """Root of f between a and b, where f(a) and f(b) differ in sign."""
    fa = f(a)
    for _ in range(iters):
        m = (a + b) / 2
        fm = f(m)
        if (fm < 0) == (fa < 0):
            a, fa = m, fm
        else:
            b = m
    return (a + b) / 2


def windows(f, t0: float, t1: float, step: float) -> list[tuple[float | None, float | None, float]]:
    """Intervals within [t0, t1] where f < 0, as (start, end, time of minimum).

    start is None if f < 0 already at t0, end is None if f < 0 still at t1.
    """
    n = max(2, int(math.ceil((t1 - t0) / step)))
    ts = [t0 + (t1 - t0) * k / n for k in range(n + 1)]
    vs = [f(t) for t in ts]
    # A dip shorter than a sample step can hide between two positive samples: look at every local
    # minimum of the samples, and add its true minimum to the samples if that goes negative.
    extra = []
    for i in range(1, n):
        if vs[i] >= 0 and vs[i] <= vs[i - 1] and vs[i] <= vs[i + 1]:
            tm, vm = _golden_min(f, ts[i - 1], ts[i + 1])
            if vm < 0:
                extra.append((tm, vm))
    if extra:
        merged = sorted(list(zip(ts, vs)) + extra)
        ts, vs = [t for t, _ in merged], [v for _, v in merged]

    out = []
    i = 0
    while i < len(ts):
        if vs[i] >= 0:
            i += 1
            continue
        j = i
        while j + 1 < len(ts) and vs[j + 1] < 0:
            j += 1
        start = _bisect(f, ts[i - 1], ts[i]) if i > 0 else None
        end = _bisect(f, ts[j], ts[j + 1]) if j + 1 < len(ts) else None
        lo, hi = ts[max(i - 1, 0)], ts[min(j + 1, len(ts) - 1)]
        peak, _ = _golden_min(f, lo, hi)
        out.append((start, end, peak))
        i = j + 1
    return out


# ---- geometry -------------------------------------------------------------------------------

def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def _axis_offset(x: Vec3, axis: Vec3) -> tuple[float, float]:
    """(distance along a unit axis, distance from it) of point x."""
    along = _dot(x, axis)
    return along, math.sqrt(max(0.0, _dot(x, x) - along * along))


def shadow_margin(x_km: Vec3, planet_helio_au: Vec3, radius_km: float, enlarge: float = 1.0) -> float:
    """How far a planetocentric point (km) is outside the planet's umbra (negative: inside).

    The umbra is a cone narrowing away from the Sun. On the day side we fall back to the distance
    above the surface, which joins smoothly at the terminator plane.
    """
    d_sun = _norm(planet_helio_au) * AU_KM
    anti_sun = tuple(c / _norm(planet_helio_au) for c in planet_helio_au)
    behind, off = _axis_offset(x_km, anti_sun)
    if behind <= 0:
        return math.sqrt(behind * behind + off * off) - radius_km
    umbra = (radius_km - behind * (SUN_RADIUS_KM - radius_km) / d_sun) * enlarge
    return off - umbra


def umbra_radius(x_km: Vec3, planet_helio_au: Vec3, radius_km: float, enlarge: float = 1.0) -> float:
    d_sun = _norm(planet_helio_au) * AU_KM
    behind = _dot(x_km, tuple(c / _norm(planet_helio_au) for c in planet_helio_au))
    return (radius_km - max(behind, 0.0) * (SUN_RADIUS_KM - radius_km) / d_sun) * enlarge


def in_umbra(x_km: Vec3, planet_helio_au: Vec3, radius_km: float) -> bool:
    return shadow_margin(x_km, planet_helio_au, radius_km) < 0


# ---- searches -------------------------------------------------------------------------------

@dataclass(frozen=True)
class MoonTrack:
    key: str
    name: str
    radius_km: float
    period_days: float
    position: PosFn  # planetocentric, au


def system_events(planet_key: str, planet_radius_km: float, planet_pos: PosFn, earth_pos: PosFn,
                  moons: list[MoonTrack], t0: float, horizon: float = SYSTEM_HORIZON_DAYS) -> list[Event]:
    """Eclipses, transits and occultations of a planet's moons over [t0, t0 + horizon]."""
    if planet_key == "earth":
        return earth_eclipses(earth_pos, moons[0].position, t0, horizon) if moons else []
    t1 = t0 + horizon
    out: list[Event] = []
    for m in moons:
        step = min(max(m.period_days / 150, 1 / 720), 0.25)  # 2 min .. 6 h

        def shadow(t, m=m):
            x = tuple(c * AU_KM for c in m.position(t))
            return shadow_margin(x, planet_pos(t), planet_radius_km)

        def disc(t, m=m):  # distance from the Earth-planet sight line, minus the planet's radius
            p = planet_pos(t)
            los = _sub(p, earth_pos(t))
            n = _norm(los)
            _, off = _axis_offset(tuple(c * AU_KM for c in m.position(t)), (los[0] / n, los[1] / n, los[2] / n))
            return off - planet_radius_km

        for start, end, peak in windows(shadow, t0, t1, step):
            out.append(Event("eclipse", m.key, m.name, start, end, peak))
        for start, end, peak in windows(disc, t0, t1, step):
            p = planet_pos(peak)
            los = _sub(p, earth_pos(peak))
            beyond = _dot(m.position(peak), los) > 0  # farther than the planet: hidden behind it
            out.append(Event("occultation" if beyond else "transit", m.key, m.name, start, end, peak))
    out.sort(key=Event.begins)
    return out


MOON_RADIUS_KM = 1_737.4
EARTH_RADIUS_KM = 6_371.0


def earth_eclipses(earth_pos: PosFn, moon_pos: PosFn, t0: float, horizon: float = EARTH_HORIZON_DAYS) -> list[Event]:
    """Lunar eclipses (umbral: partial or total) and solar eclipses (partial, annular or total)."""
    t1 = t0 + horizon
    step = 0.25  # each eclipse is a single smooth dip a day or so wide, around full or new moon

    def lunar(t):  # the Moon's limb touching the umbra
        x = tuple(c * AU_KM for c in moon_pos(t))
        return shadow_margin(x, earth_pos(t), EARTH_RADIUS_KM, ATMOSPHERE) - MOON_RADIUS_KM

    def solar_geometry(t):
        m = tuple(c * AU_KM for c in moon_pos(t))
        sun = tuple(-c * AU_KM for c in earth_pos(t))  # geocentric Sun
        axis = _sub(m, sun)
        d_sm = _norm(axis)
        axis = (axis[0] / d_sm, axis[1] / d_sm, axis[2] / d_sm)
        behind, off = _axis_offset(tuple(-c for c in m), axis)  # Earth's centre, seen from the Moon
        if behind < 0:  # Earth is sunward of the Moon (full moon): nowhere near the shadow.
            behind, off = 0.0, _norm(m)  # joins the axis distance smoothly at behind = 0
        pen = MOON_RADIUS_KM + behind * (SUN_RADIUS_KM + MOON_RADIUS_KM) / d_sm
        umb = MOON_RADIUS_KM - behind * (SUN_RADIUS_KM - MOON_RADIUS_KM) / d_sm  # < 0: antumbra
        return off, pen, umb

    def solar(t):  # the penumbra touching Earth
        off, pen, _ = solar_geometry(t)
        return off - (EARTH_RADIUS_KM + pen)

    out = []
    for start, end, peak in windows(lunar, t0, t1, step):
        x = tuple(c * AU_KM for c in moon_pos(peak))
        _, off = _axis_offset(x, tuple(c / _norm(earth_pos(peak)) for c in earth_pos(peak)))
        total = off < umbra_radius(x, earth_pos(peak), EARTH_RADIUS_KM, ATMOSPHERE) - MOON_RADIUS_KM
        out.append(Event("lunar-eclipse", "moon", "Moon", start, end, peak, "total" if total else "partial"))
    for start, end, peak in windows(solar, t0, t1, step):
        off, _, umb = solar_geometry(peak)
        if off < EARTH_RADIUS_KM + abs(umb):
            detail = "total" if umb > 0 else "annular"
        else:
            detail = "partial"
        out.append(Event("solar-eclipse", "moon", "Moon", start, end, peak, detail))
    out.sort(key=Event.begins)
    return out
