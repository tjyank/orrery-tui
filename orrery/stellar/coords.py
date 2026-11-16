"""Sky coordinates: J2000 equatorial -> galactic Cartesian, magnitudes."""
from __future__ import annotations

import math

Vec3 = tuple[float, float, float]
LY_PER_PC = 3.261_563_777
SUN_ABS_MAG = 4.83  # visual

# Rotation from J2000 equatorial to galactic coordinates (x toward the galactic centre,
# y toward l = 90 deg, the direction of the Sun's orbit, z toward the north galactic pole).
EQ_TO_GAL = (
    (-0.054_875_560_4, -0.873_437_090_2, -0.483_835_015_5),
    (0.494_109_427_9, -0.444_829_630_0, 0.746_982_244_5),
    (-0.867_666_149_0, -0.198_076_373_4, 0.455_983_776_2),
)


def parse_ra(s: str) -> float:
    """'14 39 36.5' (hours minutes seconds) -> degrees."""
    h, m, sec = (float(p) for p in s.split())
    return 15.0 * (h + m / 60 + sec / 3600)


def parse_dec(s: str) -> float:
    """'-60 50 02' (degrees arcminutes arcseconds) -> degrees."""
    parts = s.split()
    sign = -1.0 if parts[0].startswith("-") else 1.0
    d, m, sec = (abs(float(p)) for p in parts)
    return sign * (d + m / 60 + sec / 3600)


def equatorial_xyz(ra_deg: float, dec_deg: float, dist: float) -> Vec3:
    ra, dec = math.radians(ra_deg), math.radians(dec_deg)
    return (dist * math.cos(dec) * math.cos(ra), dist * math.cos(dec) * math.sin(ra), dist * math.sin(dec))


def to_galactic(v: Vec3) -> Vec3:
    return tuple(row[0] * v[0] + row[1] * v[1] + row[2] * v[2] for row in EQ_TO_GAL)  # type: ignore[return-value]


def galactic_lb(v: Vec3) -> tuple[float, float]:
    """Galactic longitude and latitude, in degrees, of a galactic Cartesian vector."""
    r = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if r < 1e-12:
        return 0.0, 0.0
    return math.degrees(math.atan2(v[1], v[0])) % 360.0, math.degrees(math.asin(max(-1.0, min(1.0, v[2] / r))))


def distance(a: Vec3, b: Vec3) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def absolute_mag(apparent: float, dist_ly: float) -> float:
    return apparent - 5 * math.log10(dist_ly / LY_PER_PC / 10)


def apparent_mag(absolute: float, dist_ly: float) -> float:
    return absolute + 5 * math.log10(max(dist_ly, 1e-9) / LY_PER_PC / 10)


def luminosity(abs_mag: float) -> float:
    """Visual luminosity in Suns."""
    return 10 ** ((SUN_ABS_MAG - abs_mag) / 2.5)


def format_ra(deg: float) -> str:
    h = deg / 15
    hh, mm = int(h), int(h * 60 % 60)
    ss = h * 3600 % 60
    return f"{hh:02d}h{mm:02d}m{ss:02.0f}s"


def format_dec(deg: float) -> str:
    sign = "−" if deg < 0 else "+"
    a = abs(deg)
    return f"{sign}{int(a):02d}°{int(a * 60 % 60):02d}′"
