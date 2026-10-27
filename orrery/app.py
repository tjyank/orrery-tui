"""Interactive orrery: event loop, HUD and info panel."""
from __future__ import annotations

import argparse
import math
import sys
import time
from datetime import datetime, timezone

from . import terminal as T
from .bodies import ALL_BODIES, BY_KEY, SUN
from .ephemeris import EphemerisService, Snapshot, best_snapshot, jd_now, jd_to_datetime, load_cache, offline_snapshot
from .kepler import AU_KM, DAY_S, Vec3
from .render import SCALE_ORDER, Camera, Canvas, Scene, SceneOptions, draw_scene, shade, to_display

C_KM_S = 299_792.458
PANEL_W = 34
MIN_PANEL_COLS = 100
REANCHOR_DRIFT_DAYS = 20  # re-fetch Horizons once simulated time drifts this far from the data epoch
REFRESH_EVERY_S = 15 * 60  # refresh live data this often while tracking the present
MIN_FETCH_GAP_S = 30  # never hit JPL more often than this (except on manual refresh)

# Simulated seconds per real second.
RATES = [-31_557_600, -2_592_000, -604_800, -86_400, -3_600, -60, -1, 1, 60, 3_600, 86_400, 604_800, 2_592_000, 31_557_600]
REALTIME = RATES.index(1)
RATE_NAMES = {1: "real time", 60: "1 min/s", 3_600: "1 hr/s", 86_400: "1 day/s", 604_800: "1 wk/s",
              2_592_000: "30 days/s", 31_557_600: "1 yr/s"}

FG = (210, 214, 224)
DIM = (120, 126, 142)
ACCENT = (255, 200, 90)
STATUS_COLORS = {"live": (110, 220, 130), "cache": (240, 200, 90), "offline": (240, 120, 100)}

HELP = "←→ rotate  ↑↓ tilt  +- zoom  Tab select  f follow  ,. speed  space pause  t now  m scale  g grid  o orbits  d drops  n names  a spin  i iso  p panel  r refresh  q quit"


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _len(a: Vec3) -> float:
    return math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)


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
        self.running = True

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

    def positions(self) -> dict[str, Vec3]:
        pos = {"sun": (0.0, 0.0, 0.0)}
        pos.update({k: o.position(self.sim_jd) for k, o in self.snapshot.orbits.items()})
        return pos

    # ---- input ------------------------------------------------------------------------
    def handle(self, key: str) -> None:
        step = math.radians(5)
        keys = [b.key for b in ALL_BODIES]
        if key in ("q", "Q", T.ESC):
            self.running = False
        elif key in (T.LEFT, "h"):
            self.cam.rotate(dyaw=-step)
        elif key in (T.RIGHT, "l"):
            self.cam.rotate(dyaw=step)
        elif key in (T.UP, "k"):
            self.cam.rotate(dpitch=step)
        elif key in (T.DOWN, "j"):
            self.cam.rotate(dpitch=-step)
        elif key in ("+", "="):
            self.cam.zoom = min(self.cam.zoom * 1.25, 400.0)
        elif key in ("-", "_"):
            self.cam.zoom = max(self.cam.zoom / 1.25, 0.2)
        elif key in (T.TAB, T.BTAB):
            i = keys.index(self.opts.selected) if self.opts.selected in keys else 0
            self.opts.selected = keys[(i + (1 if key == T.TAB else -1)) % len(keys)]
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
            self.opts.scale_mode = SCALE_ORDER[(SCALE_ORDER.index(self.opts.scale_mode) + 1) % len(SCALE_ORDER)]
        elif key == "g":
            self.opts.show_grid = not self.opts.show_grid
        elif key == "o":
            self.opts.show_orbits = not self.opts.show_orbits
        elif key == "d":
            self.opts.show_drops = not self.opts.show_drops
        elif key == "n":
            self.opts.show_labels = not self.opts.show_labels
        elif key == "a":
            self.spin = not self.spin
        elif key == "i":
            self.cam.reset_iso()
            self.cam.zoom, self.follow = 1.0, False
        elif key == "p":
            self.show_panel = not self.show_panel
        elif key == "r":
            self.request_data(force=True)
            self.message = "Refreshing from JPL Horizons…"

    # ---- drawing ----------------------------------------------------------------------
    def frame(self, cols: int, rows: int) -> str:
        cols, rows = max(cols, 40), max(rows, 12)
        panel = self.show_panel and cols >= MIN_PANEL_COLS
        scene_cols = cols - PANEL_W if panel else cols
        pos = self.positions()

        target = to_display(pos[self.opts.selected], self.opts.scale_mode) if self.follow else (0.0, 0.0, 0.0)
        self.cam.target = target

        scene = Canvas(scene_cols, rows - 2)
        draw_scene(scene, self.cam, Scene(pos, self.snapshot.orbits, self.opts))
        out = [self._header(cols).render(0, 0), scene.render(1, 0)]
        if panel:
            out.append(self._panel(rows - 2, pos).render(1, scene_cols))
        out.append(self._footer(cols).render(rows - 1, 0))
        return "".join(out)

    def _header(self, cols: int) -> Canvas:
        c = Canvas(cols, 1)
        c.fill_row(0)
        when = jd_to_datetime(self.sim_jd).strftime("%Y-%m-%d %H:%M:%S UTC")
        rate = RATES[self.rate_idx]
        rate_s = "paused" if self.paused else ("-" if rate < 0 else "") + RATE_NAMES[abs(rate)]
        snap = self.snapshot
        status = f"● {snap.label}"
        if snap.source != "offline":
            status += f" (epoch {jd_to_datetime(snap.epoch):%Y-%m-%d %H:%M})"
        if self.service.busy:
            done, total = self.service.progress
            status += f"  ↻ fetching {done}/{total}" if total else "  ↻ fetching…"
        view = f"yaw {math.degrees(self.cam.yaw):3.0f}° pitch {math.degrees(self.cam.pitch):3.0f}° zoom {self.cam.zoom:.2f}× {self.opts.scale_mode}"
        c.text(1, 0, "ORRERY", ACCENT, bold=True)
        c.text(8, 0, when, FG, bold=True)
        c.text(8 + len(when) + 2, 0, f"[{rate_s}]", ACCENT if not self.paused else DIM)
        x = 8 + len(when) + len(rate_s) + 6
        c.text(x, 0, status, STATUS_COLORS[snap.source])
        if cols - len(view) - 1 > x + len(status) + 2:
            c.text(cols - len(view) - 1, 0, view, DIM)
        return c

    def _footer(self, cols: int) -> Canvas:
        c = Canvas(cols, 1)
        c.fill_row(0)
        if self.message:
            c.text(1, 0, self.message[: cols - 2], STATUS_COLORS["offline"])
        else:
            c.text(1, 0, HELP[: cols - 2], DIM)
        return c

    def _panel(self, rows: int, pos: dict[str, Vec3]) -> Canvas:
        c = Canvas(PANEL_W, rows)
        for r in range(rows):
            c.fill_row(r)
            c.text(0, r, "│", (60, 64, 78))
        body = BY_KEY[self.opts.selected]
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
        c.text(2, 1, ("o " if self.opts.ascii else "● ") + body.name.upper(), body.color, bold=True)
        if self.follow:
            c.text(PANEL_W - 11, 1, "[follow]", ACCENT)
        for i, (k, v) in enumerate(lines):
            if 3 + i >= rows:
                break
            c.text(2, 3 + i, k, DIM)
            c.text(PANEL_W - 1 - len(v), 3 + i, v, FG)

        y = 4 + len(lines)
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
                    self.cam.rotate(dyaw=dt * math.radians(12))
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
    args = ap.parse_args(argv)

    app = App(args)
    if args.once:
        app.snapshot = best_snapshot(app.sim_jd, allow_network=not args.offline)
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
