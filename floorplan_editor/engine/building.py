"""Buildings: one plan file holding several floors.

Each floor is a complete plan (canvas, compass, walls, ...) plus an `id` and the name of its
own sheet image in the plan's folder. A file without `floors` is a one-floor plan, as before.
Floors are listed bottom to top; pictures of the whole building put the top floor first.
"""

from __future__ import annotations

import copy
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from .merge import merge_plans
from .model import PlanError, is_building
from .overlay import overlay_image
from .render import _font, draw_png, scene

FIRST_FLOOR = "floor-1"
EXPORT_WIDTH_PX = 2400
EXPORT_PPI = (0.5, 4.0)
WHITE = (255, 255, 255, 255)


def floors_of(data: dict | None) -> list[dict]:
    if not data:
        return []
    return list(data["floors"]) if is_building(data) else [data]


def floor_label(floor: dict, index: int) -> str:
    return str(floor.get("name") or f"Floor {index + 1}")


def next_floor_id(data: dict | None) -> str:
    taken = {f.get("id") for f in floors_of(data)}
    n = 1
    while f"floor-{n}" in taken:
        n += 1
    return f"floor-{n}"


def as_building(data: dict, *, name: str | None = None, sheet: str | None = None) -> dict:
    """The file as a building. A one-floor plan becomes its first floor, keeping its name."""
    if is_building(data):
        return copy.deepcopy(data)
    floor = copy.deepcopy(data)
    floor.setdefault("id", FIRST_FLOOR)
    if sheet and not floor.get("sheet"):
        floor["sheet"] = sheet
    building = {"name": name or floor.get("name") or "Building", "floors": [floor]}
    return building


def floor_by_id(data: dict | None, floor_id: str | None) -> dict | None:
    if not data:
        return None
    if not is_building(data):
        return data if floor_id in (None, FIRST_FLOOR) else None
    return next((f for f in data["floors"] if f.get("id") == floor_id), None)


# ---------------------------------------------------------------- pictures of the whole building


def scaled(plan: dict, px_per_inch: float) -> dict:
    """The same plan drawn at another scale: same inches, a bigger or smaller canvas."""
    out = copy.deepcopy(plan)
    canvas = out["canvas"]
    k = px_per_inch / float(canvas["px_per_inch"])
    canvas["width_px"] = max(1.0, float(canvas["width_px"]) * k)
    canvas["height_px"] = max(1.0, float(canvas["height_px"]) * k)
    canvas["origin_px"] = [float(canvas["origin_px"][0]) * k, float(canvas["origin_px"][1]) * k]
    canvas["px_per_inch"] = px_per_inch
    for label in out.get("labels", []):
        label["size_px"] = float(label.get("size_px", 16)) * k
    return out


def width_inches(plan: dict) -> float:
    canvas = plan["canvas"]
    return float(canvas["width_px"]) / float(canvas["px_per_inch"])


def export_ppi(floors: list[dict]) -> float:
    """One scale for every floor, so the widest sheet comes out about EXPORT_WIDTH_PX wide."""
    widest = max(width_inches(f) for f in floors)
    return max(EXPORT_PPI[0], min(EXPORT_PPI[1], EXPORT_WIDTH_PX / widest))


def _crop_to_ink(image: Image.Image, pad: int) -> Image.Image:
    box = ImageChops.difference(image.convert("RGB"), Image.new("RGB", image.size, WHITE[:3])).getbbox()
    if not box:
        return image
    x0, y0, x1, y1 = box
    return image.crop((max(0, x0 - pad), max(0, y0 - pad), min(image.width, x1 + pad), min(image.height, y1 + pad)))


def clean_floor(plan: dict, px_per_inch: float) -> Image.Image:
    """One floor drawn on white at the given scale, cropped to what is drawn."""
    big = scaled(plan, px_per_inch)
    ops = scene(big)
    line_scale = max(1.0, px_per_inch / 1.2)
    for op in ops:
        if op["op"] in ("line", "arc"):
            op["width"] = op["width"] * line_scale
    image = draw_png(big, ops, WHITE).convert("RGB")
    return _crop_to_ink(image, pad=int(px_per_inch * 12))


def stack(tiles: list[tuple[str, Image.Image]], title: str | None = None) -> Image.Image:
    """Pictures in one column, each with its caption above it, centered on white."""
    widest = max(image.width for _, image in tiles)
    caption = max(18, min(56, widest // 40))
    heading = int(caption * 1.4)
    gap = caption * 2
    margin = caption
    width = widest + 2 * margin
    height = margin + (heading + gap if title else 0)
    height += sum(caption + caption // 2 + image.height + gap for _, image in tiles)
    sheet = Image.new("RGB", (width, height), WHITE[:3])
    draw = ImageDraw.Draw(sheet)
    y = margin
    if title:
        draw.text((width / 2, y + heading / 2), title, fill=(0, 0, 0), font=_font(heading), anchor="mm")
        y += heading + gap
    for text, image in tiles:
        draw.text((width / 2, y + caption / 2), text, fill=(0, 0, 0), font=_font(caption), anchor="mm")
        y += caption + caption // 2
        sheet.paste(image, ((width - image.width) // 2, y))
        y += image.height + gap
    return sheet.crop((0, 0, width, y - gap + margin))


def top_first(data: dict) -> list[tuple[str, dict]]:
    floors = floors_of(data)
    return [(floor_label(f, i), f) for i, f in reversed(list(enumerate(floors)))]


def export_image(data: dict) -> Image.Image:
    """Every floor drawn clean at one scale, top floor first, captioned with its name."""
    floors = floors_of(data)
    ppi = export_ppi(floors)
    tiles = [(label, clean_floor(floor, ppi)) for label, floor in top_first(data)]
    title = data.get("name") if is_building(data) else None
    return stack(tiles, title)


def overlay_stack(data: dict, folder: Path, base: dict | None) -> tuple[Image.Image | None, list[str]]:
    """Each floor drawn over its own sheet, brought to one scale and stacked top floor first."""
    floors = floors_of(data)
    ppi = max(float(f["canvas"]["px_per_inch"]) for f in floors)
    tiles, warnings = [], []
    for label, floor in top_first(data):
        sheet = Path(folder) / floor["sheet"] if floor.get("sheet") else None
        if not sheet or not sheet.exists():
            warnings.append(f"{label} has no sheet image: no overlay")
            continue
        try:
            image = overlay_image(floor, sheet, floor_by_id(base, floor.get("id")))
        except PlanError as exc:
            warnings.append(f"{label}: {exc}")
            continue
        k = ppi / float(floor["canvas"]["px_per_inch"])
        if abs(k - 1) > 1e-6:
            image = image.resize((max(1, round(image.width * k)), max(1, round(image.height * k))), Image.LANCZOS)
        tiles.append((label, image))
    return (stack(tiles, data.get("name")) if tiles else None), warnings


# ---------------------------------------------------------------- merging steps of a building

FLOOR_META = ("name", "notes", "compass", "canvas", "sheet", "units")


def merge_buildings(base: dict, steps: list[tuple[str, dict]]) -> dict:
    """Merge floor by floor, matched by floor id. A step can add floors, drop them, or change a
    floor's name, scale, or sheet; two steps doing that differently to one floor is a conflict."""
    base_b = as_building(base)
    step_bs = [(name, as_building(step)) for name, step in steps]
    base_floors = {f["id"]: f for f in base_b["floors"]}
    order = [f["id"] for f in base_b["floors"]]
    errors: list[str] = []
    added: dict[str, tuple[str, dict]] = {}
    dropped: dict[str, str] = {}
    changes: dict[str, list[tuple[str, dict]]] = {fid: [] for fid in base_floors}
    for name, step in step_bs:
        step_floors = {f["id"]: f for f in step["floors"]}
        for fid in base_floors.keys() - step_floors.keys():
            dropped.setdefault(fid, name)
        for fid, floor in step_floors.items():
            if fid in base_floors:
                if floor != base_floors[fid]:
                    changes[fid].append((name, floor))
                continue
            if fid in added and added[fid][1] != floor:
                errors.append(f"floor {fid}: {added[fid][0]} and {name} add it differently")
                continue
            if fid not in added:
                added[fid] = (name, floor)
                order.append(fid)

    merged_floors: dict[str, dict] = {}
    for fid, floor in base_floors.items():
        edits = changes[fid]
        if fid in dropped:
            if edits:
                errors.append(f"floor {fid}: {dropped[fid]} removes it, but {edits[0][0]} changes it")
            continue
        try:
            merged = merge_plans(floor, edits) if edits else copy.deepcopy(floor)
        except PlanError as exc:
            errors += [f"floor {fid}: {e}" for e in exc.errors]
            continue
        owner: dict[str, str] = {}
        for name, step_floor in edits:
            for key in FLOOR_META:
                if step_floor.get(key) == floor.get(key):
                    continue
                if key in owner and merged.get(key) != step_floor.get(key):
                    errors.append(f"floor {fid} {key}: {owner[key]} and {name} change it differently")
                    continue
                owner[key] = name
                if key in step_floor:
                    merged[key] = copy.deepcopy(step_floor[key])
                else:
                    merged.pop(key, None)
        merged_floors[fid] = merged
    for fid, (_, floor) in added.items():
        merged_floors[fid] = copy.deepcopy(floor)
    out = {k: v for k, v in base_b.items() if k != "floors"}
    if is_building(base):
        owner = {}
        for name, step in step_bs:
            for key in ("name", "notes"):
                if step.get(key) == base_b.get(key):
                    continue
                if key in owner and out.get(key) != step.get(key):
                    errors.append(f"building {key}: {owner[key]} and {name} change it differently")
                    continue
                owner[key] = name
                if key in step:
                    out[key] = step[key]
                else:
                    out.pop(key, None)
    if errors:
        raise PlanError(errors)
    out["floors"] = [merged_floors[fid] for fid in order if fid in merged_floors]
    return out


def merge_any(base: dict, steps: list[tuple[str, dict]]) -> dict:
    if not is_building(base) and not any(is_building(step) for _, step in steps):
        return merge_plans(base, steps)
    return merge_buildings(base, steps)
