"""Reading a traced plan: object boxes, walls that belong together, stray bits, and doorway gaps.

A tracer sees ink, not meaning. A thick wall or a filled chase comes out as several thin
bars side by side, a wall broken by a tick mark comes out as two pieces in line, and the
letters of a room name come out as tiny walls. These functions find those cases so they
can be merged, deleted, or converted. Everything is in plan inches.
"""

from __future__ import annotations

import math
from itertools import combinations

from .geometry import along, distance_to_wall, rotate, wall_length

PARALLEL_DEG = 3.0


def find_wall(plan: dict, wall_id: str) -> dict | None:
    return next((w for w in plan.get("walls", []) if w.get("id") == wall_id), None)


def lookup(plan: dict, kind: str, ref) -> dict | None:
    items = plan.get(kind, [])
    if kind == "labels":
        try:
            return items[int(ref)]
        except (IndexError, TypeError, ValueError):
            return None
    return next((item for item in items if item.get("id") == ref), None)


def object_box(plan: dict, kind: str, obj: dict) -> tuple[float, float, float, float] | None:
    """Bounding box in inches (x0, y0, x1, y1)."""
    if kind == "walls":
        x1, y1, x2, y2 = (float(obj[k]) for k in ("x1", "y1", "x2", "y2"))
        length = math.hypot(x2 - x1, y2 - y1) or 1
        nx, ny = -(y2 - y1) / length, (x2 - x1) / length
        half = float(obj.get("thickness", 0)) / 2
        xs = [x1 + nx * half, x1 - nx * half, x2 + nx * half, x2 - nx * half]
        ys = [y1 + ny * half, y1 - ny * half, y2 + ny * half, y2 - ny * half]
        return (min(xs), min(ys), max(xs), max(ys))
    if kind == "openings":
        wall = find_wall(plan, obj.get("wall"))
        if not wall:
            return None
        ax, ay = along(wall, float(obj["offset"]))
        bx, by = along(wall, float(obj["offset"]) + float(obj["width"]))
        pad = float(obj["width"]) if obj.get("type") == "door" else float(wall.get("thickness", 0)) / 2
        return (min(ax, bx) - pad, min(ay, by) - pad, max(ax, bx) + pad, max(ay, by) + pad)
    if kind == "counters" and "points" in obj:
        xs = [float(p[0]) for p in obj["points"]]
        ys = [float(p[1]) for p in obj["points"]]
        return (min(xs), min(ys), max(xs), max(ys))
    if kind == "labels":
        x, y = float(obj["x"]), float(obj["y"])
        return (x, y, x, y)
    x, y, w, h = (float(obj[k]) for k in ("x", "y", "w", "h"))
    if kind == "fixtures":
        half = ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))
        corners = [rotate(lx, ly, float(obj.get("rot", 0))) for lx, ly in half]
        xs = [x + cx for cx, _ in corners]
        ys = [y + cy for _, cy in corners]
        return (min(xs), min(ys), max(xs), max(ys))
    return (x, y, x + w, y + h)


def union_box(boxes) -> tuple[float, float, float, float] | None:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def items_box(plan: dict, items) -> tuple[float, float, float, float] | None:
    return union_box(object_box(plan, kind, obj) for kind, ref in items if (obj := lookup(plan, kind, ref)))


# ---------------------------------------------------------------- walls in a shared frame


class Frame:
    """A wall's own axes: t along it from its start, s across it (positive to the left of travel on screen)."""

    def __init__(self, wall: dict):
        self.x, self.y = float(wall["x1"]), float(wall["y1"])
        self.length = wall_length(wall) or 0.0
        n = self.length or 1.0
        self.ux, self.uy = (float(wall["x2"]) - self.x) / n, (float(wall["y2"]) - self.y) / n
        self.nx, self.ny = -self.uy, self.ux

    def ts(self, x: float, y: float) -> tuple[float, float]:
        dx, dy = x - self.x, y - self.y
        return dx * self.ux + dy * self.uy, dx * self.nx + dy * self.ny

    def point(self, t: float, s: float = 0.0) -> tuple[float, float]:
        return self.x + self.ux * t + self.nx * s, self.y + self.uy * t + self.ny * s

    def extent(self, wall: dict) -> tuple[float, float, float, float]:
        """Another wall in this frame: (t0, t1, s0, s1), its length range and its two faces."""
        a = self.ts(float(wall["x1"]), float(wall["y1"]))
        b = self.ts(float(wall["x2"]), float(wall["y2"]))
        half = float(wall.get("thickness", 0)) / 2
        return min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]) - half, max(a[1], b[1]) + half


def parallel(a: dict, b: dict, degrees: float = PARALLEL_DEG) -> bool:
    fa, fb = Frame(a), Frame(b)
    if not fa.length or not fb.length:
        return False
    return abs(fa.ux * fb.uy - fa.uy * fb.ux) <= math.sin(math.radians(degrees))


def merged_geometry(walls: list[dict], thickness: float | None = None) -> tuple[dict, dict]:
    """One wall covering all of `walls` (which must be parallel): their full length, and either
    their full combined thickness or `thickness` about the same middle. Returns (fields, longest wall)."""
    if not walls:
        raise ValueError("no walls to merge")
    ref = max(walls, key=lambda w: wall_length(w) or 0)
    for wall in walls:
        if not parallel(ref, wall):
            raise ValueError(f"{wall['id']} isn't parallel to {ref['id']}, so they can't become one straight wall")
    frame = Frame(ref)
    spans = [frame.extent(w) for w in walls]
    t0, t1 = min(s[0] for s in spans), max(s[1] for s in spans)
    s0, s1 = min(s[2] for s in spans), max(s[3] for s in spans)
    mid = (s0 + s1) / 2
    (x1, y1), (x2, y2) = frame.point(t0, mid), frame.point(t1, mid)
    width = float(thickness) if thickness else s1 - s0
    if width <= 0:
        raise ValueError("thickness must be more than 0")
    fields = {"x1": round(x1, 2), "y1": round(y1, 2), "x2": round(x2, 2), "y2": round(y2, 2), "thickness": round(width, 2)}
    return fields, ref


# ---------------------------------------------------------------- walls that belong together


def _pair_kind(a: dict, b: dict, max_gap: float, min_overlap: float) -> str | None:
    """'stacked' (side by side, about the same length), 'inline' (one wall broken in two), or None."""
    if not parallel(a, b):
        return None
    if (wall_length(b) or 0) > (wall_length(a) or 0):
        a, b = b, a
    frame = Frame(a)
    at0, at1, as0, as1 = 0.0, frame.length, -float(a["thickness"]) / 2, float(a["thickness"]) / 2
    bt0, bt1, bs0, bs1 = frame.extent(b)
    overlap = min(at1, bt1) - max(at0, bt0)
    across_gap = max(bs0 - as1, as0 - bs1)
    if across_gap <= max_gap and overlap >= min_overlap * max(at1 - at0, bt1 - bt0):
        return "stacked"
    ta, tb = float(a["thickness"]), float(b["thickness"])
    offset = abs((bs0 + bs1) / 2)
    along_gap = max(bt0 - at1, at0 - bt1)
    if offset <= max(0.75, 0.25 * max(ta, tb)) and max(ta, tb) <= 1.5 * min(ta, tb) and along_gap <= max_gap:
        return "inline"
    return None


def _break_point(a: dict, b: dict) -> tuple[float, float]:
    frame = Frame(a)
    bt0, bt1, _, _ = frame.extent(b)
    t = (frame.length + bt0) / 2 if bt0 >= frame.length / 2 else bt1 / 2
    return frame.point(t, 0)


def _at_junction(a: dict, b: dict, walls: list[dict], max_gap: float) -> bool:
    """True when a third, crossing wall meets the break between two in-line walls: a T or a cross,
    which people split on purpose."""
    x, y = _break_point(a, b)
    reach = max(float(a["thickness"]), float(b["thickness"])) / 2 + max_gap
    for wall in walls:
        if wall is a or wall is b or parallel(wall, a):
            continue
        if distance_to_wall(wall, x, y) <= float(wall["thickness"]) / 2 + reach:
            return True
    return False


def wall_groups(
    plan: dict,
    ids: list[str] | None = None,
    *,
    max_gap: float = 2.0,
    min_overlap: float = 0.75,
    skip_stray: bool = False,
) -> list[dict]:
    """Groups of walls that are really one wall: bars side by side (a thick wall traced as thin
    ones) or pieces in line with a small break. Each group has ids, why, and the merged wall.
    skip_stray leaves out groups made only of stray bits, since traced letters look stacked too."""
    stray = {i for g in stray_bits(plan) for i in g["ids"]} if skip_stray else set()
    walls = [w for w in plan.get("walls", []) if (ids is None or w["id"] in ids) and w["id"] not in stray]
    parent = {w["id"]: w["id"] for w in walls}
    why: dict[str, set[str]] = {}

    def root(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    boxes = {w["id"]: object_box(plan, "walls", w) for w in walls}
    reach = max_gap + 0.01
    for a, b in combinations(walls, 2):
        ba, bb = boxes[a["id"]], boxes[b["id"]]
        if ba[0] - reach > bb[2] or bb[0] - reach > ba[2] or ba[1] - reach > bb[3] or bb[1] - reach > ba[3]:
            continue
        kind = _pair_kind(a, b, max_gap, min_overlap)
        if kind == "inline" and _at_junction(a, b, plan.get("walls", []), max_gap):
            kind = None
        if kind:
            ra, rb = root(a["id"]), root(b["id"])
            reasons = why.pop(ra, set()) | why.pop(rb, set()) | {kind}
            parent[ra] = rb
            why[rb] = reasons
    groups: dict[str, list[dict]] = {}
    for wall in walls:
        groups.setdefault(root(wall["id"]), []).append(wall)
    out = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        try:
            fields, ref = merged_geometry(members)
        except ValueError:
            continue
        kinds = why.get(key, set())
        reason = " and ".join(sorted(("side by side" if k == "stacked" else "in line") for k in kinds))
        out.append({"ids": [w["id"] for w in members], "keep": ref["id"], "why": reason, "merged": fields})
    out.sort(key=lambda g: (g["merged"]["y1"], g["merged"]["x1"]))
    return out


def stray_bits(plan: dict, ids: list[str] | None = None, *, max_length: float = 24.0, clearance: float = 1.0) -> list[dict]:
    """Short, thin walls that touch no real wall: usually letters of a room name or dimension, or bits
    of a symbol. Blocky ones (columns) are left out. Clustered so each group is roughly one word or
    symbol, with its box."""
    walls = [w for w in plan.get("walls", []) if ids is None or w["id"] in ids]
    long_boxes = [object_box(plan, "walls", w) for w in plan.get("walls", []) if (wall_length(w) or 0) > 2 * max_length]
    small = []
    for wall in walls:
        length = wall_length(wall) or 0
        if length > max_length or length < 1.5 * float(wall["thickness"]):
            continue
        x0, y0, x1, y1 = object_box(plan, "walls", wall)
        touches = any(
            x0 - clearance <= b[2] and b[0] <= x1 + clearance and y0 - clearance <= b[3] and b[1] <= y1 + clearance for b in long_boxes
        )
        if not touches:
            small.append((wall, (x0, y0, x1, y1)))
    parent = list(range(len(small)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    near = max_length / 2
    for i, j in combinations(range(len(small)), 2):
        a, b = small[i][1], small[j][1]
        if a[0] - near <= b[2] and b[0] <= a[2] + near and a[1] - near <= b[3] and b[1] <= a[3] + near:
            parent[root(i)] = root(j)
    clusters: dict[int, list[int]] = {}
    for i in range(len(small)):
        clusters.setdefault(root(i), []).append(i)
    out = []
    for members in clusters.values():
        box = union_box(small[i][1] for i in members)
        out.append({"ids": [small[i][0]["id"] for i in members], "box": [round(v, 2) for v in box]})
    out.sort(key=lambda c: (c["box"][1], c["box"][0]))
    return out


# ---------------------------------------------------------------- doorways


def doorway(plan: dict, x: float, y: float, *, prefer: set[str] = frozenset(), reach: float = 96.0) -> dict | None:
    """The gap between two walls in line that (x, y) is in or in front of: a doorway with no door yet.
    Returns {a, b, start, end} with the gap's two ends as points on a's centerline, or None."""
    near = []
    for wall in plan.get("walls", []):
        x0, y0, x1, y1 = object_box(plan, "walls", wall)
        if x0 - reach <= x <= x1 + reach and y0 - reach <= y <= y1 + reach:
            near.append(wall)
    best = None
    for a, b in combinations(near, 2):
        found = _gap(a, b, reach)
        if found is None:
            continue
        frame, a, b, g0, g1 = found
        t, s = frame.ts(x, y)
        if not (g0 - 1 <= t <= g1 + 1) or abs(s) > (g1 - g0) + max(float(a["thickness"]), float(b["thickness"])):
            continue
        score = abs(s) + abs(t - (g0 + g1) / 2) * 0.1 - 1000 * (len({a["id"], b["id"]} & set(prefer)))
        if best is None or score < best[0]:
            best = (score, a, b, frame.point(g0), frame.point(g1))
    if best is None:
        return None
    _, a, b, start, end = best
    return {"a": a["id"], "b": b["id"], "start": start, "end": end}


def gap_among(plan: dict, ids, *, reach: float = 96.0) -> dict | None:
    """The narrowest doorway gap between two of the given walls, as doorway() returns it."""
    walls = [w for w in plan.get("walls", []) if w["id"] in set(ids)]
    best = None
    for a, b in combinations(walls, 2):
        found = _gap(a, b, reach)
        if found and (best is None or found[4] - found[3] < best[4] - best[3]):
            best = found
    if best is None:
        return None
    frame, a, b, g0, g1 = best
    return {"a": a["id"], "b": b["id"], "start": frame.point(g0), "end": frame.point(g1)}


def _gap(a: dict, b: dict, reach: float):
    """Two walls in line with a door-sized gap between their ends: (frame of the longer one, longer,
    shorter, gap start, gap end), the gap measured along the longer one. Otherwise None."""
    if not parallel(a, b):
        return None
    if (wall_length(b) or 0) > (wall_length(a) or 0):
        a, b = b, a
    frame = Frame(a)
    bt0, bt1, bs0, bs1 = frame.extent(b)
    if abs((bs0 + bs1) / 2) > max(float(a["thickness"]), float(b["thickness"])) / 2 + 0.5:
        return None
    if bt0 >= frame.length:
        g0, g1 = frame.length, bt0
    elif bt1 <= 0:
        g0, g1 = bt1, 0.0
    else:
        return None
    if not 6 <= g1 - g0 <= reach:
        return None
    return frame, a, b, g0, g1


def nearest_wall(plan: dict, x: float, y: float, *, skip: set[str] = frozenset(), reach: float = 48.0) -> dict | None:
    from .geometry import distance_to_wall

    best = None
    for wall in plan.get("walls", []):
        if wall["id"] in skip:
            continue
        d = distance_to_wall(wall, x, y) - float(wall.get("thickness", 0)) / 2
        if d <= reach and (best is None or d < best[0]):
            best = (d, wall)
    return best[1] if best else None
