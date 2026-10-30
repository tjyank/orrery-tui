"""Interactive orrery: state, data, frame assembly and the main loop."""
from __future__ import annotations

import argparse
import math
import sys
import time
from datetime import datetime

from . import settings
from . import terminal as T
from .bodies import BY_KEY, PLANETS
from .controls import Controls
from .ephemeris import (EphemerisService, Snapshot, best_moon_snapshot, best_snapshot, jd_now, jd_to_datetime,
                        load_cache, offline_snapshot)
from .events import light_time
from .feeds import EventFeed, MIN_FETCH_GAP_S, MoonFeed, REANCHOR_DRIFT_DAYS, REFRESH_EVERY_S
from .jumps import datetime_to_jd
from .kepler import DAY_S, Vec3
from .moons import MOONS_OF
from .overlays import Overlays
from .panels import Panels
from .render import Camera, Canvas, SCALE_ORDER, Scene, SceneOptions, draw_scene, to_display
from .system import SystemScene, draw_system
from .ui import EVENT_ALERT_MAX_RATE, FLASH_S, JUMP_RESET_DAYS, MIN_PANEL_COLS, PANEL_W, RATES, REALTIME, vec_len, unit_vector


class App(Controls, Overlays, Panels):
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
            "orbits": o.show_orbits, "solo": o.solo_orbit, "drops": o.show_drops, "names": o.show_labels, "small": o.show_small,
            "selected": o.selected, "panel": self.show_panel, "spin": self.spin, "follow": self.follow,
            "sys_yaw": s.yaw, "sys_pitch": s.pitch, "sys_zoom": s.zoom, "sys_scale": so.scale_mode,
            "sys_orbits": so.show_orbits, "sys_solo": so.solo_orbit, "sys_drops": so.show_drops,
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
        for key, obj, attr in (("orbits", o, "show_orbits"), ("solo", o, "solo_orbit"), ("drops", o, "show_drops"),
                               ("names", o, "show_labels"), ("small", o, "show_small"),
                               ("sys_orbits", so, "show_orbits"), ("sys_solo", so, "solo_orbit"),
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
            # As Earth sees them: light left the moons one light time ago (minutes to hours), the
            # same timing the event list uses, so a countdown hits zero as the view shows it.
            delay = 0.0 if self.system == "earth" else light_time(pos[self.system], pos["earth"])
            moon_pos = feed.positions(self.sim_jd - delay)
            orbits = feed.orbits(self.sim_jd)
            planet = BY_KEY[self.system]
            p = pos[planet.key]
            sc = SystemScene(planet, MOONS_OF[planet.key], moon_pos, orbits,
                             unit_vector((-p[0], -p[1], -p[2])), self.sys_opts, sun_dist_au=vec_len(p))
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

    # ---- main loop --------------------------------------------------------------------
    def _view_state(self, size: tuple[int, int]) -> tuple:
        """Everything a frame depends on: while this is unchanged, so is the frame.

        Positions follow from the simulated time, so that stands in for them at the resolution
        the header clock shows (a second). Anything new on screen must be reflected here.
        """
        feed = self.feeds.get(self.system) if self.system else None
        return (
            size, int(self.sim_jd * DAY_S), self.paused, self.rate_idx,
            self.system, self.follow, self.sys_follow, self.show_panel, self.show_keys,
            (self.cam.yaw, self.cam.pitch, self.cam.zoom), (self.sys_cam.yaw, self.sys_cam.pitch, self.sys_cam.zoom),
            tuple(vars(self.opts).values()), tuple(vars(self.sys_opts).values()),
            self.menu, self.menu_idx, self.prompt, self.message,
            self.flash if time.monotonic() < self.flash_until else None,
            id(self.snapshot), self.service.busy, self.service.progress,
            feed and (id(feed.snapshot), feed.service.busy, feed.service.progress),
            self.events.key, len(self.events.events), self.events.busy,
        )

    def run(self) -> None:
        self.request_data(force=True)
        try:
            with T.Terminal(mouse=not self.args.no_mouse) as term:
                last = time.monotonic()
                shown, shown_size, shown_state = [], None, None
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
                    # Drawing a frame costs tens of milliseconds; input is checked every tick, but a
                    # frame is drawn only when something it shows has changed. At real time that's
                    # about once a second (the clock), so an idle orrery barely uses the CPU.
                    state = self._view_state(size)
                    if state == shown_state:
                        time.sleep(max(0.0, 1 / 30 - (time.monotonic() - now)))
                        continue
                    shown_state = state
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
