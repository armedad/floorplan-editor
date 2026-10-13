"""Small geometry helpers shared by rendering and editing. Units are inches."""

from __future__ import annotations

import math


def wall_length(wall: dict) -> float | None:
    try:
        return math.hypot(float(wall["x2"]) - float(wall["x1"]), float(wall["y2"]) - float(wall["y1"]))
    except (KeyError, TypeError, ValueError):
        return None


def along(wall: dict, distance: float) -> tuple[float, float]:
    length = wall_length(wall) or 1
    x1, y1 = float(wall["x1"]), float(wall["y1"])
    x2, y2 = float(wall["x2"]), float(wall["y2"])
    t = distance / length
    return (x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)


def unit(wall: dict) -> tuple[float, float]:
    length = wall_length(wall) or 1
    return (
        (float(wall["x2"]) - float(wall["x1"])) / length,
        (float(wall["y2"]) - float(wall["y1"])) / length,
    )


def perp(ux: float, uy: float, swing: str) -> tuple[float, float]:
    if swing == "cw":
        return (-uy, ux)
    return (uy, -ux)


def rect_polygon(x: float, y: float, w: float, h: float) -> list[tuple[float, float]]:
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def band(wall: dict, start: float, end: float, thickness: float) -> list[tuple[float, float]]:
    ax, ay = along(wall, start)
    bx, by = along(wall, end)
    ux, uy = unit(wall)
    px, py = -uy * thickness / 2, ux * thickness / 2
    return [(ax + px, ay + py), (bx + px, by + py), (bx - px, by - py), (ax - px, ay - py)]


def rotate(lx: float, ly: float, deg: float) -> tuple[float, float]:
    angle = math.radians(deg)
    c, s = math.cos(angle), math.sin(angle)
    return (lx * c - ly * s, lx * s + ly * c)


def project(wall: dict, x: float, y: float) -> tuple[float, float, float]:
    """Distance along the wall of the closest centerline point, clamped, plus that point."""
    length = wall_length(wall) or 0
    if length == 0:
        return 0.0, float(wall["x1"]), float(wall["y1"])
    ux, uy = unit(wall)
    t = (x - float(wall["x1"])) * ux + (y - float(wall["y1"])) * uy
    t = max(0.0, min(length, t))
    px, py = along(wall, t)
    return t, px, py


def distance_to_wall(wall: dict, x: float, y: float) -> float:
    _, px, py = project(wall, x, y)
    return math.hypot(x - px, y - py)


def parse_length(text: str) -> float:
    """Parse a length to inches: 26'5", 26' 5", 26ft 5in, 317", 317in, 317, 8.5'."""
    import re

    raw = text.strip().lower().replace("\u2019", "'").replace("\u201d", '"').replace("\u2032", "'").replace("\u2033", '"')
    raw = raw.replace("feet", "'").replace("foot", "'").replace("ft", "'").replace("inches", '"').replace("inch", '"')
    raw = raw.replace("in", '"').replace(" ", "")
    match = re.fullmatch(r"(?:(\d+(?:\.\d+)?)')?(?:(\d+(?:\.\d+)?)\"?)?", raw)
    if not raw or not match or (match.group(1) is None and match.group(2) is None):
        raise ValueError(f"cannot read a length from {text!r}")
    feet = float(match.group(1) or 0)
    inches = float(match.group(2) or 0)
    return feet * 12 + inches


def format_length(inches: float) -> str:
    sign = "-" if inches < 0 else ""
    inches = abs(inches)
    feet = int(inches // 12)
    rest = inches - feet * 12
    if round(rest, 1) >= 12:
        feet, rest = feet + 1, 0.0
    return f"{sign}{feet}'{rest:.1f}\"" if feet else f"{sign}{rest:.1f}\""
