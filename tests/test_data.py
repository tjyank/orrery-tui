"""Horizons replies, the staged fetch, the caches, light time, and the idle main loop."""
import argparse
import json
import math
import threading
import time

import pytest

from orrery import ephemeris as E
from orrery.bodies import BY_KEY, approximate_orbit

JD = 2461340.5  # 2026-10-27 00:00 TDB

# A real Horizons reply (Earth, 399, at JD), trimmed to the part we read.
REPLY = {"result": (
    "      Y,                      Z,                     VX,                     VY,                     VZ,\n"
    "*****************************************************************************************************\n"
    "$$SOE\n"
    "2461340.500000000, A.D. 2026-Oct-27 00:00:00.0000,  8.316115191229136E-01,  5.444506403706226E-01, "
    "-4.096764095450070E-05, -9.697663929903025E-03,  1.432388743877745E-02, -9.663734865310026E-07,\n"
    "$$EOE\n"
    "*****************************************************************************************************\n"
)}
# What Horizons sends for a name it can't resolve: an ordinary reply, with no data table.
UNKNOWN = {"result": "JPL/DASTCOM   Small-body Index Search Results\n NAME = NOSUCHBODY9;\n No matches found.\n"}


def test_parse_vectors():
    r, v = E.parse_vectors(REPLY, "Earth")
    assert r == (0.8316115191229136, 0.5444506403706226, -4.09676409545007e-05)
    assert v == (-0.009697663929903025, 0.01432388743877745, -9.663734865310026e-07)


@pytest.mark.parametrize("payload, message", [
    (UNKNOWN, "unexpected Horizons response"),
    ({"error": "Cannot interpret date."}, "Cannot interpret date."),
    ({"result": "$$SOE\n2461340.5, A.D. 2026-Oct-27, 1.0, oops,\n$$EOE\n"}, "unexpected Horizons response"),
    ({"signature": {}}, "unexpected Horizons response"),
])
def test_parse_vectors_rejects_bad_replies(payload, message):
    with pytest.raises(RuntimeError, match=message):
        E.parse_vectors(payload, "Thing")


@pytest.fixture
def fake_horizons(tmp_path, monkeypatch):
    """Horizons replaced by the offline model; caches in a temporary folder. Records requests."""
    monkeypatch.setattr(E, "CACHE_FILE", tmp_path / "horizons.json")
    monkeypatch.setattr(E, "MOON_CACHE_FILE", tmp_path / "horizons-moons.json")
    from orrery.moons import MOONS, mean_moon_orbit

    requests = []
    by_id = {b.horizons_id: b for b in E.PLANET_BODIES + E.SMALL_BODIES}
    moons = {m.horizons_id: m for m in MOONS}

    def fetch(horizons_id, name, jd, timeout, center="10", retries=3):
        requests.append(name)
        if horizons_id in moons:
            return mean_moon_orbit(moons[horizons_id]).state(jd)
        if name in fetch.fail:
            raise RuntimeError(f"{name}: unexpected Horizons response")
        return approximate_orbit(by_id[horizons_id], jd).state(jd)

    fetch.fail = set()
    monkeypatch.setattr(E, "_fetch_state", fetch)
    return requests, fetch


def test_staged_fetch_publishes_planets_first(fake_horizons):
    requests, _ = fake_horizons
    published = []
    snap = E.fetch_horizons(JD, publish=lambda s: published.append((len(requests), s)))
    planets, small = len(E.PLANET_BODIES), len(E.SMALL_BODIES)
    assert requests[:planets] == [b.name for b in E.PLANET_BODIES]  # planets come first
    assert len(published) == 1 and published[0][0] == planets  # published before any small body
    assert set(published[0][1].orbits) == set(snap.orbits)  # complete, small bodies from the offline model
    assert len(requests) == planets + small and snap.source == "live"


def test_routine_refresh_skips_recent_small_bodies(fake_horizons):
    requests, _ = fake_horizons
    E.fetch_horizons(JD)
    requests.clear()
    E.fetch_horizons(JD + 3)  # within SMALL_BODY_REFRESH_DAYS: planets only
    assert requests == [b.name for b in E.PLANET_BODIES]
    requests.clear()
    E.fetch_horizons(JD + E.SMALL_BODY_REFRESH_DAYS + 1)
    assert len(requests) == len(E.PLANET_BODIES) + len(E.SMALL_BODIES)


def test_failed_small_body_falls_back_to_its_offline_orbit(fake_horizons):
    _, fetch = fake_horizons
    fetch.fail = {"Halley"}
    snap = E.fetch_horizons(JD)
    offline = approximate_orbit(BY_KEY["halley"], JD)
    assert math.dist(snap.orbits["halley"].position(JD), offline.position(JD)) < 1e-9
    fetch.fail = {"Jupiter"}  # a planet failing is an error: the app falls back to cache/offline
    with pytest.raises(RuntimeError):
        E.fetch_horizons(JD + 20)


def test_cache_round_trip_and_older_format(fake_horizons):
    E.fetch_horizons(JD)
    cached = E.load_cache(JD + 1)
    assert cached.source == "cache" and set(cached.orbits) == {b.key for b in E.ORBITING}
    # Caches written before small bodies had their own epoch kept everything in one table.
    data = json.loads(E.CACHE_FILE.read_text())
    data["states"].update(data.pop("small")["states"])
    E.CACHE_FILE.write_text(json.dumps(data))
    old = E.load_cache(JD + 1)
    assert old is not None and math.dist(old.orbits["ceres"].position(JD), cached.orbits["ceres"].position(JD)) < 1e-9
    E.CACHE_FILE.write_text("{half a file")
    assert E.load_cache(JD) is None  # a damaged cache is ignored, never fatal


def test_concurrent_moon_fetches_keep_every_system(fake_horizons):
    # Each moon system fetches on its own thread and shares one cache file: no entry may be lost.
    planets = ["mars", "jupiter", "saturn", "uranus", "neptune", "pluto"]
    threads = [threading.Thread(target=E.fetch_moons, args=(p, JD)) for p in planets * 3]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(json.loads(E.MOON_CACHE_FILE.read_text())) == sorted(planets)
    assert not list(E.MOON_CACHE_FILE.parent.glob("*.tmp"))  # atomic writes leave nothing behind


def test_horizons_requests_never_overlap(monkeypatch):
    # The planets and every moon system fetch on separate threads; Horizons must see one at a time.
    active, peak = [0], [0]

    class Reply:
        def __enter__(self):
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            time.sleep(0.01)
            return self

        def __exit__(self, *exc):
            active[0] -= 1

        def read(self, *a):
            return json.dumps(REPLY).encode()

    monkeypatch.setattr(E.urllib.request, "urlopen", lambda req, timeout: Reply())
    threads = [threading.Thread(target=E._fetch_state, args=("399", "Earth", JD, 5)) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1


def test_default_cache_dir(tmp_path):
    assert E._default_cache_dir("nt", {"LOCALAPPDATA": str(tmp_path)}) == tmp_path / "orrery"
    assert E._default_cache_dir("posix", {"XDG_CACHE_HOME": str(tmp_path / "xdg")}) == tmp_path / "xdg" / "orrery"
    assert E._default_cache_dir("posix", {}).parts[-2:] == (".cache", "orrery")


def _app(system=None):
    from orrery.app import App

    return App(argparse.Namespace(offline=True, scale=None, date=None, ascii=False, once=False, size=None,
                                  system=system, fresh=True, no_mouse=False))


def test_moon_view_and_event_times_agree_with_light_time():
    # Event times are as seen from Earth, and so is the moon view: a moon must look sunlit just
    # before its listed eclipse time and shadowed just after.
    from orrery.events import light_time
    from orrery.system import in_shadow

    app = _app("jupiter")
    app.poll_events(wait=True)
    ev = next(e for e in app.events.upcoming(app.sim_jd) if e.kind == "eclipse" and e.start)

    def shadowed(jd):
        app.sim_jd = jd
        p = app.positions()
        sun = tuple(-x for x in p["jupiter"])
        d = math.hypot(*sun)
        moon = app.feed("jupiter").positions(jd - light_time(p["jupiter"], p["earth"]))[ev.body]
        return in_shadow(moon, tuple(x / d for x in sun), BY_KEY["jupiter"].radius_km, d)

    lt = light_time(app.positions()["jupiter"], app.positions()["earth"])
    assert 30 / 1440 < lt < 60 / 1440  # Jupiter is 30-60 light minutes away
    assert not shadowed(ev.start - 60 / 86400) and shadowed(ev.start + 60 / 86400)


def test_idle_main_loop_draws_only_when_something_changes(monkeypatch):
    import orrery.app as A

    script = [[" "]] + [[]] * 40 + [["+"]] + [[]] * 10 + [["q"]]  # pause, wait, zoom once, wait, quit

    class FakeTerm:
        def __init__(self, mouse=True):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def size(self):
            return (120, 36)

        def write(self, s):
            pass

        def read_keys(self):
            return script.pop(0)

    monkeypatch.setattr(A.T, "Terminal", FakeTerm)
    app = _app()
    frames = []
    real = app.frame_rows
    monkeypatch.setattr(app, "frame_rows", lambda *a: frames.append(1) or real(*a))
    app.run()
    assert 2 <= len(frames) <= 5  # the first frame, the pause, the zoom; not one per tick (~53)
