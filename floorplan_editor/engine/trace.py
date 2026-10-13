"""Start a plan from a sheet image by tracing its walls.

Walls on a floor plan sheet are long, solid, dark bars. The tracer thresholds the
image, finds rows of dark runs that line up, and keeps the bars that are thick
enough to be walls and much longer than they are thick. It runs once on the image
for horizontal walls and once on the transposed image for vertical ones.

Walls keep the bar's full extent, so two walls that meet at a corner overlap there
and the corner draws solid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .model import compass_from_up


@dataclass
class TraceSettings:
    dark: int = 110
    min_thickness_px: int = 3
    max_thickness_px: int = 24
    min_length_px: int = 12
    tolerance_px: int = 2


def _ink_rows(image: Image.Image, dark: int) -> tuple[int, int, list[bytes]]:
    gray = image.convert("L")
    mask = gray.point(lambda v: 255 if v < dark else 0)
    width, height = mask.size
    data = mask.tobytes()
    return width, height, [data[y * width : (y + 1) * width] for y in range(height)]


OVERLAP = 0.8


def _bars(rows: list[bytes], settings: TraceSettings) -> list[tuple[float, float, int, int]]:
    """Horizontal bars as (x0, x1, y0, y1), y1 exclusive.

    A run continues a bar when it covers most of the bar's first row. Runs that reach
    past the bar (a perpendicular wall at a corner, window lines in a gap) are clipped
    to it, and the bar's ends come from the inner quartiles of its rows.
    """
    pattern = re.compile(rb"\xff{%d,}" % max(1, settings.min_length_px))
    tol = settings.tolerance_px
    active: list[dict] = []
    bars = []

    def finish(bar: dict) -> None:
        n = len(bar["starts"])
        x0 = sorted(bar["starts"])[int(0.75 * (n - 1))]
        x1 = sorted(bar["ends"])[int(0.25 * (n - 1))]
        if not settings.min_thickness_px <= n <= settings.max_thickness_px:
            return
        if x1 - x0 < max(settings.min_length_px, 2 * n):
            return
        bars.append((x0, x1, bar["y0"], bar["y0"] + n))

    for y, row in enumerate(rows):
        next_active = []
        for match in pattern.finditer(row):
            start, end = match.start(), match.end()
            covered = 0
            for bar in active:
                if bar["hit"]:
                    continue
                rx0, rx1 = bar["x0"], bar["x1"]
                if min(end, rx1) - max(start, rx0) < OVERLAP * (rx1 - rx0):
                    continue
                bar["hit"] = True
                bar["starts"].append(start if start >= rx0 - tol else rx0)
                bar["ends"].append(end if end <= rx1 + tol else rx1)
                covered += rx1 - rx0
                next_active.append(bar)
            if covered < OVERLAP * (end - start):
                next_active.append({"x0": start, "x1": end, "y0": y, "starts": [start], "ends": [end], "hit": True})
        for bar in active:
            if not bar["hit"]:
                finish(bar)
        for bar in next_active:
            bar["hit"] = False
        active = next_active
    for bar in active:
        finish(bar)
    return bars


def _inside(inner: tuple, outer: tuple) -> float:
    _, ax0, ay0, ax1, ay1 = inner
    _, bx0, by0, bx1, by1 = outer
    w = min(ax1, bx1) - max(ax0, bx0)
    h = min(ay1, by1) - max(ay0, by0)
    area = (ax1 - ax0) * (ay1 - ay0)
    return w * h / area if w > 0 and h > 0 and area > 0 else 0.0


def _dedupe(bars: list[tuple]) -> list[tuple]:
    """Drop bars that lie mostly inside a bigger bar on the same axis."""
    kept: list[tuple] = []
    for bar in sorted(bars, key=lambda b: -(b[3] - b[1]) * (b[4] - b[2])):
        if not any(k[0] == bar[0] and _inside(bar, k) >= OVERLAP for k in kept):
            kept.append(bar)
    return kept


def trace_bars(image: Image.Image, settings: TraceSettings) -> list[tuple[str, float, float, float, float]]:
    """Wall bars in pixels: (axis, x0, y0, x1, y1) with axis 'h' or 'v'."""
    _, _, rows = _ink_rows(image, settings.dark)
    found = [("h", x0, y0, x1, y1) for x0, x1, y0, y1 in _bars(rows, settings)]
    _, _, cols = _ink_rows(image.transpose(Image.Transpose.TRANSPOSE), settings.dark)
    found += [("v", y0, x0, y1, x1) for x0, x1, y0, y1 in _bars(cols, settings)]
    found = _dedupe(found)
    found.sort(key=lambda bar: (bar[2], bar[1]))
    return found


def trace_walls(
    image: Image.Image | Path | str,
    settings: TraceSettings | None = None,
    *,
    px_per_inch: float = 1.0,
    origin: tuple[float, float] = (0.0, 0.0),
    id_prefix: str = "w",
) -> list[dict]:
    settings = settings or TraceSettings()
    if not isinstance(image, Image.Image):
        with Image.open(image) as opened:
            image = opened.copy()
    ox, oy = origin

    def inches(value: float, base: float) -> float:
        return round((value - base) / px_per_inch, 2)

    walls = []
    for n, (axis, x0, y0, x1, y1) in enumerate(trace_bars(image, settings), start=1):
        if axis == "h":
            cy = (y0 + y1) / 2
            wall = {"x1": inches(x0, ox), "y1": inches(cy, oy), "x2": inches(x1, ox), "y2": inches(cy, oy)}
            thickness = y1 - y0
        else:
            cx = (x0 + x1) / 2
            wall = {"x1": inches(cx, ox), "y1": inches(y0, oy), "x2": inches(cx, ox), "y2": inches(y1, oy)}
            thickness = x1 - x0
        walls.append({"id": f"{id_prefix}{n}", **wall, "thickness": round(thickness / px_per_inch, 2)})
    return walls


def new_plan_from_image(
    image: Path | str,
    *,
    up: str = "north",
    px_per_inch: float = 1.0,
    trace: bool = True,
    settings: TraceSettings | None = None,
    name: str | None = None,
) -> dict:
    with Image.open(image) as opened:
        sheet = opened.copy()
    plan = {
        "name": name or Path(image).stem,
        "units": "inches",
        "compass": compass_from_up(up),
        "canvas": {"width_px": sheet.width, "height_px": sheet.height, "px_per_inch": px_per_inch, "origin_px": [0, 0]},
    }
    if trace:
        walls = trace_walls(sheet, settings, px_per_inch=px_per_inch)
        if walls:
            plan["walls"] = walls
    return plan
