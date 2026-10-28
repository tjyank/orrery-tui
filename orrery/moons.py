"""Natural satellites: catalogue, planetocentric offline orbits and planet pole orientations."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .bodies import BY_KEY, PLANETS, RGB, Body
from .kepler import AU_KM, DAY_S, GM_SUN, Orbit, Vec3

# Offline elements are osculating planetocentric ecliptic J2000 elements fitted to JPL Horizons
# at this epoch, with the mean motion fixed to the observed sidereal period.
MOON_EPOCH = 2461041.5  # 2026-01-01 00:00 TDB
OBLIQUITY = math.radians(23.4392911)

# Gravitational parameter for planet-relative moon orbits, where the planet's mass_ratio won't do.
_GM_KM3_S2 = {"pluto": 975.5}  # Pluto + Charon: the small moons orbit the pair's barycentre


@dataclass(frozen=True)
class Moon:
    key: str
    name: str
    parent: str
    horizons_id: str
    color: RGB
    radius_km: float
    # (a km, e, i deg, node deg, argp deg, M deg), ecliptic J2000, at MOON_EPOCH
    elements: tuple[float, float, float, float, float, float]
    period_days: float
    center: str | None = None  # Horizons centre if not the planet's body centre

    @property
    def mu(self) -> float:
        return planet_mu(self.parent)


def planet_mu(planet_key: str) -> float:
    """GM of a planet for satellite orbits, au^3 / day^2."""
    if planet_key in _GM_KM3_S2:
        return _GM_KM3_S2[planet_key] * DAY_S**2 / AU_KM**3
    return GM_SUN * BY_KEY[planet_key].mass_ratio


def _m(parent, hid, name, color, radius, elements, period, center=None) -> Moon:
    return Moon(name.lower(), name, parent, hid, color, radius, elements, period, center)


MOONS: list[Moon] = [
    # Mean distance and eccentricity for the Moon: its osculating orbit swings a lot under solar tides.
    _m("earth", "301", "Moon", (200, 200, 205), 1_737.4, (384_400, 0.0549, 5.060, 340.585, 98.581, 349.010), 27.321661),
    _m("mars", "401", "Phobos", (170, 150, 135), 11.1, (9_378, 0.0155, 27.418, 81.084, 159.823, 11.214), 0.318910),
    _m("mars", "402", "Deimos", (190, 175, 160), 6.2, (23_459, 0.0003, 24.173, 81.228, 29.775, 309.422), 1.262441),
    _m("jupiter", "505", "Amalthea", (200, 120, 90), 83.5, (181_959, 0.0065, 2.284, 328.384, 238.342, 357.216), 0.498179),
    _m("jupiter", "501", "Io", (240, 220, 100), 1_821.6, (421_962, 0.0040, 2.207, 338.481, 252.846, 99.744), 1.769138),
    _m("jupiter", "502", "Europa", (225, 205, 170), 1_560.8, (671_191, 0.0094, 2.150, 325.935, 77.200, 234.746), 3.551184),
    _m("jupiter", "503", "Ganymede", (175, 165, 155), 2_634.1, (1_070_717, 0.0021, 2.339, 339.231, 337.124, 24.778), 7.154547),
    _m("jupiter", "504", "Callisto", (150, 135, 120), 2_410.3, (1_882_360, 0.0073, 1.952, 336.703, 32.070, 80.071), 16.688993),
    _m("saturn", "601", "Mimas", (200, 200, 200), 198.2, (185_973, 0.0203, 29.628, 169.605, 257.567, 70.663), 0.942421),
    _m("saturn", "602", "Enceladus", (245, 250, 255), 252.1, (238_349, 0.0049, 28.053, 169.515, 85.330, 290.263), 1.370220),
    _m("saturn", "603", "Tethys", (225, 225, 220), 531.1, (294_898, 0.0009, 27.210, 171.027, 318.106, 353.774), 1.887801),
    _m("saturn", "604", "Dione", (210, 210, 205), 561.4, (377_555, 0.0019, 28.025, 169.530, 254.414, 164.018), 2.736915),
    _m("saturn", "605", "Rhea", (195, 195, 190), 763.8, (527_078, 0.0009, 28.243, 170.034, 173.691, 254.594), 4.517506),
    _m("saturn", "606", "Titan", (235, 170, 70), 2_574.7, (1_221_908, 0.0288, 27.709, 169.077, 177.951, 352.303), 15.945454),
    _m("saturn", "607", "Hyperion", (190, 170, 140), 135.0, (1_475_680, 0.0946, 27.053, 169.491, 82.126, 292.888), 21.322424),
    _m("saturn", "608", "Iapetus", (170, 150, 120), 734.5, (3_558_026, 0.0291, 16.995, 138.896, 230.531, 102.579), 79.331376),
    _m("saturn", "609", "Phoebe", (130, 125, 120), 106.5, (12_895_766, 0.1818, 172.807, 271.042, 11.777, 140.993), 549.583871),
    _m("uranus", "705", "Miranda", (190, 195, 200), 235.8, (129_867, 0.0013, 99.186, 163.423, 51.942, 100.452), 1.413400),
    _m("uranus", "701", "Ariel", (210, 210, 215), 578.9, (190_926, 0.0006, 97.715, 167.665, 235.510, 281.329), 2.520382),
    _m("uranus", "702", "Umbriel", (140, 140, 150), 584.7, (265_985, 0.0041, 97.711, 167.724, 54.830, 21.635), 4.144179),
    _m("uranus", "703", "Titania", (195, 185, 180), 788.9, (436_244, 0.0024, 97.763, 167.642, 262.909, 307.287), 8.705862),
    _m("uranus", "704", "Oberon", (175, 160, 155), 761.4, (583_574, 0.0021, 97.906, 167.710, 157.575, 321.426), 13.463237),
    _m("neptune", "808", "Proteus", (150, 145, 140), 210.0, (117_649, 0.0005, 29.059, 48.711, 8.263, 72.634), 1.122315),
    _m("neptune", "801", "Triton", (225, 195, 185), 1_353.4, (354_765, 0.0001, 129.148, 222.662, 103.153, 306.493), 5.876822),
    _m("neptune", "802", "Nereid", (170, 170, 175), 170.0, (5_504_327, 0.7454, 5.034, 319.298, 296.852, 350.033), 360.165703),
    _m("pluto", "901", "Charon", (180, 170, 165), 606.0, (19_594, 0.0002, 112.888, 227.393, 145.446, 102.577), 6.387222),
    _m("pluto", "905", "Styx", (160, 160, 160), 5.0, (43_021, 0.0251, 112.895, 227.337, 277.571, 296.865), 20.175418, "9"),
    _m("pluto", "902", "Nix", (200, 200, 200), 19.0, (49_589, 0.0213, 112.883, 227.370, 134.002, 3.858), 24.857724, "9"),
    _m("pluto", "904", "Kerberos", (130, 130, 130), 6.0, (58_404, 0.0144, 113.290, 227.530, 19.754, 343.291), 32.173004, "9"),
    _m("pluto", "903", "Hydra", (210, 210, 210), 19.0, (65_115, 0.0046, 112.610, 227.385, 147.215, 313.789), 38.200192, "9"),
]

MOONS_OF: dict[str, list[Moon]] = {p.key: sorted((m for m in MOONS if m.parent == p.key), key=lambda m: m.elements[0])
                                   for p in PLANETS}
MOON_BY_KEY = {m.key: m for m in MOONS}


def mean_moon_orbit(moon: Moon) -> Orbit:
    """Planetocentric orbit (au) from the offline elements; the period matches the observed one."""
    a_km, e, inc, node, argp, m0 = moon.elements
    a = a_km / AU_KM
    n = math.tau / moon.period_days
    return Orbit(a, e, math.radians(inc), math.radians(node), math.radians(argp), math.radians(m0),
                 MOON_EPOCH, mu=a**3 * n**2)


def pole_vector(body: Body) -> Vec3:
    """Unit vector of the body's IAU north pole in J2000 ecliptic coordinates."""
    ra, dec = (math.radians(x) for x in body.pole)
    x, y, z = math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec)
    ce, se = math.cos(OBLIQUITY), math.sin(OBLIQUITY)
    return (x, y * ce + z * se, -y * se + z * ce)


_RETROGRADE_SPIN = {"venus", "uranus"}  # IAU "north" points against their rotation (dwarf planets use the right-hand rule)


def spin_axis(body: Body) -> Vec3:
    """Unit rotation axis (right-hand rule), in J2000 ecliptic coordinates."""
    p = pole_vector(body)
    return (-p[0], -p[1], -p[2]) if body.key in _RETROGRADE_SPIN else p
