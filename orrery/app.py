"""Interactive orrery: event loop, HUD and info panel."""
from __future__ import annotations

import argparse
import math
import sys
import time
from datetime import datetime, timezone

from functools import partial

from . import terminal as T
from .bodies import ALL_BODIES, BY_KEY, PLANETS, SUN
from .ephemeris import (EphemerisService, Snapshot, best_moon_snapshot, best_snapshot, jd_now, jd_to_datetime,
                        load_cache, load_moon_cache, offline_moon_snapshot, offline_snapshot)
from .kepler import AU_KM, DAY_S, Vec3
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

# Simulated seconds per real second.
RATES = [-31_557_600, -2_592_000, -604_800, -86_400, -3_600, -60, -1, 1, 60, 3_600, 86_400, 604_800, 2_592_000, 31_557_600]
REALTIME = RATES.index(1)
RATE_NAMES = {1: "real time", 60: "1 min/s", 3_600: "1 hr/s", 86_400: "1 day/s", 604_800: "1 wk/s",
              2_592_000: "30 days/s", 31_557_600: "1 yr/s"}

FG = (210, 214, 224)
DIM = (120, 126, 142)
ACCENT = (255, 200, 90)
STATUS_COLORS = {"live": (110, 220, 130), "cache": (240, 200, 90), "offline": (240, 120, 100)}

HELP = "←→ rotate  ↑↓ tilt  +- zoom  Tab select  Enter moons  f follow  ,. speed  space pause  t now  m scale  g grid  o orbits  d drops  n names  a spin  i iso  p panel  r refresh  q quit"
SYSTEM_HELP = "Tab moon  [ ] planet  Esc back  ←→ rotate  ↑↓ tilt  +- zoom  f follow  ,. speed  space pause  t now  m scale  g grid  o orbits  d drops  n names  a spin  i iso  p panel  r refresh  q quit"


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


class MoonFeed:
    """Moon orbits for one planet: Horizons in the background, cache or mean elements meanwhile."""

    def __init__(self, planet: str, jd: float, allow_network: bool):
        self.planet = planet
        self.service = EphemerisService(allow_network, loader=partial(best_moon_snapshot, planet))
        self.snapshot: Snapshot = load_moon_cache(planet, jd) or offline_moon_snapshot(planet, jd)
        self.last_request = -1e9
        self.last_live = -1e9

    def request(self, jd: float, force: bool = False) -> None:
        now = time.monotonic()
        if MOONS_OF[self.planet] and (force or now - self.last_request >= MIN_FETCH_GAP_S):
            if self.service.request(jd):
                self.last_request = now

    def poll(self, jd: float) -> Snapshot | None:
        """Take a newly arrived snapshot (returned once) and re-fetch when the data goes stale."""
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

    def positions(self, jd: float) -> dict[str, Vec3]:
        return {k: o.position(jd) for k, o in self.snapshot.orbits.items()}


class App:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.cam = Camera()
        self.opts = SceneOptions(scale_mode=args.scale, ascii=args.ascii, selected="earth")
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
        if args.system:
            self.enter_system(args.system)

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

    def positions(self) -> dict[str, Vec3]:
        pos = {"sun": (0.0, 0.0, 0.0)}
        pos.update({k: o.position(self.sim_jd) for k, o in self.snapshot.orbits.items()})
        return pos

    # ---- input ------------------------------------------------------------------------
    def handle(self, key: str) -> None:
        step = math.radians(5)
        cam, opts = self.active_cam, self.active_opts
        if self.system:
            keys = [self.system, *(m.key for m in MOONS_OF[self.system])]
        else:
            keys = [b.key for b in ALL_BODIES]
        planets = [p.key for p in PLANETS]
        if key in ("q", "Q") or (key == T.ESC and not self.system):
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
        elif key == "m":
            opts.scale_mode = SCALE_ORDER[(SCALE_ORDER.index(opts.scale_mode) + 1) % len(SCALE_ORDER)]
        elif key == "g":
            opts.show_grid = not opts.show_grid
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

    # ---- drawing ----------------------------------------------------------------------
    def frame(self, cols: int, rows: int) -> str:
        cols, rows = max(cols, 40), max(rows, 12)
        panel = self.show_panel and cols >= MIN_PANEL_COLS
        scene_cols = cols - PANEL_W if panel else cols
        pos = self.positions()
        scene = Canvas(scene_cols, rows - 2)

        if self.system:
            feed = self.feed(self.system)
            moon_pos = feed.positions(self.sim_jd)
            planet = BY_KEY[self.system]
            p = pos[planet.key]
            sc = SystemScene(planet, MOONS_OF[planet.key], moon_pos, feed.snapshot.orbits,
                             _unit((-p[0], -p[1], -p[2])), self.sys_opts)
            sel = self.sys_opts.selected
            self.sys_cam.target = sc.disp(moon_pos[sel]) if self.sys_follow and sel in moon_pos else (0.0, 0.0, 0.0)
            draw_system(scene, self.sys_cam, sc)
            info = (lambda n: self._system_panel(n, pos, moon_pos)) if panel else None
        else:
            target = to_display(pos[self.opts.selected], self.opts.scale_mode) if self.follow else (0.0, 0.0, 0.0)
            self.cam.target = target
            draw_scene(scene, self.cam, Scene(pos, self.snapshot.orbits, self.opts))
            info = (lambda n: self._panel(n, pos)) if panel else None

        out = [self._header(cols).render(0, 0), scene.render(1, 0)]
        if info:
            out.append(info(rows - 2).render(1, scene_cols))
        out.append(self._footer(cols).render(rows - 1, 0))
        return "".join(out)

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
        if snap.source != "offline":
            status += f" (epoch {jd_to_datetime(snap.epoch):%Y-%m-%d %H:%M})"
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
        else:
            c.text(1, 0, (SYSTEM_HELP if self.system else HELP)[: cols - 2], DIM)
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

    def _system_panel(self, rows: int, pos: dict[str, Vec3], moon_pos: dict[str, Vec3]) -> Canvas:
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
            orbit = feed.snapshot.orbits[moon.key]
            r, v = orbit.state(self.sim_jd)
            r_km = _len(r) * AU_KM
            normal = (r[1] * v[2] - r[2] * v[1], r[2] * v[0] - r[0] * v[2], r[0] * v[1] - r[1] * v[0])
            incl = _angle(normal, spin_axis(planet))
            lines += [
                (f"From {planet.name}", f"{r_km:,.0f} km"),
                ("", f"{r_km / planet.radius_km:.1f} planet radii"),
                ("Speed", f"{_len(v) * AU_KM / DAY_S:.3f} km/s"),
                ("Sunlight", "in shadow" if in_shadow(r, _unit(sun_dir), planet.radius_km) else "sunlit"),
                ("", ""),
                ("Semi-major", f"{orbit.a * AU_KM:,.0f} km"),
                ("Eccentric.", f"{orbit.e:.4f}"),
                ("Incl. (eq.)", f"{incl:.2f}°" + (" retro" if incl > 90 else "")),
                ("Period", _period(orbit.period_days)),
                ("Radius", f"{moon.radius_km:,.1f} km"),
            ]
        c = self._panel_frame(rows, moon.name if moon else f"{planet.name} system", (moon or planet).color,
                              self.sys_follow and moon is not None)
        y = self._panel_lines(c, lines, rows)

        if moons and y + 3 < rows:
            c.text(2, y, "Moons (Tab, [ ] planet)", DIM)
            shown = moons[: rows - y - 3]
            for j, m in enumerate(shown):
                is_sel = m.key == sel
                d = f"{_len(moon_pos[m.key]) * AU_KM / 1e3:,.0f}k km"
                c.text(2, y + 1 + j, "›" if is_sel else " ", ACCENT)
                c.text(4, y + 1 + j, "o" if self.opts.ascii else "●", m.color)
                c.text(6, y + 1 + j, m.name, FG if is_sel else DIM, bold=is_sel)
                c.text(PANEL_W - 1 - len(d), y + 1 + j, d, FG if is_sel else DIM)
        src = feed.snapshot
        foot = ("moons: mean elements (offline)" if src.source == "offline"
                else f"moons fetched {src.fetched_at.astimezone():%H:%M}")
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
                ("Radius", f"{body.radius_km:,.0f} km"),
            ]
            if MOONS_OF[body.key]:
                lines.append(("Moons", f"{len(MOONS_OF[body.key])}  (Enter to view)"))
        y = self._panel_lines(c, lines, rows)
        legend = [b for b in ALL_BODIES if b is not SUN]
        if y + len(legend) + 2 < rows:
            c.text(2, y, "Bodies (Tab to cycle)", DIM)
            for j, b in enumerate(legend):
                sel = b.key == self.opts.selected
                d = _len(pos[b.key])
                c.text(2, y + 1 + j, "›" if sel else " ", ACCENT)
                c.text(4, y + 1 + j, "o" if self.opts.ascii else "●", b.color)
                c.text(6, y + 1 + j, b.name, FG if sel else DIM, bold=sel)
                ds = f"{d:6.2f} au"
                c.text(PANEL_W - 1 - len(ds), y + 1 + j, ds, FG if sel else DIM)
        src = self.snapshot
        foot = f"data fetched {src.fetched_at.astimezone():%H:%M}" if src.source != "offline" else "analytic elements (no network)"
        if rows > 2:
            c.text(2, rows - 1, foot[: PANEL_W - 3], shade(STATUS_COLORS[src.source], 0.8))
        return c

    # ---- main loop --------------------------------------------------------------------
    def run(self) -> None:
        self.request_data(force=True)
        with T.Terminal() as term:
            last = time.monotonic()
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
                term.write(self.frame(*term.size()))
                time.sleep(max(0.0, 1 / 30 - (time.monotonic() - now)))


def _parse_date(s: str) -> float:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() / DAY_S + 2440587.5


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="orrery", description="Live isometric solar system in your terminal (JPL Horizons data).")
    ap.add_argument("--offline", action="store_true", help="never contact JPL Horizons; use cached or analytic positions")
    ap.add_argument("--scale", choices=SCALE_ORDER, default="log", help="radial distance scaling (default: log)")
    ap.add_argument("--date", type=_parse_date, help="start at this UTC date/time (ISO 8601) instead of now")
    ap.add_argument("--ascii", action="store_true", help="avoid non-ASCII glyphs for bodies")
    ap.add_argument("--once", action="store_true", help="print a single frame to stdout and exit")
    ap.add_argument("--size", default=None, help="frame size for --once, e.g. 140x40")
    ap.add_argument("--system", choices=[p.key for p in PLANETS], help="start in this planet's moon system view")
    args = ap.parse_args(argv)

    app = App(args)
    if args.once:
        app.snapshot = best_snapshot(app.sim_jd, allow_network=not args.offline)
        if args.system:
            app.feed(args.system).snapshot = best_moon_snapshot(args.system, app.sim_jd, not args.offline)
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
