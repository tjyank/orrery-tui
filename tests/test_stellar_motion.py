import argparse
import re

import pytest

from orrery import terminal as T
from orrery.stellar.app import StarMap as App, RATES, main, parse_year
from orrery.stellar.catalog import BY_KEY, SYSTEMS
from orrery.stellar.coords import distance
from orrery.stellar.motion import (EPOCH, MAX_YEARS, MOTION, closest_approach, format_year, position, proper_motion,
                                speed_kms)


def args(**kw):
    base = dict(radius=20.0, view_from=None, select=None, year=None, ascii=False, once=True, size=None,
                no_mouse=True)
    base.update(kw)
    return argparse.Namespace(**base)


def plain(rows) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mHJ]", "", "".join(rows))


def test_every_star_has_motion():
    assert {s.key for s in SYSTEMS} - {"sun"} == set(MOTION)


def test_position_at_epoch_is_the_catalog_position():
    for s in SYSTEMS:
        assert position(s, EPOCH) == pytest.approx(s.pos)


# Published straight-line close approaches (year, ly): Barnard's Star ~11,800 CE at ~3.75 ly,
# Ross 248 ~38,000 CE at ~3.0 ly, α Centauri ~29,700 CE at ~3.2 ly, Kapteyn's Star ~9,000 BCE at ~7 ly.
@pytest.mark.parametrize("key, year, ly", [("barnard", 11_800, 3.75), ("ross248", 38_000, 3.03),
                                           ("alphacen", 29_700, 3.2), ("kapteyn", -9_000, 7.0)])
def test_closest_approaches(key, year, ly):
    y, d = closest_approach(BY_KEY[key])
    assert y == pytest.approx(year, abs=1_000) and d == pytest.approx(ly, abs=0.2)


def test_speeds_and_proper_motions():
    assert proper_motion(BY_KEY["barnard"]) == pytest.approx(10.39, abs=0.05)
    assert speed_kms(BY_KEY["barnard"]) == pytest.approx(142, abs=5)
    assert speed_kms(BY_KEY["kapteyn"]) > 250  # a halo star
    assert closest_approach(BY_KEY["sun"]) is None


def test_format_year():
    assert format_year(2026.4) == "2,026 CE"
    assert format_year(0) == "1 BCE"  # astronomical year 0 is 1 BC
    assert format_year(-9_999) == "10,000 BCE"


@pytest.mark.parametrize("text, year", [("38000", 38_000), ("38,000 CE", 38_000), ("10000 BCE", -9_999),
                                        ("+5000", 7_000), ("-5000", -3_000), ("soon", None)])
def test_parse_year(text, year):
    got = parse_year(text, 2_000.0)
    assert got == year if year is None else got == pytest.approx(year)


def test_time_runs_and_stops_at_the_end():
    app = App(args())
    assert app.paused
    app.handle(".")
    assert not app.paused and RATES[app.rate_idx] > 0
    app.tick(2.0)
    assert app.year == pytest.approx(app.now_year + 2 * RATES[app.rate_idx])
    app.tick(1e6)  # far past the end of the slider
    assert app.year == pytest.approx(app.now_year + MAX_YEARS) and app.paused
    app.handle("t")
    assert app.year == app.now_year


def test_comma_from_pause_runs_backward():
    app = App(args())
    app.handle(",")
    assert RATES[app.rate_idx] < 0 and not app.paused


def test_ross_248_becomes_the_nearest_star():
    app = App(args(year=38_300.0))
    pos = app.positions()
    nearest = min((s for s in SYSTEMS if s.key != "sun"), key=lambda s: distance(pos[s.key], (0, 0, 0)))
    assert nearest.key == "ross248"


def test_view_from_follows_the_moving_star():
    app = App(args(view_from="Barnard's Star"))
    app.set_year(app.now_year + 10_000)
    app.tick(0.0)
    assert app.cam.target == app.pos("barnard")


def test_year_prompt():
    app = App(args())
    for k in ["y", *"10000 BCE", T.ENTER]:
        app.handle(k)
    assert app.year == pytest.approx(-9_999) and app.paused


def test_slider_click_and_drag_scrub_time():
    app = App(args())
    app.frame_rows(140, 40)
    row, c0, w = app.slider
    app.handle(T.Mouse("press", 0, c0 + w - 1, row))  # right end: +100k years
    assert app.year == pytest.approx(app.now_year + MAX_YEARS)
    app.handle(T.Mouse("drag", 0, c0, row))  # left end
    assert app.year == pytest.approx(app.now_year - MAX_YEARS)
    app.handle(T.Mouse("release", 0, c0, row))
    assert not app.scrubbing


def test_frame_in_the_future_shows_year_and_tracks():
    app = App(args(year=38_300.0))
    out = plain(app.frame_rows(140, 40))
    assert "38,300 CE" in out and "+100k" in out


def test_once_with_year(capsys):
    main(["--once", "--size", "120x40", "--year", "10000 BCE", "--select", "Kapteyn"])
    assert "10,000 BCE" in plain([capsys.readouterr().out])


def test_format_year_late_in_the_year_is_still_that_year():
    assert format_year(2026.9) == "2,026 CE"
    assert format_year(-0.5) == "2 BCE"  # astronomical year -1
