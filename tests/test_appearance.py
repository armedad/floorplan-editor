import copy

import pytest
from fastapi.testclient import TestClient

from floorplan_editor.engine.appearance import default_appearance, load_appearance, save_appearance
from floorplan_editor.engine.edit import apply
from floorplan_editor.engine.model import validate_plan
from floorplan_editor.engine.render import scene
from floorplan_editor.server.app import create_app


def _stroke(plan, collection, obj_id=None, role=None, appearance=None):
    for op in scene(plan, appearance=appearance):
        if op["obj"][0] != collection:
            continue
        if obj_id is not None and op["obj"][1] != obj_id:
            continue
        if role and op.get("role") != role:
            continue
        return op.get("stroke", op.get("fill"))
    raise AssertionError(collection)


def test_default_colors(sample):
    sample["measures"] = [{"id": "m1", "x1": 0, "y1": 0, "x2": 12, "y2": 0}]
    assert _stroke(sample, "walls", role="wall") == "#c40000"
    assert _stroke(sample, "openings", "front-door") == "#ef9a9a"
    assert _stroke(sample, "openings", "kitchen-window") == "#ef9a9a"
    assert _stroke(sample, "counters") == "#424242"
    assert _stroke(sample, "fixtures") == "#1e88e5"
    assert _stroke(sample, "labels") == "#000000"
    assert _stroke(sample, "measures", role="measure") == "#ef6c00"
    assert default_appearance()["selected"] == "#0d47a1"


def test_plan_override_and_moving_a_kind(sample):
    sample = copy.deepcopy(sample)
    sample["colors"] = {"fixtures": "#112233"}
    assert _stroke(sample, "fixtures") == "#112233"
    assert _stroke(sample, "walls", role="wall") == "#c40000"
    appearance = default_appearance()
    walls = next(group for group in appearance["groups"] if group["id"] == "walls")
    openings = next(group for group in appearance["groups"] if group["id"] == "openings")
    openings["kinds"].remove("doors")
    walls["kinds"].append("doors")
    assert _stroke(sample, "openings", "front-door", appearance=appearance) == "#c40000"
    assert _stroke(sample, "openings", "kitchen-window", appearance=appearance) == "#ef9a9a"


def test_one_ink_paints_everything(sample):
    ops = scene(sample, ink=(255, 255, 255, 255))
    assert ops[0]["stroke"] == (255, 255, 255, 255)


def test_colors_round_trip(sample):
    assert validate_plan({**sample, "colors": {"walls": "#abcdef"}}) == []
    assert any("must be a #rrggbb" in error for error in validate_plan({**sample, "colors": {"walls": "red"}}))
    plan, _ = apply(sample, {"op": "set_colors", "colors": {"labels": "#010101"}})
    assert plan["colors"] == {"labels": "#010101"}
    plan, _ = apply(plan, {"op": "set_colors", "colors": {"labels": None}})
    assert "colors" not in plan


def test_saved_groups_recolor_the_drawing(tmp_path, monkeypatch, sample):
    monkeypatch.setenv("FLOORPLAN_HOME", str(tmp_path / "home"))
    appearance = default_appearance()
    labels = next(group for group in appearance["groups"] if group["id"] == "labels")
    walls = next(group for group in appearance["groups"] if group["id"] == "walls")
    labels["kinds"].remove("labels")
    walls["kinds"].append("labels")
    save_appearance(appearance)
    assert load_appearance()["selected"] == "#0d47a1"
    assert _stroke(sample, "labels") == "#c40000"


def test_bad_appearance_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOORPLAN_HOME", str(tmp_path / "home"))
    with pytest.raises(ValueError):
        save_appearance({"groups": []})


def test_appearance_api(sample_folder):
    client = TestClient(create_app(sample_folder))
    state = client.get("/api/state").json()
    assert state["appearance"]["groups"][0]["color"] == "#c40000"
    assert next(op["stroke"] for op in state["scene"] if op.get("role") == "wall") == "#c40000"
    body = state["appearance"]
    next(group for group in body["groups"] if group["id"] == "fixtures")["color"] = "#223344"
    saved = client.post("/api/appearance", json=body).json()
    assert next(group["color"] for group in saved["groups"] if group["id"] == "fixtures") == "#223344"
    state = client.get("/api/state").json()
    assert next(op["stroke"] for op in state["scene"] if op["obj"][0] == "fixtures" and "stroke" in op) == "#223344"
    assert client.post("/api/appearance", json={"groups": []}).status_code == 400
