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
