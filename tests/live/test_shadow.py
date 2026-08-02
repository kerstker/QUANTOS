"""live/execution/shadow.py(SHADOW 比较纯核,727 §12.2,Phase 7 S1)单测。

全部手工构造 OrderBookSnapshot——零网络/零 IO/零墙钟(确定性核心,时间全部 int ms 注入)。
"""
import pytest

from live.contracts import BookLevel, OrderBookSnapshot
from live.execution.shadow import (PROVENANCE_SHADOW, ShadowLeg, ShadowObservation,
                                   WalkBookResult, observe_batch, observe_leg,
                                   walk_the_book)

_NOW = 1_700_000_000_000


def _book(bids=((100.0, 1.0), (99.5, 2.0)), asks=((100.5, 1.0), (101.0, 2.0)),
          as_of_ms=_NOW - 500, source="test"):
    return OrderBookSnapshot(instrument_id="BTC",
                             bids=tuple(BookLevel(p, s) for p, s in bids),
                             asks=tuple(BookLevel(p, s) for p, s in asks),
                             as_of_ms=as_of_ms, source=source)


def _leg(side="BUY", quantity=2.0, decision_price=100.0):
    return ShadowLeg(instrument_id="BTC", side=side, quantity=quantity,
                     decision_price=decision_price)


# --- walk_the_book ---

def test_walk_buy_full_fill_vwap_hand_computed():
    result = walk_the_book(_book(), side="BUY", quantity=2.0)
    # 1 @ 100.5 + 1 @ 101.0 -> vwap = (100.5 + 101.0) / 2
    assert result.executable is True
    assert result.vwap == pytest.approx(100.75)
    assert result.filled_quantity == pytest.approx(2.0)
    assert result.unfilled_quantity == 0.0
    assert result.levels_consumed == 2
    assert result.reason == ""


def test_walk_sell_consumes_bids():
    result = walk_the_book(_book(), side="SELL", quantity=2.0)
    # 1 @ 100.0 + 1 @ 99.5 -> vwap = (100.0 + 99.5) / 2
    assert result.executable is True
    assert result.vwap == pytest.approx(99.75)


def test_walk_exact_depth_match_fills_fully():
    result = walk_the_book(_book(), side="BUY", quantity=3.0)   # 卖侧总深 = 1 + 2
    assert result.executable is True
    assert result.levels_consumed == 2
    assert result.vwap == pytest.approx((100.5 * 1 + 101.0 * 2) / 3)


def test_walk_insufficient_depth_reports_unfilled_without_fabricated_price():
    result = walk_the_book(_book(), side="BUY", quantity=5.0)   # 卖侧总深仅 3
    assert result.executable is False
    assert result.reason == "INSUFFICIENT_DEPTH"
    assert result.filled_quantity == pytest.approx(3.0)
    assert result.unfilled_quantity == pytest.approx(2.0)
    assert result.vwap is None                       # 绝不为未成交部分编造价格
    assert result.levels_consumed == 2


def test_walk_empty_relevant_side_is_empty_book():
    result = walk_the_book(_book(asks=()), side="BUY", quantity=1.0)
    assert result.executable is False
    assert result.reason == "EMPTY_BOOK"
    assert result.filled_quantity == 0.0
    assert result.vwap is None


def test_walk_crossed_book_not_executable():
    crossed = _book(bids=((100.0, 1.0),), asks=((99.0, 1.0),))
    result = walk_the_book(crossed, side="BUY", quantity=0.5)
    assert result.executable is False
    assert result.reason == "CROSSED_BOOK"
    assert result.filled_quantity == 0.0


def test_walk_consumes_venue_given_order_without_resorting():
    # venue 原序(刻意乱序)——核心不重排,重排会掩盖 venue 报序异常
    unsorted = _book(asks=((101.0, 2.0), (100.5, 1.0)))
    result = walk_the_book(unsorted, side="BUY", quantity=1.0)
    assert result.vwap == pytest.approx(101.0)


def test_walk_rejects_non_positive_or_non_finite_quantity():
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            walk_the_book(_book(), side="BUY", quantity=bad)


def test_walk_rejects_unknown_side():
    with pytest.raises(ValueError):
        walk_the_book(_book(), side="HOLD", quantity=1.0)


# --- OrderBookSnapshot / BookLevel 构造期校验 ---

def test_book_level_rejects_nan_negative_and_zero():
    with pytest.raises(ValueError):
        BookLevel(price=float("nan"), size=1.0)
    with pytest.raises(ValueError):
        BookLevel(price=100.0, size=-1.0)
    with pytest.raises(ValueError):
        BookLevel(price=100.0, size=0.0)
    with pytest.raises(ValueError):
        BookLevel(price=0.0, size=1.0)


def test_snapshot_rejects_bad_source_bad_ts_and_non_levels():
    with pytest.raises(ValueError):
        OrderBookSnapshot(instrument_id="BTC", source="")
    with pytest.raises(ValueError):
        OrderBookSnapshot(instrument_id="BTC", source="test", as_of_ms=True)
    with pytest.raises(ValueError):
        OrderBookSnapshot(instrument_id="BTC", source="test", as_of_ms=-1)
    with pytest.raises(ValueError):
        OrderBookSnapshot(instrument_id="BTC", source="test",
                          bids=(BookLevel(1.0, 1.0), "not-a-level"))


def test_shadow_leg_rejects_bad_side_and_quantity():
    with pytest.raises(ValueError):
        ShadowLeg(instrument_id="BTC", side="HOLD", quantity=1.0, decision_price=100.0)
    with pytest.raises(ValueError):
        ShadowLeg(instrument_id="BTC", side="BUY", quantity=0.0, decision_price=100.0)
    with pytest.raises(ValueError):
        ShadowLeg(instrument_id="BTC", side="BUY", quantity=1.0, decision_price=-5.0)


# --- observe_leg ---

def test_observe_leg_book_none_is_book_unavailable():
    obs = observe_leg(_leg(), book=None, latency_ms=120, now_ms=_NOW)
    assert obs.walk.reason == "BOOK_UNAVAILABLE"
    assert obs.executable_vwap is None
    assert obs.slippage_bps_vs_decision is None
    assert obs.book_mid is None and obs.book_spread_bps is None
    assert obs.book_age_ms is None and obs.as_of_ms is None
    assert obs.expected_impact_bps is None
    assert obs.latency_ms == 120


def test_observe_leg_stale_book_closes_executable_fields_but_reports_stale_top():
    obs = observe_leg(_leg(), book=_book(as_of_ms=_NOW - 60_001), latency_ms=10,
                      now_ms=_NOW, max_book_age_ms=60_000)
    assert obs.walk.reason == "BOOK_STALE"
    assert obs.executable_vwap is None
    assert obs.slippage_bps_vs_decision is None
    assert obs.book_age_ms == 60_001
    # 旧簿的 mid/spread 仍如实报告(描述那份旧簿),只有「当此刻可成交价用」的字段被关死
    assert obs.book_mid == pytest.approx(100.25)
    assert obs.book_spread_bps == pytest.approx(0.5 / 100.25 * 1e4)


def test_observe_leg_future_book_is_fail_closed():
    obs = observe_leg(_leg(), book=_book(as_of_ms=_NOW + 1), latency_ms=10, now_ms=_NOW)
    assert obs.walk.reason == "BOOK_FROM_FUTURE"
    assert obs.executable_vwap is None
    assert obs.book_age_ms == -1


def test_observe_leg_missing_as_of_skips_stale_check_and_reports_age_none():
    obs = observe_leg(_leg(), book=_book(as_of_ms=None), latency_ms=10, now_ms=_NOW)
    assert obs.book_age_ms is None
    assert obs.as_of_ms is None
    assert obs.walk.executable is True               # 无来源时间不可判龄,不作废簿处理


def test_observe_leg_buy_spread_mid_slippage_math():
    obs = observe_leg(_leg(side="BUY", quantity=2.0, decision_price=100.0),
                      book=_book(), latency_ms=42, now_ms=_NOW)
    assert obs.book_mid == pytest.approx(100.25)
    assert obs.book_spread_bps == pytest.approx(0.5 / 100.25 * 1e4)
    assert obs.executable_vwap == pytest.approx(100.75)
    # BUY:(100.75 - 100) / 100 * 1e4 = 75 bps(买贵 = 正 = 成本)
    assert obs.slippage_bps_vs_decision == pytest.approx(75.0)
    assert obs.paper_fill_price == pytest.approx(100.0)   # 当前 paper 语义,如实记录


def test_observe_leg_sell_slippage_signed_as_cost():
    obs = observe_leg(_leg(side="SELL", quantity=2.0, decision_price=100.0),
                      book=_book(), latency_ms=42, now_ms=_NOW)
    assert obs.executable_vwap == pytest.approx(99.75)
    # SELL:决策价 100、实际 99.75 卖出 = 卖贱 25 bps = 正 = 成本
    assert obs.slippage_bps_vs_decision == pytest.approx(25.0)


def test_observe_leg_price_improvement_is_negative_slippage():
    rich = _book(bids=((98.0, 5.0),), asks=((99.0, 5.0),))
    obs = observe_leg(_leg(side="BUY", quantity=1.0, decision_price=100.0),
                      book=rich, latency_ms=1, now_ms=_NOW)
    assert obs.slippage_bps_vs_decision == pytest.approx(-100.0)   # 改善 = 负


def test_observe_leg_unexecutable_walk_leaves_vwap_and_slippage_none():
    obs = observe_leg(_leg(quantity=5.0), book=_book(), latency_ms=1, now_ms=_NOW)
    assert obs.walk.reason == "INSUFFICIENT_DEPTH"
    assert obs.executable_vwap is None
    assert obs.slippage_bps_vs_decision is None


def test_observe_leg_impact_requires_both_model_and_adv():
    class _Model:
        def temporary_bps(self, notional, adv):
            return 10.0 * (notional / adv) ** 0.5

        def permanent_bps(self, notional, adv):
            return 5.0 * (notional / adv)

    model = _Model()
    # 缺任一 -> None(UNKNOWN 纪律,UNCALIBRATED 输入绝不编造)
    assert observe_leg(_leg(), book=_book(), latency_ms=1, now_ms=_NOW,
                       impact_model=model, adv=None).expected_impact_bps is None
    assert observe_leg(_leg(), book=_book(), latency_ms=1, now_ms=_NOW,
                       impact_model=None, adv=1e9).expected_impact_bps is None
    assert observe_leg(_leg(), book=_book(), latency_ms=1, now_ms=_NOW,
                       impact_model=model, adv=0.0).expected_impact_bps is None
    # 双给:notional = 2.0 * 100.0 = 200;adv = 200 -> temp 10*1 + perm 5*1
    obs = observe_leg(_leg(quantity=2.0, decision_price=100.0), book=_book(),
                      latency_ms=1, now_ms=_NOW, impact_model=model, adv=200.0)
    assert obs.expected_impact_bps == pytest.approx(15.0)


def test_observe_leg_rejects_bad_timestamps():
    with pytest.raises(ValueError):
        observe_leg(_leg(), book=_book(), latency_ms=-1, now_ms=_NOW)
    with pytest.raises(ValueError):
        observe_leg(_leg(), book=_book(), latency_ms=1, now_ms=_NOW,
                    max_book_age_ms=True)


# --- provenance(727 §18.2) ---

def test_every_observation_carries_shadow_provenance():
    for book in (None, _book(), _book(as_of_ms=_NOW - 10_000_000)):
        obs = observe_leg(_leg(), book=book, latency_ms=1, now_ms=_NOW)
        assert obs.provenance == PROVENANCE_SHADOW == "SHADOW"


def test_observation_rejects_non_shadow_provenance():
    with pytest.raises(ValueError):
        ShadowObservation(
            instrument_id="BTC", side="BUY", quantity=1.0, decision_price=100.0,
            paper_fill_price=100.0, book_mid=None, book_spread_bps=None,
            executable_vwap=None, slippage_bps_vs_decision=None,
            walk=WalkBookResult(instrument_id="BTC", executable=False, vwap=None,
                                filled_quantity=0.0, unfilled_quantity=1.0,
                                levels_consumed=0, reason="BOOK_UNAVAILABLE"),
            expected_impact_bps=None, latency_ms=1, book_age_ms=None, as_of_ms=None,
            provenance="PAPER")


# --- observe_batch ---

def test_observe_batch_preserves_input_order_and_per_leg_fail_closed():
    legs = [ShadowLeg(instrument_id="BTC", side="BUY", quantity=1.0, decision_price=100.0),
            ShadowLeg(instrument_id="ETH", side="SELL", quantity=10.0, decision_price=3000.0)]
    books = {"BTC": OrderBookSnapshot(instrument_id="BTC",
                                      bids=(BookLevel(99.0, 5.0),),
                                      asks=(BookLevel(101.0, 5.0),),
                                      as_of_ms=_NOW - 100, source="test")}
    obs = observe_batch(legs, books, latencies_ms={"BTC": 11, "ETH": 22}, now_ms=_NOW)
    assert [o.instrument_id for o in obs] == ["BTC", "ETH"]   # 确定性顺序
    assert obs[0].walk.executable is True
    assert obs[0].latency_ms == 11
    assert obs[1].walk.reason == "BOOK_UNAVAILABLE"           # ETH 缺簿不拖垮整批
    assert obs[1].latency_ms == 22


def test_observe_batch_adv_mapping_is_per_instrument():
    class _Model:
        def temporary_bps(self, notional, adv):
            return notional / adv

        def permanent_bps(self, notional, adv):
            return 0.0

    legs = [ShadowLeg(instrument_id="BTC", side="BUY", quantity=1.0, decision_price=100.0),
            ShadowLeg(instrument_id="ETH", side="BUY", quantity=1.0, decision_price=50.0)]
    books = {"BTC": _book(), "ETH": _book()}
    obs = observe_batch(legs, books, latencies_ms={"BTC": 1, "ETH": 1}, now_ms=_NOW,
                        impact_model=_Model(), adv={"BTC": 1000.0})   # ETH 无 adv
    assert obs[0].expected_impact_bps == pytest.approx(0.1)           # 100/1000
    assert obs[1].expected_impact_bps is None                         # 缺 adv -> None


def test_observe_batch_missing_latency_is_fail_closed():
    legs = [_leg()]
    with pytest.raises(ValueError, match="latencies_ms"):
        observe_batch(legs, {}, latencies_ms={}, now_ms=_NOW)
