"""live/execution/parity.py —— live-paper 对账(2026-07-05)。设计:
docs/superpowers/specs/2026-07-05-institutional-live-stack-design.md §④。

把 Stage 0 前向纸面台账里**记录在案**的目标权重(不是重算的信号——重算会掩盖信号漂移)和
live 账本的真实仓位/订单按 decided_ts 对齐:每标的目标名义额 vs 实际名义额、缺口、订单归因
(no_order = mandate/过滤器拦截或根本没提交)。纯函数,IO 在驱动器边界。
"""
import pytest

from live.execution.filters import client_order_id
from live.execution.parity import paper_weights_from_forward_ledger, parity_report
from services.research.paper_trading.forward import ForwardLedger

_NOW = 1_700_000_000_000
_PRICES = {"BTC": 60000.0, "ETH": 3000.0}
_AUM = 6000.0


def _orders_for(weights, statuses, strategy_id="crowd_contra", decided_ts=_NOW):
    return [{"client_order_id": client_order_id(strategy_id, decided_ts, inst),
             "instrument_id": inst, "status": statuses.get(inst, "FILLED")}
            for inst in weights if inst in statuses]


def test_perfect_parity_has_zero_gaps():
    weights = {"BTC": 0.5, "ETH": -0.5}
    positions = {"BTC": 0.5 * _AUM / 60000.0, "ETH": -0.5 * _AUM / 3000.0}
    rep = parity_report(paper_weights=weights, paper_source="forward_ledger", aum=_AUM,
                        prices=_PRICES, ledger_positions=positions,
                        ledger_orders=_orders_for(weights, {"BTC": "FILLED", "ETH": "FILLED"}),
                        strategy_id="crowd_contra", decided_ts=_NOW, mode="submit")
    assert rep["schema"] == "live_parity.v1"
    assert rep["n_paper_legs"] == 2 and rep["n_with_live_order"] == 2
    assert rep["max_abs_gap_notional"] == pytest.approx(0.0, abs=1e-9)
    assert rep["unexpected_live_positions"] == []
    by_inst = {e["instrument_id"]: e for e in rep["per_instrument"]}
    assert by_inst["BTC"]["order_status"] == "FILLED"
    assert by_inst["BTC"]["gap_notional"] == pytest.approx(0.0, abs=1e-9)


def test_blocked_execution_shows_full_gap_and_no_order():
    weights = {"BTC": 0.5}
    rep = parity_report(paper_weights=weights, paper_source="forward_ledger", aum=_AUM,
                        prices=_PRICES, ledger_positions={}, ledger_orders=[],
                        strategy_id="crowd_contra", decided_ts=_NOW, mode="preview")
    e = rep["per_instrument"][0]
    assert e["order_status"] == "no_order"                     # mandate/过滤器拦截的诚实归因
    assert e["gap_notional"] == pytest.approx(-0.5 * _AUM)     # 实际 0 - 目标 3000
    assert rep["n_with_live_order"] == 0
    assert rep["mode"] == "preview"                            # preview/submit 口径如实标注


def test_partial_execution_shows_partial_gap():
    weights = {"BTC": 0.5}
    positions = {"BTC": 0.25 * _AUM / 60000.0}                 # 只成交了一半
    rep = parity_report(paper_weights=weights, paper_source="forward_ledger", aum=_AUM,
                        prices=_PRICES, ledger_positions=positions,
                        ledger_orders=_orders_for(weights, {"BTC": "CANCELED"}),
                        strategy_id="crowd_contra", decided_ts=_NOW, mode="submit")
    e = rep["per_instrument"][0]
    assert e["gap_notional"] == pytest.approx(-0.25 * _AUM)
    assert e["order_status"] == "CANCELED"                     # 超时撤单的部分成交,归因可见


def test_unexpected_live_position_is_flagged():
    rep = parity_report(paper_weights={"BTC": 0.5}, paper_source="forward_ledger", aum=_AUM,
                        prices=_PRICES,
                        ledger_positions={"BTC": 0.05, "DOGE": 1000.0},   # DOGE 不在纸面决策里
                        ledger_orders=[], strategy_id="crowd_contra", decided_ts=_NOW,
                        mode="submit")
    assert rep["unexpected_live_positions"] == ["DOGE"]


def test_missing_price_is_honest_none_not_counted_in_gaps():
    weights = {"BTC": 0.5, "GHOST": 0.5}
    positions = {"BTC": 0.5 * _AUM / 60000.0}
    rep = parity_report(paper_weights=weights, paper_source="forward_ledger", aum=_AUM,
                        prices=_PRICES, ledger_positions=positions, ledger_orders=[],
                        strategy_id="crowd_contra", decided_ts=_NOW, mode="submit")
    by_inst = {e["instrument_id"]: e for e in rep["per_instrument"]}
    assert by_inst["GHOST"]["actual_notional"] is None         # 缺价,诚实 None 不硬凑
    assert by_inst["GHOST"]["gap_notional"] is None
    assert rep["max_abs_gap_notional"] == pytest.approx(0.0, abs=1e-9)   # 只统计可计算的


def test_paper_weights_loaded_from_recorded_forward_decision(tmp_path):
    db = str(tmp_path / "paper_track.db")
    fwd = ForwardLedger(db, create=True)
    fwd.record_decision("crowd_contra", _NOW, recorded_at=_NOW + 500,
                        weights={"BTC": 0.5, "ETH": -0.5}, signals={"BTC": 1.0, "ETH": -1.0})
    w = paper_weights_from_forward_ledger(fwd, "crowd_contra", _NOW)
    assert w == {"BTC": 0.5, "ETH": -0.5}


def test_paper_weights_missing_decision_returns_none(tmp_path):
    db = str(tmp_path / "paper_track.db")
    fwd = ForwardLedger(db, create=True)
    assert paper_weights_from_forward_ledger(fwd, "crowd_contra", _NOW) is None
    fwd.record_decision("crowd_contra", _NOW, recorded_at=_NOW, weights={"BTC": 1.0}, signals={})
    assert paper_weights_from_forward_ledger(fwd, "crowd_contra", _NOW + 1) is None   # 不同期
    assert paper_weights_from_forward_ledger(fwd, "other_alpha", _NOW) is None        # 不同策略
