"""live/execution/degradation.py(W1 live-vs-replay 衰减纯核)单元测试。

钉死:除法守护(replay≈0/异号/任一侧 None -> None+reason,绝不硬算)、UNKNOWN 传播
(fee_actual/funding_actual 恒 None 进 unknown_fields;缺 mark -> live_net None)、
成分分解恒等式(residual≈0)、合成 fills 的 MTM 与 canonical reconcile 恒等式一致、
TCA 滑点成本/加权 bps、decided_ts trace 反推。
"""
from __future__ import annotations

import pytest

from live.execution import degradation as deg
from live.execution.filters import client_order_id, decision_prefix
from quantos.contracts.paper import FillMethod, OrderSide as PaperSide, PaperFill
from quantos.contracts.trading import OrderSide
from services.execution.paper_reconciliation import reconcile

DAY = 86_400_000
T0 = 1_780_000_000_000            # 合成起点(ms,整日网格)


def _fill(inst, side, qty, price, filled_at, *, cid="c", fid="f"):
    return {"fill_id": fid, "client_order_id": cid, "instrument_id": inst,
            "side": side, "quantity": qty, "price": price, "filled_at": filled_at,
            "strategy_id": "s"}


def _order(inst, side, qty, submitted_at, *, cid="c", status="FILLED"):
    return {"client_order_id": cid, "instrument_id": inst, "side": side,
            "quantity": qty, "submitted_at": submitted_at, "venue_order_id": "v",
            "status": status, "strategy_id": "s", "order_type": "MARKET",
            "limit_price": None, "canonical_state": status}


# ---------------------------------------------------------------------------
# 基础数学
# ---------------------------------------------------------------------------

def test_positions_and_turnover():
    fills = [_fill("BTC", "BUY", 0.5, 100.0, T0, fid="a"),
             _fill("BTC", "SELL", 0.2, 110.0, T0, fid="b"),
             _fill("ETH", "SELL", 2.0, 50.0, T0, fid="c")]
    assert deg.positions_from_fills(fills) == {"BTC": pytest.approx(0.3),
                                               "ETH": pytest.approx(-2.0)}
    assert deg.turnover_notional(fills) == pytest.approx(0.5 * 100 + 0.2 * 110 + 2 * 50)


def test_mtm_pnl_matches_canonical_reconcile_identity():
    """Σ signed_qty×(mark−price) 必须等于 reconcile 的 gross_trading_pnl(平均成本恒等式)。"""
    raw = [("BTC", PaperSide.BUY, 2, 100.0, T0),
           ("BTC", PaperSide.SELL, 1, 120.0, T0 + DAY),
           ("ETH", PaperSide.SELL, 5, 40.0, T0),
           ("ETH", PaperSide.BUY, 2, 44.0, T0 + DAY)]
    pfills = [PaperFill(fill_id=f"f{i}", order_id=f"o{i}", run_id="r", instrument_id=inst,
                        side=side, quantity=qty, price=px, fill_method=FillMethod.NEXT_OPEN,
                        timestamp=ts, commission=0.0, slippage=0.0)
              for i, (inst, side, qty, px, ts) in enumerate(raw)]
    marks = {"BTC": 130.0, "ETH": 38.0}
    res = reconcile(pfills, marks=marks, initial_capital=10_000.0)
    mine, missing = deg.mtm_trading_pnl(
        [_fill(inst, side.value, qty, px, ts, fid=f"f{i}")
         for i, (inst, side, qty, px, ts) in enumerate(raw)], marks)
    assert missing == []
    assert mine == pytest.approx(res.gross_trading_pnl, rel=1e-12)


def test_mtm_pnl_missing_mark_is_unknown_not_estimated():
    pnl, missing = deg.mtm_trading_pnl(
        [_fill("BTC", "BUY", 1, 100.0, T0), _fill("DOGE", "BUY", 10, 1.0, T0, fid="g")],
        {"BTC": 110.0})
    assert pnl is None and missing == ["DOGE"]


def test_positions_by_day_fill_timing_no_lookahead():
    # 当日盘中成交从下一行起进持仓(行 t 的 mark 是 t 时刻可见价)
    fills = [_fill("BTC", "BUY", 1, 100.0, T0 + 6 * 3_600_000)]     # T0 当天 06:00 成交
    grid = [T0, T0 + DAY, T0 + 2 * DAY]
    by_day = deg.positions_by_day(fills, grid)
    assert by_day[0] == {}                       # 当日 00:00 行:尚未入账(不前视)
    assert by_day[1] == {"BTC": 1.0}
    assert by_day[2] == {"BTC": 1.0}


# ---------------------------------------------------------------------------
# TCA / 资金费
# ---------------------------------------------------------------------------

def test_tca_slippage_cost_direction_and_skip():
    rows = [
        {"side": "BUY", "decision_price": 100.0, "fill_price": 101.0, "quantity": 2.0},   # +2
        {"side": "SELL", "decision_price": 50.0, "fill_price": 49.0, "quantity": 4.0},    # +4
        {"side": "BUY", "decision_price": 0.0, "fill_price": 1.0, "quantity": 1.0},       # 非法,跳过
    ]
    cost, skipped = deg.tca_slippage_cost(rows)
    assert cost == pytest.approx(2 * (101 - 100) + 4 * (50 - 49))
    assert skipped == 1
    assert deg.tca_slippage_cost([]) == (None, 0)


def test_weighted_slippage_bps_notional_weighted():
    rows = [
        {"slippage_bps": 10.0, "fill_price": 100.0, "quantity": 1.0},    # notional 100
        {"slippage_bps": 30.0, "fill_price": 100.0, "quantity": 3.0},    # notional 300
        {"slippage_bps": None, "fill_price": 1.0, "quantity": 1.0},      # 不进加权
    ]
    assert deg.weighted_slippage_bps(rows) == pytest.approx((10 * 100 + 30 * 300) / 400)
    assert deg.weighted_slippage_bps([{"slippage_bps": None, "fill_price": 1, "quantity": 1}]) is None


def test_arrival_coverage_none_without_snapshots():
    rows = [{"arrival_price": None, "slippage_bps": 5.0, "fill_price": 1, "quantity": 1}]
    assert deg.arrival_slippage_coverage(rows) == (None, 0)


def test_expected_funding_partial_and_missing():
    grid = [T0, T0 + DAY]
    positions = [{"BTC": 1.0, "ETH": -2.0}, {"BTC": 1.0, "ETH": -2.0}]
    rates = [{"BTC": 0.001, "ETH": -0.002}, {"BTC": 0.001}]              # ETH 第二天缺 rate
    marks = [{"BTC": 100.0, "ETH": 50.0}, {"BTC": 110.0, "ETH": 55.0}]
    total, skipped = deg.expected_funding(positions, grid, rates, marks)
    # day1: -(0.001*1*100) - (-0.002*-2*50) = -0.1 - 0.2 = -0.3 ; day2: -(0.001*1*110) = -0.11
    assert total == pytest.approx(-0.41)
    assert skipped == 1                        # ETH day2 缺 rate -> 偏合计,调用方标 unknown
    none_total, skipped2 = deg.expected_funding(positions, grid, [{}, {}], marks)
    assert none_total is None and skipped2 == 4


def test_fee_expected():
    assert deg.fee_expected(12_345.0, 10.0) == pytest.approx(12_345.0 * 10 / 1e4)


# ---------------------------------------------------------------------------
# 除法守护
# ---------------------------------------------------------------------------

def test_ratio_normal():
    assert deg.degradation_ratio(8.0, 10.0) == (pytest.approx(0.8), None)
    assert deg.degradation_ratio(-4.0, -10.0) == (pytest.approx(0.4), None)


def test_ratio_guards_never_fabricate():
    assert deg.degradation_ratio(None, 10.0) == (None, "live_net_unknown")
    assert deg.degradation_ratio(5.0, None) == (None, "replay_net_unknown")
    assert deg.degradation_ratio(5.0, 0.0)[0] is None
    assert deg.degradation_ratio(5.0, 1e-12) == (None, "replay_net_approx_zero_ratio_meaningless")
    assert deg.degradation_ratio(-5.0, 10.0) == (None, "sign_flip_ratio_meaningless")
    assert deg.degradation_ratio(5.0, -10.0) == (None, "sign_flip_ratio_meaningless")


# ---------------------------------------------------------------------------
# decided_ts trace 反推
# ---------------------------------------------------------------------------

def test_recover_decision_ts_traces_and_discloses_smoke():
    ts = T0
    cid = client_order_id("crowd_contra", ts, "BTC", OrderSide.BUY, 0.5)
    assert cid[:16] == decision_prefix("crowd_contra", ts, "BTC")
    orders = [_order("BTC", "BUY", 0.5, ts + 1000, cid=cid),
              _order("BTC", "BUY", 0.002, ts + 2000, cid="quantos-smoke-123")]
    traced, untraced = deg.recover_decision_ts(orders, "crowd_contra", [ts, ts + DAY])
    assert traced == {cid: ts}
    assert untraced == ["quantos-smoke-123"]


def test_recover_decision_ts_wrong_strategy_no_false_match():
    ts = T0
    cid = client_order_id("crowd_contra", ts, "BTC", OrderSide.BUY, 0.5)
    orders = [_order("BTC", "BUY", 0.5, ts, cid=cid)]
    traced, untraced = deg.recover_decision_ts(orders, "other_strategy", [ts])
    assert traced == {} and untraced == [cid]


# ---------------------------------------------------------------------------
# build_degradation_report 装配
# ---------------------------------------------------------------------------

def _synthetic_book():
    """一个两腿小账本 + 一格决策重放:所有数字可手算。"""
    grid = [T0, T0 + DAY, T0 + 2 * DAY]
    # live: T0 06:00 买 1 BTC @100(决策价 99,滑点 +100bps)、卖 2 ETH @50
    fills = [_fill("BTC", "BUY", 1.0, 100.0, T0 + 6 * 3_600_000, cid="o1", fid="f1"),
             _fill("ETH", "SELL", 2.0, 50.0, T0 + 6 * 3_600_000, cid="o2", fid="f2")]
    orders = [_order("BTC", "BUY", 1.0, T0 + 5 * 3_600_000, cid="o1"),
              _order("ETH", "SELL", 2.0, T0 + 5 * 3_600_000, cid="o2")]
    tca = [{"fill_id": "t1", "client_order_id": "o1", "instrument_id": "BTC", "side": "BUY",
            "decision_price": 99.0, "fill_price": 100.0, "quantity": 1.0,
            "slippage_bps": (100 - 99) / 99 * 1e4, "arrival_price": None},
           {"fill_id": "t2", "client_order_id": "o2", "instrument_id": "ETH", "side": "SELL",
            "decision_price": 50.0, "fill_price": 50.0, "quantity": 2.0,
            "slippage_bps": 0.0, "arrival_price": None}]
    marks = [{"BTC": 100.0, "ETH": 50.0}, {"BTC": 110.0, "ETH": 48.0}, {"BTC": 120.0, "ETH": 45.0}]
    rates = [{"BTC": 0.0, "ETH": 0.0}] * 3
    # replay: T0 行进场(1 BTC @99 + 空 2 ETH @50),10bps 佣金
    replay = {"net_pnl": (120 - 99) * 1 + (50 - 45) * 2 - (99 + 100) * 10 / 1e4,
              "gross_pnl": (120 - 99) * 1 + (50 - 45) * 2,
              "model_cost": (99 * 1 + 50 * 2) * 10 / 1e4,
              "funding": 0.0,
              "curve": [{"t": g, "equity": 1000.0} for g in grid]}
    return grid, fills, orders, tca, marks, rates, replay


def test_build_report_decomposition_identity_and_unknowns():
    grid, fills, orders, tca, marks, rates, replay = _synthetic_book()
    rep = deg.build_degradation_report(
        strategy_id="s", venue="v", window={"n_days": 3},
        fills=fills, orders=orders, tca_rows=tca, day_grid=grid,
        marks_by_day=marks, rates_by_day=rates, replay=replay, aum=1000.0, cost_bps=10.0)
    # live_gross = (120−100)*1 + (50−45)*2 = 30
    assert rep.live_gross_pnl == pytest.approx(30.0)
    assert rep.live_net_pnl == pytest.approx(30.0)      # 已记录现实净额(费/资金费 UNKNOWN)
    assert rep.replay_net_pnl == pytest.approx(replay["net_pnl"])
    assert rep.live_degradation_ratio == pytest.approx(30.0 / replay["net_pnl"])
    # 分解恒等式:residual ≈ 0(浮点尘)
    assert rep.components.residual is not None
    assert abs(rep.components.residual) < 1e-6
    # trading_delta = 30 − 31 = −1(执行价差);position_timing = −1 + slippage(=+1.0) = 0
    assert rep.components.slippage_cost_actual == pytest.approx(1.0)
    assert rep.components.trading_pnl_delta == pytest.approx(-1.0)
    assert rep.components.position_timing_effect == pytest.approx(0.0)
    # UNKNOWN 纪律:费/资金费实际值恒 None 且进 unknown_fields;无到达价快照同样标注
    unk = set(rep.unknown_fields)
    assert {"fee_actual", "funding_actual", "cost_overrun", "slippage_vs_arrival"} <= unk
    assert rep.components.fee_actual is None and rep.components.funding_actual is None
    assert rep.cost_overrun is None
    # 已记录部分仍然可见:slippage(+1) − fee_expected(10bps×200=0.2) = +0.8
    assert rep.components.fee_expected == pytest.approx(0.2)
    assert rep.cost_overrun_recorded_part == pytest.approx(0.8)
    # 计数与 fill_rate
    assert rep.n_orders == 2 and rep.n_fills == 2 and rep.n_rejects == 0
    assert rep.fill_rate == pytest.approx(1.0)
    assert rep.n_fills_without_tca == 0
    # 逐日瀑布:T0 行 live 尚未入账(盘中成交不前视)-> equity=aum
    assert rep.daily[0]["live_equity"] == pytest.approx(1000.0)
    assert rep.daily[0]["replay_equity"] == pytest.approx(1000.0)
    assert rep.daily[0]["gap"] == pytest.approx(0.0)
    # T0+2d: live equity = 1000 + (110→120 逐日 MTM 到 120/45) = 1000+30 = 1030
    assert rep.daily[2]["live_equity"] == pytest.approx(1030.0)
    assert rep.daily[2]["gap"] == pytest.approx(30.0)


def test_build_report_replay_none_propagates_unknown():
    grid, fills, orders, tca, marks, rates, _ = _synthetic_book()
    rep = deg.build_degradation_report(
        strategy_id="s", venue="v", window={"n_days": 3},
        fills=fills, orders=orders, tca_rows=tca, day_grid=grid,
        marks_by_day=marks, rates_by_day=rates, replay=None, aum=1000.0, cost_bps=10.0)
    assert rep.replay_net_pnl is None
    assert rep.live_degradation_ratio is None
    assert rep.degradation_ratio_reason == "replay_net_unknown"
    assert "replay_net_pnl" in rep.unknown_fields
    assert all(d["replay_equity"] is None and d["gap"] is None for d in rep.daily)


def test_build_report_missing_mark_makes_live_unknown():
    grid, fills, orders, tca, marks, rates, replay = _synthetic_book()
    marks[-1] = {"BTC": 120.0}                       # ETH 缺 mark
    rep = deg.build_degradation_report(
        strategy_id="s", venue="v", window={"n_days": 3},
        fills=fills, orders=orders, tca_rows=tca, day_grid=grid,
        marks_by_day=marks, rates_by_day=rates, replay=replay, aum=1000.0, cost_bps=10.0)
    assert rep.live_net_pnl is None
    assert rep.live_degradation_ratio is None
    assert rep.degradation_ratio_reason == "live_net_unknown"
    assert "marks:ETH" in rep.unknown_fields


def test_build_report_no_tca_and_reject_counting():
    grid = [T0]
    fills = [_fill("BTC", "BUY", 1.0, 100.0, T0, cid="o1", fid="f1")]
    orders = [_order("BTC", "BUY", 1.0, T0, cid="o1"),
              _order("ETH", "BUY", 1.0, T0, cid="o2", status="CANCELED")]
    rep = deg.build_degradation_report(
        strategy_id="s", venue="v", window={"n_days": 1},
        fills=fills, orders=orders, tca_rows=[], day_grid=grid,
        marks_by_day=[{"BTC": 110.0, "ETH": 1.0}], rates_by_day=[{"BTC": 0.0}],
        replay=None, aum=1000.0, cost_bps=10.0)
    assert rep.n_rejects == 1
    assert rep.fill_rate == pytest.approx(0.5)
    assert rep.components.slippage_cost_actual is None       # 无 TCA = 没有记录,不是零
    assert rep.cost_overrun_recorded_part is None
    assert rep.n_fills_without_tca == 1


def test_ratio_floor_relative_to_aum_in_report():
    # |replay_net| = 0.5 < 0.1%×aum(1000)=1.0 -> 比值是噪声放大,守护为 None+带地板的 reason
    grid, fills, orders, tca, marks, rates, replay = _synthetic_book()
    tiny = dict(replay, net_pnl=0.5)
    rep = deg.build_degradation_report(
        strategy_id="s", venue="v", window={"n_days": 3},
        fills=fills, orders=orders, tca_rows=tca, day_grid=grid,
        marks_by_day=marks, rates_by_day=rates, replay=tiny, aum=1000.0, cost_bps=10.0)
    assert rep.live_degradation_ratio is None
    assert rep.degradation_ratio_reason.startswith("replay_net_approx_zero_ratio_meaningless")
    assert "0.1%·aum" in rep.degradation_ratio_reason


def test_significance_label_thresholds():
    assert deg.significance_label(5, 30)["level"] == "SMALL_SAMPLE"
    assert deg.significance_label(50, 3)["level"] == "SMALL_SAMPLE"
    assert deg.significance_label(50, 30)["level"] == "DESCRIPTIVE_ONLY"
