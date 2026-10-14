"""Which kinds of object share a color, and the color each group starts with.

Group membership and the default colors are an editor preference on this computer, so every plan
opened here uses them. A plan can override a group's color; that override is stored on the plan
and travels with the file. Another computer uses its own groups, then applies those overrides
where the group id matches.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

KINDS = ("walls", "doors", "windows", "counters", "fixtures", "labels", "measures")

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

# Darker than the fixture blue (#1e88e5), and only used for the editor's selection highlight.
DEFAULT_SELECTED = "#0d47a1"


def is_color(value) -> bool:
    return isinstance(value, str) and bool(HEX_COLOR.match(value))


def default_appearance() -> dict:
    return {
        "selected": DEFAULT_SELECTED,
        "groups": [
            {"id": "walls", "name": "Walls", "color": "#c40000", "kinds": ["walls"]},
            {"id": "openings", "name": "Openings", "color": "#ef9a9a", "kinds": ["doors", "windows"]},
            {"id": "counters", "name": "Counters", "color": "#424242", "kinds": ["counters"]},
            {"id": "fixtures", "name": "Fixtures", "color": "#1e88e5", "kinds": ["fixtures"]},
            {"id": "labels", "name": "Labels", "color": "#000000", "kinds": ["labels"]},
            {"id": "measures", "name": "Measurements", "color": "#ef6c00", "kinds": ["measures"]},
        ],
    }


def prefs_path() -> Path:
    root = Path(os.environ.get("FLOORPLAN_HOME") or Path.home() / ".floorplan-editor").expanduser()
    return root / "appearance.json"


def load_appearance() -> dict:
    path = prefs_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_appearance()
    return normalize_appearance(raw) or default_appearance()


def save_appearance(raw: dict) -> dict:
    appearance = normalize_appearance(raw)
    if appearance is None:
        raise ValueError("appearance must give every kind its own group, with a #rrggbb color")
    path = prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(appearance, indent=2) + "\n", encoding="utf-8")
    return appearance


def normalize_appearance(raw) -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("groups"), list) or not raw["groups"]:
        return None
    seen: list[str] = []
    ids: set[str] = set()
    groups = []
    for group in raw["groups"]:
        if not isinstance(group, dict):
            return None
        gid, name, color, kinds = group.get("id"), group.get("name"), group.get("color"), group.get("kinds")
        if not isinstance(gid, str) or not gid.strip() or gid in ids:
            return None
        if not isinstance(name, str) or not name.strip():
            return None
        if not is_color(color) or not isinstance(kinds, list):
            return None
        if any(not isinstance(kind, str) or kind not in KINDS or kind in seen for kind in kinds):
            return None
        ids.add(gid)
        seen.extend(kinds)
        groups.append({"id": gid, "name": name.strip(), "color": color.lower(), "kinds": list(kinds)})
    if set(seen) != set(KINDS):
        return None
    selected = raw.get("selected") or DEFAULT_SELECTED
    if not is_color(selected):
        return None
    return {"selected": selected.lower(), "groups": groups}


def palette(plan: dict | None, appearance: dict | None = None) -> dict[str, str]:
    """Kind to the color drawn for it: the plan's override of that kind's group, else the editor default."""
    appearance = appearance if appearance is not None else default_appearance()
    overrides = plan.get("colors") if isinstance(plan, dict) else None
    if not isinstance(overrides, dict):
        overrides = {}
    out: dict[str, str] = {}
    for group in appearance["groups"]:
        override = overrides.get(group["id"])
        color = override.lower() if is_color(override) else group["color"]
        for kind in group["kinds"]:
            out[kind] = color
    return out


def rgba(hex_color: str, alpha: int) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)
