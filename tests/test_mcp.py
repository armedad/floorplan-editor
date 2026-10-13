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


def test_fixture_box_is_centered_and_rotates(sample):
    toilet = {"id": "t", "type": "toilet", "x": 100, "y": 100, "w": 20, "h": 28}
    assert m.object_box(sample, "fixtures", toilet) == pytest.approx((90, 86, 110, 114))
    toilet["rot"] = 90
    assert m.object_box(sample, "fixtures", toilet) == pytest.approx((86, 90, 114, 110))


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


def test_flags_round_trip(live, sample_folder):
    with httpx.Client(base_url=live) as client:
        client.post("/api/flags", json={"action": "add", "x": 320, "y": 22, "note": "swap for a smaller toilet", "target": ["fixtures", "toilet"]})
        client.post("/api/flags", json={"action": "add", "x": 300, "y": 0, "w": 100, "h": 110, "note": "the whole bath"})
    listed = m.list_flags()
    assert listed["count"] == 2
    first, region = listed["flags"]
    assert first["placed_on"]["id"] == "toilet" and first["near"][0]["id"] == "toilet"
    assert {"toilet", "tub", "bath-sink"} <= {hit["id"] for hit in region["inside"]}
    assert m.editor_status()["open_flags"] == ["F1", "F2"]

    image, caption = m.view_image("overlay", flag="F1", max_px=600)
    assert "around F1" in caption and Image.open(BytesIO(image.data)).size == (576, 576)

    mine = m.add_flag(150, 130, note="column here?")
    assert mine["id"] == "F3" and mine["by"] == "cursor" and mine["near"][0]["id"] == "column"
    assert m.update_flag("f1", done=True)["done"] is True
    assert m.list_flags()["count"] == 2 and m.list_flags(include_done=True)["count"] == 3
    assert m.delete_flags(all_done=True)["remaining"] == ["F2", "F3"]
    assert json.loads((sample_folder / "flags.json").read_text())["next"] == 4


def test_comments_round_trip(live, sample_folder):
    added = m.add_comment("is this a column or a chase?", on="column")
    first = added["comment"]
    assert first["id"] == "c1" and first["by"] == "cursor" and first["on"]["id"] == "column" and added["dirty"]
    assert m.editor_status()["comments"] == 1

    with httpx.Client(base_url=live) as client:
        client.post("/api/selection", json={"keys": ["labels:2"]})
    second = m.add_comment("call it PRIMARY", use_selection=True)["comment"]
    assert second["id"] == "c2" and (second["on"]["kind"], second["on"]["id"]) == ("labels", 2)
    assert "BEDROOM" in second["on"]["what"]
    with httpx.Client(base_url=live) as client:
        client.post("/api/selection", json={"keys": ["labels:2", "walls:column"]})
    with pytest.raises(m.ToolError, match="one object"):
        m.add_comment("x", use_selection=True)

    assert m.list_comments()["count"] == 2
    assert [c["id"] for c in m.list_comments(on=["column"])["comments"]] == ["c1"]
    assert [c["id"] for c in m.list_comments(use_selection=True)["comments"]] == ["c1", "c2"]
    assert [c["id"] for c in m.list_comments(text="primary")["comments"]] == ["c2"]
    assert m.list_comments(by="user")["count"] == 0 and m.list_comments(ids=["C2"])["count"] == 1
    assert m.get_objects(["column"])["objects"][0]["object"]["comments"][0]["text"] == "is this a column or a chase?"

    edited = m.update_comment("c1", text="it's a chase", move_to="ext-n")["comment"]
    assert edited["text"] == "it's a chase" and edited["edited"] and edited["on"]["id"] == "ext-n"
    _, caption = m.view_image("plan")
    assert "comment tags in purple on 2 objects" in caption

    m.save()
    saved = json.loads((sample_folder / "plan.json").read_text())
    assert next(w for w in saved["walls"] if w["id"] == "ext-n")["comments"][0]["id"] == "c1"

    assert m.delete_comments(["c1", "c2"])["deleted"] == ["c1", "c2"]
    assert m.list_comments()["count"] == 0
    m.undo()
    assert m.list_comments()["count"] == 2
    with pytest.raises(m.ToolError, match="no comment"):
        m.update_comment("c9", text="x")


def test_selection_and_analysis_endpoints(client):
    assert client.post("/api/selection", json={"keys": ["walls:column", "labels:1", "walls:gone"]}).json()["keys"]
    selected = client.get("/api/selection").json()
    assert [(i["kind"], i["id"]) for i in selected["items"]] == [("walls", "column"), ("labels", 1)]
    client.post("/api/op", json={"op": {"op": "add", "kind": "walls", "obj": {"x1": 160, "y1": 132, "x2": 160, "y2": 140, "thickness": 12}}})
    groups = client.post("/api/analyze", json={"what": "stacked"}).json()["groups"]
    assert any("column" in g["ids"] for g in groups)
    assert client.post("/api/analyze", json={"what": "stray"}).json()["groups"] == []


def test_resolve_takes_ids_keys_and_pairs(sample):
    assert m.resolve(sample, ["toilet", "walls:column", "labels:2", ["openings", "bath-door"]]) == [
        ["fixtures", "toilet"],
        ["walls", "column"],
        ["labels", 2],
        ["openings", "bath-door"],
    ]
    with pytest.raises(m.ToolError, match="no object"):
        m.resolve(sample, ["nope"])


def test_tools_convert_merge_and_label_ids(live):
    with httpx.Client(base_url=live) as client:
        client.post("/api/selection", json={"keys": ["walls:column", "fixtures:stool-1"]})
    selected = m.get_selection()
    assert {i["id"] for i in selected["items"]} == {"column", "stool-1"} and len(selected["box"]) == 4
    assert m.get_objects(["toilet"])["objects"][0]["kind"] == "fixtures"

    out = m.convert_objects("fixture", use_selection=True, fixture_type="generic")
    assert set(out["removed"]) == {"column", "stool-1"} and out["touched"]["object"]["type"] == "generic"
    m.undo()

    m.add_object("walls", {"id": "twin", "x1": 160, "y1": 132, "x2": 160, "y2": 120, "thickness": 4})
    assert m.find_stacked_walls()["count"] == 0
    found = m.find_stacked_walls(ids=["column", "twin"])
    assert found["count"] == 1 and set(found["groups"][0]["ids"]) == {"column", "twin"}
    merged = m.merge_walls(["column", "twin"], thickness="10")
    assert merged["touched"]["object"]["thickness"] == 10
    thick = m.set_wall_thickness(6, ids=["column"], keep="left")
    assert thick["touched"]["object"]["x1"] == 158

    image, caption = m.view_image("overlay", region=[140, 100, 180, 150], highlight=["column"])
    assert "ids labeled" in caption and "walls:column" in caption
    stray = m.find_stray_bits()
    assert stray["count"] == 1 and stray["groups"][0]["ids"] == ["column"]


def test_open_anything_turns_an_image_into_a_project(live, sample_folder, tmp_path):
    scan = tmp_path / "scan.png"
    scan.write_bytes((sample_folder / "sheet.png").read_bytes())
    out = m.open_anything(str(scan), open_browser=False)
    assert out["opened"]["how"] == "new_from_image" and out["file"] == "plan.json"
    assert out["folder"] == str(tmp_path / "workspace" / "scan")
    assert m.open_anything(str(sample_folder / "plan.json"), open_browser=False)["folder"] == str(sample_folder)
