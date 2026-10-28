"""Geocentric Moon from the main terms of ELP-2000/82 (Meeus, Astronomical Algorithms, ch. 47).

Good to ~10" in longitude and ~4" in latitude over centuries: far steadier than an osculating
two-body orbit, whose shape the Sun distorts by degrees within weeks. Eclipse prediction needs it.
"""
from __future__ import annotations

import math

from .kepler import AU_KM, Vec3

J2000 = 2451545.0

# (D, M, M', F, Σl [1e-6 deg], Σr [1e-3 km]) for longitude and distance.
_LR = (
    (0, 0, 1, 0, 6288774, -20905355), (2, 0, -1, 0, 1274027, -3699111), (2, 0, 0, 0, 658314, -2955968),
    (0, 0, 2, 0, 213618, -569925), (0, 1, 0, 0, -185116, 48888), (0, 0, 0, 2, -114332, -3149),
    (2, 0, -2, 0, 58793, 246158), (2, -1, -1, 0, 57066, -152138), (2, 0, 1, 0, 53322, -170733),
    (2, -1, 0, 0, 45758, -204586), (0, 1, -1, 0, -40923, -129620), (1, 0, 0, 0, -34720, 108743),
    (0, 1, 1, 0, -30383, 104755), (2, 0, 0, -2, 15327, 10321), (0, 0, 1, 2, -12528, 0),
    (0, 0, 1, -2, 10980, 79661), (4, 0, -1, 0, 10675, -34782), (0, 0, 3, 0, 10034, -23210),
    (4, 0, -2, 0, 8548, -21636), (2, 1, -1, 0, -7888, 24208), (2, 1, 0, 0, -6766, 30824),
    (1, 0, -1, 0, -5163, -8379), (1, 1, 0, 0, 4987, -16675), (2, -1, 1, 0, 4036, -12831),
    (2, 0, 2, 0, 3994, -10445), (4, 0, 0, 0, 3861, -11650), (2, 0, -3, 0, 3665, 14403),
    (0, 1, -2, 0, -2689, -7003), (2, 0, -1, 2, -2602, 0), (2, -1, -2, 0, 2390, 10056),
    (1, 0, 1, 0, -2348, 6322), (2, -2, 0, 0, 2236, -9884),
)

# (D, M, M', F, Σb [1e-6 deg]) for latitude.
_B = (
    (0, 0, 0, 1, 5128122), (0, 0, 1, 1, 280602), (0, 0, 1, -1, 277693), (2, 0, 0, -1, 173237),
    (2, 0, -1, 1, 55413), (2, 0, -1, -1, 46271), (2, 0, 0, 1, 32573), (0, 0, 2, 1, 17198),
    (2, 0, 1, -1, 9266), (0, 0, 2, -1, 8822), (2, -1, 0, -1, 8216), (2, 0, -2, -1, 4324),
    (2, 0, 1, 1, 4200), (2, 1, 0, -1, -3359), (2, -1, -1, 1, 2463), (2, -1, 0, 1, 2211),
    (2, -1, -1, -1, 2065), (0, 1, -1, -1, -1870), (4, 0, -1, -1, 1828), (0, 1, 0, 1, -1794),
)


def moon_geocentric(jd: float) -> Vec3:
    """Moon relative to Earth's centre, ecliptic J2000, au."""
    t = (jd - J2000) / 36525.0
    rad = math.radians
    lp = rad(218.3164477 + 481267.88123421 * t - 0.0015786 * t * t)  # mean longitude
    d = rad(297.8501921 + 445267.1114034 * t - 0.0018819 * t * t)  # mean elongation
    m = rad(357.5291092 + 35999.0502909 * t - 0.0001536 * t * t)  # Sun's mean anomaly
    mp = rad(134.9633964 + 477198.8675055 * t + 0.0087414 * t * t)  # Moon's mean anomaly
    f = rad(93.2720950 + 483202.0175233 * t - 0.0036539 * t * t)  # argument of latitude
    a1, a2, a3 = rad(119.75 + 131.849 * t), rad(53.09 + 479264.290 * t), rad(313.45 + 481266.484 * t)
    e = 1 - 0.002516 * t - 0.0000074 * t * t  # Earth's orbital eccentricity is shrinking

    sl = sr = sb = 0.0
    for cd, cm, cmp, cf, kl, kr in _LR:
        arg = cd * d + cm * m + cmp * mp + cf * f
        k = e ** abs(cm)
        sl += kl * k * math.sin(arg)
        sr += kr * k * math.cos(arg)
    for cd, cm, cmp, cf, kb in _B:
        sb += kb * e ** abs(cm) * math.sin(cd * d + cm * m + cmp * mp + cf * f)
    sl += 3958 * math.sin(a1) + 1962 * math.sin(lp - f) + 318 * math.sin(a2)
    sb += (-2235 * math.sin(lp) + 382 * math.sin(a3) + 175 * math.sin(a1 - f) + 175 * math.sin(a1 + f)
           + 127 * math.sin(lp - mp) - 115 * math.sin(lp + mp))

    # Mean ecliptic and equinox of date -> J2000: undo the general precession in longitude.
    lon = lp + rad(sl / 1e6) - rad(1.3969713 * t + 0.0003086 * t * t)
    lat = rad(sb / 1e6)
    dist = (385_000.56 + sr / 1000) / AU_KM
    return (dist * math.cos(lat) * math.cos(lon), dist * math.cos(lat) * math.sin(lon), dist * math.sin(lat))


MOON_MASS_FRACTION = 1 / 82.30057  # Moon / (Earth + Moon)


def earth_heliocentric(jd: float) -> Vec3:
    """Earth's centre, au, ecliptic J2000.

    The JPL mean elements track the Earth-Moon barycentre, which keeps a far steadier orbit than
    Earth's centre (that wobbles ~4,700 km monthly around it); the Moon then gives the offset.
    """
    from .bodies import BY_KEY, approximate_orbit

    emb = approximate_orbit(BY_KEY["earth"], jd).position(jd)
    m = moon_geocentric(jd)
    return tuple(emb[k] - MOON_MASS_FRACTION * m[k] for k in range(3))


def moon_state(jd: float, h: float = 0.01) -> tuple[Vec3, Vec3]:
    """Position (au) and velocity (au/day), the velocity by central difference."""
    a, b = moon_geocentric(jd - h), moon_geocentric(jd + h)
    return moon_geocentric(jd), tuple((b[k] - a[k]) / (2 * h) for k in range(3))
