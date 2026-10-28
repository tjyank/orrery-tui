"""Static catalogue of the bodies we draw, plus offline fallback orbital elements."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .kepler import GM_SUN, Orbit

RGB = tuple[int, int, int]
GM_PLANETS_RATIO = 0.00134  # mass of the planets / mass of the Sun: barycentric orbits feel both


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
    pole: tuple[float, float] = (0.0, 90.0)  # IAU north pole (RA, Dec) in degrees, ICRF
    kind: str = "planet"  # star | planet | dwarf | asteroid | comet
    # Horizons centre for this body's orbit: the Sun ("10"), or the solar-system barycentre ("0")
    # for slow outer orbits, whose heliocentric elements wobble with Jupiter's pull on the Sun.
    center: str = "10"

    @property
    def mu(self) -> float:
        if self.center == "0":
            return GM_SUN * (1 + GM_PLANETS_RATIO)
        return GM_SUN * (1 + self.mass_ratio)

    @property
    def small(self) -> bool:
        """Drawn only when small bodies are switched on (Pluto stays a fixture of the planet set)."""
        return self.kind in ("asteroid", "comet") or (self.kind == "dwarf" and self.key != "pluto")

    @property
    def type_name(self) -> str:
        return {"star": "Star", "planet": "Planet", "dwarf": "Dwarf planet", "asteroid": "Asteroid",
                "comet": "Comet"}[self.kind]


SUN = Body("sun", "Sun", "10", (255, 214, 92), 695_700, 1.0, pole=(286.13, 63.87), kind="star")

PLANETS: list[Body] = [
    Body("mercury", "Mercury", "199", (176, 168, 160), 2_439.7, 1.6601e-7,
         (0.38709927, 0.20563593, 7.00497902, 252.25032350, 77.45779628, 48.33076593),
         (0.00000037, 0.00001906, -0.00594749, 149472.67411175, 0.16047689, -0.12534081),
         pole=(281.01, 61.45)),
    Body("venus", "Venus", "299", (232, 200, 140), 6_051.8, 2.4478e-6,
         (0.72333566, 0.00677672, 3.39467605, 181.97909950, 131.60246718, 76.67984255),
         (0.00000390, -0.00004107, -0.00078890, 58517.81538729, 0.00268329, -0.27769418),
         pole=(272.76, 67.16)),
    Body("earth", "Earth", "399", (90, 156, 255), 6_371.0, 3.0035e-6,
         (1.00000261, 0.01671123, -0.00001531, 100.46457166, 102.93768193, 0.0),
         (0.00000562, -0.00004392, -0.01294668, 35999.37244981, 0.32327364, 0.0),
         pole=(0.0, 90.0)),
    Body("mars", "Mars", "499", (232, 98, 64), 3_389.5, 3.2272e-7,
         (1.52371034, 0.09339410, 1.84969142, -4.55343205, -23.94362959, 49.55953891),
         (0.00001847, 0.00007882, -0.00813131, 19140.30268499, 0.44441088, -0.29257343),
         pole=(317.68, 52.89)),
    Body("jupiter", "Jupiter", "599", (222, 170, 120), 69_911, 9.5479e-4,
         (5.20288700, 0.04838624, 1.30439695, 34.39644051, 14.72847983, 100.47390909),
         (-0.00011607, -0.00013253, -0.00183714, 3034.74612775, 0.21252668, 0.20469106),
         pole=(268.06, 64.5)),
    Body("saturn", "Saturn", "699", (236, 214, 150), 58_232, 2.8589e-4,
         (9.53667594, 0.05386179, 2.48599187, 49.95424423, 92.59887831, 113.66242448),
         (-0.00125060, -0.00050991, 0.00193609, 1222.49362201, -0.41897216, -0.28867794),
         pole=(40.59, 83.54)),
    Body("uranus", "Uranus", "799", (140, 226, 232), 25_362, 4.3662e-5,
         (19.18916464, 0.04725744, 0.77263783, 313.23810451, 170.95427630, 74.01692503),
         (-0.00196176, -0.00004397, -0.00242939, 428.48202785, 0.40805281, 0.04240589),
         pole=(257.31, -15.18)),
    Body("neptune", "Neptune", "899", (84, 120, 255), 24_622, 5.1514e-5,
         (30.06992276, 0.00859048, 1.77004347, -55.12002969, 44.96476227, 131.78422574),
         (0.00026291, 0.00005105, 0.00035372, 218.45945325, -0.32241464, -0.00508664),
         pole=(299.36, 43.46)),
    Body("pluto", "Pluto", "999", (210, 180, 160), 1_188.3, 6.58e-9,
         (39.48211675, 0.24882730, 17.14001206, 238.92903833, 224.06891629, 110.30393684),
         (-0.00031596, 0.00005170, 0.00004818, 145.20780515, -0.04062942, -0.01183482),
         pole=(132.99, -6.16), kind="dwarf"),
]


def _small(key, name, hid, color, radius, kind, elements, l_rate, center="10") -> Body:
    """Small bodies: osculating elements fitted to JPL Horizons at 2026-01-01, in the same
    (a, e, I, L, long.peri, long.node) form as the planets, advancing only the mean longitude."""
    return Body(key, name, hid, color, radius, 0.0, elements, (0, 0, 0, l_rate, 0, 0), kind=kind, center=center)


SMALL_BODIES: list[Body] = [
    _small("ceres", "Ceres", "1;", (180, 170, 160), 469.7, "dwarf",
           (2.76555733, 0.07960223, 10.587946, 158.740169, 153.555937, 80.249109), 7827.449977),
    _small("pallas", "Pallas", "2;", (160, 165, 175), 256.0, "asteroid",
           (2.76987522, 0.23065363, 34.929259, 113.735400, 123.827302, 172.887811), 7809.154083),
    _small("vesta", "Vesta", "4;", (200, 190, 170), 262.7, "asteroid",
           (2.36154565, 0.09017811, 7.144046, 234.052992, 255.230913, 103.702272), 9919.728361),
    _small("haumea", "Haumea", "136108;", (220, 220, 230), 780.0, "dwarf",
           (43.10245827, 0.19502458, 28.204825, 192.320842, 1.893529, 121.946447), 127.301060, "0"),
    _small("makemake", "Makemake", "136472;", (210, 150, 120), 715.0, "dwarf",
           (45.49869444, 0.16038650, 29.002080, 155.617099, 15.506940, 79.441468), 117.377993, "0"),
    _small("eris", "Eris", "136199;", (230, 230, 235), 1_163.0, "dwarf",
           (67.83516192, 0.43843001, 43.992966, 21.040901, 187.197567, 35.976476), 64.476697, "0"),
    _small("halley", "Halley", "DES=1P;CAP;", (150, 220, 255), 5.5, "comet",
           (17.86506860, 0.96730265, 162.245813, 236.855577, 170.569592, 58.835071), 477.065542, "0"),
    _small("encke", "Encke", "DES=2P;CAP;", (150, 240, 220), 2.4, "comet",
           (2.21800836, 0.84723149, 11.346621, 86.887148, 161.297581, 334.016764), 10898.068453),
    _small("67p", "67P/C-G", "DES=67P;CAP;", (170, 200, 255), 2.0, "comet",
           (3.45876054, 0.64969881, 3.867254, 276.373876, 58.530214, 36.304545), 5596.461940),
]

ORBITING: list[Body] = [*PLANETS, *SMALL_BODIES]  # everything with a heliocentric orbit
ALL_BODIES: list[Body] = [SUN, *ORBITING]
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
