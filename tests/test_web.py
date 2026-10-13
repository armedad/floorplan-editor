import shutil
import subprocess
from pathlib import Path

import pytest

WEB_TESTS = Path(__file__).resolve().parent / "web"


def test_browser_modules_with_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    result = subprocess.run([node, "--test", *sorted(str(p) for p in WEB_TESTS.glob("*.test.mjs"))], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
