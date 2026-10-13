import copy

import pytest

from floorplan_editor.engine import merge_plans, validate_plan
from floorplan_editor.engine.edit import EditError, apply
from floorplan_editor.engine.model import PlanError, comments_of
from floorplan_editor.engine.overlay import removed_objects


def find(plan, kind, ref):
    items = plan.get(kind, [])
    return items[ref] if kind == "labels" else next((o for o in items if o["id"] == ref), None)


def comment(plan, kind, ref, text, by="user"):
    plan, _ = apply(plan, {"op": "add_comment", "kind": kind, "id": ref, "text": text, "by": by})
    return plan


def all_comments(plan):
    return [(kind, ref, c["id"], c["text"]) for kind, ref, _, c in comments_of(plan)]


def test_add_update_delete(sample):
    plan = comment(sample, "walls", "column", "  is this structural?  ")
    plan = comment(plan, "labels", 1, "rename to FAMILY", by="cursor")
    assert all_comments(plan) == [("walls", "column", "c1", "is this structural?"), ("labels", 1, "c2", "rename to FAMILY")]
    c1 = find(plan, "walls", "column")["comments"][0]
    assert c1["by"] == "user" and c1["created"] and "edited" not in c1
    assert find(plan, "labels", 1)["comments"][0]["by"] == "cursor"
    assert validate_plan(plan) == []

    plan, ref = apply(plan, {"op": "update_comment", "comment": "C1", "text": "yes, keep it"})
    assert ref == ["walls", "column"]
    assert find(plan, "walls", "column")["comments"][0]["text"] == "yes, keep it"
    assert find(plan, "walls", "column")["comments"][0]["edited"]

    plan, ref = apply(plan, {"op": "update_comment", "comment": "c1", "to": ["fixtures", "toilet"]})
    assert ref == ["fixtures", "toilet"] and "comments" not in find(plan, "walls", "column")
    assert find(plan, "fixtures", "toilet")["comments"][0]["id"] == "c1"

    plan, ref = apply(plan, {"op": "delete_comments", "comments": ["c1", "c2"]})
    assert ref == ["labels", 1] and all_comments(plan) == []
    assert "comments" not in find(plan, "labels", 1)


def test_bad_comment_ops(sample):
    with pytest.raises(EditError, match="needs text"):
        apply(sample, {"op": "add_comment", "kind": "walls", "id": "column", "text": "  "})
    with pytest.raises(EditError, match="no walls"):
        apply(sample, {"op": "add_comment", "kind": "walls", "id": "nope", "text": "x"})
    with pytest.raises(EditError, match="no comment"):
        apply(sample, {"op": "delete_comments", "comments": ["c9"]})
    plan = comment(sample, "walls", "column", "x")
    with pytest.raises(EditError, match="taken"):
        apply(plan, {"op": "add_comment", "kind": "walls", "id": "ext-n", "text": "y", "comment_id": "c1"})


def test_new_ids_follow_the_highest(sample):
    plan = comment(comment(sample, "walls", "column", "a"), "walls", "column", "b")
    plan, _ = apply(plan, {"op": "delete_comments", "comments": ["c1"]})
    assert find(comment(plan, "fixtures", "tub", "c"), "fixtures", "tub")["comments"][0]["id"] == "c3"


def test_validation_catches_bad_comments(sample):
    plan = copy.deepcopy(sample)
    plan["walls"][0]["comments"] = [{"id": "c1", "text": "a"}]
    plan["walls"][1]["comments"] = [{"id": "c1", "text": ""}, {"text": "no id", "color": "red"}]
    errors = validate_plan(plan)
    assert any("duplicates" in e for e in errors)
    assert any("text must be a non-empty string" in e for e in errors)
    assert any(".id is required" in e for e in errors) and any("color is not a field" in e for e in errors)


def test_copies_drop_comments_and_split_keeps_them_on_the_first_half(sample):
    plan = comment(sample, "walls", "bed-w", "check this")
    plan = comment(plan, "openings", "bed-door", "pocket door?")
    plan, refs = apply(plan, {"op": "duplicate", "items": [["walls", "bed-w"]], "dx": 0, "dy": 0})
    assert [c[2] for c in all_comments(plan)] == ["c1", "c2"]
    assert all("comments" not in find(plan, k, r) for k, r in refs)

    wall = find(plan, "walls", "bed-w")
    plan, ref = apply(plan, {"op": "split_wall", "id": "bed-w", "x": (wall["x1"] + wall["x2"]) / 2, "y": (wall["y1"] + wall["y2"]) / 2})
    assert "comments" not in find(plan, *ref) and find(plan, "walls", "bed-w")["comments"][0]["id"] == "c1"
    assert validate_plan(plan) == []


def test_merge_and_convert_carry_comments(sample):
    plan, _ = apply(sample, {"op": "add", "kind": "walls", "obj": {"id": "twin", "x1": 160, "y1": 132, "x2": 160, "y2": 120, "thickness": 4}})
    plan = comment(plan, "walls", "twin", "one wall with the column")
    plan, ref = apply(plan, {"op": "merge_walls", "ids": ["column", "twin"]})
    assert [c["text"] for c in find(plan, *ref)["comments"]] == ["one wall with the column"]

    plan = comment(plan, "fixtures", "stool-1", "stool")
    plan, ref = apply(plan, {"op": "convert", "items": [["fixtures", "stool-1"], ["fixtures", "stool-2"]], "to": "fixture", "type": "generic"})
    assert [c["id"] for c in find(plan, *ref)["comments"]] == ["c2"]

    plan, ref = apply(plan, {"op": "convert", "items": [list(ref)], "to": "label", "text": "BAR"})
    assert ref[0] == "labels" and find(plan, *ref)["comments"][0]["id"] == "c2"
    assert validate_plan(plan) == []


def test_add_renumbers_comment_ids_that_are_taken(sample):
    plan = comment(sample, "walls", "column", "first")
    obj = {"type": "toilet", "x": 10, "y": 10, "w": 20, "h": 28, "comments": [{"id": "c1", "text": "pasted"}]}
    plan, ref = apply(plan, {"op": "add", "kind": "fixtures", "obj": obj})
    assert find(plan, *ref)["comments"][0]["id"] == "c2"


def test_comments_dont_count_as_moved_in_overlays(sample):
    plan = comment(sample, "walls", "column", "keep")
    assert removed_objects(sample, plan)["walls"] == []


def test_merge_steps_comment_and_move_the_same_wall(sample):
    a = comment(sample, "walls", "column", "from step a")
    b, _ = apply(sample, {"op": "move", "kind": "walls", "id": "column", "dx": 6, "dy": 0, "join": False})
    b = comment(b, "fixtures", "tub", "from step b")
    merged = merge_plans(sample, [("a", a), ("b", b)])
    column = find(merged, "walls", "column")
    assert column["x1"] == find(b, "walls", "column")["x1"]
    assert [c["text"] for c in column["comments"]] == ["from step a"]
    assert [(k, r, i) for k, r, i, _ in all_comments(merged)] == [("walls", "column", "c1"), ("fixtures", "tub", "c2")]
    assert validate_plan(merged) == []


def test_merge_comment_conflicts(sample):
    base = comment(sample, "walls", "column", "original")
    a, _ = apply(base, {"op": "update_comment", "comment": "c1", "text": "a says"})
    b, _ = apply(base, {"op": "update_comment", "comment": "c1", "text": "b says"})
    with pytest.raises(PlanError, match="comment c1"):
        merge_plans(base, [("a", a), ("b", b)])

    gone, _ = apply(sample, {"op": "delete", "kind": "walls", "id": "column"})
    noted = comment(sample, "walls", "column", "keep this")
    with pytest.raises(PlanError, match="deletes it"):
        merge_plans(sample, [("noted", noted), ("gone", gone)])

    removed, _ = apply(base, {"op": "delete_comments", "comments": ["c1"]})
    assert all_comments(merge_plans(base, [("removed", removed)])) == []
