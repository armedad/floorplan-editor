"""Placement rules: a sentence and its check, turned on per plan, and the flags an edit raises."""

import json

import pytest
from fastapi.testclient import TestClient

from floorplan_editor.engine.edit import apply
from floorplan_editor.engine.rules import (
    RuleError,
    add_rule,
    check_plan,
    default_rules,
    flag_updates,
    load_rules,
    rules_path,
    set_rule_code,
    update_rule_text,
)
from floorplan_editor.server.app import create_app


def _ready(plan, off=(), **inches):
    rows = []
    for row in default_rules():
        code = dict(row["code"])
        if row["id"] in inches:
            code["inches"] = inches[row["id"]]
        rows.append({**row, "code": code})
    plan["enabled_rules"] = [row["id"] for row in rows if row["id"] not in off]
    return rows


def test_a_plan_with_no_list_runs_no_rules():
    plan = {"fixtures": [{"id": "t", "type": "toilet", "x": 12, "y": 30, "w": 18, "h": 28}]}
    assert check_plan(plan, default_rules()) == []


def test_sample_plan_passes_when_the_built_in_rules_are_on(sample):
    _ready(sample)
    assert check_plan(sample) == []


def test_toilet_clearance_uses_the_side_wall_and_the_number():
    plan = {
        "walls": [
            {"id": "back", "x1": 0, "y1": 0, "x2": 80, "y2": 0, "thickness": 4},
            {"id": "side", "x1": 0, "y1": 0, "x2": 0, "y2": 80, "thickness": 4},
        ],
        "fixtures": [{"id": "t", "type": "toilet", "x": 40, "y": 30, "w": 18, "h": 28}],
    }
    assert check_plan(plan, _ready(plan)) == []
    plan["fixtures"][0]["x"] = 12
    hit = check_plan(plan, _ready(plan))
    assert len(hit) == 1 and hit[0]["rule"] == "toilet-side-clearance"
    assert hit[0]["measured"] == 10
    assert hit[0]["objects"][1] == ["walls", "side"]
    assert check_plan(plan, _ready(plan, off=("toilet-side-clearance",))) == []
    assert check_plan(plan, _ready(plan, **{"toilet-side-clearance": 6})) == []


def test_door_jambs_only_when_both_ends_are_open():
    wall = {"id": "w", "x1": 0, "y1": 0, "x2": 36, "y2": 0, "thickness": 4}
    door = {"id": "d", "wall": "w", "type": "door", "offset": 0, "width": 36, "hinge": "start", "swing": "cw"}
    bare = {"walls": [wall], "openings": [door]}
    assert [item["rule"] for item in check_plan(bare, _ready(bare))] == ["door-jambs"]

    corner = {"walls": [wall, {"id": "n", "x1": 0, "y1": 0, "x2": 0, "y2": 40, "thickness": 4}], "openings": [door]}
    assert check_plan(corner, _ready(corner)) == []

    longer = {"walls": [{"id": "w", "x1": 0, "y1": 0, "x2": 80, "y2": 0, "thickness": 4}], "openings": [door]}
    assert check_plan(longer, _ready(longer)) == []


def test_fixture_may_touch_a_wall_but_not_sit_in_it():
    wall = {"id": "w", "x1": 0, "y1": 0, "x2": 0, "y2": 80, "thickness": 4}
    touching = {"walls": [wall], "fixtures": [{"id": "s", "type": "sink", "x": 11, "y": 40, "w": 18, "h": 18}]}
    assert check_plan(touching, _ready(touching)) == []
    inside = {"walls": [wall], "fixtures": [{"id": "s", "type": "sink", "x": 0, "y": 40, "w": 18, "h": 18}]}
    hit = check_plan(inside, _ready(inside))
    assert len(hit) == 1 and hit[0]["rule"] == "fixture-wall-clear" and hit[0]["measured"] > 1


def test_a_wall_longer_than_the_number_is_a_miss():
    plan = {"walls": [{"id": "w", "x1": 0, "y1": 0, "x2": 100, "y2": 0, "thickness": 4}], "enabled_rules": ["r1"]}
    rules = [{"id": "r1", "text": "no wall longer than 80 inches", "code": {"type": "wall", "property": "length", "compare": "at-most", "inches": 80}}]
    hit = check_plan(plan, rules)
    assert hit[0]["rule"] == "r1" and hit[0]["property"] == "length" and hit[0]["measured"] == 100
    rules[0]["code"] = None
    assert check_plan(plan, rules) == []


def test_side_clearance_applies_to_a_sink_without_a_new_measurement():
    plan = {
        "walls": [{"id": "side", "x1": 0, "y1": 0, "x2": 0, "y2": 80, "thickness": 4}],
        "fixtures": [
            {"id": "s", "type": "sink", "x": 12, "y": 40, "w": 18, "h": 18},
            {"id": "t", "type": "toilet", "x": 40, "y": 40, "w": 18, "h": 28},
        ],
        "enabled_rules": ["r1"],
    }
    rules = [{"id": "r1", "text": "a sink stays 15 inches off the side wall", "code": {"type": "sink", "property": "side-clearance", "compare": "at-least", "inches": 15}}]
    hit = check_plan(plan, rules)
    assert len(hit) == 1 and hit[0]["primary"] == ["fixtures", "s"] and hit[0]["measured"] == 10
    assert hit[0]["property"] == "side-clearance" and hit[0]["type"] == "sink"


def test_a_miss_flags_the_object_the_edit_touched_and_a_later_edit_updates_it():
    plan = {
        "walls": [{"id": "side", "x1": 0, "y1": 0, "x2": 0, "y2": 80, "thickness": 4}],
        "fixtures": [{"id": "t", "type": "toilet", "x": 10, "y": 40, "w": 18, "h": 28}],
    }
    rules = _ready(plan)
    misses = check_plan(plan, rules)
    first = flag_updates(misses, [], [("fixtures", "t")], plan)
    assert len(first) == 1 and first[0]["action"] == "add" and first[0]["by"] == "rule"
    assert first[0]["target"] == ["fixtures", "t"] and first[0]["x"] == 10
    flag = {"id": "F1", "by": "rule", "rule": "toilet-side-clearance", "target": ["fixtures", "t"], "note": first[0]["note"], "done": False, "x": 10, "y": 40}
    assert flag_updates(misses, [flag], [("fixtures", "t")], plan) == []

    plan["fixtures"][0]["x"] = 11
    moved = check_plan(plan, rules)
    again = flag_updates(moved, [flag], [("fixtures", "t")], plan)
    assert again[0]["action"] == "update" and again[0]["id"] == "F1" and again[0]["change"] == "updated"

    plan["fixtures"][0]["x"] = 40
    fixed = flag_updates(check_plan(plan, rules), [{**flag, "note": again[0]["note"], "x": 11}], [("fixtures", "t")], plan)
    assert fixed == [{"action": "update", "change": "done", "id": "F1", "rule": "toilet-side-clearance", "target": ["fixtures", "t"], "done": True}]

    done = {**flag, "done": True, "x": 40}
    plan["fixtures"][0]["x"] = 10
    left_alone = flag_updates(check_plan(plan, rules), [done], [("walls", "side")], plan)
    assert left_alone[0]["action"] == "add" and left_alone[0]["target"] == ["walls", "side"]
    assert all(item.get("id") != "F1" for item in left_alone)
    reopened = flag_updates(check_plan(plan, rules), [done], [("fixtures", "t")], plan)
    assert reopened[0]["id"] == "F1" and reopened[0]["change"] == "opened" and reopened[0]["done"] is False


def test_enabling_a_rule_changes_only_this_plan(sample):
    other = {"walls": [], "fixtures": []}
    changed, _ = apply(sample, {"op": "set_enabled_rules", "id": "toilet-side-clearance", "on": True})
    assert changed["enabled_rules"] == ["toilet-side-clearance"]
    assert "enabled_rules" not in sample
    also, _ = apply(other, {"op": "set_enabled_rules", "id": "door-jambs", "on": True})
    assert also["enabled_rules"] == ["door-jambs"]
    assert changed["enabled_rules"] == ["toilet-side-clearance"]
    cleared, _ = apply(changed, {"op": "set_enabled_rules", "id": "toilet-side-clearance", "on": False})
    assert "enabled_rules" not in cleared


def test_adding_a_sentence_stores_no_check_and_editing_it_clears_the_check(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOORPLAN_HOME", str(tmp_path / "home"))
    added = add_rule("no wall should be longer than 200 yards")
    row = next(item for item in added if item["text"].startswith("no wall"))
    assert row["code"] is None and row["id"] == "r1"
    coded = set_rule_code(row["id"], {"type": "wall", "property": "length", "compare": "at-most", "inches": 7200})
    assert next(item for item in coded if item["id"] == "r1")["code"] == {"type": "wall", "property": "length", "compare": "at-most", "inches": 7200}
    cleared = update_rule_text("r1", "no wall should be longer than 100 yards")
    assert next(item for item in cleared if item["id"] == "r1")["code"] is None
    with pytest.raises(RuleError, match="property"):
        set_rule_code("r1", {"check": "swing-matches-arc", "inches": 1})
    with pytest.raises(RuleError, match="property"):
        set_rule_code("r1", {"python": "print(1)"})
    kept = set_rule_code("r1", {"python": "print(1)", "type": "wall", "property": "length", "compare": "at-most", "inches": 80})
    stored = next(item for item in kept if item["id"] == "r1")["code"]
    assert stored == {"type": "wall", "property": "length", "compare": "at-most", "inches": 80}
    assert "python" not in stored


def test_an_old_rules_file_becomes_a_sentence_and_a_check(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOORPLAN_HOME", str(tmp_path / "home"))
    path = rules_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"rules": [{"id": "door-jambs", "enabled": False, "inches": 3}]}), encoding="utf-8")
    row = next(item for item in load_rules() if item["id"] == "door-jambs")
    assert row["code"] == {"type": "door", "property": "jamb", "compare": "at-least", "inches": 3}
    assert "3" in row["text"] and "enabled" not in row


def test_rules_endpoint_saves_the_sentence_and_a_pin_stays(tmp_path, monkeypatch, sample_folder):
    monkeypatch.setenv("FLOORPLAN_HOME", str(tmp_path / "home"))
    client = TestClient(create_app(sample_folder))
    listed = client.get("/api/rules").json()
    assert listed["enabled"] == []
    assert {row["id"] for row in listed["rules"]} == {"toilet-side-clearance", "door-jambs", "fixture-wall-clear"}
    added = client.post("/api/rules", json={"action": "add", "text": "no wall should be longer than 200 yards"}).json()["rules"]
    row = next(item for item in added if item["id"] == "r1")
    assert row["code"] is None
    coded = client.post("/api/rules", json={"action": "code", "id": "r1", "code": {"type": "wall", "property": "length", "compare": "at-most", "inches": 7200}}).json()["rules"]
    assert next(item for item in coded if item["id"] == "r1")["code"]["inches"] == 7200
    changed = client.post("/api/rules", json={"action": "text", "id": "r1", "text": "shorter walls"}).json()["rules"]
    assert next(item for item in changed if item["id"] == "r1")["code"] is None
    assert client.post("/api/rules", json={"action": "code", "id": "r1", "code": {"python": "print(1)"}}).status_code == 400

    first = client.post("/api/flags", json={"action": "pin_check"}).json()
    second = client.post("/api/flags", json={"action": "pin_check"}).json()
    assert first["flag"] == second["flag"]
    pinned = next(flag for flag in client.get("/api/state").json()["flags"] if flag["id"] == first["flag"])
    assert pinned["by"] == "pinned" and pinned["rule"] == "check-all" and pinned["note"] == "Check all rules."
    client.post("/api/flags", json={"action": "update", "id": pinned["id"], "done": True})
    client.post("/api/flags", json={"action": "clear_done"})
    left = client.get("/api/state").json()["flags"]
    assert any(flag["id"] == pinned["id"] for flag in left)
