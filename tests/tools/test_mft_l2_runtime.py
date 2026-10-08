from __future__ import annotations

import json
from pathlib import Path

from quantos.contracts.scheduled_worker import SCHEDULE_EXIT_FAILURE
from services.orchestration.mft_runtime import open_mft_runtime
from tools.mft_l2_runtime import build_parser, main, run_resident_loop


T0 = 1_700_000_000_000


def test_parser_exposes_only_l2_runtime_controls():
    parser = build_parser()
    options = {option for action in parser._actions for option in action.option_strings}
    assert options == {
        "-h",
        "--help",
        "--state-root",
        "--owner-id",
        "--interval-ms",
        "--lease-ms",
        "--poll-seconds",
        "--once",
    }
    source = Path("tools/mft_l2_runtime.py").read_text(encoding="utf-8").lower()
    assert "api_key" not in source
    assert "secret_key" not in source
    assert "submit_order" not in source


def test_resident_loop_uses_injected_clock_and_sleep(tmp_path):
    runtime = open_mft_runtime(str(tmp_path / "mft.db"), create=True)
    runtime.register_tasks(
        interval_ms=1_000, first_due_ms=T0, now_ms=T0 - 1_000
    )
    times = iter((T0, T0 + 1))
    sleeps = []
    statuses = []
    try:
        result = run_resident_loop(
            runtime,
            owner_id="node-a",
            lease_ms=60_000,
            poll_seconds=0.25,
            max_iterations=2,
            clock_ms=lambda: next(times),
            sleeper=sleeps.append,
            on_result=lambda item: statuses.append(item["runtime_status"]),
        )
        assert statuses == ["BLOCKED", "BLOCKED"]
        assert sleeps == [0.25]
        assert result["runtime_status"] == "BLOCKED"
    finally:
        runtime.close()


def test_once_cli_persists_honest_pre_task32_block(tmp_path):
    exit_code = main(
        [
            "--state-root",
            str(tmp_path),
            "--interval-ms",
            "1000",
            "--lease-ms",
            "60000",
            "--once",
        ],
        clock_ms=lambda: T0,
        sleeper=lambda _seconds: None,
    )
    assert exit_code == SCHEDULE_EXIT_FAILURE
    status = json.loads((tmp_path / "mft_l2_runtime_status.json").read_text())
    assert status["runtime_status"] == "BLOCKED"
    assert status["live_allowed"] is False
    assert status["order_submission_allowed"] is False

    reopened = open_mft_runtime(str(tmp_path / "mft_l2_runtime.db"))
    try:
        dispatch = reopened.checkpoints.latest(T0, "execution_dispatch")
        assert dispatch is not None
        assert dispatch.reason_code == "EXECUTION_DISPATCH_DISABLED_BEFORE_TASK_32"
    finally:
        reopened.close()
