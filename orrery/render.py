"""Isometric (orthographic) 3D projection onto a braille-pixel terminal canvas."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

from .bodies import ALL_BODIES, SUN, Body
from .kepler import Orbit, Vec3

RGB = tuple[int, int, int]
ISO_YAW = math.radians(45.0)
ISO_PITCH = math.atan(1 / math.sqrt(2))  # 35.264 deg: the true isometric elevation

# Radial distance compression so Mercury and Neptune fit on one screen.
# Each maps au -> display units, with Neptune (~30 au) landing near 5.5 in all modes.
SCALE_MODES: dict[str, callable] = {
    "sqrt": math.sqrt,
    "log": lambda r: 1.1 * math.log1p(r / 0.2),
    "linear": lambda r: 0.183 * r,
}
SCALE_ORDER = ["log", "sqrt", "linear"]
VIEW_EXTENT = 6.2  # display units visible from centre at zoom 1

_BRAILLE = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))
NEG_INF = float("-inf")


def to_display(p: Vec3, mode: str) -> Vec3:
    r = math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2)
    if r < 1e-12:
        return (0.0, 0.0, 0.0)
    f = SCALE_MODES[mode](r) / r
    return (p[0] * f, p[1] * f, p[2] * f)


@lru_cache(maxsize=512)
def display_path(orbit: Orbit, mode: str, samples: int = 240) -> tuple[Vec3, ...]:
    """An orbit's polyline in display space; orbits are immutable, so this is cached across frames."""
    return tuple(to_display(p, mode) for p in orbit.path(samples))


def shade(c: RGB, k: float) -> RGB:
    return (int(c[0] * k), int(c[1] * k), int(c[2] * k))


@dataclass
class Camera:
    yaw: float = ISO_YAW
    pitch: float = ISO_PITCH
    zoom: float = 1.0
    target: Vec3 = (0.0, 0.0, 0.0)  # display-space point at screen centre

    def reset_iso(self) -> None:
        self.yaw, self.pitch = ISO_YAW, ISO_PITCH

    def rotate(self, dyaw: float = 0.0, dpitch: float = 0.0) -> None:
        self.yaw = (self.yaw + dyaw) % math.tau
        self.pitch = max(-math.pi / 2, min(math.pi / 2, self.pitch + dpitch))


class Projector:
    """Precomputes the camera basis for one frame."""

    def __init__(self, cam: Camera, width_px: int, height_px: int,
                 extent: tuple[float, float] = (VIEW_EXTENT, VIEW_EXTENT * math.sin(ISO_PITCH))):
        """`extent`: half-width and half-height, in display units, that fill the view at zoom 1."""
        self.cy, self.sy = math.cos(cam.yaw), math.sin(cam.yaw)
        self.cp, self.sp = math.cos(cam.pitch), math.sin(cam.pitch)
        self.target = cam.target
        self.ox, self.oy = width_px / 2, height_px / 2
        fit = min((width_px / 2) / extent[0], (height_px / 2) / extent[1])
        self.scale = fit * cam.zoom

    def direction(self, v: Vec3) -> Vec3:
        """Rotate a direction into camera space: (right, up, toward viewer)."""
        x1 = v[0] * self.cy + v[1] * self.sy
        y1 = -v[0] * self.sy + v[1] * self.cy
        return x1, y1 * self.sp + v[2] * self.cp, -y1 * self.cp + v[2] * self.sp

    def many(self, pts) -> list[tuple[float, float, float]]:
        """Project a sequence of display-space points (same as calling the projector on each)."""
        cy, sy, cp, sp, sc = self.cy, self.sy, self.cp, self.sp, self.scale
        tx, ty, tz = self.target
        ox, oy = self.ox, self.oy
        out = []
        for px, py, pz in pts:
            x, y, z = px - tx, py - ty, pz - tz
            y1 = -x * sy + y * cy
            out.append((ox + (x * cy + y * sy) * sc, oy - (y1 * sp + z * cp) * sc, -y1 * cp + z * sp))
        return out

    def __call__(self, p: Vec3) -> tuple[float, float, float]:
        """Display-space point -> (pixel x, pixel y, depth); larger depth = nearer the viewer."""
        x, y, z = p[0] - self.target[0], p[1] - self.target[1], p[2] - self.target[2]
        x1 = x * self.cy + y * self.sy
        y1 = -x * self.sy + y * self.cy
        up = y1 * self.sp + z * self.cp
        depth = -y1 * self.cp + z * self.sp
        return self.ox + x1 * self.scale, self.oy - up * self.scale, depth


@dataclass
class Cell:
    ch: str
    fg: RGB
    bold: bool = False
    reverse: bool = False


class Canvas:
    def __init__(self, cols: int, rows: int):
        self.cols, self.rows = cols, rows
        self.width, self.height = cols * 2, rows * 4
        self.bits = [[0] * cols for _ in range(rows)]
        self.dot_fg: list[list[RGB | None]] = [[None] * cols for _ in range(rows)]
        self.dot_depth = [[NEG_INF] * cols for _ in range(rows)]
        self.cells: list[list[Cell | None]] = [[None] * cols for _ in range(rows)]
        self.cell_depth = [[NEG_INF] * cols for _ in range(rows)]
        self.labels: list[tuple[int, int, int, str]] = []  # name labels drawn: (row, first col, end col, body key)

    def dot(self, x: float, y: float, color: RGB, depth: float = 0.0) -> None:
        xi, yi = math.floor(x), math.floor(y)
        if not (0 <= xi < self.width and 0 <= yi < self.height):
            return
        row, col = yi >> 2, xi >> 1
        self.bits[row][col] |= _BRAILLE[yi & 3][xi & 1]
        if depth >= self.dot_depth[row][col]:
            self.dot_depth[row][col] = depth
            self.dot_fg[row][col] = color

    def line(self, a: tuple[float, float, float], b: tuple[float, float, float], color: RGB, hidden=None) -> None:
        """Draw a 3D-projected segment; `hidden(x, y, depth)` may veto individual dots."""
        (x0, y0, d0), (x1, y1, d1) = a, b
        if (max(x0, x1) < 0 or min(x0, x1) >= self.width or max(y0, y1) < 0 or min(y0, y1) >= self.height):
            return
        steps = int(min(max(abs(x1 - x0), abs(y1 - y0)), 4 * (self.width + self.height))) + 1
        # Inlined Canvas.dot: this loop is the hottest code in the renderer.
        w, h, bits, fgs, depths = self.width, self.height, self.bits, self.dot_fg, self.dot_depth
        dx, dy, dd = (x1 - x0) / steps, (y1 - y0) / steps, (d1 - d0) / steps
        for k in range(steps + 1):
            x, y, d = x0 + dx * k, y0 + dy * k, d0 + dd * k
            if not (0 <= x < w and 0 <= y < h) or (hidden is not None and hidden(x, y, d)):
                continue
            xi, yi = int(x), int(y)
            row, col = yi >> 2, xi >> 1
            bits[row][col] |= _BRAILLE[yi & 3][xi & 1]
            if d >= depths[row][col]:
                depths[row][col] = d
                fgs[row][col] = color

    def polyline(self, pts, color: RGB, hidden=None, back: RGB | None = None, split: float = 0.0) -> None:
        """Connected segments; with `back`, segments whose mean depth is below `split` use that colour.

        Same result as calling line() per segment, with the per-segment overhead inlined.
        """
        w, h, bits, fgs, depths = self.width, self.height, self.bits, self.dot_fg, self.dot_depth
        cap = 4 * (w + h)
        split2 = 2 * split
        x0, y0, d0 = pts[0]
        for x1, y1, d1 in pts[1:]:
            if not ((x0 < 0 and x1 < 0) or (x0 >= w and x1 >= w) or (y0 < 0 and y1 < 0) or (y0 >= h and y1 >= h)):
                c = color if back is None or d0 + d1 >= split2 else back
                ax, ay = x1 - x0 if x1 > x0 else x0 - x1, y1 - y0 if y1 > y0 else y0 - y1
                steps = int(min(ax if ax > ay else ay, cap)) + 1
                dx, dy, dd = (x1 - x0) / steps, (y1 - y0) / steps, (d1 - d0) / steps
                for k in range(steps + 1):
                    x, y, d = x0 + dx * k, y0 + dy * k, d0 + dd * k
                    if not (0 <= x < w and 0 <= y < h) or (hidden is not None and hidden(x, y, d)):
                        continue
                    xi, yi = int(x), int(y)
                    row, col = yi >> 2, xi >> 1
                    bits[row][col] |= _BRAILLE[yi & 3][xi & 1]
                    if d >= depths[row][col]:
                        depths[row][col] = d
                        fgs[row][col] = c
            x0, y0, d0 = x1, y1, d1

    def free(self, col: int, row: int) -> bool:
        return 0 <= col < self.cols and 0 <= row < self.rows and self.cells[row][col] is None

    def put(self, col: int, row: int, cell: Cell, depth: float = math.inf) -> None:
        if 0 <= col < self.cols and 0 <= row < self.rows and depth >= self.cell_depth[row][col]:
            self.cells[row][col] = cell
            self.cell_depth[row][col] = depth

    def text(self, col: int, row: int, s: str, fg: RGB, bold: bool = False, reverse: bool = False,
             depth: float = math.inf) -> None:
        for i, ch in enumerate(s):
            self.put(col + i, row, Cell(ch, fg, bold, reverse), depth)

    def fill_row(self, row: int, fg: RGB = (0, 0, 0)) -> None:
        for col in range(self.cols):
            self.put(col, row, Cell(" ", fg))

    def render(self, row0: int = 0, col0: int = 0) -> str:
        return "".join(self.render_rows(row0, col0))

    def render_rows(self, row0: int = 0, col0: int = 0) -> list[str]:
        """One self-positioning ANSI string per row, so callers can skip rows that haven't changed."""
        out: list[str] = []
        for row in range(self.rows):
            parts: list[str] = [f"\x1b[{row0 + row + 1};{col0 + 1}H"]
            last = None
            for col in range(self.cols):
                cell = self.cells[row][col]
                if cell is not None:
                    style = (cell.fg, cell.bold, cell.reverse)
                    ch = cell.ch
                elif self.bits[row][col]:
                    style = (self.dot_fg[row][col], False, False)
                    ch = chr(0x2800 + self.bits[row][col])
                else:
                    style, ch = None, " "
                if style != last:
                    if style is None:
                        parts.append("\x1b[0m")
                    else:
                        (r, g, b), bold, rev = style
                        parts.append(f"\x1b[0;{'1;' if bold else ''}{'7;' if rev else ''}38;2;{r};{g};{b}m")
                    last = style
                parts.append(ch)
            parts.append("\x1b[0m")
            out.append("".join(parts))
        return out


@dataclass
class SceneOptions:
    scale_mode: str = "log"
    show_orbits: bool = True
    solo_orbit: bool = False  # with show_orbits: only the selected (followed) body's orbit
    show_drops: bool = True
    show_labels: bool = True
    show_small: bool = True  # dwarf planets, asteroids and comets
    ascii: bool = False
    selected: str | None = None


@dataclass
class Scene:
    positions: dict[str, Vec3]  # heliocentric ecliptic, au
    orbits: dict[str, Orbit]
    opts: SceneOptions = field(default_factory=SceneOptions)


def draw_scene(canvas: Canvas, cam: Camera, scene: Scene) -> dict[str, tuple[int, int]]:
    """Draw the solar system; returns the canvas cell of each body drawn (for mouse picking)."""
    proj = Projector(cam, canvas.width, canvas.height)
    mode = scene.opts.scale_mode
    disp = lambda p: to_display(p, mode)  # noqa: E731
    bodies = [b for b in ALL_BODIES
              if (b is SUN or b.key in scene.positions)  # no data, nothing to draw (not a dot on the Sun)
              and (scene.opts.show_small or not b.small or b.key == scene.opts.selected)]

    if scene.opts.show_orbits:
        for body in bodies[1:]:
            orbit = scene.orbits.get(body.key)
            if orbit is None or (scene.opts.solo_orbit and body.key != scene.opts.selected):
                continue
            if body.key == scene.opts.selected:
                k_front, k_back = 0.75, 0.38
            else:
                k_front, k_back = (0.32, 0.14) if body.small else (0.5, 0.22)
            canvas.polyline(proj.many(display_path(orbit, mode)), shade(body.color, k_front),
                            back=shade(body.color, k_back))

    # Bodies, far to near, so nearer glyphs win.
    items = []
    for body in bodies:
        d = disp(scene.positions.get(body.key, (0.0, 0.0, 0.0)))  # only the Sun may be missing
        items.append((proj(d), d, body))
    items.sort(key=lambda it: it[0][2])

    if scene.opts.show_drops:
        for (_, d, body) in items:
            if body is SUN:
                continue
            foot = proj((d[0], d[1], 0.0))
            top = proj(d)
            canvas.line(top, foot, shade(body.color, 0.35))
            canvas.dot(foot[0], foot[1], shade(body.color, 0.6), foot[2])

    for (sx, sy, depth), d, body in items:
        if body.kind == "comet":
            _draw_tail(canvas, proj, body, scene.positions.get(body.key), d)
        _draw_body(canvas, body, sx, sy, depth, proj.scale, scene.opts)

    if scene.opts.show_labels:
        for (sx, sy, _), _, body in reversed(items):  # nearest bodies claim label space first
            if body is SUN and sun_radius_px(proj.scale) >= SUN_GLYPH_PX:  # beside the disc, not on it
                sx += sun_radius_px(proj.scale) - 2
            _draw_label(canvas, body, sx, sy, body.key == scene.opts.selected)
    return {body.key: (int(sx) // 2, int(sy) // 4) for (sx, sy, _), _, body in items}


TAIL_MAX_AU = 4.0  # comets grow a visible tail inside roughly Jupiter's orbit


def _draw_tail(canvas: Canvas, proj: Projector, body: Body, p_au: Vec3 | None, d: Vec3) -> None:
    """An ion tail: straight away from the Sun, longer and brighter closer in."""
    if p_au is None:
        return
    r_au = math.sqrt(p_au[0] ** 2 + p_au[1] ** 2 + p_au[2] ** 2)
    rd = math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2)
    if r_au > TAIL_MAX_AU or rd < 1e-9:
        return
    length = min(1.2, 0.5 / max(r_au, 0.3)) * (1 - r_au / TAIL_MAX_AU)  # display units
    ux, uy, uz = d[0] / rd, d[1] / rd, d[2] / rd
    segments = 4
    for k in range(segments):  # fade out along the tail
        a = proj((d[0] + ux * length * k / segments, d[1] + uy * length * k / segments, d[2] + uz * length * k / segments))
        b = proj((d[0] + ux * length * (k + 1) / segments, d[1] + uy * length * (k + 1) / segments,
                  d[2] + uz * length * (k + 1) / segments))
        canvas.line(a, b, shade(body.color, 0.8 * (1 - k / segments)))


SUN_MIN_PX, SUN_MAX_PX = 1.5, 14.0  # the Sun's drawn radius in dots: decorative, not to scale
SUN_GLYPH_PX = 4.5  # below this radius a braille disc looks like a block, so ☼ marks the Sun instead
SUN_CORE, SUN_EDGE = (255, 248, 215), (255, 165, 45)
SUN_CLEAR_PX = 2.0  # orbit dots are cleared this far beyond the Sun's limb, keeping it round


def sun_radius_px(scale: float) -> float:
    """The Sun grows with zoom (within limits), like everything else on screen."""
    return max(SUN_MIN_PX, min(SUN_MAX_PX, 0.18 * scale))


def _draw_body(canvas: Canvas, body: Body, sx: float, sy: float, depth: float, scale: float, opts: SceneOptions) -> None:
    col, row = int(sx) // 2, int(sy) // 4
    if body is SUN:
        # A solid disc, white-hot at the centre fading to orange at the limb. (Hollow rings of
        # dots broke apart around the glyph and label once they grew.)
        r = sun_radius_px(scale)
        if r < SUN_GLYPH_PX:  # too small for a round disc: the glyph's cell hides whatever is beneath
            canvas.put(col, row, Cell("*" if opts.ascii else "☼", (255, 240, 160), bold=True), depth + 1e6)
            return
        reach = math.ceil(r + SUN_CLEAR_PX)
        bits, fgs, depths = canvas.bits, canvas.dot_fg, canvas.dot_depth
        disc, ring = [], []
        for yi in range(max(0, math.floor(sy) - reach), min(canvas.height, math.floor(sy) + reach + 1)):
            for xi in range(max(0, math.floor(sx) - reach), min(canvas.width, math.floor(sx) + reach + 1)):
                t = math.hypot(xi + 0.5 - sx, yi + 0.5 - sy) / r
                if t <= 1:
                    disc.append((xi, yi, t))
                elif t <= 1 + SUN_CLEAR_PX / r:
                    ring.append((xi, yi))
        # Orbits seen edge-on run straight through the Sun. Clear them: from every cell the disc
        # touches (a cell has one colour, so any orbit dot left there would turn Sun-coloured), and
        # from a thin ring round the limb, so the Sun keeps a clean round outline.
        for cr, cc in {(yi >> 2, xi >> 1) for xi, yi, _ in disc}:
            bits[cr][cc] = 0
        for xi, yi in ring:
            bits[yi >> 2][xi >> 1] &= ~_BRAILLE[yi & 3][xi & 1]
        for xi, yi, t in disc:  # white-hot at the centre, fading to orange at the limb
            cr, cc = yi >> 2, xi >> 1
            bits[cr][cc] |= _BRAILLE[yi & 3][xi & 1]
            fgs[cr][cc] = tuple(int(a + (b - a) * t**1.5) for a, b in zip(SUN_CORE, SUN_EDGE))
            depths[cr][cc] = depth
        return
    if body.kind == "comet":
        # ◆ sits in Geometric Shapes beside ●, so terminal fonts have it at one cell wide. (✦ is a
        # dingbat most fonts lack; the fallback font drew it wider, onto the selection bracket.)
        glyph = "*" if opts.ascii else "◆"
    elif body.small:
        glyph = "." if opts.ascii else "•"
    else:
        glyph = "o" if opts.ascii else "●"
    canvas.put(col, row, Cell(glyph, body.color, bold=True), depth + 1e6)
    if body.key == opts.selected:
        for dc, ch in ((-1, "["), (1, "]")):
            if canvas.free(col + dc, row):
                canvas.put(col + dc, row, Cell(ch, (255, 255, 255), bold=True), depth + 1e6)


def _draw_label(canvas: Canvas, body, sx: float, sy: float, selected: bool) -> None:
    col, row = int(sx) // 2, int(sy) // 4
    name = f" {body.name} " if selected else body.name
    pad = 3 if selected else 2
    for c0, r0 in ((col + pad, row), (col - pad - len(name) + 1, row), (col - len(name) // 2, row - 1), (col - len(name) // 2, row + 1)):
        if all(canvas.free(c0 + i, r0) for i in range(len(name))) and all(
            canvas.cells[r0][c] is None for c in (c0 - 1, c0 + len(name)) if 0 <= c < canvas.cols
        ):
            fg = (255, 255, 255) if selected else shade(body.color, 0.7 if getattr(body, "small", False) else 0.95)
            canvas.text(c0, r0, name, fg, bold=selected, reverse=selected)
            canvas.labels.append((r0, max(c0, 0), min(c0 + len(name), canvas.cols), body.key))  # clickable
            return
