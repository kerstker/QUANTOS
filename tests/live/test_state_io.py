"""live/execution/state_io.py —— 原子文件状态落盘(2026-07-06,安全审查 punch-list 修复:
peak_equity/day_state 此前 `json.dump(obj, open(path, "w"))` 非原子,进程被杀在写一半时会
留下截断 JSON,下次加载 JSONDecodeError -> 现有 fail-open 逻辑悄悄当成"无历史状态"重置。
"""
import json
import os

import pytest

from live.execution.state_io import atomic_write_json


def _record_equity(path, *, equity, ts=1, source="test"):
    from live.execution.equity_state import append_equity_observation

    append_equity_observation(
        str(path),
        {"ts": ts, "equity": equity, "source": source},
    )


def test_atomic_write_json_creates_file_with_content(tmp_path):
    path = str(tmp_path / "state.json")
    atomic_write_json(path, {"peak_equity": 100.0})
    assert json.load(open(path, encoding="utf-8")) == {"peak_equity": 100.0}


def test_atomic_write_json_overwrites_existing_file(tmp_path):
    path = str(tmp_path / "state.json")
    atomic_write_json(path, {"peak_equity": 100.0})
    atomic_write_json(path, {"peak_equity": 200.0})
    assert json.load(open(path, encoding="utf-8")) == {"peak_equity": 200.0}


def test_atomic_write_json_leaves_original_untouched_on_serialization_error(tmp_path):
    path = str(tmp_path / "state.json")
    atomic_write_json(path, {"peak_equity": 100.0})
    with pytest.raises(TypeError):
        atomic_write_json(path, {"peak_equity", "not json serializable as a set"})
    # 原文件必须完好——绝不能留下一份写了一半的损坏内容
    assert json.load(open(path, encoding="utf-8")) == {"peak_equity": 100.0}


def test_atomic_write_json_does_not_leave_temp_file_behind(tmp_path):
    path = str(tmp_path / "state.json")
    atomic_write_json(path, {"peak_equity": 100.0})
    try:
        atomic_write_json(path, {"peak_equity", "bad"})
    except TypeError:
        pass
    leftovers = [f for f in os.listdir(str(tmp_path)) if f != "state.json"]
    assert leftovers == []


def test_missing_peak_state_initializes_from_observed_current_equity(tmp_path):
    from live.execution.equity_state import load_validated_peak_equity, max_observed_equity

    history = tmp_path / "equity.jsonl"
    state = tmp_path / "risk_state.json"
    _record_equity(history, equity=100.0)

    checked = load_validated_peak_equity(
        str(state),
        current_equity=100.0,
        observed_max_equity=max_observed_equity(str(history)),
    )

    assert checked.valid is True
    assert checked.peak_equity == 100.0
    assert checked.reason == "initialized_from_current_equity"


def test_valid_peak_state_is_bounded_by_observed_equity_history(tmp_path):
    from live.execution.equity_state import load_validated_peak_equity, max_observed_equity

    history = tmp_path / "equity.jsonl"
    state = tmp_path / "risk_state.json"
    _record_equity(history, equity=120.0, ts=1)
    _record_equity(history, equity=100.0, ts=2)
    atomic_write_json(str(state), {"peak_equity": 120.0})

    checked = load_validated_peak_equity(
        str(state),
        current_equity=100.0,
        observed_max_equity=max_observed_equity(str(history)),
    )

    assert checked.valid is True
    assert checked.peak_equity == 120.0
    assert checked.reason == "verified_against_equity_history"


@pytest.mark.parametrize("bad_peak", [float("nan"), float("inf"), -1.0, 0.0, True, "100"])
def test_invalid_peak_state_fails_closed(tmp_path, bad_peak):
    from live.execution.equity_state import load_validated_peak_equity

    state = tmp_path / "risk_state.json"
    atomic_write_json(str(state), {"peak_equity": bad_peak})

    checked = load_validated_peak_equity(
        str(state), current_equity=100.0, observed_max_equity=100.0
    )

    assert checked.valid is False
    assert checked.peak_equity == 0.0
    assert checked.reason == "invalid_persisted_peak_equity"


def test_peak_above_observed_history_cannot_trigger_single_round_auto_flatten(tmp_path):
    from live.execution.emergency import flatten_target_for_breach
    from live.execution.equity_state import load_validated_peak_equity
    from live.execution.risk_monitor import evaluate_risk_state

    state = tmp_path / "risk_state.json"
    atomic_write_json(str(state), {"peak_equity": 1_000_000_000.0})

    checked = load_validated_peak_equity(
        str(state), current_equity=100.0, observed_max_equity=120.0
    )
    breach = evaluate_risk_state(
        equity=100.0,
        peak_equity=checked.peak_equity,
        positions={"BTC": 1.0},
        prices={"BTC": 10.0},
        max_drawdown_pct=0.20,
        max_gross_exposure=100.0,
        max_net_exposure=100.0,
    )

    assert checked.valid is False
    assert checked.reason == "peak_equity_exceeds_observed_history"
    assert breach.reasons == ("peak_equity_unknown",)
    assert flatten_target_for_breach(
        breach, max_gross_exposure=100.0, max_net_exposure=100.0
    ) is None


def test_record_and_validate_peak_preserves_invalid_state_for_forensics(tmp_path):
    from live.execution.equity_state import record_and_validate_peak_equity

    history = tmp_path / "equity.jsonl"
    state = tmp_path / "risk_state.json"
    atomic_write_json(str(state), {"peak_equity": 1_000_000_000.0})

    checked = record_and_validate_peak_equity(
        state_path=str(state),
        history_path=str(history),
        observation={"ts": 123, "equity": 100.0, "source": "risk_sentinel"},
    )

    assert checked.valid is False
    assert checked.peak_equity == 0.0
    assert json.load(open(state, encoding="utf-8")) == {"peak_equity": 1_000_000_000.0}


def test_record_and_validate_peak_writes_versioned_provenance(tmp_path):
    from live.execution.equity_state import record_and_validate_peak_equity

    history = tmp_path / "equity.jsonl"
    state = tmp_path / "risk_state.json"

    checked = record_and_validate_peak_equity(
        state_path=str(state),
        history_path=str(history),
        observation={"ts": 123, "equity": 100.0, "source": "live_track"},
    )

    assert checked.valid is True
    assert json.load(open(state, encoding="utf-8")) == {
        "schema_version": 2,
        "peak_equity": 100.0,
        "last_observed_equity": 100.0,
        "last_observed_at_ms": 123,
        "evidence_max_equity": 100.0,
    }
