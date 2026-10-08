from types import SimpleNamespace

import pytest

from tools import binance_depth_snapshot_process as process


def test_depth_snapshot_process_uses_fixed_module_without_shell(monkeypatch):
    observed = {}

    def run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(
            stdout='{"event_time_ms":99,"last_update_id":11}',
            returncode=0,
        )

    monkeypatch.setattr(process.subprocess, "run", run)

    snapshot = process.run_depth_snapshot("btcusdt", observed_at_ms=100)

    assert snapshot == {"event_time_ms": 99, "last_update_id": 11}
    assert observed["command"][1:4] == [
        "-m",
        "tools.binance_depth_snapshot_process",
        "--symbol",
    ]
    assert observed["kwargs"]["shell"] is False
    assert observed["kwargs"]["timeout"] == 45.0


@pytest.mark.parametrize(
    ("returncode", "stdout", "message"),
    (
        (1, "", "process failed"),
        (0, "not-json", "malformed output"),
        (0, "{}", "inconsistent result"),
    ),
)
def test_depth_snapshot_result_is_fail_closed(returncode, stdout, message):
    with pytest.raises(process.DepthSnapshotProcessError, match=message):
        process.parse_depth_snapshot_result(
            stdout=stdout,
            returncode=returncode,
        )
