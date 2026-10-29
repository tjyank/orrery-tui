"""Interactive orrery: event loop, HUD and info panel."""
from __future__ import annotations

import argparse
import math
import sys
import threading
import time
from datetime import datetime
from functools import partial

from . import settings
from . import terminal as T
from .bodies import ALL_BODIES, BY_KEY, PLANETS, SUN
from .ephemeris import (EphemerisService, Snapshot, best_moon_snapshot, best_snapshot, jd_now, jd_to_datetime,
                        load_cache, load_moon_cache, offline_moon_snapshot, offline_snapshot)
from .events import EARTH_HORIZON_DAYS, SYSTEM_HORIZON_DAYS, Event, MoonTrack, system_events
from .jumps import datetime_to_jd, next_perihelion, parse_when, presets
from .kepler import AU_KM, DAY_S, Orbit, Vec3, orbit_from_state
from .lunar import earth_heliocentric, moon_geocentric, moon_state
from .moons import MOON_BY_KEY, MOONS_OF, spin_axis
from .render import SCALE_ORDER, Camera, Canvas, Scene, SceneOptions, draw_scene, shade, to_display
from .system import SystemScene, draw_system, in_shadow

C_KM_S = 299_792.458
PANEL_W = 34
MIN_PANEL_COLS = 100
REANCHOR_DRIFT_DAYS = 20  # re-fetch Horizons once simulated time drifts this far from the data epoch
REFRESH_EVERY_S = 15 * 60  # refresh live data this often while tracking the present
MIN_FETCH_GAP_S = 30  # never hit JPL more often than this (except on manual refresh)
MOON_REANCHOR_DRIFT_DAYS = 2  # moon orbits are perturbed faster, so re-fetch sooner
FLASH_S = 4  # how long transient footer messages stay up
JUMP_RESET_DAYS = 45  # after a jump this far, switch to the offline model at once while Horizons loads
EVENT_ALERT_MAX_RATE = 3_600  # announce events as they begin only at up to 1 hr/s (any faster is a blur)
DOUBLE_CLICK_S = 0.5  # Windows' default double-click time

# Simulated seconds per real second.
RATES = [-31_557_600, -2_592_000, -604_800, -86_400, -3_600, -60, -1, 1, 60, 3_600, 86_400, 604_800, 2_592_000, 31_557_600]
REALTIME = RATES.index(1)
RATE_NAMES = {1: "real time", 60: "1 min/s", 3_600: "1 hr/s", 86_400: "1 day/s", 604_800: "1 wk/s",
              2_592_000: "30 days/s", 31_557_600: "1 yr/s"}

FG = (210, 214, 224)
DIM = (120, 126, 142)
ACCENT = (255, 200, 90)
STATUS_COLORS = {"live": (110, 220, 130), "cache": (240, 200, 90), "offline": (240, 120, 100),
                 "analytic": (140, 200, 255)}

# The ? legend, by section. Keep it in step with App.handle and handle_mouse.
KEY_LEGEND = [
    ("Camera", [
        ("← → / h l", "rotate"),
        ("↑ ↓ / k j", "tilt"),
        ("+ -", "zoom"),
        ("i", "isometric view"),
        ("a", "auto-spin"),
        ("m", "distance scale"),
    ]),
    ("Bodies", [
        ("Tab Shift-Tab", "select next / previous"),
        ("f", "follow selected"),
        ("Enter / v", "open a planet's moons"),
        ("b", "small bodies on/off"),
    ]),
    ("Moon view", [
        ("Tab", "cycle moons"),
        ("[ ]", "previous / next planet"),
        ("Esc Enter v", "back to solar system"),
    ]),
    ("Time", [
        (", .", "slower / faster, reverse"),
        ("space", "pause"),
        ("t", "now, at real time"),
        ("J", "jump to a date or event"),
    ]),
    ("Display", [
        ("o d n", "orbits, drop lines, names"),
        ("p", "info panel"),
        ("?", "this legend"),
    ]),
    ("Mouse", [
        ("drag", "rotate and tilt"),
        ("scroll", "zoom"),
        ("click", "select"),
        ("double-click", "open moons / follow"),
        ("right-click", "back"),
    ]),
    ("Other", [
        ("r", "refetch from Horizons"),
        ("q", "quit"),
    ]),
]


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _len(a: Vec3) -> float:
    return math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)


def _angle(a: Vec3, b: Vec3) -> float:
    c = (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / (_len(a) * _len(b))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _unit(a: Vec3) -> Vec3:
    n = _len(a)
    return (a[0] / n, a[1] / n, a[2] / n)


def _period(days: float) -> str:
    return f"{days * 24:.2f} h" if days < 2 else f"{days:.3f} d" if days < 100 else f"{days / 365.25:.3f} yr"


def _countdown(days: float) -> str:
    """Compact time until something: '45s', '12m', '2h14m', '3d4h'."""
    s = days * DAY_S
    if s < 60:
        return f"{max(s, 0):.0f}s"
    if s < 3600:
        return f"{s / 60:.0f}m"
    if s < 86400:
        return f"{int(s // 3600)}h{int(s % 3600 // 60):02d}m"
    return f"{int(s // 86400)}d{int(s % 86400 // 3600)}h"


def _short_event(ev: Event) -> str:
    return {"eclipse": f"{ev.name} eclipsed", "transit": f"{ev.name} transit",
            "occultation": f"{ev.name} occulted"}.get(ev.kind, ev.name)


class MoonFeed:
    """Moon orbits for one planet: Horizons in the background, cache or mean elements meanwhile.

    Earth's Moon is the exception: it comes from the analytic lunar theory, which beats an
    osculating orbit for anything beyond a few days, and needs no network.
    """

    def __init__(self, planet: str, jd: float, allow_network: bool):
        self.planet = planet
        self.analytic = planet == "earth"
        self.service = EphemerisService(allow_network, loader=partial(best_moon_snapshot, planet))
        if self.analytic:
            self.snapshot = Snapshot({}, jd, "analytic")
        else:
            self.snapshot: Snapshot = load_moon_cache(planet, jd) or offline_moon_snapshot(planet, jd)
        self.last_request = -1e9
        self.last_live = -1e9

    def request(self, jd: float, force: bool = False) -> None:
        now = time.monotonic()
        if self.analytic or not MOONS_OF[self.planet]:
            return
        if force or now - self.last_request >= MIN_FETCH_GAP_S:
            if self.service.request(jd):
                self.last_request = now

    def poll(self, jd: float) -> Snapshot | None:
        """Take a newly arrived snapshot (returned once) and re-fetch when the data goes stale."""
        if self.analytic:
            return None
        snap = self.service.take()
        if snap is not None:
            self.snapshot = snap
            if snap.source == "live":
                self.last_live = time.monotonic()
        tracking_now = abs(jd - jd_now()) < 1
        stale = time.monotonic() - self.last_live > REFRESH_EVERY_S
        drifted = abs(jd - self.snapshot.epoch) > MOON_REANCHOR_DRIFT_DAYS
        if not self.service.busy and (drifted or (tracking_now and stale)):
            self.request(jd)
        return snap

    def reset(self, jd: float) -> None:
        """After a long time jump: the offline orbits until Horizons answers."""
        if not self.analytic:
            self.snapshot = offline_moon_snapshot(self.planet, jd)

    def orbits(self, jd: float) -> dict[str, Orbit]:
        if self.analytic:
            r, v = moon_state(jd)
            return {"moon": orbit_from_state(r, v, jd, MOON_BY_KEY["moon"].mu)}
        return self.snapshot.orbits

    def positions(self, jd: float) -> dict[str, Vec3]:
        if self.analytic:
            return {"moon": moon_geocentric(jd)}
        return {k: o.position(jd) for k, o in self.snapshot.orbits.items()}

    def tracks(self) -> list[MoonTrack]:
        out = []
        for m in MOONS_OF[self.planet]:
            fn = moon_geocentric if self.analytic else self.snapshot.orbits[m.key].position
            out.append(MoonTrack(m.key, m.name, m.radius_km, m.period_days, fn))
        return out


class EventFeed:
    """Upcoming moon events for the open system, searched on a background thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self.key = None  # what the current list was computed for
        self.t0 = 0.0
        self.horizon = 0.0
        self.events: list[Event] = []
        self.busy = False

    def wanted(self, key, jd: float) -> bool:
        """Does the list need (re)computing for this system and data at this time?"""
        if self.busy:
            return False
        if key != self.key:
            return True
        return jd < self.t0 or jd > self.t0 + self.horizon / 2

    def request(self, key, planet: str, radius_km: float, planet_pos, tracks: list[MoonTrack], jd: float,
                wait: bool = False) -> None:
        horizon = EARTH_HORIZON_DAYS if planet == "earth" else SYSTEM_HORIZON_DAYS
        with self._lock:
            if self.busy:
                return
            self.busy = True

        def run():
            try:
                found = system_events(planet, radius_km, planet_pos, earth_heliocentric, tracks, jd, horizon)
            except Exception:  # an event search must never take the UI down
                found = []
            with self._lock:
                self.key, self.t0, self.horizon, self.events, self.busy = key, jd, horizon, found, False

        if wait:
            run()
        else:
            threading.Thread(target=run, daemon=True).start()

    def upcoming(self, jd: float, key=None) -> list[Event]:
        """Events not yet over at `jd`, soonest first; empty if computed for other data than `key`."""
        with self._lock:
            if key is not None and key != self.key:
                return []
            return [e for e in self.events if (e.end if e.end is not None else math.inf) >= jd]


class App:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.cam = Camera()
        self.opts = SceneOptions(scale_mode=args.scale or "log", ascii=args.ascii, selected="earth")
        self.start_jd = jd_now() if args.date is None else args.date
        self.sim_jd = self.start_jd
        self.rate_idx = REALTIME
        self.paused = False
        self.follow = False
        self.spin = False
        self.show_panel = True
        self.service = EphemerisService(allow_network=not args.offline)
        self.snapshot: Snapshot = load_cache(self.sim_jd) or offline_snapshot(self.sim_jd)
        self.last_fetch_request = -1e9
        self.last_live_at = -1e9
        self.message = ""
        self.flash, self.flash_until = "", 0.0
        self.running = True
        # Planetary system view: its own camera and options, so the orrery view is kept as it was.
        self.system: str | None = None
        self.sys_cam = Camera()
        self.sys_opts = SceneOptions(scale_mode="log", ascii=args.ascii)
        self.sys_follow = False
        self.feeds: dict[str, MoonFeed] = {}
        self.events = EventFeed()
        self.last_alert_jd = self.sim_jd
        # Jump menu (J) and its date prompt
        self.menu: str | None = None  # None | "jump" | "date"
        self.menu_idx = 0
        self.prompt = ""
        self.show_keys = False  # the ? legend; unlike the menu, keys keep working while it's open
        self.n_overlay = 0  # rows at the end of a frame that are painted over the others (the legend)
        # Mouse: what is on screen where (terminal cells), and any drag in progress
        self.hits: dict[str, tuple[int, int]] = {}
        self.labels: list[tuple[int, int, int, str]] = []  # clickable names: (row, first col, end col, key)
        self.panel_hits: dict[int, str] = {}
        self.menu_hits: dict[int, int] = {}
        self.scene_cols = 0
        self.drag: tuple[int, int] | None = None
        self.dragged = False
        self.last_click: tuple[str, float] | None = None
        self.disc: tuple[float, float, float] | None = None  # moon view: planet disc, terminal cells

        if not args.fresh and not args.once:
            self.apply_settings(settings.load())
        if args.scale:  # an explicit --scale beats the remembered one
            self.opts.scale_mode = args.scale
        if args.system:
            self.enter_system(args.system)

    # ---- settings ---------------------------------------------------------------------
    def settings(self) -> dict:
        c, s, o, so = self.cam, self.sys_cam, self.opts, self.sys_opts
        return {
            "yaw": c.yaw, "pitch": c.pitch, "zoom": c.zoom, "scale": o.scale_mode,
            "orbits": o.show_orbits, "drops": o.show_drops, "names": o.show_labels, "small": o.show_small,
            "selected": o.selected, "panel": self.show_panel, "spin": self.spin, "follow": self.follow,
            "sys_yaw": s.yaw, "sys_pitch": s.pitch, "sys_zoom": s.zoom, "sys_scale": so.scale_mode,
            "sys_orbits": so.show_orbits, "sys_drops": so.show_drops,
            "sys_names": so.show_labels,
        }

    def apply_settings(self, d: dict) -> None:
        def clamp(v, lo, hi):
            return max(lo, min(hi, v))

        c, s, o, so = self.cam, self.sys_cam, self.opts, self.sys_opts
        c.yaw = d.get("yaw", c.yaw) % math.tau
        c.pitch = clamp(d.get("pitch", c.pitch), -math.pi / 2, math.pi / 2)
        c.zoom = clamp(d.get("zoom", c.zoom), 0.2, 400.0)
        s.yaw = d.get("sys_yaw", s.yaw) % math.tau
        s.pitch = clamp(d.get("sys_pitch", s.pitch), -math.pi / 2, math.pi / 2)
        s.zoom = clamp(d.get("sys_zoom", s.zoom), 0.2, 400.0)
        if d.get("scale") in SCALE_ORDER:
            o.scale_mode = d["scale"]
        if d.get("sys_scale") in SCALE_ORDER:
            so.scale_mode = d["sys_scale"]
        if d.get("selected") in BY_KEY:
            o.selected = d["selected"]
        for key, obj, attr in (("orbits", o, "show_orbits"), ("drops", o, "show_drops"),
                               ("names", o, "show_labels"), ("small", o, "show_small"),
                               ("sys_orbits", so, "show_orbits"),
                               ("sys_drops", so, "show_drops"), ("sys_names", so, "show_labels"),
                               ("panel", self, "show_panel"), ("spin", self, "spin"), ("follow", self, "follow")):
            if key in d:
                setattr(obj, attr, d[key])

    # ---- planetary system view --------------------------------------------------------
    @property
    def active_cam(self) -> Camera:
        return self.sys_cam if self.system else self.cam

    @property
    def active_opts(self) -> SceneOptions:
        return self.sys_opts if self.system else self.opts

    def feed(self, planet: str) -> MoonFeed:
        if planet not in self.feeds:
            self.feeds[planet] = MoonFeed(planet, self.sim_jd, not self.args.offline)
        return self.feeds[planet]

    def enter_system(self, planet: str) -> None:
        self.system = self.opts.selected = planet
        self.sys_opts.selected = planet
        self.sys_follow = False
        self.sys_cam.target = (0.0, 0.0, 0.0)
        if not self.args.once:
            self.feed(planet).request(self.sim_jd)
        if not MOONS_OF[planet]:
            self.say(f"{BY_KEY[planet].name} has no moons")

    def say(self, text: str) -> None:
        self.flash, self.flash_until = text, time.monotonic() + FLASH_S

    # ---- data -------------------------------------------------------------------------
    def request_data(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_fetch_request < MIN_FETCH_GAP_S:
            return
        if self.service.request(self.sim_jd):
            self.last_fetch_request = now

    def poll_data(self) -> None:
        snap = self.service.take()
        if snap is not None:
            self.snapshot = snap
            if snap.source == "live":
                self.last_live_at = time.monotonic()
            self.message = f"Horizons unavailable: {snap.error}" if snap.error else ""
        tracking_now = abs(self.sim_jd - jd_now()) < 1
        stale = time.monotonic() - self.last_live_at > REFRESH_EVERY_S
        drifted = abs(self.sim_jd - self.snapshot.epoch) > REANCHOR_DRIFT_DAYS
        if not self.service.busy and (drifted or (tracking_now and stale)):
            self.request_data()
        if self.system:
            snap = self.feed(self.system).poll(self.sim_jd)
            if snap is not None and snap.error:
                self.say(f"Horizons unavailable for moons: {snap.error}")
            self.poll_events()

    def events_key(self):
        """Identifies the data an event list was computed from: a new snapshot means a new list."""
        return (self.system, id(self.feed(self.system).snapshot), id(self.snapshot))

    def poll_events(self, wait: bool = False) -> None:
        """Keep the open system's event list current, and announce events as they begin."""
        if not MOONS_OF[self.system]:
            return
        key = self.events_key()
        if self.events.wanted(key, self.sim_jd):
            self.events.request(key, self.system, BY_KEY[self.system].radius_km,
                                self.snapshot.orbits[self.system].position, self.feed(self.system).tracks(),
                                self.sim_jd, wait)
        rate = 0 if self.paused else RATES[self.rate_idx]
        if 0 < rate <= EVENT_ALERT_MAX_RATE:
            for ev in self.events.upcoming(self.last_alert_jd, key):
                if ev.start is not None and self.last_alert_jd < ev.start <= self.sim_jd:
                    self.say("Now: " + ev.title(BY_KEY[self.system].name))
        self.last_alert_jd = self.sim_jd

    def positions(self) -> dict[str, Vec3]:
        pos = {"sun": (0.0, 0.0, 0.0)}
        pos.update({k: o.position(self.sim_jd) for k, o in self.snapshot.orbits.items()})
        return pos

    # ---- time jumps -------------------------------------------------------------------
    def jump_to(self, jd: float, select: str | None = None, system: str | None = None, rate: int | None = None) -> None:
        far = abs(jd - self.sim_jd) > JUMP_RESET_DAYS
        self.sim_jd = self.last_alert_jd = jd
        self.paused = False
        if rate is not None:
            self.rate_idx = RATES.index(rate)
        if far:
            # Osculating orbits from today drift badly over years: switch to the mean elements
            # straight away, and let Horizons refine them for the new date.
            self.snapshot = offline_snapshot(jd)
            for f in self.feeds.values():
                f.reset(jd)
        if not self.args.once:
            self.request_data(force=True)
            for f in self.feeds.values():
                f.request(jd, force=True)
        if system:
            self.enter_system(system)
        elif select and self.system and select != self.system:
            self.system = None  # the target is a body in the main view
        if select and not system:
            self.opts.selected = select
            if BY_KEY[select].small:
                self.opts.show_small = True
        self.say(f"Jumped to {jd_to_datetime(jd):%Y-%m-%d %H:%M} UTC")

    def _presets(self):
        return presets(lambda key, t: self.snapshot.orbits[key].position(t), self.snapshot.orbits)

    def menu_items(self) -> list[str]:
        return ["Now", "Type a date or offset…", *(p.label for p in self._presets())]

    def choose(self, idx: int) -> None:
        self.menu = None
        if idx == 0:
            self.jump_to(jd_now(), rate=1)
        elif idx == 1:
            self.menu, self.prompt = "date", ""
        else:
            preset = self._presets()[idx - 2]
            target = preset.when(self.sim_jd)
            if target is None:
                self.say(f"{preset.label}: none found in the next few years")
            else:
                self.jump_to(target, preset.select, preset.system, preset.rate)

    def handle_menu(self, key: str) -> None:
        if self.menu == "date":
            if key == T.ESC:
                self.menu = None
            elif key == T.ENTER:
                self.menu = None
                try:
                    self.jump_to(parse_when(self.prompt, self.sim_jd))
                except (ValueError, OverflowError):
                    self.say(f"Couldn't read {self.prompt!r}: try 2061-07-28, 1989-08-25 03:56, +30d or -2y")
            elif key == T.BACKSPACE:
                self.prompt = self.prompt[:-1]
            elif len(key) == 1 and key.isprintable() and len(self.prompt) < 32:
                self.prompt += key
            return
        n = len(self.menu_items())
        if key in (T.ESC, "J", "q"):
            self.menu = None
        elif key in (T.UP, "k"):
            self.menu_idx = (self.menu_idx - 1) % n
        elif key in (T.DOWN, "j", T.TAB):
            self.menu_idx = (self.menu_idx + 1) % n
        elif key == T.ENTER:
            self.choose(self.menu_idx)
        elif key.isdigit() and 0 < int(key) <= min(n, 9):
            self.choose(int(key) - 1)

    # ---- input ------------------------------------------------------------------------
    def visible_keys(self) -> list[str]:
        """The bodies Tab cycles through."""
        if self.system:
            return [self.system, *(m.key for m in MOONS_OF[self.system])]
        return [b.key for b in ALL_BODIES if self.opts.show_small or not b.small]

    def handle(self, key) -> None:
        if isinstance(key, T.Mouse):
            self.handle_mouse(key)
            return
        if self.menu:
            self.handle_menu(key)
            return
        step = math.radians(5)
        cam, opts = self.active_cam, self.active_opts
        keys = self.visible_keys()
        planets = [p.key for p in PLANETS]
        if key == "?":
            self.show_keys = not self.show_keys
        elif key == T.ESC and self.show_keys:  # Esc closes the legend before it backs out or quits
            self.show_keys = False
        elif key in ("q", "Q") or (key == T.ESC and not self.system):
            self.running = False
        elif key in (T.ESC, T.ENTER, "v") and self.system:
            self.system = None
        elif key in (T.ENTER, "v"):
            if self.opts.selected in planets:
                self.enter_system(self.opts.selected)
            else:
                self.say("Select a planet (Tab) to view its moons")
        elif key in ("[", "]") and self.system:
            i = planets.index(self.system)
            self.enter_system(planets[(i + (1 if key == "]" else -1)) % len(planets)])
        elif key == "J":
            self.menu, self.menu_idx = "jump", 0
        elif key in (T.LEFT, "h"):
            cam.rotate(dyaw=-step)
        elif key in (T.RIGHT, "l"):
            cam.rotate(dyaw=step)
        elif key in (T.UP, "k"):
            cam.rotate(dpitch=step)
        elif key in (T.DOWN, "j"):
            cam.rotate(dpitch=-step)
        elif key in ("+", "="):
            cam.zoom = min(cam.zoom * 1.25, 400.0)
        elif key in ("-", "_"):
            cam.zoom = max(cam.zoom / 1.25, 0.2)
        elif key in (T.TAB, T.BTAB):
            i = keys.index(opts.selected) if opts.selected in keys else 0
            opts.selected = keys[(i + (1 if key == T.TAB else -1)) % len(keys)]
        elif key == "f" and self.system:
            self.sys_follow = not self.sys_follow
        elif key == "f":
            self.follow = not self.follow
        elif key == ".":
            self.rate_idx = min(self.rate_idx + 1, len(RATES) - 1)
            self.paused = False
        elif key == ",":
            self.rate_idx = max(self.rate_idx - 1, 0)
            self.paused = False
        elif key == " ":
            self.paused = not self.paused
        elif key == "t":
            self.sim_jd, self.rate_idx, self.paused = jd_now(), REALTIME, False
            self.last_alert_jd = self.sim_jd
        elif key == "b" and not self.system:
            self.opts.show_small = not self.opts.show_small
            self.say(f"Dwarf planets, asteroids and comets {'shown' if self.opts.show_small else 'hidden'}")
        elif key == "m":
            opts.scale_mode = SCALE_ORDER[(SCALE_ORDER.index(opts.scale_mode) + 1) % len(SCALE_ORDER)]
        elif key == "o":
            opts.show_orbits = not opts.show_orbits
        elif key == "d":
            opts.show_drops = not opts.show_drops
        elif key == "n":
            opts.show_labels = not opts.show_labels
        elif key == "a":
            self.spin = not self.spin
        elif key == "i":
            cam.reset_iso()
            cam.zoom = 1.0
            if self.system:
                self.sys_follow = False
            else:
                self.follow = False
        elif key == "p":
            self.show_panel = not self.show_panel
        elif key == "r":
            self.request_data(force=True)
            if self.system:
                self.feed(self.system).request(self.sim_jd, force=True)
            self.message = "Refreshing from JPL Horizons…"

    def handle_mouse(self, ev: T.Mouse) -> None:
        cam = self.active_cam
        if ev.kind == "scroll":
            if not self.menu:
                cam.zoom = max(0.2, min(400.0, cam.zoom * (1.15 if ev.button > 0 else 1 / 1.15)))
        elif ev.kind == "press" and ev.button == 0:
            self.drag, self.dragged = (ev.col, ev.row), False
        elif ev.kind == "drag" and ev.button == 0 and self.drag and not self.menu:
            dx, dy = ev.col - self.drag[0], ev.row - self.drag[1]
            # Terminals can report motion without the pointer leaving its cell; only a real move
            # is a drag, so a click (or either half of a double click) isn't swallowed by one.
            if dx or dy:
                cam.rotate(dyaw=-math.radians(2) * dx, dpitch=math.radians(4) * dy)  # cells are ~2× taller than wide
                self.drag, self.dragged = (ev.col, ev.row), True
        elif ev.kind == "release" and ev.button == 0:
            was_drag, self.drag = self.dragged, None
            if not was_drag:
                self.click(ev.col, ev.row)
        elif ev.kind == "press" and ev.button == 2:  # right click backs out
            if self.menu:
                self.menu = None
            elif self.system:
                self.system = None

    def click(self, col: int, row: int) -> None:
        if self.menu == "jump":
            if row in self.menu_hits:
                self.choose(self.menu_hits[row])
            else:
                self.menu = None
            return
        if self.menu:
            return
        if self.show_keys:  # a click anywhere dismisses the legend
            self.show_keys = False
            return
        if col >= self.scene_cols and row in self.panel_hits:
            key = self.panel_hits[row]
        elif col < self.scene_cols:
            key = self.pick(col, row)
        else:
            key = None
        if key is None:
            return
        now = time.monotonic()
        double = self.last_click is not None and self.last_click[0] == key and now - self.last_click[1] < DOUBLE_CLICK_S
        self.last_click = (key, now)
        self.active_opts.selected = key  # a single click selects: the panel shows its details
        if double:
            if self.system:  # in the moon view: follow what was double-clicked, moon or planet
                self.sys_follow = True
            elif key in MOONS_OF:  # a planet: open its system
                self.last_click = None
                self.enter_system(key)
            else:  # the Sun or a small body: follow it
                self.follow = True

    def pick(self, col: int, row: int) -> str | None:
        """The body whose name was clicked, or the body drawn at or nearest the click."""
        for r, c0, c1, key in self.labels:
            if row == r and c0 <= col < c1:
                return key
        best, best_d = None, 3.5
        for key, (c, r) in self.hits.items():
            d = math.hypot(c - col, 2 * (r - row))  # a row is about two columns tall
            if d < best_d:
                best, best_d = key, d
        if self.system and self.disc:
            # The planet is a disc, not a point: a click anywhere on it picks it, unless a moon
            # drawn over it is right under the pointer.
            cx, cy, rc = self.disc
            if math.hypot(col + 0.5 - cx, 2 * (row + 0.5 - cy)) <= rc + 1 and not (best != self.system and best_d <= 1.5):
                return self.system
        return best

    # ---- drawing ----------------------------------------------------------------------
    def frame(self, cols: int, rows: int) -> str:
        return "".join(self.frame_rows(cols, rows))

    def frame_rows(self, cols: int, rows: int) -> list[str]:
        """The frame as self-positioning row strings (see Canvas.render_rows)."""
        cols, rows = max(cols, 40), max(rows, 12)
        panel = self.show_panel and cols >= MIN_PANEL_COLS
        scene_cols = cols - PANEL_W if panel else cols
        self.scene_cols = scene_cols
        self.panel_hits = {}
        pos = self.positions()
        scene = Canvas(scene_cols, rows - 2)

        if self.system:
            feed = self.feed(self.system)
            moon_pos = feed.positions(self.sim_jd)
            orbits = feed.orbits(self.sim_jd)
            planet = BY_KEY[self.system]
            p = pos[planet.key]
            sc = SystemScene(planet, MOONS_OF[planet.key], moon_pos, orbits,
                             _unit((-p[0], -p[1], -p[2])), self.sys_opts, sun_dist_au=_len(p))
            sel = self.sys_opts.selected
            self.sys_cam.target = sc.disp(moon_pos[sel]) if self.sys_follow and sel in moon_pos else (0.0, 0.0, 0.0)
            hits = draw_system(scene, self.sys_cam, sc)
            cx, cy, rc = sc.disc
            self.disc = (cx, cy + 1, rc)  # in terminal cells: the scene starts on row 1
            info = (lambda n: self._system_panel(n, pos, moon_pos, orbits)) if panel else None
        else:
            target = to_display(pos[self.opts.selected], self.opts.scale_mode) if self.follow else (0.0, 0.0, 0.0)
            self.cam.target = target
            hits = draw_scene(scene, self.cam, Scene(pos, self.snapshot.orbits, self.opts))
            info = (lambda n: self._panel(n, pos)) if panel else None
        self.hits = {k: (c, r + 1) for k, (c, r) in hits.items()}  # the scene starts on terminal row 1
        self.labels = [(r + 1, c0, c1, key) for r, c0, c1, key in scene.labels]

        if self.menu:
            self._draw_menu(scene)
        out = self._header(cols).render_rows(0, 0) + scene.render_rows(1, 0)
        if info:
            out += info(rows - 2).render_rows(1, scene_cols)
        out += self._footer(cols).render_rows(rows - 1, 0)
        overlay = self._keys_rows(cols, rows) if self.show_keys else []
        self.n_overlay = len(overlay)  # these go last, painted over the rows before them
        out += overlay
        return out

    def _draw_menu(self, c: Canvas) -> None:
        """The jump menu, or its date prompt, as an opaque box in the middle of the scene."""
        if self.menu == "date":
            title = "JUMP TO DATE"
            lines = ["A UTC date, or an offset from the time shown", "", f"> {self.prompt}_", "",
                     "e.g.  2061-07-28   1989-08-25 03:56   +30d   -2y", "units: h d w m y",
                     "", "Enter to jump, Esc to cancel"]
        else:
            title = "JUMP TO"
            items = self.menu_items()
            lines = [f"{'›' if i == self.menu_idx else ' '} {i + 1 if i < 9 else ' '} {label}" for i, label in enumerate(items)]
            lines += ["", "↑↓ Enter, a number, or click   Esc close"]
        w = min(c.cols, max(len(s) for s in lines) + 6)
        h = min(c.rows, len(lines) + 4)
        x0, y0 = self._box(c, title, w, h)
        self.menu_hits = {}
        n_items = len(self.menu_items()) if self.menu == "jump" else 0
        for i, s in enumerate(lines[: h - 4]):
            row = y0 + 2 + i
            sel = self.menu == "jump" and i == self.menu_idx
            dim = self.menu == "jump" and i >= n_items
            c.text(x0 + 3, row, s[: w - 5], (255, 255, 255) if sel else DIM if dim else FG, bold=sel, reverse=sel)
            if i < n_items:
                self.menu_hits[row + 1] = i  # +1: the scene starts on terminal row 1

    def _box(self, c: Canvas, title: str, w: int, h: int, centre: bool = True) -> tuple[int, int]:
        """An opaque bordered box, centred on the canvas or at its corner; returns its top-left cell."""
        x0, y0 = (max(0, (c.cols - w) // 2), max(0, (c.rows - h) // 2)) if centre else (0, 0)
        edge = (90, 96, 116)
        horiz, vert, corners = ("-", "|", "++++") if self.opts.ascii else ("─", "│", "╭╮╰╯")
        for r in range(h):  # cells hide the dots beneath them, so this blanks the box
            if r in (0, h - 1):
                left, right = (corners[0], corners[1]) if r == 0 else (corners[2], corners[3])
                c.text(x0, y0 + r, left + horiz * (w - 2) + right, edge)
            else:
                c.text(x0, y0 + r, vert + " " * (w - 2) + vert, edge)
        c.text(x0 + 2, y0, f" {title} ", ACCENT, bold=True)
        return x0, y0

    def _keys_rows(self, cols: int, rows: int) -> list[str]:
        """The key legend as its own small canvas, centred over the whole screen (panel included),
        so it isn't squeezed into the scene. Rendered last, it covers only the box itself."""
        blocks = [[(title, None)] + list(entries) for title, entries in KEY_LEGEND]
        room = max(4, rows - 2 - 4)  # rows for entries inside the box: less the borders and padding

        def layout(gap: int):
            columns: list[list] = [[]]
            for block in blocks:
                if columns[-1] and len(columns[-1]) + gap + len(block) > room:
                    columns.append([])
                if columns[-1] and gap:
                    columns[-1].append(("", None))  # a blank line between sections
                columns[-1].extend(block)
            # Each column sizes its key field to its own longest key, which keeps the box narrow.
            key_ws = [max(len(k) for k, d in col if d) + 2 for col in columns]
            col_w = [kw + max(len(d) for _, d in col if d) + 3 for kw, col in zip(key_ws, columns)]
            return columns, key_ws, col_w

        # Airy if it fits; on a small terminal, drop the gaps between sections to save a column.
        options = [layout(1), layout(0)]
        columns, key_ws, col_w = next((o for o in options if sum(o[2]) + 3 <= cols),
                                      min(options, key=lambda o: sum(o[2])))
        hint = "? or Esc to close"
        w = min(cols, sum(col_w) + 3)
        h = min(rows - 2, max(len(col) for col in columns) + 4)
        c = Canvas(w, h)
        x0, y0 = self._box(c, "KEYS", w, h, centre=False)
        x = x0 + 3
        for col, cw, key_w in zip(columns, col_w, key_ws):
            for i, (k, d) in enumerate(col):
                row = y0 + 2 + i
                if row >= y0 + h - 1 or x >= x0 + w - 2:
                    break
                limit = x0 + w - 2 - x  # clip to the box when the terminal is too narrow
                if d is None:
                    c.text(x, row, k[:limit], ACCENT, bold=True)
                else:
                    c.text(x, row, k[:limit], FG, bold=True)
                    c.text(x + key_w, row, d[: max(0, limit - key_w)], DIM)
            x += cw
        c.text(x0 + w - len(hint) - 4, y0 + h - 1, f" {hint} ", DIM)  # set into the bottom border
        # Centre the box on the area between header and footer (terminal rows 1 .. rows-2).
        return c.render_rows(1 + max(0, (rows - 2 - h) // 2), max(0, (cols - w) // 2))

    def _header(self, cols: int) -> Canvas:
        c = Canvas(cols, 1)
        c.fill_row(0)
        when = jd_to_datetime(self.sim_jd).strftime("%Y-%m-%d %H:%M:%S UTC")
        rate = RATES[self.rate_idx]
        rate_s = "paused" if self.paused else ("-" if rate < 0 else "") + RATE_NAMES[abs(rate)]
        snap, service = self.snapshot, self.service
        if self.system and MOONS_OF[self.system]:
            feed = self.feed(self.system)
            snap, service = feed.snapshot, feed.service
        status = f"● {snap.label}"
        if service.busy:
            done, total = service.progress
            status += f"  ↻ fetching {done}/{total}" if total else "  ↻ fetching…"
        cam, opts = self.active_cam, self.active_opts
        # Round to int before formatting: a float like -1e-15 would otherwise print as "-0".
        yaw, pitch = round(math.degrees(cam.yaw)) % 360, round(math.degrees(cam.pitch))
        view = f"yaw {yaw:3d}° pitch {pitch:3d}° zoom {cam.zoom:.2f}× {opts.scale_mode}"
        c.text(1, 0, "ORRERY", ACCENT, bold=True)
        x = 8
        if self.system:
            crumb = f"› {BY_KEY[self.system].name.upper()} SYSTEM"
            c.text(x, 0, crumb, BY_KEY[self.system].color, bold=True)
            x += len(crumb) + 2
        c.text(x, 0, when, FG, bold=True)
        c.text(x + len(when) + 2, 0, f"[{rate_s}]", ACCENT if not self.paused else DIM)
        x += len(when) + len(rate_s) + 6
        c.text(x, 0, status, STATUS_COLORS[snap.source])
        if cols - len(view) - 1 > x + len(status) + 2:
            c.text(cols - len(view) - 1, 0, view, DIM)
        return c

    def _footer(self, cols: int) -> Canvas:
        c = Canvas(cols, 1)
        c.fill_row(0)
        if self.flash and time.monotonic() < self.flash_until:
            c.text(1, 0, self.flash[: cols - 2], ACCENT)
        elif self.message:
            c.text(1, 0, self.message[: cols - 2], STATUS_COLORS["offline"])
        elif not self.show_keys:
            hint = "? keys"
            c.text(cols - len(hint) - 1, 0, hint, DIM)
        return c

    def _panel_frame(self, rows: int, name: str, color, follow: bool) -> Canvas:
        c = Canvas(PANEL_W, rows)
        for r in range(rows):
            c.fill_row(r)
            c.text(0, r, "│", (60, 64, 78))
        c.text(2, 1, ("o " if self.opts.ascii else "● ") + name.upper(), color, bold=True)
        if follow:
            c.text(PANEL_W - 11, 1, "[follow]", ACCENT)
        return c

    @staticmethod
    def _panel_lines(c: Canvas, lines: list[tuple[str, str]], rows: int) -> int:
        """Key/value rows from row 3 down; returns the first free row after a blank line."""
        for i, (k, v) in enumerate(lines):
            if 3 + i >= rows:
                break
            c.text(2, 3 + i, k, DIM)
            c.text(PANEL_W - 1 - len(v), 3 + i, v, FG)
        return 4 + len(lines)

    def _list_rows(self, c: Canvas, y: int, rows: int, title: str, entries, selected: str) -> int:
        """A clickable list of (key, colour, name, value), scrolled to keep the selection in view
        when it doesn't all fit. Returns the next free row."""
        room = rows - y - 3
        if room < 2 or not entries:
            return y
        keys = [e[0] for e in entries]
        first = 0
        if len(entries) > room and selected in keys:
            first = max(0, min(keys.index(selected) - room // 2, len(entries) - room))
        shown = entries[first: first + room]
        more = f"  {first + 1}-{first + len(shown)}/{len(entries)}" if len(shown) < len(entries) else ""
        c.text(2, y, title + more, DIM)
        for j, (key, color, name, value) in enumerate(shown):
            row = y + 1 + j
            is_sel = key == selected
            c.text(2, row, "›" if is_sel else " ", ACCENT)
            c.text(4, row, "o" if self.opts.ascii else "●", color)
            c.text(6, row, name, FG if is_sel else DIM, bold=is_sel)
            c.text(PANEL_W - 1 - len(value), row, value, FG if is_sel else DIM)
            self.panel_hits[row + 1] = key  # +1: the panel starts on terminal row 1
        return y + 2 + len(shown)

    def _events_rows(self, c: Canvas, y: int, rows: int, moon_key: str | None) -> int:
        """The next few events: a countdown (or the date, when days away) and what happens."""
        if rows - y < 6:
            return y
        planet = BY_KEY[self.system]
        key = self.events_key()
        evs = [e for e in self.events.upcoming(self.sim_jd, key) if moon_key is None or e.body == moon_key]
        searching = self.events.key != key
        horizon = EARTH_HORIZON_DAYS if planet.key == "earth" else SYSTEM_HORIZON_DAYS
        c.text(2, y, "Upcoming" + (" (searching…)" if searching else ""), DIM)
        if searching:
            return y + 2
        if not evs:
            c.text(4, y + 1, f"nothing in the next {horizon:.0f} days", DIM)
            return y + 3
        shown = evs[: min(5, rows - y - 5)]
        for j, ev in enumerate(shown):
            begins = ev.begins()
            if begins <= self.sim_jd:
                when = "now"
            elif begins - self.sim_jd < 2:
                when = _countdown(begins - self.sim_jd)
            else:
                when = f"{jd_to_datetime(begins):%b %d}"
            what = ev.title(planet.name) if planet.key == "earth" else _short_event(ev)
            c.text(2, y + 1 + j, f"{when:>6}", ACCENT if when == "now" else FG)
            c.text(9, y + 1 + j, what[: PANEL_W - 10], FG if when == "now" else DIM)
        return y + 2 + len(shown)

    def _system_panel(self, rows: int, pos: dict[str, Vec3], moon_pos: dict[str, Vec3], orbits) -> Canvas:
        planet = BY_KEY[self.system]
        moons = MOONS_OF[planet.key]
        feed = self.feed(planet.key)
        sel = self.sys_opts.selected
        moon = MOON_BY_KEY.get(sel) if sel in moon_pos else None
        sun_dir = tuple(-x for x in pos[planet.key])
        lines: list[tuple[str, str]] = []
        if moon is None:
            p = pos[planet.key]
            lines += [
                ("Moons shown", str(len(moons)) if moons else "none"),
                ("Radius", f"{planet.radius_km:,.0f} km"),
                ("From Sun", f"{_len(p):.4f} au"),
            ]
            if planet.key != "earth":
                d_e = _len(_sub(p, pos["earth"]))
                lines += [("From Earth", f"{d_e:.4f} au"), ("Light time", f"{d_e * AU_KM / C_KM_S / 60:.1f} min")]
        else:
            orbit = orbits[moon.key]
            r, v = orbit.state(self.sim_jd)
            r_km = _len(r) * AU_KM
            normal = (r[1] * v[2] - r[2] * v[1], r[2] * v[0] - r[0] * v[2], r[0] * v[1] - r[1] * v[0])
            incl = _angle(normal, spin_axis(planet))
            dark = in_shadow(moon_pos[moon.key], _unit(sun_dir), planet.radius_km, _len(sun_dir))
            lines += [
                (f"From {planet.name}", f"{r_km:,.0f} km"),
                ("", f"{r_km / planet.radius_km:.1f} planet radii"),
                ("Speed", f"{_len(v) * AU_KM / DAY_S:.3f} km/s"),
                ("Sunlight", "in shadow" if dark else "sunlit"),
                ("", ""),
                ("Semi-major", f"{orbit.a * AU_KM:,.0f} km"),
                ("Eccentric.", f"{orbit.e:.4f}"),
                ("Incl. (eq.)", f"{incl:.2f}°" + (" retro" if incl > 90 else "")),
                ("Period", _period(orbit.period_days)),
                ("Radius", f"{moon.radius_km:,.1f} km"),
            ]
        c = self._panel_frame(rows, moon.name if moon else f"{planet.name} system", (moon or planet).color,
                              self.sys_follow)
        y = self._panel_lines(c, lines, rows)
        if moons:
            y = self._events_rows(c, y, rows, moon.key if moon else None)
        entries = [(m.key, m.color, m.name, f"{_len(moon_pos[m.key]) * AU_KM / 1e3:,.0f}k km") for m in moons]
        self._list_rows(c, y, rows, "Moons (Tab, [ ] planet)", entries, sel)
        src = feed.snapshot
        if src.source == "analytic":
            foot = "Moon: ELP-2000 lunar theory"
        elif src.source == "offline":
            foot = "moons: mean elements (offline)"
        else:
            foot = f"moons fetched {src.fetched_at.astimezone():%H:%M}"
        if rows > 2 and moons:
            c.text(2, rows - 1, foot[: PANEL_W - 3], shade(STATUS_COLORS[src.source], 0.8))
        return c

    def _panel(self, rows: int, pos: dict[str, Vec3]) -> Canvas:
        body = BY_KEY[self.opts.selected]
        c = self._panel_frame(rows, body.name, body.color, self.follow)
        lines: list[tuple[str, str]] = []
        earth = pos["earth"]
        p = pos[body.key]
        if body is SUN:
            d_e = _len(earth)
            lines += [
                ("Type", "G2V star"),
                ("Radius", f"{body.radius_km:,.0f} km"),
                ("From Earth", f"{d_e:.4f} au"),
                ("", f"{d_e * AU_KM / 1e6:,.2f} M km"),
                ("Light time", f"{d_e * AU_KM / C_KM_S / 60:.2f} min"),
            ]
        else:
            orbit = self.snapshot.orbits[body.key]
            _, v = orbit.state(self.sim_jd)
            r = _len(p)
            lon = math.degrees(math.atan2(p[1], p[0])) % 360
            lat = math.degrees(math.asin(p[2] / r))
            lines += [
                ("Type", body.type_name),
                ("From Sun", f"{r:.4f} au"),
                ("", f"{r * AU_KM / 1e6:,.1f} M km"),
            ]
            if body.key != "earth":
                d_e = _len(_sub(p, earth))
                lines += [
                    ("From Earth", f"{d_e:.4f} au"),
                    ("Light time", f"{d_e * AU_KM / C_KM_S / 60:.1f} min"),
                ]
            lines += [
                ("Speed", f"{_len(v) * AU_KM / DAY_S:.2f} km/s"),
                ("Helio lon", f"{lon:7.2f}°"),
                ("Helio lat", f"{lat:+7.2f}°"),
                ("", ""),
                ("Semi-major", f"{orbit.a:.4f} au"),
                ("Eccentric.", f"{orbit.e:.5f}"),
                ("Inclination", f"{math.degrees(orbit.i):.3f}°"),
                ("Period", f"{orbit.period_days / 365.25:.3f} yr"),
            ]
            if orbit.e > 0.1:
                peri = jd_to_datetime(next_perihelion(orbit, self.sim_jd))
                lines += [("Perihelion", f"{orbit.a * (1 - orbit.e):.3f} au"), ("Next peri.", f"{peri:%Y-%m-%d}")]
            lines.append(("Radius", f"{body.radius_km:,.1f} km" if body.radius_km < 100 else f"{body.radius_km:,.0f} km"))
            if MOONS_OF.get(body.key):
                lines.append(("Moons", f"{len(MOONS_OF[body.key])}  (Enter to view)"))
        y = self._panel_lines(c, lines, rows)
        entries = [(b.key, b.color, b.name, f"{_len(pos[b.key]):6.2f} au") for b in ALL_BODIES
                   if b is not SUN and (self.opts.show_small or not b.small or b.key == self.opts.selected)]
        self._list_rows(c, y, rows, "Bodies (Tab, click)", entries, self.opts.selected)
        src = self.snapshot
        foot = f"data fetched {src.fetched_at.astimezone():%H:%M}" if src.source != "offline" else "analytic elements (no network)"
        if rows > 2:
            c.text(2, rows - 1, foot[: PANEL_W - 3], shade(STATUS_COLORS[src.source], 0.8))
        return c

    # ---- main loop --------------------------------------------------------------------
    def run(self) -> None:
        self.request_data(force=True)
        try:
            with T.Terminal(mouse=not self.args.no_mouse) as term:
                last = time.monotonic()
                shown, shown_size = [], None
                while self.running:
                    now = time.monotonic()
                    dt, last = now - last, now
                    for key in term.read_keys():
                        self.handle(key)
                    if not self.paused:
                        self.sim_jd += RATES[self.rate_idx] * dt / DAY_S
                    if self.spin:
                        self.active_cam.rotate(dyaw=dt * math.radians(12))
                    self.poll_data()
                    size = term.size()
                    rows = self.frame_rows(*size)
                    if size != shown_size or len(rows) != len(shown):
                        # resized, or the legend opened or closed: clear and redraw everything
                        term.write("\x1b[2J" + "".join(rows))
                    else:  # only the rows that changed; most of a still scene doesn't
                        base = len(rows) - self.n_overlay
                        changed = "".join(r for r, old in zip(rows[:base], shown[:base]) if r != old)
                        # A changed row beneath the legend erases part of it, so repaint the legend too.
                        overlay = "".join(rows[base:]) if changed or rows[base:] != shown[base:] else ""
                        term.write(changed + overlay)
                    shown, shown_size = rows, size
                    time.sleep(max(0.0, 1 / 30 - (time.monotonic() - now)))
        finally:
            if not self.args.fresh:
                settings.save(self.settings())


def _parse_date(s: str) -> float:
    return datetime_to_jd(datetime.fromisoformat(s))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="orrery", description="Live isometric solar system in your terminal (JPL Horizons data).")
    ap.add_argument("--offline", action="store_true", help="never contact JPL Horizons; use cached or analytic positions")
    ap.add_argument("--scale", choices=SCALE_ORDER, default=None, help="radial distance scaling (default: log, or as last used)")
    ap.add_argument("--date", type=_parse_date, help="start at this UTC date/time (ISO 8601) instead of now")
    ap.add_argument("--ascii", action="store_true", help="avoid non-ASCII glyphs for bodies")
    ap.add_argument("--once", action="store_true", help="print a single frame to stdout and exit")
    ap.add_argument("--size", default=None, help="frame size for --once, e.g. 140x40")
    ap.add_argument("--system", choices=[p.key for p in PLANETS], help="start in this planet's moon system view")
    ap.add_argument("--fresh", action="store_true", help="ignore (and don't save) remembered view settings")
    ap.add_argument("--no-mouse", action="store_true", help="don't capture the mouse (keeps terminal text selection)")
    args = ap.parse_args(argv)

    app = App(args)
    if args.once:
        app.snapshot = best_snapshot(app.sim_jd, allow_network=not args.offline)
        if args.system and args.system != "earth":  # Earth's Moon is analytic: nothing to fetch
            app.feed(args.system).snapshot = best_moon_snapshot(args.system, app.sim_jd, not args.offline)
        if args.system:
            app.poll_events(wait=True)  # a single frame can't wait for the background search
        cols, rows = map(int, args.size.lower().split("x")) if args.size else T.Terminal.size()
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write("\x1b[2J" + app.frame(cols, rows) + f"\x1b[{rows};1H\x1b[0m\n")
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        sys.exit("orrery needs an interactive terminal (use --once to print a single frame)")
    try:
        app.run()
    except KeyboardInterrupt:
        pass
