"""FastAPI app behind `floorplan serve`.

The server holds one open document: a plan folder, the plan file being edited, the
plan in memory, and undo/redo snapshots. The browser sends edit operations as JSON;
the engine applies them and the server answers with the new plan and its scene.

Other clients (the MCP server) use the same API. Every change bumps `version`, and
`/api/events` streams version bumps and UI commands so an open browser follows along.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import re
import shutil
import threading
import webbrowser
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from ..engine import PlanError, draw_png, load_plan, scene, validate_plan
from ..engine.edit import EditError, apply
from ..engine.model import FIXTURE_TYPES, compass_from_up
from ..engine.overlay import apply_erase_regions, overlay_image
from ..engine.steps import (
    BASE_NAME,
    IMAGE_SUFFIXES,
    MERGED_NAME,
    PLAN_RE,
    STEP_RE,
    find_sheet,
    list_plans,
    load_base,
    merge_folder,
    next_step_name,
    write_outputs,
)
from ..engine.trace import TraceSettings, trace_walls

WEB = Path(__file__).resolve().parent.parent / "web"
UNDO_LIMIT = 200
UI_LOG_LIMIT = 50
SAFE_NAME = re.compile(r"^[\w.() -]+$")
UI_ACTIONS = {"select", "zoom", "fit", "tool", "view", "message", "reload"}


class Document:
    def __init__(self, folder: Path, sheet: Path | None = None):
        self.lock = threading.RLock()
        self.version = 0
        self.ui_log: list[tuple[int, dict]] = []
        self.ui_seq = 0
        self.browsers = 0
        self.open_folder(folder, sheet)

    def bump(self) -> None:
        self.version += 1

    def post_ui(self, command: dict) -> int:
        self.ui_seq += 1
        self.ui_log.append((self.ui_seq, command))
        del self.ui_log[:-UI_LOG_LIMIT]
        return self.ui_seq

    # ------------------------------------------------------------ folder and files

    def open_folder(self, folder: Path, sheet: Path | None = None) -> None:
        folder = Path(folder).expanduser().resolve()
        if not folder.is_dir():
            raise HTTPException(400, f"{folder} is not a folder")
        self.folder = folder
        self.sheet_override = Path(sheet).resolve() if sheet else None
        self.file: str | None = None
        self.plan: dict | None = None
        self.saved: dict | None = None
        self.undo: list[dict] = []
        self.redo: list[dict] = []
        self.bump()
        plans = list_plans(folder)
        steps = [name for name in plans if STEP_RE.match(name)]
        start = steps[-1] if steps else (BASE_NAME if BASE_NAME in plans else (plans[-1] if plans else None))
        if start:
            self.open_file(start)

    def open_file(self, name: str) -> None:
        path = self._path(name)
        if not path.exists():
            raise HTTPException(404, f"{name} not found")
        try:
            plan = load_plan(path)
        except PlanError as exc:
            raise HTTPException(400, {"errors": exc.errors}) from exc
        self.file, self.plan, self.saved = name, plan, copy.deepcopy(plan)
        self.undo, self.redo = [], []
        self.bump()

    def _path(self, name: str) -> Path:
        if not name or not SAFE_NAME.match(name) or name.startswith("."):
            raise HTTPException(400, f"bad file name {name!r}")
        return self.folder / name

    @property
    def sheet(self) -> Path | None:
        return find_sheet(self.folder, self.sheet_override)

    def sheet_info(self) -> dict | None:
        sheet = self.sheet
        if not sheet or not sheet.exists():
            return None
        with Image.open(sheet) as image:
            width, height = image.size
        matches = bool(self.plan) and (width, height) == (
            int(round(self.plan["canvas"]["width_px"])),
            int(round(self.plan["canvas"]["height_px"])),
        )
        return {
            "name": sheet.name,
            "path": str(sheet),
            "width": width,
            "height": height,
            "matches": matches,
            "mtime": sheet.stat().st_mtime_ns,
        }

    # ------------------------------------------------------------ editing

    def require_plan(self) -> dict:
        if self.plan is None:
            raise HTTPException(400, "no plan is open")
        return self.plan

    def apply_op(self, op: dict):
        plan = self.require_plan()
        try:
            new, ref = apply(plan, op)
            scene(new)
        except EditError as exc:
            raise HTTPException(400, str(exc)) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, f"{op.get('op')}: {exc}") from exc
        self.undo.append(plan)
        del self.undo[:-UNDO_LIMIT]
        self.redo.clear()
        self.plan = new
        self.bump()
        return ref

    def step_back(self, forward: bool) -> bool:
        source, target = (self.redo, self.undo) if forward else (self.undo, self.redo)
        if not source:
            return False
        target.append(self.plan)
        self.plan = source.pop()
        self.bump()
        return True

    # ------------------------------------------------------------ state

    def state(self, **extra) -> dict:
        plan = self.plan
        errors: list[str] = []
        ops: list[dict] = []
        if plan is not None:
            errors = validate_plan(plan)
            try:
                ops = scene(plan)
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"cannot draw the plan: {exc}")
        return {
            "version": self.version,
            "folder": str(self.folder),
            "file": self.file,
            "files": list_plans(self.folder),
            "next_step": next_step_name(self.folder),
            "plan": plan,
            "scene": ops,
            "errors": errors,
            "dirty": plan is not None and plan != self.saved,
            "can_undo": bool(self.undo),
            "can_redo": bool(self.redo),
            "sheet": self.sheet_info(),
            "has_base": (self.folder / BASE_NAME).exists(),
            "fixture_types": list(FIXTURE_TYPES),
            "browsers": self.browsers,
            **extra,
        }


def _settings(body: dict) -> TraceSettings:
    raw = body.get("settings") or {}
    defaults = TraceSettings()
    return TraceSettings(
        dark=int(raw.get("dark", defaults.dark)),
        min_thickness_px=int(raw.get("min_thickness_px", defaults.min_thickness_px)),
        max_thickness_px=int(raw.get("max_thickness_px", defaults.max_thickness_px)),
        min_length_px=int(raw.get("min_length_px", defaults.min_length_px)),
    )


def _png(image: Image.Image) -> Response:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return Response(buffer.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


def create_app(folder: Path, sheet: Path | None = None) -> FastAPI:
    app = FastAPI(title="floorplan-editor")
    doc = Document(folder, sheet)
    app.state.doc = doc

    @app.middleware("http")
    async def no_cache_static(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/state")
    def get_state():
        with doc.lock:
            return doc.state()

    @app.post("/api/folder")
    def open_folder(body: dict = Body(...)):
        with doc.lock:
            doc.open_folder(Path(body["path"]), body.get("sheet") or None)
            return doc.state()

    @app.post("/api/open")
    def open_file(body: dict = Body(...)):
        with doc.lock:
            doc.open_file(body["file"])
            return doc.state()

    @app.post("/api/revert")
    def revert():
        with doc.lock:
            if doc.file:
                doc.open_file(doc.file)
            return doc.state()

    @app.post("/api/op")
    def edit(body: dict = Body(...)):
        with doc.lock:
            ref = doc.apply_op(body["op"])
            return doc.state(ref=ref)

    @app.post("/api/undo")
    def undo():
        with doc.lock:
            doc.step_back(forward=False)
            return doc.state()

    @app.post("/api/redo")
    def redo():
        with doc.lock:
            doc.step_back(forward=True)
            return doc.state()

    @app.post("/api/save")
    def save(body: dict = Body(default={})):
        with doc.lock:
            plan = doc.require_plan()
            mode = body.get("mode", "overwrite")
            if mode == "step":
                name = next_step_name(doc.folder)
            elif mode == "as":
                name = str(body.get("name", "")).strip()
                if not name.endswith(".json"):
                    name += ".json"
                if not PLAN_RE.match(name):
                    raise HTTPException(400, "plan files are named plan.json or plan-<something>.json")
            else:
                name = doc.file or BASE_NAME
            errors = validate_plan(plan)
            if errors:
                raise HTTPException(400, {"errors": errors})
            base = load_base(doc.folder)
            result = write_outputs(plan, doc._path(name), doc.sheet, base)
            doc.file, doc.saved = name, copy.deepcopy(plan)
            doc.bump()
            return doc.state(saved={"files": result.files, "warnings": result.warnings})

    @app.post("/api/merge")
    def merge(body: dict = Body(...)):
        with doc.lock:
            base_name = body.get("base", BASE_NAME)
            steps = list(body.get("steps") or [])
            out = body.get("out", MERGED_NAME)
            if not steps:
                raise HTTPException(400, "pick at least one step to merge")
            for name in [base_name, out, *steps]:
                doc._path(name)
            try:
                _, result = merge_folder(doc.folder, base_name, steps, doc.sheet, out)
            except PlanError as exc:
                raise HTTPException(400, {"errors": exc.errors}) from exc
            if body.get("open", True):
                doc.open_file(out)
            else:
                doc.bump()
            return doc.state(saved={"files": result.files, "warnings": result.warnings})

    @app.get("/api/sheet")
    def sheet_image(erase: bool = True):
        with doc.lock:
            sheet = doc.sheet
            if not sheet or not sheet.exists():
                raise HTTPException(404, "no sheet image in this folder")
            with Image.open(sheet) as raw:
                image = raw.convert("RGB")
            if erase and doc.plan is not None:
                image = apply_erase_regions(image, doc.plan)
            return _png(image)

    @app.get("/api/overlay")
    def overlay():
        with doc.lock:
            plan = doc.require_plan()
            sheet = doc.sheet
            if not sheet:
                raise HTTPException(404, "no sheet image in this folder")
            base = load_base(doc.folder) if doc.file != BASE_NAME else None
            try:
                return _png(overlay_image(plan, sheet, base))
            except PlanError as exc:
                raise HTTPException(400, {"errors": exc.errors}) from exc

    @app.get("/api/file/{name}")
    def folder_file(name: str):
        with doc.lock:
            path = doc._path(name)
            if not path.is_file():
                raise HTTPException(404, f"{name} not found")
            return FileResponse(path, headers={"Cache-Control": "no-store"})

    @app.post("/api/new")
    def new_plan(body: dict = Body(...)):
        """Start a plan sized to a sheet image: an upload, a path, or the folder's sheet."""
        with doc.lock:
            name = body.get("name") or BASE_NAME
            if not PLAN_RE.match(name):
                raise HTTPException(400, "plan files are named plan.json or plan-<something>.json")
            if doc._path(name).exists() and not body.get("overwrite"):
                raise HTTPException(409, f"{name} already exists")
            upload = body.get("image")
            if upload:
                suffix = Path(upload["filename"]).suffix.lower()
                if suffix not in IMAGE_SUFFIXES:
                    raise HTTPException(400, "the sheet must be a .png or .jpg")
                target = doc.folder / f"sheet{suffix}"
                n = 2
                while target.exists():
                    target = doc.folder / f"sheet-{n}{suffix}"
                    n += 1
                target.write_bytes(base64.b64decode(upload["data"]))
                doc.sheet_override = target
            elif body.get("image_path"):
                doc.sheet_override = Path(body["image_path"]).expanduser().resolve()
            sheet = doc.sheet
            if not sheet or not sheet.exists():
                raise HTTPException(400, "choose a sheet image first")
            with Image.open(sheet) as image:
                width, height = image.size
            plan = {
                "name": body.get("title") or doc.folder.name,
                "units": "inches",
                "compass": compass_from_up(body.get("up", "north")),
                "canvas": {
                    "width_px": width,
                    "height_px": height,
                    "px_per_inch": float(body.get("px_per_inch") or 1.0),
                    "origin_px": [0, 0],
                },
            }
            doc.file, doc.plan, doc.saved = name, plan, None
            doc.undo, doc.redo = [], []
            doc.bump()
            return doc.state()

    @app.post("/api/trace")
    def trace(body: dict = Body(default={})):
        with doc.lock:
            plan = doc.require_plan()
            sheet = doc.sheet
            if not sheet:
                raise HTTPException(400, "no sheet image to trace")
            canvas = plan["canvas"]
            walls = trace_walls(
                sheet,
                _settings(body),
                px_per_inch=float(canvas["px_per_inch"]),
                origin=tuple(canvas["origin_px"]),
            )
            if body.get("preview", True):
                return {"walls": walls}
            doc.apply_op({"op": "add_walls", "walls": walls, "replace": bool(body.get("replace"))})
            return doc.state(traced=len(walls))

    @app.get("/api/render")
    def render_plan():
        """The open plan drawn on white, without the sheet."""
        with doc.lock:
            plan = doc.require_plan()
            try:
                image = draw_png(plan, scene(plan), (255, 255, 255, 255)).convert("RGB")
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(400, f"cannot draw the plan: {exc}") from exc
            return _png(image)

    @app.post("/api/import")
    def import_file(body: dict = Body(...)):
        """Copy a plan JSON or a sheet image from anywhere on disk into the folder."""
        with doc.lock:
            source = Path(str(body.get("path") or "")).expanduser()
            if not body.get("path"):
                raise HTTPException(400, "path is required")
            if not source.is_file():
                raise HTTPException(404, f"{source} is not a file")
            suffix = source.suffix.lower()
            overwrite = bool(body.get("overwrite"))
            if suffix == ".json":
                try:
                    load_plan(source)
                except PlanError as exc:
                    raise HTTPException(400, {"errors": exc.errors}) from exc
                name = body.get("name") or (source.name if PLAN_RE.match(source.name) else f"plan-{source.stem}.json")
                if not PLAN_RE.match(name):
                    raise HTTPException(400, "plan files are named plan.json or plan-<something>.json")
            elif suffix in IMAGE_SUFFIXES:
                name = body.get("name") or source.name
                if Path(name).suffix.lower() != suffix or name.lower().startswith("plan"):
                    raise HTTPException(400, f"image names keep their {suffix} suffix and can't start with 'plan'")
            else:
                raise HTTPException(400, "import a plan .json or a .png/.jpg sheet")
            target = doc._path(name)
            if target.resolve() != source.resolve():
                if target.exists() and not overwrite:
                    raise HTTPException(409, f"{name} already exists in the folder")
                shutil.copyfile(source, target)
            if suffix == ".json":
                if body.get("open", True):
                    doc.open_file(name)
                else:
                    doc.bump()
            else:
                if body.get("sheet", True):
                    doc.sheet_override = target
                doc.bump()
            return doc.state(imported=name)

    @app.post("/api/ui")
    def ui(body: dict = Body(...)):
        """Queue a command for open browsers: select, zoom, fit, tool, view, message, reload."""
        if body.get("action") not in UI_ACTIONS:
            raise HTTPException(400, f"action must be one of {sorted(UI_ACTIONS)}")
        with doc.lock:
            seq = doc.post_ui(body)
            return {"seq": seq, "browsers": doc.browsers}

    @app.get("/api/events")
    async def events(request: Request):
        async def stream():
            doc.browsers += 1
            try:
                version, seq = doc.version, doc.ui_seq
                yield f"event: state\ndata: {json.dumps({'version': version})}\n\n"
                idle = 0.0
                while not await request.is_disconnected():
                    if doc.version != version:
                        version = doc.version
                        yield f"event: state\ndata: {json.dumps({'version': version})}\n\n"
                        idle = 0.0
                    for n, command in list(doc.ui_log):
                        if n > seq:
                            seq = n
                            yield f"event: ui\ndata: {json.dumps(command)}\n\n"
                            idle = 0.0
                    if idle >= 15:
                        yield ": keep-alive\n\n"
                        idle = 0.0
                    await asyncio.sleep(0.2)
                    idle += 0.2
            finally:
                doc.browsers -= 1

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    @app.post("/api/shutdown")
    def shutdown():
        server = getattr(app.state, "server", None)
        if server is None:
            raise HTTPException(400, "this editor wasn't started by `floorplan serve`")
        server.should_exit = True
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app


def serve(folder: Path, *, sheet: Path | None = None, port: int = 8765, open_browser: bool = True) -> None:
    import uvicorn

    app = create_app(Path(folder), sheet)
    url = f"http://127.0.0.1:{port}/"
    print(f"floorplan editor: {url}  (folder {app.state.doc.folder})  Ctrl+C to stop", flush=True)
    if open_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=1)
    server = uvicorn.Server(config)
    app.state.server = server
    server.run()
