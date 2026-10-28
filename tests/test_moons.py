import math

from orrery.bodies import BY_KEY, PLANETS
from orrery.ephemeris import offline_moon_snapshot
from orrery.kepler import AU_KM, orbit_from_state
from orrery.moons import MOONS, MOONS_OF, mean_moon_orbit, pole_vector, spin_axis
from orrery.render import Camera, Canvas
from orrery.system import SystemScene, draw_system, in_shadow

JD = 2461340.5  # 2026-10-27 00:00 TDB


def _angle(a, b):
    return math.degrees(math.acos(sum(x * y for x, y in zip(a, b)) / math.hypot(*a) / math.hypot(*b)))


def test_catalogue_is_consistent():
    assert {m.parent for m in MOONS} <= {p.key for p in PLANETS}
    assert len({m.key for m in MOONS}) == len(MOONS)
    assert [m.name for m in MOONS_OF["jupiter"]] == ["Amalthea", "Io", "Europa", "Ganymede", "Callisto"]
    assert MOONS_OF["mercury"] == [] and MOONS_OF["venus"] == []


def test_mean_orbits_keep_observed_periods_and_distances():
    for moon in MOONS:
        orbit = mean_moon_orbit(moon)
        assert math.isclose(orbit.period_days, moon.period_days, rel_tol=1e-9)
        r = math.hypot(*orbit.position(JD)) * AU_KM
        a, e = moon.elements[:2]
        assert a * (1 - e) * 0.999 <= r <= a * (1 + e) * 1.001


def test_mean_orbit_mass_is_close_to_planet_mass():
    # The period-derived GM should agree with the planet's own GM for an outer, unperturbed moon.
    for key in ("io", "titan", "titania", "triton", "charon"):
        moon = next(m for m in MOONS if m.key == key)
        assert math.isclose(mean_moon_orbit(moon).mu, moon.mu, rel_tol=0.02), key


def test_regular_moons_orbit_in_the_planet_equator():
    for key, limit in (("io", 1), ("titan", 1), ("ariel", 1), ("charon", 1)):
        moon = next(m for m in MOONS if m.key == key)
        r, v = mean_moon_orbit(moon).state(JD)
        normal = (r[1] * v[2] - r[2] * v[1], r[2] * v[0] - r[0] * v[2], r[0] * v[1] - r[1] * v[0])
        assert _angle(normal, spin_axis(BY_KEY[moon.parent])) < limit, key
    triton = next(m for m in MOONS if m.key == "triton")
    r, v = mean_moon_orbit(triton).state(JD)
    normal = (r[1] * v[2] - r[2] * v[1], r[2] * v[0] - r[0] * v[2], r[0] * v[1] - r[1] * v[0])
    assert _angle(normal, spin_axis(BY_KEY["neptune"])) > 150  # retrograde


def test_pole_vectors():
    assert math.isclose(math.hypot(*pole_vector(BY_KEY["jupiter"])), 1.0)
    assert 23 < _angle(pole_vector(BY_KEY["earth"]), (0, 0, 1)) < 24  # obliquity
    assert 95 < _angle(spin_axis(BY_KEY["uranus"]), (0, 0, 1)) < 100


def test_state_roundtrip_with_planet_mu():
    moon = next(m for m in MOONS if m.key == "europa")
    r, v = mean_moon_orbit(moon).state(JD)
    o = orbit_from_state(r, v, JD, moon.mu)
    assert math.dist(o.position(JD), r) < 1e-12


def test_shadow():
    sun = (1.0, 0.0, 0.0)
    r_au = 10 * 69_911 / AU_KM
    assert in_shadow((-r_au, 0.0, 0.0), sun, 69_911)
    assert not in_shadow((r_au, 0.0, 0.0), sun, 69_911)
    assert not in_shadow((-r_au, 2 * 69_911 / AU_KM, 0.0), sun, 69_911)


def test_system_render_smoke():
    for planet in ("jupiter", "uranus", "mercury"):
        snap = offline_moon_snapshot(planet, JD)
        pos = {k: o.position(JD) for k, o in snap.orbits.items()}
        sc = SystemScene(BY_KEY[planet], MOONS_OF[planet], pos, snap.orbits, (1.0, 0.0, 0.0))
        canvas = Canvas(100, 30)
        draw_system(canvas, Camera(), sc)
        out = canvas.render()
        assert BY_KEY[planet].name in out and "Sun" in out
        shown = sum(m.name in out for m in MOONS_OF[planet])
        assert shown >= len(MOONS_OF[planet]) // 2  # some may be behind the planet or crowded out
