"""Trails: their length follows the time rate, they never lap an orbit, and they can follow a body."""
import argparse
import math

from orrery.bodies import BY_KEY
from orrery.ephemeris import offline_snapshot
from orrery.render import Camera, Canvas, Scene, draw_scene, shade, to_display
from orrery.trails import MAX_TRAIL_DAYS, TRAIL_REAL_SECONDS, TRAIL_SAMPLES, build_trails, trail_span_days

JD = 2461343.0
SNAP = offline_snapshot(JD)


def position(key, t):
    return to_display(SNAP.orbits[key].position(t), "log")


def period(key):
    return SNAP.orbits[key].period_days


def test_span_follows_the_time_rate():
    day = 86_400
    assert trail_span_days(day) == -TRAIL_REAL_SECONDS  # 1 day/s: the last 15 days
    assert trail_span_days(-day) == TRAIL_REAL_SECONDS  # running backwards: the trail is ahead in time
    assert trail_span_days(1) > -1e-3  # real time: next to nothing
    assert trail_span_days(10**12) == -MAX_TRAIL_DAYS


def test_trails_end_at_the_body_and_never_lap_an_orbit():
    trails = build_trails(["mercury", "jupiter"], JD, 31_557_600, position, period)  # 1 yr/s
    for key, pts in trails.items():
        assert len(pts) == TRAIL_SAMPLES
        newest = pts[-1]
        assert math.dist(newest, position(key, JD)) < math.dist(pts[0], position(key, JD))  # oldest first
    # Mercury (88 days) would lap itself many times in 15 years of trail: capped below one orbit.
    first_t = JD - 0.95 * period("mercury")
    assert math.dist(trails["mercury"][0], position("mercury", first_t)) < 1e-9


def test_speed_shows_in_the_spacing():
    # Mercury moves fastest at perihelion: with dots evenly spaced in time, its gaps vary a lot.
    pts = build_trails(["mercury"], JD, 7 * 86_400, position, period)["mercury"]  # 1 wk/s: 15 weeks
    gaps = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
    assert max(gaps) > 1.5 * min(gaps)


def test_trail_in_a_followed_frame_shows_retrograde_motion():
    # Following Earth, Mars's trail must double back at some point (its retrograde loop around
    # opposition in early 2027), which it never does around the Sun.
    def earth(t):
        return position("earth", t)

    years = 2 * 365.25 * 86_400 / 15  # a two-year trail
    rel = build_trails(["mars"], JD + 200, years, position, period, earth)["mars"]
    helio = build_trails(["mars"], JD + 200, years, position, period)["mars"]

    def turns(pts):  # sign changes in the rate of change of the angle seen from the frame's origin
        here = earth(JD + 200)
        angles = [math.atan2(p[1] - here[1], p[0] - here[0]) for p in pts]
        steps = [math.remainder(b - a, math.tau) for a, b in zip(angles, angles[1:])]
        return sum(1 for a, b in zip(steps, steps[1:]) if a * b < 0)

    assert turns(rel) >= 2  # into retrograde, and out again
    sun_steps = [math.remainder(math.atan2(b[1], b[0]) - math.atan2(a[1], a[0]), math.tau)
                 for a, b in zip(helio, helio[1:])]
    assert all(s > 0 for s in sun_steps)  # around the Sun it only ever moves forward


def test_trails_are_drawn_in_the_body_colour():
    pos = {"sun": (0.0, 0.0, 0.0), **{k: o.position(JD) for k, o in SNAP.orbits.items()}}
    scene = Scene(pos, SNAP.orbits)
    scene.opts.show_orbits = scene.opts.show_labels = scene.opts.show_drops = False
    scene.trails = build_trails(["mars"], JD, 30 * 86_400 * 7, position, period)
    c = Canvas(120, 40)
    draw_scene(c, Camera(), scene)
    mars = BY_KEY["mars"].color
    trail_cells = [c.dot_fg[r][k] for r in range(c.rows) for k in range(c.cols)
                   if c.bits[r][k] and c.dot_fg[r][k] and c.dot_fg[r][k][0] == shade(mars, c.dot_fg[r][k][0] / mars[0])[0]]
    assert len(trail_cells) > 10


def test_t_toggles_trails_and_app_builds_them():
    from orrery.app import App

    app = App(argparse.Namespace(offline=True, scale=None, date=None, ascii=False, once=True, size=None,
                                 system=None, fresh=True, no_mouse=False))
    app.handle("T")
    assert app.opts.show_trails
    app.rate_idx = 12  # 30 days/s
    assert "mars" in app._trails() and "sun" not in app._trails()
    app.opts.selected, app.follow = "earth", True
    assert "earth" not in app._trails()  # the followed body has no trail in its own frame
    app.enter_system("jupiter")
    app.handle("T")
    assert app.sys_opts.show_trails and app.frame(120, 40)
