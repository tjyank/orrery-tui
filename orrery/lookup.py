"""Looking up any asteroid or comet on JPL Horizons by name, and keeping the ones the user adds."""
from __future__ import annotations

import json
import math
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from .bodies import BY_KEY, J2000, USER_BODIES, Body, register
from .ephemeris import _write_json_atomic, parse_vectors, query_vectors
from .kepler import orbit_from_state
from .settings import settings_path

ASTEROID_COLOR = (196, 182, 160)
COMET_COLOR = (160, 214, 255)
MAX_CHOICES = 6  # matches listed when a name is ambiguous


class LookupFailed(Exception):
    """A lookup that can't add a body; the message is meant for the user."""


@dataclass(frozen=True)
class Match:
    designation: str
    name: str


def bodies_path() -> Path:
    if "ORRERY_BODIES" in os.environ:
        return Path(os.environ["ORRERY_BODIES"])
    return settings_path().with_name("bodies.json")


# ---- reading Horizons replies -----------------------------------------------------------------

def parse_matches(text: str) -> list[Match]:
    """The rows of a small-body search that matched more than one record."""
    lines = text.splitlines()
    try:
        header = next(i for i, l in enumerate(lines) if "Primary Desig" in l)
    except StopIteration:
        return []
    # Columns are left-aligned under their headers; the name is last, headed "Name" or ">MATCH NAME<".
    head = lines[header]
    desig_at = head.index("Primary Desig")
    name_at = head.find(">MATCH NAME<") if ">MATCH NAME<" in head else head.rfind("Name")
    out = []
    for line in lines[header + 2:]:  # +2 skips the ---- underline
        if not line.strip():
            break
        desig, name = line[desig_at:name_at].strip(), line[name_at:].strip()
        if desig:
            out.append(Match(desig, name))
    return out


def body_name(text: str) -> str | None:
    """'Target body name: 99942 Apophis (2004 MN4)   {source: ...}' -> '99942 Apophis (2004 MN4)'.

    The line can carry more after a wide gap (an ID, a source), so the name ends at two spaces."""
    m = re.search(r"Target body name:\s*(.+?)(?:\s{2,}|\s*\{|\s*$)", text, re.M)
    return m.group(1).strip() if m else None


def body_radius_km(text: str) -> float | None:
    m = re.search(r"\bRAD\s*=\s*([0-9]*\.?[0-9]+)", text)
    return float(m.group(1)) if m else None


_COMET = re.compile(r"(^|[\s(])(\d+[PDI](/|\b)|[CPDXI]/\d{3,4}\b)")


def is_comet(full_name: str) -> bool:
    return bool(_COMET.search(full_name))


def short_name(full_name: str) -> str:
    """'99942 Apophis (2004 MN4)' -> 'Apophis'; '1P/Halley' -> 'Halley'; 'Hale-Bopp (C/1995 O1)' -> 'Hale-Bopp'."""
    name = re.sub(r"\s*\(.*\)\s*$", "", full_name).strip()  # a trailing (designation)
    if not re.fullmatch(r"\d{4}\s+[A-Z]{1,2}\d*", name):  # leave a designation like "2004 MN4" whole
        name = re.sub(r"^\d+\s+(?=\D)", "", name)  # a leading catalogue number
    name = re.sub(r"^\d+[PDI]/", "", name)  # a periodic comet's number
    return name or full_name.strip()


# ---- the lookup ---------------------------------------------------------------------------------

@dataclass
class Found:
    body: Body
    state: tuple[tuple, tuple]  # heliocentric position (au) and velocity (au/day) at the lookup time
    jd: float


def lookup(query: str, jd: float, timeout: float = 30.0) -> Found:
    """Resolve a name or designation on Horizons; raises LookupFailed with a message for the user."""
    query = query.strip().rstrip(";").strip()
    if not query:
        raise LookupFailed("Type a name or designation")
    command = f"{query};"  # the semicolon asks for a small body (an asteroid or comet), not a planet
    reply = _query(command, jd, timeout)
    text = reply.get("result", "")
    if "$$SOE" not in text:
        matches = parse_matches(text)
        if not matches:
            raise LookupFailed(f"No asteroid or comet called {query!r} on JPL Horizons")
        desigs = list(dict.fromkeys(m.designation for m in matches))
        if len(desigs) > 1:
            listed = ", ".join(f"{m.designation} {m.name}".strip() for m in _unique(matches)[:MAX_CHOICES])
            more = "…" if len(desigs) > MAX_CHOICES else ""
            raise LookupFailed(f"{query!r} matches several: {listed}{more}. Type a designation")
        # Several records of one designation: a comet's apparitions. Take the one nearest now.
        command = f"DES={desigs[0]};CAP;"
        reply = _query(command, jd, timeout)
        text = reply.get("result", "")
    full = body_name(text) or query
    try:
        state = parse_vectors(reply, full)
    except RuntimeError as exc:
        raise LookupFailed(f"Horizons couldn't give a position for {full!r}") from exc
    try:
        orbit = orbit_from_state(*state, epoch=jd)
    except ValueError:
        raise LookupFailed(f"{short_name(full)} isn't on a closed orbit: it's passing through the solar "
                           "system, which this orrery can't draw yet") from None
    comet = is_comet(full)
    body = Body(_new_key(full), short_name(full), command, COMET_COLOR if comet else ASTEROID_COLOR,
                body_radius_km(text) or 0.0, 0.0, *_elements(orbit, jd), kind="comet" if comet else "asteroid")
    return Found(body, state, jd)


def _query(command: str, jd: float, timeout: float) -> dict:
    try:
        reply = query_vectors(command, jd, timeout, object_data=True)
    except OSError as exc:  # network, TLS, HTTP
        raise LookupFailed(f"Couldn't reach JPL Horizons: {exc}") from None
    if "error" in reply:
        raise LookupFailed(f"Horizons: {reply['error'].strip()}")
    return reply


def _unique(matches: list[Match]) -> list[Match]:
    seen, out = set(), []
    for m in matches:
        if m.designation not in seen:
            seen.add(m.designation)
            out.append(m)
    return out


def _new_key(full_name: str) -> str:
    base = "user-" + (re.sub(r"[^a-z0-9]+", "-", full_name.lower()).strip("-") or "body")
    key, n = base, 2
    while key in BY_KEY:
        key, n = f"{base}-{n}", n + 1
    return key


def _elements(orbit, jd: float) -> tuple[tuple, tuple]:
    """The osculating orbit in the catalogue's (a, e, I, L, long.peri, long.node) form, with the
    mean longitude advancing at the mean motion, so the body has an offline orbit too."""
    deg = math.degrees
    n = deg(orbit.mean_motion)  # deg / day
    peri = deg(orbit.node + orbit.argp) % 360
    mean_long = (peri + deg(orbit.m0) - n * (jd - J2000)) % 360  # at J2000
    return ((orbit.a, orbit.e, deg(orbit.i), mean_long, peri, deg(orbit.node) % 360),
            (0.0, 0.0, 0.0, n * 36525, 0.0, 0.0))


def same_object(found: Body) -> Body | None:
    """An already-shown body that is the found one (same command or same name), if any."""
    for b in BY_KEY.values():
        if b.horizons_id == found.horizons_id or b.name.lower() == found.name.lower():
            return b
    return None


# ---- keeping the user's bodies ----------------------------------------------------------------------

_FIELDS = ("key", "name", "horizons_id", "color", "radius_km", "elements", "rates", "kind")


def save_user_bodies(path: Path | None = None) -> None:
    _write_json_atomic(path or bodies_path(), [{f: getattr(b, f) for f in _FIELDS} for b in USER_BODIES])


def load_user_bodies(path: Path | None = None) -> list[Body]:
    """Register the bodies saved last time. A damaged file or entry is skipped, never fatal."""
    try:
        entries = json.loads((path or bodies_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    added = []
    for e in entries if isinstance(entries, list) else []:
        try:
            body = Body(str(e["key"]), str(e["name"]), str(e["horizons_id"]), tuple(int(c) for c in e["color"]),
                        float(e["radius_km"]), 0.0, tuple(float(x) for x in e["elements"]),
                        tuple(float(x) for x in e["rates"]), kind=e["kind"] if e["kind"] in ("asteroid", "comet") else "asteroid")
            if len(body.elements) == 6 and len(body.rates) == 6 and body.key not in BY_KEY:
                register(body)
                added.append(body)
        except (KeyError, TypeError, ValueError):
            continue
    return added


class LookupService:
    """Runs one lookup at a time on a background thread; the UI polls `take()` for the outcome."""

    def __init__(self):
        self._lock = threading.Lock()
        self.query: str | None = None  # being looked up
        self._result: Found | LookupFailed | None = None

    @property
    def busy(self) -> bool:
        return self.query is not None

    def start(self, query: str, jd: float) -> bool:
        with self._lock:
            if self.query is not None:
                return False
            self.query = query

        def run():
            try:
                result = lookup(query, jd)
            except LookupFailed as exc:
                result = exc
            except Exception as exc:  # never let the worker die silently
                result = LookupFailed(f"Lookup failed: {exc}")
            with self._lock:
                self._result, self.query = result, None

        threading.Thread(target=run, daemon=True).start()
        return True

    def take(self) -> Found | LookupFailed | None:
        with self._lock:
            result, self._result = self._result, None
        return result
