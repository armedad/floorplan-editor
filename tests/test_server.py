import base64
import json

import pytest
from fastapi.testclient import TestClient

from floorplan_editor.server.app import create_app


@pytest.fixture
def client(sample_folder):
    return TestClient(create_app(sample_folder))


def test_state_opens_base_plan(client):
    state = client.get("/api/state").json()
    assert state["file"] == "plan.json"
    assert state["sheet"]["matches"] is True
    assert state["errors"] == []
    assert state["dirty"] is False
    assert {op["obj"][0] for op in state["scene"]} >= {"walls", "openings", "counters", "fixtures", "labels"}


def test_op_undo_redo(client):
    state = client.post("/api/op", json={"op": {"op": "delete", "kind": "walls", "id": "column"}}).json()
    assert state["dirty"] and state["can_undo"]
    assert "column" not in {w["id"] for w in state["plan"]["walls"]}
    state = client.post("/api/undo").json()
    assert "column" in {w["id"] for w in state["plan"]["walls"]}
    assert state["dirty"] is False and state["can_redo"]
    state = client.post("/api/redo").json()
    assert "column" not in {w["id"] for w in state["plan"]["walls"]}


def test_bad_op_is_rejected(client):
    response = client.post("/api/op", json={"op": {"op": "delete", "kind": "walls", "id": "nope"}})
    assert response.status_code == 400
    assert "nope" in response.json()["detail"]
    assert client.get("/api/state").json()["can_undo"] is False


def test_save_step_and_merge(client, sample_folder):
    client.post("/api/op", json={"op": {"op": "delete", "kind": "walls", "id": "column"}})
    state = client.post("/api/save", json={"mode": "step"}).json()
    assert state["file"] == "plan-step1.json"
    assert "plan-step1-overlay.png" in state["saved"]["files"]
    assert not state["dirty"]

    client.post("/api/open", json={"file": "plan.json"})
    client.post(
        "/api/op",
        json={"op": {"op": "add", "kind": "fixtures", "obj": {"id": "wd", "type": "washer_dryer", "x": 270, "y": 240, "w": 27, "h": 28}}},
    )
    client.post("/api/save", json={"mode": "step"})
    state = client.post("/api/merge", json={"steps": ["plan-step1.json", "plan-step2.json"]}).json()
    assert state["file"] == "plan-merged.json"
    merged = json.loads((sample_folder / "plan-merged.json").read_text())
    assert "wd" in {f["id"] for f in merged["fixtures"]}
    assert "column" not in {w["id"] for w in merged["walls"]}


def test_save_as_any_json_name(client, sample_folder):
    state = client.post("/api/save", json={"mode": "as", "name": "golden-gate"}).json()
    assert state["file"] == "golden-gate.json"
    assert "golden-gate.json" in state["files"]
    assert (sample_folder / "golden-gate.json").exists()
    assert (sample_folder / "golden-gate-overlay.png").exists()
    refused = client.post("/api/save", json={"mode": "as", "name": "flags.json"})
    assert refused.status_code == 400


def test_save_as_into_another_folder(client, sample_folder, tmp_path):
    dest = tmp_path / "copies"
    state = client.post("/api/save", json={"mode": "as", "name": "copy-plan", "folder": str(dest)}).json()
    assert state["file"] == "copy-plan.json"
    assert state["folder"] == str(dest.resolve())
    assert (dest / "copy-plan.json").is_file()
    assert (dest / "sheet.png").is_file()
    assert (dest / "copy-plan-overlay.png").is_file()
    assert not (sample_folder / "copy-plan.json").exists()
    client.post("/api/folder", json={"path": str(sample_folder)})
    clash = client.post("/api/save", json={"mode": "as", "name": "copy-plan", "folder": str(dest)})
    assert clash.status_code == 409
    replaced = client.post("/api/save", json={"mode": "as", "name": "copy-plan", "folder": str(dest), "replace": True})
    assert replaced.status_code == 200
    assert replaced.json()["file"] == "copy-plan.json"


def test_default_save_folder_and_relative_save_as(client, tmp_path):
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("x", encoding="utf-8")
    assert client.post("/api/save", json={"mode": "as", "name": "nope", "folder": str(blocked)}).status_code == 400
    root = tmp_path / "defaults"
    saved = client.post("/api/prefs", json={"save_folder": str(root)}).json()
    assert saved["save_folder"] == str(root)
    assert client.get("/api/prefs").json() == {"save_folder": str(root)}
    assert client.get("/api/state").json()["save_folder"] == str(root)
    assert client.post("/api/prefs", json={"save_folder": 3}).status_code == 400
    state = client.post("/api/save", json={"mode": "as", "name": "rel", "folder": "job"}).json()
    assert state["folder"] == str((root / "job").resolve())
    assert (root / "job" / "rel.json").is_file()
    assert (root / "job" / "sheet.png").is_file()
    assert client.post("/api/prefs", json={"save_folder": "  "}).json()["save_folder"] == ""


def test_save_refuses_invalid_plan(client):
    client.post("/api/op", json={"op": {"op": "update", "kind": "walls", "id": "column", "fields": {"thickness": -1}}})
    response = client.post("/api/save", json={})
    assert response.status_code == 400
    assert response.json()["detail"]["errors"]


def test_sheet_applies_erase_regions(client):
    client.post("/api/op", json={"op": {"op": "add", "kind": "erase", "obj": {"x": 0, "y": -3, "w": 20, "h": 6}}})
    response = client.get("/api/sheet")
    assert response.headers["content-type"] == "image/png"
    from io import BytesIO

    from PIL import Image

    image = Image.open(BytesIO(response.content)).convert("RGB")
    assert image.getpixel((40, 30)) == (255, 255, 255)


def test_overlay_and_files(client):
    assert client.get("/api/overlay").status_code == 200
    assert client.get("/api/file/plan.json").status_code == 200
    assert client.get("/api/file/..%5Cplan.json").status_code in (400, 404)


def test_new_plan_from_upload_and_trace(tmp_path, sample_folder):
    empty = tmp_path / "fresh"
    empty.mkdir()
    client = TestClient(create_app(empty))
    assert client.get("/api/state").json()["plan"] is None
    data = base64.b64encode((sample_folder / "sheet.png").read_bytes()).decode()
    state = client.post(
        "/api/new", json={"image": {"filename": "scan.png", "data": data}, "up": "south", "px_per_inch": 2}
    ).json()
    assert state["sheet"]["name"] == "sheet.png"
    assert state["plan"]["compass"]["up"] == "south"
    assert state["dirty"]
    preview = client.post("/api/trace", json={"preview": True}).json()
    assert len(preview["walls"]) >= 10
    state = client.post("/api/trace", json={"preview": False}).json()
    assert state["traced"] == len(preview["walls"]) and state["can_undo"]
    state = client.post("/api/save", json={}).json()
    assert state["file"] == "plan.json"
    assert (empty / "plan.png").exists()


def test_index_served(client):
    assert client.get("/").status_code == 200


def _b64(path):
    return base64.b64encode(path.read_bytes()).decode()


def test_open_image_path_makes_a_traced_project_once(client, sample_folder, tmp_path):
    scan = tmp_path / "downloads" / "Unit 4B.png"
    scan.parent.mkdir()
    scan.write_bytes((sample_folder / "sheet.png").read_bytes())
    state = client.post("/api/open-any", json={"path": str(scan)}).json()
    opened = state["opened"]
    assert opened["how"] == "new_from_image" and opened["traced"] >= 10
    assert state["folder"] == str(tmp_path / "workspace" / "Unit-4B")
    assert state["file"] == "plan.json" and not state["dirty"]
    assert state["plan"]["canvas"]["px_per_inch"] == pytest.approx(opened["estimated_px_per_inch"])
    assert (tmp_path / "workspace" / "Unit-4B" / "plan.json").exists()
    again = client.post("/api/open-any", json={"path": str(scan)}).json()
    assert again["opened"]["how"] == "reopened" and again["folder"] == state["folder"]
    assert str(tmp_path / "workspace" / "Unit-4B") in again["recent"]


def test_open_image_next_to_its_plans_opens_that_folder(client, sample_folder):
    state = client.post("/api/open-any", json={"path": str(sample_folder / "sheet.png")}).json()
    assert state["opened"]["how"] == "folder" and state["file"] == "plan.json"


def test_dropped_plan_json_joins_a_folder_with_a_matching_sheet(client, sample_folder, sample):
    sample["name"] = "idea"
    data = base64.b64encode(json.dumps(sample).encode()).decode()
    state = client.post("/api/open-any", json={"files": [{"filename": "idea.json", "data": data}]}).json()
    assert state["file"] == "plan-idea.json" and state["plan"]["name"] == "idea"
    same = client.post("/api/open-any", json={"files": [{"filename": "copy.json", "data": data}]}).json()
    assert same["file"] == "plan-idea.json" and "same as" in same["opened"]["message"]


def test_dropped_plan_and_image_together_make_a_project(client, sample_folder, sample, tmp_path):
    files = [
        {"filename": "unit.json", "data": base64.b64encode(json.dumps(sample).encode()).decode()},
        {"filename": "unit.png", "data": _b64(sample_folder / "sheet.png")},
    ]
    state = client.post("/api/open-any", json={"files": files}).json()
    assert state["folder"] == str(tmp_path / "workspace" / "unit")
    assert state["file"] == "plan.json" and state["sheet"]["matches"]
    assert client.post("/api/open-any", json={"files": [{"filename": "notes.txt", "data": ""}]}).status_code == 400


def test_flags_live_in_the_folder_and_track_scale(client, sample_folder):
    state = client.post("/api/flags", json={"action": "add", "x": 100, "y": 50, "note": "move this", "target": ["walls", "ext-n"]}).json()
    assert state["flag"] == "F1"
    flag = state["flags"][0]
    assert (flag["x"], flag["y"], flag["px"]) == (100, 50, [230, 130])
    state = client.post("/api/flags", json={"action": "add", "x": 10, "y": 10, "w": 20, "h": 10}).json()
    assert state["flags"][1]["w"] == 20 and state["flags"][1]["size"] == [40, 20]
    client.post("/api/flags", json={"action": "update", "id": "f1", "done": True, "note": "moved"})
    saved = json.loads((sample_folder / "flags.json").read_text())
    assert saved["flags"][0]["done"] and saved["flags"][0]["note"] == "moved"
    client.post("/api/op", json={"op": {"op": "calibrate", "p1": [0, 0], "p2": [4, 0], "length": 1}})
    flag = client.get("/api/state").json()["flags"][0]
    assert (flag["x"], flag["y"]) == (50, 25)
    state = client.post("/api/flags", json={"action": "clear_done"}).json()
    assert [f["id"] for f in state["flags"]] == ["F2"]
    state = client.post("/api/flags", json={"action": "add", "x": 1, "y": 1}).json()
    assert state["flag"] == "F3"
    assert client.post("/api/flags", json={"action": "delete", "ids": ["F9"]}).status_code == 400


def test_group_ops_return_every_ref(client):
    state = client.post(
        "/api/op", json={"op": {"op": "duplicate", "items": [["fixtures", "toilet"], ["fixtures", "tub"]], "dx": 0, "dy": 100}}
    ).json()
    assert state["ref"] == state["refs"][0] and len(state["refs"]) == 2
