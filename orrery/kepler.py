"""Two-body orbital mechanics: osculating elements <-> state vectors, propagation."""
from __future__ import annotations

import math
from dataclasses import dataclass

GM_SUN = 2.9591220828559115e-04  # au^3 / day^2 (Gaussian constant squared)
AU_KM = 149_597_870.7
DAY_S = 86_400.0

Vec3 = tuple[float, float, float]


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def solve_kepler(m: float, e: float) -> float:
    """Eccentric anomaly E for mean anomaly M (radians), elliptic orbits."""
    m = math.remainder(m, math.tau)
    ecc = m + e * math.sin(m) if e < 0.8 else math.pi * (1 if m >= 0 else -1)
    for _ in range(50):
        f = ecc - e * math.sin(ecc) - m
        step = f / (1 - e * math.cos(ecc))
        ecc -= step
        if abs(step) < 1e-13:
            break
    return ecc


@dataclass(frozen=True)
class Orbit:
    """Heliocentric ecliptic (J2000) osculating orbit. Angles in radians, a in au."""

    a: float
    e: float
    i: float
    node: float
    argp: float
    m0: float
    epoch: float  # Julian date (TDB) at which m0 applies
    mu: float = GM_SUN

    @property
    def mean_motion(self) -> float:  # rad / day
        return math.sqrt(self.mu / self.a**3)

    @property
    def period_days(self) -> float:
        return math.tau / self.mean_motion

    def _rotate(self, xp: float, yp: float) -> Vec3:
        cO, sO = math.cos(self.node), math.sin(self.node)
        cw, sw = math.cos(self.argp), math.sin(self.argp)
        ci, si = math.cos(self.i), math.sin(self.i)
        x = (cO * cw - sO * sw * ci) * xp + (-cO * sw - sO * cw * ci) * yp
        y = (sO * cw + cO * sw * ci) * xp + (-sO * sw + cO * cw * ci) * yp
        z = (sw * si) * xp + (cw * si) * yp
        return (x, y, z)

    def _perifocal(self, ecc_anom: float) -> tuple[float, float]:
        return (
            self.a * (math.cos(ecc_anom) - self.e),
            self.a * math.sqrt(1 - self.e**2) * math.sin(ecc_anom),
        )

    def position(self, jd: float) -> Vec3:
        m = self.m0 + self.mean_motion * (jd - self.epoch)
        return self._rotate(*self._perifocal(solve_kepler(m, self.e)))

    def state(self, jd: float) -> tuple[Vec3, Vec3]:
        """Position (au) and velocity (au/day)."""
        m = self.m0 + self.mean_motion * (jd - self.epoch)
        ecc = solve_kepler(m, self.e)
        xp, yp = self._perifocal(ecc)
        edot = self.mean_motion / (1 - self.e * math.cos(ecc))
        vxp = -self.a * math.sin(ecc) * edot
        vyp = self.a * math.sqrt(1 - self.e**2) * math.cos(ecc) * edot
        return self._rotate(xp, yp), self._rotate(vxp, vyp)

    def path(self, samples: int = 180) -> list[Vec3]:
        """Closed polyline of the orbit, sampled uniformly in eccentric anomaly."""
        return [
            self._rotate(*self._perifocal(math.tau * k / samples)) for k in range(samples + 1)
        ]


def orbit_from_state(r: Vec3, v: Vec3, epoch: float, mu: float = GM_SUN) -> Orbit:
    """Osculating elements from a heliocentric ecliptic state vector (au, au/day)."""
    rn, v2 = _norm(r), _dot(v, v)
    h = _cross(r, v)
    hn = _norm(h)
    rv = _dot(r, v)
    evec = tuple(((v2 - mu / rn) * r[k] - rv * v[k]) / mu for k in range(3))
    e = _norm(evec)
    energy = v2 / 2 - mu / rn
    if energy >= 0:
        raise ValueError("orbit is not bound (e >= 1)")
    a = -mu / (2 * energy)
    i = math.acos(max(-1.0, min(1.0, h[2] / hn)))

    n = (-h[1], h[0], 0.0)
    if _norm(n) < 1e-14 * hn:  # equatorial: node undefined, measure from +x
        node = 0.0
        argp = math.atan2(evec[1], evec[0]) * (1 if h[2] >= 0 else -1)
    else:
        node = math.atan2(h[0], -h[1])
        argp = math.atan2(_dot(_cross(n, evec), h) / hn, _dot(n, evec))
    nu = math.atan2(_dot(_cross(evec, r), h) / hn, _dot(evec, r))
    ecc = math.atan2(math.sqrt(1 - e * e) * math.sin(nu), e + math.cos(nu))
    m0 = ecc - e * math.sin(ecc)
    return Orbit(a, e, i, node % math.tau, argp % math.tau, m0, epoch, mu)
