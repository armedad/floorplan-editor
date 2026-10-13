"""Draw a plan in red over its source sheet, erasing ink the plan removed."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from .model import PlanError
from .render import Canvas, draw_png, scene

DRAWN_KEYS = ("walls", "openings", "counters", "fixtures")
OVERLAY_RED = (196, 0, 0, 220)
OVERLAY_WALL_FILL = (196, 0, 0, 110)


def apply_erase_regions(image: Image.Image, plan: dict) -> Image.Image:
    """Paint the plan's erase regions white on a copy of the sheet image."""
    regions = plan.get("erase", [])
    if not regions:
        return image
    canvas = Canvas(plan)
    out = image.copy()
    draw = ImageDraw.Draw(out)
    white = (255, 255, 255, 255) if out.mode == "RGBA" else (255, 255, 255)
    for region in regions:
        x1, y1 = canvas.pt(float(region["x"]), float(region["y"]))
        x2, y2 = canvas.pt(float(region["x"]) + float(region["w"]), float(region["y"]) + float(region["h"]))
        draw.rectangle([min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)], fill=white)
    return out


def removed_objects(original: dict, plan: dict) -> dict:
    """Objects in original that plan deleted or changed, as a plan of their own."""
    removed = {key: [] for key in DRAWN_KEYS}
    for key in DRAWN_KEYS:
        now = {item["id"]: item for item in plan.get(key, [])}
        for item in original.get(key, []):
            if now.get(item["id"]) != item:
                removed[key].append(dict(item))
    gone = {key: removed[key] for key in ("walls", "counters", "fixtures")}
    return {**plan, **gone, "openings": [], "labels": [], "erase": []}


def erase_removed(base: Image.Image, original: dict, plan: dict) -> None:
    """White out source ink of objects the plan removed or changed. Walls still in the plan are kept."""
    removed = removed_objects(original, plan)
    canvas = Canvas(plan)
    pad_in = 2 / canvas.scale
    for wall in removed["walls"]:
        wall["thickness"] = float(wall["thickness"]) + 2 * pad_in
    ops = scene(removed, ink=(255, 255, 255, 255), fill_walls=True)
    for op in ops:
        if op["op"] == "polygon":
            op["fill"] = (255, 255, 255, 255)
        if "width" in op:
            op["width"] = op["width"] + 4
    erase_mask = draw_png(removed, ops, (0, 0, 0, 0)).getchannel("A")

    walls_only = {**plan, "openings": [], "counters": [], "fixtures": [], "labels": []}
    keep_ops = [op for op in scene(walls_only, fill_walls=True) if op.get("role") == "wall"]
    for op in keep_ops:
        op["fill"] = op["stroke"] = (0, 0, 0, 255)
    keep_mask = draw_png(walls_only, keep_ops, (0, 0, 0, 0)).getchannel("A")

    mask = ImageChops.subtract(erase_mask, keep_mask)
    base.paste((255, 255, 255, 255), (0, 0), mask)


def overlay_image(plan: dict, source: Path, erase_from: dict | None = None) -> Image.Image:
    canvas = Canvas(plan)
    with Image.open(source) as raw:
        base = raw.convert("RGBA")
    if base.size != (canvas.width, canvas.height):
        raise PlanError(
            [f"source image is {base.size[0]}x{base.size[1]} and canvas is {canvas.width}x{canvas.height}"]
        )
    base = apply_erase_regions(base, plan)
    if erase_from is not None:
        erase_removed(base, erase_from, plan)
    ops = scene(plan, ink=OVERLAY_RED, fill_walls=False)
    for op in ops:
        if op["op"] == "polygon" and op.get("role") == "wall":
            op["fill"] = OVERLAY_WALL_FILL
        if op["op"] == "text":
            op["fill"] = (196, 0, 0, 255)
    layer = draw_png(plan, ops, (0, 0, 0, 0))
    return Image.alpha_composite(base, layer).convert("RGB")


def overlay_file(plan: dict, source: Path, out: Path, erase_from: dict | None = None) -> None:
    overlay_image(plan, source, erase_from).save(out, "PNG")
