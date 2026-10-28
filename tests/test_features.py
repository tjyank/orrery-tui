"""Events, the lunar theory, small bodies, jumps, settings, input parsing and rendering speedups."""
import json
import math
from datetime import datetime, timezone

from orrery.bodies import BY_KEY, SMALL_BODIES, approximate_orbit
from orrery.ephemeris import jd_to_datetime, offline_moon_snapshot, offline_snapshot
from orrery.events import MoonTrack, earth_eclipses, system_events, windows
from orrery.jumps import datetime_to_jd, next_opposition, next_perihelion, parse_when
from orrery.lunar import earth_heliocentric, moon_geocentric
from orrery.moons import MOONS_OF
from orrery.render import Camera, Canvas, Scene, draw_scene
from orrery import settings
from orrery.terminal import BTAB, ENTER, ESC, UP, Mouse, parse_input

JD = 2461340.5  # 2026-10-27 00:00 TDB
# JPL Horizons: geocentric Moon (301 from 500@399), ecliptic J2000, au, at JD.
MOON_R = (0.001746014517678985, 0.00170546320134981, 0.0002097659924504562)


def _jd(iso: str) -> float:
    return datetime_to_jd(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc))


def test_lunar_theory_matches_horizons():
    m = moon_geocentric(JD)
    angle = math.acos(sum(a * b for a, b in zip(m, MOON_R)) / math.hypot(*m) / math.hypot(*MOON_R))
    assert math.degrees(angle) * 3600 < 60  # arcseconds
    assert abs(math.hypot(*m) - math.hypot(*MOON_R)) * 149_597_870.7 < 50  # km


def test_eclipses_2026_2027_match_the_published_list():
    found = {(e.kind, e.detail, jd_to_datetime(e.peak).strftime("%Y-%m-%d"), e.peak)
             for e in earth_eclipses(earth_heliocentric, moon_geocentric, _jd("2026-01-01"), 730)}
    expected = [  # (kind, type, date, greatest eclipse UTC) from NASA's eclipse catalogues
        ("solar-eclipse", "annular", "2026-02-17", "2026-02-17T12:12"),
        ("lunar-eclipse", "total", "2026-03-03", "2026-03-03T11:33"),
        ("solar-eclipse", "total", "2026-08-12", "2026-08-12T17:46"),
        ("lunar-eclipse", "partial", "2026-08-28", "2026-08-28T04:13"),
        ("solar-eclipse", "annular", "2027-02-06", "2027-02-06T16:00"),
        ("solar-eclipse", "total", "2027-08-02", "2027-08-02T10:07"),
    ]
    assert len(found) == len(expected)  # 2027's three lunar eclipses are penumbral only: not listed
    for kind, detail, day, peak in expected:
        match = [f for f in found if f[:3] == (kind, detail, day)]
        assert match, (kind, detail, day)
        assert abs(match[0][3] - _jd(peak)) * 1440 < 10  # minutes


def test_windows_finds_a_dip_between_samples():
    # A 0.1-wide dip at t=5.03, sampled every 1.0: it falls between grid points.
    f = lambda t: abs(t - 5.03) - 0.05  # noqa: E731
    (start, end, peak), = windows(f, 0, 10, 1.0)
    assert abs(start - 4.98) < 1e-6 and abs(end - 5.08) < 1e-6 and abs(peak - 5.03) < 1e-4
    assert windows(lambda t: t - 3, 0, 10, 1.0)[0][0] is None  # already under way at the start


def test_io_events_are_regular():
    snap, moons = offline_snapshot(JD), offline_moon_snapshot("jupiter", JD)
    io = next(m for m in MOONS_OF["jupiter"] if m.key == "io")
    track = MoonTrack(io.key, io.name, io.radius_km, io.period_days, moons.orbits["io"].position)
    evs = system_events("jupiter", BY_KEY["jupiter"].radius_km, snap.orbits["jupiter"].position,
                        earth_heliocentric, [track], JD)
    eclipses = [e for e in evs if e.kind == "eclipse" and e.start and e.end]
    assert len(eclipses) >= 4  # one per 1.77-day orbit
    for e in eclipses:
        assert 1.8 < (e.end - e.start) * 24 < 2.6  # hours in Jupiter's shadow
    gaps = [b.start - a.start for a, b in zip(eclipses, eclipses[1:])]
    assert all(abs(g - 1.7699) < 0.01 for g in gaps)  # Io's synodic period
    assert {e.kind for e in evs} >= {"eclipse", "transit", "occultation"}


def test_no_events_for_uranus_far_from_equinox():
    snap, moons = offline_snapshot(JD), offline_moon_snapshot("uranus", JD)
    tracks = [MoonTrack(m.key, m.name, m.radius_km, m.period_days, moons.orbits[m.key].position)
              for m in MOONS_OF["uranus"]]
    evs = system_events("uranus", BY_KEY["uranus"].radius_km, snap.orbits["uranus"].position,
                        earth_heliocentric, tracks, JD)
    assert not [e for e in evs if e.kind == "eclipse"]


def test_small_bodies():
    assert {b.kind for b in SMALL_BODIES} == {"dwarf", "asteroid", "comet"}
    assert all(b.small for b in SMALL_BODIES) and not BY_KEY["pluto"].small
    halley = approximate_orbit(BY_KEY["halley"], JD)
    assert 75 < halley.period_days / 365.25 < 76
    assert jd_to_datetime(next_perihelion(halley, JD)).strftime("%Y-%m") == "2061-07"
    ceres = approximate_orbit(BY_KEY["ceres"], JD)
    assert 2.5 < math.hypot(*ceres.position(JD)) < 3.0


def test_jumps():
    assert abs(parse_when("+30d", JD) - (JD + 30)) < 1e-9
    assert abs(parse_when("-1y", JD) - (JD - 365.25)) < 1e-9
    assert jd_to_datetime(parse_when("1969-07-20 20:17", JD)).year == 1969  # pre-1970 works on Windows
    snap = offline_snapshot(JD)
    t = next_opposition(lambda k, jd: snap.orbits[k].position(jd), "mars", JD)
    assert jd_to_datetime(t).strftime("%Y-%m-%d") in ("2027-02-19", "2027-02-20")


def test_settings_validation(tmp_path):
    p = tmp_path / "s.json"
    settings.save({"zoom": 2.5, "grid": False, "nonsense": 1}, p)
    assert settings.load(p) == {"zoom": 2.5, "grid": False}
    p.write_text(json.dumps({"zoom": True, "grid": 0, "scale": 3}))
    assert settings.load(p) == {}  # wrong types are dropped, not trusted
    p.write_text("{broken")
    assert settings.load(p) == {}


def test_parse_input():
    esc = "\x1b"
    keys, rest = parse_input(f"a{esc}[A{esc}[Z\r{esc}[<0;5;3M{esc}[<32;6;3M{esc}[<0;6;3m{esc}[<65;1;1M{esc}")
    assert keys == ["a", UP, BTAB, ENTER, Mouse("press", 0, 4, 2), Mouse("drag", 0, 5, 2),
                    Mouse("release", 0, 5, 2), Mouse("scroll", -1, 0, 0)]
    assert rest == esc  # maybe the start of a sequence: the terminal decides once nothing follows
    assert parse_input(f"{esc}q")[0] == [ESC, "q"]
    assert parse_input(f"{esc}[<0;1")[1] == f"{esc}[<0;1"  # incomplete: kept for the next read


def test_polyline_matches_line_by_line():
    pts = [(-5.0, 3.0, 0.1), (20.3, 7.7, 0.4), (40.1, 60.2, -0.2), (300.0, 2.0, 0.0), (10.0, 10.0, 1.0)]
    a, b = Canvas(60, 20), Canvas(60, 20)
    a.polyline(pts, (1, 2, 3), back=(4, 5, 6), split=0.2)
    for p, q in zip(pts, pts[1:]):
        b.line(p, q, (1, 2, 3) if p[2] + q[2] >= 0.4 else (4, 5, 6))
    assert a.bits == b.bits and a.dot_fg == b.dot_fg


def test_orbit_in_front_of_planet_is_cut_out_of_it():
    # Nearly edge-on, moon orbits cross Jupiter's face. Rather than recolouring the planet's dots
    # (a cell has one colour), the orbit is cut out of the planet: fewer dots, planet colour kept.
    from orrery.render import shade
    from orrery.system import SystemScene, draw_system

    moons = offline_moon_snapshot("jupiter", JD)
    pos = {k: o.position(JD) for k, o in moons.orbits.items()}

    def draw(orbits: bool) -> Canvas:
        sc = SystemScene(BY_KEY["jupiter"], MOONS_OF["jupiter"], pos, moons.orbits, (1.0, 0.0, 0.0))
        sc.opts.show_grid = sc.opts.show_drops = sc.opts.show_labels = False
        sc.opts.show_orbits = orbits
        cam = Camera()
        cam.pitch = math.radians(4)
        c = Canvas(100, 30)
        draw_system(c, cam, sc)
        return c

    bare, crossed = draw(False), draw(True)
    jupiter = BY_KEY["jupiter"].color

    def planet_shade(c) -> bool:  # some brightness of Jupiter's own colour
        k = c[0] / jupiter[0]
        return all(abs(c[i] - jupiter[i] * k) <= 2 for i in range(3))

    orbit_colours = {shade(m.color, f) for m in MOONS_OF["jupiter"] for f in (0.5, 0.75)}
    # Cells where an orbit removed some planet dots but some remain. (A limb cell whose few planet
    # dots were all cut away holds only orbit dots, and rightly keeps the orbit's colour.)
    gaps = [(r, k) for r in range(bare.rows) for k in range(bare.cols)
            if bare.bits[r][k] & ~crossed.bits[r][k] and bare.bits[r][k] & crossed.bits[r][k]]
    assert len(gaps) > 5
    for r, k in gaps:
        assert planet_shade(crossed.dot_fg[r][k]) and crossed.dot_fg[r][k] not in orbit_colours


def test_grid_hides_what_wraps_behind_the_planet():
    # From above (the default pitch), a ring's far half projects above the planet's centre on
    # screen. Those parts are hidden; seen face-on (from the pole), rings have no back and stay whole.
    from orrery.render import GRID
    from orrery.system import SystemScene, draw_system

    moons = offline_moon_snapshot("jupiter", JD)
    pos = {k: o.position(JD) for k, o in moons.orbits.items()}

    def grid_dots(pitch_deg: float) -> tuple[int, int]:
        sc = SystemScene(BY_KEY["jupiter"], MOONS_OF["jupiter"], pos, moons.orbits, (1.0, 0.0, 0.0))
        sc.opts.show_orbits = sc.opts.show_drops = sc.opts.show_labels = False
        cam = Camera()
        cam.pitch = math.radians(pitch_deg)
        c = Canvas(100, 30)
        draw_system(c, cam, sc)
        centre_row = int(sc.disc[1])
        cells = [(r, k) for r in range(c.rows) for k in range(c.cols) if c.bits[r][k] and c.dot_fg[r][k] == GRID]
        return sum(r < centre_row - 2 for r, _ in cells), len(cells)

    above, total = grid_dots(35)
    assert total > 100 and above < 0.05 * total  # only the front arcs (and the pole axis) remain
    above, total = grid_dots(90)
    assert above > 0.3 * total  # face-on: whole rings


def test_clicks_pick_the_planet_disc_and_double_clicks_follow():
    import argparse
    from orrery.app import App
    from orrery.terminal import Mouse

    args = argparse.Namespace(offline=True, scale=None, date=None, ascii=False, once=True, size=None,
                              system="jupiter", fresh=True, no_mouse=False)
    app = App(args)
    app.frame(140, 40)

    def click(col, row):
        app.handle(Mouse("press", 0, col, row))
        app.handle(Mouse("drag", 0, col, row))  # a motion report within the cell is not a drag
        app.handle(Mouse("release", 0, col, row))

    col, row = app.hits["io"]
    click(col, row)
    click(col, row)
    assert app.sys_opts.selected == "io" and app.sys_follow
    app.frame(140, 40)
    cx, cy, rc = app.disc
    app.last_click = None
    click(int(cx + 0.7 * rc), int(cy))  # off-centre, but on the disc
    assert app.sys_opts.selected == "jupiter"


def test_render_returns_hits_and_hides_small_bodies():
    snap = offline_snapshot(JD)
    pos = {"sun": (0.0, 0.0, 0.0), **{k: o.position(JD) for k, o in snap.orbits.items()}}
    scene = Scene(pos, snap.orbits)
    hits = draw_scene(Canvas(120, 40), Camera(), scene)
    assert "ceres" in hits and "jupiter" in hits
    scene.opts.show_small = False
    assert "ceres" not in draw_scene(Canvas(120, 40), Camera(), scene)
