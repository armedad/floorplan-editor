"""Edit operations on a plan. Pure: each takes a plan and returns a new plan.

`apply(plan, op)` dispatches on `op["op"]` and returns `(new_plan, ref)`, where
`ref` is `[collection, id]` of the object the edit touched, a list of those for
edits on several objects, or None. The editor sends these operations as JSON;
tests call them directly.
"""

from __future__ import annotations

import copy
import math
import re
from datetime import datetime, timezone

from .analyze import Frame, doorway, gap_among, items_box, merged_geometry, nearest_wall, parallel, wall_groups
from .appearance import is_color
from .geometry import along, parse_length, project, wall_length
from .model import FIXTURE_TYPES, ID_KEYS, LIST_KEYS, SIDE_VEC, comments_of, compass_from_up

JOIN_TOL = 1.0
ID_PREFIX = {
    "walls": "wall",
    "openings": "door",
    "counters": "counter",
    "fixtures": "fixture",
    "erase": "erase",
    "measures": "measure",
}


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


# ---------------------------------------------------------------- comments


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def comment_ids(plan: dict) -> set[str]:
    return {str(c.get("id")) for *_, c in comments_of(plan)}


def _next_comment_id(taken: set[str]) -> str:
    numbers = [int(m.group(1)) for t in taken if (m := re.fullmatch(r"c(\d+)", t))]
    return f"c{max(numbers, default=0) + 1}"


def new_comment_id(plan: dict) -> str:
    return _next_comment_id(comment_ids(plan))


def _fresh_comment_ids(plan: dict, obj: dict) -> None:
    """Give `obj`'s comments ids no other comment in the plan uses."""
    mine = obj.get("comments") or []
    taken = {str(c.get("id")) for *_, c in comments_of(plan) if not any(c is m for m in mine)}
    for comment in mine:
        if not comment.get("id") or comment["id"] in taken:
            comment["id"] = _next_comment_id(taken)
        taken.add(comment["id"])


def _carry_comments(target: dict, sources: list[dict]) -> None:
    carried = [c for s in sources if s is not target for c in s.get("comments") or []]
    if carried:
        target["comments"] = (target.get("comments") or []) + carried


def find_comment(plan: dict, comment_id: str):
    """(kind, ref, object, comment) for a comment id such as c3."""
    wanted = str(comment_id).strip().lower()
    for kind, ref, obj, comment in comments_of(plan):
        if str(comment.get("id")).lower() == wanted:
            return kind, ref, obj, comment
    raise EditError(f"no comment {comment_id!r}")


def _comment_text(op: dict) -> str:
    text = str(op.get("text") or "").strip()
    if not text:
        raise EditError("a comment needs text")
    return text


def _ref(kind: str, ref):
    return [kind, int(ref) if kind == "labels" else ref]


def op_add_comment(plan: dict, op: dict):
    kind, ref = op["kind"], op["id"]
    obj = find(plan, kind, ref)
    comment_id = op.get("comment_id") or new_comment_id(plan)
    if comment_id in comment_ids(plan):
        raise EditError(f"comment id {comment_id!r} is taken")
    comment = {"id": comment_id, "text": _comment_text(op), "by": str(op.get("by") or "user"), "created": op.get("created") or _now()}
    obj.setdefault("comments", []).append(comment)
    return _ref(kind, ref)


def op_update_comment(plan: dict, op: dict):
    """Change a comment's text, or move it to another object with `to`: [kind, id]."""
    kind, ref, obj, comment = find_comment(plan, op["comment"])
    if op.get("text") is not None:
        text = _comment_text(op)
        if text != comment["text"]:
            comment["text"] = text
            comment["edited"] = op.get("edited") or _now()
    if op.get("to"):
        to_kind, to_ref = op["to"]
        target = find(plan, to_kind, to_ref)
        if target is not obj:
            obj["comments"] = [c for c in obj["comments"] if c is not comment]
            if not obj["comments"]:
                obj.pop("comments")
            target.setdefault("comments", []).append(comment)
            kind, ref = to_kind, to_ref
    return _ref(kind, ref)


def op_delete_comments(plan: dict, op: dict):
    ids = op.get("comments") or [op["comment"]]
    ref = None
    for comment_id in ids:
        kind, owner, obj, comment = find_comment(plan, comment_id)
        obj["comments"] = [c for c in obj["comments"] if c is not comment]
        if not obj["comments"]:
            obj.pop("comments")
        ref = _ref(kind, owner)
    return ref


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
        _fresh_comment_ids(plan, obj)
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
    _fresh_comment_ids(plan, obj)
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
    elif kind == "measures":
        item["x1"] = _r(float(item["x1"]) + dx)
        item["y1"] = _r(float(item["y1"]) + dy)
        item["x2"] = _r(float(item["x2"]) + dx)
        item["y2"] = _r(float(item["y2"]) + dy)
    else:
        item["x"] = _r(float(item["x"]) + dx)
        item["y"] = _r(float(item["y"]) + dy)
    return [kind, ref]


def _items(op: dict) -> list[tuple[str, object]]:
    items = op.get("items")
    if not isinstance(items, list) or not items:
        raise EditError(f"{op.get('op')} needs items: [[kind, id], ...]")
    return [(str(kind), ref) for kind, ref in items]


def op_move_group(plan: dict, op: dict):
    """Move several objects by (dx, dy) together. Each wall end moves once, even where two moved walls meet."""
    dx, dy = float(op["dx"]), float(op["dy"])
    items = _items(op)
    walls = {ref for kind, ref in items if kind == "walls"}
    ends: list[tuple[dict, str]] = []
    for ref in walls:
        wall = find(plan, "walls", ref)
        ends += [(wall, "start"), (wall, "end")]
    if op.get("join", True):
        inside = {(w["id"], e) for w, e in ends}
        for wall, end in list(ends):
            ex, ey = _get_end(wall, end)
            ends += [(w, e) for w, e in _endpoints_at(plan, ex, ey, JOIN_TOL, inside) if (w["id"], e) not in inside]
            inside |= {(w["id"], e) for w, e in ends}
    seen = set()
    for wall, end in ends:
        if (wall["id"], end) in seen:
            continue
        seen.add((wall["id"], end))
        ex, ey = _get_end(wall, end)
        _set_end(wall, end, ex + dx, ey + dy)
    for wall_id in {w["id"] for w, _ in ends}:
        clamp_openings(plan, wall_id)
    for kind, ref in items:
        if kind == "walls":
            continue
        if kind == "openings" and find(plan, kind, ref)["wall"] in walls:
            continue
        op_move(plan, {"kind": kind, "id": ref, "dx": dx, "dy": dy, "join": False})
    return [[kind, ref] for kind, ref in items]


def op_delete_many(plan: dict, op: dict):
    """Delete several objects. Doors and windows already removed with their wall are skipped."""
    items = _items(op)
    labels = sorted((int(ref) for kind, ref in items if kind == "labels"), reverse=True)
    for kind, ref in items:
        if kind == "labels":
            continue
        if kind == "openings" and not any(o.get("id") == ref for o in plan.get("openings", [])):
            continue
        op_delete(plan, {"kind": kind, "id": ref})
    for index in labels:
        op_delete(plan, {"kind": "labels", "id": index})
    return None


def op_duplicate(plan: dict, op: dict):
    """Copy objects, shifted by (dx, dy). Copied walls bring their doors and windows along."""
    dx, dy = float(op.get("dx", 12)), float(op.get("dy", 12))
    items = _items(op)
    walls = {ref for kind, ref in items if kind == "walls"}
    refs = []
    for kind, ref in items:
        source = find(plan, kind, ref)
        if kind == "openings" and source["wall"] in walls:
            continue
        copy_ = copy.deepcopy(source)
        copy_.pop("id", None)
        copy_.pop("comments", None)
        if kind == "openings":
            copy_["offset"] = float(source["offset"]) + float(source["width"]) + 6
        new_ref = op_add(plan, {"kind": kind, "obj": copy_})
        refs.append(new_ref)
        if kind == "openings":
            clamp_openings(plan, source["wall"])
            continue
        op_move(plan, {"kind": kind, "id": new_ref[1], "dx": dx, "dy": dy, "join": False})
        if kind == "walls":
            for opening in [o for o in plan.get("openings", []) if o["wall"] == ref]:
                twin = copy.deepcopy(opening)
                twin.pop("id")
                twin.pop("comments", None)
                twin["wall"] = new_ref[1]
                refs.append(op_add(plan, {"kind": "openings", "obj": twin}))
    return refs


def op_resize_wall(plan: dict, op: dict):
    """Set a wall's length, keeping its start, center, or end in place. Joined wall ends follow."""
    wall = find(plan, "walls", op["id"])
    length = float(op["length"])
    if length < 0.5:
        raise EditError("a wall needs some length")
    old = wall_length(wall) or 0
    if not old:
        raise EditError("the wall has no direction to grow along")
    (x1, y1), (x2, y2) = _get_end(wall, "start"), _get_end(wall, "end")
    ux, uy = (x2 - x1) / old, (y2 - y1) / old
    anchor = op.get("anchor", "start")
    if anchor == "start":
        start, end = (x1, y1), (x1 + ux * length, y1 + uy * length)
    elif anchor == "end":
        start, end = (x2 - ux * length, y2 - uy * length), (x2, y2)
    elif anchor == "center":
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        start = (cx - ux * length / 2, cy - uy * length / 2)
        end = (cx + ux * length / 2, cy + uy * length / 2)
    else:
        raise EditError("anchor must be start, center, or end")
    join = op.get("join", True)
    if anchor != "start":
        op_move_wall_end(plan, {"id": wall["id"], "end": "start", "x": start[0], "y": start[1], "join": join})
    if anchor != "end":
        op_move_wall_end(plan, {"id": wall["id"], "end": "end", "x": end[0], "y": end[1], "join": join})
    return ["walls", wall["id"]]


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
    new.pop("comments", None)
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


def op_set_colors(plan: dict, op: dict):
    """Set or clear this plan's color overrides. A null value clears that group and falls back to the editor default."""
    colors = dict(plan.get("colors") or {})
    updates = op.get("colors")
    if not isinstance(updates, dict):
        raise EditError("colors must be an object")
    for key, value in updates.items():
        if not isinstance(key, str) or not key:
            raise EditError("a color override needs a group")
        if value is None:
            colors.pop(key, None)
        elif is_color(value):
            colors[key] = value.lower()
        else:
            raise EditError("a color override must be a #rrggbb color")
    if colors:
        plan["colors"] = colors
    else:
        plan.pop("colors", None)
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


# ---------------------------------------------------------------- merging and converting


def _length(value) -> float:
    return parse_length(value) if isinstance(value, str) else float(value)


def _line_hit(wall: dict, end: str, target: dict) -> tuple[float, float] | None:
    """Where the line of `wall` crosses the centerline of `target`, for moving `end` onto it."""
    if parallel(wall, target, 10):
        return None
    f, g = Frame(wall), Frame(target)
    ex, ey = _get_end(wall, end)
    denom = f.ux * g.ny - f.uy * g.nx
    if abs(denom) < 1e-9:
        return None
    _, s = g.ts(ex, ey)
    k = -s / denom
    return ex + f.ux * k, ey + f.uy * k


def merge_into_one(plan: dict, walls: list[dict], thickness: float | None = None) -> dict:
    """Replace parallel walls with one wall that covers them, keeping the longest one's id.
    Doors and windows move onto it, and wall ends that were joined to the old walls meet the new one."""
    try:
        fields, keep = merged_geometry(walls, thickness)
    except ValueError as exc:
        raise EditError(str(exc)) from exc
    ids = {w["id"] for w in walls}
    moved = []
    for opening in plan.get("openings", []):
        if opening["wall"] in ids:
            host = find(plan, "walls", opening["wall"])
            moved.append((opening, along(host, float(opening["offset"]) + float(opening["width"]) / 2)))
    group_ends = {(w["id"], e) for w in walls for e in ("start", "end")}
    joined = []
    for wall in walls:
        for end in ("start", "end"):
            joined += _endpoints_at(plan, *_get_end(wall, end), JOIN_TOL, group_ends)
    keep.update(fields)
    _carry_comments(keep, walls)
    plan["walls"] = [w for w in plan["walls"] if w is keep or w["id"] not in ids]
    for opening, (cx, cy) in moved:
        opening["wall"] = keep["id"]
        t, _, _ = project(keep, cx, cy)
        opening["offset"] = _r(t - float(opening["width"]) / 2)
    clamp_openings(plan, keep["id"])
    reach = float(keep["thickness"]) + 2
    for wall, end in joined:
        hit = _line_hit(wall, end, keep)
        if hit and math.hypot(hit[0] - _get_end(wall, end)[0], hit[1] - _get_end(wall, end)[1]) <= reach:
            _set_end(wall, end, *hit)
            clamp_openings(plan, wall["id"])
    return keep


def op_merge_walls(plan: dict, op: dict):
    """Merge parallel walls (bars side by side, or pieces in line) into one, optionally at a set thickness."""
    ids = list(op["ids"])
    if len(ids) < 2:
        raise EditError("merging needs at least two walls")
    walls = [find(plan, "walls", i) for i in ids]
    thickness = _length(op["thickness"]) if op.get("thickness") else None
    return ["walls", merge_into_one(plan, walls, thickness)["id"]]


def op_merge_stacked(plan: dict, op: dict):
    """Find walls that are really one (side by side or broken in line) and merge each group."""
    ids = op.get("ids")
    groups = wall_groups(
        plan,
        ids,
        max_gap=float(op.get("max_gap", 2)),
        min_overlap=float(op.get("min_overlap", 0.75)),
        skip_stray=not ids,
    )
    if not groups:
        where = "among the selected walls" if ids else "in the plan"
        raise EditError(f"no stacked or broken walls found {where}")
    refs = []
    for group in groups:
        refs.append(["walls", merge_into_one(plan, [find(plan, "walls", i) for i in group["ids"]])["id"]])
    return refs


def op_set_wall_thickness(plan: dict, op: dict):
    """Set walls' thickness. `keep` names the face that stays put (up, down, left, right on screen),
    or center; a wall running the same way as that side keeps its center."""
    ids = op["ids"] if "ids" in op else [op["id"]]
    thickness = _length(op["thickness"])
    if thickness <= 0:
        raise EditError("thickness must be more than 0")
    keep = op.get("keep", "center")
    if keep != "center" and keep not in SIDE_VEC:
        raise EditError("keep must be center, up, down, left, or right")
    refs = []
    for wall_id in ids:
        wall = find(plan, "walls", wall_id)
        delta = thickness - float(wall["thickness"])
        if keep != "center":
            frame = Frame(wall)
            sx, sy = SIDE_VEC[keep]
            facing = frame.nx * sx + frame.ny * sy
            if abs(facing) >= 0.3 and delta:
                sign = 1 if facing > 0 else -1
                shift = -sign * delta / 2
                op_move(plan, {"kind": "walls", "id": wall_id, "dx": frame.nx * shift, "dy": frame.ny * shift, "join": op.get("join", True)})
        wall["thickness"] = _r(thickness)
        refs.append(["walls", wall_id])
    return refs if len(refs) > 1 else refs[0]


CONVERT_TARGETS = ("wall", "door", "window", "fixture", "counter", "outline", "label", "erase")
DEFAULT_WIDTH = {"door": 30.0, "window": 36.0}
HOST_LENGTH = 48.0


def _convert_opening(plan: dict, op: dict, items: list, box, at) -> tuple[dict, set]:
    """Place a door or window from the selection's box, or the point `at`. Returns the opening
    and the ids of selected walls that stay (the wall it sits on, and any long walls).

    A given `wall` hosts it. Otherwise a gap between two walls in line, near the selection, is the
    doorway: the two walls join into one and the opening fills the gap. Otherwise it goes on the
    nearest wall, as wide as the selection along it."""
    kind = op["to"]
    selected = {ref for k, ref in items if k == "walls"}
    long_walls = {ref for ref in selected if (wall_length(find(plan, "walls", ref)) or 0) >= HOST_LENGTH}
    cx, cy = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) if box else (float(at[0]), float(at[1]))
    width = _length(op["width"]) if op.get("width") else None
    gap = None
    if not op.get("wall"):
        gap = (gap_among(plan, selected) if box else None) or doorway(plan, cx, cy, prefer=selected)
    if gap is not None:
        a, b = find(plan, "walls", gap["a"]), find(plan, "walls", gap["b"])
        host = merge_into_one(plan, [a, b], max(float(a["thickness"]), float(b["thickness"])))
        t0, _, _ = project(host, *gap["start"])
        t1, _, _ = project(host, *gap["end"])
        t0, t1 = min(t0, t1), max(t0, t1)
        keep = long_walls | {gap["a"], gap["b"]}
    elif op.get("gap_only"):
        raise EditError(f"no doorway gap there for a {kind}: click on a wall, or between the ends of two walls in line")
    else:
        if op.get("wall"):
            host = find(plan, "walls", op["wall"])
        else:
            reach = max(HOST_LENGTH, (box[2] - box[0]) if box else 0, (box[3] - box[1]) if box else 0)
            host = nearest_wall(plan, cx, cy, skip=selected - long_walls, reach=reach)
            if host is None:
                raise EditError(f"no wall to put the {kind} on: select the walls on both sides of the doorway, or name the wall")
        keep = long_walls | {host["id"]}
        frame = Frame(host)
        drawn = items_box(plan, [(k, r) for k, r in items if not (k == "walls" and r == host["id"])])
        if drawn:
            ts = [frame.ts(x, y)[0] for x in (drawn[0], drawn[2]) for y in (drawn[1], drawn[3])]
            t0, t1 = max(0.0, min(ts)), min(frame.length, max(ts))
        else:
            t, _ = frame.ts(cx, cy)
            t0 = t1 = t
        if t1 - t0 < 12 and not width:
            width = DEFAULT_WIDTH[kind]
    if width:
        mid = (t0 + t1) / 2
        t0, t1 = mid - width / 2, mid + width / 2
    opening: dict = {"type": kind, "wall": host["id"], "offset": _r(t0), "width": _r(t1 - t0)}
    if kind == "door":
        opening["hinge"] = op.get("hinge", "start")
        swing = op.get("swing")
        if not swing:
            _, s = Frame(host).ts(cx, cy)
            direction = -1 if opening["hinge"] == "end" else 1
            swing = "ccw" if abs(s) < 1 else ("cw" if (1 if s > 0 else -1) * direction == 1 else "ccw")
        opening["swing"] = swing
    return opening, keep


def op_convert(plan: dict, op: dict):
    """Replace objects (usually traced walls) with what they really are, sized to their box.

    to: wall (several walls merge into one), door or window (on the wall or doorway they're in),
    fixture (type, rot), counter, outline, label (text), or erase (white out the sheet there).
    `at` [x, y] can stand in for items when placing a door or window in a doorway gap.
    keep: true leaves the original objects in place."""
    to = op.get("to")
    if to not in CONVERT_TARGETS:
        raise EditError(f"to must be one of {', '.join(CONVERT_TARGETS)}")
    items = [tuple(i) for i in _items(op)] if op.get("items") else []
    for kind, ref in items:
        find(plan, kind, ref)
    at = op.get("at")
    if not items and not (at and to in ("door", "window")):
        raise EditError("convert needs items (or a point `at` for a door or window)")
    box = items_box(plan, items) if items else None
    if items and box is None:
        raise EditError("the selection has no size to convert")
    keep_ids: set = set()
    if to == "wall":
        walls = [find(plan, "walls", ref) for kind, ref in items if kind == "walls"]
        thickness = _length(op["thickness"]) if op.get("thickness") else None
        if len(walls) == len(items) and len(walls) >= 2:
            new = merge_into_one(plan, walls, thickness)
            return ["walls", new["id"]]
        if len(walls) == len(items) == 1:
            return op_set_wall_thickness(plan, {"ids": [walls[0]["id"]], "thickness": thickness}) if thickness else ["walls", walls[0]["id"]]
        x0, y0, x1, y1 = box
        if x1 - x0 >= y1 - y0:
            cy = (y0 + y1) / 2
            obj = {"x1": _r(x0), "y1": _r(cy), "x2": _r(x1), "y2": _r(cy), "thickness": _r(thickness or max(y1 - y0, 0.5))}
        else:
            cx = (x0 + x1) / 2
            obj = {"x1": _r(cx), "y1": _r(y0), "x2": _r(cx), "y2": _r(y1), "thickness": _r(thickness or max(x1 - x0, 0.5))}
        kind = "walls"
    elif to in ("door", "window"):
        obj, keep_ids = _convert_opening(plan, op, items, box, at)
        kind = "openings"
    else:
        x0, y0, x1, y1 = box
        w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if to == "fixture":
            ftype = op.get("type", "generic")
            if ftype not in FIXTURE_TYPES:
                raise EditError(f"fixture type must be one of {', '.join(FIXTURE_TYPES)}")
            rot = float(op.get("rot", 0)) % 360
            fw, fh = (w, h) if round(rot / 90) % 2 == 0 else (h, w)
            fw = _length(op["w"]) if op.get("w") else fw
            fh = _length(op["h"]) if op.get("h") else fh
            obj = {"type": ftype, "x": _r(cx), "y": _r(cy), "w": _r(fw), "h": _r(fh)}
            if rot:
                obj["rot"] = int(rot) if rot == int(rot) else _r(rot)
            kind = "fixtures"
        elif to == "counter":
            obj, kind = {"x": _r(x0), "y": _r(y0), "w": _r(w), "h": _r(h)}, "counters"
        elif to == "outline":
            obj = {"points": [[_r(x0), _r(y0)], [_r(x0 + w), _r(y0)], [_r(x0 + w), _r(y0 + h)], [_r(x0), _r(y0 + h)]]}
            kind = "counters"
        elif to == "label":
            text = str(op.get("text") or "").strip()
            if not text:
                raise EditError("a label needs text")
            scale = float(plan["canvas"]["px_per_inch"])
            size = op.get("size_px") or max(8, min(72, round((y1 - y0) * scale)))
            obj, kind = {"text": text, "x": _r(cx), "y": _r(cy), "size_px": size}, "labels"
        else:
            pad = float(op.get("pad", 1))
            obj = {"x": _r(x0 - pad), "y": _r(y0 - pad), "w": _r(w + 2 * pad), "h": _r(h + 2 * pad)}
            kind = "erase"
    if op.get("id") and kind != "labels":
        obj["id"] = op["id"]
    if not op.get("keep"):
        gone = [(k, r) for k, r in items if not (k == "walls" and r in keep_ids)]
        if gone:
            present = [(k, r) for k, r in gone if k == "labels" or any(o.get("id") == r for o in plan.get(k, []))]
            _carry_comments(obj, [find(plan, k, r) for k, r in present])
            op_delete_many(plan, {"items": [list(i) for i in gone]})
    ref = op_add(plan, {"kind": kind, "obj": obj})
    if kind == "openings":
        clamp_openings(plan, obj["wall"])
    return ref


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
    "move_group": op_move_group,
    "delete_many": op_delete_many,
    "duplicate": op_duplicate,
    "resize_wall": op_resize_wall,
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
    "set_colors": op_set_colors,
    "add_walls": op_add_walls,
    "merge_walls": op_merge_walls,
    "merge_stacked": op_merge_stacked,
    "set_wall_thickness": op_set_wall_thickness,
    "convert": op_convert,
    "add_comment": op_add_comment,
    "update_comment": op_update_comment,
    "delete_comments": op_delete_comments,
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
