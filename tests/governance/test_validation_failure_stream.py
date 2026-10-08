"""Completed failures remain identifiable when a later pytest test times out.

The original runner and an actual pytest child stream to a real file. This
covers inherited stdout, not the runner's bounded fallback capture tail.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
STAGES = (
    "pytest: all suites (contracts, governance, research, trading)",
    "pytest: live execution stack (live, trading, tools)",
)


@pytest.mark.parametrize("stage", STAGES)
def test_failed_nodeid_is_streamed_before_later_stage_timeout(tmp_path, stage):
    case = tmp_path / "test_stream.py"
    marker = tmp_path / "entered-block"
    report = tmp_path / "validation.json"
    log = tmp_path / "canonical.log"
    parameter = "retained-failure-" + "abcdef0123456789" * 4
    nodeid = f"test_stream.py::test_00_failure[{parameter}]"
    case.write_text(
        "import time\nfrom pathlib import Path\nimport pytest\n\n"
        f"@pytest.mark.parametrize('value', [1], ids=[{parameter!r}])\n"
        "def test_00_failure(value):\n    assert value == 0\n\n"
        "def test_01_block():\n"
        f"    Path({str(marker)!r}).write_text('entered', encoding='utf-8')\n"
        "    time.sleep(30)\n",
        encoding="utf-8",
    )
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from scripts import validate\n"
        f"name = {stage!r}\n"
        "declared = next(command for label, command, cwd in validate.CHECKS if label == name)\n"
        # Use the actual main stage's Python/pytest/verbosity prefix; the
        # isolated two-test selection avoids running any repository suite.
        f"command = declared[:4] + [{str(case)!r}]\n"
        "raise SystemExit(validate.run(\n"
        f"    stages=[name], json_report={str(report)!r}, timeout_seconds=3,\n"
        f"    checks=[(name, command, {str(tmp_path)!r})]))\n",
        encoding="utf-8",
    )
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PY_COLORS="0")
    env.pop("PYTEST_ADDOPTS", None)
    with log.open("wb") as stream:
        completed = subprocess.run(
            [sys.executable, "-B", str(driver)], cwd=tmp_path, env=env,
            stdout=stream, stderr=subprocess.STDOUT, timeout=15, check=False,
        )
    assert marker.read_text(encoding="utf-8") == "entered"
    result = json.loads(report.read_text(encoding="utf-8"))
    assert completed.returncode == 1 and result["overall_status"] == "FAIL"
    assert result["summary"]["timed_out"] == 1
    assert result["results"][0]["status"] == "TIMEOUT"
    assert result["results"][0]["timeout_seconds"] == 3
    output = log.read_text(encoding="utf-8")
    assert nodeid + " FAILED" in output, output
    assert output.index(nodeid + " FAILED") < output.index(f"[validate] END {stage}: TIMEOUT")
