"""A plan folder: the traced plan.json, numbered step files, a merged plan, and the source sheet.

Every save writes the plan's JSON, SVG, PNG, and (when the sheet matches the canvas)
its overlay, so a step always has the same four files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .building import export_image, floor_by_id, merge_any, overlay_stack
from .model import PlanError, checked, is_building, load_plan, save_plan
from .overlay import overlay_file
from .render import Canvas, render_files

BASE_NAME = "plan.json"
MERGED_NAME = "plan-merged.json"
STEP_RE = re.compile(r"^plan-step(\d+)\.json$")
PLAN_RE = re.compile(r"^plan(-[\w.-]+)?\.json$")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")


def _order(name: str) -> tuple[int, int, str]:
    if name == BASE_NAME:
        return (0, 0, name)
    match = STEP_RE.match(name)
    if match:
        return (1, int(match.group(1)), name)
    if name == MERGED_NAME:
        return (3, 0, name)
    return (2, 0, name)


def list_plans(folder: Path) -> list[str]:
    names = [p.name for p in Path(folder).glob("plan*.json") if PLAN_RE.match(p.name)]
    return sorted(names, key=_order)


def newest_plan(folder: Path) -> str | None:
    """The plan a folder opens to: its newest step, else plan.json, else whatever plan is there."""
    plans = list_plans(folder)
    steps = [name for name in plans if STEP_RE.match(name)]
    if steps:
        return steps[-1]
    if BASE_NAME in plans:
        return BASE_NAME
    return plans[-1] if plans else None


def next_step_name(folder: Path) -> str:
    numbers = [int(m.group(1)) for name in list_plans(folder) if (m := STEP_RE.match(name))]
    return f"plan-step{max(numbers, default=0) + 1}.json"


def find_sheet(folder: Path, explicit: Path | None = None) -> Path | None:
    if explicit:
        return Path(explicit)
    images = [
        p
        for p in sorted(Path(folder).iterdir())
        if p.suffix.lower() in IMAGE_SUFFIXES
        and not p.name.startswith(("plan", "_"))
        and p.is_file()
    ]
    preferred = [p for p in images if p.stem.lower().startswith("sheet")]
    return (preferred or images or [None])[0]


@dataclass
class SaveResult:
    files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def write_outputs(plan: dict, path: Path, sheet: Path | None, base: dict | None) -> SaveResult:
    """Save plan JSON plus .svg, .png, and -overlay.png next to it. A building gets its floors
    stacked in the .png and -overlay.png, top floor first, and no .svg."""
    checked(plan)
    path = Path(path)
    result = SaveResult()
    save_plan(plan, path)
    result.files.append(path.name)
    if is_building(plan):
        png = path.with_suffix(".png")
        export_image(plan).save(png, "PNG")
        result.files.append(png.name)
        overlay, warnings = overlay_stack(plan, path.parent, base if path.name != BASE_NAME else None)
        if overlay is not None:
            out = path.with_name(f"{path.stem}-overlay.png")
            overlay.save(out, "PNG")
            result.files.append(out.name)
        result.warnings += warnings
        return result
    svg, png = path.with_suffix(".svg"), path.with_suffix(".png")
    render_files(plan, svg, png)
    result.files += [svg.name, png.name]
    if sheet and Path(sheet).exists():
        with Image.open(sheet) as image:
            size = image.size
        canvas = Canvas(plan)
        if size == (canvas.width, canvas.height):
            out = path.with_name(f"{path.stem}-overlay.png")
            erase_from = floor_by_id(base, plan.get("id")) if path.name != BASE_NAME else None
            overlay_file(plan, Path(sheet), out, erase_from=erase_from)
            result.files.append(out.name)
        else:
            result.warnings.append(f"sheet is {size[0]}x{size[1]}, canvas is {canvas.width}x{canvas.height}: no overlay")
    else:
        result.warnings.append("no sheet image: no overlay")
    return result


def load_base(folder: Path) -> dict | None:
    path = Path(folder) / BASE_NAME
    if not path.exists():
        return None
    try:
        return checked(load_plan(path))
    except PlanError:
        return None


def merge_folder(folder: Path, base_name: str, step_names: list[str], sheet: Path | None, out_name: str = MERGED_NAME):
    folder = Path(folder)
    base = checked(load_plan(folder / base_name))
    steps = [(name, checked(load_plan(folder / name))) for name in step_names]
    merged = merge_any(base, steps)
    merged["name"] = merged.get("name", "merged")
    merged["notes"] = "Merged from " + ", ".join(step_names) + f" on base {base_name}."
    result = write_outputs(merged, folder / out_name, sheet, load_base(folder))
    return merged, result
