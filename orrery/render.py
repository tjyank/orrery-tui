"""Isometric (orthographic) 3D projection onto a braille-pixel terminal canvas."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

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

GRID_RINGS_AU = (1, 5, 10, 20, 30, 40)

_BRAILLE = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))
NEG_INF = float("-inf")
BACKDROP = -1e18  # depth for grid labels: drawn, but anything else may overwrite them


def to_display(p: Vec3, mode: str) -> Vec3:
    r = math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2)
    if r < 1e-12:
        return (0.0, 0.0, 0.0)
    f = SCALE_MODES[mode](r) / r
    return (p[0] * f, p[1] * f, p[2] * f)


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
        for k in range(steps + 1):
            t = k / steps
            x, y, d = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, d0 + (d1 - d0) * t
            if hidden is None or not hidden(x, y, d):
                self.dot(x, y, color, d)

    def free(self, col: int, row: int) -> bool:
        return 0 <= col < self.cols and 0 <= row < self.rows and self.cell_depth[row][col] <= BACKDROP

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
        return "".join(out)


@dataclass
class SceneOptions:
    scale_mode: str = "log"
    show_orbits: bool = True
    show_grid: bool = True
    show_drops: bool = True
    show_labels: bool = True
    ascii: bool = False
    selected: str | None = None


@dataclass
class Scene:
    positions: dict[str, Vec3]  # heliocentric ecliptic, au
    orbits: dict[str, Orbit]
    opts: SceneOptions = field(default_factory=SceneOptions)


GRID = (52, 58, 72)
GRID_LABEL = (96, 104, 124)


def draw_scene(canvas: Canvas, cam: Camera, scene: Scene) -> None:
    proj = Projector(cam, canvas.width, canvas.height)
    mode = scene.opts.scale_mode
    disp = lambda p: to_display(p, mode)  # noqa: E731

    if scene.opts.show_grid:
        _draw_grid(canvas, proj, mode)

    if scene.opts.show_orbits:
        for body in ALL_BODIES[1:]:
            orbit = scene.orbits.get(body.key)
            if orbit is None:
                continue
            k_front, k_back = (0.75, 0.38) if body.key == scene.opts.selected else (0.5, 0.22)
            pts = [proj(disp(p)) for p in orbit.path(240)]
            for a, b in zip(pts, pts[1:]):
                canvas.line(a, b, shade(body.color, k_front if a[2] + b[2] >= 0 else k_back))

    # Bodies, far to near, so nearer glyphs win.
    items = []
    for body in ALL_BODIES:
        p = scene.positions.get(body.key, (0.0, 0.0, 0.0))
        d = disp(p)
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

    for (sx, sy, depth), _, body in items:
        _draw_body(canvas, body, sx, sy, depth, proj.scale, scene.opts)

    if scene.opts.show_labels:
        for (sx, sy, _), _, body in reversed(items):  # nearest bodies claim label space first
            _draw_label(canvas, body, sx, sy, body.key == scene.opts.selected)


def _draw_grid(canvas: Canvas, proj: Projector, mode: str) -> None:
    for r_au in GRID_RINGS_AU:
        rd = SCALE_MODES[mode](r_au)
        n = 160
        pts = [proj((rd * math.cos(math.tau * k / n), rd * math.sin(math.tau * k / n), 0.0)) for k in range(n + 1)]
        for a, b in zip(pts, pts[1:]):
            canvas.line(a, b, GRID)
        lx, ly, _ = proj((rd * math.cos(-0.35), rd * math.sin(-0.35), 0.0))
        col, row, label = int(lx) // 2 + 1, int(ly) // 4, f"{r_au}au"
        if 0 <= row < canvas.rows and all(
            canvas.cells[row][c] is None for c in range(col - 1, col + len(label) + 1) if 0 <= c < canvas.cols
        ):
            canvas.text(col, row, label, GRID_LABEL, depth=BACKDROP)
    r_in, r_out = SCALE_MODES[mode](0.25), SCALE_MODES[mode](GRID_RINGS_AU[-1])
    for deg in (0, 90, 180, 270):
        c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        canvas.line(proj((r_in * c, r_in * s, 0.0)), proj((r_out * c, r_out * s, 0.0)), GRID)
    ex, ey, _ = proj((r_out * 1.04, 0.0, 0.0))
    canvas.text(int(ex) // 2, int(ey) // 4, "♈ 0°", GRID_LABEL, depth=BACKDROP)


def _draw_body(canvas: Canvas, body: Body, sx: float, sy: float, depth: float, scale: float, opts: SceneOptions) -> None:
    col, row = int(sx) // 2, int(sy) // 4
    if body is SUN:
        glow = max(1.5, min(6.0, 0.18 * scale))
        steps = int(glow * 8)
        for ring in (glow, glow * 0.6):
            for k in range(steps):
                a = math.tau * k / steps
                canvas.dot(sx + ring * math.cos(a), sy + ring * math.sin(a), shade(body.color, 0.8), depth)
        canvas.put(col, row, Cell("*" if opts.ascii else "☼", (255, 240, 160), bold=True), depth + 1e6)
        return
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
            fg = (255, 255, 255) if selected else shade(body.color, 0.95)
            canvas.text(c0, r0, name, fg, bold=selected, reverse=selected)
            return
