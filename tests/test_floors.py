import base64
import copy
import json
import shutil

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from floorplan_editor.engine import PlanError, validate_plan
from floorplan_editor.engine.building import as_building, export_image, merge_any
from floorplan_editor.server.app import create_app


def building_of(sample, n=2):
    floors = []
    for i in range(n):
        floor = copy.deepcopy(sample)
        floor["id"] = f"floor-{i + 1}"
        floor["name"] = f"Floor {i + 1}"
        floors.append(floor)
    return {"name": "House", "floors": floors}


# ---------------------------------------------------------------- the format


def test_building_validates(sample):
    assert validate_plan(building_of(sample)) == []


def test_building_errors_name_the_floor(sample):
    data = building_of(sample)
    data["floors"][1]["id"] = "floor-1"
    data["floors"][1]["walls"][0]["thickness"] = -1
    errors = validate_plan(data)
    assert "plan.floors[1].id duplicates plan.floors[0]" in errors
    assert any(e.startswith("plan.floors[1].walls") for e in errors), errors
    assert validate_plan({"name": "x", "floors": []})
    data = building_of(sample)
    data["floors"][0]["sheet"] = "../outside.png"
    assert validate_plan(data)


def test_single_plan_becomes_first_floor(sample):
    building = as_building(sample, name="House", sheet="sheet.png")
    assert building["name"] == "House"
    assert [f["id"] for f in building["floors"]] == ["floor-1"]
    assert building["floors"][0]["sheet"] == "sheet.png"
    assert validate_plan(building) == []


def test_export_stacks_every_floor(sample):
    one = export_image(sample)
    two = export_image(building_of(sample))
    assert two.width >= one.width
    assert two.height > one.height * 1.6


def test_merge_building_steps_floor_by_floor(sample):
    base = building_of(sample)
    a = copy.deepcopy(base)
    a["floors"][0]["walls"] = [w for w in a["floors"][0]["walls"] if w["id"] != "column"]
    b = copy.deepcopy(base)
    b["floors"][1]["name"] = "Upper floor"
    b["name"] = "Chestnut"
    merged = merge_any(base, [("a", a), ("b", b)])
    assert merged["name"] == "Chestnut"
    assert "column" not in {w["id"] for w in merged["floors"][0]["walls"]}
    assert "column" in {w["id"] for w in merged["floors"][1]["walls"]}
    assert merged["floors"][1]["name"] == "Upper floor"


def test_merge_conflicting_floor_changes(sample):
    base = building_of(sample)
    a, b = copy.deepcopy(base), copy.deepcopy(base)
    a["floors"][1]["name"] = "Upstairs"
    b["floors"][1]["name"] = "Second floor"
    with pytest.raises(PlanError) as exc:
        merge_any(base, [("a", a), ("b", b)])
    assert "floor-2" in str(exc.value.errors)


# ---------------------------------------------------------------- the editor


@pytest.fixture
def upper(tmp_path, sample_folder):
    folder = tmp_path / "upper"
    shutil.copytree(sample_folder, folder)
    plan = json.loads((folder / "plan.json").read_text())
    plan["name"] = "Upper floor"
    plan["walls"] = [w for w in plan["walls"] if w["id"] != "column"]
    (folder / "plan.json").write_text(json.dumps(plan))
    return folder


@pytest.fixture
def client(sample_folder):
    return TestClient(create_app(sample_folder))


def test_add_a_floor_from_another_folder(client, upper):
    state = client.post("/api/floors", json={"action": "add", "path": str(upper)}).json()
    assert [f["name"] for f in state["floors"]] == [state["floors"][0]["name"], "Upper floor"]
    assert state["floor"] == 1
    assert state["building"]
    assert not has_column(state)
    assert state["sheet"]["name"] == "floor-2.png"
    assert state["errors"] == []
    assert state["dirty"]

    state = client.post("/api/floor", json={"index": 0}).json()
    assert state["floor"] == 0 and has_column(state)
    assert state["sheet"]["name"] == "sheet.png"

    state = client.post("/api/undo").json()
    assert state["building"] is None and len(state["floors"]) == 1


def has_column(state):
    return "column" in {w["id"] for w in state["plan"]["walls"]}


def test_edits_go_to_the_floor_being_edited(client, upper):
    client.post("/api/floors", json={"action": "add", "path": str(upper / "plan.json")})
    client.post("/api/floor", json={"index": 0})
    state = client.post("/api/op", json={"op": {"op": "delete", "kind": "walls", "id": "column"}}).json()
    assert not has_column(state)
    state = client.post("/api/floor", json={"index": 1}).json()
    state = client.post("/api/op", json={"op": {"op": "delete", "kind": "walls", "id": "bed-n"}}).json()
    assert state["floor"] == 1
    state = client.post("/api/undo").json()
    assert state["floor"] == 1 and "bed-n" in {w["id"] for w in state["plan"]["walls"]}
    state = client.post("/api/undo").json()
    assert state["floor"] == 0 and has_column(state)


def test_rename_move_delete_floors(client, upper):
    client.post("/api/floors", json={"action": "add", "path": str(upper)})
    state = client.post("/api/floors", json={"action": "rename", "index": 0, "name": "Main floor"}).json()
    assert state["floors"][0]["name"] == "Main floor"
    state = client.post("/api/floors", json={"action": "move", "index": 0, "to": 1}).json()
    assert [f["name"] for f in state["floors"]] == ["Upper floor", "Main floor"]
    assert state["floor"] == 1
    state = client.post("/api/floors", json={"action": "building", "name": "Chestnut"}).json()
    assert state["building"]["name"] == "Chestnut"
    state = client.post("/api/floors", json={"action": "delete", "index": 0}).json()
    assert [f["name"] for f in state["floors"]] == ["Main floor"]
    assert client.post("/api/floors", json={"action": "delete", "index": 0}).status_code == 400


def test_rename_a_single_plan_keeps_it_single(client):
    state = client.post("/api/floors", json={"action": "rename", "index": 0, "name": "Ground"}).json()
    assert state["building"] is None and state["plan"]["name"] == "Ground"


def test_save_export_and_save_to_folder(client, upper, sample_folder, tmp_path):
    client.post("/api/floors", json={"action": "add", "path": str(upper)})
    state = client.post("/api/save", json={}).json()
    saved = json.loads((sample_folder / "plan.json").read_text())
    assert len(saved["floors"]) == 2 and saved["floors"][0]["sheet"] == "sheet.png"
    assert "plan.png" in state["saved"]["files"] and "plan-overlay.png" in state["saved"]["files"]

    out = client.post("/api/export", json={}).json()["exported"]
    assert out["floors"] == 2
    with Image.open(sample_folder / out["file"]) as image:
        assert image.format == "JPEG" and image.height > image.width * 0.5

    state = client.post("/api/save", json={"mode": "folder", "path": "chestnut"}).json()
    target = tmp_path / "chestnut"
    assert state["folder"] == str(target)
    assert {"plan.json", "sheet.png", "floor-2.png"} <= {p.name for p in target.iterdir()}
    assert len(state["floors"]) == 2 and state["file"] == "plan.json"
    assert client.post("/api/save", json={"mode": "folder", "path": str(target)}).status_code == 409

    dest = tmp_path / "copies"
    state = client.post("/api/save", json={"mode": "as", "name": "house-copy", "folder": str(dest)}).json()
    assert state["file"] == "house-copy.json" and state["folder"] == str(dest.resolve())
    assert len(state["floors"]) == 2
    assert {"house-copy.json", "sheet.png", "floor-2.png"} <= {p.name for p in dest.iterdir()}


def test_flags_are_kept_per_floor(client, upper, sample_folder):
    client.post("/api/flags", json={"action": "add", "x": 10, "y": 10, "note": "ground"})
    client.post("/api/floors", json={"action": "add", "path": str(upper)})
    state = client.get("/api/state").json()
    assert state["flags"] == []
    client.post("/api/flags", json={"action": "add", "x": 20, "y": 20, "note": "upper"})
    assert (sample_folder / "flags-floor-2.json").exists()
    state = client.post("/api/floor", json={"index": 0}).json()
    assert [f["note"] for f in state["flags"]] == ["ground"]


def test_add_a_floor_from_an_uploaded_image(client, sample_folder):
    data = base64.b64encode((sample_folder / "sheet.png").read_bytes()).decode()
    state = client.post("/api/floors", json={"action": "add", "files": [{"filename": "Upper.png", "data": data}]}).json()
    assert len(state["floors"]) == 2 and state["floor"] == 1
    assert state["floors"][1]["name"] == "Upper"
    assert state["plan"]["walls"]
    assert state["floors_changed"]["traced"] > 0
    assert state["sheet"]["name"] == "floor-2.png"
