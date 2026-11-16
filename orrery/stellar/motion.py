"""Stellar motion: proper motion plus radial velocity, as a straight-line 3D velocity.

Each star moves at constant velocity relative to the Sun. Over tens of thousands of years that is
a good approximation; past about 100,000 years the stars' curved orbits around the Galaxy (and
close passes between them) matter, so the time slider stops there.
"""
from __future__ import annotations

import math

from .coords import Vec3, parse_dec, parse_ra, to_galactic

EPOCH = 2000.0  # the catalog positions are J2000
MAX_YEARS = 100_000  # how far from the present the slider reaches, each way
MAS = math.radians(1 / 3_600_000)  # one milliarcsecond, in radians
KMS_TO_LY_PER_YR = 1 / 299_792.458  # 1 ly/yr is the speed of light

# Proper motion in RA (already multiplied by cos Dec) and Dec, in mas/yr, and radial velocity in
# km/s (positive: receding; None: not measured). Multiple systems use their primary's values.
MOTION: dict[str, tuple[float, float, float | None]] = {
    "alphacen": (-3679.3, 473.7, -22.3),
    "proxima": (-3781.3, 769.8, -22.4),
    "barnard": (-801.6, 10362.5, -110.5),
    "luhman16": (-2759.0, 354.0, 20.1),
    "wise0855": (-8118.0, 673.0, None),
    "wolf359": (-3842.0, -2725.0, 19.6),
    "lalande21185": (-580.3, -4766.0, -85.0),
    "sirius": (-546.0, -1223.1, -5.5),
    "luyten7268": (3321.0, 562.0, 29.0),
    "ross154": (637.0, -191.6, -10.7),
    "ross248": (115.1, -1592.8, -77.7),
    "epseri": (-975.2, 19.5, 16.4),
    "lacaille9352": (6766.6, 1327.6, 8.8),
    "ross128": (607.3, -1223.0, -31.0),
    "ezaqr": (2314.0, 2295.0, -60.6),
    "61cyg": (4164.2, 3249.9, -65.7),
    "procyon": (-714.6, -1036.8, -3.2),
    "struve2398": (-1311.7, 1792.3, -0.8),
    "groombridge34": (2891.5, 411.8, 11.6),
    "dxcnc": (-1113.0, -611.0, 9.0),
    "epsind": (3966.7, -2536.2, -40.4),
    "tauceti": (-1721.7, 854.2, -16.7),
    "gj1061": (745.7, -373.3, -20.0),
    "yzceti": (1205.3, 637.5, 28.0),
    "luyten": (571.2, -3693.5, 18.2),
    "teegarden": (3429.0, -3806.0, 68.0),
    "kapteyn": (6505.0, -5731.0, 245.2),
    "lacaille8760": (-3258.6, -1145.3, 20.6),
    "scr1845": (2664.0, 619.0, None),
    "kruger60": (-725.0, -223.0, -33.9),
    "wolf1061": (-94.0, -1184.0, -20.6),
    "wolf424": (-1726.0, 252.0, -5.0),
    "vanmaanen": (1231.3, -2711.8, 54.0),
    "gj1": (5634.0, -2337.0, 23.6),
    "tzari": (1096.0, -1771.0, -29.0),
    "gj687": (-320.7, -1269.6, -28.7),
    "lhs292": (-546.0, -1556.0, None),
    "gj674": (572.0, -880.0, -2.7),
    "gj876": (960.3, -675.6, -1.5),
    "gj832": (45.9, -817.8, 13.4),
    "40eri": (-2240.1, -3420.3, -42.3),
    "70oph": (124.6, -962.6, -7.0),
    "altair": (536.2, 385.3, -26.1),
    "sigdra": (598.4, -1738.4, 26.8),
    "etacas": (1087.1, -559.5, 8.4),
    "36oph": (-480.0, -1136.0, -0.3),
    "82eri": (3038.3, 726.6, 87.6),
    "delpav": (1210.7, -1130.3, -21.7),
    "vega": (200.9, 286.2, -13.9),
    "fomalhaut": (329.0, -164.7, 6.5),
    "pollux": (-626.6, -45.8, 3.2),
    "arcturus": (-1093.4, -2000.1, -5.2),
    "capella": (75.5, -427.1, 29.2),
    "aldebaran": (63.5, -188.9, 54.3),
}

_ZERO = (0.0, 0.0, 0.0)
_velocity_cache: dict[str, Vec3] = {}


def velocity(system) -> Vec3:
    """Galactic Cartesian velocity in ly/yr, relative to the Sun."""
    v = _velocity_cache.get(system.key)
    if v is None:
        v = _velocity_cache[system.key] = _compute_velocity(system)
    return v


def _compute_velocity(system) -> Vec3:
    m = MOTION.get(system.key)
    if m is None or system.dist_ly == 0:
        return _ZERO
    pm_ra, pm_dec, rv = m
    ra, dec = math.radians(parse_ra(system.ra)), math.radians(parse_dec(system.dec))
    d = system.dist_ly
    radial = (math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec))
    east = (-math.sin(ra), math.cos(ra), 0.0)
    north = (-math.sin(dec) * math.cos(ra), -math.sin(dec) * math.sin(ra), math.cos(dec))
    vr = (rv or 0.0) * KMS_TO_LY_PER_YR
    ve, vn = d * pm_ra * MAS, d * pm_dec * MAS
    eq = tuple(vr * radial[i] + ve * east[i] + vn * north[i] for i in range(3))
    return to_galactic(eq)  # type: ignore[arg-type]


def position(system, year: float) -> Vec3:
    """Where the system is in a given (decimal) year, in galactic light years from the Sun."""
    v = velocity(system)
    dt = year - EPOCH
    p = system.pos
    return (p[0] + v[0] * dt, p[1] + v[1] * dt, p[2] + v[2] * dt)


def speed_kms(system) -> float:
    v = velocity(system)
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2) / KMS_TO_LY_PER_YR


def proper_motion(system) -> float:
    """Total proper motion in arcseconds per year."""
    m = MOTION.get(system.key)
    return 0.0 if m is None else math.hypot(m[0], m[1]) / 1000


def radial_velocity(system) -> float | None:
    m = MOTION.get(system.key)
    return None if m is None else m[2]


def closest_approach(system) -> tuple[float, float] | None:
    """(year, distance in ly) of the system's closest approach to the Sun, on its straight line."""
    v = velocity(system)
    v2 = v[0] ** 2 + v[1] ** 2 + v[2] ** 2
    if v2 == 0:
        return None
    p = system.pos
    t = -(p[0] * v[0] + p[1] * v[1] + p[2] * v[2]) / v2
    q = (p[0] + v[0] * t, p[1] + v[1] * t, p[2] + v[2] * t)
    return EPOCH + t, math.sqrt(q[0] ** 2 + q[1] ** 2 + q[2] ** 2)


def format_year(year: float) -> str:
    y = math.floor(year)  # 2026.9 is still 2026: rounding would show next year from July on
    return f"{y:,} CE" if y >= 1 else f"{1 - y:,} BCE"
