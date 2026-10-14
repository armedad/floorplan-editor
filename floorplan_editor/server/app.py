"""FastAPI app behind `floorplan serve`.

The server holds one open document: a plan folder, the plan file being edited, the
plan in memory, and undo/redo snapshots. The browser sends edit operations as JSON;
the engine applies them and the server answers with the new plan and its scene.

Other clients (the MCP server) use the same API. Every change bumps `version`, and
`/api/events` streams version bumps and UI commands so an open browser follows along.

Opening is one step for any file: a folder, a plan JSON, or a floor plan image, picked
on disk or dropped in the browser. An image nobody has planned yet becomes a new
project in the workspace, traced and scaled automatically.
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

from ..engine import PlanError, checked, draw_png, is_building, load_plan, scene, validate_plan
from ..engine.analyze import lookup, stray_bits, wall_groups
from ..engine.building import FIRST_FLOOR, as_building, export_image, floor_by_id, floor_label, floors_of, next_floor_id
from ..engine.edit import EditError, apply
from ..engine.model import FIXTURE_TYPES, LIST_KEYS, compass_from_up
from ..engine.overlay import apply_erase_regions, overlay_image
from ..engine.steps import (
    BASE_NAME,
    IMAGE_SUFFIXES,
    MERGED_NAME,
    PLAN_RE,
    find_sheet,
    list_plans,
    load_base,
    merge_folder,
    newest_plan,
    next_step_name,
    write_outputs,
)
from ..engine.trace import TraceSettings, estimate_px_per_inch, trace_walls
from .flags import FlagError, FlagStore, flags_name
from .projects import (
    PickerUnavailable,
    is_image,
    native_pick,
    plans_matching,
    project_with_sheet,
    recent_folders,
    remember,
    slug,
    unique_folder,
    workspace,
)

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
        self.selection: list[str] = []
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
        # The whole file: one plan, or a building whose `floors` are plans. `floor` is the one being edited.
        self.data: dict | None = None
        self.floor = 0
        self.saved: dict | None = None
        self.undo: list[tuple[dict, int]] = []
        self.redo: list[tuple[dict, int]] = []
        self.flags = FlagStore(folder)
        self.bump()
        remember(folder)
        start = newest_plan(folder)
        if start:
            self.open_file(start)

    def open_file(self, name: str) -> None:
        path = self._path(name)
        if not path.exists():
            raise HTTPException(404, f"{name} not found")
        try:
            data = load_plan(path)
        except PlanError as exc:
            raise HTTPException(400, {"errors": exc.errors}) from exc
        self.file, self.data, self.saved = name, data, copy.deepcopy(data)
        self.floor = min(self.floor, len(floors_of(data)) - 1)
        self.undo, self.redo = [], []
        self._sync_flags()
        self.bump()

    @property
    def plan(self) -> dict | None:
        """The floor being edited (the whole plan, for a one-floor file)."""
        if self.data is None:
            return None
        return self.data["floors"][self.floor] if is_building(self.data) else self.data

    def _with_plan(self, plan: dict) -> dict:
        if not is_building(self.data):
            return plan
        floors = list(self.data["floors"])
        floors[self.floor] = plan
        return {**self.data, "floors": floors}

    def _sync_flags(self) -> None:
        plan = self.plan
        name = flags_name(plan.get("id") if plan is not None and is_building(self.data) else None)
        if self.flags.path != self.folder / name:
            self.flags = FlagStore(self.folder, name)

    def _change(self, data: dict, floor: int | None = None) -> None:
        """Replace the whole file as one undoable step, optionally moving to another floor."""
        self.undo.append((self.data, self.floor))
        del self.undo[:-UNDO_LIMIT]
        self.redo.clear()
        self.data = data
        if floor is not None:
            self.floor = floor
        self.floor = max(0, min(self.floor, len(floors_of(data)) - 1))
        self._sync_flags()
        self.bump()

    def _path(self, name: str) -> Path:
        if not name or not SAFE_NAME.match(name) or name.startswith("."):
            raise HTTPException(400, f"bad file name {name!r}")
        return self.folder / name

    @property
    def sheet(self) -> Path | None:
        plan = self.plan
        if plan is not None and plan.get("sheet"):
            return self.folder / plan["sheet"]
        if is_building(self.data):
            return None
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

    # ------------------------------------------------------------ new plans and opening anything

    def start_plan(self, name: str = BASE_NAME, *, up: str = "north", px_per_inch=None, title=None, auto=False) -> dict:
        """A plan sized to the sheet. `auto` traces the walls and guesses the scale from their thickness."""
        sheet = self.sheet
        if not sheet or not sheet.exists():
            raise HTTPException(400, "choose a sheet image first")
        with Image.open(sheet) as image:
            width, height = image.size
        scale = float(px_per_inch or 1.0)
        self.file, self.saved = name, None
        self.data = {
            "name": title or self.folder.name,
            "units": "inches",
            "compass": compass_from_up(up),
            "canvas": {"width_px": width, "height_px": height, "px_per_inch": scale, "origin_px": [0, 0]},
        }
        self.floor = 0
        self.undo, self.redo = [], []
        self._sync_flags()
        self.bump()
        if not auto:
            return {}
        walls = trace_walls(sheet, TraceSettings(), px_per_inch=scale, origin=(0, 0))
        info: dict = {"traced": len(walls)}
        ops: list[dict] = [{"op": "add_walls", "walls": walls}] if walls else []
        guess = None if px_per_inch else estimate_px_per_inch(walls, scale)
        if guess:
            ops.append({"op": "calibrate", "p1": [0, 0], "p2": [guess, 0], "length": 1})
            info["estimated_px_per_inch"] = guess
        if ops:
            self.apply_op({"op": "batch", "ops": ops})
            self.undo = []
        return info

    def save_plan(self, mode: str = "overwrite", name: str | None = None) -> dict:
        plan = self.require_plan()
        if mode == "step":
            name = next_step_name(self.folder)
        elif mode == "as":
            name = str(name or "").strip()
            if not name.endswith(".json"):
                name += ".json"
            if not PLAN_RE.match(name):
                raise HTTPException(400, "plan files are named plan.json or plan-<something>.json")
        else:
            name = self.file or BASE_NAME
        errors = validate_plan(self.data)
        if errors:
            raise HTTPException(400, {"errors": errors})
        result = write_outputs(self.data, self._path(name), self.sheet, load_base(self.folder))
        self.file, self.saved = name, copy.deepcopy(self.data)
        self.bump()
        return {"files": result.files, "warnings": result.warnings}

    def save_to_folder(self, raw: str) -> dict:
        """Write the open file as plan.json in a new folder, with its sheet images and flags, and open it there."""
        self.require_plan()
        target = Path(str(raw or "").strip().strip('"')).expanduser()
        if not str(target):
            raise HTTPException(400, "give the new folder's name or path")
        if not target.is_absolute():
            target = self.folder.parent / target
        target = target.resolve()
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise HTTPException(409, f"{target} already has files in it; pick a new folder")
        errors = validate_plan(self.data)
        if errors:
            raise HTTPException(400, {"errors": errors})
        target.mkdir(parents=True, exist_ok=True)
        data = copy.deepcopy(self.data)
        single_sheet = None
        if is_building(data):
            for floor in data["floors"]:
                source = self.folder / floor["sheet"] if floor.get("sheet") else None
                if source and source.exists():
                    shutil.copyfile(source, target / floor["sheet"])
        else:
            source = self.sheet
            if source and source.exists():
                name = data.get("sheet") or (source.name if not source.name.startswith(("plan", "_")) else f"sheet{source.suffix.lower()}")
                shutil.copyfile(source, target / name)
                single_sheet = target / name
        for flags in self.folder.glob("flags*.json"):
            shutil.copyfile(flags, target / flags.name)
        result = write_outputs(data, target / BASE_NAME, single_sheet, None)
        floor = self.floor
        self.open_folder(target)
        self.floor = min(floor, len(floors_of(self.data)) - 1)
        self._sync_flags()
        return {"folder": str(target), "files": result.files, "warnings": result.warnings}

    def export_jpeg(self) -> dict:
        """Every floor drawn clean at one scale in one JPEG, top floor first, saved in the folder."""
        self.require_plan()
        errors = validate_plan(self.data)
        if errors:
            raise HTTPException(400, {"errors": errors})
        image = export_image(self.data)
        name = f"{Path(self.file or BASE_NAME).stem}-export.jpg"
        image.save(self.folder / name, "JPEG", quality=92)
        return {"file": name, "width": image.width, "height": image.height, "floors": len(floors_of(self.data))}

    # ------------------------------------------------------------ floors

    def switch_floor(self, index: int) -> None:
        floors = floors_of(self.data)
        if not floors:
            raise HTTPException(400, "no plan is open")
        if not 0 <= index < len(floors):
            raise HTTPException(400, f"there is no floor {index + 1}; this plan has {len(floors)}")
        if index != self.floor:
            self.floor = index
            self._sync_flags()
            self.bump()

    def _building(self) -> dict:
        """The open file as a building, bringing a one-floor plan's sheet into the folder if it lives elsewhere."""
        data = self.require_plan() if not is_building(self.data) else self.data
        if is_building(data):
            return copy.deepcopy(data)
        sheet_name = data.get("sheet")
        sheet = self.sheet
        if not sheet_name and sheet and sheet.exists():
            sheet_name = sheet.name if sheet.parent == self.folder else self._store_sheet(FIRST_FLOOR, sheet.suffix, sheet.read_bytes())
        return as_building(data, name=self.folder.name, sheet=sheet_name)

    def _store_sheet(self, floor_id: str, suffix: str, data: bytes) -> str:
        """Put a floor's sheet image in the folder as <floor id>.png, reusing an identical file."""
        suffix = suffix.lower() if suffix.lower() in IMAGE_SUFFIXES else ".png"
        name, n = f"{floor_id}{suffix}", 2
        while (self.folder / name).exists() and (self.folder / name).read_bytes() != data:
            name = f"{floor_id}-{n}{suffix}"
            n += 1
        if not (self.folder / name).exists():
            (self.folder / name).write_bytes(data)
        return name

    def _traced_floor(self, building: dict, image_name: str, data: bytes, name: str | None) -> tuple[dict, dict]:
        floor_id = next_floor_id(building)
        sheet = self._store_sheet(floor_id, Path(image_name).suffix, data)
        with Image.open(self.folder / sheet) as image:
            width, height = image.size
        current = self.plan or {}
        floor = {
            "id": floor_id,
            "name": name or _floor_name(image_name, len(building["floors"])),
            "sheet": sheet,
            "units": "inches",
            "compass": copy.deepcopy(current.get("compass") or compass_from_up("north")),
            "canvas": {"width_px": width, "height_px": height, "px_per_inch": 1.0, "origin_px": [0, 0]},
        }
        walls = trace_walls(self.folder / sheet, TraceSettings(), px_per_inch=1.0, origin=(0, 0))
        guess = estimate_px_per_inch(walls, 1.0)
        scale = guess or (current.get("canvas") or {}).get("px_per_inch")
        ops: list[dict] = [{"op": "add_walls", "walls": walls}] if walls else []
        if scale and scale != 1.0:
            ops.append({"op": "calibrate", "p1": [0, 0], "p2": [scale, 0], "length": 1})
        if ops:
            floor, _ = apply(floor, {"op": "batch", "ops": ops})
        return floor, {"traced": len(walls), "estimated_px_per_inch": guess}

    def _plan_floors(self, building: dict, incoming: dict, source: Path | None, sheet: tuple[str, bytes] | None) -> list[dict]:
        """Floors to add from a plan file: every floor of a building, or a one-floor plan, each with its sheet copied in."""
        try:
            checked(incoming)
        except PlanError as exc:
            raise HTTPException(400, {"errors": exc.errors}) from exc
        added: list[dict] = []
        for floor in floors_of(incoming):
            floor = copy.deepcopy(floor)
            floor_id = next_floor_id({"floors": building["floors"] + added})
            image: tuple[str, bytes] | None = None
            if sheet and not is_building(incoming):
                image = sheet
            elif source is not None:
                path = source / floor["sheet"] if floor.get("sheet") else (None if is_building(incoming) else find_sheet(source))
                if path and path.exists():
                    image = (path.name, path.read_bytes())
            floor.pop("sheet", None)
            if image:
                floor["sheet"] = self._store_sheet(floor_id, Path(image[0]).suffix, image[1])
            floor["id"] = floor_id
            floor["name"] = floor.get("name") or f"Floor {len(building['floors']) + len(added) + 1}"
            added.append(floor)
        return added

    def add_floors(self, *, path: str | None = None, files: list[dict] | None = None, name: str | None = None) -> dict:
        """Add floors from a floor plan image (traced), a plan .json (one floor or a whole building), or a plan folder."""
        building = self._building()
        new: list[dict] = []
        info: dict = {}
        if path:
            source = Path(str(path).strip().strip('"')).expanduser().resolve()
            if source.is_dir():
                start = newest_plan(source)
                if not start:
                    raise HTTPException(400, f"{source} has no plan files")
                new = self._plan_floors(building, load_plan(source / start), source, None)
            elif not source.is_file():
                raise HTTPException(404, f"{source} not found")
            elif source.suffix.lower() == ".json":
                try:
                    incoming = load_plan(source)
                except PlanError as exc:
                    raise HTTPException(400, {"errors": exc.errors}) from exc
                new = self._plan_floors(building, incoming, source.parent, None)
            elif is_image(source.name):
                floor, info = self._traced_floor(building, source.name, source.read_bytes(), name)
                new = [floor]
            else:
                raise HTTPException(400, "add a floor from a .png/.jpg floor plan image, a plan .json, or a plan folder")
        elif files:
            images = [f for f in files if is_image(str(f.get("filename", "")))]
            plans = [f for f in files if str(f.get("filename", "")).lower().endswith(".json")]
            if plans:
                try:
                    incoming = json.loads(base64.b64decode(plans[0]["data"]).decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise HTTPException(400, f"{plans[0]['filename']} is not JSON: {exc}") from exc
                if not isinstance(incoming, dict):
                    raise HTTPException(400, f"{plans[0]['filename']} is not a floor plan")
                sheet = (images[0]["filename"], base64.b64decode(images[0]["data"])) if images else None
                new = self._plan_floors(building, incoming, None, sheet)
            else:
                traced = 0
                for image in images:
                    floor, one = self._traced_floor({"floors": building["floors"] + new}, image["filename"], base64.b64decode(image["data"]), name if len(images) == 1 else None)
                    new.append(floor)
                    traced += one["traced"]
                info = {"traced": traced}
            if not new:
                raise HTTPException(400, "drop a .png/.jpg floor plan image or a plan .json")
        else:
            raise HTTPException(400, "give a path or files")
        if name and len(new) == 1:
            new[0]["name"] = name
        building["floors"] += new
        self._change(building, len(building["floors"]) - len(new))
        names = ", ".join(f["name"] for f in new)
        message = f"Added {names}"
        if info.get("traced") is not None:
            message += f": traced {info['traced']} walls"
            message += ", scale guessed from wall thickness" if info.get("estimated_px_per_inch") else ""
            message += ". Check the scale with Scale (K), then Save."
        else:
            message += ". Save to keep it."
        return {"added": [f["id"] for f in new], "message": message, **info}

    def rename_floor(self, index: int, name: str) -> None:
        name = str(name or "").strip()
        if not name:
            raise HTTPException(400, "a floor needs a name")
        if not is_building(self.data):
            self.apply_op({"op": "set_meta", "name": name})
            return
        building = self._building()
        self._floor_at(building, index)["name"] = name
        self._change(building)

    def move_floor(self, index, to) -> None:
        building = self._building()
        floors = building["floors"]
        self._floor_at(building, index)
        try:
            to = max(0, min(len(floors) - 1, int(to)))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "where to? give the new index") from exc
        floors.insert(to, floors.pop(int(index)))
        self._change(building, to)

    def delete_floor(self, index) -> None:
        building = self._building()
        self._floor_at(building, index)
        index = int(index)
        if len(building["floors"]) == 1:
            raise HTTPException(400, "that's the only floor; a plan needs at least one")
        building["floors"].pop(index)
        self._change(building, min(self.floor if self.floor < index else self.floor - 1, len(building["floors"]) - 1))

    def set_building(self, name: str) -> None:
        building = self._building()
        building["name"] = str(name or "").strip() or self.folder.name
        self._change(building)

    @staticmethod
    def _floor_at(building: dict, index) -> dict:
        try:
            index = int(index)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "which floor? give its index") from exc
        if not 0 <= index < len(building["floors"]):
            raise HTTPException(400, f"there is no floor {index + 1}")
        return building["floors"][index]

    def _new_project_from_image(self, folder: Path, image_name: str, data: bytes | None = None, source: Path | None = None) -> dict:
        folder.mkdir(parents=True, exist_ok=True)
        suffix = Path(image_name).suffix.lower()
        target = folder / f"sheet{suffix}"
        n = 2
        while target.exists():
            target = folder / f"sheet-{n}{suffix}"
            n += 1
        if data is not None:
            target.write_bytes(data)
        else:
            shutil.copyfile(source, target)
        self.open_folder(folder, target)
        info = self.start_plan(BASE_NAME, title=Path(image_name).stem, auto=True)
        saved = self.save_plan("overwrite")
        traced = info.get("traced", 0)
        message = f"New plan for {image_name} in {folder}: traced {traced} walls"
        if info.get("estimated_px_per_inch"):
            message += ", scale guessed from wall thickness. Check it with Scale (K) on a dimension you know."
        else:
            message += ". Set the scale with Scale (K) on a dimension you know."
        return {"how": "new_from_image", "folder": str(folder), "message": message, "saved": saved, **info}

    def _plan_name_for(self, filename: str) -> str:
        if not (self.folder / BASE_NAME).exists():
            return BASE_NAME
        if PLAN_RE.match(filename) and not (self.folder / filename).exists():
            return filename
        stem = slug(Path(filename).stem)
        stem = stem[5:] if stem.startswith("plan-") else stem
        name, n = f"plan-{stem}.json", 2
        while (self.folder / name).exists():
            name = f"plan-{stem}-{n}.json"
            n += 1
        return name

    def open_path(self, raw: str) -> dict:
        """Open whatever is at a path on disk: a plan folder, a plan JSON, or a floor plan image."""
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            self.open_folder(path)
            return {"how": "folder", "message": f"Opened folder {path}"}
        if not path.is_file():
            raise HTTPException(404, f"{path} not found")
        if path.suffix.lower() == ".json":
            try:
                load_plan(path)
            except PlanError as exc:
                raise HTTPException(400, {"errors": exc.errors}) from exc
            self.open_folder(path.parent)
            self.open_file(path.name)
            return {"how": "plan", "message": f"Opened {path.name} in {path.parent}"}
        if not is_image(path.name):
            raise HTTPException(400, "open a plan folder, a plan .json, or a .png/.jpg floor plan image")
        with Image.open(path) as image:
            size = image.size
        matching = plans_matching(path.parent, size)
        if matching:
            self.open_folder(path.parent, path)
            return {"how": "folder", "message": f"Opened the plans for {path.name} in {path.parent}"}
        data = path.read_bytes()
        existing = project_with_sheet(data)
        if existing:
            self.open_folder(existing)
            return {"how": "reopened", "folder": str(existing), "message": f"Opened {existing}, the project already made from {path.name}"}
        return self._new_project_from_image(unique_folder(workspace(), path.stem), path.name, source=path)

    def open_uploads(self, files: list[dict]) -> dict:
        """Open files dropped or picked in the browser, which arrive without a path."""
        images = [f for f in files if is_image(str(f.get("filename", "")))]
        plans = [f for f in files if str(f.get("filename", "")).lower().endswith(".json")]
        if not images and not plans:
            raise HTTPException(400, "drop a plan .json or a .png/.jpg floor plan image")
        plan = data = None
        if plans:
            try:
                plan = json.loads(base64.b64decode(plans[0]["data"]).decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise HTTPException(400, f"{plans[0]['filename']} is not JSON: {exc}") from exc
            if not isinstance(plan, dict) or ("canvas" not in plan and not is_building(plan)):
                raise HTTPException(400, f"{plans[0]['filename']} is not a floor plan (it has no canvas)")
        if images:
            image = images[0]
            data = base64.b64decode(image["data"])
            if plan is None:
                current = self.sheet
                if current and current.exists() and self.plan is not None and current.read_bytes() == data:
                    return {"how": "same", "message": f"{image['filename']} is the sheet already open here"}
                existing = project_with_sheet(data)
                if existing:
                    self.open_folder(existing)
                    return {"how": "reopened", "folder": str(existing), "message": f"Opened {existing}, the project already made from {image['filename']}"}
                empty = not list_plans(self.folder) and self.plan is None
                folder = self.folder if empty else unique_folder(workspace(), Path(image["filename"]).stem)
                return self._new_project_from_image(folder, image["filename"], data=data)
            folder = unique_folder(workspace(), Path(plans[0]["filename"]).stem)
            folder.mkdir(parents=True, exist_ok=True)
            sheet = folder / f"sheet{Path(image['filename']).suffix.lower()}"
            sheet.write_bytes(data)
            (folder / BASE_NAME).write_text(json.dumps(plan, indent=2), encoding="utf-8")
            self.open_folder(folder, sheet)
            return {"how": "plan_and_image", "folder": str(folder), "message": f"Opened {plans[0]['filename']} over {image['filename']} in {folder}"}
        filename = plans[0]["filename"]
        for name in list_plans(self.folder):
            if load_plan(self.folder / name) == plan:
                self.open_file(name)
                return {"how": "plan", "message": f"{filename} is the same as {name}; opened it"}
        if is_building(plan):
            fits = not list_plans(self.folder)
        else:
            canvas = plan["canvas"]
            size = (int(round(canvas.get("width_px", 0))), int(round(canvas.get("height_px", 0))))
            sheet = self.sheet_info()
            fits = (sheet and (sheet["width"], sheet["height"]) == size) or (not sheet and not list_plans(self.folder))
        if fits:
            name = self._plan_name_for(filename)
            (self.folder / name).write_text(json.dumps(plan, indent=2), encoding="utf-8")
            self.open_file(name)
            return {"how": "plan", "message": f"Added {filename} to this folder as {name}"}
        folder = unique_folder(workspace(), Path(filename).stem)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / BASE_NAME).write_text(json.dumps(plan, indent=2), encoding="utf-8")
        self.open_folder(folder)
        return {
            "how": "plan",
            "folder": str(folder),
            "message": f"{filename} doesn't fit this folder's sheet, so it opened in {folder}. Drop its sheet image to see it underneath.",
        }

    # ------------------------------------------------------------ flags

    def flag_px(self, x: float, y: float) -> list[float]:
        canvas = self.require_plan()["canvas"]
        scale = float(canvas["px_per_inch"])
        ox, oy = canvas["origin_px"]
        return [ox + float(x) * scale, oy + float(y) * scale]

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
        self._change(self._with_plan(new))
        return ref

    def step_back(self, forward: bool) -> bool:
        source, target = (self.redo, self.undo) if forward else (self.undo, self.redo)
        if not source:
            return False
        target.append((self.data, self.floor))
        self.data, self.floor = source.pop()
        self._sync_flags()
        self.bump()
        return True

    # ------------------------------------------------------------ state

    def state(self, **extra) -> dict:
        plan = self.plan
        errors: list[str] = []
        ops: list[dict] = []
        if plan is not None:
            errors = validate_plan(self.data)
            try:
                ops = scene(plan)
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"cannot draw the plan: {exc}")
        building = is_building(self.data)
        return {
            "version": self.version,
            "folder": str(self.folder),
            "file": self.file,
            "files": list_plans(self.folder),
            "next_step": next_step_name(self.folder),
            "plan": plan,
            "floor": self.floor,
            "floors": [
                {"index": i, "id": f.get("id"), "name": floor_label(f, i), "sheet": f.get("sheet")}
                for i, f in enumerate(floors_of(self.data))
            ],
            "building": {"name": self.data.get("name") or self.folder.name} if building else None,
            "scene": ops,
            "errors": errors,
            "dirty": self.data is not None and self.data != self.saved,
            "can_undo": bool(self.undo),
            "can_redo": bool(self.redo),
            "sheet": self.sheet_info(),
            "has_base": (self.folder / BASE_NAME).exists(),
            "fixture_types": list(FIXTURE_TYPES),
            "browsers": self.browsers,
            "flags": self.flags.listing(plan["canvas"] if plan else None),
            "workspace": str(workspace()),
            "recent": recent_folders(),
            **extra,
        }


def _floor_name(image_name: str, count: int) -> str:
    stem = Path(image_name).stem.strip()
    return stem[:1].upper() + stem[1:] if stem else f"Floor {count + 1}"


def _settings(body: dict) -> TraceSettings:
    raw = body.get("settings") or {}
    defaults = TraceSettings()
    return TraceSettings(
        dark=int(raw.get("dark", defaults.dark)),
        min_thickness_px=int(raw.get("min_thickness_px", defaults.min_thickness_px)),
        max_thickness_px=int(raw.get("max_thickness_px", defaults.max_thickness_px)),
        min_length_px=int(raw.get("min_length_px", defaults.min_length_px)),
        merge=bool(raw.get("merge", defaults.merge)),
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
            if ref and isinstance(ref[0], list):
                return doc.state(ref=ref[0], refs=ref)
            return doc.state(ref=ref or None)

    @app.post("/api/open-any")
    def open_any(body: dict = Body(...)):
        """Open a path on disk, or files uploaded from the browser, whatever they are."""
        with doc.lock:
            if body.get("path"):
                info = doc.open_path(str(body["path"]))
            elif body.get("files"):
                info = doc.open_uploads(list(body["files"]))
            else:
                raise HTTPException(400, "give a path or files")
            return doc.state(opened=info)

    @app.post("/api/pick")
    def pick(body: dict = Body(default={})):
        """Show the system's open dialog, then open what was picked (or with path_only, just say what it was)."""
        kind = "folder" if body.get("kind") == "folder" else "file"
        try:
            path = native_pick(kind, str(doc.folder))
        except PickerUnavailable as exc:
            raise HTTPException(501, f"no file dialog here ({exc}); drop the file on the page instead") from exc
        if not path:
            return {"cancelled": True}
        if body.get("path_only"):
            return {"path": path}
        with doc.lock:
            info = doc.open_path(path)
            return doc.state(opened=info)

    @app.post("/api/floor")
    def switch_floor(body: dict = Body(...)):
        """Edit another floor of the open file: {index} counts from 0 at the bottom floor."""
        with doc.lock:
            try:
                index = int(body["index"])
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(400, "give the floor's index") from exc
            doc.switch_floor(index)
            return doc.state()

    @app.post("/api/floors")
    def floors(body: dict = Body(...)):
        """Change the file's floors, each one undoable step: add (path or files, optional name),
        rename (index, name), move (index, to), delete (index), or building (name)."""
        action = body.get("action")
        with doc.lock:
            info: dict = {}
            if action == "add":
                info = doc.add_floors(path=body.get("path"), files=body.get("files"), name=body.get("name"))
            elif action == "rename":
                doc.rename_floor(body.get("index"), body.get("name"))
            elif action == "move":
                doc.move_floor(body.get("index"), body.get("to"))
            elif action == "delete":
                doc.delete_floor(body.get("index"))
            elif action == "building":
                doc.set_building(body.get("name"))
            else:
                raise HTTPException(400, "action must be add, rename, move, delete, or building")
            return doc.state(floors_changed=info)

    @app.post("/api/export")
    def export():
        """Write <file>-export.jpg: every floor drawn clean at one scale, top floor first."""
        with doc.lock:
            return doc.state(exported=doc.export_jpeg())

    @app.post("/api/flags")
    def flags(body: dict = Body(...)):
        """Add, change, or remove flags: {action: add|update|delete|clear_done, ...} in plan inches."""
        action = body.get("action")
        with doc.lock:
            try:
                flag_id = None
                if action == "add":
                    px = body.get("px") or doc.flag_px(body["x"], body["y"])
                    size = None
                    if body.get("w") and body.get("h"):
                        scale = float(doc.require_plan()["canvas"]["px_per_inch"])
                        size = [float(body["w"]) * scale, float(body["h"]) * scale]
                    flag = doc.flags.add(px, size=size, note=str(body.get("note") or ""), target=body.get("target"), by=str(body.get("by") or "user"))
                    flag_id = flag["id"]
                elif action == "update":
                    fields = {k: body.get(k) for k in ("note", "done")}
                    if body.get("x") is not None and body.get("y") is not None:
                        fields["px"] = doc.flag_px(body["x"], body["y"])
                    if "target" in body:
                        fields["target"] = body["target"]
                    flag_id = doc.flags.update(str(body["id"]), **fields)["id"]
                elif action == "delete":
                    ids = body.get("ids") or ([body["id"]] if body.get("id") else [])
                    doc.flags.delete([str(i) for i in ids])
                elif action == "clear_done":
                    doc.flags.clear_done()
                else:
                    raise HTTPException(400, "action must be add, update, delete, or clear_done")
            except FlagError as exc:
                raise HTTPException(400, str(exc)) from exc
            except KeyError as exc:
                raise HTTPException(400, f"flags {action}: missing {exc}") from exc
            doc.bump()
            return doc.state(flag=flag_id)

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
            if body.get("mode") == "folder":
                saved = doc.save_to_folder(body.get("path") or body.get("name"))
                return doc.state(saved=saved)
            saved = doc.save_plan(body.get("mode", "overwrite"), body.get("name"))
            return doc.state(saved=saved)

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
                return _png(overlay_image(plan, sheet, floor_by_id(base, plan.get("id"))))
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
            info = doc.start_plan(
                name,
                up=body.get("up", "north"),
                px_per_inch=body.get("px_per_inch"),
                title=body.get("title"),
                auto=bool(body.get("auto")),
            )
            return doc.state(**info)

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

    @app.post("/api/selection")
    def set_selection(body: dict = Body(...)):
        """The browser reports what the user has selected, so other clients can act on it."""
        with doc.lock:
            doc.selection = [str(k) for k in body.get("keys") or []]
            return {"keys": doc.selection}

    @app.get("/api/selection")
    def get_selection():
        """What the user has selected in the browser: keys like walls:w12, with each object."""
        with doc.lock:
            plan = doc.plan or {}
            items = []
            for key in doc.selection:
                kind, _, ref = key.partition(":")
                if kind == "flags":
                    flag = next((f for f in doc.flags.listing(plan.get("canvas")) if f["id"] == ref), None)
                    if flag:
                        items.append({"kind": "flags", "id": ref, "object": flag})
                    continue
                obj = lookup(plan, kind, int(ref) if kind == "labels" and ref.isdigit() else ref) if kind in LIST_KEYS else None
                if obj is not None:
                    items.append({"kind": kind, "id": int(ref) if kind == "labels" else ref, "object": obj})
            return {"keys": doc.selection, "items": items, "browsers": doc.browsers}

    @app.post("/api/analyze")
    def analyze(body: dict = Body(default={})):
        """Find walls that are really one (what: stacked) or stray short bits like letters (what: stray)."""
        with doc.lock:
            plan = doc.require_plan()
            ids = body.get("ids") or None
            if body.get("what") == "stray":
                return {"groups": stray_bits(plan, ids, max_length=float(body.get("max_length", 24)))}
            return {
                "groups": wall_groups(
                    plan,
                    ids,
                    max_gap=float(body.get("max_gap", 2)),
                    min_overlap=float(body.get("min_overlap", 0.75)),
                    skip_stray=not ids,
                )
            }

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


def serve(folder: Path, *, sheet: Path | None = None, port: int = 35667, open_browser: bool = True) -> None:
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
