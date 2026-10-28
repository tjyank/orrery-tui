"""Planetary system view: one planet, lit by the Sun, with its moons on their real orbits."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

from .bodies import Body
from .events import in_umbra
from .kepler import AU_KM, Orbit, Vec3
from .moons import Moon, pole_vector
from .render import _BRAILLE, BACKDROP, GRID, GRID_LABEL, Camera, Canvas, Cell, Projector, SceneOptions, _draw_label, shade

SYSTEM_EXTENT = 5.5  # display units from the planet to the outermost apoapsis at zoom 1
MIN_RING_GAP = 0.55  # display units between labelled distance rings
MAX_CACHED_DISC_PX = 120  # larger planet discs are shaded per frame, clipped to the canvas


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


def in_shadow(p_au: Vec3, sun_dir: Vec3, radius_km: float, sun_dist_au: float = 1e9) -> bool:
    """Is a planetocentric point inside the planet's umbra (the cone the event search uses)?"""
    x_km = (p_au[0] * AU_KM, p_au[1] * AU_KM, p_au[2] * AU_KM)
    planet_helio = (-sun_dir[0] * sun_dist_au, -sun_dir[1] * sun_dist_au, -sun_dir[2] * sun_dist_au)
    return in_umbra(x_km, planet_helio, radius_km)


@dataclass
class SystemScene:
    planet: Body
    moons: list[Moon]
    positions: dict[str, Vec3]  # planetocentric ecliptic J2000, au
    orbits: dict[str, Orbit]
    sun_dir: Vec3  # unit vector from the planet towards the Sun
    opts: SceneOptions = field(default_factory=SceneOptions)
    sun_dist_au: float = 1e9  # shapes the shadow cone; the default is effectively a cylinder
    disc: tuple[float, float, float] | None = None  # set by draw_system: planet centre (col, row), radius (cols)

    def __post_init__(self) -> None:
        self.reach_km = system_reach_km(self.planet, self.moons)
        self.scale = system_scale(self.opts.scale_mode, self.planet.radius_km, self.reach_km)

    def disp(self, p_au: Vec3) -> Vec3:
        return _disp(p_au, self.scale)

    def orbit_path(self, orbit: Orbit) -> tuple[Vec3, ...]:
        return _disp_path(orbit, self.opts.scale_mode, self.planet.radius_km, self.reach_km)


def _disp(p_au: Vec3, scale) -> Vec3:
    r = math.sqrt(_dot(p_au, p_au)) * AU_KM
    if r < 1e-9:
        return (0.0, 0.0, 0.0)
    f = scale(r) / r * AU_KM
    return (p_au[0] * f, p_au[1] * f, p_au[2] * f)


@lru_cache(maxsize=256)
def _disp_path(orbit: Orbit, mode: str, radius_km: float, reach_km: float) -> tuple[Vec3, ...]:
    scale = system_scale(mode, radius_km, reach_km)
    return tuple(_disp(p, scale) for p in orbit.path(240))


@lru_cache(maxsize=64)
def _ring(e1: Vec3, e2: Vec3, r: float, n: int = 160) -> tuple[Vec3, ...]:
    pts = []
    for k in range(n + 1):
        c, s = math.cos(math.tau * k / n) * r, math.sin(math.tau * k / n) * r
        pts.append((e1[0] * c + e2[0] * s, e1[1] * c + e2[1] * s, e1[2] * c + e2[2] * s))
    return tuple(pts)


def _fit_extent(pole: Vec3) -> tuple[float, float]:
    """Screen half-extents of the equatorial disc from the default isometric camera.

    Fitting to a fixed camera keeps the zoom steady while the user rotates the view.
    """
    right, up, _ = Projector(Camera(), 2, 2).direction(pole)
    ext = SYSTEM_EXTENT * 1.08
    return (ext * max(0.5, math.sqrt(1 - right**2)), ext * max(0.5, math.sqrt(1 - up**2)))


def draw_system(canvas: Canvas, cam: Camera, sc: SystemScene) -> dict[str, tuple[int, int]]:
    """Draw a planet and its moons; returns the canvas cell of each body shown (for mouse picking)."""
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
        _draw_rings(canvas, proj, sc, (e1, e2), plane_point, hidden, pcd)
        # The pole axis: its far end, where it pokes out behind the planet, is hidden like the rings.
        axis_len = 1.6 * r_disp
        ends = [proj(tuple(k * x for x in pole)) for k in (-axis_len, axis_len)]
        canvas.line(*ends, GRID, _behind(hidden, pcd, axis_len))
        nx, ny, nd = ends[1]
        if nd >= pcd - BEHIND_MARGIN * axis_len and canvas.free(int(nx) // 2, int(ny) // 4):
            canvas.text(int(nx) // 2, int(ny) // 4, "N", GRID_LABEL, depth=BACKDROP)

    if sc.opts.show_orbits:
        for moon in sc.moons:
            orbit = sc.orbits.get(moon.key)
            if orbit is None:
                continue
            k_front, k_back = (0.75, 0.38) if moon.key == sc.opts.selected else (0.5, 0.22)
            canvas.polyline(proj.many(sc.orbit_path(orbit)), shade(moon.color, k_front), hidden,
                            back=shade(moon.color, k_back), split=pcd)

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
        dark = in_shadow(p, sc.sun_dir, sc.planet.radius_km, sc.sun_dist_au)
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
    sc.disc = (pcx / 2, pcy / 4, r_px / 2)  # centre (col, row) and radius in columns, for picking
    hits = {sc.planet.key: (int(pcx) // 2, int(pcy) // 4)}
    hits.update({moon.key: (int(sx) // 2, int(sy) // 4) for (sx, sy, depth), _, _, moon in items
                 if not hidden(sx, sy, depth)})
    return hits


BEHIND_MARGIN = 0.15  # grid lines hide this far (× their own size) behind the planet's centre


def _behind(hidden, pcd: float, size: float):
    """`hidden`, extended to any grid point farther away than the planet's centre.

    The grid is only a reference: the parts of it that wrap round behind the planet just add
    clutter. The margin keeps a ring seen nearly face-on (no real "behind") whole.
    """
    cutoff = pcd - BEHIND_MARGIN * size
    return lambda x, y, d: d < cutoff or hidden(x, y, d)


def _draw_rings(canvas: Canvas, proj: Projector, sc: SystemScene, plane, plane_point, hidden, pcd: float) -> None:
    reach = system_reach_km(sc.planet, sc.moons)
    last = sc.scale(sc.planet.radius_km)
    # Label each ring near its point nearest the viewer, which is never among the hidden parts.
    front = max((math.tau * k / 32 for k in range(32)), key=lambda a: proj(plane_point(1.0, a))[2])
    for km in (m * 10**k for k in range(3, 9) for m in (1, 2, 5)):
        if km < 1.5 * sc.planet.radius_km or km > reach * 1.05:
            continue
        r = sc.scale(km)
        if r - last < MIN_RING_GAP:
            continue
        last = r
        canvas.polyline(proj.many(_ring(*plane, r)), GRID, _behind(hidden, pcd, r))
        label = f"{km / 1e6:g}M km" if km >= 1e6 else f"{km / 1e3:g}k km"
        lx, ly, _ = proj(plane_point(r, front - 0.35))
        col, row = int(lx) // 2 + 1, int(ly) // 4
        if 0 <= row < canvas.rows and all(
            canvas.cells[row][c] is None for c in range(col - 1, col + len(label) + 1) if 0 <= c < canvas.cols
        ):
            canvas.text(col, row, label, GRID_LABEL, depth=BACKDROP)


def _draw_planet(canvas: Canvas, proj: Projector, sc: SystemScene, centre, r_disp: float, r_px: float) -> None:
    """A braille-dot sphere: solid on the day side, a sparse dim stipple on the night side.

    A cell has one colour for all its dots, so an orbit crossing in front of the planet can't be
    drawn in its own colour without recolouring the planet's dots in that cell. Instead the planet
    keeps its colour and the orbit is cut out of it: wherever something in front already set a dot,
    the planet leaves that dot dark, so the orbit shows as a gap line across the surface.
    """
    pcx, pcy, pcd = centre
    color = sc.planet.color
    if r_px < 2:
        canvas.put(int(pcx) // 2, int(pcy) // 4, Cell("o" if sc.opts.ascii else "●", color, bold=True), pcd + 1e6)
        return
    sun = tuple(round(c, 2) for c in proj.direction(sc.sun_dir))
    ix, iy = math.floor(pcx), math.floor(pcy)
    if r_px > MAX_CACHED_DISC_PX:  # zoomed right in: shade just the on-screen pixels
        x0, y0 = max(0, math.floor(pcx - r_px)), max(0, math.floor(pcy - r_px))
        x1, y1 = min(canvas.width - 1, math.ceil(pcx + r_px)), min(canvas.height - 1, math.ceil(pcy + r_px))
        template = _disc_pixels(r_px, pcx - ix, pcy - iy, sun, color, (ix + iy) % 2,
                                (x0 - ix, x1 - ix, y0 - iy, y1 - iy))
    else:
        # The shaded disc only depends on its size, sub-pixel offset and the Sun's direction on
        # screen, so it is cached and re-used while the view is still (the usual case).
        template = _disc(round(r_px, 1), round(pcx - ix, 1), round(pcy - iy, 1), sun, color, (ix + iy) % 2)
    # Everything drawn so far that falls on the disc is in front of it: whatever was behind was
    # already dropped by `hidden`. So a dot that is already set marks where to cut the planet.
    bits, fgs, depths, w, h = canvas.bits, canvas.dot_fg, canvas.dot_depth, canvas.width, canvas.height
    for dx, dy, nz, c in template:
        xi, yi = ix + dx, iy + dy
        if 0 <= xi < w and 0 <= yi < h:
            row, col = yi >> 2, xi >> 1
            bit = _BRAILLE[yi & 3][xi & 1]
            if bits[row][col] & bit:
                bits[row][col] &= ~bit  # something in front: leave a gap in the planet
                continue
            bits[row][col] |= bit
            depths[row][col] = pcd + nz * r_disp
            fgs[row][col] = c  # the planet's colour wins the cell, gaps and all


@lru_cache(maxsize=32)
def _disc(r_px: float, fx: float, fy: float, sun: Vec3, color, parity: int):
    reach = math.ceil(r_px) + 1
    return _disc_pixels(r_px, fx, fy, sun, color, parity, (-reach, reach, -reach, reach))


def _disc_pixels(r_px: float, fx: float, fy: float, sun: Vec3, color, parity: int, box):
    """Pixels of a lit sphere relative to its centre's pixel, within `box` (dx0, dx1, dy0, dy1):
    (dx, dy, normal-z, colour). The night side is a sparse stipple with a solid rim."""
    out = []
    dx0, dx1, dy0, dy1 = box
    for dy in range(dy0, dy1 + 1):
        for dx in range(dx0, dx1 + 1):
            nx, ny = (dx + 0.5 - fx) / r_px, -(dy + 0.5 - fy) / r_px
            rho2 = nx * nx + ny * ny
            if rho2 > 1:
                continue
            nz = math.sqrt(1 - rho2)
            lit = nx * sun[0] + ny * sun[1] + nz * sun[2]
            if lit > 0:
                out.append((dx, dy, nz, shade(color, 0.5 + 0.5 * lit)))
            elif (dx + dy + parity) % 2 == 0 or rho2 > 0.85:
                out.append((dx, dy, nz, shade(color, 0.28)))
    return tuple(out)


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
