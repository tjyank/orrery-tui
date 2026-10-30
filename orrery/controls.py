"""Keyboard, mouse and menu input (mixed into App)."""
from __future__ import annotations

import math
import time

from . import terminal as T
from .bodies import ALL_BODIES, PLANETS
from .ephemeris import jd_now
from .jumps import parse_when, presets
from .moons import MOONS_OF
from .render import SCALE_ORDER
from .ui import DOUBLE_CLICK_S, RATES, REALTIME


class Controls:
    """Input handling for App: keys, mouse, the jump menu and its date prompt."""

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
        elif key == "o":  # all orbits -> just the selected (followed) body's -> none
            if not opts.show_orbits:
                opts.show_orbits, opts.solo_orbit = True, False
            elif not opts.solo_orbit:
                opts.solo_orbit = True
            else:
                opts.show_orbits = False
            if not opts.show_orbits:
                self.say("Orbits hidden")
            elif opts.solo_orbit:
                self.say("Selected body's orbit")
            else:
                self.say("All orbits shown")
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
