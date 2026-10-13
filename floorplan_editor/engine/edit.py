"""Edit operations on a plan. Pure: each takes a plan and returns a new plan.

`apply(plan, op)` dispatches on `op["op"]` and returns `(new_plan, ref)`, where
`ref` is `[collection, id]` of the object the edit touched (or None). The editor
sends these operations as JSON; tests call them directly.
"""

from __future__ import annotations

import copy
import math

from .geometry import parse_length, project, wall_length
from .model import FIXTURE_TYPES, ID_KEYS, LIST_KEYS, compass_from_up

JOIN_TOL = 1.0
ID_PREFIX = {"walls": "wall", "openings": "door", "counters": "counter", "fixtures": "fixture", "erase": "erase"}


class EditError(ValueError):
    pass


def all_ids(plan: dict) -> set[str]:
    return {item["id"] for key in ID_KEYS for item in plan.get(key, []) if "id" in item}


def new_id(plan: dict, prefix: str) -> str:
    taken = all_ids(plan)
    n = 1
    while f"{prefix}-{n}" in taken:
        n += 1
    return f"{prefix}-{n}"


def find(plan: dict, kind: str, ref) -> dict:
    if kind not in LIST_KEYS:
        raise EditError(f"unknown collection {kind!r}")
    items = plan.get(kind, [])
    if kind == "labels":
        try:
            return items[int(ref)]
        except (IndexError, ValueError, TypeError) as exc:
            raise EditError(f"no label {ref!r}") from exc
    for item in items:
        if item.get("id") == ref:
            return item
    raise EditError(f"no {kind} {ref!r}")


def _r(value: float) -> float:
    return round(float(value), 2)


def clamp_openings(plan: dict, wall_id: str) -> None:
    """Keep doors and windows on their wall after it changes length."""
    wall = find(plan, "walls", wall_id)
    length = wall_length(wall) or 0
    for opening in plan.get("openings", []):
        if opening["wall"] != wall_id:
            continue
        width = min(float(opening["width"]), length)
        opening["width"] = _r(width)
        opening["offset"] = _r(max(0.0, min(float(opening["offset"]), length - width)))


def _endpoints_at(plan: dict, x: float, y: float, tol: float, skip: set[tuple[str, str]]) -> list[tuple[dict, str]]:
    hits = []
    for wall in plan.get("walls", []):
        for end, (ex, ey) in (("start", (wall["x1"], wall["y1"])), ("end", (wall["x2"], wall["y2"]))):
            if (wall["id"], end) in skip:
                continue
            if math.hypot(float(ex) - x, float(ey) - y) <= tol:
                hits.append((wall, end))
    return hits


def _set_end(wall: dict, end: str, x: float, y: float) -> None:
    if end == "start":
        wall["x1"], wall["y1"] = _r(x), _r(y)
    else:
        wall["x2"], wall["y2"] = _r(x), _r(y)


def _get_end(wall: dict, end: str) -> tuple[float, float]:
    return (float(wall["x1"]), float(wall["y1"])) if end == "start" else (float(wall["x2"]), float(wall["y2"]))


def snap_point(
    plan: dict,
    x: float,
    y: float,
    tol: float,
    *,
    grid: float | None = None,
    skip_walls: set[str] = frozenset(),
) -> tuple[float, float, str]:
    """Snap to a wall endpoint, else onto a wall centerline, else to the grid."""
    best = None
    for wall in plan.get("walls", []):
        if wall["id"] in skip_walls:
            continue
        for end in ("start", "end"):
            ex, ey = _get_end(wall, end)
            d = math.hypot(ex - x, ey - y)
            if d <= tol and (best is None or d < best[0]):
                best = (d, ex, ey)
    if best:
        return best[1], best[2], "endpoint"
    best = None
    for wall in plan.get("walls", []):
        if wall["id"] in skip_walls:
            continue
        _, px, py = project(wall, x, y)
        d = math.hypot(px - x, py - y)
        if d <= tol and (best is None or d < best[0]):
            best = (d, px, py)
    if best:
        return best[1], best[2], "wall"
    if grid:
        return round(x / grid) * grid, round(y / grid) * grid, "grid"
    return x, y, "none"


def _maybe_snap(plan: dict, op: dict, x: float, y: float, skip_walls: set[str] = frozenset()) -> tuple[float, float]:
    tol = op.get("snap")
    if not tol:
        return x, y
    sx, sy, _ = snap_point(plan, x, y, float(tol), grid=op.get("grid"), skip_walls=skip_walls)
    return sx, sy


# ---------------------------------------------------------------- operations


def op_add(plan: dict, op: dict):
    kind = op["kind"]
    obj = copy.deepcopy(op["obj"])
    if kind == "labels":
        plan.setdefault("labels", []).append(obj)
        return ["labels", len(plan["labels"]) - 1]
    if kind not in ID_KEYS:
        raise EditError(f"unknown collection {kind!r}")
    if not obj.get("id"):
        prefix = ID_PREFIX[kind]
        if kind == "openings" and obj.get("type") == "window":
            prefix = "window"
        if kind == "fixtures" and obj.get("type") in FIXTURE_TYPES:
            prefix = obj["type"].replace("_", "-")
        obj["id"] = new_id(plan, prefix)
    elif obj["id"] in all_ids(plan):
        raise EditError(f"id {obj['id']!r} is taken")
    if kind == "openings" and "at" in obj:
        wall = find(plan, "walls", obj["wall"])
        x, y = obj.pop("at")
        t, _, _ = project(wall, float(x), float(y))
        length = wall_length(wall) or 0
        width = min(float(obj["width"]), length)
        obj["width"] = _r(width)
        obj["offset"] = _r(max(0.0, min(t - width / 2, length - width)))
    plan.setdefault(kind, []).append(obj)
    return [kind, obj["id"]]


def op_update(plan: dict, op: dict):
    kind, ref = op["kind"], op["id"]
    item = find(plan, kind, ref)
    fields = dict(op["fields"])
    new_ref = ref
    if "id" in fields and fields["id"] != item.get("id") and kind != "labels":
        new = fields["id"]
        if not isinstance(new, str) or not new.strip():
            raise EditError("id must be a non-empty string")
        if new in all_ids(plan):
            raise EditError(f"id {new!r} is taken")
        if kind == "walls":
            for opening in plan.get("openings", []):
                if opening["wall"] == ref:
                    opening["wall"] = new
        new_ref = new
    for key, value in fields.items():
        if value is None:
            item.pop(key, None)
        else:
            item[key] = value
    if kind == "walls":
        clamp_openings(plan, new_ref)
    if kind == "openings" and "wall" in fields:
        clamp_openings(plan, item["wall"])
    return [kind, new_ref]


def op_delete(plan: dict, op: dict):
    kind, ref = op["kind"], op["id"]
    item = find(plan, kind, ref)
    plan[kind] = [x for x in plan[kind] if x is not item]
    if kind == "walls":
        plan["openings"] = [o for o in plan.get("openings", []) if o["wall"] != ref]
    return None


def op_move(plan: dict, op: dict):
    """Translate an object by (dx, dy). Walls drag joined neighbor endpoints along."""
    kind, ref = op["kind"], op["id"]
    dx, dy = float(op["dx"]), float(op["dy"])
    item = find(plan, kind, ref)
    if kind == "walls":
        neighbors = []
        if op.get("join", True):
            for end in ("start", "end"):
                ex, ey = _get_end(item, end)
                neighbors += [
                    (w, e)
                    for w, e in _endpoints_at(plan, ex, ey, JOIN_TOL, {(ref, "start"), (ref, "end")})
                ]
        for end in ("start", "end"):
            ex, ey = _get_end(item, end)
            _set_end(item, end, ex + dx, ey + dy)
        for wall, end in neighbors:
            ex, ey = _get_end(wall, end)
            _set_end(wall, end, ex + dx, ey + dy)
            clamp_openings(plan, wall["id"])
        clamp_openings(plan, ref)
    elif kind == "openings":
        wall = find(plan, "walls", item["wall"])
        ux = (float(wall["x2"]) - float(wall["x1"])) / (wall_length(wall) or 1)
        uy = (float(wall["y2"]) - float(wall["y1"])) / (wall_length(wall) or 1)
        item["offset"] = _r(float(item["offset"]) + dx * ux + dy * uy)
        clamp_openings(plan, wall["id"])
    elif kind == "counters" and "points" in item:
        item["points"] = [[_r(px + dx), _r(py + dy)] for px, py in item["points"]]
    else:
        item["x"] = _r(float(item["x"]) + dx)
        item["y"] = _r(float(item["y"]) + dy)
    return [kind, ref]


def op_move_wall_end(plan: dict, op: dict):
    wall = find(plan, "walls", op["id"])
    end = op["end"]
    ox, oy = _get_end(wall, end)
    x, y = _maybe_snap(plan, op, float(op["x"]), float(op["y"]), skip_walls={wall["id"]})
    other = _get_end(wall, "end" if end == "start" else "start")
    if math.hypot(x - other[0], y - other[1]) < 0.5:
        raise EditError("a wall needs some length")
    joined = _endpoints_at(plan, ox, oy, JOIN_TOL, {(wall["id"], end)}) if op.get("join", True) else []
    _set_end(wall, end, x, y)
    clamp_openings(plan, wall["id"])
    for neighbor, nend in joined:
        _set_end(neighbor, nend, x, y)
        clamp_openings(plan, neighbor["id"])
    return ["walls", wall["id"]]


def op_split_wall(plan: dict, op: dict):
    wall = find(plan, "walls", op["id"])
    t, sx, sy = project(wall, float(op["x"]), float(op["y"]))
    length = wall_length(wall) or 0
    if t < 1 or t > length - 1:
        raise EditError("split point is too close to the end of the wall")
    new = dict(wall)
    new["id"] = op.get("new_id") or new_id(plan, wall["id"])
    new["x1"], new["y1"] = _r(sx), _r(sy)
    wall["x2"], wall["y2"] = _r(sx), _r(sy)
    index = plan["walls"].index(wall)
    plan["walls"].insert(index + 1, new)
    for opening in plan.get("openings", []):
        if opening["wall"] != wall["id"]:
            continue
        center = float(opening["offset"]) + float(opening["width"]) / 2
        if center > t:
            opening["wall"] = new["id"]
            opening["offset"] = _r(float(opening["offset"]) - t)
    clamp_openings(plan, wall["id"])
    clamp_openings(plan, new["id"])
    return ["walls", new["id"]]


def _polygon(plan: dict, ref: str) -> dict:
    counter = find(plan, "counters", ref)
    if "points" not in counter:
        raise EditError(f"counter {ref!r} is a box, not a polygon")
    return counter


def op_move_vertex(plan: dict, op: dict):
    counter = _polygon(plan, op["id"])
    x, y = _maybe_snap(plan, op, float(op["x"]), float(op["y"]))
    counter["points"][int(op["index"])] = [_r(x), _r(y)]
    return ["counters", counter["id"]]


def op_insert_vertex(plan: dict, op: dict):
    """Insert a corner after `index`."""
    counter = _polygon(plan, op["id"])
    counter["points"].insert(int(op["index"]) + 1, [_r(op["x"]), _r(op["y"])])
    return ["counters", counter["id"]]


def op_delete_vertex(plan: dict, op: dict):
    counter = _polygon(plan, op["id"])
    if len(counter["points"]) <= 3:
        raise EditError("a counter outline needs at least 3 corners")
    del counter["points"][int(op["index"])]
    return ["counters", counter["id"]]


def op_box_to_polygon(plan: dict, op: dict):
    """Turn a box counter (and its overhang) into an editable outline."""
    from .render import Canvas, counter_top

    counter = find(plan, "counters", op["id"])
    if "points" in counter:
        return ["counters", counter["id"]]
    x, y, w, h = counter_top(counter, Canvas(plan))
    for key in ("x", "y", "w", "h", "overhang"):
        counter.pop(key, None)
    counter["points"] = [[_r(x), _r(y)], [_r(x + w), _r(y)], [_r(x + w), _r(y + h)], [_r(x), _r(y + h)]]
    return ["counters", counter["id"]]


def op_flip_door(plan: dict, op: dict):
    door = find(plan, "openings", op["id"])
    if door.get("type") != "door":
        raise EditError("only doors swing")
    if op.get("what", "swing") == "hinge":
        door["hinge"] = "end" if door.get("hinge", "start") == "start" else "start"
    else:
        door["swing"] = "cw" if door.get("swing", "ccw") == "ccw" else "ccw"
    return ["openings", door["id"]]


def op_slide_opening(plan: dict, op: dict):
    opening = find(plan, "openings", op["id"])
    wall = find(plan, "walls", opening["wall"])
    t, _, _ = project(wall, float(op["x"]), float(op["y"]))
    opening["offset"] = _r(t - float(opening["width"]) / 2)
    clamp_openings(plan, wall["id"])
    return ["openings", opening["id"]]


def _rescale(plan: dict, factor: float) -> None:
    """Multiply every inch coordinate by factor (pixel positions stay put when px_per_inch divides by it)."""
    for wall in plan.get("walls", []):
        for key in ("x1", "y1", "x2", "y2", "thickness"):
            wall[key] = _r(float(wall[key]) * factor)
    for opening in plan.get("openings", []):
        opening["offset"] = _r(float(opening["offset"]) * factor)
        opening["width"] = _r(float(opening["width"]) * factor)
    for counter in plan.get("counters", []):
        if "points" in counter:
            counter["points"] = [[_r(x * factor), _r(y * factor)] for x, y in counter["points"]]
        else:
            for key in ("x", "y", "w", "h"):
                counter[key] = _r(float(counter[key]) * factor)
            if "overhang" in counter:
                counter["overhang"]["inches"] = _r(float(counter["overhang"]["inches"]) * factor)
    for key in ("fixtures", "erase"):
        for item in plan.get(key, []):
            for field in ("x", "y", "w", "h"):
                item[field] = _r(float(item[field]) * factor)
    for label in plan.get("labels", []):
        label["x"], label["y"] = _r(float(label["x"]) * factor), _r(float(label["y"]) * factor)


def op_calibrate(plan: dict, op: dict):
    """Set px_per_inch from two sheet pixels and their real distance; objects keep their place on the sheet."""
    (x1, y1), (x2, y2) = op["p1"], op["p2"]
    pixels = math.hypot(float(x2) - float(x1), float(y2) - float(y1))
    length = op["length"]
    inches = parse_length(length) if isinstance(length, str) else float(length)
    if pixels <= 0 or inches <= 0:
        raise EditError("calibration needs two different points and a positive length")
    old = float(plan["canvas"]["px_per_inch"])
    new = pixels / inches
    _rescale(plan, old / new)
    plan["canvas"]["px_per_inch"] = round(new, 6)
    return None


def op_set_compass(plan: dict, op: dict):
    plan["compass"] = compass_from_up(op["up"])
    return None


def op_set_meta(plan: dict, op: dict):
    for key in ("name", "notes"):
        if key in op:
            if op[key]:
                plan[key] = op[key]
            else:
                plan.pop(key, None)
    return None


def op_add_walls(plan: dict, op: dict):
    """Add many walls at once (an auto-trace). `replace` drops existing walls and their openings first."""
    if op.get("replace"):
        plan["walls"] = []
        plan["openings"] = []
    for wall in op["walls"]:
        wall = dict(wall)
        if not wall.get("id") or wall["id"] in all_ids(plan):
            wall["id"] = new_id(plan, "w")
        plan.setdefault("walls", []).append(wall)
    return None


def op_batch(plan: dict, op: dict):
    ref = None
    for sub in op["ops"]:
        plan, ref = _apply(plan, sub)
    return plan, ref


OPS = {
    "add": op_add,
    "update": op_update,
    "delete": op_delete,
    "move": op_move,
    "move_wall_end": op_move_wall_end,
    "split_wall": op_split_wall,
    "move_vertex": op_move_vertex,
    "insert_vertex": op_insert_vertex,
    "delete_vertex": op_delete_vertex,
    "box_to_polygon": op_box_to_polygon,
    "flip_door": op_flip_door,
    "slide_opening": op_slide_opening,
    "calibrate": op_calibrate,
    "set_compass": op_set_compass,
    "set_meta": op_set_meta,
    "add_walls": op_add_walls,
}


def _apply(plan: dict, op: dict):
    name = op.get("op")
    if name == "batch":
        return op_batch(plan, op)
    if name not in OPS:
        raise EditError(f"unknown operation {name!r}")
    try:
        ref = OPS[name](plan, op)
    except KeyError as exc:
        raise EditError(f"{name}: missing {exc}") from exc
    for key in LIST_KEYS:
        if key in plan and not plan[key]:
            plan.pop(key)
    return plan, ref


def apply(plan: dict, op: dict):
    """Return (new_plan, ref). The input plan is not modified."""
    return _apply(copy.deepcopy(plan), op)
