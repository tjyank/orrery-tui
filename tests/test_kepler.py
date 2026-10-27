import math

from orrery.bodies import BY_KEY, PLANETS, approximate_orbit
from orrery.kepler import GM_SUN, orbit_from_state
from orrery.render import ISO_PITCH, Camera, Canvas, Scene, draw_scene, to_display

JD = 2461340.5  # 2026-10-27 00:00 TDB

# JPL Horizons heliocentric ecliptic J2000 state of Earth (399) at JD, au and au/day.
EARTH_R = (8.316115191229136e-01, 5.444506403706226e-01, -4.096764095450070e-05)
EARTH_V = (-9.697663929903025e-03, 1.432388743877745e-02, -9.663734865310026e-07)


def _dist(a, b):
    return math.dist(a, b)


def test_state_roundtrip():
    orbit = orbit_from_state(EARTH_R, EARTH_V, JD, BY_KEY["earth"].mu)
    r, v = orbit.state(JD)
    assert _dist(r, EARTH_R) < 1e-10
    assert _dist(v, EARTH_V) < 1e-12
    assert abs(orbit.a - 1.0) < 0.02 and orbit.e < 0.03


def test_roundtrip_inclined_eccentric():
    # Pluto-like orbit at an arbitrary phase
    o1 = approximate_orbit(BY_KEY["pluto"], JD)
    r, v = o1.state(JD + 1234.5)
    o2 = orbit_from_state(r, v, JD + 1234.5, o1.mu)
    for jd in (JD, JD + 5000, JD - 30000):
        assert _dist(o1.position(jd), o2.position(jd)) < 1e-8


def test_offline_model_close_to_horizons():
    # The analytic model should put Earth within ~0.01 au of the real ephemeris.
    assert _dist(approximate_orbit(BY_KEY["earth"], JD).position(JD), EARTH_R) < 0.01


def test_periods_are_sane():
    years = {b.key: approximate_orbit(b, JD).period_days / 365.25 for b in PLANETS}
    assert abs(years["earth"] - 1) < 0.01
    assert 11.5 < years["jupiter"] < 12.2
    assert 160 < years["neptune"] < 170


def test_display_scaling_preserves_direction():
    p = (3.0, 4.0, 0.0)
    for mode in ("sqrt", "log", "linear"):
        d = to_display(p, mode)
        assert math.isclose(d[1] / d[0], 4 / 3)


def test_render_smoke():
    orbits = {b.key: approximate_orbit(b, JD) for b in PLANETS}
    pos = {"sun": (0.0, 0.0, 0.0), **{k: o.position(JD) for k, o in orbits.items()}}
    canvas = Canvas(100, 30)
    cam = Camera()
    assert math.isclose(cam.pitch, ISO_PITCH)
    draw_scene(canvas, cam, Scene(pos, orbits))
    out = canvas.render()
    assert "☼" in out and "Earth" in out and "Neptune" in out
    assert any(0x2800 < ord(ch) <= 0x28FF for ch in out)  # orbits drawn as braille


def test_gm_constant():
    assert math.isclose(GM_SUN, 0.01720209895**2, rel_tol=1e-9)
