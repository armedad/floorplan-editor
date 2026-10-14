"""Regression net for a new submission.

Every edit operation the editor can send has one case here, and dropping or renaming one fails
this file until the case is updated. The read-only HTTP API, saved-picture colors, and a floor's
color override are checked the same way: a change that breaks them fails before it is merged.
"""

import copy
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from floorplan_editor.engine import PlanError, scene, to_svg, validate_plan
from floorplan_editor.engine.appearance import load_appearance, prefs_path, save_appearance
from floorplan_editor.engine.building import as_building, merge_any
from floorplan_editor.engine.edit import OPS, EditError, apply
from floorplan_editor.engine.overlay import overlay_image
from floorplan_editor.server.app import create_app

WEB = Path(__file__).resolve().parent.parent / "floorplan_editor" / "web"

# One successful call of every edit operation, against the sample plan (a case may add the
# objects it needs first). `batch` is the wrapper around the others, so it is not listed twice.
CASES = {
    "add": lambda p: {"op": "add", "kind": "labels", "obj": {"x": 12, "y": 12, "text": "Pantry"}},
    "update": lambda p: {"op": "update", "kind": "walls", "id": "column", "fields": {"thickness": 8}},
    "delete": lambda p: {"op": "delete", "kind": "walls", "id": "column"},
    "move": lambda p: {"op": "move", "kind": "fixtures", "id": "toilet", "dx": 4, "dy": 0},
    "move_group": lambda p: {"op": "move_group", "dx": 6, "dy": 0, "items": [["fixtures", "toilet"], ["fixtures", "tub"]]},
    "delete_many": lambda p: {"op": "delete_many", "items": [["fixtures", "stool-1"], ["fixtures", "stool-2"]]},
    "duplicate": lambda p: {"op": "duplicate", "dx": 24, "dy": 0, "items": [["fixtures", "toilet"]]},
    "resize_wall": lambda p: {"op": "resize_wall", "id": "column", "length": 20, "anchor": "start"},
    "move_wall_end": lambda p: {"op": "move_wall_end", "id": "column", "end": "end", "x": 160, "y": 150},
    "split_wall": lambda p: {"op": "split_wall", "id": "column", "x": 160, "y": 126},
    "move_vertex": lambda p: {"op": "move_vertex", "id": "kitchen-l", "index": 0, "x": 4, "y": 4},
    "insert_vertex": lambda p: {"op": "insert_vertex", "id": "kitchen-l", "index": 0, "x": 20, "y": 8},
    "delete_vertex": lambda p: {"op": "delete_vertex", "id": "kitchen-l", "index": 1},
    "box_to_polygon": lambda p: {"op": "box_to_polygon", "id": "peninsula"},
    "flip_door": lambda p: {"op": "flip_door", "id": "front-door", "what": "swing"},
    "slide_opening": lambda p: {"op": "slide_opening", "id": "front-door", "x": 200, "y": 260},
    "calibrate": lambda p: {"op": "calibrate", "p1": [30, 30], "p2": [230, 30], "length": 100},
    "set_compass": lambda p: {"op": "set_compass", "up": "east"},
    "set_meta": lambda p: {"op": "set_meta", "name": "Renamed", "notes": "A note"},
    "set_colors": lambda p: {"op": "set_colors", "colors": {"walls": "#112233"}},
    "add_walls": lambda p: {"op": "add_walls", "walls": [{"x1": 20, "y1": 220, "x2": 80, "y2": 220, "thickness": 4}]},
    "merge_walls": lambda p: {"op": "merge_walls", "ids": ["bed-n", "bath-s"]},
    "merge_stacked": lambda p: _stacked(p),
    "set_wall_thickness": lambda p: {"op": "set_wall_thickness", "id": "column", "thickness": 8, "keep": "center"},
    "convert": lambda p: {"op": "convert", "to": "label", "text": "Column", "items": [["walls", "column"]]},
    "add_comment": lambda p: {"op": "add_comment", "kind": "walls", "id": "column", "text": "Look here"},
    "update_comment": lambda p: _comment(p, {"op": "update_comment", "comment": "c9", "text": "Edited"}),
    "delete_comments": lambda p: _comment(p, {"op": "delete_comments", "comments": ["c9"]}),
    "batch": lambda p: {"op": "batch", "ops": [{"op": "move", "kind": "fixtures", "id": "toilet", "dx": 1, "dy": 0}]},
}


def _stacked(plan):
    plan["walls"] += [
        {"id": "bar-a", "x1": 10, "y1": 200, "x2": 90, "y2": 200, "thickness": 2},
        {"id": "bar-b", "x1": 10, "y1": 203, "x2": 90, "y2": 203, "thickness": 2},
    ]
    return {"op": "merge_stacked", "ids": ["bar-a", "bar-b"]}


def _comment(plan, op):
    plan["walls"][0].setdefault("comments", []).append({"id": "c9", "text": "note", "by": "user"})
    return op


@pytest.fixture
def client(sample_folder):
    return TestClient(create_app(sample_folder))


def test_every_edit_operation_has_a_case():
    # `batch` is dispatched on its own, ahead of the OPS table, and still needs a case.
    assert set(CASES) == set(OPS) | {"batch"}


@pytest.mark.parametrize("name", sorted(CASES))
def test_edit_operation_leaves_a_valid_drawable_plan(sample, name):
    plan = copy.deepcopy(sample)
    new, _ = apply(plan, CASES[name](plan))
    assert validate_plan(new) == [], name
    scene(new)
    assert sample["walls"][0]["id"] == "ext-n"


def test_a_rejected_edit_does_not_change_the_plan(sample):
    before = copy.deepcopy(sample)
    with pytest.raises(EditError):
        apply(sample, {"op": "delete", "kind": "walls", "id": "missing"})
    assert sample == before


def test_compass_turns_clockwise_and_meta_can_be_cleared(sample):
    plan, _ = apply(sample, {"op": "set_compass", "up": "west"})
    assert plan["compass"] == {"up": "west", "right": "north", "down": "east", "left": "south"}
    plan, _ = apply(plan, {"op": "set_meta", "name": "West", "notes": "note"})
    plan, _ = apply(plan, {"op": "set_meta", "name": "", "notes": ""})
    assert "name" not in plan and "notes" not in plan
    assert validate_plan(plan) == []


def test_saved_pictures_use_the_group_colors(sample, sample_folder):
    sample = copy.deepcopy(sample)
    sample["measures"] = [{"id": "m1", "x1": 40, "y1": 40, "x2": 80, "y2": 40}]
    sample["walls"].append({"id": "rail", "x1": 20, "y1": 40, "x2": 60, "y2": 40, "thickness": 1.5, "kind": "glass_rail"})
    svg = to_svg(sample, scene(sample))
    for color in ("#c40000", "#ef9a9a", "#424242", "#1e88e5", "#000000", "#ef6c00"):
        assert color in svg
    assert "#00897b" not in svg and "#0d47a1" not in svg
    rail = next(op for op in scene(sample) if op.get("role") == "rail")
    assert rail["stroke"] == "#c40000"

    image = overlay_image(sample, sample_folder / "sheet.png")
    # Mid-span of the north wall, clear of the kitchen window.
    red = image.getpixel((30 + 80 * 2, 30))
    assert red[0] > 150 and red[1] < 50 and red[2] < 50


def test_a_floor_color_override_survives_a_merge_and_two_overrides_conflict(sample):
    base = as_building(sample)
    changed = copy.deepcopy(base)
    changed["floors"][0]["colors"] = {"walls": "#112233"}
    merged = merge_any(base, [("step", changed)])
    assert merged["floors"][0]["colors"] == {"walls": "#112233"}

    other = copy.deepcopy(base)
    other["floors"][0]["colors"] = {"walls": "#445566"}
    with pytest.raises(PlanError) as exc:
        merge_any(base, [("a", changed), ("b", other)])
    assert any("colors" in error for error in exc.value.errors)


def test_a_broken_appearance_file_falls_back_to_the_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOORPLAN_HOME", str(tmp_path / "home"))
    path = prefs_path()
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    assert load_appearance()["selected"] == "#0d47a1"
    broken = load_appearance()
    broken["groups"][0]["kinds"].append("doors")
    with pytest.raises(ValueError):
        save_appearance(broken)


def test_editor_pages_offer_settings_and_the_read_api_answers(client):
    page = client.get("/").text
    for tab in ("about", "commands", "customize"):
        assert f'data-settings-tab="{tab}"' in page
    assert "This plan" in page and 'href="/agents"' in page
    assert "list_flags" in client.get("/agents").text

    for path in ("/api/state", "/api/appearance", "/api/sheet", "/api/overlay", "/api/render", "/api/selection", "/api/file/plan.json"):
        assert client.get(path).status_code == 200, path
    assert client.post("/api/op", json={"op": {"op": "nope"}}).status_code == 400
    assert client.post("/api/appearance", json={"groups": []}).status_code == 400


def test_undo_of_a_plan_color_restores_the_drawing(client):
    state = client.post("/api/op", json={"op": {"op": "set_colors", "colors": {"fixtures": "#112233"}}}).json()
    assert state["dirty"] is True
    assert state["plan"]["colors"] == {"fixtures": "#112233"}
    assert next(op["stroke"] for op in state["scene"] if op["obj"][0] == "fixtures" and "stroke" in op) == "#112233"
    state = client.post("/api/undo").json()
    assert "colors" not in state["plan"]
    assert state["dirty"] is False
    assert next(op["stroke"] for op in state["scene"] if op["obj"][0] == "fixtures" and "stroke" in op) == "#1e88e5"


def test_browser_sources_parse():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    for path in sorted(WEB.glob("*.js")):
        result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, path.name + "\n" + result.stderr
