"""Shared look and feel: colours, simulation speeds, the key legend, formatting helpers."""
from __future__ import annotations

import math

from .events import Event
from .kepler import DAY_S, Vec3


C_KM_S = 299_792.458
PANEL_W = 34
MIN_PANEL_COLS = 100
FLASH_S = 4  # how long transient footer messages stay up
JUMP_RESET_DAYS = 45  # after a jump this far, switch to the offline model at once while Horizons loads
EVENT_ALERT_MAX_RATE = 3_600  # announce events as they begin only at up to 1 hr/s (any faster is a blur)
DOUBLE_CLICK_S = 0.5  # Windows' default double-click time

# Simulated seconds per real second.
RATES = [-31_557_600, -2_592_000, -604_800, -86_400, -3_600, -60, -1, 1, 60, 3_600, 86_400, 604_800, 2_592_000, 31_557_600]
REALTIME = RATES.index(1)
RATE_NAMES = {1: "real time", 60: "1 min/s", 3_600: "1 hr/s", 86_400: "1 day/s", 604_800: "1 wk/s",
              2_592_000: "30 days/s", 31_557_600: "1 yr/s"}

FG = (210, 214, 224)
DIM = (120, 126, 142)
ACCENT = (255, 200, 90)
STATUS_COLORS = {"live": (110, 220, 130), "cache": (240, 200, 90), "offline": (240, 120, 100),
                 "analytic": (140, 200, 255)}

# The ? legend, by section. Keep it in step with App.handle and handle_mouse.
KEY_LEGEND = [
    ("Camera", [
        ("← → / h l", "rotate"),
        ("↑ ↓ / k j", "tilt"),
        ("+ -", "zoom"),
        ("i", "isometric view"),
        ("a", "auto-spin"),
        ("m", "distance scale"),
    ]),
    ("Bodies", [
        ("Tab Shift-Tab", "select next / previous"),
        ("f", "follow selected"),
        ("Enter / v", "open a planet's moons"),
        ("b", "small bodies on/off"),
    ]),
    ("Moon view", [
        ("Tab", "cycle moons"),
        ("[ ]", "previous / next planet"),
        ("Esc Enter v", "back to solar system"),
    ]),
    ("Time", [
        (", .", "slower / faster, reverse"),
        ("space", "pause"),
        ("t", "now, at real time"),
        ("J", "jump to a date or event"),
    ]),
    ("Display", [
        ("o", "orbits: all, selected, none"),
        ("d n", "drop lines, names"),
        ("p", "info panel"),
        ("?", "this legend"),
    ]),
    ("Mouse", [
        ("drag", "rotate and tilt"),
        ("scroll", "zoom"),
        ("click", "select"),
        ("double-click", "open moons / follow"),
        ("right-click", "back"),
    ]),
    ("Other", [
        ("r", "refetch from Horizons"),
        ("q", "quit"),
    ]),
]


def vec_sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vec_len(a: Vec3) -> float:
    return math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)


def vec_angle(a: Vec3, b: Vec3) -> float:
    c = (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / (vec_len(a) * vec_len(b))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def unit_vector(a: Vec3) -> Vec3:
    n = vec_len(a)
    return (a[0] / n, a[1] / n, a[2] / n)


def format_period(days: float) -> str:
    return f"{days * 24:.2f} h" if days < 2 else f"{days:.3f} d" if days < 100 else f"{days / 365.25:.3f} yr"


def countdown(days: float) -> str:
    """Compact time until something: '45s', '12m', '2h14m', '3d4h'."""
    s = days * DAY_S
    if s < 60:
        return f"{max(s, 0):.0f}s"
    if s < 3600:
        return f"{s / 60:.0f}m"
    if s < 86400:
        return f"{int(s // 3600)}h{int(s % 3600 // 60):02d}m"
    return f"{int(s // 86400)}d{int(s % 86400 // 3600)}h"


def short_event(ev: Event) -> str:
    return {"eclipse": f"{ev.name} eclipsed", "transit": f"{ev.name} transit",
            "occultation": f"{ev.name} occulted"}.get(ev.kind, ev.name)
