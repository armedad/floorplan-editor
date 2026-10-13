"""Plan file schema: load, save, and validate.

Coordinates are inches. X increases right. Y increases downward, same as the source image.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .geometry import wall_length

CARDINALS = ("north", "east", "south", "west")
SIDES = ("up", "right", "down", "left")
SIDE_VEC = {"up": (0, -1), "right": (1, 0), "down": (0, 1), "left": (-1, 0)}
FIXTURE_TYPES = (
    "refrigerator",
    "range",
    "oven",
    "dishwasher",
    "toilet",
    "tub",
    "soaking_tub",
    "sink",
    "washer",
    "dryer",
    "washer_dryer",
    "stool",
    "generic",
)
FIXTURE_LABELS = {
    "refrigerator": "REF",
    "oven": "OV",
    "dishwasher": "DW",
    "washer": "W",
    "dryer": "D",
    "washer_dryer": "W/D",
}
ID_KEYS = ("walls", "openings", "counters", "fixtures", "erase")
LIST_KEYS = ID_KEYS + ("labels",)


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


def validate_plan(plan: dict) -> list[str]:
    errors: list[str] = []
    _fields(plan, {"units", "compass", "canvas"}, set(LIST_KEYS) | {"name", "notes"}, "plan", errors)
    if plan.get("units") != "inches":
        errors.append("plan.units must be 'inches'")
    for key in ("name", "notes"):
        if key in plan and not isinstance(plan[key], str):
            errors.append(f"plan.{key} must be a string")

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
        _fields(wall, {"id", "x1", "y1", "x2", "y2", "thickness"}, set(), path, errors)
        wall_id = take_id(wall.get("id"), f"{path}.id")
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
        _fields(opening, {"id", "wall", "type", "offset", "width"}, optional, path, errors)
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
            _fields(counter, {"id", "points"}, set(), path, errors)
            take_id(counter.get("id"), f"{path}.id")
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
        _fields(counter, {"id", "x", "y", "w", "h"}, {"overhang"}, path, errors)
        take_id(counter.get("id"), f"{path}.id")
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
        _fields(fixture, {"id", "type", "x", "y", "w", "h"}, {"rot"}, path, errors)
        take_id(fixture.get("id"), f"{path}.id")
        if fixture.get("type") not in FIXTURE_TYPES:
            errors.append(f"{path}.type is not a known fixture")
        _box(fixture, path, errors)
        if "rot" in fixture:
            _num(fixture.get("rot"), f"{path}.rot", errors)

    for i, label in enumerate(_list(plan, "labels", errors)):
        path = f"plan.labels[{i}]"
        if not isinstance(label, dict):
            errors.append(f"{path} must be an object")
            continue
        _fields(label, {"text", "x", "y"}, {"size_px"}, path, errors)
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
        _fields(region, {"id", "x", "y", "w", "h"}, set(), path, errors)
        take_id(region.get("id"), f"{path}.id")
        _box(region, path, errors)

    return errors
