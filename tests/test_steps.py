import copy
import json

from floorplan_editor.engine.steps import find_sheet, list_plans, merge_folder, next_step_name, write_outputs


def test_list_and_next_step(sample_folder):
    for name in ("plan-step2.json", "plan-step10.json", "plan-merged.json", "plan-alt.json", "notes.json"):
        (sample_folder / name).write_text("{}", encoding="utf-8")
    assert list_plans(sample_folder) == [
        "plan.json",
        "plan-step2.json",
        "plan-step10.json",
        "plan-alt.json",
        "plan-merged.json",
    ]
    assert next_step_name(sample_folder) == "plan-step11.json"
    (sample_folder / "golden-gate.json").write_text(json.dumps({"canvas": {}}), encoding="utf-8")
    assert "golden-gate.json" in list_plans(sample_folder)
    assert "notes.json" not in list_plans(sample_folder)


def test_find_sheet(sample_folder):
    assert find_sheet(sample_folder).name == "sheet.png"


def test_write_outputs_makes_four_files(sample_folder, sample):
    plan = copy.deepcopy(sample)
    plan["walls"] = [w for w in plan["walls"] if w["id"] != "column"]
    result = write_outputs(plan, sample_folder / "plan-step1.json", find_sheet(sample_folder), sample)
    assert result.files == ["plan-step1.json", "plan-step1.svg", "plan-step1.png", "plan-step1-overlay.png"]
    assert result.warnings == []


def test_merge_folder(sample_folder, sample):
    a = copy.deepcopy(sample)
    a["walls"] = [w for w in a["walls"] if w["id"] != "column"]
    b = copy.deepcopy(sample)
    b["fixtures"].append({"id": "wd", "type": "washer_dryer", "x": 270, "y": 240, "w": 27, "h": 28})
    (sample_folder / "plan-step1.json").write_text(json.dumps(a), encoding="utf-8")
    (sample_folder / "plan-step2.json").write_text(json.dumps(b), encoding="utf-8")
    merged, result = merge_folder(sample_folder, "plan.json", ["plan-step1.json", "plan-step2.json"], find_sheet(sample_folder))
    assert "plan-merged-overlay.png" in result.files
    assert "wd" in {f["id"] for f in merged["fixtures"]}
