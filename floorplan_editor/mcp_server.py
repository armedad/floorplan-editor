"""MCP server that lets an agent drive the floorplan editor.

It talks to the editor's HTTP API, so every change lands in the same in-memory
document the browser shows, and an open browser follows along live. If no editor is
running, `start_editor` launches `floorplan serve` in the background.

Run it with `floorplan mcp` (stdio). Set FLOORPLAN_URL to use a port other than 35667.
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

from .engine.analyze import object_box, union_box
from .engine.geometry import along, distance_to_wall, format_length, project, wall_length
from .engine.model import FIXTURE_TYPES, ID_KEYS, LIST_KEYS, comments_of

URL = os.environ.get("FLOORPLAN_URL", "http://127.0.0.1:35667").rstrip("/")
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
        "view_image(flag=...) to look. When they say 'this' or 'what I selected', call get_selection. "
        "Use find_objects to locate things by position, and view_image to look at the sheet, overlay, "
        "or plan (crop with a region in inches; cropped views label every object's id, so you can name "
        "them). Objects are referenced by id (w12, toilet-1); labels as labels:<index>. "
        "A traced plan is mostly walls: convert_objects turns traced pieces into doors, windows, "
        "fixtures, counters, labels, or erase regions; merge_walls and find_stacked_walls join thin "
        "bars that are one thick wall; find_stray_bits finds traced letters; set_wall_thickness sets "
        "thickness keeping a chosen face. Edits stay in memory until save; save mode 'step' writes the "
        "next plan-stepN.json. The user's browser, if open, updates live. Mark a flag done with "
        "update_flag once it's handled. "
        "Objects can carry comments (c1, c2, ...) that the user or you wrote about them; they live in "
        "the plan with their object. list_comments reads them (all, or on given objects or the "
        "selection); add_comment, update_comment, and delete_comments change them. When the user asks "
        "what's noted on something, or to handle the comments, call list_comments."
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


def _note(text: str, ref: list | None = None, refs: list | None = None) -> None:
    if refs:
        _ui({"action": "select", "keys": [f"{k}:{i}" for k, i in refs]})
    elif ref:
        _ui({"action": "select", "key": f"{ref[0]}:{ref[1]}"})
    _ui({"action": "message", "text": f"Cursor: {text}"})


def _selection_keys() -> list[str]:
    try:
        return list(_get("/api/selection").get("keys") or [])
    except ToolError:
        return []


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
    comments = sum(1 for _ in comments_of(plan)) if plan else 0
    if comments:
        out["comments"] = comments
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
    _note(note, state.get("ref"), state.get("refs"))
    summary = _summary(state)
    if state.get("refs") and state.get("plan"):
        summary["touched_all"] = [{"kind": k, "id": i} for k, i in state["refs"]]
    return summary


# ---------------------------------------------------------------- object geometry


def resolve(plan: dict, refs: list) -> list[list]:
    """[kind, id] pairs for references given as ids ("w12"), keys ("walls:w12", "labels:3"), or [kind, id].
    Ids are unique across kinds, so a bare id is enough for everything but labels."""
    out = []
    for ref in refs:
        if isinstance(ref, (list, tuple)) and len(ref) == 2:
            kind, rid = str(ref[0]), ref[1]
        elif isinstance(ref, str) and ":" in ref:
            kind, _, rid = ref.partition(":")
        else:
            rid = ref
            kind = next((k for k in ID_KEYS if any(o.get("id") == rid for o in plan.get(k, []))), None)
            if kind is None:
                raise ToolError(f"no object with id {rid!r} (labels are referenced as labels:<index>)")
        if kind == "labels":
            rid = int(rid)
        if kind not in LIST_KEYS or _lookup(plan, kind, rid) is None:
            raise ToolError(f"no {kind} {rid!r}")
        out.append([kind, rid])
    return out


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
        port = httpx.URL(URL).port or 35667
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


HIGHLIGHT_RGB = (21, 101, 192)


def draw_ids(
    image: PILImage.Image, plan: dict, crop: tuple[int, int, int, int], text_px: float, marked: set[str], *, label: bool = True
) -> int:
    """Write each object's id at its middle (a wall's at its midpoint), and outline the `marked`
    keys (kind:id) in blue. Only objects inside `crop` (sheet pixels) are drawn. Returns how many."""
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.load_default(size=max(8, round(text_px)))
    except TypeError:
        font = ImageFont.load_default()
    canvas = plan["canvas"]
    scale, (ox, oy) = float(canvas["px_per_inch"]), canvas["origin_px"]

    def to_px(x: float, y: float) -> tuple[float, float]:
        return ox + x * scale, oy + y * scale

    n = 0
    line = max(2, round(text_px / 6))
    for kind in LIST_KEYS:
        for index, obj in enumerate(plan.get(kind, [])):
            ref = index if kind == "labels" else obj.get("id")
            box = object_box(plan, kind, obj)
            if box is None:
                continue
            bx0, by0 = to_px(box[0], box[1])
            bx1, by1 = to_px(box[2], box[3])
            if bx1 < crop[0] or bx0 > crop[2] or by1 < crop[1] or by0 > crop[3]:
                continue
            if f"{kind}:{ref}" in marked:
                draw.rectangle([bx0 - line, by0 - line, bx1 + line, by1 + line], outline=HIGHLIGHT_RGB + (255,), width=line)
            if not label:
                continue
            if kind == "walls":
                cx, cy = to_px((float(obj["x1"]) + float(obj["x2"])) / 2, (float(obj["y1"]) + float(obj["y2"])) / 2)
            else:
                cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
            text = f"label {ref}" if kind == "labels" else str(ref)
            left, top, right, bottom = draw.textbbox((cx, cy), text, font=font, anchor="mm")
            draw.rectangle([left - 2, top - 1, right + 2, bottom + 1], fill=(255, 255, 255, 215))
            draw.text((cx, cy), text, fill=HIGHLIGHT_RGB if f"{kind}:{ref}" in marked else (20, 20, 20), font=font, anchor="mm")
            n += 1
    return n


COMMENT_RGB = (106, 27, 154)


def draw_comments(image: PILImage.Image, plan: dict, crop: tuple[int, int, int, int], text_px: float) -> int:
    """Tag each commented object at its top right with its comment ids. Returns how many objects."""
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=max(8, round(text_px)))
    except TypeError:
        font = ImageFont.load_default()
    canvas = plan["canvas"]
    scale, (ox, oy) = float(canvas["px_per_inch"]), canvas["origin_px"]
    tagged: dict[tuple[str, Any], list[str]] = {}
    owners: dict[tuple[str, Any], dict] = {}
    for kind, ref, obj, comment in comments_of(plan):
        tagged.setdefault((kind, ref), []).append(str(comment.get("id")))
        owners[(kind, ref)] = obj
    n = 0
    for (kind, ref), ids in tagged.items():
        box = object_box(plan, kind, owners[(kind, ref)])
        if box is None:
            continue
        x, y = ox + box[2] * scale, oy + box[1] * scale
        if not (crop[0] <= x <= crop[2] and crop[1] <= y <= crop[3]):
            continue
        text = ", ".join(ids)
        left, top, right, bottom = draw.textbbox((x, y), text, font=font, anchor="lb")
        draw.rectangle([left - 3, top - 2, right + 3, bottom + 2], fill=COMMENT_RGB)
        draw.text((x, y), text, fill=(255, 255, 255), font=font, anchor="lb")
        n += 1
    return n


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
    show_comments: bool = True,
    label_ids: bool | None = None,
    highlight: list[str] | None = None,
    show_selection: bool = True,
):
    """Look at the drawing. Returns a PNG.

    source: 'overlay' (the open plan in red over the sheet, with removed objects whited out),
      'sheet' (the source image; erase regions applied unless erase=false), 'plan' (the plan alone
      on white), or 'file' (a .png in the folder, named by `file`, such as plan-step1-overlay.png).
    region: [x0, y0, x1, y1] in inches to crop to, plus `padding` inches around it. Small crops
      are enlarged so details are readable.
    flag: crop around a flag (F3), or the box it marks, with 36" of room unless padding is larger.
    show_flags: draw the user's flags as orange numbered pins (gray once done).
    show_comments: tag commented objects at their top right with their comment ids, in purple.
    label_ids: write each object's id on it, so you can name objects in later calls. On by default
      when cropping (region or flag), off for the whole sheet.
    highlight: ids (or kind:id keys) to outline in blue. show_selection also outlines whatever the
      user has selected in the editor.
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
    state = _get("/api/state")
    plan = state.get("plan")
    flags = state.get("flags") or []
    if flag:
        match = next((f for f in flags if f["id"].lower() == flag.lower()), None)
        if match is None or "x" not in match:
            raise ToolError(f"no flag {flag!r} on an open plan")
        x0, y0 = match["x"], match["y"]
        region = [x0, y0, x0 + match.get("w", 0), y0 + match.get("h", 0)]
        padding = max(padding, 36)
        caption += f", around {match['id']}"
    crop = (0, 0, image.width, image.height)
    if region:
        if not plan:
            raise ToolError("cropping to a region needs an open plan for the scale")
        canvas = plan["canvas"]
        scale, (ox, oy) = float(canvas["px_per_inch"]), canvas["origin_px"]
        x0, y0, x1, y1 = (float(v) for v in region)
        crop = (
            max(0, int(ox + (min(x0, x1) - padding) * scale)),
            max(0, int(oy + (min(y0, y1) - padding) * scale)),
            min(image.width, int(math.ceil(ox + (max(x0, x1) + padding) * scale))),
            min(image.height, int(math.ceil(oy + (max(y0, y1) + padding) * scale))),
        )
        if crop[2] <= crop[0] or crop[3] <= crop[1]:
            raise ToolError("that region is off the sheet")
        caption += f", cropped to inches [{x0}, {y0}, {x1}, {y1}] with {padding}\" padding (pixels {list(crop)})"
    factor = min(max_px / max(crop[2] - crop[0], crop[3] - crop[1]), 4.0)
    if show_flags and flags:
        draw_flags(image, flags)
    if label_ids is None:
        label_ids = bool(region)
    marked: set[str] = set()
    if plan and highlight:
        marked |= {f"{k}:{i}" for k, i in resolve(plan, highlight)}
    if plan and show_selection:
        marked |= {k for k in _selection_keys() if not k.startswith("flags:")}
    if plan and (label_ids or marked):
        n = draw_ids(image, plan, crop, 11 / factor, marked, label=label_ids)
        if label_ids:
            caption += f", {n} ids labeled"
        if marked:
            caption += f", outlined in blue: {', '.join(sorted(marked))}"
    if plan and show_comments:
        n = draw_comments(image, plan, crop, 11 / factor)
        if n:
            caption += f", comment tags in purple on {n} objects"
    if region:
        image = image.crop(crop)
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


def _object_view(plan: dict, kind: str, ref, obj: dict) -> dict:
    box = object_box(plan, kind, obj)
    return {"kind": kind, "id": ref, "what": describe(plan, kind, obj), "box": [round(v, 2) for v in box] if box else None, "object": obj}


def _open_plan() -> dict:
    plan = _get("/api/state").get("plan")
    if plan is None:
        raise ToolError("No plan is open.")
    return plan


@mcp.tool()
def get_selection() -> dict:
    """What the user has selected in the editor right now: each object's kind, id, description, box
    (inches), and full JSON, plus the box around them all. Use it when the user says "this", "these",
    or "what I selected", then act on the ids (convert_objects with use_selection=true, merge_walls, ...)."""
    data = _get("/api/selection")
    plan = _open_plan()
    items = [_object_view(plan, i["kind"], i["id"], i["object"]) for i in data["items"] if i["kind"] != "flags"]
    out: dict[str, Any] = {"count": len(items), "items": items}
    flags = [i["id"] for i in data["items"] if i["kind"] == "flags"]
    if flags:
        out["flags"] = flags
    box = union_box(tuple(i["box"]) for i in items if i["box"])
    if box:
        out["box"] = [round(v, 2) for v in box]
    walls = [i["object"] for i in items if i["kind"] == "walls"]
    if len(walls) >= 2 and len(walls) == len(items):
        from .engine.analyze import parallel

        out["walls_parallel"] = all(parallel(walls[0], w) for w in walls[1:])
    if not data.get("browsers"):
        out["note"] = "No browser has the editor open, so the selection may be stale."
    elif not items and not flags:
        out["note"] = "Nothing is selected."
    return out


@mcp.tool()
def get_objects(ids: list[str]) -> dict:
    """Full JSON, a description, and the box (inches) of each object. Refer to objects by id (w12,
    toilet-1, door-3); ids are unique across kinds. Labels have no id: use labels:<index>."""
    plan = _open_plan()
    return {"objects": [_object_view(plan, k, i, _lookup(plan, k, i)) for k, i in resolve(plan, ids)]}


def _items_for(plan: dict, ids: list[str] | None, use_selection: bool) -> list[list]:
    if use_selection:
        keys = [k for k in _selection_keys() if not k.startswith("flags:")]
        if not keys:
            raise ToolError("Nothing is selected in the editor.")
        return resolve(plan, keys)
    if not ids:
        raise ToolError("give ids, or use_selection=true")
    return resolve(plan, ids)


@mcp.tool()
def convert_objects(
    to: Literal["wall", "door", "window", "fixture", "counter", "outline", "label", "erase"],
    ids: list[str] | None = None,
    use_selection: bool = False,
    fixture_type: str | None = None,
    rot: float = 0,
    text: str | None = None,
    wall: str | None = None,
    width: float | str | None = None,
    hinge: Literal["start", "end"] | None = None,
    swing: Literal["cw", "ccw"] | None = None,
    thickness: float | str | None = None,
    w: float | str | None = None,
    h: float | str | None = None,
    at: list[float] | None = None,
    keep_originals: bool = False,
) -> dict:
    """Replace objects (usually traced walls that are really something else) with the real thing,
    sized to the box around them, as one undo step. Name them by ids, or use_selection=true for
    what the user selected. The originals are removed unless keep_originals.

    to='wall': several parallel walls merge into one covering them all (thickness overrides the
      combined thickness); anything else becomes a wall along the box's long side.
    to='door'/'window': a gap between two walls in line near the objects is the doorway; the two
      walls join into one and the opening fills the gap exactly. Otherwise it goes on `wall`, or the
      nearest wall, as wide as the objects along it. `width` overrides the width; long walls in the
      selection are kept. A door's swing faces the side its drawn leaf was on unless swing is given.
      With no ids, `at` [x, y] inches finds the doorway gap at that point.
    to='fixture': fixture_type (refrigerator, range, oven, dishwasher, toilet, tub, soaking_tub, sink,
      washer, dryer, washer_dryer, stool, generic) centered on the box; rot turns it clockwise
      (90/270 swap w and h so it still fits the box); w/h override the size.
    to='counter' (a box), 'outline' (editable corners), 'label' (text, sized to the box's height),
    'erase' (white out the sheet ink there: good for text and symbols that aren't plan objects).
    Lengths accept inches or text like 2'6".
    """
    plan = _open_plan()
    items = _items_for(plan, ids, use_selection) if (ids or use_selection or not at) else []
    op: dict[str, Any] = {"op": "convert", "to": to, "keep": keep_originals}
    if items:
        op["items"] = items
    for key, value in (("type", fixture_type), ("text", text), ("wall", wall), ("width", width), ("hinge", hinge), ("swing", swing), ("thickness", thickness), ("w", w), ("h", h), ("at", at)):
        if value is not None:
            op[key] = value
    if rot:
        op["rot"] = rot
    if to == "fixture" and not fixture_type:
        raise ToolError(f"fixture_type is needed: one of {', '.join(FIXTURE_TYPES)}")
    if to == "label" and not text:
        raise ToolError("a label needs text")
    summary = _op(op, f"converted {len(items) or 'a gap'} to {fixture_type or to}")
    after = _get("/api/state").get("plan") or {}
    summary["removed"] = [i for k, i in items if _lookup(after, k, i) is None]
    return summary


@mcp.tool()
def merge_walls(ids: list[str] | None = None, use_selection: bool = False, thickness: float | str | None = None) -> dict:
    """Merge parallel walls into one wall that covers them all: thin bars side by side that are really
    one thick wall, or pieces of one wall broken in line. Keeps the longest one's id; doors and
    windows move onto it, and walls joined to the old ends meet the new one. `thickness` sets the
    result's thickness about its middle instead of the combined thickness."""
    plan = _open_plan()
    items = _items_for(plan, ids, use_selection)
    walls = [i for k, i in items if k == "walls"]
    if len(walls) != len(items):
        raise ToolError("merge_walls takes walls only; use convert_objects for other objects")
    op: dict[str, Any] = {"op": "merge_walls", "ids": walls}
    if thickness is not None:
        op["thickness"] = thickness
    return _op(op, f"merged {', '.join(walls)}")


def _in_region(box, region) -> bool:
    if not region:
        return True
    x0, y0, x1, y1 = min(region[0], region[2]), min(region[1], region[3]), max(region[0], region[2]), max(region[1], region[3])
    return not (box[2] < x0 or box[0] > x1 or box[3] < y0 or box[1] > y1)


@mcp.tool()
def find_stacked_walls(
    region: list[float] | None = None,
    ids: list[str] | None = None,
    max_gap: float = 2.0,
    apply: bool = False,
) -> dict:
    """Find walls that are really one wall: bars side by side of about the same length (a thick wall
    or chase traced as thin bars), or pieces in line with a break of at most max_gap inches. Each
    group lists its ids, why, and the merged wall it would become. Limit to a region [x0, y0, x1, y1]
    inches or to ids. apply=true merges every group found as one undo step.
    Raise max_gap (say 8) for walls drawn as two outline lines with white between them."""
    plan = _open_plan()
    body: dict[str, Any] = {"what": "stacked", "max_gap": max_gap}
    if ids:
        body["ids"] = [i for k, i in resolve(plan, ids) if k == "walls"]
    groups = _post("/api/analyze", body)["groups"]
    groups = [g for g in groups if _in_region(object_box(plan, "walls", {**g["merged"]}), region)]
    if not apply or not groups:
        return {"count": len(groups), "groups": groups}
    members = [i for g in groups for i in g["ids"]]
    summary = _op({"op": "merge_stacked", "ids": members, "max_gap": max_gap}, f"merged {len(groups)} groups of stacked walls")
    summary["merged_groups"] = len(groups)
    return summary


@mcp.tool()
def find_stray_bits(region: list[float] | None = None, max_length: float = 24, delete: bool = False) -> dict:
    """Find short, thin walls that touch no real wall: usually letters of room names and captions, or
    bits of symbols, picked up by the tracer. Blocky ones (columns) are skipped, but check each
    cluster before deleting. Clustered roughly by word or symbol, each with ids and a box.
    Look at a cluster with view_image(region=box, source='sheet') to read it, then convert_objects
    (to='label' with the text, or to='erase' to white it out), or delete=true removes them all."""
    plan = _open_plan()
    groups = [g for g in _post("/api/analyze", {"what": "stray", "max_length": max_length})["groups"] if _in_region(g["box"], region)]
    if not delete or not groups:
        return {"count": len(groups), "walls": sum(len(g["ids"]) for g in groups), "groups": groups}
    ids = [i for g in groups for i in g["ids"]]
    summary = _op({"op": "delete_many", "items": [["walls", i] for i in ids]}, f"deleted {len(ids)} stray bits")
    summary["deleted"] = ids
    return summary


@mcp.tool()
def set_wall_thickness(
    thickness: float | str,
    ids: list[str] | None = None,
    use_selection: bool = False,
    keep: Literal["center", "up", "down", "left", "right"] = "center",
) -> dict:
    """Set the thickness of walls (by ids or the user's selection). keep names the face that stays
    put, as seen on screen: up or down for walls running across, left or right for walls running
    up and down; walls running the other way keep their center. Walls joined to a moved face follow."""
    plan = _open_plan()
    walls = [i for k, i in _items_for(plan, ids, use_selection) if k == "walls"]
    if not walls:
        raise ToolError("no walls given")
    return _op({"op": "set_wall_thickness", "ids": walls, "thickness": thickness, "keep": keep}, f"set {len(walls)} walls to {thickness} thick")


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
- merge_walls: {"op":"merge_walls","ids":[WALL,...],"thickness":optional}: parallel walls become one.
- merge_stacked: {"op":"merge_stacked","ids":optional,"max_gap":2}: merge every group of walls that are one.
- set_wall_thickness: {"op":"set_wall_thickness","ids":[...],"thickness":6,"keep":"center"|"up"|"down"|"left"|"right"}
- convert: {"op":"convert","items":[[K,ID],...],"to":"wall"|"door"|"window"|"fixture"|"counter"|"outline"|"label"|"erase",
  "type":fixture type,"rot":..,"text":label,"wall":host,"width":..,"keep":false} (see convert_objects).
- add_comment: {"op":"add_comment","kind":K,"id":ID,"text":..,"by":"cursor"};
  update_comment: {"op":"update_comment","comment":"c3","text":..,"to":[K,ID]}; delete_comments: {"op":"delete_comments","comments":["c3"]}
Object fields: walls {id,x1,y1,x2,y2,thickness}; openings {id,wall,type:door|window,offset,width,
hinge:start|end,swing:cw|ccw}; counters {id,x,y,w,h,overhang?:{edge,inches}} or {id,points:[[x,y],...]};
fixtures {id,type,x,y,w,h,rot?} (x,y is the center; rot turns clockwise); labels {text,x,y,size_px?};
erase {id,x,y,w,h}. Any object may also have comments [{id,text,by,created,edited?}]. Fixture types: refrigerator, range, oven, dishwasher, toilet, tub, soaking_tub,
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
    merge_stacked: bool = True,
) -> dict:
    """Auto-trace walls from the sheet: long, solid, dark bars become walls.

    apply=false previews: returns how many walls it found (and the first few) without changing the plan.
    apply=true adds them (replace=true drops existing walls and openings first) as one undo step.
    dark: gray level (0-255) below which a pixel counts as ink. min/max_thickness_px: bar thickness
    range in pixels (raise the min to skip window and cabinet lines). min_length_px: drop shorter bars.
    merge_stacked: join bars that touch side by side (a thick wall traced as thin ones) or in line.
    Afterwards, find_stray_bits finds traced letters and symbol bits to delete or convert.
    """
    settings = {
        "dark": dark,
        "min_thickness_px": min_thickness_px,
        "max_thickness_px": max_thickness_px,
        "min_length_px": min_length_px,
        "merge": merge_stacked,
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


# ---------------------------------------------------------------- comments


def _comment_view(plan: dict, kind: str, ref, obj: dict, comment: dict) -> dict:
    out = {k: comment[k] for k in ("id", "text", "by", "created", "edited") if k in comment}
    out["on"] = {"kind": kind, "id": ref, "what": describe(plan, kind, obj)}
    return out


def _find_comment(plan: dict, comment_id: str) -> dict:
    wanted = str(comment_id).strip().lower()
    for kind, ref, obj, comment in comments_of(plan):
        if str(comment.get("id")).lower() == wanted:
            return _comment_view(plan, kind, ref, obj, comment)
    raise ToolError(f"no comment {comment_id!r}")


def _one_object(plan: dict, on: str | None, use_selection: bool) -> list:
    items = _items_for(plan, [on] if on else None, use_selection)
    if len(items) != 1:
        raise ToolError(f"a comment goes on one object; {len(items)} are selected")
    return items[0]


@mcp.tool()
def list_comments(
    on: list[str] | None = None,
    use_selection: bool = False,
    ids: list[str] | None = None,
    text: str | None = None,
    by: str | None = None,
) -> dict:
    """Comments attached to objects in the open plan: each comment's id (c1, c2, ...), text, author
    (user or cursor), created and edited times, and the object it's on (kind, id, description).

    on: only comments on these objects (w12, toilet-1, labels:3). use_selection: only comments on what
    the user has selected. ids: these comment ids. text: comments containing these words. by: 'user'
    or 'cursor'. With no filters, every comment in the plan."""
    plan = _open_plan()
    keys = None
    if on or use_selection:
        keys = {(k, i) for k, i in _items_for(plan, on, use_selection)}
    wanted = {str(i).lower() for i in ids} if ids else None
    out = []
    for kind, ref, obj, comment in comments_of(plan):
        if keys is not None and (kind, ref) not in keys:
            continue
        if wanted is not None and str(comment.get("id")).lower() not in wanted:
            continue
        if text and text.lower() not in str(comment.get("text", "")).lower():
            continue
        if by and comment.get("by") != by:
            continue
        out.append(_comment_view(plan, kind, ref, obj, comment))
    return {"count": len(out), "comments": out}


@mcp.tool()
def add_comment(text: str, on: str | None = None, use_selection: bool = False) -> dict:
    """Attach a comment to one object: `on` names it (w12, toilet-1, labels:3), or use_selection for the
    one object the user has selected. The comment shows in the user's editor, marked as from Cursor.
    Comments are part of the plan: they move, merge, and convert with their object, are deleted with
    it, and are undoable. Like other edits they stay in memory until save."""
    plan = _open_plan()
    kind, ref = _one_object(plan, on, use_selection)
    summary = _op({"op": "add_comment", "kind": kind, "id": ref, "text": text, "by": "cursor"}, f"commented on {ref}")
    after = _open_plan()
    obj = _lookup(after, kind, ref)
    summary["comment"] = _comment_view(after, kind, ref, obj, obj["comments"][-1])
    return summary


@mcp.tool()
def update_comment(comment_id: str, text: str | None = None, move_to: str | None = None) -> dict:
    """Change a comment's text, or move it to another object (move_to: w12, toilet-1, labels:3)."""
    if text is None and not move_to:
        raise ToolError("give text or move_to")
    plan = _open_plan()
    op: dict[str, Any] = {"op": "update_comment", "comment": comment_id}
    if text is not None:
        op["text"] = text
    if move_to:
        op["to"] = resolve(plan, [move_to])[0]
    summary = _op(op, f"updated comment {comment_id}")
    summary["comment"] = _find_comment(_open_plan(), comment_id)
    return summary


@mcp.tool()
def delete_comments(ids: list[str]) -> dict:
    """Delete comments by id (c1, c2, ...), as one undo step."""
    if not ids:
        raise ToolError("ids is empty")
    summary = _op({"op": "delete_comments", "comments": ids}, f"deleted comment{'s' if len(ids) > 1 else ''} {', '.join(ids)}")
    summary["deleted"] = ids
    return summary


# ---------------------------------------------------------------- the browser


@mcp.tool()
def control_browser(
    action: Literal["open", "select", "zoom", "fit", "tool", "view", "message", "reload"],
    kind: Literal["walls", "openings", "counters", "fixtures", "labels", "erase", "flags"] | None = None,
    id: str | int | None = None,
    ids: list[str] | None = None,
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

    open: open the editor in the default browser. select: select kind/id, or several objects with
    ids (w12, labels:3, ...), to show the user what you mean (or clear with neither); kind 'flags'
    with id F3 selects a flag. zoom: zoom to kind/id, to ids, or to region [x0, y0, x1, y1]
    inches. fit: show the whole sheet.
    tool: switch the active tool. view: toggle sheet, erase regions, snapping, the 1" grid, the
    side panel, or set the sheet opacity (0-1). message: show text in the status bar.
    reload: reload the page.
    """
    if action == "open":
        webbrowser.open(URL + "/")
        return {"opened": URL + "/"}
    command: dict[str, Any] = {"action": action}
    if action in ("select", "zoom") and ids:
        command["keys"] = [f"{k}:{i}" for k, i in resolve(_open_plan(), ids)]
        command["zoom"] = action == "zoom"
        command["action"] = "select"
    elif action in ("select", "zoom") and kind and id is not None:
        command["key"] = f"{kind}:{id}"
    if action == "zoom" and region and "keys" not in command:
        command["box"] = region
    if action == "zoom" and not {"key", "keys", "box"} & command.keys():
        raise ToolError("zoom needs kind and id, ids, or a region")
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
