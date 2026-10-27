"""Static catalogue of the bodies we draw, plus offline fallback orbital elements."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .kepler import GM_SUN, Orbit

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Body:
    key: str
    name: str
    horizons_id: str
    color: RGB
    radius_km: float
    mass_ratio: float  # body mass / solar mass
    # JPL "Keplerian Elements for Approximate Positions" (1800-2050 AD), J2000 ecliptic:
    # (a au, e, I deg, L deg, long.peri deg, long.node deg) and their rates per Julian century.
    elements: tuple[float, ...] = ()
    rates: tuple[float, ...] = ()

    @property
    def mu(self) -> float:
        return GM_SUN * (1 + self.mass_ratio)


SUN = Body("sun", "Sun", "10", (255, 214, 92), 695_700, 1.0)

PLANETS: list[Body] = [
    Body("mercury", "Mercury", "199", (176, 168, 160), 2_439.7, 1.6601e-7,
         (0.38709927, 0.20563593, 7.00497902, 252.25032350, 77.45779628, 48.33076593),
         (0.00000037, 0.00001906, -0.00594749, 149472.67411175, 0.16047689, -0.12534081)),
    Body("venus", "Venus", "299", (232, 200, 140), 6_051.8, 2.4478e-6,
         (0.72333566, 0.00677672, 3.39467605, 181.97909950, 131.60246718, 76.67984255),
         (0.00000390, -0.00004107, -0.00078890, 58517.81538729, 0.00268329, -0.27769418)),
    Body("earth", "Earth", "399", (90, 156, 255), 6_371.0, 3.0035e-6,
         (1.00000261, 0.01671123, -0.00001531, 100.46457166, 102.93768193, 0.0),
         (0.00000562, -0.00004392, -0.01294668, 35999.37244981, 0.32327364, 0.0)),
    Body("mars", "Mars", "499", (232, 98, 64), 3_389.5, 3.2272e-7,
         (1.52371034, 0.09339410, 1.84969142, -4.55343205, -23.94362959, 49.55953891),
         (0.00001847, 0.00007882, -0.00813131, 19140.30268499, 0.44441088, -0.29257343)),
    Body("jupiter", "Jupiter", "599", (222, 170, 120), 69_911, 9.5479e-4,
         (5.20288700, 0.04838624, 1.30439695, 34.39644051, 14.72847983, 100.47390909),
         (-0.00011607, -0.00013253, -0.00183714, 3034.74612775, 0.21252668, 0.20469106)),
    Body("saturn", "Saturn", "699", (236, 214, 150), 58_232, 2.8589e-4,
         (9.53667594, 0.05386179, 2.48599187, 49.95424423, 92.59887831, 113.66242448),
         (-0.00125060, -0.00050991, 0.00193609, 1222.49362201, -0.41897216, -0.28867794)),
    Body("uranus", "Uranus", "799", (140, 226, 232), 25_362, 4.3662e-5,
         (19.18916464, 0.04725744, 0.77263783, 313.23810451, 170.95427630, 74.01692503),
         (-0.00196176, -0.00004397, -0.00242939, 428.48202785, 0.40805281, 0.04240589)),
    Body("neptune", "Neptune", "899", (84, 120, 255), 24_622, 5.1514e-5,
         (30.06992276, 0.00859048, 1.77004347, -55.12002969, 44.96476227, 131.78422574),
         (0.00026291, 0.00005105, 0.00035372, 218.45945325, -0.32241464, -0.00508664)),
    Body("pluto", "Pluto", "999", (210, 180, 160), 1_188.3, 6.58e-9,
         (39.48211675, 0.24882730, 17.14001206, 238.92903833, 224.06891629, 110.30393684),
         (-0.00031596, 0.00005170, 0.00004818, 145.20780515, -0.04062942, -0.01183482)),
]

ALL_BODIES: list[Body] = [SUN, *PLANETS]
BY_KEY = {b.key: b for b in ALL_BODIES}

J2000 = 2451545.0


def approximate_orbit(body: Body, jd: float) -> Orbit:
    """Mean orbit from the JPL approximate-elements table, anchored at `jd`."""
    t = (jd - J2000) / 36525.0
    a, e, inc, mean_long, long_peri, long_node = (
        x + dx * t for x, dx in zip(body.elements, body.rates)
    )
    if inc < 0:  # same orbit described with a positive inclination and flipped node
        inc, long_node = -inc, long_node + 180.0
    m = math.radians(mean_long - long_peri)
    return Orbit(
        a=a,
        e=e,
        i=math.radians(inc),
        node=math.radians(long_node) % math.tau,
        argp=math.radians(long_peri - long_node) % math.tau,
        m0=m,
        epoch=jd,
        mu=body.mu,
    )
