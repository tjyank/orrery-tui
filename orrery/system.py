"""Planetary system view: one planet, lit by the Sun, with its moons on their real orbits."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .bodies import Body
from .kepler import AU_KM, Orbit, Vec3
from .moons import Moon, pole_vector
from .render import BACKDROP, GRID, GRID_LABEL, Camera, Canvas, Cell, Projector, SceneOptions, _draw_label, shade

SYSTEM_EXTENT = 5.5  # display units from the planet to the outermost apoapsis at zoom 1
MIN_RING_GAP = 0.55  # display units between labelled distance rings


def _norm(v: Vec3) -> Vec3:
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    return (v[0] / n, v[1] / n, v[2] / n)


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def system_reach_km(planet: Body, moons: list[Moon]) -> float:
    """Distance the view is scaled to fit: the farthest apoapsis, or a few radii with no moons."""
    return max([m.elements[0] * (1 + m.elements[1]) for m in moons] + [8 * planet.radius_km])


def system_scale(mode: str, radius_km: float, reach_km: float):
    """km from the planet centre -> display units. `log` keeps close-in moons clear of the disc."""
    if mode == "log":
        k = SYSTEM_EXTENT / math.log1p(reach_km / radius_km)
        return lambda r: k * math.log1p(r / radius_km)
    if mode == "sqrt":
        k = SYSTEM_EXTENT / math.sqrt(reach_km)
        return lambda r: k * math.sqrt(r)
    k = SYSTEM_EXTENT / reach_km
    return lambda r: k * r


def in_shadow(p_au: Vec3, sun_dir: Vec3, radius_km: float) -> bool:
    """Is a planetocentric point inside the planet's (cylindrical) shadow?"""
    along = _dot(p_au, sun_dir)
    if along >= 0:
        return False
    off = (p_au[0] - along * sun_dir[0], p_au[1] - along * sun_dir[1], p_au[2] - along * sun_dir[2])
    return math.sqrt(_dot(off, off)) * AU_KM < radius_km


@dataclass
class SystemScene:
    planet: Body
    moons: list[Moon]
    positions: dict[str, Vec3]  # planetocentric ecliptic J2000, au
    orbits: dict[str, Orbit]
    sun_dir: Vec3  # unit vector from the planet towards the Sun
    opts: SceneOptions = field(default_factory=SceneOptions)

    def __post_init__(self) -> None:
        self.scale = system_scale(self.opts.scale_mode, self.planet.radius_km, system_reach_km(self.planet, self.moons))

    def disp(self, p_au: Vec3) -> Vec3:
        r = math.sqrt(_dot(p_au, p_au)) * AU_KM
        if r < 1e-9:
            return (0.0, 0.0, 0.0)
        f = self.scale(r) / r * AU_KM
        return (p_au[0] * f, p_au[1] * f, p_au[2] * f)


def _fit_extent(pole: Vec3) -> tuple[float, float]:
    """Screen half-extents of the equatorial disc from the default isometric camera.

    Fitting to a fixed camera keeps the zoom steady while the user rotates the view.
    """
    right, up, _ = Projector(Camera(), 2, 2).direction(pole)
    ext = SYSTEM_EXTENT * 1.08
    return (ext * max(0.5, math.sqrt(1 - right**2)), ext * max(0.5, math.sqrt(1 - up**2)))


def draw_system(canvas: Canvas, cam: Camera, sc: SystemScene) -> None:
    pole = pole_vector(sc.planet)
    proj = Projector(cam, canvas.width, canvas.height, _fit_extent(pole))
    e1 = _norm(_cross((0.0, 0.0, 1.0), pole)) if abs(pole[2]) < 0.999 else (1.0, 0.0, 0.0)
    e2 = _cross(pole, e1)  # (e1, e2) span the planet's equatorial plane

    r_disp = sc.scale(sc.planet.radius_km)
    pcx, pcy, pcd = proj((0.0, 0.0, 0.0))
    r_px = max(r_disp * proj.scale, 1.0)

    def hidden(x: float, y: float, d: float) -> bool:
        """Behind the planet's disc, as seen by the camera."""
        rho2 = ((x - pcx) ** 2 + (y - pcy) ** 2) / r_px**2
        return rho2 < 1 and d < pcd + math.sqrt(1 - rho2) * r_disp

    def plane_point(r: float, a: float) -> Vec3:
        c, s = math.cos(a) * r, math.sin(a) * r
        return (e1[0] * c + e2[0] * s, e1[1] * c + e2[1] * s, e1[2] * c + e2[2] * s)

    if sc.opts.show_grid:
        _draw_rings(canvas, proj, sc, plane_point, hidden)
        ends = [proj(tuple(k * r_disp * x for x in pole)) for k in (-1.6, 1.6)]
        canvas.line(*ends, GRID, hidden)
        nx, ny, _ = ends[1]
        if canvas.free(int(nx) // 2, int(ny) // 4):
            canvas.text(int(nx) // 2, int(ny) // 4, "N", GRID_LABEL, depth=BACKDROP)

    if sc.opts.show_orbits:
        for moon in sc.moons:
            orbit = sc.orbits.get(moon.key)
            if orbit is None:
                continue
            k_front, k_back = (0.75, 0.38) if moon.key == sc.opts.selected else (0.5, 0.22)
            pts = [proj(sc.disp(p)) for p in orbit.path(240)]
            for a, b in zip(pts, pts[1:]):
                canvas.line(a, b, shade(moon.color, k_front if a[2] + b[2] >= 2 * pcd else k_back), hidden)

    items = []
    for moon in sc.moons:
        p = sc.positions[moon.key]
        d = sc.disp(p)
        items.append((proj(d), d, p, moon))
    items.sort(key=lambda it: it[0][2])

    if sc.opts.show_drops:
        for (_, d, _, moon) in items:
            h = _dot(d, pole)
            foot = proj((d[0] - h * pole[0], d[1] - h * pole[1], d[2] - h * pole[2]))
            canvas.line(proj(d), foot, shade(moon.color, 0.35), hidden)
            if not hidden(*foot):
                canvas.dot(foot[0], foot[1], shade(moon.color, 0.6), foot[2])

    _draw_planet(canvas, proj, sc, (pcx, pcy, pcd), r_disp, r_px)

    for (sx, sy, depth), _, p, moon in items:
        if hidden(sx, sy, depth):
            continue
        dark = in_shadow(p, sc.sun_dir, sc.planet.radius_km)
        glyph = ("o" if moon.radius_km >= 500 else ".") if sc.opts.ascii else ("●" if moon.radius_km >= 500 else "•")
        col, row = int(sx) // 2, int(sy) // 4
        canvas.put(col, row, Cell(glyph, shade(moon.color, 0.35) if dark else moon.color, bold=not dark), depth + 1e6)
        if moon.key == sc.opts.selected:
            for dc, ch in ((-1, "["), (1, "]")):
                if canvas.free(col + dc, row):
                    canvas.put(col + dc, row, Cell(ch, (255, 255, 255), bold=True), depth + 1e6)

    if sc.opts.show_labels:
        _draw_label(canvas, sc.planet, pcx + r_px, pcy, sc.planet.key == sc.opts.selected)
        for (sx, sy, depth), _, _, moon in reversed(items):
            if not hidden(sx, sy, depth):
                _draw_label(canvas, moon, sx, sy, moon.key == sc.opts.selected)

    _draw_sun_marker(canvas, proj, sc.sun_dir, sc.opts.ascii)


def _draw_rings(canvas: Canvas, proj: Projector, sc: SystemScene, plane_point, hidden) -> None:
    reach = system_reach_km(sc.planet, sc.moons)
    last = sc.scale(sc.planet.radius_km)
    for km in (m * 10**k for k in range(3, 9) for m in (1, 2, 5)):
        if km < 1.5 * sc.planet.radius_km or km > reach * 1.05:
            continue
        r = sc.scale(km)
        if r - last < MIN_RING_GAP:
            continue
        last = r
        n = 160
        pts = [proj(plane_point(r, math.tau * k / n)) for k in range(n + 1)]
        for a, b in zip(pts, pts[1:]):
            canvas.line(a, b, GRID, hidden)
        label = f"{km / 1e6:g}M km" if km >= 1e6 else f"{km / 1e3:g}k km"
        lx, ly, _ = proj(plane_point(r, -0.35))
        col, row = int(lx) // 2 + 1, int(ly) // 4
        if 0 <= row < canvas.rows and all(
            canvas.cells[row][c] is None for c in range(col - 1, col + len(label) + 1) if 0 <= c < canvas.cols
        ):
            canvas.text(col, row, label, GRID_LABEL, depth=BACKDROP)


def _draw_planet(canvas: Canvas, proj: Projector, sc: SystemScene, centre, r_disp: float, r_px: float) -> None:
    """A braille-dot sphere: solid on the day side, a sparse dim stipple on the night side."""
    pcx, pcy, pcd = centre
    color = sc.planet.color
    if r_px < 2:
        canvas.put(int(pcx) // 2, int(pcy) // 4, Cell("o" if sc.opts.ascii else "●", color, bold=True), pcd + 1e6)
        return
    sun = proj.direction(sc.sun_dir)
    # Only the part of the disc that is on the canvas: zoomed in, it can be far larger.
    y_lo, y_hi = max(0, math.floor(pcy - r_px)), min(canvas.height - 1, math.ceil(pcy + r_px))
    x_lo, x_hi = max(0, math.floor(pcx - r_px)), min(canvas.width - 1, math.ceil(pcx + r_px))
    for yi in range(y_lo, y_hi + 1):
        for xi in range(x_lo, x_hi + 1):
            nx, ny = (xi + 0.5 - pcx) / r_px, -(yi + 0.5 - pcy) / r_px
            rho2 = nx * nx + ny * ny
            if rho2 > 1:
                continue
            nz = math.sqrt(1 - rho2)
            lit = nx * sun[0] + ny * sun[1] + nz * sun[2]
            depth = pcd + nz * r_disp
            if lit > 0:
                canvas.dot(xi, yi, shade(color, 0.5 + 0.5 * lit), depth)
            elif (xi + yi) % 2 == 0 or rho2 > 0.85:
                canvas.dot(xi, yi, shade(color, 0.28), depth)


def _draw_sun_marker(canvas: Canvas, proj: Projector, sun_dir: Vec3, ascii_: bool) -> None:
    """Point at the Sun from the edge of the view."""
    right, up, toward = proj.direction(sun_dir)
    glyph = "*" if ascii_ else "☼"
    sun_c = (255, 220, 110)
    if math.hypot(right, up) < 0.2:
        canvas.text(1, canvas.rows - 1, f"{glyph} Sun {'behind you' if toward > 0 else 'behind planet'}", sun_c)
        return
    # Walk from the centre of the canvas (in cells) to its border along the Sun's screen direction.
    dx, dy = right, -up / 2  # cells are about twice as tall as wide
    cx, cy = canvas.cols / 2, canvas.rows / 2
    t = min((cx - 2) / abs(dx) if dx else math.inf, (cy - 1) / abs(dy) if dy else math.inf)
    col, row = int(cx + dx * t), int(cy + dy * t)
    label = "Sun"
    canvas.put(col, row, Cell(glyph, sun_c, bold=True))
    lc = col + 2 if dx < 0 else col - len(label) - 1
    canvas.text(max(0, min(canvas.cols - len(label), lc)), row, label, shade(sun_c, 0.85))
