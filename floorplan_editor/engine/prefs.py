"""Editor preferences for this computer, kept beside the color settings.

The default save folder is where Save as… starts. It is not stored in a plan.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def prefs_path() -> Path:
    root = Path(os.environ.get("FLOORPLAN_HOME") or Path.home() / ".floorplan-editor").expanduser()
    return root / "editor.json"


def load_prefs() -> dict:
    path = prefs_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"save_folder": ""}
    folder = raw.get("save_folder") if isinstance(raw, dict) else ""
    return {"save_folder": folder.strip() if isinstance(folder, str) else ""}


def save_prefs(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("preferences must be an object")
    folder = raw.get("save_folder", "")
    if not isinstance(folder, str):
        raise ValueError("save folder must be a path")
    data = {"save_folder": folder.strip().strip('"')}
    path = prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data
