"""tools/exit_policy_lab.py 的驱动器测试 —— 全合成书:管道结构、基线与管线恒等、
退出改变成交、零成本孪生成本口径、制品确定性(两跑逐键一致)、预注册规则入制品。
不碰真实 DB/JSON/网络。"""
from __future__ import annotations

import json

import pytest

from services.execution.backtest_authority import (
    BacktestIntegrityBlocked,
    LEGACY_INVALIDATION_CODE,
)
from services.execution.dry_run_pipeline import (
    _run_multi_period_dry_run_legacy_diagnostic,
)
from services.research import exit_policies as ep
from tools import exit_policy_lab as lab

_DAY = 86_400_000
_T0 = 1_700_000_000_000
_POLICIES = ["baseline_signal_only", "fixed_stop", "risk_budget_reduce", "time_stop"]


def _synth_book(prices, *, cost_bps=10.0, weight=0.5):
    """单标的合成书:每 7 天一期,每期一根日线 bar(收=price,高低±1),恒定信号权重。"""
    periods = [_T0 + i * 7 * _DAY for i in range(len(prices))]
    bars = tuple(ep.ExitBar(t, px, px + 1.0, px - 1.0, px) for t, px in zip(periods, prices))
    return lab.LabBook(
        "synth", "synth_long", periods,
        {t: {"A": weight} for t in periods},
        {t: {"A": px} for t, px in zip(periods, prices)},
        {"A": bars}, cost_bps, {"target_ann": 0.10, "lookback": 8, "lev_cap": 3.0, "ppy": 52},
        cash_flow_fn=None, data_source="SYNTHETIC", note="synthetic plumbing book")


def _run_book(book, *, policies=None):
    return lab._run_book_legacy_diagnostic(
        book,
        runner=_run_multi_period_dry_run_legacy_diagnostic,
        policies=policies,
    )


def test_main_quarantines_before_history_read_or_artifact_write(monkeypatch, tmp_path):
    output = tmp_path / "exit_policy_lab.json"
    monkeypatch.setattr(lab, "_OUT", str(output))
    monkeypatch.setattr(
        lab,
        "build_crowd_book",
        lambda: pytest.fail("legacy history was read before quarantine"),
    )
    monkeypatch.setattr(
        lab,
        "atomic_write_json",
        lambda *_args, **_kwargs: pytest.fail("artifact write bypassed quarantine"),
    )

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        lab.main()

    assert not output.exists()


def test_public_run_book_quarantines_before_book_policy_or_gate_access(monkeypatch):
    monkeypatch.setattr(
        lab,
        "_make_gate_and_ctx",
        lambda *_args, **_kwargs: pytest.fail("gate constructed before quarantine"),
    )
    monkeypatch.setattr(
        ep,
        "apply_exit_policy",
        lambda *_args, **_kwargs: pytest.fail("policy evaluated before quarantine"),
    )

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        lab.run_book(object(), policies=object())


def test_run_book_structure_and_residuals():
    book = _synth_book([100, 103, 101, 99, 104, 106, 105, 108, 110, 112])
    res = _run_book(book, policies=_POLICIES)
    for key in ("strategy_id", "baseline", "policies", "rankings", "verdict",
                "worth_further_study", "n_periods", "cost_bps"):
        assert key in res
    base = res["baseline"]
    assert base["signal_only_assessment"] == "PROFITABLE_WITH_SIGNAL_ONLY_EXITS"
    assert set(res["policies"]) == {"fixed_stop", "risk_budget_reduce", "time_stop"}
    for name, p in res["policies"].items():
        for key in ("net_pnl", "sharpe", "max_drawdown", "profit_factor", "right_tail_share",
                    "trade_count", "cost_total", "delta_net_pnl", "delta_sharpe",
                    "delta_max_drawdown", "delta_profit_factor", "concentration",
                    "selection", "selection_warning", "policy_version"):
            assert key in p, (name, key)
        assert abs(p["residual"]) < 1e-6          # 修改权重流后对账恒等式仍然成立
    assert abs(base["residual"]) < 1e-6
    ranks = res["rankings"]
    assert len(ranks["by_sharpe_delta_desc"]) == len(ranks["by_maxdd_delta_asc"]) == 3
    assert "net PnL alone is forbidden" in ranks["note"]


def test_baseline_matches_direct_pipeline_run():
    prices = [100, 102, 101, 105, 103, 107, 106, 109]
    book = _synth_book(prices)
    res = _run_book(book, policies=_POLICIES)
    gate, ctx = lab._make_gate_and_ctx(book)
    direct = lab._run_stream(
        book,
        book.weights_by_period,
        book.cost_bps,
        gate,
        ctx,
        "direct",
        runner=_run_multi_period_dry_run_legacy_diagnostic,
    )
    assert res["baseline"]["sharpe"] == direct.pnl["sharpe"]
    assert res["baseline"]["net_pnl"] == pytest.approx(direct.pnl["final_equity"] - lab._AUM)


def test_fixed_stop_cuts_crash_and_locks_out():
    # 期3 崩 -20%(>10% 止损)→ EXIT + 锁定至书末(信号恒 0.5 不复位)
    book = _synth_book([100, 100, 100, 80, 79, 78, 120, 130, 140, 150])
    res = _run_book(book, policies=_POLICIES)
    pol = res["policies"]["fixed_stop"]
    assert pol["n_exit_events"] == 1 and pol["n_lockout_periods"] == 6
    assert pol["delta_max_drawdown"] <= 0.0       # 砍掉崩溃段 → 回撤不劣于基线
    assert pol["delta_max_drawdown"] < 0.0
    assert res["baseline"]["max_drawdown"] > 0.1  # 基线确实吃了这段回撤
    # time_stop(8 期):前 8 期持有,第 9 期决策(periods_held=8)强平
    ts = res["policies"]["time_stop"]
    assert ts["n_exit_events"] == 1 and ts["n_lockout_periods"] == 1


def test_zero_cost_twin_cost_total():
    book = _synth_book([100, 101, 102, 103, 104, 105, 106, 107], cost_bps=10.0)
    res = _run_book(book, policies=_POLICIES)
    base = res["baseline"]
    assert base["cost_total"] > 0
    # 独立复算:直接各跑一遍 0/10bps,最终权益差必须等于 cost_total(孪生口径非恒真)
    gate, ctx = lab._make_gate_and_ctx(book)
    rc = lab._run_stream(
        book,
        book.weights_by_period,
        10.0,
        gate,
        ctx,
        "chk_c10",
        runner=_run_multi_period_dry_run_legacy_diagnostic,
    )
    gate2, ctx2 = lab._make_gate_and_ctx(book)
    r0 = lab._run_stream(
        book,
        book.weights_by_period,
        0.0,
        gate2,
        ctx2,
        "chk_c0",
        runner=_run_multi_period_dry_run_legacy_diagnostic,
    )
    assert base["cost_total"] == pytest.approx(
        r0.pnl["final_equity"] - rc.pnl["final_equity"], abs=1e-6)


def test_artifact_determinism_two_runs_identical():
    prices = [100, 103, 99, 97, 102, 105, 101, 98, 104, 108, 110, 106]
    r1 = _run_book(_synth_book(prices), policies=_POLICIES)
    r2 = _run_book(_synth_book(prices), policies=_POLICIES)
    assert r1 == r2
    r1["_as_of"] = r2["_as_of"] = 123456789
    a1 = lab.build_artifact({"synth": r1})
    a2 = lab.build_artifact({"synth": r2})
    assert a1 == a2
    blob = json.dumps(a1, allow_nan=False, ensure_ascii=False)   # inf/nan 会在此炸出
    assert len(blob) > 1000
    assert "_as_of" not in blob


def test_selection_rule_and_definitions_in_artifact():
    res = _run_book(_synth_book([100, 101, 100, 102]), policies=_POLICIES)
    artifact = lab.build_artifact({"synth": res})
    rule = artifact["selection_rule"]
    assert rule["version"] == ep.SELECTION_RULE_VERSION
    assert "FORBIDDEN" in rule["text"] and "Sharpe delta > 0" in rule["text"]
    assert artifact["provenance"] == "REAL_EXIT_POLICY_LAB"
    assert artifact["readiness"] == "L2_DRY_RUN_ONLY"
    for key in ("profit_factor", "right_tail_share", "delta_concentration", "cost_total"):
        assert key in artifact["metric_definitions"]
    assert len(artifact["policies"]) == len(ep.POLICY_REGISTRY)
    assert all(p["threshold_provenance"] == "UNCALIBRATED_ENGINEERING_DEFAULT"
               for p in artifact["policies"])
    assert artifact["honest_limits"]
