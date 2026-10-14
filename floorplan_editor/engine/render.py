"""Turn a plan into drawing operations, SVG, and PNG.

`scene()` is the single geometry implementation. Every operation carries `obj`,
`[collection, id]` (labels use their index), so the editor can group and pick them.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .geometry import along, band, rect_polygon, rotate, unit, wall_length
from .model import FIXTURE_LABELS, SIDE_VEC

# How far a door leaf opens from the wall, in degrees. 90 would be straight out.
DOOR_SWING = 60


class Canvas:
    def __init__(self, plan: dict):
        canvas = plan["canvas"]
        self.width = int(round(canvas["width_px"]))
        self.height = int(round(canvas["height_px"]))
        self.scale = float(canvas["px_per_inch"])
        self.origin = (float(canvas["origin_px"][0]), float(canvas["origin_px"][1]))
        self.compass = plan["compass"]

    def pt(self, x: float, y: float) -> tuple[float, float]:
        return (self.origin[0] + x * self.scale, self.origin[1] + y * self.scale)

    def to_inches(self, px: float, py: float) -> tuple[float, float]:
        return ((px - self.origin[0]) / self.scale, (py - self.origin[1]) / self.scale)

    def px(self, inches: float) -> float:
        return inches * self.scale

    def cardinal_vec(self, cardinal: str) -> tuple[int, int]:
        for side, name in self.compass.items():
            if name == cardinal:
                return SIDE_VEC[side]
        raise KeyError(cardinal)


def wall_parts(wall: dict, openings: list[dict]) -> list[tuple[float, float]]:
    length = wall_length(wall) or 0
    cuts = [(0.0, 0.0)]
    for opening in openings:
        cuts.append((float(opening["offset"]), float(opening["offset"]) + float(opening["width"])))
    cuts.append((length, length))
    cuts.sort()
    parts = []
    cursor = 0.0
    for start, end in cuts:
        if start > cursor + 1e-6:
            parts.append((cursor, start))
        cursor = max(cursor, end)
    return parts


def counter_top(counter: dict, canvas: Canvas) -> tuple[float, float, float, float]:
    x, y = float(counter["x"]), float(counter["y"])
    w, h = float(counter["w"]), float(counter["h"])
    overhang = counter.get("overhang")
    if not overhang or float(overhang["inches"]) == 0:
        return x, y, w, h
    vx, vy = canvas.cardinal_vec(overhang["edge"])
    inches = float(overhang["inches"])
    if vx > 0:
        w += inches
    elif vx < 0:
        x -= inches
        w += inches
    if vy > 0:
        h += inches
    elif vy < 0:
        y -= inches
        h += inches
    return x, y, w, h


def _local(fixture: dict, lx: float, ly: float) -> tuple[float, float]:
    rx, ry = rotate(lx, ly, float(fixture.get("rot", 0)))
    return (float(fixture["x"]) + rx, float(fixture["y"]) + ry)


def scene(plan: dict, *, ink="#000", fill_walls: bool = True) -> list[dict]:
    canvas = Canvas(plan)
    ops: list[dict] = []
    openings_by_wall: dict[str, list[dict]] = {}
    for opening in plan.get("openings", []):
        openings_by_wall.setdefault(opening["wall"], []).append(opening)

    for wall in plan.get("walls", []):
        thickness = float(wall["thickness"])
        tag = ["walls", wall["id"]]
        rail = wall.get("kind") == "glass_rail"
        for start, end in wall_parts(wall, openings_by_wall.get(wall["id"], [])):
            if rail:
                ops.append(
                    {
                        "op": "polygon",
                        "points": [canvas.pt(*p) for p in band(wall, start, end, thickness)],
                        "fill": None,
                        "stroke": ink,
                        "width": 1,
                        "role": "rail",
                        "obj": tag,
                    }
                )
                ops.append(
                    {"op": "line", "a": canvas.pt(*along(wall, start)), "b": canvas.pt(*along(wall, end)),
                     "stroke": ink, "width": 1, "obj": tag}
                )
                continue
            ops.append(
                {
                    "op": "polygon",
                    "points": [canvas.pt(*p) for p in band(wall, start, end, thickness)],
                    "fill": ink if fill_walls else None,
                    "stroke": ink,
                    "width": 1,
                    "role": "wall",
                    "obj": tag,
                }
            )
        for opening in openings_by_wall.get(wall["id"], []):
            otag = ["openings", opening["id"]]
            if opening["type"] == "door":
                width = float(opening["width"])
                hinge_at = float(opening["offset"])
                if opening.get("hinge", "start") == "end":
                    hinge_at += width
                swing = opening.get("swing", "ccw")
                direction = -1 if opening.get("hinge", "start") == "end" else 1
                ux, uy = unit(wall)
                hx, hy = along(wall, hinge_at)
                closed = (hx + ux * width * direction, hy + uy * width * direction)
                # Positive rotation is clockwise on the sheet. A door opens 60° from the wall.
                turn = DOOR_SWING if swing == "cw" else -DOOR_SWING
                px, py = rotate(ux * direction, uy * direction, turn)
                opened = (hx + px * width, hy + py * width)
                center = canvas.pt(hx, hy)
                radius = canvas.px(width)
                closed_px = canvas.pt(*closed)
                open_px = canvas.pt(*opened)
                ops.append({"op": "line", "a": center, "b": open_px, "stroke": ink, "width": 2, "obj": otag})
                start_angle = math.degrees(math.atan2(closed_px[1] - center[1], closed_px[0] - center[0])) % 360
                end_angle = math.degrees(math.atan2(open_px[1] - center[1], open_px[0] - center[0])) % 360
                if swing == "cw":
                    arc_start, arc_end = start_angle, end_angle
                else:
                    arc_start, arc_end = end_angle, start_angle
                ops.append(
                    {
                        "op": "arc",
                        "c": center,
                        "r": radius,
                        "start": arc_start,
                        "end": arc_end,
                        "stroke": ink,
                        "width": 1.5,
                        "obj": otag,
                    }
                )
            else:
                ux, uy = unit(wall)
                nx, ny = -uy, ux
                inset = thickness * 0.22
                start = along(wall, float(opening["offset"]))
                end = along(wall, float(opening["offset"]) + float(opening["width"]))
                for sign in (-1, 1):
                    ox, oy = nx * inset * sign, ny * inset * sign
                    ops.append(
                        {
                            "op": "line",
                            "a": canvas.pt(start[0] + ox, start[1] + oy),
                            "b": canvas.pt(end[0] + ox, end[1] + oy),
                            "stroke": ink,
                            "width": max(1, canvas.px(thickness) * 0.08),
                            "obj": otag,
                        }
                    )

    for counter in plan.get("counters", []):
        tag = ["counters", counter["id"]]
        if "points" in counter:
            ops.append(
                {
                    "op": "polygon",
                    "points": [canvas.pt(float(x), float(y)) for x, y in counter["points"]],
                    "fill": None,
                    "stroke": ink,
                    "width": 1.5,
                    "obj": tag,
                }
            )
            continue
        top = rect_polygon(*counter_top(counter, canvas))
        box = rect_polygon(float(counter["x"]), float(counter["y"]), float(counter["w"]), float(counter["h"]))
        ops.append(
            {
                "op": "polygon",
                "points": [canvas.pt(*p) for p in top],
                "fill": "#fff" if fill_walls else None,
                "stroke": ink,
                "width": 1.5,
                "obj": tag,
            }
        )
        if counter.get("overhang") and float(counter["overhang"]["inches"]) > 0:
            ops.append(
                {
                    "op": "polygon",
                    "points": [canvas.pt(*p) for p in box],
                    "fill": None,
                    "stroke": ink,
                    "width": 2,
                    "obj": tag,
                }
            )

    for fixture in plan.get("fixtures", []):
        ops.extend(fixture_ops(fixture, canvas, ink, fill_walls))

    for index, label in enumerate(plan.get("labels", [])):
        ops.append(
            {
                "op": "text",
                "at": canvas.pt(float(label["x"]), float(label["y"])),
                "text": label["text"],
                "size": float(label.get("size_px", 16)),
                "fill": ink,
                "obj": ["labels", index],
            }
        )

    for measure in plan.get("measures", []):
        ops.extend(_measure_ops(measure, canvas))
    return ops


def _length_label(inches: float) -> str:
    sign = "-" if inches < 0 else ""
    rest = abs(inches)
    feet = int(rest // 12)
    rest -= feet * 12
    if round(rest, 1) >= 12:
        feet += 1
        rest = 0.0
    inch = f"{rest:.1f}"
    return f"{sign}{feet}'{inch}\"" if feet else f"{sign}{inch}\""


def _measure_ops(measure: dict, canvas: Canvas) -> list[dict]:
    x1, y1 = float(measure["x1"]), float(measure["y1"])
    x2, y2 = float(measure["x2"]), float(measure["y2"])
    ax, ay = canvas.pt(x1, y1)
    bx, by = canvas.pt(x2, y2)
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length
    tick = 6.0
    gap = 12.0
    tag = ["measures", measure["id"]]
    color = "#00897b"
    line = {"stroke": color, "width": 1.5, "role": "measure", "obj": tag}
    return [
        {"op": "line", "a": [ax, ay], "b": [bx, by], **line},
        {"op": "line", "a": [ax - nx * tick, ay - ny * tick], "b": [ax + nx * tick, ay + ny * tick], **line},
        {"op": "line", "a": [bx - nx * tick, by - ny * tick], "b": [bx + nx * tick, by + ny * tick], **line},
        {
            "op": "text",
            "at": [(ax + bx) / 2 + nx * gap, (ay + by) / 2 + ny * gap],
            "text": _length_label(math.hypot(x2 - x1, y2 - y1)),
            "size": 13,
            "fill": "#00695c",
            "role": "measure",
            "normal": [nx, ny],
            "gap": gap,
            "obj": tag,
        },
    ]


def fixture_ops(fixture: dict, canvas: Canvas, ink, fill: bool) -> list[dict]:
    kind = fixture["type"]
    w, h = float(fixture["w"]), float(fixture["h"])
    tag = ["fixtures", fixture["id"]]
    ops: list[dict] = []

    def poly(points, width=1.5, do_fill=True):
        ops.append(
            {
                "op": "polygon",
                "points": [canvas.pt(*_local(fixture, x, y)) for x, y in points],
                "fill": "#fff" if fill and do_fill else None,
                "stroke": ink,
                "width": width,
                "obj": tag,
            }
        )

    def oval(lx, ly, ow, oh, do_fill=True):
        points = []
        for step in range(64):
            angle = 2 * math.pi * step / 64
            points.append((lx + math.cos(angle) * ow / 2, ly + math.sin(angle) * oh / 2))
        poly(points, do_fill=do_fill)

    box = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]

    if kind == "stool":
        oval(0, 0, w, h, do_fill=False)
        return ops

    if kind == "toilet":
        poly([(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, -h / 2 + h * 0.34), (-w / 2, -h / 2 + h * 0.34)])
        oval(0, h * 0.12, w * 0.78, h * 0.5)
        return ops

    if kind == "soaking_tub":
        oval(0, 0, w, h)
        oval(0, 0, w * 0.78, h * 0.86, do_fill=False)
        return ops

    if kind == "tub":
        poly(box)
        poly(
            [
                (-w / 2 + w * 0.12, -h / 2 + h * 0.1),
                (w / 2 - w * 0.12, -h / 2 + h * 0.1),
                (w / 2 - w * 0.12, h / 2 - h * 0.1),
                (-w / 2 + w * 0.12, h / 2 - h * 0.1),
            ],
            do_fill=False,
        )
        return ops

    if kind == "sink":
        poly(box)
        oval(0, 0, w * 0.55, h * 0.55)
        return ops

    if kind == "shower":
        poly(box)
        for a, b in (((-w / 2, -h / 2), (w / 2, h / 2)), ((w / 2, -h / 2), (-w / 2, h / 2))):
            ops.append({"op": "line", "a": canvas.pt(*_local(fixture, *a)), "b": canvas.pt(*_local(fixture, *b)),
                        "stroke": ink, "width": 1, "obj": tag})
        oval(0, 0, min(w, h) * 0.14, min(w, h) * 0.14)
        return ops

    if kind == "fireplace":
        poly(box)
        step = max(min(w, h) / 4, 1e-6)
        c = -w / 2 - h / 2 + step
        while c < w / 2 + h / 2:
            x0, x1 = max(-w / 2, -h / 2 + c), min(w / 2, h / 2 + c)
            if x1 > x0:
                ops.append({"op": "line", "a": canvas.pt(*_local(fixture, x0, x0 - c)),
                            "b": canvas.pt(*_local(fixture, x1, x1 - c)), "stroke": ink, "width": 1, "obj": tag})
            c += step
        return ops

    if kind == "cabinet":
        poly(box)
        ops.append({"op": "line", "a": canvas.pt(*_local(fixture, -w / 2, -h / 2)),
                    "b": canvas.pt(*_local(fixture, w / 2, h / 2)), "stroke": ink, "width": 1, "obj": tag})
        return ops

    if kind == "range":
        poly(box)
        for sx in (-0.22, 0.22):
            for sy in (-0.22, 0.22):
                oval(sx * w, sy * h, w * 0.22, h * 0.22)
        return ops

    poly(box)
    label = FIXTURE_LABELS.get(kind, kind[:3].upper() if kind != "generic" else "")
    if label:
        ops.append(
            {
                "op": "text",
                "at": canvas.pt(*_local(fixture, 0, 0)),
                "text": label,
                "size": max(8, min(canvas.px(w), canvas.px(h)) * 0.28),
                "fill": ink,
                "obj": tag,
            }
        )
    return ops


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def to_svg(plan: dict, ops: list[dict]) -> str:
    canvas = Canvas(plan)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas.width}" height="{canvas.height}" '
        f'viewBox="0 0 {canvas.width} {canvas.height}">',
        '<rect width="100%" height="100%" fill="#fff"/>',
    ]
    for op in ops:
        if op["op"] == "polygon":
            points = " ".join(f"{x:.2f},{y:.2f}" for x, y in op["points"])
            fill = op["fill"] or "none"
            parts.append(
                f'<polygon points="{points}" fill="{fill}" stroke="{op["stroke"]}" '
                f'stroke-width="{op["width"]}" stroke-linejoin="miter"/>'
            )
        elif op["op"] == "line":
            x1, y1 = op["a"]
            x2, y2 = op["b"]
            parts.append(
                f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                f'stroke="{op["stroke"]}" stroke-width="{op["width"]}" fill="none"/>'
            )
        elif op["op"] == "arc":
            parts.append(
                f'<path d="{arc_path(op)}" fill="none" stroke="{op["stroke"]}" stroke-width="{op["width"]}"/>'
            )
        elif op["op"] == "text":
            x, y = op["at"]
            parts.append(
                f'<text x="{x:.2f}" y="{y:.2f}" fill="{op["fill"]}" font-family="Arial, sans-serif" '
                f'font-size="{op["size"]:.1f}" text-anchor="middle" dominant-baseline="middle">{_xml(op["text"])}</text>'
            )
    parts.append("</svg>")
    return "\n".join(parts)


def arc_path(op: dict) -> str:
    cx, cy = op["c"]
    radius = op["r"]
    start = math.radians(op["start"])
    end = math.radians(op["end"])
    x1, y1 = cx + radius * math.cos(start), cy + radius * math.sin(start)
    x2, y2 = cx + radius * math.cos(end), cy + radius * math.sin(end)
    delta = (op["end"] - op["start"]) % 360
    large = 1 if delta > 180 else 0
    return f"M {x1:.2f} {y1:.2f} A {radius:.2f} {radius:.2f} 0 {large} 1 {x2:.2f} {y2:.2f}"


FONT_CANDIDATES = (
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _font(size: float) -> ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, max(1, int(round(size))))
    return ImageFont.load_default()


def draw_png(plan: dict, ops: list[dict], background: tuple[int, int, int, int]) -> Image.Image:
    canvas = Canvas(plan)
    image = Image.new("RGBA", (canvas.width, canvas.height), background)
    draw = ImageDraw.Draw(image)
    for op in ops:
        if op["op"] == "polygon":
            draw.polygon(op["points"], fill=op["fill"], outline=op["stroke"])
        elif op["op"] == "line":
            draw.line([op["a"], op["b"]], fill=op["stroke"], width=max(1, int(round(op["width"]))))
        elif op["op"] == "arc":
            cx, cy = op["c"]
            radius = op["r"]
            box = [cx - radius, cy - radius, cx + radius, cy + radius]
            draw.arc(box, start=op["start"], end=op["end"], fill=op["stroke"], width=max(1, int(round(op["width"]))))
        elif op["op"] == "text":
            draw.text(op["at"], op["text"], fill=op["fill"], font=_font(op["size"]), anchor="mm")
    return image


def render_files(plan: dict, svg_path: Path, png_path: Path) -> None:
    ops = scene(plan)
    Path(svg_path).write_text(to_svg(plan, ops), encoding="utf-8")
    draw_png(plan, ops, (255, 255, 255, 255)).convert("RGB").save(png_path, "PNG")
