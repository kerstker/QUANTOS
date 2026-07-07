"""Phase 5 item 1 (P1-7):alpha 维护 report -> 每策略 live 准入状态桥。纯函数,镜像
firm_gate_skip_reason 的 fail-safe 语义:只在 KNOWN 降级(RETIRED/PAUSED)时产生"停止新增"skip,
DEGRADED 只观察不停,未知/缺策略/坏输入 -> None(不伪造 block)。"""
from services.alpha.live_linkage import (
    strategy_status_from_report,
    maintenance_skip_reason,
)


def test_empty_report_yields_empty_status():
    # fail-closed:没有证据 = 不知道有降级 = 空状态(不产生任何 skip)
    assert strategy_status_from_report({}) == {}


def test_non_dict_report_yields_empty():
    assert strategy_status_from_report(None) == {}
    assert strategy_status_from_report("nope") == {}


def test_retired_paused_degraded_mapped_with_reasons():
    report = {
        "a1": {"action": "RETIRED", "reasons": ["decay", "regime"]},
        "a2": {"action": "PAUSED", "alerts": ["ic_drop"]},
        "a3": {"action": "DEGRADED", "alerts": ["slippage_up"]},
    }
    out = strategy_status_from_report(report)
    assert out["a1"] == {"state": "RETIRED", "reasons": ["decay", "regime"]}
    assert out["a2"] == {"state": "PAUSED", "reasons": ["ic_drop"]}
    assert out["a3"] == {"state": "DEGRADED", "reasons": ["slippage_up"]}


def test_error_entry_surfaces_reason_but_state_ok():
    # 评估某 alpha 时抛错不是"降级证据"——state 保持 OK(不停 live),但错误诚实进 reasons 可见
    out = strategy_status_from_report({"a1": {"error": "ValueError"}})
    assert out["a1"]["state"] == "OK"
    assert "error:ValueError" in out["a1"]["reasons"]


def test_skip_reason_only_for_retired_or_paused():
    status = {"strategies": {
        "crowd_contra": {"state": "RETIRED", "reasons": ["decay"]},
        "carry": {"state": "PAUSED", "reasons": []},
        "trend": {"state": "DEGRADED", "reasons": ["x"]},
        "ok_strat": {"state": "OK", "reasons": []},
    }}
    assert maintenance_skip_reason(status, "crowd_contra").startswith("maintenance_retired:")
    assert maintenance_skip_reason(status, "carry").startswith("maintenance_paused:")
    assert maintenance_skip_reason(status, "trend") is None      # DEGRADED 只观察不停
    assert maintenance_skip_reason(status, "ok_strat") is None


def test_skip_reason_failsafe_none_paths():
    assert maintenance_skip_reason(None, "x") is None
    assert maintenance_skip_reason({}, "x") is None
    assert maintenance_skip_reason({"strategies": {}}, "x") is None      # 策略不在案
    assert maintenance_skip_reason("notadict", "x") is None
    assert maintenance_skip_reason({"strategies": "bad"}, "x") is None


def test_skip_reason_string_includes_reasons():
    status = {"strategies": {"s": {"state": "RETIRED", "reasons": ["decay", "regime"]}}}
    r = maintenance_skip_reason(status, "s")
    assert "decay" in r and "regime" in r


def test_skip_reason_falls_back_to_state_when_no_reasons():
    status = {"strategies": {"s": {"state": "PAUSED", "reasons": []}}}
    assert maintenance_skip_reason(status, "s") == "maintenance_paused:PAUSED"
