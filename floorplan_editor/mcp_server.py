"""MCP server that lets an agent drive the floorplan editor.

It talks to the editor's HTTP API, so every change lands in the same in-memory
document the browser shows, and an open browser follows along live. If no editor is
running, `start_editor` launches `floorplan serve` in the background.

Run it with `floorplan mcp` (stdio). Set FLOORPLAN_URL to use a port other than 8765.
"""

from __future__ import annotations

import io
import logging
import math
import os
import subprocess
import sys
import tempfile
import time
import webbrowser
from pathlib import Path
from typing import Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.fastmcp.exceptions import ToolError
from PIL import Image as PILImage

from .engine.geometry import along, distance_to_wall, format_length, project, rotate, wall_length
from .engine.model import LIST_KEYS

URL = os.environ.get("FLOORPLAN_URL", "http://127.0.0.1:8765").rstrip("/")
LOG = Path(tempfile.gettempdir()) / "floorplan-editor.log"
BASE = "plan.json"

Kind = Literal["walls", "openings", "counters", "fixtures", "labels", "erase"]

mcp = FastMCP(
    "floorplan-editor",
    log_level="WARNING",
    instructions=(
        "Drives the floorplan editor: a local web app that edits floor plan JSON (walls, doors, "
        "windows, counters, fixtures, labels, erase regions) laid over a source sheet image. "
        "Coordinates are inches; x grows right and y grows down, like the sheet. "
        "Start with editor_status (or start_editor with a folder, or open_anything with any file). "
        "The user drops numbered flags (F1, F2, ...) on the drawing to point at things; when they "
        "mention a flag, call list_flags to see where it is and what is under it, and "
        "view_image(flag=...) to look. Use find_objects to locate things by position, view_image "
        "to look at the sheet, overlay, or plan (crop with a region in inches), then edit. Edits "
        "stay in memory until save; save mode 'step' writes the next plan-stepN.json. The user's "
        "browser, if open, updates live. Mark a flag done with update_flag once it's handled."
    ),
)


# ---------------------------------------------------------------- HTTP client


def _client(timeout: float = 120) -> httpx.Client:
    return httpx.Client(base_url=URL, timeout=timeout)


def _error_text(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return response.text or response.reason_phrase
    if isinstance(detail, dict) and isinstance(detail.get("errors"), list):
        return "; ".join(detail["errors"])
    return detail if isinstance(detail, str) else str(detail)


def _request(method: str, path: str, body: dict | None = None, *, timeout: float = 120) -> httpx.Response:
    try:
        with _client(timeout) as client:
            response = client.request(method, path, json=body)
    except httpx.ConnectError as exc:
        raise ToolError(f"The floorplan editor isn't running at {URL}. Call start_editor with a plan folder.") from exc
    if response.status_code >= 400:
        raise ToolError(_error_text(response))
    return response


def _get(path: str) -> dict:
    return _request("GET", path).json()


def _post(path: str, body: dict | None = None) -> dict:
    return _request("POST", path, body or {}).json()


def _running() -> bool:
    try:
        with httpx.Client(base_url=URL, timeout=1.5) as client:
            return client.get("/api/state").status_code == 200
    except httpx.HTTPError:
        return False


def _ui(command: dict) -> int:
    """Send a command to open browsers; returns how many are listening. Never fails an edit."""
    try:
        return int(_post("/api/ui", command).get("browsers", 0))
    except ToolError:
        return 0


def _note(text: str, ref: list | None = None) -> None:
    if ref:
        _ui({"action": "select", "key": f"{ref[0]}:{ref[1]}"})
    _ui({"action": "message", "text": f"Cursor: {text}"})


# ---------------------------------------------------------------- summaries


def _counts(plan: dict | None) -> dict:
    if not plan:
        return {}
    return {key: len(plan.get(key, [])) for key in LIST_KEYS}


def _summary(state: dict, **extra) -> dict:
    plan = state.get("plan")
    out = {
        "folder": state.get("folder"),
        "file": state.get("file"),
        "dirty": state.get("dirty"),
        "can_undo": state.get("can_undo"),
        "can_redo": state.get("can_redo"),
        "counts": _counts(plan),
    }
    open_flags = [f["id"] for f in state.get("flags") or [] if not f.get("done")]
    if open_flags:
        out["open_flags"] = open_flags
    if state.get("errors"):
        out["errors"] = state["errors"][:20]
    if state.get("saved"):
        out["saved"] = state["saved"]
    ref = state.get("ref")
    if ref and plan:
        out["touched"] = {"kind": ref[0], "id": ref[1], "object": _lookup(plan, ref[0], ref[1])}
    out.update(extra)
    return out


def _lookup(plan: dict, kind: str, ref: Any) -> dict | None:
    items = plan.get(kind, [])
    if kind == "labels":
        try:
            return items[int(ref)]
        except (IndexError, TypeError, ValueError):
            return None
    return next((item for item in items if item.get("id") == ref), None)


def _require_clean(discard_unsaved: bool) -> dict:
    state = _get("/api/state")
    if state.get("dirty") and not discard_unsaved:
        raise ToolError(
            f"{state.get('file')} has unsaved edits. Save first, or pass discard_unsaved=true to drop them."
        )
    return state


def _op(op: dict, note: str) -> dict:
    state = _post("/api/op", {"op": op})
    _note(note, state.get("ref"))
    return _summary(state)


# ---------------------------------------------------------------- object geometry


def object_box(plan: dict, kind: str, obj: dict) -> tuple[float, float, float, float] | None:
    """Bounding box in inches (x0, y0, x1, y1)."""
    if kind == "walls":
        half = float(obj.get("thickness", 0)) / 2
        xs, ys = (float(obj["x1"]), float(obj["x2"])), (float(obj["y1"]), float(obj["y2"]))
        return (min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half)
    if kind == "openings":
        wall = _lookup(plan, "walls", obj.get("wall"))
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


def _distance(plan: dict, kind: str, obj: dict, box, x: float, y: float) -> float:
    if kind == "walls":
        return max(0.0, distance_to_wall(obj, x, y) - float(obj.get("thickness", 0)) / 2)
    if kind == "openings":
        wall = _lookup(plan, "walls", obj.get("wall"))
        if wall:
            t, _, _ = project(wall, x, y)
            t = max(float(obj["offset"]), min(float(obj["offset"]) + float(obj["width"]), t))
            px, py = along(wall, t)
            return math.hypot(x - px, y - py)
    x0, y0, x1, y1 = box
    return math.hypot(max(x0 - x, 0, x - x1), max(y0 - y, 0, y - y1))


def describe(plan: dict, kind: str, obj: dict) -> str:
    if kind == "walls":
        length = wall_length(obj) or 0
        return (
            f"wall ({obj['x1']}, {obj['y1']}) to ({obj['x2']}, {obj['y2']}), "
            f"{format_length(length)} long, {obj['thickness']}\" thick"
        )
    if kind == "openings":
        extra = f", hinge {obj.get('hinge', 'start')}, swing {obj.get('swing', 'ccw')}" if obj.get("type") == "door" else ""
        return f"{obj.get('type')} on {obj.get('wall')} at offset {obj['offset']}, {obj['width']}\" wide{extra}"
    if kind == "counters":
        if "points" in obj:
            return f"counter outline with {len(obj['points'])} corners"
        return f"box counter {obj['w']}x{obj['h']} at ({obj['x']}, {obj['y']})"
    if kind == "fixtures":
        return f"{obj['type']} {obj['w']}x{obj['h']} centered at ({obj['x']}, {obj['y']}), rot {obj.get('rot', 0)}"
    if kind == "labels":
        return f"label {obj['text']!r} at ({obj['x']}, {obj['y']})"
    return f"erase region {obj['w']}x{obj['h']} at ({obj['x']}, {obj['y']})"


def search(
    plan: dict,
    *,
    kinds: list[str] | None = None,
    near: list[float] | None = None,
    radius: float = 24,
    within: list[float] | None = None,
    type_: str | None = None,
    text: str | None = None,
    limit: int = 25,
) -> list[dict]:
    hits = []
    for kind in kinds or list(LIST_KEYS):
        for index, obj in enumerate(plan.get(kind, [])):
            ref = index if kind == "labels" else obj.get("id")
            if type_ and obj.get("type") != type_:
                continue
            if text:
                needle = text.lower()
                hay = f"{ref} {obj.get('text', '')} {obj.get('type', '')}".lower()
                if needle not in hay:
                    continue
            box = object_box(plan, kind, obj)
            if box is None:
                continue
            if within:
                wx0, wy0, wx1, wy1 = min(within[0], within[2]), min(within[1], within[3]), max(within[0], within[2]), max(within[1], within[3])
                if box[2] < wx0 or box[0] > wx1 or box[3] < wy0 or box[1] > wy1:
                    continue
            hit = {"kind": kind, "id": ref, "what": describe(plan, kind, obj), "box": [round(v, 2) for v in box]}
            if near:
                d = _distance(plan, kind, obj, box, float(near[0]), float(near[1]))
                if d > radius:
                    continue
                hit["distance"] = round(d, 2)
            hits.append(hit)
    if near:
        hits.sort(key=lambda h: h["distance"])
    return hits[:limit]


# ---------------------------------------------------------------- editor lifecycle


def _spawn_detached(cmd: list[str]) -> None:
    """Start the editor so it outlives this MCP server, which can be restarted at any time."""
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stderr": subprocess.STDOUT}
    with LOG.open("ab") as log:
        if os.name != "nt":
            subprocess.Popen(cmd, stdout=log, start_new_session=True, **kwargs)
            return
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:
            # Leave the parent's job object, which kills its whole process tree on exit.
            subprocess.Popen(cmd, stdout=log, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
        except OSError:
            subprocess.Popen(cmd, stdout=log, creationflags=flags, **kwargs)


@mcp.tool()
def editor_status() -> dict:
    """Whether the editor is running, its URL, the open folder and file, files in the folder,
    the sheet image, object counts, unsaved state, validation errors, and how many browsers watch it."""
    if not _running():
        return {"running": False, "url": URL, "hint": "Call start_editor with a plan folder."}
    state = _get("/api/state")
    plan = state.get("plan") or {}
    return {
        "running": True,
        "url": URL + "/",
        "browsers_connected": state.get("browsers", 0),
        **_summary(state),
        "files": state.get("files"),
        "next_step": state.get("next_step"),
        "sheet": state.get("sheet"),
        "plan_name": plan.get("name"),
        "compass": plan.get("compass"),
        "canvas": plan.get("canvas"),
    }


@mcp.tool()
def start_editor(
    folder: str | None = None,
    sheet: str | None = None,
    open_browser: bool = True,
    discard_unsaved: bool = False,
) -> dict:
    """Start the editor on a plan folder (or switch a running editor to that folder).

    folder: a folder holding plan.json / plan-stepN.json and the sheet image. It can be empty,
      for starting a new plan with new_plan_from_image.
    sheet: an image to use as the sheet instead of the one found in the folder.
    open_browser: open the editor page in the default browser if none is connected.
    """
    if _running():
        if folder:
            path = Path(folder).expanduser().resolve()
            state = _get("/api/state")
            if Path(state["folder"]) != path or sheet:
                _require_clean(discard_unsaved)
                _post("/api/folder", {"path": str(path), "sheet": sheet})
    else:
        if not folder:
            raise ToolError("The editor isn't running; pass the plan folder to start it on.")
        path = Path(folder).expanduser().resolve()
        if not path.is_dir():
            raise ToolError(f"{path} is not a folder")
        port = httpx.URL(URL).port or 8765
        cmd = [sys.executable, "-m", "floorplan_editor", "serve", str(path), "--port", str(port), "--no-browser"]
        if sheet:
            cmd += ["--sheet", str(Path(sheet).expanduser().resolve())]
        _spawn_detached(cmd)
        deadline = time.time() + 20
        while not _running():
            if time.time() > deadline:
                raise ToolError(f"The editor didn't start; see {LOG}")
            time.sleep(0.3)
    if open_browser and not _get("/api/state").get("browsers"):
        webbrowser.open(URL + "/")
    return editor_status()


@mcp.tool()
def stop_editor(discard_unsaved: bool = False) -> str:
    """Stop the editor server. Refuses while the open plan has unsaved edits unless discard_unsaved."""
    if not _running():
        return "The editor isn't running."
    _require_clean(discard_unsaved)
    _post("/api/shutdown")
    return "Stopped."


# ---------------------------------------------------------------- files and images


@mcp.tool()
def open_plan(file: str, discard_unsaved: bool = False) -> dict:
    """Open a plan for editing. `file` is a name in the folder (plan.json, plan-step2.json) or a
    path to a plan JSON anywhere on disk, which is copied into the folder first."""
    _require_clean(discard_unsaved)
    path = Path(file).expanduser()
    if path.is_absolute() or len(path.parts) > 1:
        state = _post("/api/import", {"path": str(path.resolve()), "open": True})
    else:
        state = _post("/api/open", {"file": file})
    _note(f"opened {state.get('file')}")
    return _summary(state)


@mcp.tool()
def import_file(
    path: str,
    name: str | None = None,
    overwrite: bool = False,
    open: bool = True,
    use_as_sheet: bool = True,
    discard_unsaved: bool = False,
) -> dict:
    """Copy a plan JSON or a sheet image (.png/.jpg) from anywhere on disk into the open folder.

    Plans are named plan-<something>.json (the original name if it already fits); `open` opens it.
    Images keep their name unless `name` is given; `use_as_sheet` makes the image the sheet the
    plan is drawn over (it must be the canvas size for overlays).
    """
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() == ".json" and open:
        _require_clean(discard_unsaved)
    body = {"path": str(source), "name": name, "overwrite": overwrite, "open": open, "sheet": use_as_sheet}
    state = _post("/api/import", body)
    _note(f"imported {state.get('imported')}")
    return _summary(state, imported=state.get("imported"), sheet=state.get("sheet"))


@mcp.tool()
def open_anything(path: str, open_browser: bool = True, discard_unsaved: bool = False) -> dict:
    """Open whatever is at `path`, the way the editor's Open button does.

    A plan folder opens. A plan JSON opens in its own folder. A floor plan image opens the plans
    already made for it (next to it, or in the workspace); otherwise it becomes a new project in
    the workspace with its walls traced and the scale guessed from wall thickness, saved as
    plan.json. Starts the editor if it isn't running.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise ToolError(f"{target} not found")
    if not _running():
        start_editor(str(target if target.is_dir() else target.parent), open_browser=open_browser)
    else:
        _require_clean(discard_unsaved)
    state = _post("/api/open-any", {"path": str(target)})
    opened = state.get("opened") or {}
    _ui({"action": "fit"})
    _note(opened.get("message", f"opened {target.name}"))
    if open_browser and not state.get("browsers"):
        webbrowser.open(URL + "/")
    return _summary(state, opened=opened, sheet=state.get("sheet"), files=state.get("files"))


@mcp.tool()
def new_plan_from_image(
    image_path: str | None = None,
    name: str = BASE,
    up: Literal["north", "east", "south", "west"] = "north",
    px_per_inch: float | None = None,
    trace: bool = False,
    dark: int = 110,
    min_thickness_px: int = 3,
    max_thickness_px: int = 24,
    min_length_px: int = 12,
    overwrite: bool = False,
    discard_unsaved: bool = False,
) -> dict:
    """Start a new plan sized to a sheet image, optionally auto-tracing its walls.

    image_path: the floor plan image; it's copied into the folder. Omit it to use the folder's sheet.
    up: which compass direction is at the top of the sheet.
    px_per_inch: sheet pixels per real inch, if known. Otherwise fix it later with set_plan_properties
      (calibrate with two sheet points a known distance apart).
    trace: add walls found by the tracer (same settings as trace_walls).
    The new plan is unsaved until save is called.
    """
    _require_clean(discard_unsaved)
    if image_path:
        _post("/api/import", {"path": str(Path(image_path).expanduser().resolve()), "sheet": True, "overwrite": overwrite})
    body = {"name": name, "up": up, "px_per_inch": px_per_inch, "overwrite": overwrite}
    state = _post("/api/new", body)
    if trace:
        settings = {
            "dark": dark,
            "min_thickness_px": min_thickness_px,
            "max_thickness_px": max_thickness_px,
            "min_length_px": min_length_px,
        }
        state = _post("/api/trace", {"preview": False, "settings": settings})
    _note(f"new plan {state.get('file')}" + (f", traced {state.get('traced')} walls" if trace else ""))
    return _summary(state, sheet=state.get("sheet"), traced=state.get("traced"))


FLAG_RGB = (255, 109, 0)
DONE_RGB = (130, 130, 130)


def draw_flags(image: PILImage.Image, flags: list[dict]) -> None:
    """Draw flags as numbered pins (regions as outlined boxes), sized to read at any image size."""
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(image)
    r = max(9, round(max(image.size) / 110))
    try:
        font = ImageFont.load_default(size=round(r * 1.2))
    except TypeError:
        font = ImageFont.load_default()
    for flag in flags:
        color = DONE_RGB if flag.get("done") else FLAG_RGB
        x, y = flag["px"]
        if flag.get("size"):
            w, h = flag["size"]
            draw.rectangle([x, y, x + w, y + h], outline=color, width=max(2, r // 4))
        draw.ellipse([x - r, y - 2 * r - r, x + r, y - r], fill=color, outline=(255, 255, 255), width=2)
        draw.polygon([(x - r * 0.6, y - r * 1.4), (x + r * 0.6, y - r * 1.4), (x, y)], fill=color)
        draw.text((x, y - 2 * r), flag["id"], fill=(255, 255, 255), font=font, anchor="mm")


@mcp.tool()
def view_image(
    source: Literal["overlay", "sheet", "plan", "file"] = "overlay",
    file: str | None = None,
    region: list[float] | None = None,
    flag: str | None = None,
    padding: float = 12,
    max_px: int = 1200,
    erase: bool = True,
    show_flags: bool = True,
):
    """Look at the drawing. Returns a PNG.

    source: 'overlay' (the open plan in red over the sheet, with removed objects whited out),
      'sheet' (the source image; erase regions applied unless erase=false), 'plan' (the plan alone
      on white), or 'file' (a .png in the folder, named by `file`, such as plan-step1-overlay.png).
    region: [x0, y0, x1, y1] in inches to crop to, plus `padding` inches around it. Small crops
      are enlarged so details are readable.
    flag: crop around a flag (F3), or the box it marks, with 36" of room unless padding is larger.
    show_flags: draw the user's flags as orange numbered pins (gray once done).
    max_px: longest side of the returned image.
    """
    if source == "file":
        if not file:
            raise ToolError("source 'file' needs the file name")
        path = f"/api/file/{file}"
    else:
        path = {"overlay": "/api/overlay", "sheet": f"/api/sheet?erase={'true' if erase else 'false'}", "plan": "/api/render"}[source]
    data = _request("GET", path).content
    image = PILImage.open(io.BytesIO(data)).convert("RGB")
    caption = f"{source}{' ' + file if file else ''}: {image.width}x{image.height}px"
    if flag or show_flags:
        flags = _get("/api/state").get("flags") or []
        if flag:
            match = next((f for f in flags if f["id"].lower() == flag.lower()), None)
            if match is None or "x" not in match:
                raise ToolError(f"no flag {flag!r} on an open plan")
            x0, y0 = match["x"], match["y"]
            region = [x0, y0, x0 + match.get("w", 0), y0 + match.get("h", 0)]
            padding = max(padding, 36)
            caption += f", around {match['id']}"
        if show_flags and flags:
            draw_flags(image, flags)
    if region:
        state = _get("/api/state")
        plan = state.get("plan")
        if not plan:
            raise ToolError("cropping to a region needs an open plan for the scale")
        canvas = plan["canvas"]
        scale, (ox, oy) = float(canvas["px_per_inch"]), canvas["origin_px"]
        x0, y0, x1, y1 = (float(v) for v in region)
        box = (
            max(0, int(ox + (min(x0, x1) - padding) * scale)),
            max(0, int(oy + (min(y0, y1) - padding) * scale)),
            min(image.width, int(math.ceil(ox + (max(x0, x1) + padding) * scale))),
            min(image.height, int(math.ceil(oy + (max(y0, y1) + padding) * scale))),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ToolError("that region is off the sheet")
        image = image.crop(box)
        caption += f", cropped to inches [{x0}, {y0}, {x1}, {y1}] with {padding}\" padding (pixels {list(box)})"
    longest = max(image.width, image.height)
    factor = min(max_px / longest, 4.0)
    if abs(factor - 1) > 0.01:
        image = image.resize((max(1, round(image.width * factor)), max(1, round(image.height * factor))), PILImage.LANCZOS)
        caption += f", shown at {factor:.2f}x"
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return [Image(data=buffer.getvalue(), format="png"), caption]


# ---------------------------------------------------------------- reading the plan


@mcp.tool()
def get_plan(kinds: list[Kind] | None = None) -> dict:
    """The open plan's JSON. Pass kinds (e.g. ["fixtures", "labels"]) to get just those lists
    plus the plan's name, compass, and canvas."""
    state = _get("/api/state")
    plan = state.get("plan")
    if plan is None:
        raise ToolError("No plan is open. Use open_plan or new_plan_from_image.")
    if kinds:
        keep = {"name", "units", "compass", "canvas", *kinds}
        plan = {key: value for key, value in plan.items() if key in keep}
    return {"file": state.get("file"), "dirty": state.get("dirty"), "plan": plan}


@mcp.tool()
def find_objects(
    kinds: list[Kind] | None = None,
    near: list[float] | None = None,
    radius: float = 24,
    within: list[float] | None = None,
    type: str | None = None,
    text: str | None = None,
    limit: int = 25,
) -> dict:
    """Find objects in the open plan by place or name. Each hit has kind, id (a list index for
    labels), a one-line description, and its bounding box in inches.

    near: [x, y] inches; returns objects within `radius` inches, closest first.
    within: [x0, y0, x1, y1] inches; returns objects whose box overlaps it.
    type: fixture or opening type (toilet, washer_dryer, door, window, ...).
    text: matches ids, label text, and types.
    """
    plan = _get("/api/state").get("plan")
    if plan is None:
        raise ToolError("No plan is open.")
    hits = search(plan, kinds=kinds, near=near, radius=radius, within=within, type_=type, text=text, limit=limit)
    return {"count": len(hits), "hits": hits}


# ---------------------------------------------------------------- editing

OPS_HELP = """
Operations (each a dict with "op"):
- add: {"op":"add","kind":K,"obj":{...}}. id is optional. For doors/windows give "wall" and either
  "offset" (inches from the wall's start) or "at":[x,y] to center it at the nearest wall point.
- update: {"op":"update","kind":K,"id":ID,"fields":{...}}; a field set to null is removed.
- delete: {"op":"delete","kind":K,"id":ID}; deleting a wall deletes its doors and windows.
- move: {"op":"move","kind":K,"id":ID,"dx":..,"dy":..,"join":true}; walls drag joined wall ends along.
- move_group / duplicate / delete_many: {"op":..,"items":[[K,ID],...],"dx":..,"dy":..}; a group moves
  shared corners once; duplicate copies walls with their doors and windows.
- resize_wall: {"op":"resize_wall","id":WALL,"length":..,"anchor":"start"|"center"|"end","join":true}
- move_wall_end: {"op":"move_wall_end","id":WALL,"end":"start"|"end","x":..,"y":..,"join":true,"snap":tol}
- split_wall: {"op":"split_wall","id":WALL,"x":..,"y":..}
- move_vertex / insert_vertex / delete_vertex: {"id":COUNTER,"index":i,"x":..,"y":..}
  (insert puts the new corner after index i); box_to_polygon: {"id":COUNTER}
- flip_door: {"op":"flip_door","id":DOOR,"what":"swing"|"hinge"}
- slide_opening: {"op":"slide_opening","id":OPENING,"x":..,"y":..}
- calibrate: {"op":"calibrate","p1":[px,py],"p2":[px,py],"length":"26'5\\""} (sheet pixels)
- set_compass: {"op":"set_compass","up":"north"}; set_meta: {"op":"set_meta","name":..,"notes":..}
- add_walls: {"op":"add_walls","walls":[...],"replace":false}
Object fields: walls {id,x1,y1,x2,y2,thickness}; openings {id,wall,type:door|window,offset,width,
hinge:start|end,swing:cw|ccw}; counters {id,x,y,w,h,overhang?:{edge,inches}} or {id,points:[[x,y],...]};
fixtures {id,type,x,y,w,h,rot?} (x,y is the center; rot turns clockwise); labels {text,x,y,size_px?};
erase {id,x,y,w,h}. Fixture types: refrigerator, range, oven, dishwasher, toilet, tub, soaking_tub,
sink, washer, dryer, washer_dryer, stool, generic.
"""


@mcp.tool(description="Apply several edit operations as one undo step." + OPS_HELP)
def apply_ops(ops: list[dict], note: str | None = None) -> dict:
    if not ops:
        raise ToolError("ops is empty")
    return _op({"op": "batch", "ops": ops}, note or f"applied {len(ops)} edits")


@mcp.tool()
def add_object(kind: Kind, obj: dict) -> dict:
    """Add one object. Examples:
    wall {"x1":0,"y1":0,"x2":120,"y2":0,"thickness":4};
    door {"wall":"w12","type":"door","at":[300,410],"width":30,"hinge":"start","swing":"cw"};
    box counter {"x":860,"y":330,"w":24,"h":42}; outline counter {"points":[[0,0],[60,0],[60,24],[0,24]]};
    fixture {"type":"toilet","x":327,"y":305,"w":20,"h":28,"rot":90}; label {"text":"PAN","x":879,"y":354};
    erase region {"x":490,"y":481,"w":54,"h":27}. Ids are generated when omitted."""
    return _op({"op": "add", "kind": kind, "obj": obj}, f"added {kind[:-1] if kind != 'erase' else 'erase region'}")


@mcp.tool()
def update_object(kind: Kind, id: str | int, fields: dict) -> dict:
    """Change fields of one object, e.g. {"thickness": 5} or {"rot": 180}. A null value removes the field.
    Renaming a wall's id updates its doors and windows."""
    return _op({"op": "update", "kind": kind, "id": id, "fields": fields}, f"updated {id}")


@mcp.tool()
def delete_objects(kind: Kind, ids: list[str | int]) -> dict:
    """Delete objects (one undo step). Deleting a wall also deletes its doors and windows.
    Label ids are list indexes."""
    if not ids:
        raise ToolError("ids is empty")
    if kind == "labels":
        ids = sorted((int(i) for i in ids), reverse=True)
    ops = [{"op": "delete", "kind": kind, "id": i} for i in ids]
    return _op({"op": "batch", "ops": ops}, f"deleted {', '.join(str(i) for i in ids)}")


@mcp.tool()
def move_object(kind: Kind, id: str | int, dx: float, dy: float, join: bool = True) -> dict:
    """Move an object by (dx, dy) inches. A moved wall drags the ends of walls joined to it unless
    join=false. A door or window slides along its wall."""
    return _op({"op": "move", "kind": kind, "id": id, "dx": dx, "dy": dy, "join": join}, f"moved {id}")


@mcp.tool()
def move_wall_end(
    wall_id: str,
    end: Literal["start", "end"],
    x: float,
    y: float,
    join: bool = True,
    snap: float | None = None,
) -> dict:
    """Move one end of a wall to (x, y) inches. Wall ends joined at that corner move with it unless
    join=false. snap: tolerance in inches for snapping to another wall's end or centerline."""
    op = {"op": "move_wall_end", "id": wall_id, "end": end, "x": x, "y": y, "join": join}
    if snap:
        op["snap"] = snap
    return _op(op, f"moved the {end} of {wall_id}")


@mcp.tool()
def split_wall(wall_id: str, x: float, y: float) -> dict:
    """Split a wall in two at the point on it nearest (x, y). Openings go with the half they sit on."""
    return _op({"op": "split_wall", "id": wall_id, "x": x, "y": y}, f"split {wall_id}")


@mcp.tool()
def edit_counter_outline(
    counter_id: str,
    action: Literal["move_corner", "insert_corner", "delete_corner", "to_outline"],
    index: int | None = None,
    x: float | None = None,
    y: float | None = None,
) -> dict:
    """Edit an outline counter's corners. move_corner sets corner `index` to (x, y); insert_corner adds
    (x, y) after corner `index`; delete_corner removes corner `index`; to_outline turns a box counter
    into an outline so its corners can be edited."""
    name = {
        "move_corner": "move_vertex",
        "insert_corner": "insert_vertex",
        "delete_corner": "delete_vertex",
        "to_outline": "box_to_polygon",
    }[action]
    op: dict[str, Any] = {"op": name, "id": counter_id}
    if action != "to_outline":
        if index is None:
            raise ToolError(f"{action} needs index")
        op["index"] = index
    if action in ("move_corner", "insert_corner"):
        if x is None or y is None:
            raise ToolError(f"{action} needs x and y")
        op.update(x=x, y=y)
    return _op(op, f"{action.replace('_', ' ')} on {counter_id}")


@mcp.tool()
def flip_door(door_id: str, what: Literal["swing", "hinge"] = "swing") -> dict:
    """Flip a door's swing (which side of the wall it opens to) or its hinge (which end it pivots on)."""
    return _op({"op": "flip_door", "id": door_id, "what": what}, f"flipped the {what} of {door_id}")


@mcp.tool()
def rotate_fixture(fixture_id: str, degrees: float = 90) -> dict:
    """Turn a fixture clockwise by `degrees` about its center (negative turns counterclockwise)."""
    plan = _get("/api/state").get("plan") or {}
    fixture = _lookup(plan, "fixtures", fixture_id)
    if fixture is None:
        raise ToolError(f"no fixture {fixture_id!r}")
    rot = (float(fixture.get("rot", 0)) + degrees) % 360
    rot = int(rot) if rot == int(rot) else rot
    return _op({"op": "update", "kind": "fixtures", "id": fixture_id, "fields": {"rot": rot or None}}, f"rotated {fixture_id} to {rot}°")


@mcp.tool()
def set_plan_properties(
    name: str | None = None,
    notes: str | None = None,
    up: Literal["north", "east", "south", "west"] | None = None,
    px_per_inch: float | None = None,
    calibrate_p1: list[float] | None = None,
    calibrate_p2: list[float] | None = None,
    calibrate_length: str | None = None,
) -> dict:
    """Set the plan's name, notes, compass (which direction is up on the sheet), or scale.

    Scale either directly with px_per_inch, or by calibration: two sheet pixel points
    (calibrate_p1, calibrate_p2) a known real distance apart (calibrate_length like 26'5" or 317).
    Changing the scale keeps every object where it is on the sheet.
    """
    ops: list[dict] = []
    if name is not None or notes is not None:
        meta: dict[str, Any] = {"op": "set_meta"}
        if name is not None:
            meta["name"] = name
        if notes is not None:
            meta["notes"] = notes
        ops.append(meta)
    if up:
        ops.append({"op": "set_compass", "up": up})
    if px_per_inch:
        ops.append({"op": "calibrate", "p1": [0, 0], "p2": [px_per_inch, 0], "length": 1})
    if calibrate_p1 or calibrate_p2 or calibrate_length:
        if not (calibrate_p1 and calibrate_p2 and calibrate_length):
            raise ToolError("calibration needs calibrate_p1, calibrate_p2, and calibrate_length")
        ops.append({"op": "calibrate", "p1": calibrate_p1, "p2": calibrate_p2, "length": calibrate_length})
    if not ops:
        raise ToolError("nothing to change")
    summary = _op({"op": "batch", "ops": ops}, "updated plan properties")
    plan = _get("/api/state").get("plan") or {}
    summary.update(name=plan.get("name"), compass=plan.get("compass"), canvas=plan.get("canvas"))
    return summary


@mcp.tool()
def trace_walls(
    apply: bool = False,
    replace: bool = False,
    dark: int = 110,
    min_thickness_px: int = 3,
    max_thickness_px: int = 24,
    min_length_px: int = 12,
) -> dict:
    """Auto-trace walls from the sheet: long, solid, dark bars become walls.

    apply=false previews: returns how many walls it found (and the first few) without changing the plan.
    apply=true adds them (replace=true drops existing walls and openings first) as one undo step.
    dark: gray level (0-255) below which a pixel counts as ink. min/max_thickness_px: bar thickness
    range in pixels (raise the min to skip window and cabinet lines). min_length_px: drop shorter bars.
    """
    settings = {
        "dark": dark,
        "min_thickness_px": min_thickness_px,
        "max_thickness_px": max_thickness_px,
        "min_length_px": min_length_px,
    }
    if not apply:
        walls = _post("/api/trace", {"preview": True, "settings": settings})["walls"]
        return {"found": len(walls), "first": walls[:10], "settings": settings}
    state = _post("/api/trace", {"preview": False, "replace": replace, "settings": settings})
    _note(f"traced {state.get('traced')} walls")
    return _summary(state, traced=state.get("traced"))


# ---------------------------------------------------------------- history and files


@mcp.tool()
def undo(steps: int = 1) -> dict:
    """Undo the last edits in the open plan."""
    state = _get("/api/state")
    for _ in range(max(1, steps)):
        if not state.get("can_undo"):
            break
        state = _post("/api/undo")
    _note("undo")
    return _summary(state)


@mcp.tool()
def redo(steps: int = 1) -> dict:
    """Redo edits that were undone."""
    state = _get("/api/state")
    for _ in range(max(1, steps)):
        if not state.get("can_redo"):
            break
        state = _post("/api/redo")
    _note("redo")
    return _summary(state)


@mcp.tool()
def revert(discard_unsaved: bool = False) -> dict:
    """Reload the open file from disk, dropping unsaved edits (needs discard_unsaved=true if there are any)."""
    _require_clean(discard_unsaved)
    state = _post("/api/revert")
    _note(f"reverted {state.get('file')}")
    return _summary(state)


@mcp.tool()
def save(
    mode: Literal["overwrite", "step", "as"] = "overwrite",
    name: str | None = None,
    confirm_overwrite_base: bool = False,
) -> dict:
    """Save the open plan with its .svg, .png, and -overlay.png.

    mode 'overwrite' saves over the open file; 'step' writes the next plan-stepN.json and opens it;
    'as' writes plan-<name>.json. Overwriting plan.json while steps exist needs
    confirm_overwrite_base=true, since steps and merges are measured against it.
    """
    state = _get("/api/state")
    if mode == "overwrite" and state.get("file") == BASE and len(state.get("files") or []) > 1 and not confirm_overwrite_base:
        raise ToolError("This would overwrite plan.json, the base the steps are built on. Save as a step, or pass confirm_overwrite_base=true.")
    if mode == "as" and not name:
        raise ToolError("mode 'as' needs a name")
    if mode == "as" and not name.startswith("plan"):
        name = f"plan-{name}"
    state = _post("/api/save", {"mode": mode, "name": name})
    _note(f"saved {state.get('file')}")
    return _summary(state)


@mcp.tool()
def merge_steps(
    steps: list[str],
    base: str = BASE,
    out: str = "plan-merged.json",
    open: bool = True,
    discard_unsaved: bool = False,
) -> dict:
    """Merge step files that were each edited from the same base into one plan (with its pictures).
    Two steps changing the same object differently is an error that names both."""
    if open:
        _require_clean(discard_unsaved)
    state = _post("/api/merge", {"steps": steps, "base": base, "out": out, "open": open})
    _note(f"merged {', '.join(steps)} into {out}")
    return _summary(state)


# ---------------------------------------------------------------- flags


def _flag_view(plan: dict | None, flag: dict) -> dict:
    out = {k: flag[k] for k in ("id", "note", "done", "by", "x", "y", "w", "h") if k in flag}
    if not plan or "x" not in flag:
        return out
    target = flag.get("target")
    if target:
        obj = _lookup(plan, target[0], target[1])
        out["placed_on"] = {"kind": target[0], "id": target[1], "what": describe(plan, target[0], obj) if obj else "(since deleted)"}
    if "w" in flag:
        box = [flag["x"], flag["y"], flag["x"] + flag["w"], flag["y"] + flag["h"]]
        out["inside"] = search(plan, within=box, limit=12)
    else:
        out["near"] = search(plan, near=[flag["x"], flag["y"]], radius=18, limit=5)
    return out


@mcp.tool()
def list_flags(include_done: bool = False) -> dict:
    """The flags the user placed to point at things: id (F1, F2, ...), their note, where they are in
    inches, the object they were dropped on, and what is under or near them now (closest first).
    A flag with w/h marks a box; `inside` lists what it covers."""
    state = _get("/api/state")
    plan = state.get("plan")
    flags = [f for f in state.get("flags") or [] if include_done or not f.get("done")]
    return {"count": len(flags), "flags": [_flag_view(plan, f) for f in flags]}


@mcp.tool()
def add_flag(x: float, y: float, note: str = "", w: float | None = None, h: float | None = None) -> dict:
    """Drop a flag at (x, y) inches to point something out to the user (w/h make it a box from x, y).
    It shows as an orange numbered pin in their editor and in its flag list."""
    body: dict[str, Any] = {"action": "add", "x": x, "y": y, "note": note, "by": "cursor"}
    if w and h:
        body.update(w=w, h=h)
    state = _post("/api/flags", body)
    flag_id = state.get("flag")
    _ui({"action": "select", "key": f"flags:{flag_id}"})
    _ui({"action": "message", "text": f"Cursor flagged {flag_id}: {note}" if note else f"Cursor flagged {flag_id}"})
    flag = next(f for f in state["flags"] if f["id"] == flag_id)
    return _flag_view(state.get("plan"), flag)


@mcp.tool()
def update_flag(flag_id: str, note: str | None = None, done: bool | None = None, x: float | None = None, y: float | None = None) -> dict:
    """Change a flag's note, mark it done (handled) or not, or move it to (x, y) inches."""
    body: dict[str, Any] = {"action": "update", "id": flag_id, "note": note, "done": done}
    if x is not None and y is not None:
        body.update(x=x, y=y)
    state = _post("/api/flags", body)
    flag = next(f for f in state["flags"] if f["id"] == state.get("flag"))
    if done:
        _ui({"action": "message", "text": f"Cursor: {flag['id']} done"})
    return _flag_view(state.get("plan"), flag)


@mcp.tool()
def delete_flags(ids: list[str] | None = None, all_done: bool = False) -> dict:
    """Remove flags by id, or every flag marked done."""
    if all_done:
        state = _post("/api/flags", {"action": "clear_done"})
    elif ids:
        state = _post("/api/flags", {"action": "delete", "ids": ids})
    else:
        raise ToolError("give ids or all_done=true")
    return {"remaining": [f["id"] for f in state.get("flags") or []]}


# ---------------------------------------------------------------- the browser


@mcp.tool()
def control_browser(
    action: Literal["open", "select", "zoom", "fit", "tool", "view", "message", "reload"],
    kind: Literal["walls", "openings", "counters", "fixtures", "labels", "erase", "flags"] | None = None,
    id: str | int | None = None,
    region: list[float] | None = None,
    tool: Literal["select", "wall", "door", "window", "counter", "polygon", "fixture", "label", "erase", "calibrate", "flag"]
    | None = None,
    sheet: bool | None = None,
    erase: bool | None = None,
    opacity: float | None = None,
    panel: bool | None = None,
    snap: bool | None = None,
    grid: bool | None = None,
    text: str | None = None,
) -> dict:
    """Drive the open editor page.

    open: open the editor in the default browser. select: select kind/id (or clear with no id);
    kind 'flags' with id F3 selects a flag. zoom: zoom to kind/id, or to region [x0, y0, x1, y1]
    inches. fit: show the whole sheet.
    tool: switch the active tool. view: toggle sheet, erase regions, snapping, the 1" grid, the
    side panel, or set the sheet opacity (0-1). message: show text in the status bar.
    reload: reload the page.
    """
    if action == "open":
        webbrowser.open(URL + "/")
        return {"opened": URL + "/"}
    command: dict[str, Any] = {"action": action}
    if action in ("select", "zoom") and kind and id is not None:
        command["key"] = f"{kind}:{id}"
    if action == "zoom" and region:
        command["box"] = region
    if action == "zoom" and "key" not in command and "box" not in command:
        raise ToolError("zoom needs kind and id, or a region")
    if action == "tool":
        if not tool:
            raise ToolError("action 'tool' needs tool")
        command["tool"] = tool
    if action == "view":
        for key, value in (("sheet", sheet), ("erase", erase), ("opacity", opacity), ("panel", panel), ("snap", snap), ("grid", grid)):
            if value is not None:
                command[key] = value
    if action == "message":
        command["text"] = text or ""
    browsers = _ui(command)
    result: dict[str, Any] = {"sent": command, "browsers_connected": browsers}
    if browsers == 0:
        result["note"] = "No browser has the editor open; use action 'open'."
    return result


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp.run()


if __name__ == "__main__":
    main()
