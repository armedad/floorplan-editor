import shutil
import subprocess
from pathlib import Path

import pytest

WEB_TESTS = Path(__file__).resolve().parent / "web"


def _node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return node


def test_browser_modules_with_node():
    result = subprocess.run([_node(), "--test", *sorted(str(p) for p in WEB_TESTS.glob("*.test.mjs"))], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
