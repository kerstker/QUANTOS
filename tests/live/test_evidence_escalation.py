"""live/execution/evidence_escalation.py —— live-paper 偏差自动升级(2026-07-05,机构审计
P1 quick win)。

背景:真实数据里出现过 parity gap≈7% AUM、TCA 滑点 942bps 都没有触发任何有别于日常
alerts 列表的信号——都被淹没在一堆普通的 info 级 alert 里。本模块是纯决策函数:超阈值
才返回非空的"升级"信息(供调用方用不同严重度渠道处理,如 emit 到独立告警通道/未来的
死人开关),阈值内不产生噪音。
"""
import pytest

from live.execution.evidence_escalation import check_evidence_deviation


def _tca(weighted_bps=None, assumed_bps=10.0, n_fills=1):
    return {"notional_weighted_slippage_bps": weighted_bps, "assumed_cost_bps": assumed_bps,
           "n_with_slippage": n_fills,
           "weighted_minus_assumed_bps": (weighted_bps - assumed_bps) if weighted_bps is not None else None}


def test_no_escalation_when_everything_within_bounds():
    out = check_evidence_deviation({"max_abs_gap_notional": 10.0}, _tca(weighted_bps=12.0),
                                   aum=10000.0, max_gap_pct_of_aum=0.05,
                                   max_slippage_over_assumed_bps=50.0)
    assert out == []


def test_escalates_when_parity_gap_exceeds_pct_of_aum():
    # 真实事故复现:gap≈7% AUM 应该升级
    out = check_evidence_deviation({"max_abs_gap_notional": 700.0}, _tca(weighted_bps=12.0),
                                   aum=10000.0, max_gap_pct_of_aum=0.05,
                                   max_slippage_over_assumed_bps=50.0)
    assert len(out) == 1 and "parity gap" in out[0].lower()


def test_escalates_when_slippage_far_exceeds_assumption():
    # 真实事故复现:942bps 滑点应该升级(远超默认 10bps 假设)
    out = check_evidence_deviation({"max_abs_gap_notional": 0.0}, _tca(weighted_bps=952.0),
                                   aum=10000.0, max_gap_pct_of_aum=0.05,
                                   max_slippage_over_assumed_bps=50.0)
    assert len(out) == 1 and "slippage" in out[0].lower()


def test_both_can_escalate_independently():
    out = check_evidence_deviation({"max_abs_gap_notional": 700.0}, _tca(weighted_bps=952.0),
                                   aum=10000.0, max_gap_pct_of_aum=0.05,
                                   max_slippage_over_assumed_bps=50.0)
    assert len(out) == 2


def test_missing_gap_or_tca_data_does_not_crash():
    out = check_evidence_deviation({"max_abs_gap_notional": None}, _tca(weighted_bps=None),
                                   aum=10000.0, max_gap_pct_of_aum=0.05,
                                   max_slippage_over_assumed_bps=50.0)
    assert out == []


def test_zero_aum_does_not_divide_by_zero():
    out = check_evidence_deviation({"max_abs_gap_notional": 100.0}, _tca(weighted_bps=12.0),
                                   aum=0.0, max_gap_pct_of_aum=0.05,
                                   max_slippage_over_assumed_bps=50.0)
    assert out == []                                    # 无法算百分比,诚实不升级(不是误报"正常")


def test_negative_slippage_improvement_never_escalates():
    # 滑点为负(价格改善)不该被当成"偏差"升级,即便绝对值很大
    out = check_evidence_deviation({"max_abs_gap_notional": 0.0}, _tca(weighted_bps=-500.0),
                                   aum=10000.0, max_gap_pct_of_aum=0.05,
                                   max_slippage_over_assumed_bps=50.0)
    assert out == []
