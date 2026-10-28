"""Remembering the view between runs: camera, scale, toggles and selection."""
from __future__ import annotations

import json
import os
from pathlib import Path


def settings_path() -> Path:
    if "ORRERY_SETTINGS" in os.environ:
        return Path(os.environ["ORRERY_SETTINGS"])
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "orrery" / "settings.json"
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "orrery" / "settings.json"


# What we store, and its type. Anything else in the file (or of the wrong type) is ignored, so a
# hand-edited or older settings file can never stop the app from starting.
SCHEMA: dict[str, type | tuple[type, ...]] = {
    "yaw": (int, float), "pitch": (int, float), "zoom": (int, float),
    "scale": str, "grid": bool, "orbits": bool, "drops": bool, "names": bool, "small": bool,
    "selected": str, "panel": bool, "spin": bool, "follow": bool,
    "sys_yaw": (int, float), "sys_pitch": (int, float), "sys_zoom": (int, float), "sys_scale": str,
    "sys_grid": bool, "sys_orbits": bool, "sys_drops": bool, "sys_names": bool,
}


def load(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or settings_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, kind in SCHEMA.items():
        value = data.get(key)
        # bool is an int subclass: don't let true/false through as a number, or 1/0 as a flag.
        if isinstance(value, kind) and (kind is bool) == isinstance(value, bool):
            out[key] = value
    return out


def save(values: dict, path: Path | None = None) -> None:
    path = path or settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({k: v for k, v in values.items() if k in SCHEMA}, indent=2), encoding="utf-8")
    except OSError:
        pass  # not being able to remember settings is no reason to fail
