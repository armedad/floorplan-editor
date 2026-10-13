import json
import socket
import subprocess
import sys
import time
from io import BytesIO

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from floorplan_editor import mcp_server as m
from floorplan_editor.server.app import create_app


# ---------------------------------------------------------------- server endpoints the MCP server uses


@pytest.fixture
def client(sample_folder):
    return TestClient(create_app(sample_folder))


def test_version_bumps_and_ui_queue(client):
    v0 = client.get("/api/state").json()["version"]
    state = client.post("/api/op", json={"op": {"op": "delete", "kind": "walls", "id": "column"}}).json()
    assert state["version"] > v0
    assert client.post("/api/ui", json={"action": "fit"}).json() == {"seq": 1, "browsers": 0}
    assert client.post("/api/ui", json={"action": "explode"}).status_code == 400


def test_import_plan_and_image(client, sample_folder, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    plan = json.loads((sample_folder / "plan.json").read_text())
    plan["name"] = "from elsewhere"
    (elsewhere / "idea.json").write_text(json.dumps(plan))
    state = client.post("/api/import", json={"path": str(elsewhere / "idea.json")}).json()
    assert state["file"] == "plan-idea.json" and state["plan"]["name"] == "from elsewhere"
    assert client.post("/api/import", json={"path": str(elsewhere / "idea.json")}).status_code == 409

    (sample_folder / "sheet.png").replace(elsewhere / "scan.png")
    state = client.post("/api/import", json={"path": str(elsewhere / "scan.png")}).json()
    assert state["sheet"]["name"] == "scan.png" and state["sheet"]["matches"]
    assert client.post("/api/import", json={"path": str(elsewhere / "nope.png")}).status_code == 404


def test_render_plan_png(client):
    response = client.get("/api/render")
    image = Image.open(BytesIO(response.content))
    assert image.size == (860, 580)


# ---------------------------------------------------------------- search helpers


def test_search_near_and_by_type(sample):
    hits = m.search(sample, kinds=["walls"], near=[301, 50], radius=10)
    assert hits[0]["id"] == "bath-w" and hits[0]["distance"] == 0
    assert [h["id"] for h in m.search(sample, type_="toilet")] == ["toilet"]
    inside = {h["id"] for h in m.search(sample, kinds=["fixtures"], within=[300, 0, 400, 110])}
    assert inside == {"toilet", "tub", "bath-sink"}
    assert m.search(sample, text="kitchen")[0]["id"] in {"kitchen-window", "kitchen-l", "kitchen-sink", 0}


def test_rotated_fixture_box(sample):
    toilet = {"id": "t", "type": "toilet", "x": 100, "y": 100, "w": 20, "h": 28, "rot": 90}
    assert m.object_box(sample, "fixtures", toilet) == pytest.approx((72, 100, 100, 120))


# ---------------------------------------------------------------- tools against a live editor


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(sample_folder, monkeypatch):
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "floorplan_editor", "serve", str(sample_folder), "--port", str(port), "--no-browser"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    monkeypatch.setattr(m, "URL", url)
    deadline = time.time() + 20
    while not m._running():
        assert proc.poll() is None and time.time() < deadline, "editor did not start"
        time.sleep(0.2)
    yield url
    if proc.poll() is None:
        try:
            m.stop_editor(discard_unsaved=True)
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()


def test_tools_edit_save_merge(live, sample_folder):
    status = m.editor_status()
    assert status["running"] and status["file"] == "plan.json" and status["counts"]["walls"] == 9

    added = m.add_object("fixtures", {"type": "washer_dryer", "x": 250, "y": 200, "w": 27, "h": 30})
    wd = added["touched"]["id"]
    assert wd == "washer-dryer-1" and added["dirty"]
    assert m.rotate_fixture(wd, 90)["touched"]["object"]["rot"] == 90
    m.move_wall_end("bed-w", "end", 240, 250, join=False)
    m.edit_counter_outline("peninsula", "to_outline")
    out = m.edit_counter_outline("peninsula", "insert_corner", index=0, x=112, y=60)
    assert len(out["touched"]["object"]["points"]) == 5
    assert m.flip_door("bath-door")["touched"]["object"]["swing"] == "cw"
    m.delete_objects("walls", ["column"])
    assert m.undo()["counts"]["walls"] == 9
    assert m.redo()["counts"]["walls"] == 8

    with pytest.raises(m.ToolError, match="unsaved"):
        m.open_plan("plan.json")
    saved = m.save(mode="step")
    assert saved["file"] == "plan-step1.json" and (sample_folder / "plan-step1-overlay.png").exists()

    m.open_plan("plan.json")
    m.apply_ops([{"op": "add", "kind": "labels", "obj": {"text": "LAUNDRY", "x": 260, "y": 230}}])
    m.save(mode="step")
    merged = m.merge_steps(["plan-step1.json", "plan-step2.json"])
    assert merged["file"] == "plan-merged.json"
    plan = m.get_plan(["fixtures", "labels"])["plan"]
    assert wd in {f["id"] for f in plan["fixtures"]} and "LAUNDRY" in {l["text"] for l in plan["labels"]}
    assert "walls" not in plan

    with pytest.raises(m.ToolError, match="base"):
        m.open_plan("plan.json")
        m.add_object("labels", {"text": "X", "x": 1, "y": 1})
        m.save()


def test_view_image_crops_and_scales(live):
    image, caption = m.view_image("overlay", region=[300, 0, 400, 110], padding=0, max_px=800)
    png = Image.open(BytesIO(image.data))
    assert png.size == (727, 800)
    assert "cropped" in caption
    sheet, _ = m.view_image("plan", max_px=430)
    assert Image.open(BytesIO(sheet.data)).size == (430, 290)


def test_new_plan_from_image_with_trace(live, sample_folder, tmp_path):
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    m.start_editor(str(fresh), open_browser=False)
    assert m.editor_status()["file"] is None
    out = m.new_plan_from_image(str(sample_folder / "sheet.png"), up="south", px_per_inch=2, trace=True)
    assert out["traced"] >= 10 and out["counts"]["walls"] == out["traced"]
    assert (fresh / "sheet.png").exists()
    preview = m.trace_walls(min_thickness_px=6)
    assert 0 < preview["found"] <= out["traced"]
    props = m.set_plan_properties(name="Fresh", calibrate_p1=[30, 30], calibrate_p2=[830, 30], calibrate_length="33'4\"")
    assert props["canvas"]["px_per_inch"] == pytest.approx(2.0)
    assert props["name"] == "Fresh" and props["compass"]["up"] == "south"


def test_browser_events_stream(live):
    with httpx.Client(base_url=live, timeout=10) as client, client.stream("GET", "/api/events") as stream:
        lines = stream.iter_lines()
        assert next(lines) == "event: state"
        first = json.loads(next(lines)[len("data: "):])
        assert m.editor_status()["browsers_connected"] == 1
        m.delete_objects("walls", ["column"])
        events = []
        for line in lines:
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
            if any(e.get("action") == "message" for e in events):
                break
        versions = [e["version"] for e in events if "version" in e]
        assert versions and versions[0] > first["version"]
        assert {"action": "message", "text": "Cursor: deleted column"} in events
        sent = m.control_browser("zoom", region=[0, 0, 100, 100])
        assert sent["browsers_connected"] == 1
