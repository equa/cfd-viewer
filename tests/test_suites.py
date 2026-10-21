"""The two script suites, under pytest.

``tests/test_pipeline.py`` and ``tests/check_client.py`` stay plain scripts that
run directly (their check-by-check output is the point of them); this runs each
as a subprocess, so their module-level counters and the VTK state stay out of
the pytest process, and fails with the script's tail when the script fails.

The browser suite is marked ``browser``: ``doit test_py`` deselects it and
``doit test_ui`` runs only it, because it needs Chromium and a few minutes.
Both need what the scripts need -- a built demo case, and for the pipeline's
render checks a GL backend (OSMesa or EGL; CLAUDE.md, "Environment").
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _run(script, *args):
    result = subprocess.run(
        [sys.executable, str(ROOT / "tests" / script), *args],
        cwd=ROOT, capture_output=True, text=True,
    )
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-40:])
    assert result.returncode == 0, f"{script} failed:\n{tail}"


def test_pipeline_suite():
    _run("test_pipeline.py")


@pytest.mark.browser
def test_browser_suite(tmp_path):
    _run("check_client.py", "--out", str(tmp_path / "shots"))
