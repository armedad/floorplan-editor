import json

from PIL import Image

from floorplan_editor.__main__ import main
from floorplan_editor.engine import validate_plan
from floorplan_editor.engine.render import Canvas
from floorplan_editor.engine.trace import TraceSettings, new_plan_from_image, trace_walls


def _matches(sample_wall, traced, tol=1.5):
    horizontal = sample_wall["y1"] == sample_wall["y2"]
    lo, hi = sorted((sample_wall["x1"], sample_wall["x2"]) if horizontal else (sample_wall["y1"], sample_wall["y2"]))
    covered = 0.0
    for wall in traced:
        if horizontal != (wall["y1"] == wall["y2"]):
            continue
        across = wall["y1"] - sample_wall["y1"] if horizontal else wall["x1"] - sample_wall["x1"]
        if abs(across) > tol:
            continue
        a, b = sorted((wall["x1"], wall["x2"]) if horizontal else (wall["y1"], wall["y2"]))
        covered += max(0.0, min(b, hi) - max(a, lo))
    return covered / (hi - lo)


def test_trace_finds_every_long_wall(sample, sample_folder):
    canvas = Canvas(sample)
    traced = trace_walls(sample_folder / "sheet.png", px_per_inch=canvas.scale, origin=canvas.origin)
    for wall in sample["walls"]:
        if wall["id"] == "column":
            continue
        openings = sum(o["width"] for o in sample["openings"] if o["wall"] == wall["id"])
        length = abs(wall["x2"] - wall["x1"]) + abs(wall["y2"] - wall["y1"])
        assert _matches(wall, traced) >= (length - openings) / length * 0.9, wall["id"]
    assert len(traced) <= 16


def test_traced_thickness_is_close(sample, sample_folder):
    canvas = Canvas(sample)
    traced = trace_walls(sample_folder / "sheet.png", px_per_inch=canvas.scale, origin=canvas.origin)
    thicknesses = sorted({round(w["thickness"]) for w in traced})
    assert set(thicknesses) <= {4, 5, 6, 7}


def test_sensitivity_settings(sample_folder):
    loose = trace_walls(sample_folder / "sheet.png", TraceSettings(), px_per_inch=2)
    thick_only = trace_walls(sample_folder / "sheet.png", TraceSettings(min_thickness_px=11), px_per_inch=2)
    assert 0 < len(thick_only) < len(loose)
    assert trace_walls(sample_folder / "sheet.png", TraceSettings(dark=0), px_per_inch=2) == []


def test_new_plan_from_image(sample_folder):
    plan = new_plan_from_image(sample_folder / "sheet.png", up="south", px_per_inch=2)
    width, height = Image.open(sample_folder / "sheet.png").size
    assert plan["canvas"]["width_px"] == width and plan["canvas"]["height_px"] == height
    assert plan["compass"] == {"up": "south", "right": "west", "down": "north", "left": "east"}
    assert plan["walls"]
    assert validate_plan(plan) == []
    empty = new_plan_from_image(sample_folder / "sheet.png", trace=False)
    assert "walls" not in empty and validate_plan(empty) == []


def test_cli_trace(sample_folder, capsys):
    out = sample_folder / "traced.json"
    assert main(["trace", str(sample_folder / "sheet.png"), "--out", str(out), "--px-per-inch", "2", "--up", "east"]) == 0
    plan = json.loads(out.read_text())
    assert validate_plan(plan) == []
    assert plan["compass"]["up"] == "east"
    assert "walls" in capsys.readouterr().out
