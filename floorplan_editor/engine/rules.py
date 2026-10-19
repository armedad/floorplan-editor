"""Placement checks the editor can measure itself.

A rule keeps the sentence someone wrote and, once a sweep has converted it, a measurement:
which objects, which property, at least or at most, and the number of inches. The measurements
are side clearance, wall overlap, a door's jambs, and wall length. A new combination of those
is data, so the running program can apply it. The sentence is saved on this computer. Each plan
turns rules on for itself. Changing the sentence clears the measurement. A miss does not move
anything.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

from .analyze import find_wall, lookup
from .geometry import along, band, distance_to_wall, project, rotate, wall_length
from .model import FIXTURE_TYPES

# A neighbor this close to a jamb still counts as the wall the edit moved, after it has
# pulled away from the corner. Farther than this, the edit did not cause the door's miss.
JAMB_REACH = 12.0
MAX_INCHES = 200000.0


class RuleError(ValueError):
    pass


def _home() -> Path:
    return Path(os.environ.get("FLOORPLAN_HOME") or Path.home() / ".floorplan-editor").expanduser()


def rules_path() -> Path:
    return _home() / "rules.json"


# id, name, sentence with {inches}, default inches, type, property, compare
_CATALOG = (
    (
        "toilet-side-clearance",
        "Toilet side clearance",
        "A toilet's center stays at least {inches} from the nearest wall face beside it.",
        15.0,
        "toilet",
        "side-clearance",
        "at-least",
    ),
    (
        "door-jambs",
        "Door jambs",
        "A door is flagged when both jambs end flush with its wall and no other wall meets them. "
        "A jamb is held when the wall continues {inches} past it, or another wall comes that close.",
        2.0,
        "door",
        "jamb",
        "at-least",
    ),
    (
        "fixture-wall-clear",
        "Fixture clear of walls",
        "A fixture's box may overlap a wall by at most {inches}.",
        1.0,
        "fixture",
        "wall-overlap",
        "at-most",
    ),
)

# Older saved rules named one combination. The measurement underneath is the same.
_LEGACY = {
    "toilet-side-clearance": ("toilet", "side-clearance", "at-least"),
    "door-jambs": ("door", "jamb", "at-least"),
    "fixture-wall-clear": ("fixture", "wall-overlap", "at-most"),
    "wall-max-length": ("wall", "length", "at-most"),
}

# property -> which types it can measure. "fixture" means every fixture type.
_FIXTURE_PROPS = frozenset({"side-clearance", "wall-overlap"})
_PROPERTIES = frozenset({"side-clearance", "wall-overlap", "jamb", "length"})


def _type_fits(prop: str, kind: str) -> bool:
    if prop in _FIXTURE_PROPS:
        return kind == "fixture" or kind in FIXTURE_TYPES
    if prop == "jamb":
        return kind == "door"
    if prop == "length":
        return kind == "wall"
    return False


def _inches_text(value: float) -> str:
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return f'{text}"'


def _sentence(template: str, inches: float) -> str:
    return template.format(inches=_inches_text(inches))


def _number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RuleError(f"{label} must be a number of inches")
    if value < 0 or value > MAX_INCHES:
        raise RuleError(f"{label} must be between 0 and {MAX_INCHES:g} inches")
    return float(value)


def _by_id():
    return {spec[0]: spec for spec in _CATALOG}


def _code_for(spec, inches: float) -> dict:
    return {"type": spec[4], "property": spec[5], "compare": spec[6], "inches": inches}


def _builtin(spec) -> dict:
    return {"id": spec[0], "text": _sentence(spec[2], spec[3]), "code": _code_for(spec, spec[3])}


def default_rules() -> list[dict]:
    return [_builtin(spec) for spec in _CATALOG]


def _text(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuleError("a rule needs a sentence")
    return value.strip()


def _legacy_code(code: dict) -> dict | None:
    """A saved name such as toilet-side-clearance, read as type, property, and compare."""
    named = _LEGACY.get(code.get("check"))
    if named is None:
        return None
    return {"type": named[0], "property": named[1], "compare": named[2], "inches": code.get("inches")}


def _clean_code(code) -> dict | None:
    """A measurement the program can run, or nothing. A script on the rule is ignored."""
    if not isinstance(code, dict):
        return None
    if "property" not in code and "check" in code:
        code = _legacy_code(code)
        if code is None:
            return None
    kind = code.get("type")
    prop = code.get("property")
    compare = code.get("compare")
    if not isinstance(kind, str) or not isinstance(prop, str) or prop not in _PROPERTIES:
        return None
    if compare not in ("at-least", "at-most") or not _type_fits(prop, kind.strip()):
        return None
    try:
        inches = _number(code.get("inches"), prop)
    except RuleError:
        return None
    return {"type": kind.strip(), "property": prop, "compare": compare, "inches": inches}


def _from_saved(item: dict) -> dict | None:
    rid = item.get("id")
    if not isinstance(rid, str) or not rid.strip():
        return None
    spec = _by_id().get(rid)
    text = item.get("text")
    if isinstance(text, str) and text.strip():
        text = text.strip()
        code = _clean_code(item.get("code"))
    elif spec is not None:
        try:
            inches = _number(item.get("inches", spec[3]), spec[1])
        except RuleError:
            inches = spec[3]
        text = _sentence(spec[2], inches)
        code = _code_for(spec, inches)
    else:
        return None
    return {"id": rid, "text": text, "code": code}


def load_rules() -> list[dict]:
    """Every rule on this computer: the sentence, and the check beside it. A missing file is the built-in sentences."""
    try:
        raw = json.loads(rules_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_rules()
    saved: dict[str, dict] = {}
    if isinstance(raw, dict) and isinstance(raw.get("rules"), list):
        for item in raw["rules"]:
            if isinstance(item, dict):
                row = _from_saved(item)
                if row:
                    saved[row["id"]] = row
    rows = []
    for spec in _CATALOG:
        rows.append(saved.pop(spec[0], _builtin(spec)))
    rows.extend(saved[key] for key in sorted(saved))
    return rows


def _write(rules: list[dict]) -> list[dict]:
    stored = {"rules": [{"id": row["id"], "text": row["text"], "code": row["code"]} for row in rules]}
    path = rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
    return load_rules()


def add_rule(text: str) -> list[dict]:
    """Store a sentence with no check. This does not measure the plan and does not drop a flag."""
    rules = load_rules()
    taken = {row["id"] for row in rules}
    number = 1
    while f"r{number}" in taken:
        number += 1
    rules.append({"id": f"r{number}", "text": _text(text), "code": None})
    return _write(rules)


def update_rule_text(rule_id: str, text: str) -> list[dict]:
    """Save new words. Different words clear the check, so the old number cannot keep running."""
    rules = load_rules()
    found = next((row for row in rules if row["id"] == rule_id), None)
    if found is None:
        raise RuleError(f"unknown rule {rule_id!r}")
    text = _text(text)
    if found["text"] != text:
        found["text"] = text
        found["code"] = None
    return _write(rules)


def set_rule_code(rule_id: str, code) -> list[dict]:
    """Attach a check to the sentence, or pass null to clear it. The sentence is left as it is."""
    rules = load_rules()
    found = next((row for row in rules if row["id"] == rule_id), None)
    if found is None:
        raise RuleError(f"unknown rule {rule_id!r}")
    if code is None:
        found["code"] = None
    else:
        cleaned = _clean_code(code)
        if cleaned is None:
            raise RuleError(
                "code needs a type, a property, at-least or at-most, and inches. "
                "side-clearance and wall-overlap take a fixture type or fixture; jamb takes door; length takes wall"
            )
        found["code"] = cleaned
    return _write(rules)


def delete_rule(rule_id: str) -> list[dict]:
    """Remove a sentence someone added. The built-in sentences stay."""
    if rule_id in _by_id():
        raise RuleError(f"{rule_id} is a built-in sentence")
    rules = load_rules()
    if not any(row["id"] == rule_id for row in rules):
        raise RuleError(f"unknown rule {rule_id!r}")
    return _write([row for row in rules if row["id"] != rule_id])


# ---------------------------------------------------------------- geometry


def _walls(plan: dict) -> list[dict]:
    return [w for w in plan.get("walls", []) if wall_length(w)]


def _fixture_corners(fixture: dict) -> list[tuple[float, float]]:
    w, h = float(fixture["w"]), float(fixture["h"])
    rot = float(fixture.get("rot") or 0)
    cx, cy = float(fixture["x"]), float(fixture["y"])
    return [
        (cx + rx, cy + ry)
        for lx, ly in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))
        for rx, ry in (rotate(lx, ly, rot),)
    ]


def _wall_corners(wall: dict) -> list[tuple[float, float]]:
    return band(wall, 0, wall_length(wall) or 0, float(wall.get("thickness") or 0))


def _overlap(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    """How far two convex shapes interlock. Zero when they only touch or sit apart."""
    depth = math.inf
    for poly in (a, b):
        for i, (x1, y1) in enumerate(poly):
            x2, y2 = poly[(i + 1) % len(poly)]
            nx, ny = y1 - y2, x2 - x1
            length = math.hypot(nx, ny) or 1
            nx, ny = nx / length, ny / length
            a0 = a1 = b0 = b1 = None
            for px, py in a:
                t = px * nx + py * ny
                a0 = t if a0 is None else min(a0, t)
                a1 = t if a1 is None else max(a1, t)
            for px, py in b:
                t = px * nx + py * ny
                b0 = t if b0 is None else min(b0, t)
                b1 = t if b1 is None else max(b1, t)
            gap = min(a1, b1) - max(a0, b0)
            if gap <= 1e-6:
                return 0.0
            depth = min(depth, gap)
    return 0.0 if depth is math.inf else depth


def _key(kind, ref) -> tuple[str, str]:
    return (str(kind), str(ref))


def _violation(rule: str, primary: list, objects: list, message: str, measured: float, wanted: float) -> dict:
    return {
        "rule": rule,
        "primary": list(primary),
        "objects": [list(item) for item in objects],
        "message": message,
        "measured": round(float(measured), 2),
        "wanted": float(wanted),
    }


def _side_clearance(fixture: dict, walls: list[dict]):
    """Nearest wall face that sits beside the fixture, not in front of it or behind it."""
    rot = float(fixture.get("rot") or 0)
    sx, sy = rotate(1, 0, rot)
    dx, dy = rotate(0, 1, rot)
    cx, cy = float(fixture["x"]), float(fixture["y"])
    best = None
    for wall in walls:
        _, px, py = project(wall, cx, cy)
        vx, vy = px - cx, py - cy
        lateral = vx * sx + vy * sy
        depth = vx * dx + vy * dy
        if abs(lateral) + 1e-6 < abs(depth):
            continue
        face = math.hypot(vx, vy) - float(wall.get("thickness") or 0) / 2
        if best is None or face < best[0]:
            best = (face, wall)
    return best


def _fails(measured: float, compare: str, inches: float) -> bool:
    if compare == "at-least":
        return measured < inches - 1e-6
    return measured > inches + 1e-6


def _compare_text(compare: str) -> str:
    return "at least" if compare == "at-least" else "at most"


def _fixtures(plan: dict, kind: str):
    for fixture in plan.get("fixtures", []):
        if not isinstance(fixture, dict) or "id" not in fixture:
            continue
        if kind != "fixture" and fixture.get("type") != kind:
            continue
        yield fixture


def _measure_side(plan: dict, kind: str):
    walls = _walls(plan)
    for fixture in _fixtures(plan, kind):
        try:
            found = _side_clearance(fixture, walls)
        except (KeyError, TypeError, ValueError):
            continue
        if not found:
            continue
        face, wall = found
        fid = fixture["id"]
        label = fixture.get("type") or "fixture"
        yield (
            face,
            ["fixtures", fid],
            [["fixtures", fid], ["walls", wall["id"]]],
            f"{label} {fid} is {_inches_text(face)} from wall {wall['id']}",
        )


def _measure_overlap(plan: dict, kind: str):
    walls = _walls(plan)
    for fixture in _fixtures(plan, kind):
        try:
            corners = _fixture_corners(fixture)
        except (KeyError, TypeError, ValueError):
            continue
        hits = []
        for wall in walls:
            depth = _overlap(corners, _wall_corners(wall))
            if depth > 1e-6:
                hits.append((depth, wall))
        fid = fixture["id"]
        if not hits:
            yield (0.0, ["fixtures", fid], [["fixtures", fid]], f"Fixture {fid} does not overlap a wall")
            continue
        hits.sort(key=lambda item: item[0], reverse=True)
        depth, wall = hits[0]
        yield (
            depth,
            ["fixtures", fid],
            [["fixtures", fid], *[["walls", hit[1]["id"]] for hit in hits]],
            f"Fixture {fid} overlaps wall {wall['id']} by {_inches_text(depth)}",
        )


def _measure_length(plan: dict):
    for wall in _walls(plan):
        length = wall_length(wall)
        if length is None:
            continue
        wid = wall["id"]
        yield (length, ["walls", wid], [["walls", wid]], f"Wall {wid} is {_inches_text(length)} long")


def _jamb_met(plan: dict, host: dict, x: float, y: float, inches: float) -> bool:
    for wall in _walls(plan):
        if wall.get("id") == host.get("id"):
            continue
        reach = float(wall.get("thickness") or 0) / 2 + inches
        if distance_to_wall(wall, x, y) <= reach + 1e-6:
            return True
    return False


def _measure_jamb(plan: dict, compare: str, inches: float):
    for opening in plan.get("openings", []):
        if opening.get("type") != "door":
            continue
        host = find_wall(plan, opening.get("wall"))
        length = wall_length(host) if host else None
        if not host or not length:
            continue
        try:
            offset = float(opening["offset"])
            width = float(opening["width"])
        except (KeyError, TypeError, ValueError):
            continue
        past = (offset, length - offset - width)
        oid = opening["id"]
        primary = ["openings", oid]
        if compare == "at-most":
            yield (
                min(past),
                primary,
                [primary, ["walls", host["id"]]],
                f"Door {oid} on wall {host['id']} has wall past both jambs",
            )
            continue
        ends = (along(host, offset), along(host, offset + width))
        held = (
            past[0] >= inches - 1e-6 or _jamb_met(plan, host, *ends[0], inches),
            past[1] >= inches - 1e-6 or _jamb_met(plan, host, *ends[1], inches),
        )
        nearby = []
        for wall in _walls(plan):
            if wall.get("id") == host.get("id"):
                continue
            if any(distance_to_wall(wall, *end) <= JAMB_REACH for end in ends):
                nearby.append(["walls", wall["id"]])
        yield (
            inches if held[0] or held[1] else 0.0,
            primary,
            [primary, ["walls", host["id"]], *nearby],
            f"Door {oid} on wall {host['id']} has no wall past either jamb",
        )


def _measure(plan: dict, code: dict):
    prop = code["property"]
    kind = code["type"]
    if prop == "side-clearance":
        yield from _measure_side(plan, kind)
    elif prop == "wall-overlap":
        yield from _measure_overlap(plan, kind)
    elif prop == "length":
        yield from _measure_length(plan)
    elif prop == "jamb":
        yield from _measure_jamb(plan, code["compare"], float(code["inches"]))


def check_plan(plan: dict, rules: list[dict] | None = None) -> list[dict]:
    """Misses for the rules this plan has turned on that already have a measurement. A sentence with none is skipped."""
    if not isinstance(plan, dict):
        return []
    enabled = plan.get("enabled_rules")
    if not isinstance(enabled, list):
        return []
    on = {str(item) for item in enabled}
    out = []
    for row in rules if rules is not None else load_rules():
        if str(row.get("id")) not in on:
            continue
        code = row.get("code")
        if not isinstance(code, dict) or code.get("property") not in _PROPERTIES:
            continue
        inches = float(code["inches"])
        compare = code["compare"]
        for measured, primary, objects, detail in _measure(plan, code):
            if not _fails(measured, compare, inches):
                continue
            violation = _violation(
                row["id"],
                primary,
                objects,
                f"{detail}; this rule asks for {_compare_text(compare)} {_inches_text(inches)}.",
                measured,
                inches,
            )
            violation["type"] = code["type"]
            violation["property"] = code["property"]
            violation["compare"] = compare
            out.append(violation)
    return out


def anchor(plan: dict, kind: str, ref) -> tuple[float, float] | None:
    """A point on the object, in inches, where a flag pin can sit."""
    obj = lookup(plan, kind, ref)
    if not obj:
        return None
    if kind == "walls":
        return ((float(obj["x1"]) + float(obj["x2"])) / 2, (float(obj["y1"]) + float(obj["y2"])) / 2)
    if kind == "openings":
        wall = find_wall(plan, obj.get("wall"))
        if not wall:
            return None
        return along(wall, float(obj["offset"]) + float(obj["width"]) / 2)
    if kind == "fixtures":
        return (float(obj["x"]), float(obj["y"]))
    if obj.get("x") is None or "w" not in obj:
        return None
    return (float(obj["x"]) + float(obj["w"]) / 2, float(obj["y"]) + float(obj["h"]) / 2)


def _pin(violation: dict, touched: list[tuple]) -> tuple | None:
    """The object this edit placed or moved. The fixture or door wins when the edit included it."""
    involved = {_key(*item) for item in violation["objects"]}
    hits = [item for item in touched if _key(*item) in involved]
    if not hits:
        return None
    primary = violation["primary"]
    if any(_key(*item) == _key(*primary) for item in hits):
        return (primary[0], primary[1])
    return hits[-1]


def flag_updates(violations: list[dict], flags: list[dict], touched: list[tuple], plan: dict) -> list[dict]:
    """Flag operations for this edit. One flag per rule and object. A miss that was not caused here is left alone.

    A flag someone marked done stays done until an edit touches that object and it still misses.
    When a later edit of that object passes, the flag is marked done.
    """
    grouped: dict[tuple, list[dict]] = {}
    for violation in violations:
        pin = _pin(violation, touched)
        if pin is None:
            continue
        grouped.setdefault((violation["rule"], _key(*pin)), []).append(violation)

    existing = {}
    for flag in flags:
        target = flag.get("target")
        if flag.get("by") != "rule" or not flag.get("rule") or not target or len(target) != 2:
            continue
        existing[(flag["rule"], _key(target[0], target[1]))] = flag

    actions = []
    for key, group in grouped.items():
        rule, pin_key = key
        pin = _pin(group[0], touched)
        point = anchor(plan, pin[0], pin[1]) if pin else None
        if point is None:
            continue
        note = " ".join(item["message"] for item in group)
        found = existing.get((rule, pin_key))
        same_place = (
            found
            and found.get("x") is not None
            and abs(float(found["x"]) - point[0]) < 0.05
            and abs(float(found["y"]) - point[1]) < 0.05
        )
        if found and not found.get("done") and found.get("note") == note and same_place:
            continue
        change = "opened" if not found or found.get("done") else "updated"
        action = {
            "change": change,
            "rule": rule,
            "target": [pin[0], pin[1]],
            "note": note,
            "x": round(point[0], 2),
            "y": round(point[1], 2),
            "done": False,
        }
        if found:
            action["action"] = "update"
            action["id"] = found["id"]
        else:
            action["action"] = "add"
            action["by"] = "rule"
        actions.append(action)

    touched_keys = {_key(*item) for item in touched}
    for key, flag in existing.items():
        if key in grouped or key[1] not in touched_keys or flag.get("done"):
            continue
        actions.append({
            "action": "update",
            "change": "done",
            "id": flag["id"],
            "rule": flag["rule"],
            "target": list(flag["target"]),
            "done": True,
        })
    return actions
