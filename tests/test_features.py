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
    settings.save({"zoom": 2.5, "orbits": False, "nonsense": 1}, p)
    assert settings.load(p) == {"zoom": 2.5, "orbits": False}
    p.write_text(json.dumps({"zoom": True, "orbits": 0, "scale": 3}))
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
        sc.opts.show_drops = sc.opts.show_labels = False
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


def test_pole_axis_shows_tilt_and_hides_behind_the_planet():
    from orrery.system import AXIS, SystemScene, draw_system

    def axis_cells(planet: str, pitch_deg: float):
        moons = offline_moon_snapshot(planet, JD)
        pos = {k: o.position(JD) for k, o in moons.orbits.items()}
        sc = SystemScene(BY_KEY[planet], MOONS_OF[planet], pos, moons.orbits, (1.0, 0.0, 0.0))
        sc.opts.show_orbits = sc.opts.show_drops = sc.opts.show_labels = False
        cam = Camera()
        cam.pitch = math.radians(pitch_deg)
        c = Canvas(100, 30)
        draw_system(c, cam, sc)
        cells = [(r, k) for r in range(c.rows) for k in range(c.cols) if c.bits[r][k] and c.dot_fg[r][k] == AXIS]
        label = any(c.cells[r][k] and c.cells[r][k].ch == "N" for r in range(c.rows) for k in range(c.cols))
        return cells, label, sc.disc

    cells, label, (cx, cy, _) = axis_cells("jupiter", 35)
    assert cells and label
    # Jupiter's axis is nearly upright: seen from above, only the north end (above centre) shows.
    assert all(r < cy for r, _ in cells)
    cells, _, (cx, cy, _) = axis_cells("uranus", 0)
    rows = {r for r, _ in cells}
    assert len({k for _, k in cells}) > len(rows)  # Uranus's axis lies nearly sideways


def test_sun_is_a_solid_disc_when_zoomed_in():
    # Zoomed in, the Sun is a filled disc with its label beside it (hollow rings used to break up
    # around the ☼ glyph and label). Zoomed out, the ☼ glyph marks it.
    snap = offline_snapshot(JD)
    pos = {"sun": (0.0, 0.0, 0.0), **{k: o.position(JD) for k, o in snap.orbits.items()}}
    scene = Scene(pos, snap.orbits)
    scene.opts.show_orbits = False
    cam = Camera()
    cam.zoom = 4.0
    c = Canvas(120, 40)
    col, row = draw_scene(c, cam, scene)["sun"]
    assert c.bits[row][col] == 0xFF and c.cells[row][col] is None  # a full cell of dots, no glyph
    label = "".join(cell.ch if cell else " " for cell in c.cells[row])
    assert "Sun" in label and label.index("Sun") > col + 2  # beside the disc
    cam.zoom = 0.5
    small = Canvas(120, 40)
    col, row = draw_scene(small, cam, scene)["sun"]
    assert small.cells[row][col].ch == "☼"


def test_clicking_a_name_selects_its_body():
    import argparse
    from orrery.app import App
    from orrery.terminal import Mouse

    def click(app, col, row):
        app.last_click = None
        app.handle(Mouse("press", 0, col, row))
        app.handle(Mouse("release", 0, col, row))

    for system in (None, "jupiter"):
        app = App(argparse.Namespace(offline=True, scale=None, date=None, ascii=False, once=True, size=None,
                                     system=system, fresh=True, no_mouse=False))
        app.frame(140, 40)
        opts = app.active_opts
        clicked = 0
        for key in [k for *_, k in app.labels]:
            spans = [(r, c1) for r, _, c1, k in app.labels if k == key]  # where it is now: selecting moves labels
            if key == opts.selected or not spans:
                continue
            row, c1 = spans[0]
            click(app, c1 - 1, row)  # the last letter: well away from the body's own dot
            assert opts.selected == key, (system, key)
            clicked += 1
            app.frame(140, 40)
        assert clicked >= 4


def test_sun_stays_round_when_orbits_run_through_it_edge_on():
    # Nearly edge-on, the inner orbits flatten into bands through the Sun. They're cleared from
    # the disc and a ring round it, so the Sun keeps its own colour and its round outline.
    from orrery.render import SUN_CLEAR_PX, sun_radius_px

    snap = offline_snapshot(JD)
    pos = {"sun": (0.0, 0.0, 0.0), **{k: o.position(JD) for k, o in snap.orbits.items()}}
    cam = Camera()
    cam.pitch, cam.zoom = math.radians(4), 2.0
    c = Canvas(116, 40)
    draw_scene(c, cam, Scene(pos, snap.orbits))
    sx, sy = c.width / 2, c.height / 2  # the Sun sits at the centre of the view
    r = sun_radius_px(min(c.width / 2, c.height / 2 / math.sin(math.atan(1 / math.sqrt(2)))) / 6.2 * cam.zoom)
    for yi in range(c.height):
        for xi in range(c.width):
            dist = math.hypot(xi + 0.5 - sx, yi + 0.5 - sy)
            on = bool(c.bits[yi >> 2][xi >> 1] & (((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))[yi & 3][xi & 1]))
            if dist <= r:
                assert on and c.dot_fg[yi >> 2][xi >> 1][0] == 255  # Sun dots, in the Sun's colours
            elif dist <= r + SUN_CLEAR_PX:
                assert not on  # the clear ring round the limb


def test_no_stray_sun_coloured_dots_around_the_sun():
    # A cell has one colour, so an orbit dot left in a cell the Sun's disc touches would turn
    # Sun-coloured. Every dot in such a cell must be part of the disc itself.
    from orrery.render import sun_radius_px

    snap = offline_snapshot(JD)
    pos = {"sun": (0.0, 0.0, 0.0), **{k: o.position(JD) for k, o in snap.orbits.items()}}
    for zoom in (1.5, 2.0, 4.0):
        cam = Camera()
        cam.pitch, cam.zoom = math.radians(5), zoom
        c = Canvas(116, 40)
        draw_scene(c, cam, Scene(pos, snap.orbits))
        sx, sy = c.width / 2, c.height / 2
        r = sun_radius_px(min(c.width / 2, c.height / 2 / math.sin(math.atan(1 / math.sqrt(2)))) / 6.2 * zoom)
        braille = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))
        for yi in range(c.height):
            for xi in range(c.width):
                cell_has_sun = any(math.hypot(2 * (xi >> 1) + dx + 0.5 - sx, 4 * (yi >> 2) + dy + 0.5 - sy) <= r
                                   for dx in (0, 1) for dy in range(4))
                on = c.bits[yi >> 2][xi >> 1] & braille[yi & 3][xi & 1]
                if cell_has_sun and on:
                    assert math.hypot(xi + 0.5 - sx, yi + 0.5 - sy) <= r, (zoom, xi, yi)


def test_planet_colour_does_not_bleed_into_orbits():
    # Every dot drawn in the planet's colour must be part of the planet's disc: an orbit dot that
    # shares a cell with the planet's limb must not take the planet's colour.
    from orrery.system import SystemScene, draw_system

    braille = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))
    for planet, yaw, pitch, zoom in (("saturn", 89, -29, 0.76), ("jupiter", 45, 10, 1.0), ("uranus", 0, 20, 1.5)):
        moons = offline_moon_snapshot(planet, JD)
        pos = {k: o.position(JD) for k, o in moons.orbits.items()}
        sc = SystemScene(BY_KEY[planet], MOONS_OF[planet], pos, moons.orbits, (1.0, 0.0, 0.0))
        sc.opts.show_labels = False
        cam = Camera()
        cam.yaw, cam.pitch, cam.zoom = math.radians(yaw), math.radians(pitch), zoom
        c = Canvas(116, 40)
        draw_system(c, cam, sc)
        cx, cy, rc = sc.disc
        base = BY_KEY[planet].color
        for row in range(c.rows):
            for col in range(c.cols):
                fg = c.dot_fg[row][col]
                if not c.bits[row][col] or fg is None:
                    continue
                k = fg[0] / base[0]
                if not all(abs(fg[i] - base[i] * k) <= 2 for i in range(3)):
                    continue  # not the planet's colour
                for dy in range(4):
                    for dx in range(2):
                        if c.bits[row][col] & braille[dy][dx]:
                            x, y = 2 * col + dx + 0.5, 4 * row + dy + 0.5
                            assert math.hypot(x - 2 * cx, y - 4 * cy) <= 2 * rc + 0.75, (planet, row, col)


def test_o_cycles_all_then_only_the_followed_orbit_then_none():
    import argparse
    from orrery.app import App

    app = App(argparse.Namespace(offline=True, scale=None, date=None, ascii=False, once=True, size=None,
                                 system=None, fresh=True, no_mouse=False))
    app.opts.selected, app.follow = "mars", True
    app.opts.show_drops = app.opts.show_labels = False

    def orbit_colours() -> set:
        c = Canvas(120, 40)
        snap = app.snapshot
        pos = {"sun": (0.0, 0.0, 0.0), **{k: o.position(JD) for k, o in snap.orbits.items()}}
        draw_scene(c, Camera(), Scene(pos, snap.orbits, app.opts))
        return {c.dot_fg[r][k] for r in range(c.rows) for k in range(c.cols) if c.bits[r][k]}

    from orrery.render import SUN_CORE, shade
    mars, earth = BY_KEY["mars"].color, BY_KEY["earth"].color
    assert shade(earth, 0.5) in orbit_colours()  # all orbits
    app.handle("o")
    assert app.opts.solo_orbit and app.flash == "Selected body's orbit"
    seen = orbit_colours()
    assert shade(mars, 0.75) in seen and shade(earth, 0.5) not in seen  # just the followed one
    app.handle("o")
    assert not app.opts.show_orbits
    seen = orbit_colours()
    assert shade(mars, 0.75) not in seen
    app.handle("o")
    assert app.opts.show_orbits and not app.opts.solo_orbit

    app.enter_system("jupiter")  # same in the moon view, with the selected moon
    app.sys_opts.selected = "io"
    app.handle("o")
    assert app.sys_opts.solo_orbit and app.flash == "Selected body's orbit"
