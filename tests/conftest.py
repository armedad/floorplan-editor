import copy
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "examples" / "sample" / "plan.json"


@pytest.fixture
def sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


@pytest.fixture
def sample_folder(tmp_path, sample) -> Path:
    """A plan folder with plan.json and a sheet image rendered from it."""
    from floorplan_editor.engine import draw_png, scene

    folder = tmp_path / "plans"
    folder.mkdir()
    (folder / "plan.json").write_text(json.dumps(sample, indent=2), encoding="utf-8")
    sheet = copy.deepcopy(sample)
    draw_png(sheet, scene(sheet), (255, 255, 255, 255)).convert("RGB").save(folder / "sheet.png")
    return folder
