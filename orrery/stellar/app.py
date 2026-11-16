"""The star map: an isometric, rotatable map of the stars around the Sun.

Opened from the orrery with S (embedded: Esc or S goes back), or on its own with `python -m orrery.stellar`.
"""
from __future__ import annotations

import argparse
import math
import sys
import textwrap
import time
from datetime import datetime, timezone

from .. import terminal as T
from ..render import Camera, Canvas, Cell

from .catalog import BY_KEY, SYSTEMS, System, kind
from .coords import (LY_PER_PC, Vec3, apparent_mag, distance, format_dec, format_ra, galactic_lb, luminosity,
                     parse_dec, parse_ra)
from .motion import (MAX_YEARS, closest_approach, format_year, position, proper_motion, radial_velocity,
                     speed_kms)
from .scene import View, draw

RANGES = [10.0, 15.0, 20.0, 30.0, 50.0, 70.0]
PANEL_W = 36
MIN_PANEL_COLS = 100
FG, DIM, ACCENT = (210, 214, 224), (120, 126, 142), (255, 200, 90)
FLASH_S = 4
DOUBLE_CLICK_S = 0.5
GLIDE_S = 0.6  # camera move when viewing from another star
RATES = [-10_000, -1_000, -100, -10, 10, 100, 1_000, 10_000]  # simulated years per real second

KEYS = [
    ("← → / h l", "rotate"), ("↑ ↓ / k j", "tilt"), ("+ -", "zoom"), ("i", "isometric view"),
    ("a", "auto-spin"), ("Tab Shift-Tab", "next / previous star"), ("/", "find a star by name"),
    ("Enter / v", "view from the selected star"), ("s / Backspace", "back to the Sun"),
    ("[ ]", "smaller / larger range"), (", .", "time slower / faster"), ("space", "play / pause time"),
    ("t", "back to the present"), ("y", "go to a year"), ("T", "motion tracks"), ("m", "mark: measure to here"), ("M", "clear the mark"),
    ("g d", "grid, drop lines"), ("n", "names: all, bright, none"), ("p", "info panel"),
    ("?", "this list"), ("q / Esc", "quit"), ("", ""),  # embedded, KEYS_EMBEDDED replaces the quit line
    ("drag", "rotate and tilt"), ("scroll", "zoom"), ("click", "select"),
    ("double-click", "view from it"), ("right-click", "back to the Sun"), ("slider", "click or drag: scrub time"),
]
KEYS_EMBEDDED = [entry for k, what in KEYS
                 for entry in ([("Esc / S", "back to the planets"), ("q", "quit")] if k == "q / Esc" else [(k, what)])]


def format_luminosity(lum: float) -> str:
    """Visual luminosity in Suns, readable at both ends of the range."""
    if lum < 0.01:
        return f"1/{1 / lum:,.0f} of the Sun"
    if abs(lum - 1) < 0.005:
        return "1 Sun"
    return f"{lum:.3g} Suns"


def present_year() -> float:
    now = datetime.now(timezone.utc)
    start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    return now.year + (now - start).total_seconds() / (365.2425 * 86400)


def parse_year(text: str, now: float) -> float | None:
    """'38000' or '38000 CE' -> that year; '10000 BCE' -> 10,000 BC; '+5000' / '-5000' -> years from now."""
    t = text.strip().upper().replace(",", "").replace("_", "")
    try:
        if t.endswith(("BCE", "BC")):
            return 1 - float(t.removesuffix("BCE").removesuffix("BC").strip())
        if t.endswith(("CE", "AD")):
            return float(t.removesuffix("CE").removesuffix("AD").strip())
        if t[:1] in "+-":
            return now + float(t)
        return float(t)
    except ValueError:
        return None


def rate_name(rate: int) -> str:
    return f"{abs(rate):,} yr/s"


def find(query: str) -> System | None:
    q = query.strip().lower()
    if not q:
        return None
    for exact in (True, False):
        for s in SYSTEMS:
            for name in s.names():
                n = name.lower()
                if (n == q) if exact else (q in n):
                    return s
    return None


class StarMap:
    def __init__(self, args: argparse.Namespace, embedded: bool = False):
        self.args = args
        self.embedded = embedded  # inside the orrery: Esc and S go back to it instead of quitting
        self.leave = False  # embedded: set when the user asks to go back
        self.cam = Camera()
        self.focus = "sun"
        self.selected = "sun"
        self.mark: str | None = None
        self.radius = args.radius
        self.show_grid = self.show_drops = True
        self.names = "all"
        self.show_panel = True
        self.show_keys = False
        self.spin = False
        self.glide: tuple[tuple, tuple, float] | None = None  # from, to, start time
        self.prompt: str | None = None  # text being typed: a / search or a y year
        self.prompt_kind = "find"
        # Time: the year shown, and how fast it runs. It starts paused at the present.
        self.now_year = present_year()
        self.year = self.now_year
        self.rate_idx = RATES.index(1_000)
        self.paused = True
        self.show_tracks = True
        self.slider: tuple[int, int, int] | None = None  # terminal row, first column, width of the bar
        self.scrubbing = False
        self._positions: tuple[float, dict[str, Vec3]] | None = None
        self.flash, self.flash_until = "", 0.0
        self.running = True
        self.dirty = True
        self.hits: dict[str, tuple[int, int]] = {}
        self.labels: list[tuple[int, int, int, str]] = []
        self.scene_cols = 0
        self.drag: tuple[int, int] | None = None
        self.dragged = False
        self.last_click: tuple[str, float] | None = None
        if getattr(args, "year", None) is not None:
            self.set_year(args.year)
        if args.view_from:
            s = find(args.view_from)
            if s is None:
                sys.exit(f"stellar: no star called {args.view_from!r}")
            self.view_from(s.key, glide=False)

    # ---- state ------------------------------------------------------------------------
    def positions(self) -> dict[str, Vec3]:
        """Every system's position in the year shown (cached: it changes only with the year)."""
        if self._positions is None or self._positions[0] != self.year:
            self._positions = (self.year, {s.key: position(s, self.year) for s in SYSTEMS})
        return self._positions[1]

    def pos(self, key: str) -> Vec3:
        return self.positions()[key]

    @property
    def origin(self) -> Vec3:
        return self.pos(self.focus)

    def visible(self) -> list[System]:
        o, pos = self.origin, self.positions()
        return [s for s in SYSTEMS if distance(pos[s.key], o) <= self.radius + 1e-9
                or s.key in (self.selected, self.mark, self.focus)]

    def by_distance(self) -> list[System]:
        o, pos = self.origin, self.positions()
        return sorted(self.visible(), key=lambda s: distance(pos[s.key], o))

    # ---- time -------------------------------------------------------------------------
    def set_year(self, year: float) -> None:
        lo, hi = self.now_year - MAX_YEARS, self.now_year + MAX_YEARS
        self.year = max(lo, min(hi, year))

    def change_rate(self, step: int) -> None:
        if self.paused:  # the first press just starts time, in the direction asked
            self.paused = False
            if (RATES[self.rate_idx] > 0) != (step > 0):
                self.rate_idx = RATES.index(1_000 if step > 0 else -1_000)
        else:
            self.rate_idx = max(0, min(len(RATES) - 1, self.rate_idx + step))
        self.say(f"Time: {'forward' if RATES[self.rate_idx] > 0 else 'backward'}, {rate_name(RATES[self.rate_idx])}")

    def toggle_pause(self) -> None:
        self.paused = not self.paused

    def to_present(self) -> None:
        self.year, self.paused = self.now_year, True
        self.say("Back to the present")

    def say(self, text: str, seconds: float = FLASH_S) -> None:
        self.flash, self.flash_until = text, time.monotonic() + seconds

    def view_from(self, key: str, glide: bool = True) -> None:
        if key == self.focus:
            return
        self.focus = self.selected = key
        if glide:
            self.glide = (self.cam.target, time.monotonic())
        else:
            self.cam.target = self.origin
        self.say(f"Viewing from {BY_KEY[key].name}")

    def select(self, key: str) -> None:
        self.selected = key
        d = distance(self.pos(key), self.origin)
        if d > self.radius:  # out of range: widen the view to include it
            self.radius = next((r for r in RANGES if r >= d), RANGES[-1])

    def cycle(self, step: int) -> None:
        order = [s.key for s in self.by_distance()]
        i = order.index(self.selected) if self.selected in order else 0
        self.selected = order[(i + step) % len(order)]

    def set_range(self, step: int) -> None:
        i = min(range(len(RANGES)), key=lambda k: abs(RANGES[k] - self.radius))
        self.radius = RANGES[max(0, min(len(RANGES) - 1, i + step))]
        self.say(f"Showing stars within {self.radius:g} ly of {BY_KEY[self.focus].name}")

    # ---- input ------------------------------------------------------------------------
    def handle(self, key) -> None:
        self.dirty = True
        if isinstance(key, T.Mouse):
            self.handle_mouse(key)
            return
        if self.prompt is not None:
            self._prompt_key(key)
            return
        step = math.radians(7.5)
        c = self.cam
        actions = {
            T.LEFT: lambda: c.rotate(dyaw=step), "h": lambda: c.rotate(dyaw=step),
            T.RIGHT: lambda: c.rotate(dyaw=-step), "l": lambda: c.rotate(dyaw=-step),
            T.UP: lambda: c.rotate(dpitch=step), "k": lambda: c.rotate(dpitch=step),
            T.DOWN: lambda: c.rotate(dpitch=-step), "j": lambda: c.rotate(dpitch=-step),
            "+": lambda: self.zoom(1.2), "=": lambda: self.zoom(1.2), "-": lambda: self.zoom(1 / 1.2),
            "i": c.reset_iso, "a": self.toggle_spin,
            T.TAB: lambda: self.cycle(1), T.BTAB: lambda: self.cycle(-1),
            T.ENTER: lambda: self.view_from(self.selected), "v": lambda: self.view_from(self.selected),
            "s": lambda: self.view_from("sun"), T.BACKSPACE: lambda: self.view_from("sun"),
            "[": lambda: self.set_range(-1), "]": lambda: self.set_range(1),
            "m": self.toggle_mark, "M": self.clear_mark,
            "g": lambda: setattr(self, "show_grid", not self.show_grid),
            "d": lambda: setattr(self, "show_drops", not self.show_drops),
            "n": self.cycle_names,
            "p": lambda: setattr(self, "show_panel", not self.show_panel),
            "?": lambda: setattr(self, "show_keys", not self.show_keys),
            "/": lambda: self.open_prompt("find"), "y": lambda: self.open_prompt("year"),
            ",": lambda: self.change_rate(-1), ".": lambda: self.change_rate(1), " ": self.toggle_pause,
            "t": self.to_present, "T": lambda: setattr(self, "show_tracks", not self.show_tracks),
            "q": self.quit, T.ESC: self.back,
        }
        if self.embedded:
            actions["S"] = self.back
        if key in actions:
            actions[key]()

    def open_prompt(self, kind: str) -> None:
        self.prompt, self.prompt_kind = "", kind

    def _prompt_key(self, key) -> None:
        if key == T.ESC:
            self.prompt = None
        elif key == T.ENTER:
            text, self.prompt = self.prompt or "", None
            if self.prompt_kind == "year":
                year = parse_year(text, self.now_year)
                if year is None:
                    self.say("Type a year like 38000, 10000 BCE, or +5000 for years from now")
                else:
                    self.set_year(year)
                    self.paused = True
                    self.say(f"Year {format_year(self.year)}")
                return
            s = find(text)
            if s is None:
                self.say("No star by that name")
            else:
                self.select(s.key)
        elif key == T.BACKSPACE:
            self.prompt = (self.prompt or "")[:-1]
        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            self.prompt = (self.prompt or "") + key

    def quit(self) -> None:
        if self.show_keys:
            self.show_keys = False
        else:
            self.running = False

    def back(self) -> None:
        """Esc: close the key list first; then back to the orrery (embedded) or quit (on its own)."""
        if self.show_keys:
            self.show_keys = False
        elif self.embedded:
            self.leave = True
        else:
            self.running = False

    def zoom(self, f: float) -> None:
        self.cam.zoom = max(0.3, min(30.0, self.cam.zoom * f))

    def toggle_spin(self) -> None:
        self.spin = not self.spin

    def toggle_mark(self) -> None:
        if self.mark == self.selected:
            self.mark = None
        else:
            self.mark = self.selected
            self.say(f"Marked {BY_KEY[self.mark].name}: select another star to measure the distance")

    def clear_mark(self) -> None:
        self.mark = None

    def cycle_names(self) -> None:
        self.names = {"all": "bright", "bright": "none", "none": "all"}[self.names]
        self.say(f"Names: {self.names}")

    def pick(self, col: int, row: int) -> str | None:
        for r, c0, c1, key in self.labels:
            if r == row and c0 <= col < c1:
                return key
        best, best_d = None, 2.5
        for key, (c, r) in self.hits.items():
            d = math.hypot((c - col) / 2, r - row)  # cells are about twice as tall as wide
            if d <= best_d:
                best, best_d = key, d
        return best

    def on_slider(self, col: int, row: int) -> bool:
        return self.slider is not None and row == self.slider[0] and self.slider[1] <= col < self.slider[1] + self.slider[2]

    def scrub(self, col: int) -> None:
        _, c0, w = self.slider
        frac = max(0.0, min(1.0, (col - c0) / max(1, w - 1)))
        self.set_year(self.now_year - MAX_YEARS + frac * 2 * MAX_YEARS)
        self.paused = True

    def handle_mouse(self, ev: T.Mouse) -> None:
        if ev.kind == "press" and ev.button == 0 and self.on_slider(ev.col, ev.row):
            self.scrubbing = True
            self.scrub(ev.col)
            return
        if self.scrubbing:
            if ev.kind == "drag":
                self.scrub(ev.col)
            elif ev.kind == "release":
                self.scrubbing = False
            return
        if ev.kind == "scroll":
            self.zoom(1.15 if ev.button > 0 else 1 / 1.15)
        elif ev.kind == "press" and ev.button == 0:
            self.drag, self.dragged = (ev.col, ev.row), False
        elif ev.kind == "drag" and self.drag:
            dc, dr = ev.col - self.drag[0], ev.row - self.drag[1]
            if dc or dr:
                self.cam.rotate(dyaw=-dc * math.radians(2.0), dpitch=dr * math.radians(4.0))
                self.drag, self.dragged = (ev.col, ev.row), True
        elif ev.kind == "release" and ev.button == 0:
            if self.drag and not self.dragged and ev.col < self.scene_cols:
                key = self.pick(ev.col, ev.row - 1)  # the scene starts on terminal row 1
                if key:
                    now = time.monotonic()
                    if self.last_click and self.last_click[0] == key and now - self.last_click[1] < DOUBLE_CLICK_S:
                        self.view_from(key)
                        self.last_click = None
                    else:
                        self.selected = key
                        self.last_click = (key, now)
            self.drag = None
        elif ev.kind == "press" and ev.button == 2:
            self.view_from("sun")

    # ---- per-tick ---------------------------------------------------------------------
    def tick(self, dt: float) -> None:
        if not self.paused:
            rate = RATES[self.rate_idx]
            self.set_year(self.year + rate * dt)
            if self.year == self.now_year + (MAX_YEARS if rate > 0 else -MAX_YEARS):  # the end of the slider
                self.paused = True
                self.say(f"Straight-line motion is only good to {MAX_YEARS:,} years either way")
            self.dirty = True
        if self.spin:
            self.cam.rotate(dyaw=dt * math.radians(10))
            self.dirty = True
        if self.glide:
            a, t0 = self.glide
            b = self.origin
            t = min(1.0, (time.monotonic() - t0) / GLIDE_S)
            e = t * t * (3 - 2 * t)  # smoothstep
            self.cam.target = tuple(a[i] + (b[i] - a[i]) * e for i in range(3))
            if t >= 1:
                self.glide = None
            self.dirty = True
        elif self.cam.target != self.origin:  # the star viewed from moves with time
            self.cam.target = self.origin
            self.dirty = True
        if self.flash and time.monotonic() > self.flash_until:
            self.flash = ""
            self.dirty = True

    # ---- drawing ----------------------------------------------------------------------
    def frame_rows(self, cols: int, rows: int) -> list[str]:
        cols, rows = max(cols, 40), max(rows, 12)
        panel = self.show_panel and cols >= MIN_PANEL_COLS
        self.scene_cols = scene_cols = cols - PANEL_W if panel else cols
        if not self.glide:
            self.cam.target = self.origin
        canvas = Canvas(scene_cols, rows - 3)
        visible = self.visible()
        moved = self.show_tracks and abs(self.year - self.now_year) > 1
        tracks = {s.key: position(s, self.now_year) for s in visible} if moved else {}
        view = View(visible, self.positions(), self.origin, self.radius, self.selected, self.mark,
                    self.show_grid, self.show_drops, self.names, self.args.ascii, tracks)
        self.hits = draw(canvas, self.cam, view)
        self.labels = list(canvas.labels)
        out = self._header(cols).render_rows(0, 0) + canvas.render_rows(1, 0)
        if panel:
            side = self._keys_panel(rows - 3) if self.show_keys else self._info_panel(rows - 3)
            out += side.render_rows(1, scene_cols)
        out += self._slider(cols, rows - 2).render_rows(rows - 2, 0)
        out += self._footer(cols).render_rows(rows - 1, 0)
        return out

    def _header(self, cols: int) -> Canvas:
        c = Canvas(cols, 1)
        c.fill_row(0)
        n = sum(1 for s in self.visible() if not s.landmark)
        left = f" ✶ {'ORRERY › STARS' if self.embedded else 'stellar'}   view from {BY_KEY[self.focus].name}  ·  within {self.radius:g} ly  ·  {n} systems"
        c.text(0, 0, left, FG, bold=True)
        yaw, pitch = math.degrees(self.cam.yaw), math.degrees(self.cam.pitch)
        right = f"yaw {yaw:3.0f}°  tilt {pitch:3.0f}°  zoom {self.cam.zoom:.1f}× "
        c.text(max(len(left) + 2, cols - len(right)), 0, right, DIM)
        return c

    def _slider(self, cols: int, row: int) -> Canvas:
        """The time row: play state and rate, a scrubbable bar from -100k to +100k years, the year."""
        c = Canvas(cols, 1)
        c.fill_row(0)
        rate = RATES[self.rate_idx]
        if self.paused:
            left, color = " ⏸ paused", DIM
        else:
            left, color = f" {'▶' if rate > 0 else '◀'} {rate_name(rate)}", ACCENT
        c.text(0, 0, left, color, bold=not self.paused)
        offset = self.year - self.now_year
        right = format_year(self.year)
        if abs(offset) >= 1:
            right += f"  ({offset:+,.0f} yr)"
        right += " "
        c.text(cols - len(right), 0, right, ACCENT if abs(offset) >= 1 else FG, bold=True)
        lo_label, hi_label = f"−{MAX_YEARS // 1000}k ", f" +{MAX_YEARS // 1000}k"
        c0 = 16 + len(lo_label)
        w = cols - len(right) - c0 - len(hi_label) - 2
        if w < 10:
            self.slider = None
            return c
        self.slider = (row, c0, w)
        c.text(c0 - len(lo_label), 0, lo_label, DIM)
        c.text(c0 + w, 0, hi_label, DIM)
        now_col = c0 + (w - 1) // 2
        frac = (self.year - (self.now_year - MAX_YEARS)) / (2 * MAX_YEARS)
        knob = c0 + round(frac * (w - 1))
        for col in range(c0, c0 + w):
            between = min(now_col, knob) <= col <= max(now_col, knob)
            c.put(col, 0, Cell("━" if between else "─", ACCENT if between else (70, 76, 92)))
        c.put(now_col, 0, Cell("┼", FG))
        c.put(knob, 0, Cell("●", ACCENT, bold=True))
        return c

    def _footer(self, cols: int) -> Canvas:
        c = Canvas(cols, 1)
        c.fill_row(0)
        if self.prompt is not None:
            if self.prompt_kind == "year":
                c.text(0, 0, f" Year: {self.prompt}▏", ACCENT, bold=True)
                c.text(len(self.prompt) + 10, 0, "e.g. 38000, 10000 BCE, +5000 (from now)", DIM)
            else:
                match = find(self.prompt)
                c.text(0, 0, f" Find: {self.prompt}▏", ACCENT, bold=True)
                if match:
                    c.text(len(self.prompt) + 10, 0, f"→ {match.name}", DIM)
        elif self.flash:
            c.text(0, 0, " " + self.flash, ACCENT)
        else:
            done = "Esc planets" if self.embedded else "q quit"
            c.text(0, 0, f" Tab select · Enter view from · / find · , . time · y year · [ ] range · m measure · ? keys · {done}", DIM)
        return c

    def _keys_panel(self, rows: int) -> Canvas:
        c = Canvas(PANEL_W, rows)
        c.text(1, 0, "Keys", ACCENT, bold=True)
        for i, (k, what) in enumerate(KEYS_EMBEDDED if self.embedded else KEYS):
            if 2 + i < rows:
                c.text(1, 2 + i, f"{k:<14}", FG, bold=True)
                c.text(16, 2 + i, what[:PANEL_W - 17], DIM)
        return c

    def _info_panel(self, rows: int) -> Canvas:
        c = Canvas(PANEL_W, rows)
        s = BY_KEY[self.selected]
        focus = BY_KEY[self.focus]
        pos = self.positions()
        here = pos[s.key]
        lines: list[tuple[str, tuple, bool]] = []

        def add(text: str = "", fg=FG, bold=False):
            lines.append((text, fg, bold))

        def kv(k: str, v: str):
            add(f"{k:<12}{v}")

        add(s.name, s.color, True)
        if s.aka:
            for w in textwrap.wrap(s.aka, PANEL_W - 2):
                add(w, DIM)
        add()
        d_sun = distance(here, (0.0, 0.0, 0.0))
        if s.key != "sun":
            kv("Distance", f"{d_sun:.2f} ly  ({d_sun / LY_PER_PC:.2f} pc)")
            kv("Light left", format_year(self.year - d_sun))
        if focus.key != "sun" and s.key != focus.key:
            add(f"From {focus.name[:20]}: {distance(here, pos[focus.key]):.2f} ly")
        if s.key != "sun":
            kv("RA / Dec", f"{format_ra(parse_ra(s.ra))} {format_dec(parse_dec(s.dec))}")
        l, b = galactic_lb(here)
        if s.key != "sun":
            kv("Galactic", f"l {l:5.1f}°  b {b:+5.1f}°")
        m = s.combined_abs_mag
        if m is not None:
            kv("Luminosity", format_luminosity(luminosity(m)))
        if s.planets:
            kv("Planets", str(s.planets))
        if s.key != "sun":
            add()
            add("Motion", ACCENT, True)
            kv("Proper", f"{proper_motion(s):.2f}″ a year")
            rv = radial_velocity(s)
            if rv is None:
                kv("Radial", "not measured")
                kv("Speed", f"≥ {speed_kms(s):.0f} km/s")
            else:
                kv("Radial", f"{rv:+.1f} km/s, {'toward us' if rv < 0 else 'away'}")
                kv("Speed", f"{speed_kms(s):.0f} km/s")
            ca = closest_approach(s)
            if ca:
                kv("Closest", f"{ca[1]:.2f} ly, {format_year(ca[0])}")
        add()
        add("Stars", ACCENT, True)
        for st in s.stars:
            v = "—" if st.vmag is None else f"V {st.vmag:5.2f}"
            add(f" {st.name[:13]:<13} {st.spectral:<7} {v}", st and FG)
            add(f"   {kind(st.spectral)}", DIM)
        viewer_d = distance(here, pos[focus.key])
        if s.key != focus.key and m is not None:
            add()
            seen = apparent_mag(s.abs_mag, viewer_d)
            eye = "naked eye" if seen < 6 else "telescope"
            kv("Seen from", f"{focus.name[:20]}")
            kv("", f"mag {seen:+.1f} ({eye})")
        if self.mark and self.mark != s.key:
            add()
            kv("To marked", f"{BY_KEY[self.mark].name[:12]}")
            kv("", f"{distance(here, pos[self.mark]):.2f} ly")
        if s.note:
            add()
            for w in textwrap.wrap(s.note, PANEL_W - 2):
                add(w, DIM)
        add()
        add("Nearest neighbours", ACCENT, True)
        near = sorted((x for x in SYSTEMS if x.key != s.key), key=lambda x: distance(pos[x.key], here))[:5]
        for x in near:
            add(f" {x.name[:22]:<22} {distance(pos[x.key], here):5.2f} ly", x.color)

        for i, (text, fg, bold) in enumerate(lines[:rows]):
            c.text(1, i, text[:PANEL_W - 2], fg, bold=bold)
        # A thin rule down the left edge separates the panel from the scene.
        for r in range(rows):
            c.put(0, r, Cell("│", (55, 60, 75)))
        return c

    # ---- main loop --------------------------------------------------------------------
    def run(self) -> None:
        with T.Terminal(mouse=not self.args.no_mouse) as term:
            last = time.monotonic()
            shown, shown_size = [], None
            while self.running:
                now = time.monotonic()
                dt, last = now - last, now
                for key in term.read_keys():
                    self.handle(key)
                self.tick(dt)
                size = term.size()
                if self.dirty or size != shown_size:
                    self.dirty = False
                    rows = self.frame_rows(*size)
                    if size != shown_size or len(rows) != len(shown):
                        term.write("\x1b[2J" + "".join(rows))
                    else:  # only the rows that changed
                        term.write("".join(r for r, old in zip(rows, shown) if r != old))
                    shown, shown_size = rows, size
                time.sleep(max(0.0, 1 / 30 - (time.monotonic() - now)))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m orrery.stellar", description="Isometric map of the stellar neighbourhood in your terminal.")
    ap.add_argument("--radius", type=float, default=20.0, help="show stars within this many light years (default 20)")
    ap.add_argument("--from", dest="view_from", metavar="STAR", help="start viewing from this star, e.g. 'Sirius'")
    ap.add_argument("--select", metavar="STAR", help="start with this star selected")
    ap.add_argument("--year", metavar="YEAR", help="start in this year: 38000, '10000 BCE', or +5000 from now")
    ap.add_argument("--ascii", action="store_true", help="ASCII glyphs for stars")
    ap.add_argument("--once", action="store_true", help="print a single frame and exit")
    ap.add_argument("--size", default=None, help="frame size for --once, e.g. 140x40")
    ap.add_argument("--no-mouse", action="store_true", help="don't capture the mouse")
    args = ap.parse_args(argv)
    if args.year is not None:
        y = parse_year(args.year, present_year())
        if y is None:
            ap.error(f"can't read the year {args.year!r}")
        args.year = y

    app = StarMap(args)
    if args.select:
        s = find(args.select)
        if s is None:
            sys.exit(f"stellar: no star called {args.select!r}")
        app.select(s.key)
    if args.once:
        cols, rows = map(int, args.size.lower().split("x")) if args.size else T.Terminal.size()
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write("\x1b[2J" + "".join(app.frame_rows(cols, rows)) + f"\x1b[{rows};1H\x1b[0m\n")
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        sys.exit("stellar needs an interactive terminal (use --once to print a single frame)")
    try:
        app.run()
    except KeyboardInterrupt:
        pass
