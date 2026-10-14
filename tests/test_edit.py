import pytest

from floorplan_editor.engine import validate_plan
from floorplan_editor.engine.edit import EditError, apply, snap_point
from floorplan_editor.engine.render import scene


def wall(plan, wall_id):
    return next(w for w in plan["walls"] if w["id"] == wall_id)


def test_apply_does_not_mutate_input(sample):
    before = repr(sample)
    apply(sample, {"op": "delete", "kind": "walls", "id": "column"})
    assert repr(sample) == before


def test_add_assigns_ids_and_validates(sample):
    plan, ref = apply(sample, {"op": "add", "kind": "fixtures", "obj": {"type": "washer_dryer", "x": 270, "y": 240, "w": 27, "h": 28}})
    assert ref == ["fixtures", "washer-dryer-1"]
    plan, ref = apply(plan, {"op": "add", "kind": "erase", "obj": {"x": 1, "y": 1, "w": 3, "h": 3}})
    assert ref == ["erase", "erase-1"]
    plan, ref = apply(plan, {"op": "add", "kind": "labels", "obj": {"text": "W/D", "x": 270, "y": 240}})
    assert ref == ["labels", 4]
    assert validate_plan(plan) == []


def test_add_door_at_point_centers_on_wall(sample):
    plan, ref = apply(
        sample,
        {"op": "add", "kind": "openings", "obj": {"wall": "bed-n", "type": "door", "width": 24, "at": [270, 112]}},
    )
    door = plan["openings"][-1]
    assert ref == ["openings", "door-1"]
    assert door["offset"] == pytest.approx(30 - 12)
    assert validate_plan(plan) == []


def test_delete_wall_drops_its_openings(sample):
    plan, _ = apply(sample, {"op": "delete", "kind": "walls", "id": "bath-s"})
    assert "bath-door" not in {o["id"] for o in plan["openings"]}
    assert validate_plan(plan) == []


def test_move_wall_end_drags_joined_ends(sample):
    plan, _ = apply(sample, {"op": "move_wall_end", "id": "bath-w", "end": "end", "x": 300, "y": 130})
    assert (wall(plan, "bath-s")["x1"], wall(plan, "bath-s")["y1"]) == (300, 130)
    assert (wall(plan, "bed-n")["x2"], wall(plan, "bed-n")["y2"]) == (300, 130)


def test_move_wall_end_detached(sample):
    plan, _ = apply(sample, {"op": "move_wall_end", "id": "bath-w", "end": "end", "x": 300, "y": 130, "join": False})
    assert (wall(plan, "bath-s")["x1"], wall(plan, "bath-s")["y1"]) == (300, 110)


def test_shortening_wall_clamps_openings(sample):
    plan, _ = apply(sample, {"op": "move_wall_end", "id": "bath-s", "end": "end", "x": 330, "y": 110, "join": False})
    door = next(o for o in plan["openings"] if o["id"] == "bath-door")
    assert door["offset"] + door["width"] <= 30 + 1e-6
    assert validate_plan(plan) == []


def test_move_wall_moves_neighbors(sample):
    plan, _ = apply(sample, {"op": "move", "kind": "walls", "id": "bed-w", "dx": -20, "dy": 0})
    assert wall(plan, "bed-w")["x1"] == 220
    assert wall(plan, "bed-n")["x1"] == 220


def test_move_opening_slides_along_wall(sample):
    plan, _ = apply(sample, {"op": "move", "kind": "openings", "id": "bed-door", "dx": 5, "dy": 10})
    assert next(o for o in plan["openings"] if o["id"] == "bed-door")["offset"] == 30


def test_split_wall_moves_far_openings(sample):
    plan, ref = apply(sample, {"op": "split_wall", "id": "ext-n", "x": 100, "y": 0})
    new_id = ref[1]
    assert wall(plan, "ext-n")["x2"] == 100
    assert wall(plan, new_id)["x1"] == 100
    window = next(o for o in plan["openings"] if o["id"] == "kitchen-window")
    assert window["wall"] == new_id and window["offset"] == 50
    assert validate_plan(plan) == []


def test_polygon_vertex_edits(sample):
    plan, _ = apply(sample, {"op": "move_vertex", "id": "kitchen-l", "index": 1, "x": 150, "y": 3})
    plan, _ = apply(plan, {"op": "insert_vertex", "id": "kitchen-l", "index": 1, "x": 150, "y": 15})
    counter = next(c for c in plan["counters"] if c["id"] == "kitchen-l")
    assert counter["points"][1] == [150, 3] and counter["points"][2] == [150, 15]
    plan, _ = apply(plan, {"op": "delete_vertex", "id": "kitchen-l", "index": 2})
    assert len(next(c for c in plan["counters"] if c["id"] == "kitchen-l")["points"]) == 6


def test_box_to_polygon_includes_overhang(sample):
    plan, _ = apply(sample, {"op": "box_to_polygon", "id": "peninsula"})
    points = next(c for c in plan["counters"] if c["id"] == "peninsula")["points"]
    assert points == [[100, 60], [137, 60], [137, 120], [100, 120]]
    assert validate_plan(plan) == []


def test_flip_door(sample):
    plan, _ = apply(sample, {"op": "flip_door", "id": "bath-door", "what": "swing"})
    plan, _ = apply(plan, {"op": "flip_door", "id": "bath-door", "what": "hinge"})
    door = next(o for o in plan["openings"] if o["id"] == "bath-door")
    assert door["swing"] == "cw" and door["hinge"] == "end"
    with pytest.raises(EditError):
        apply(plan, {"op": "flip_door", "id": "bed-window"})


def test_update_rename_wall_keeps_openings(sample):
    plan, ref = apply(sample, {"op": "update", "kind": "walls", "id": "bath-s", "fields": {"id": "bath-north"}})
    assert ref == ["walls", "bath-north"]
    assert next(o for o in plan["openings"] if o["id"] == "bath-door")["wall"] == "bath-north"
    assert validate_plan(plan) == []


def test_update_none_removes_field(sample):
    plan, _ = apply(sample, {"op": "update", "kind": "counters", "id": "peninsula", "fields": {"overhang": None}})
    assert "overhang" not in next(c for c in plan["counters"] if c["id"] == "peninsula")


def test_calibrate_keeps_pixels(sample):
    plan, _ = apply(sample, {"op": "calibrate", "p1": [30, 30], "p2": [830, 30], "length": "50'"})
    assert plan["canvas"]["px_per_inch"] == pytest.approx(800 / 600)
    assert wall(plan, "ext-n")["x2"] == pytest.approx(600)


def test_snap_point(sample):
    assert snap_point(sample, 299, 1, 3)[2] == "endpoint"
    x, y, kind = snap_point(sample, 150, 2, 3)
    assert kind == "wall" and (x, y) == (150, 0)
    assert snap_point(sample, 150.4, 50.6, 1, grid=1) == (150, 51, "grid")


def test_snap_applies_to_wall_end(sample):
    plan, _ = apply(sample, {"op": "move_wall_end", "id": "column", "end": "end", "x": 161, "y": 258, "snap": 4, "join": False})
    assert (wall(plan, "column")["x2"], wall(plan, "column")["y2"]) == (161, 260)


def test_batch_is_one_edit(sample):
    plan, ref = apply(
        sample,
        {
            "op": "batch",
            "ops": [
                {"op": "delete", "kind": "walls", "id": "column"},
                {"op": "add", "kind": "fixtures", "obj": {"id": "wd", "type": "washer_dryer", "x": 270, "y": 240, "w": 27, "h": 28}},
            ],
        },
    )
    assert ref == ["fixtures", "wd"]
    assert "column" not in {w["id"] for w in plan["walls"]}


def test_unknown_op(sample):
    with pytest.raises(EditError):
        apply(sample, {"op": "explode"})


def test_resize_wall_keeps_the_anchor_and_drags_joined_ends(sample):
    plan, ref = apply(sample, {"op": "resize_wall", "id": "bath-s", "length": 80, "anchor": "end"})
    assert ref == ["walls", "bath-s"]
    assert (wall(plan, "bath-s")["x1"], wall(plan, "bath-s")["x2"]) == (320, 400)
    assert wall(plan, "bath-w")["x2"] == 320 and wall(plan, "bed-n")["x2"] == 320
    plan, _ = apply(sample, {"op": "resize_wall", "id": "bed-n", "length": 72, "anchor": "center"})
    assert (wall(plan, "bed-n")["x1"], wall(plan, "bed-n")["x2"]) == (234, 306)
    assert wall(plan, "bed-w")["x1"] == 234 and wall(plan, "bath-s")["x1"] == 306
    with pytest.raises(EditError):
        apply(sample, {"op": "resize_wall", "id": "bed-n", "length": 10, "anchor": "middle"})


def test_move_group_moves_shared_corners_once(sample):
    items = [["walls", "bath-w"], ["walls", "bath-s"], ["fixtures", "toilet"]]
    plan, refs = apply(sample, {"op": "move_group", "items": items, "dx": 0, "dy": 10})
    assert refs == items
    assert (wall(plan, "bath-w")["y1"], wall(plan, "bath-w")["y2"]) == (10, 120)
    assert wall(plan, "bath-s")["y1"] == 120 and wall(plan, "bed-n")["y2"] == 120
    assert next(f for f in plan["fixtures"] if f["id"] == "toilet")["y"] == 30
    assert validate_plan(plan) == []


def test_delete_many_skips_openings_gone_with_their_wall(sample):
    items = [["walls", "bath-s"], ["openings", "bath-door"], ["labels", 0], ["labels", 2]]
    plan, _ = apply(sample, {"op": "delete_many", "items": items})
    assert "bath-s" not in {w["id"] for w in plan["walls"]}
    assert "bath-door" not in {o["id"] for o in plan["openings"]}
    assert [label["text"] for label in plan["labels"]] == ["LIVING", "BATH"]


def test_duplicate_copies_walls_with_their_openings(sample):
    plan, refs = apply(sample, {"op": "duplicate", "items": [["walls", "bath-s"], ["fixtures", "toilet"]], "dx": 0, "dy": 40})
    kinds = [kind for kind, _ in refs]
    assert kinds == ["walls", "openings", "fixtures"]
    copy_wall = wall(plan, refs[0][1])
    assert (copy_wall["y1"], copy_wall["y2"]) == (150, 150)
    door = next(o for o in plan["openings"] if o["id"] == refs[1][1])
    assert door["wall"] == copy_wall["id"] and door["offset"] == 20
    assert validate_plan(plan) == []


def test_measure_moves_both_ends(sample):
    plan, ref = apply(sample, {"op": "add", "kind": "measures", "obj": {"x1": 10, "y1": 20, "x2": 40, "y2": 20}})
    assert ref == ["measures", "measure-1"]
    plan, _ = apply(plan, {"op": "move", "kind": "measures", "id": "measure-1", "dx": 5, "dy": -3})
    measure = plan["measures"][0]
    assert (measure["x1"], measure["y1"], measure["x2"], measure["y2"]) == (15, 17, 45, 17)
    assert validate_plan(plan) == []
    assert any(op.get("role") == "measure" and op.get("text") == "2'6.0\"" for op in scene(plan))


def test_measure_must_have_length(sample):
    plan, _ = apply(sample, {"op": "add", "kind": "measures", "obj": {"x1": 1, "y1": 1, "x2": 1, "y2": 1}})
    assert any("has no length" in error for error in validate_plan(plan))
