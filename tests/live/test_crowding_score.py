"""live/execution/crowding_score.py —— 截面拥挤度分数(2026-07-05,机构审计 P1 quick win)。

crowd_contra 本身就是"反着做拥挤的散户多空仓位"因子(CS_RANK(NEG(global_ls)))——`crowding_gate`
(services/portfolio/crowding_gate.py)已建成测试却从未接入真实下单路径,因为缺一个"这条腿
到底有多拥挤"的证据源。这里量化:global_ls 偏离 1.0(多空平衡)越远 = 越拥挤,截面分位排名
到 [0,1]。
"""
import pytest

from live.execution.crowding_score import compute_crowding_scores


def test_balanced_ratio_gets_low_crowding_rank():
    scores = compute_crowding_scores({"BTC": 1.0, "ETH": 3.0, "SOL": 0.2})
    assert scores["BTC"] < scores["ETH"]
    assert scores["BTC"] < scores["SOL"]


def test_scores_are_normalized_to_unit_interval():
    scores = compute_crowding_scores({"A": 1.0, "B": 2.0, "C": 5.0, "D": 0.1})
    assert min(scores.values()) == pytest.approx(0.0)
    assert max(scores.values()) == pytest.approx(1.0)
    assert all(0.0 <= v <= 1.0 for v in scores.values())


def test_extreme_deviation_either_direction_ranks_high():
    # 多头拥挤(global_ls 很大)和空头拥挤(global_ls 接近 0)都该排到高分——crowding 不分方向
    scores = compute_crowding_scores({"BALANCED": 1.0, "LONG_CROWDED": 10.0, "SHORT_CROWDED": 0.05})
    assert scores["LONG_CROWDED"] > scores["BALANCED"]
    assert scores["SHORT_CROWDED"] > scores["BALANCED"]


def test_none_values_are_dropped_not_crashed():
    scores = compute_crowding_scores({"BTC": 1.0, "GHOST": None, "ETH": 2.0})
    assert "GHOST" not in scores
    assert set(scores) == {"BTC", "ETH"}


def test_empty_input_returns_empty():
    assert compute_crowding_scores({}) == {}


def test_single_instrument_gets_neutral_score():
    # 单标的没有截面可排,给中性分(不是 0 也不是 1,避免制造假信号)
    scores = compute_crowding_scores({"BTC": 5.0})
    assert scores["BTC"] == pytest.approx(0.5)


def test_ties_get_consistent_ranks():
    scores = compute_crowding_scores({"A": 1.0, "B": 1.0, "C": 5.0})
    assert scores["A"] == scores["B"]
    assert scores["C"] > scores["A"]
