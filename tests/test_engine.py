import copy
import json

import pytest
from PIL import Image

from floorplan_editor.__main__ import main
from floorplan_editor.engine import (
    PlanError,
    apply_erase_regions,
    merge_plans,
    overlay_file,
    render_files,
    scene,
    validate_plan,
)
from floorplan_editor.engine.geometry import format_length, parse_length


def test_sample_is_valid(sample):
    assert validate_plan(sample) == []


def test_unknown_field_and_bad_values_are_errors(sample):
    sample["walls"][0]["color"] = "red"
    sample["fixtures"][0]["type"] = "piano"
    sample["openings"][0]["offset"] = 9999
    errors = validate_plan(sample)
    assert any("must be one of the palette colors" in e for e in errors)
    assert any("not a known FF&E type" in e for e in errors)
    assert any("does not fit on wall" in e for e in errors)


def test_duplicate_ids_across_collections(sample):
    sample["erase"] = [{"id": "fridge", "x": 0, "y": 0, "w": 5, "h": 5}]
    assert any("duplicates" in e for e in validate_plan(sample))


def test_erase_region_schema(sample):
    sample["erase"] = [{"id": "e1", "x": 0, "y": 0, "w": 0, "h": 5}]
    assert any("plan.erase[0].w must be > 0" in e for e in validate_plan(sample))


def test_doors_swing_sixty_degrees(sample):
    for opening in sample["openings"]:
        if opening.get("type") != "door":
            continue
        for hinge in ("start", "end"):
            for swing in ("cw", "ccw"):
                opening["hinge"] = hinge
                opening["swing"] = swing
                arc = next(op for op in scene(sample) if op["obj"] == ["openings", opening["id"]] and op["op"] == "arc")
                assert (arc["end"] - arc["start"]) % 360 == pytest.approx(60)


def test_scene_ops_are_tagged(sample):
    ops = scene(sample)
    tags = {tuple(op["obj"]) for op in ops}
    assert ("walls", "ext-n") in tags
    assert ("openings", "front-door") in tags
    assert ("counters", "kitchen-l") in tags
    assert ("fixtures", "toilet") in tags
    assert ("labels", 0) in tags
    assert all("obj" in op for op in ops)


def test_shower_and_cabinet_fixtures(sample):
    sample["fixtures"] += [
        {"id": "sh", "type": "shower", "x": 300, "y": 200, "w": 36, "h": 36},
        {"id": "pantry", "type": "cabinet", "x": 250, "y": 200, "w": 24, "h": 24, "rot": 90},
        {"id": "uppers", "type": "upper_cabinet", "x": 200, "y": 200, "w": 36, "h": 12},
        {"id": "hearth", "type": "fireplace", "x": 150, "y": 200, "w": 60, "h": 24, "rot": 42},
    ]
    assert validate_plan(sample) == []
    ops = scene(sample)
    lines = lambda fid: [op for op in ops if op["obj"] == ["fixtures", fid] and op["op"] == "line"]
    assert len(lines("sh")) == 2
    assert len(lines("pantry")) == 1
    assert len(lines("hearth")) >= 4
    assert any(op["op"] == "text" and op["text"] == "UPPER" for op in ops if op["obj"] == ["fixtures", "uppers"])


def test_glass_rail_walls(sample):
    sample["walls"].append({"id": "rail", "x1": 10, "y1": 10, "x2": 60, "y2": 10, "thickness": 1.5, "kind": "glass_rail"})
    assert validate_plan(sample) == []
    rail = [op for op in scene(sample) if op["obj"] == ["walls", "rail"]]
    assert {op["op"] for op in rail} == {"polygon", "line"}
    assert all(op.get("fill") is None for op in rail)
    sample["walls"][-1]["kind"] = "fence"
    assert any("kind must be one of glass_rail" in e for e in validate_plan(sample))


def test_render_files(tmp_path, sample):
    render_files(sample, tmp_path / "p.svg", tmp_path / "p.png")
    assert (tmp_path / "p.svg").read_text(encoding="utf-8").startswith("<svg")
    with Image.open(tmp_path / "p.png") as image:
        assert image.size == (860, 580)


def test_erase_regions_paint_white(sample):
    image = Image.new("RGB", (860, 580), (0, 0, 0))
    sample["erase"] = [{"id": "e1", "x": 10, "y": 10, "w": 20, "h": 20}]
    out = apply_erase_regions(image, sample)
    assert out.getpixel((30 + 40, 30 + 40)) == (255, 255, 255)
    assert out.getpixel((5, 5)) == (0, 0, 0)
    assert image.getpixel((70, 70)) == (0, 0, 0)


def test_overlay_erases_removed_wall(sample_folder, sample):
    edited = copy.deepcopy(sample)
    edited["walls"] = [w for w in edited["walls"] if w["id"] != "column"]
    out = sample_folder / "o.png"
    overlay_file(edited, sample_folder / "sheet.png", out, erase_from=sample)
    with Image.open(out) as image:
        x, y = 30 + 160 * 2, 30 + 126 * 2
        assert image.getpixel((x, y)) == (255, 255, 255)


def test_overlay_size_mismatch(tmp_path, sample):
    Image.new("RGB", (10, 10)).save(tmp_path / "small.png")
    with pytest.raises(PlanError):
        overlay_file(sample, tmp_path / "small.png", tmp_path / "o.png")


def test_merge_independent_steps(sample):
    a = copy.deepcopy(sample)
    a["walls"] = [w for w in a["walls"] if w["id"] != "column"]
    b = copy.deepcopy(sample)
    b["fixtures"].append({"id": "wd", "type": "washer_dryer", "x": 270, "y": 240, "w": 27, "h": 28})
    b["erase"] = [{"id": "e1", "x": 1, "y": 1, "w": 2, "h": 2}]
    merged = merge_plans(sample, [("a", a), ("b", b)])
    assert "column" not in {w["id"] for w in merged["walls"]}
    assert "wd" in {f["id"] for f in merged["fixtures"]}
    assert merged["erase"][0]["id"] == "e1"


def test_merge_conflict(sample):
    a = copy.deepcopy(sample)
    a["fixtures"][0]["x"] = 1
    b = copy.deepcopy(sample)
    b["fixtures"][0]["x"] = 2
    with pytest.raises(PlanError):
        merge_plans(sample, [("a", a), ("b", b)])


def test_cli_validate_render_overlay(sample_folder, capsys):
    plan = sample_folder / "plan.json"
    assert main(["validate", str(plan)]) == 0
    assert main(["render", str(plan)]) == 0
    assert (sample_folder / "plan.png").exists()
    assert main(["overlay", str(plan), str(sample_folder / "sheet.png")]) == 0
    assert (sample_folder / "plan-overlay.png").exists()


def test_cli_validate_reports_errors(tmp_path, sample, capsys):
    sample["units"] = "cm"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(sample), encoding="utf-8")
    assert main(["validate", str(path)]) == 1
    assert "units" in capsys.readouterr().err


@pytest.mark.parametrize(
    "text,inches",
    [("26'5\"", 317), ("26' 5\"", 317), ("317", 317), ("317in", 317), ("8.5'", 102), ("2ft 6in", 30)],
)
def test_parse_length(text, inches):
    assert parse_length(text) == pytest.approx(inches)


def test_format_length():
    assert format_length(317) == "26'5.0\""
    assert format_length(6) == '6.0"'
