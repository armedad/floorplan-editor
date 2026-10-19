"""Plan file schema: load, save, and validate.

Coordinates are inches. X increases right. Y increases downward, same as the source image.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .geometry import wall_length

CARDINALS = ("north", "east", "south", "west")
SIDES = ("up", "right", "down", "left")
SIDE_VEC = {"up": (0, -1), "right": (1, 0), "down": (0, 1), "left": (-1, 0)}
# FF&E: furniture, fixtures, and equipment. Stored under plan.fixtures; category is by type.
FIXTURE_CATEGORIES = ("fixtures", "furniture", "equipment")

FIXTURE_TYPES = (
    # fixtures
    "toilet",
    "tub",
    "soaking_tub",
    "shower",
    "sink",
    "fireplace",
    "cabinet",
    "upper_cabinet",
    "generic",
    # furniture
    "stool",
    "bed",
    "sofa",
    "loveseat",
    "armchair",
    "dining_chair",
    "coffee_table",
    "dining_table",
    "nightstand",
    "dresser",
    "desk",
    "bookshelf",
    "tv_stand",
    "ottoman",
    "bench",
    "aquarium",
    # equipment
    "refrigerator",
    "range",
    "oven",
    "dishwasher",
    "washer",
    "dryer",
    "washer_dryer",
    "water_heater",
    "hvac",
    "furnace",
    "computer",
    "exercise_equipment",
    "tv_monitor",
    "electrical_panel",
    "server_rack",
)

FIXTURE_CATEGORY = {
    "toilet": "fixtures",
    "tub": "fixtures",
    "soaking_tub": "fixtures",
    "shower": "fixtures",
    "sink": "fixtures",
    "fireplace": "fixtures",
    "cabinet": "fixtures",
    "upper_cabinet": "fixtures",
    "generic": "fixtures",
    "stool": "furniture",
    "bed": "furniture",
    "sofa": "furniture",
    "loveseat": "furniture",
    "armchair": "furniture",
    "dining_chair": "furniture",
    "coffee_table": "furniture",
    "dining_table": "furniture",
    "nightstand": "furniture",
    "dresser": "furniture",
    "desk": "furniture",
    "bookshelf": "furniture",
    "tv_stand": "furniture",
    "ottoman": "furniture",
    "bench": "furniture",
    "aquarium": "furniture",
    "refrigerator": "equipment",
    "range": "equipment",
    "oven": "equipment",
    "dishwasher": "equipment",
    "washer": "equipment",
    "dryer": "equipment",
    "washer_dryer": "equipment",
    "water_heater": "equipment",
    "hvac": "equipment",
    "furnace": "equipment",
    "computer": "equipment",
    "exercise_equipment": "equipment",
    "tv_monitor": "equipment",
    "electrical_panel": "equipment",
    "server_rack": "equipment",
}

FIXTURE_LABELS = {
    "refrigerator": "REF",
    "oven": "OV",
    "dishwasher": "DW",
    "washer": "W",
    "dryer": "D",
    "washer_dryer": "W/D",
    "upper_cabinet": "UPPER",
    "water_heater": "WH",
    "hvac": "HVAC",
    "furnace": "FURN",
    "computer": "PC",
    "exercise_equipment": "EXER",
    "tv_monitor": "TV",
    "electrical_panel": "PANEL",
    "server_rack": "RACK",
    "dining_chair": "CHAIR",
    "coffee_table": "TABLE",
    "dining_table": "TABLE",
    "tv_stand": "TV ST",
}

# Fixed swatches for a per-object color override. Default means the Settings color for that kind.
OBJECT_COLORS = (
    "#1a1a1a",
    "#ffffff",
    "#bdbdbd",
    "#616161",
    "#c62828",
    "#ef6c00",
    "#f9a825",
    "#2e7d32",
    "#1565c0",
    "#6a1b9a",
    "#6d4c41",
    "#00897b",
)
OBJECT_COLOR_SET = {c.lower() for c in OBJECT_COLORS}


def fixture_category(fixture_type: str) -> str:
    return FIXTURE_CATEGORY.get(fixture_type, "fixtures")


def fixture_catalog() -> list[dict]:
    """Types an agent or the UI can place, with the FF&E group each belongs to."""
    return [{"id": t, "name": t.replace("_", " "), "category": fixture_category(t)} for t in FIXTURE_TYPES]


def is_object_color(value) -> bool:
    return isinstance(value, str) and value.lower() in OBJECT_COLOR_SET
WALL_KINDS = ("glass_rail",)
ID_KEYS = ("walls", "openings", "counters", "fixtures", "erase", "measures")
LIST_KEYS = ID_KEYS + ("labels",)
COMMENTED = {"comments"}


class PlanError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


def load_plan(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PlanError([f"{path}: {exc}"]) from exc
    if not isinstance(data, dict):
        raise PlanError([f"{path}: top level must be an object"])
    return data


def save_plan(plan: dict, path: Path) -> None:
    Path(path).write_text(json.dumps(plan, indent=2), encoding="utf-8")


def checked(plan: dict) -> dict:
    errors = validate_plan(plan)
    if errors:
        raise PlanError(errors)
    return plan


def _num(value, path: str, errors: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{path} must be a number")
        return None
    if not math.isfinite(value):
        errors.append(f"{path} must be finite")
        return None
    return float(value)


def _str(value, path: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} must be a non-empty string")
        return None
    return value


def _fields(obj: dict, required: set[str], optional: set[str], path: str, errors: list[str]) -> None:
    missing = required - obj.keys()
    extra = set(obj) - required - optional
    for key in sorted(missing):
        errors.append(f"{path}.{key} is required")
    for key in sorted(extra):
        errors.append(f"{path}.{key} is not a field")


def _object_color(obj: dict, path: str, errors: list[str]) -> None:
    if "color" not in obj:
        return
    if not is_object_color(obj.get("color")):
        errors.append(f"{path}.color must be one of the palette colors")
    else:
        obj["color"] = obj["color"].lower()


def comments_of(plan: dict):
    """Every comment in the plan as (kind, ref, object, comment); ref is a label's index."""
    for kind in LIST_KEYS:
        for index, obj in enumerate(plan.get(kind) or []):
            if not isinstance(obj, dict) or not isinstance(obj.get("comments"), list):
                continue
            for comment in obj["comments"]:
                if isinstance(comment, dict):
                    yield kind, index if kind == "labels" else obj.get("id"), obj, comment


def _check_comments(plan: dict, errors: list[str]) -> None:
    seen: dict[str, str] = {}
    for kind in LIST_KEYS:
        for i, obj in enumerate(plan.get(kind) or []):
            if not isinstance(obj, dict) or "comments" not in obj:
                continue
            path = f"plan.{kind}[{i}].comments"
            comments = obj["comments"]
            if not isinstance(comments, list):
                errors.append(f"{path} must be a list")
                continue
            for j, comment in enumerate(comments):
                cpath = f"{path}[{j}]"
                if not isinstance(comment, dict):
                    errors.append(f"{cpath} must be an object")
                    continue
                _fields(comment, {"id", "text"}, {"by", "created", "edited"}, cpath, errors)
                _str(comment.get("text"), f"{cpath}.text", errors)
                for key in ("by", "created", "edited"):
                    if key in comment and not isinstance(comment[key], str):
                        errors.append(f"{cpath}.{key} must be a string")
                comment_id = _str(comment.get("id"), f"{cpath}.id", errors)
                if comment_id is None:
                    continue
                if comment_id in seen:
                    errors.append(f"{cpath}.id duplicates {seen[comment_id]}")
                else:
                    seen[comment_id] = cpath


def compass_ok(compass: dict) -> bool:
    seq = [compass.get(side) for side in SIDES]
    if any(item not in CARDINALS for item in seq):
        return False
    if len(set(seq)) != 4:
        return False
    start = CARDINALS.index(seq[0])
    expected = [CARDINALS[(start + i) % 4] for i in range(4)]
    return seq == expected


def compass_from_up(up: str) -> dict:
    start = CARDINALS.index(up)
    return {side: CARDINALS[(start + i) % 4] for i, side in enumerate(SIDES)}


def _list(plan: dict, key: str, errors: list[str]) -> list:
    items = plan.get(key, [])
    if not isinstance(items, list):
        errors.append(f"plan.{key} must be a list")
        return []
    return items


def _box(obj: dict, path: str, errors: list[str]) -> None:
    for key in ("x", "y", "w", "h"):
        value = _num(obj.get(key), f"{path}.{key}", errors)
        if key in ("w", "h") and value is not None and value <= 0:
            errors.append(f"{path}.{key} must be > 0")


SHEET_NAME = re.compile(r"^[\w.() -]+\.(png|jpe?g)$", re.IGNORECASE)


def is_building(data) -> bool:
    """A plan file holding several floors, each a plan of its own."""
    return isinstance(data, dict) and "floors" in data


def validate_plan(plan: dict) -> list[str]:
    if is_building(plan):
        return validate_building(plan)
    return _validate_floor(plan)


def validate_building(data: dict) -> list[str]:
    errors: list[str] = []
    _fields(data, {"floors"}, {"name", "notes"}, "plan", errors)
    for key in ("name", "notes"):
        if key in data and not isinstance(data[key], str):
            errors.append(f"plan.{key} must be a string")
    floors = data.get("floors")
    if not isinstance(floors, list) or not floors:
        errors.append("plan.floors must list at least one floor")
        return errors
    seen: dict[str, int] = {}
    for i, floor in enumerate(floors):
        path = f"plan.floors[{i}]"
        if not isinstance(floor, dict):
            errors.append(f"{path} must be an object")
            continue
        floor_id = _str(floor.get("id"), f"{path}.id", errors)
        if floor_id is not None:
            if floor_id in seen:
                errors.append(f"{path}.id duplicates plan.floors[{seen[floor_id]}]")
            seen[floor_id] = i
        errors += [path + e[len("plan"):] if e.startswith("plan") else f"{path}: {e}" for e in _validate_floor(floor, floor=True)]
    return errors


_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _validate_colors(plan: dict, errors: list[str]) -> None:
    colors = plan.get("colors")
    if "colors" not in plan:
        return
    if not isinstance(colors, dict):
        errors.append("plan.colors must be an object of group id to #rrggbb")
        return
    for key, value in colors.items():
        if not isinstance(key, str) or not key:
            errors.append("plan.colors keys must be group ids")
        elif not isinstance(value, str) or not _HEX_COLOR.match(value):
            errors.append(f"plan.colors.{key} must be a #rrggbb color")


def _validate_enabled_rules(plan: dict, errors: list[str]) -> None:
    if "enabled_rules" not in plan:
        return
    rules = plan["enabled_rules"]
    if not isinstance(rules, list) or any(not isinstance(item, str) or not item.strip() for item in rules):
        errors.append("plan.enabled_rules must be a list of rule ids")


def _validate_floor(plan: dict, *, floor: bool = False) -> list[str]:
    errors: list[str] = []
    optional = set(LIST_KEYS) | {"name", "notes", "sheet", "colors", "enabled_rules"} | ({"id"} if floor else set())
    _fields(plan, {"units", "compass", "canvas"}, optional, "plan", errors)
    if plan.get("units") != "inches":
        errors.append("plan.units must be 'inches'")
    for key in ("name", "notes"):
        if key in plan and not isinstance(plan[key], str):
            errors.append(f"plan.{key} must be a string")
    if "sheet" in plan and not (isinstance(plan["sheet"], str) and SHEET_NAME.match(plan["sheet"])):
        errors.append("plan.sheet must be the name of a .png or .jpg in the plan's folder")
    _validate_colors(plan, errors)
    _validate_enabled_rules(plan, errors)

    compass = plan.get("compass")
    if not isinstance(compass, dict):
        errors.append("plan.compass must be an object")
    else:
        _fields(compass, set(SIDES), set(), "plan.compass", errors)
        if not compass_ok(compass):
            errors.append(
                "plan.compass must be a rotation of north, east, south, west "
                "going clockwise from up to right to down to left"
            )

    canvas = plan.get("canvas")
    if not isinstance(canvas, dict):
        errors.append("plan.canvas must be an object")
    else:
        _fields(canvas, {"width_px", "height_px", "px_per_inch", "origin_px"}, set(), "plan.canvas", errors)
        for key in ("width_px", "height_px", "px_per_inch"):
            value = _num(canvas.get(key), f"plan.canvas.{key}", errors)
            if value is not None and value <= 0:
                errors.append(f"plan.canvas.{key} must be > 0")
        origin = canvas.get("origin_px")
        if not isinstance(origin, list) or len(origin) != 2:
            errors.append("plan.canvas.origin_px must be [x, y]")
        else:
            _num(origin[0], "plan.canvas.origin_px[0]", errors)
            _num(origin[1], "plan.canvas.origin_px[1]", errors)

    ids: dict[str, str] = {}

    def take_id(value, path: str) -> str | None:
        text = _str(value, path, errors)
        if text is None:
            return None
        if text in ids:
            errors.append(f"{path} duplicates {ids[text]}")
        else:
            ids[text] = path
        return text

    wall_by_id: dict[str, dict] = {}
    for i, wall in enumerate(_list(plan, "walls", errors)):
        path = f"plan.walls[{i}]"
        if not isinstance(wall, dict):
            errors.append(f"{path} must be an object")
            continue
        _fields(wall, {"id", "x1", "y1", "x2", "y2", "thickness"}, {"kind", "color"} | COMMENTED, path, errors)
        _object_color(wall, path, errors)
        wall_id = take_id(wall.get("id"), f"{path}.id")
        if "kind" in wall and wall["kind"] not in WALL_KINDS:
            errors.append(f"{path}.kind must be one of {', '.join(WALL_KINDS)}")
        coords = [_num(wall.get(k), f"{path}.{k}", errors) for k in ("x1", "y1", "x2", "y2")]
        thickness = _num(wall.get("thickness"), f"{path}.thickness", errors)
        if None not in coords and math.hypot(coords[2] - coords[0], coords[3] - coords[1]) <= 0:
            errors.append(f"{path} has zero length")
        if thickness is not None and thickness <= 0:
            errors.append(f"{path}.thickness must be > 0")
        if wall_id is not None:
            wall_by_id[wall_id] = wall

    spans: dict[str, list[tuple[float, float, str]]] = {}
    for i, opening in enumerate(_list(plan, "openings", errors)):
        path = f"plan.openings[{i}]"
        if not isinstance(opening, dict):
            errors.append(f"{path} must be an object")
            continue
        kind = opening.get("type")
        optional = {"hinge", "swing"} if kind == "door" else set()
        _fields(opening, {"id", "wall", "type", "offset", "width"}, optional | {"color"} | COMMENTED, path, errors)
        _object_color(opening, path, errors)
        take_id(opening.get("id"), f"{path}.id")
        wall_id = opening.get("wall")
        if wall_id not in wall_by_id:
            errors.append(f"{path}.wall does not match a wall id")
        if kind not in ("door", "window"):
            errors.append(f"{path}.type must be 'door' or 'window'")
        offset = _num(opening.get("offset"), f"{path}.offset", errors)
        width = _num(opening.get("width"), f"{path}.width", errors)
        if width is not None and width <= 0:
            errors.append(f"{path}.width must be > 0")
        if kind == "door":
            if opening.get("hinge", "start") not in ("start", "end"):
                errors.append(f"{path}.hinge must be 'start' or 'end'")
            if opening.get("swing", "ccw") not in ("cw", "ccw"):
                errors.append(f"{path}.swing must be 'cw' or 'ccw'")
        if wall_id in wall_by_id and None not in (offset, width):
            length = wall_length(wall_by_id[wall_id])
            if length is not None and (offset < -1e-6 or offset + width > length + 1e-6):
                errors.append(f"{path} does not fit on wall {wall_id}")
            spans.setdefault(wall_id, []).append((offset, offset + width, path))

    for wall_id, pieces in spans.items():
        pieces.sort()
        prev_end = -math.inf
        for start, end, path in pieces:
            if start < prev_end - 1e-6:
                errors.append(f"{path} overlaps another opening on wall {wall_id}")
            prev_end = max(prev_end, end)

    for i, counter in enumerate(_list(plan, "counters", errors)):
        path = f"plan.counters[{i}]"
        if not isinstance(counter, dict):
            errors.append(f"{path} must be an object")
            continue
        if "points" in counter:
            _fields(counter, {"id", "points"}, {"color"} | COMMENTED, path, errors)
            take_id(counter.get("id"), f"{path}.id")
            _object_color(counter, path, errors)
            points = counter.get("points")
            if not isinstance(points, list) or len(points) < 3:
                errors.append(f"{path}.points must list at least 3 [x, y] corners")
                continue
            for j, point in enumerate(points):
                if not isinstance(point, list) or len(point) != 2:
                    errors.append(f"{path}.points[{j}] must be [x, y]")
                    continue
                _num(point[0], f"{path}.points[{j}][0]", errors)
                _num(point[1], f"{path}.points[{j}][1]", errors)
            continue
        _fields(counter, {"id", "x", "y", "w", "h"}, {"overhang", "color"} | COMMENTED, path, errors)
        take_id(counter.get("id"), f"{path}.id")
        _object_color(counter, path, errors)
        _box(counter, path, errors)
        if "overhang" in counter:
            overhang = counter["overhang"]
            if not isinstance(overhang, dict):
                errors.append(f"{path}.overhang must be an object")
            else:
                _fields(overhang, {"edge", "inches"}, set(), f"{path}.overhang", errors)
                if overhang.get("edge") not in CARDINALS:
                    errors.append(f"{path}.overhang.edge must be a cardinal direction")
                inches = _num(overhang.get("inches"), f"{path}.overhang.inches", errors)
                if inches is not None and inches < 0:
                    errors.append(f"{path}.overhang.inches must be >= 0")

    for i, fixture in enumerate(_list(plan, "fixtures", errors)):
        path = f"plan.fixtures[{i}]"
        if not isinstance(fixture, dict):
            errors.append(f"{path} must be an object")
            continue
        _fields(fixture, {"id", "type", "x", "y", "w", "h"}, {"rot", "color"} | COMMENTED, path, errors)
        take_id(fixture.get("id"), f"{path}.id")
        if fixture.get("type") not in FIXTURE_TYPES:
            errors.append(f"{path}.type is not a known FF&E type")
        _object_color(fixture, path, errors)
        _box(fixture, path, errors)
        if "rot" in fixture:
            _num(fixture.get("rot"), f"{path}.rot", errors)

    for i, label in enumerate(_list(plan, "labels", errors)):
        path = f"plan.labels[{i}]"
        if not isinstance(label, dict):
            errors.append(f"{path} must be an object")
            continue
        _fields(label, {"text", "x", "y"}, {"size_px", "color"} | COMMENTED, path, errors)
        _object_color(label, path, errors)
        _str(label.get("text"), f"{path}.text", errors)
        _num(label.get("x"), f"{path}.x", errors)
        _num(label.get("y"), f"{path}.y", errors)
        if "size_px" in label:
            size = _num(label.get("size_px"), f"{path}.size_px", errors)
            if size is not None and size <= 0:
                errors.append(f"{path}.size_px must be > 0")

    for i, region in enumerate(_list(plan, "erase", errors)):
        path = f"plan.erase[{i}]"
        if not isinstance(region, dict):
            errors.append(f"{path} must be an object")
            continue
        _fields(region, {"id", "x", "y", "w", "h"}, {"color"} | COMMENTED, path, errors)
        take_id(region.get("id"), f"{path}.id")
        _object_color(region, path, errors)
        _box(region, path, errors)

    for i, measure in enumerate(_list(plan, "measures", errors)):
        path = f"plan.measures[{i}]"
        if not isinstance(measure, dict):
            errors.append(f"{path} must be an object")
            continue
        _fields(measure, {"id", "x1", "y1", "x2", "y2"}, {"color"} | COMMENTED, path, errors)
        take_id(measure.get("id"), f"{path}.id")
        _object_color(measure, path, errors)
        nums = [_num(measure.get(key), f"{path}.{key}", errors) for key in ("x1", "y1", "x2", "y2")]
        if all(n is not None for n in nums) and math.hypot(nums[2] - nums[0], nums[3] - nums[1]) <= 0:
            errors.append(f"{path} has no length")

    _check_comments(plan, errors)
    return errors
