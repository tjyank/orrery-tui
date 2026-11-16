"""Drawing the neighbourhood: galactic-plane grid, drop lines, stars and labels.

Built on orrery-tui's braille canvas and isometric projector. Display units are light years;
the camera target is the star being viewed from, so everything turns around it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..render import ISO_PITCH, Camera, Canvas, Cell, Projector, shade

from .catalog import System
from .coords import Vec3, distance

GRID = (48, 62, 92)
GRID_LABEL = (96, 112, 150)
MEASURE = (255, 200, 90)
WHITE = (255, 255, 255)

# Where the galactic axes point, with the constellation that lies that way from the Sun.
DIRECTIONS = [((1, 0), "Sgr · galactic centre"), ((0, 1), "Cyg · l 90°"),
              ((-1, 0), "Aur · anticentre"), ((0, -1), "Vel · l 270°")]


@dataclass
class View:
    systems: list[System]  # those to draw
    positions: dict[str, Vec3]  # where each system is at the time shown (galactic ly, Sun-centred)
    origin: Vec3  # the star viewed from: rings and drop lines sit in the galactic plane through it
    radius: float  # light years: the outer ring
    selected: str | None = None
    mark: str | None = None  # the other end of the measuring line
    show_grid: bool = True
    show_drops: bool = True
    names: str = "all"  # all | bright | none
    ascii: bool = False
    tracks: dict[str, Vec3] = field(default_factory=dict)  # present-day positions, to draw paths from


def ring_step(radius: float) -> float:
    return 5.0 if radius <= 25 else 10.0 if radius <= 60 else 25.0


def projector(cam: Camera, canvas: Canvas, radius: float) -> Projector:
    half = radius * 1.12
    return Projector(cam, canvas.width, canvas.height, (half, radius * 0.95))


def glyph(sys_: System, ascii_: bool) -> str:
    m = sys_.abs_mag
    if m is None:  # brown dwarfs
        return "," if ascii_ else "·"
    if m < 6.0:
        return "O" if ascii_ else "●"
    if m < 11.5:
        return "o" if ascii_ else "•"
    return "." if ascii_ else "∙"


def is_bright(sys_: System) -> bool:
    m = sys_.abs_mag
    return sys_.key == "sun" or (m is not None and m < 8.5) or len(sys_.stars) > 2


def draw(canvas: Canvas, cam: Camera, view: View) -> dict[str, tuple[int, int]]:
    """Draw the scene; returns each drawn system's canvas cell, for mouse picking."""
    proj = projector(cam, canvas, view.radius)
    ox, oy, oz = view.origin
    if view.show_grid:
        _grid(canvas, proj, view)

    pos = view.positions
    items = sorted(((proj(pos[s.key]), s) for s in view.systems), key=lambda it: it[0][2])  # far to near

    for s in view.systems:  # paths travelled since the present: straight lines, fading toward now
        start = view.tracks.get(s.key)
        if start is not None:
            a, b = proj(start), proj(pos[s.key])
            mid = tuple((a[i] + b[i]) / 2 for i in range(3))
            canvas.line(a, mid, shade(s.color, 0.16))
            canvas.line(mid, b, shade(s.color, 0.3))

    if view.show_drops:
        for (_, _, _), s in items:
            p = pos[s.key]
            foot = proj((p[0], p[1], oz))
            top = proj(p)
            above = p[2] >= oz
            canvas.line(top, foot, shade(s.color, 0.32 if above else 0.2))
            canvas.dot(foot[0], foot[1], shade(s.color, 0.55), foot[2])

    by_key = {s.key: (p, s) for p, s in items}
    if view.mark and view.selected and view.mark in by_key and view.selected in by_key:
        _measure(canvas, by_key[view.selected], by_key[view.mark], distance(pos[view.selected], pos[view.mark]))

    for (sx, sy, depth), s in items:
        _star(canvas, s, sx, sy, depth, view)

    if view.names != "none":
        for (sx, sy, _), s in reversed(items):  # nearest first: they claim label space first
            if s.key == view.selected or s.key == view.mark or view.names == "all" or is_bright(s):
                _label(canvas, s, sx, sy, s.key == view.selected)
    return {s.key: (int(sx) // 2, int(sy) // 4) for (sx, sy, _), s in items}


def _grid(canvas: Canvas, proj: Projector, view: View) -> None:
    ox, oy, oz = view.origin
    step = ring_step(view.radius)
    n_rings = max(1, int(view.radius // step + 1e-9))
    for i in range(1, n_rings + 1):
        r = step * i
        pts = proj.many([(ox + r * math.cos(t), oy + r * math.sin(t), oz)
                         for t in (math.tau * k / 160 for k in range(161))])
        outer = i == n_rings
        canvas.polyline(pts, shade(GRID, 1.25 if outer else 1.0), back=shade(GRID, 0.7 if outer else 0.55),
                        split=proj((ox, oy, oz))[2])
        x, y, _ = max(pts, key=lambda p: p[1])  # the ring's lowest point on screen
        _free_text(canvas, int(x) // 2 - 2, int(y) // 4 + 1, f"{r:g} ly", GRID_LABEL)
    r = step * n_rings
    for (dx, dy), name in DIRECTIONS:
        a, b = proj((ox, oy, oz)), proj((ox + dx * r * 1.06, oy + dy * r * 1.06, oz))
        canvas.line(a, b, shade(GRID, 0.6))
        end = proj((ox + dx * r * 1.12, oy + dy * r * 1.12, oz))
        col, row = int(end[0]) // 2, int(end[1]) // 4
        if dx or dy:
            col -= len(name) // 2 if abs(end[0] - a[0]) < canvas.width * 0.08 else (0 if end[0] > a[0] else len(name) - 1)
        _free_text(canvas, col, row, name, GRID_LABEL)
    top = proj((ox, oy, oz + r * 0.3))
    canvas.line(proj((ox, oy, oz)), top, shade(GRID, 0.8))
    _free_text(canvas, int(top[0]) // 2 + 1, int(top[1]) // 4, "NGP", GRID_LABEL)


def _free_text(canvas: Canvas, col: int, row: int, text: str, fg) -> None:
    """Grid captions: drawn only where nothing else is, at the lowest depth."""
    col = max(0, min(canvas.cols - len(text), col))
    if all(canvas.free(col + i, row) for i in range(len(text))):
        canvas.text(col, row, text, fg, depth=-math.inf)


def _measure(canvas: Canvas, a, b, ly: float) -> None:
    (pa, _), (pb, _) = a, b
    n = 40
    for k in range(n):  # dashed
        if k % 2 == 0:
            t0, t1 = k / n, (k + 1) / n
            canvas.line(tuple(pa[i] + (pb[i] - pa[i]) * t0 for i in range(3)),
                        tuple(pa[i] + (pb[i] - pa[i]) * t1 for i in range(3)), MEASURE)
    text = f" {ly:.2f} ly "
    col = int((pa[0] + pb[0]) / 2) // 2 - len(text) // 2
    row = int((pa[1] + pb[1]) / 2) // 4
    canvas.text(max(0, col), row, text, (20, 20, 20), bold=True, reverse=False, depth=math.inf)
    for i in range(len(text)):
        c = canvas.cells[row][max(0, col) + i] if 0 <= row < canvas.rows and max(0, col) + i < canvas.cols else None
        if c is not None:
            c.fg, c.reverse = MEASURE, True


def _star(canvas: Canvas, s: System, sx: float, sy: float, depth: float, view: View) -> None:
    col, row = int(sx) // 2, int(sy) // 4
    m = s.abs_mag
    if m is not None and m < 3.0 and not view.ascii:  # luminous stars get a faint halo of dots
        r = 3.5 if m < 1.5 else 2.6
        for k in range(16):
            t = math.tau * k / 16
            canvas.dot(sx + r * 1.0 * math.cos(t), sy + r * 1.0 * math.sin(t), shade(s.color, 0.45), depth)
    color = s.color if not s.landmark else shade(s.color, 0.8)
    canvas.put(col, row, Cell(glyph(s, view.ascii), color, bold=True), depth + 1e6)
    if s.key == view.selected:
        for dc, ch in ((-1, "["), (1, "]")):
            if canvas.free(col + dc, row):
                canvas.put(col + dc, row, Cell(ch, WHITE, bold=True), depth + 1e6)
    elif s.key == view.mark:
        for dc, ch in ((-1, "<"), (1, ">")):
            if canvas.free(col + dc, row):
                canvas.put(col + dc, row, Cell(ch, MEASURE, bold=True), depth + 1e6)


def _label(canvas: Canvas, s: System, sx: float, sy: float, selected: bool) -> None:
    col, row = int(sx) // 2, int(sy) // 4
    name = f" {s.name} " if selected else s.name
    pad = 3 if selected else 2
    for c0, r0 in ((col + pad, row), (col - pad - len(name) + 1, row),
                   (col - len(name) // 2, row - 1), (col - len(name) // 2, row + 1)):
        if c0 < 0 or c0 + len(name) > canvas.cols:
            continue
        if all(canvas.free(c0 + i, r0) for i in range(len(name))) and all(
                canvas.cells[r0][c] is None for c in (c0 - 1, c0 + len(name)) if 0 <= c < canvas.cols):
            fg = WHITE if selected else shade(s.color, 0.6 if s.landmark else 0.9)
            canvas.text(c0, r0, name, fg, bold=selected, reverse=selected)
            canvas.labels.append((r0, c0, c0 + len(name), s.key))
            return
