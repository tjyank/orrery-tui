"""The header, footer and info panels (mixed into App)."""
from __future__ import annotations

import math
import time

from .bodies import ALL_BODIES, BY_KEY, SUN
from .ephemeris import jd_to_datetime
from .events import EARTH_HORIZON_DAYS, SYSTEM_HORIZON_DAYS
from .jumps import next_perihelion
from .kepler import AU_KM, DAY_S, Vec3
from .moons import MOONS_OF, MOON_BY_KEY, spin_axis
from .render import Canvas, shade
from .system import in_shadow
from .ui import (ACCENT, C_KM_S, DIM, FG, PANEL_W, RATES, RATE_NAMES, STATUS_COLORS,
                 vec_angle, countdown, vec_len, format_period, short_event, vec_sub, unit_vector)


class Panels:
    """Header, footer and the info panels of both views, for App."""

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
        c.text(2, y, "Upcoming (searching…)" if searching else "Upcoming, seen from Earth", DIM)
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
                when = countdown(begins - self.sim_jd)
            else:
                when = f"{jd_to_datetime(begins):%b %d}"
            what = ev.title(planet.name) if planet.key == "earth" else short_event(ev)
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
                ("From Sun", f"{vec_len(p):.4f} au"),
            ]
            if planet.key != "earth":
                d_e = vec_len(vec_sub(p, pos["earth"]))
                lines += [("From Earth", f"{d_e:.4f} au"), ("Light time", f"{d_e * AU_KM / C_KM_S / 60:.1f} min")]
        else:
            orbit = orbits[moon.key]
            r, v = orbit.state(self.sim_jd)
            r_km = vec_len(r) * AU_KM
            normal = (r[1] * v[2] - r[2] * v[1], r[2] * v[0] - r[0] * v[2], r[0] * v[1] - r[1] * v[0])
            incl = vec_angle(normal, spin_axis(planet))
            dark = in_shadow(moon_pos[moon.key], unit_vector(sun_dir), planet.radius_km, vec_len(sun_dir))
            lines += [
                (f"From {planet.name}", f"{r_km:,.0f} km"),
                ("", f"{r_km / planet.radius_km:.1f} planet radii"),
                ("Speed", f"{vec_len(v) * AU_KM / DAY_S:.3f} km/s"),
                ("Sunlight", "in shadow" if dark else "sunlit"),
                ("", ""),
                ("Semi-major", f"{orbit.a * AU_KM:,.0f} km"),
                ("Eccentric.", f"{orbit.e:.4f}"),
                ("Incl. (eq.)", f"{incl:.2f}°" + (" retro" if incl > 90 else "")),
                ("Period", format_period(orbit.period_days)),
                ("Radius", f"{moon.radius_km:,.1f} km"),
            ]
        c = self._panel_frame(rows, moon.name if moon else f"{planet.name} system", (moon or planet).color,
                              self.sys_follow)
        y = self._panel_lines(c, lines, rows)
        if moons:
            y = self._events_rows(c, y, rows, moon.key if moon else None)
        entries = [(m.key, m.color, m.name, f"{vec_len(moon_pos[m.key]) * AU_KM / 1e3:,.0f}k km") for m in moons]
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
            d_e = vec_len(earth)
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
            r = vec_len(p)
            lon = math.degrees(math.atan2(p[1], p[0])) % 360
            lat = math.degrees(math.asin(p[2] / r))
            lines += [
                ("Type", body.type_name),
                ("From Sun", f"{r:.4f} au"),
                ("", f"{r * AU_KM / 1e6:,.1f} M km"),
            ]
            if body.key != "earth":
                d_e = vec_len(vec_sub(p, earth))
                lines += [
                    ("From Earth", f"{d_e:.4f} au"),
                    ("Light time", f"{d_e * AU_KM / C_KM_S / 60:.1f} min"),
                ]
            lines += [
                ("Speed", f"{vec_len(v) * AU_KM / DAY_S:.2f} km/s"),
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
        entries = [(b.key, b.color, b.name, f"{vec_len(pos[b.key]):6.2f} au") for b in ALL_BODIES
                   if b is not SUN and (self.opts.show_small or not b.small or b.key == self.opts.selected)]
        self._list_rows(c, y, rows, "Bodies (Tab, click)", entries, self.opts.selected)
        src = self.snapshot
        foot = f"data fetched {src.fetched_at.astimezone():%H:%M}" if src.source != "offline" else "analytic elements (no network)"
        if rows > 2:
            c.text(2, rows - 1, foot[: PANEL_W - 3], shade(STATUS_COLORS[src.source], 0.8))
        return c
