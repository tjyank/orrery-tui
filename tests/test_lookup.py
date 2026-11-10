"""Looking up and adding objects by name, with canned Horizons replies (no network)."""
import json
import math

import pytest

from orrery import bodies, lookup as L
from orrery import ephemeris as E

JD = 2461343.0
TABLE = ("$$SOE\n2461343.000000000, A.D. 2026-Oct-29 12:00:00.0000, {x}, {y}, 0.0, {vx}, {vy}, 0.0,\n$$EOE\n")


def reply(name: str | None = None, radius: str | None = None, x=0.9, y=0.3, vx=-0.005, vy=0.016) -> dict:
    head = f"Target body name: {name}      {{source: JPL#220}}\n" if name else ""
    head += f"  RAD= {radius}   ROTPER= 30.4\n" if radius else ""
    return {"result": head + TABLE.format(x=x, y=y, vx=vx, vy=vy)}


MATCHES_ONE_COMET = {"result": """ Matching small-bodies:

    Record #  Epoch-yr  >MATCH DESIG<  Primary Desig  Name
    --------  --------  -------------  -------------  -------------------------
    90000001    -239    1P             1P              Halley
    90000002    -163    1P             1P              Halley

"""}
MATCHES_SEVERAL = {"result": """ Matching small-bodies:

    Record #  Epoch-yr  Primary Desig  >MATCH NAME<
    --------  --------  -------------  -------------------------
    90002102    1978    C/1977 V1       Tsuchinshan
    90004439    2017    C/2017 E2       Tsuchinshan

"""}
NO_MATCH = {"result": " Matching small-bodies:\n    No matches found.\n"}


@pytest.fixture
def horizons(monkeypatch):
    """Answer each command from a table; record the commands asked."""
    answers, asked = {}, []

    def query(command, jd, timeout=20.0, center="10", object_data=False, retries=3):
        asked.append(command)
        return answers[command]

    monkeypatch.setattr(L, "query_vectors", query)
    return answers, asked


@pytest.fixture
def clean_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("ORRERY_BODIES", str(tmp_path / "bodies.json"))
    yield
    for b in list(bodies.USER_BODIES):
        bodies.unregister(b.key)


def test_unique_name(horizons):
    answers, asked = horizons
    answers["Apophis;"] = reply("99942 Apophis (2004 MN4)", ".170")
    found = L.lookup("Apophis", JD)
    b = found.body
    assert (b.name, b.kind, b.radius_km, b.horizons_id) == ("Apophis", "asteroid", 0.17, "Apophis;")
    assert asked == ["Apophis;"] and b.key.startswith("user-")
    # Its offline orbit reproduces the looked-up position at the lookup time.
    assert math.dist(bodies.approximate_orbit(b, JD).position(JD), found.state[0]) < 1e-9


def test_comet_apparitions_use_the_nearest(horizons):
    answers, asked = horizons
    answers["1P;"] = MATCHES_ONE_COMET
    answers["DES=1P;CAP;"] = reply("1P/Halley", "5.5")
    b = L.lookup("1P", JD).body
    assert asked == ["1P;", "DES=1P;CAP;"] and b.horizons_id == "DES=1P;CAP;"
    assert (b.name, b.kind) == ("Halley", "comet")


@pytest.mark.parametrize("answer, message", [
    (MATCHES_SEVERAL, "matches several: C/1977 V1 Tsuchinshan, C/2017 E2 Tsuchinshan"),
    (NO_MATCH, "No asteroid or comet called"),
    ({"error": "Bad request"}, "Horizons: Bad request"),
    (reply("Fast (C/2023 A3)", vx=0.5, vy=0.5), "isn't on a closed orbit"),  # far above escape speed
    (reply("Ghost Rock", x=0, y=0, vx=0, vy=0), "couldn't give a position"),  # degenerate state
])
def test_lookups_that_cannot_add(horizons, answer, message):
    answers, _ = horizons
    answers["Thing;"] = answer
    with pytest.raises(L.LookupFailed, match=message):
        L.lookup("Thing", JD)


def test_names():
    assert L.short_name("99942 Apophis (2004 MN4)") == "Apophis"
    assert L.short_name("1P/Halley") == "Halley"
    assert L.short_name("Hale-Bopp (C/1995 O1)") == "Hale-Bopp"
    assert L.short_name("2004 MN4") == "2004 MN4"
    assert L.is_comet("Hale-Bopp (C/1995 O1)") and L.is_comet("67P/Churyumov-Gerasimenko")
    assert not L.is_comet("433 Eros (A898 PA)") and not L.is_comet("2004 MN4")
    assert L.body_name("Target body name: 101955 Bennu (1999 RQ36)     2101955\n") == "101955 Bennu (1999 RQ36)"


def test_added_bodies_persist_join_the_data_and_can_be_removed(horizons, clean_registry):
    answers, _ = horizons
    answers["Apophis;"] = reply("99942 Apophis (2004 MN4)", ".170")
    body = L.lookup("Apophis", JD).body
    bodies.register(body)
    L.save_user_bodies()
    assert body in bodies.SMALL_BODIES and bodies.BY_KEY[body.key] is body
    assert body.key in E.offline_snapshot(JD).orbits  # every data source now includes it

    bodies.unregister(body.key)
    assert body.key not in bodies.BY_KEY and body not in bodies.ALL_BODIES
    restored = L.load_user_bodies()  # as on the next start
    assert [b.key for b in restored] == [body.key] and restored[0].elements == body.elements


def test_damaged_saved_bodies_are_skipped(tmp_path, clean_registry):
    path = tmp_path / "bodies.json"
    path.write_text(json.dumps([{"key": "user-x"}, "nonsense", {"key": "ceres", "name": "Imposter"}]))
    assert L.load_user_bodies(path) == []
    path.write_text("{half")
    assert L.load_user_bodies(path) == []
