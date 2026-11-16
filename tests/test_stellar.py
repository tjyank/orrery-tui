import argparse
import math
import re

import pytest

from orrery import terminal as T
from orrery.stellar.app import StarMap as App, find, format_luminosity, main
from orrery.stellar.catalog import BY_KEY, SYSTEMS, kind, spectral_class
from orrery.stellar.coords import distance, equatorial_xyz, galactic_lb, to_galactic


def args(**kw):
    base = dict(radius=20.0, view_from=None, select=None, year=None, ascii=False, once=True, size=None, no_mouse=True)
    base.update(kw)
    return argparse.Namespace(**base)


def plain(rows: list[str]) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mHJ]", "", "".join(rows))


# ---- coordinates ------------------------------------------------------------------------

def test_galactic_centre_direction():
    l, b = galactic_lb(to_galactic(equatorial_xyz(266.405, -28.936, 1.0)))  # Sgr A*
    assert min(l, 360 - l) < 0.1 and abs(b) < 0.1


def test_north_galactic_pole():
    _, b = galactic_lb(to_galactic(equatorial_xyz(192.859, 27.128, 1.0)))
    assert b == pytest.approx(90, abs=0.05)


@pytest.mark.parametrize("key, l, b", [("sirius", 227.2, -8.9), ("alphacen", 315.7, -0.7), ("vega", 67.4, 19.2)])
def test_known_galactic_coordinates(key, l, b):
    gl, gb = BY_KEY[key].galactic
    assert gl == pytest.approx(l, abs=0.3) and gb == pytest.approx(b, abs=0.3)


@pytest.mark.parametrize("a, b, ly", [("sirius", "procyon", 5.24), ("alphacen", "proxima", 0.21),
                                      ("procyon", "luyten", 1.12)])
def test_distances_between_stars(a, b, ly):
    assert distance(BY_KEY[a].pos, BY_KEY[b].pos) == pytest.approx(ly, abs=0.1)


# ---- catalog ----------------------------------------------------------------------------

def test_catalog_is_consistent():
    keys = [s.key for s in SYSTEMS]
    assert len(keys) == len(set(keys))
    for s in SYSTEMS:
        assert s.stars
        assert math.isclose(math.dist((0, 0, 0), s.pos), s.dist_ly, rel_tol=1e-9)


def test_absolute_magnitudes():
    assert BY_KEY["sun"].abs_mag == pytest.approx(4.83)
    assert BY_KEY["sirius"].abs_mag == pytest.approx(1.42, abs=0.05)
    assert BY_KEY["luhman16"].abs_mag is None  # brown dwarfs only


def test_spectral_kinds():
    assert spectral_class("sdM1") == "M"
    assert kind("DA2") == "white dwarf" and kind("T6") == "brown dwarf"
    assert kind("K0III") == "giant" and kind("G2V") == "yellow dwarf"


def test_find():
    assert find("sirius").key == "sirius"
    assert find("Alpha Centauri").key == "alphacen"
    assert find("proxima").key == "proxima"
    assert find("barnard").key == "barnard"
    assert find("vulcan") is None and find("") is None


def test_format_luminosity():
    assert format_luminosity(1.0) == "1 Sun"
    assert format_luminosity(25.4) == "25.4 Suns"
    assert format_luminosity(0.0001) == "1/10,000 of the Sun"


# ---- app --------------------------------------------------------------------------------

@pytest.mark.parametrize("size", [(40, 12), (90, 30), (160, 50)])
@pytest.mark.parametrize("view_from", [None, "Sirius", "Gliese 832"])
def test_frames_render(size, view_from):
    app = App(args(view_from=view_from))
    out = plain(app.frame_rows(*size))
    assert "stellar" in out


def test_view_from_glides_and_changes_origin():
    app = App(args())
    app.selected = "sirius"
    app.handle(T.ENTER)
    assert app.focus == "sirius" and app.glide is not None
    app.glide = (app.glide[0], app.glide[1] - 10)  # pretend the glide has finished
    app.tick(0.0)
    assert app.cam.target == app.pos("sirius")  # where Sirius is today, not at J2000
    app.handle("s")
    assert app.focus == "sun"


def test_selecting_a_far_star_widens_the_range():
    app = App(args(radius=10.0))
    app.select("arcturus")
    assert app.radius >= BY_KEY["arcturus"].dist_ly


def test_tab_cycles_by_distance():
    app = App(args())
    app.handle(T.TAB)
    assert app.selected == "proxima"
    app.handle(T.BTAB)
    assert app.selected == "sun"


def test_measure_shows_distance():
    app = App(args())
    app.selected = "sirius"
    app.handle("m")
    app.selected = "procyon"
    out = plain(app.frame_rows(160, 50))
    assert "5.2" in out and app.mark == "sirius"


def test_search_prompt():
    app = App(args())
    for k in ["/", *"tau ceti", T.ENTER]:
        app.handle(k)
    assert app.selected == "tauceti" and app.prompt is None


def test_once_prints_a_frame(capsys):
    main(["--once", "--size", "120x40", "--select", "Altair"])
    assert "Altair" in plain([capsys.readouterr().out])


# ---- inside the orrery ------------------------------------------------------------------

def _orrery():
    from orrery.app import App as Orrery

    return Orrery(argparse.Namespace(offline=True, scale=None, date=None, ascii=False, once=True, size=None,
                                     system=None, fresh=True, no_mouse=True))


def test_S_opens_the_star_map_in_the_orrerys_year_and_Esc_comes_back():
    from orrery.jumps import datetime_to_jd
    from datetime import datetime, timezone

    app = _orrery()
    app.sim_jd = datetime_to_jd(datetime(2100, 7, 2, tzinfo=timezone.utc))
    app.handle("S")
    assert app.in_stars and app.stars.year == pytest.approx(2100.5, abs=0.01) and app.stars.paused
    out = plain(app.frame_rows(140, 40))
    assert "ORRERY › STARS" in out and "Esc planets" in out
    app.handle(T.TAB)  # star-map keys go to the star map
    assert app.stars.selected == "proxima"
    app.handle("?")
    app.handle(T.ESC)  # Esc closes the key list first
    assert app.in_stars and not app.stars.show_keys
    app.handle(T.ESC)
    assert not app.in_stars and app.running
    assert "ORRERY" in plain(app.frame_rows(140, 40))
    app.handle("S")  # reopens as it was left
    assert app.in_stars and app.stars.selected == "proxima"
    app.handle("S")
    assert not app.in_stars


def test_q_in_the_star_map_quits_the_orrery():
    app = _orrery()
    app.handle("S")
    app.handle("q")
    assert not app.running


def test_orrery_stars_once(capsys):
    from orrery.app import main as orrery_main

    orrery_main(["--once", "--offline", "--fresh", "--stars", "--size", "120x40"])
    out = plain([capsys.readouterr().out])
    assert "ORRERY › STARS" in out and "Proxima" in out
