import pytest

from floorplan_editor.engine.analyze import doorway, stray_bits, wall_groups
from floorplan_editor.engine.edit import EditError, apply
from floorplan_editor.engine.model import validate_plan
from floorplan_editor.engine.trace import merge_stacked_bars


def wall(id_, x1, y1, x2, y2, t=4):
    return {"id": id_, "x1": x1, "y1": y1, "x2": x2, "y2": y2, "thickness": t}


def plan_with(*walls, **extra):
    plan = {
        "units": "inches",
        "compass": {"up": "north", "right": "east", "down": "south", "left": "west"},
        "canvas": {"width_px": 400, "height_px": 300, "px_per_inch": 1, "origin_px": [0, 0]},
        "walls": list(walls),
    }
    plan.update(extra)
    return plan


def get(plan, kind, id_):
    return next(o for o in plan[kind] if o["id"] == id_)


# ---------------------------------------------------------------- merging walls


def test_merge_side_by_side_bars_into_one_thick_wall():
    plan = plan_with(wall("a", 0, 10, 100, 10, 4), wall("b", 0, 14, 98, 14, 4))
    out, ref = apply(plan, {"op": "merge_walls", "ids": ["a", "b"]})
    assert ref == ["walls", "a"] and len(out["walls"]) == 1
    merged = out["walls"][0]
    assert merged["thickness"] == 8 and merged["y1"] == merged["y2"] == 12
    assert (merged["x1"], merged["x2"]) == (0, 100)


def test_merge_moves_openings_and_reconnects_joined_walls():
    plan = plan_with(
        wall("long", 0, 0, 120, 0, 4),
        wall("piece", 120, 0, 200, 0, 4),
        wall("side", 200, 0, 200, 80, 4),
        openings=[{"id": "d", "wall": "piece", "type": "door", "offset": 20, "width": 30}],
    )
    out, _ = apply(plan, {"op": "merge_walls", "ids": ["long", "piece"], "thickness": 6})
    merged = get(out, "walls", "long")
    assert (merged["x1"], merged["x2"], merged["thickness"]) == (0, 200, 6)
    door = out["openings"][0]
    assert door["wall"] == "long" and door["offset"] == 140
    assert validate_plan(out) == []


def test_merge_refuses_walls_that_are_not_parallel():
    plan = plan_with(wall("a", 0, 0, 100, 0), wall("b", 0, 0, 0, 100))
    with pytest.raises(EditError, match="parallel"):
        apply(plan, {"op": "merge_walls", "ids": ["a", "b"]})


def test_wall_groups_find_stacked_and_broken_walls_but_not_l_shapes():
    plan = plan_with(
        wall("top", 0, 10, 100, 10, 4),
        wall("top2", 0, 14, 100, 14, 4),
        wall("broken1", 0, 100, 50, 100, 4),
        wall("broken2", 51, 100, 120, 100, 4),
        wall("long", 200, 0, 200, 200, 4),
        wall("stub", 204, 150, 204, 200, 4),
    )
    groups = {tuple(sorted(g["ids"])): g["why"] for g in wall_groups(plan)}
    assert groups == {("top", "top2"): "side by side", ("broken1", "broken2"): "in line"}
    out, refs = apply(plan, {"op": "merge_stacked"})
    assert len(refs) == 2 and len(out["walls"]) == 4


def test_walls_split_where_a_third_wall_joins_stay_split():
    plan = plan_with(
        wall("left", 0, 100, 60, 100, 4),
        wall("right", 60, 100, 160, 100, 4),
        wall("tee", 60, 0, 60, 100, 4),
    )
    assert wall_groups(plan) == []


def test_merge_stacked_bars_after_a_trace_renumbers():
    walls = [wall("w1", 0, 10, 100, 10, 4), wall("w2", 0, 14, 100, 14, 4), wall("w3", 0, 50, 100, 50, 4)]
    merged = merge_stacked_bars(walls, 2)
    assert [w["id"] for w in merged] == ["w1", "w2"] and merged[0]["thickness"] == 8


# ---------------------------------------------------------------- thickness


@pytest.mark.parametrize(
    ("keep", "center"),
    [("center", 10), ("up", 12), ("down", 8), ("left", 10)],
)
def test_set_thickness_keeps_the_chosen_face(keep, center):
    plan = plan_with(wall("a", 0, 10, 100, 10, 4))
    out, _ = apply(plan, {"op": "set_wall_thickness", "ids": ["a"], "thickness": "8", "keep": keep})
    a = out["walls"][0]
    assert a["thickness"] == 8 and a["y1"] == a["y2"] == center


def test_set_thickness_on_a_wall_drawn_right_to_left():
    plan = plan_with(wall("a", 100, 10, 0, 10, 4))
    out, _ = apply(plan, {"op": "set_wall_thickness", "ids": ["a"], "thickness": 8, "keep": "up"})
    assert out["walls"][0]["y1"] == 12


# ---------------------------------------------------------------- converting


def doorway_plan():
    return plan_with(
        wall("left", 0, 0, 100, 0, 4),
        wall("right", 136, 0, 240, 0, 4),
        wall("leaf", 100, -2, 100, -36, 2),
    )


def test_doorway_gap_is_found_from_a_point_in_front_of_it():
    gap = doorway(doorway_plan(), 118, -20)
    assert {gap["a"], gap["b"]} == {"left", "right"}


def test_convert_leaf_in_a_gap_to_a_door_joins_the_walls():
    out, ref = apply(doorway_plan(), {"op": "convert", "items": [["walls", "leaf"]], "to": "door"})
    assert ref[0] == "openings"
    door = get(out, "openings", ref[1])
    host = get(out, "walls", door["wall"])
    assert [w["id"] for w in out["walls"]] == [host["id"]] and (host["x1"], host["x2"]) == (0, 240)
    assert (door["offset"], door["width"]) == (100, 36)
    assert door["swing"] == "ccw"
    assert validate_plan(out) == []


def test_door_at_a_point_fills_the_gap_and_width_centers_it():
    out, ref = apply(doorway_plan(), {"op": "convert", "to": "door", "at": [118, 0], "width": 30})
    door = get(out, "openings", ref[1])
    assert (door["offset"], door["width"]) == (103, 30)
    assert "leaf" in {w["id"] for w in out["walls"]}


def test_two_selected_walls_make_a_door_in_the_gap_between_them():
    plan = plan_with(wall("short", 0, 0, 50, 0, 4), wall("long", 80, 0, 210, 0, 4), wall("chase", 98, -30, 98, 2, 30))
    out, ref = apply(plan, {"op": "convert", "items": [["walls", "short"], ["walls", "long"]], "to": "door"})
    door = get(out, "openings", ref[1])
    host = get(out, "walls", door["wall"])
    assert host["id"] == "long" and (min(host["x1"], host["x2"]), max(host["x1"], host["x2"])) == (0, 210)
    assert door["width"] == 30 and min(door["offset"], 210 - door["offset"] - 30) in (50, 130)


def test_window_on_the_nearest_wall_when_there_is_no_gap():
    plan = plan_with(wall("w", 0, 0, 200, 0, 6), wall("glass", 50, 0, 90, 0, 1))
    out, ref = apply(plan, {"op": "convert", "items": [["walls", "glass"]], "to": "window"})
    window = get(out, "openings", ref[1])
    assert window["wall"] == "w" and (window["offset"], window["width"]) == (50, 40)
    assert [x["id"] for x in out["walls"]] == ["w"]


def test_convert_box_of_lines_to_a_fixture():
    plan = plan_with(
        wall("a", 10, 10, 30, 10, 1),
        wall("b", 10, 38, 30, 38, 1),
        wall("c", 10, 10, 10, 38, 1),
    )
    items = [["walls", "a"], ["walls", "b"], ["walls", "c"]]
    out, ref = apply(plan, {"op": "convert", "items": items, "to": "fixture", "type": "toilet", "rot": 90})
    toilet = get(out, "fixtures", ref[1])
    assert ref[1] == "toilet-1" and toilet["rot"] == 90
    assert (toilet["x"], toilet["y"], toilet["w"], toilet["h"]) == (19.75, 24.0, 29.0, 20.5)
    assert "walls" not in out


def test_convert_to_label_erase_counter_and_keep():
    plan = plan_with(wall("s", 100, 100, 100, 112, 3), wall("t", 104, 100, 110, 100, 2))
    items = [["walls", "s"], ["walls", "t"]]
    out, ref = apply(plan, {"op": "convert", "items": items, "to": "label", "text": "ST"})
    assert ref == ["labels", 0] and out["labels"][0]["text"] == "ST" and out["labels"][0]["size_px"] == 13
    out, ref = apply(plan, {"op": "convert", "items": items, "to": "erase", "keep": True})
    assert len(out["walls"]) == 2 and get(out, "erase", ref[1])["x"] == 97.5
    out, ref = apply(plan, {"op": "convert", "items": items, "to": "counter"})
    assert get(out, "counters", ref[1])["w"] == 11.5


def test_convert_needs_a_known_target_and_label_text():
    plan = plan_with(wall("a", 0, 0, 10, 0))
    with pytest.raises(EditError, match="to must be"):
        apply(plan, {"op": "convert", "items": [["walls", "a"]], "to": "sofa"})
    with pytest.raises(EditError, match="text"):
        apply(plan, {"op": "convert", "items": [["walls", "a"]], "to": "label"})


def test_stray_bits_skip_stubs_that_touch_walls():
    plan = plan_with(
        wall("room", 0, 0, 200, 0, 4),
        wall("stub", 50, 2, 50, 14, 4),
        wall("l1", 100, 50, 100, 62, 3),
        wall("l2", 100, 62, 108, 62, 3),
        wall("far", 300, 200, 310, 200, 3),
    )
    clusters = stray_bits(plan)
    assert [sorted(c["ids"]) for c in clusters] == [["l1", "l2"], ["far"]]
