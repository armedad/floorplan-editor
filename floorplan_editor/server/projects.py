"""Plan folders: where new ones go, which ones were used lately, and the native file picker.

New projects made from a dropped image go in the workspace (FLOORPLAN_WORKSPACE, default
~/floorplans), one folder per image. Recent folders are kept in FLOORPLAN_HOME
(default ~/.floorplan-editor).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from ..engine.model import load_plan
from ..engine.steps import IMAGE_SUFFIXES, find_sheet, list_plans

RECENT_LIMIT = 12


def workspace() -> Path:
    return Path(os.environ.get("FLOORPLAN_WORKSPACE") or Path.home() / "floorplans").expanduser()


def _home() -> Path:
    return Path(os.environ.get("FLOORPLAN_HOME") or Path.home() / ".floorplan-editor").expanduser()


def recent_folders() -> list[str]:
    try:
        items = json.loads((_home() / "recent.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [p for p in items if isinstance(p, str) and Path(p).is_dir()]


def remember(folder: Path) -> None:
    items = [str(folder)] + [p for p in recent_folders() if Path(p) != Path(folder)]
    try:
        _home().mkdir(parents=True, exist_ok=True)
        (_home() / "recent.json").write_text(json.dumps(items[:RECENT_LIMIT], indent=2), encoding="utf-8")
    except OSError:
        pass


def slug(text: str) -> str:
    return re.sub(r"[^\w.-]+", "-", text).strip("-.") or "plan"


def unique_folder(root: Path, stem: str) -> Path:
    base = root / slug(stem)
    candidate, n = base, 2
    while candidate.exists() and any(candidate.iterdir()):
        candidate = base.with_name(f"{base.name}-{n}")
        n += 1
    return candidate


def plan_size(path: Path) -> tuple[int, int] | None:
    try:
        canvas = load_plan(path)["canvas"]
        return int(round(canvas["width_px"])), int(round(canvas["height_px"]))
    except Exception:
        return None


def plans_matching(folder: Path, size: tuple[int, int]) -> list[str]:
    return [name for name in list_plans(folder) if plan_size(folder / name) == size]


def project_with_sheet(data: bytes) -> Path | None:
    """A workspace folder whose sheet image has exactly these bytes."""
    root = workspace()
    if not root.is_dir():
        return None
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        sheet = find_sheet(folder)
        if sheet and sheet.stat().st_size == len(data) and sheet.read_bytes() == data and list_plans(folder):
            return folder
    return None


def is_image(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_SUFFIXES


class PickerUnavailable(RuntimeError):
    pass


_PICKER = r"""
import sys
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
root.update()
kind, initial = sys.argv[1], sys.argv[2]
if kind == "folder":
    path = filedialog.askdirectory(parent=root, initialdir=initial, title="Open a plan folder")
else:
    path = filedialog.askopenfilename(
        parent=root,
        initialdir=initial,
        title="Open a plan or a floor plan image",
        filetypes=[("Plans and floor plan images", "*.json *.png *.jpg *.jpeg"), ("All files", "*.*")],
    )
print(path or "")
"""


def native_pick(kind: str, initial: str) -> str | None:
    """Show the system's open dialog on this machine. Runs in its own process so Tk never touches the server's threads."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", _PICKER, kind, initial],
            capture_output=True,
            text=True,
            timeout=900,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PickerUnavailable(str(exc)) from exc
    if result.returncode != 0:
        raise PickerUnavailable(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "the file dialog failed")
    return result.stdout.strip() or None
