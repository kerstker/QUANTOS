"""Phase 2 — data quality, normalization/alignment, PIT corporate actions,
ingestion interface."""
import pytest
from pydantic import ValidationError

from quantos.contracts.data import Bar, CorporateAction, CorporateActionType
from services.data import quality
from services.data.normalization import (
    FutureDataError, align_to_calendar, normalize_bar, normalize_timestamp, reject_if_future,
)
from services.data.corporate_actions import adjusted_close, cash_dividend_total, split_factor
from services.data.ingestion import InMemoryMarketDataSource, MarketDataSource


def _bar(et, kt, close=10.0):
    return Bar(instrument_id="X", event_time=et, knowledge_time=kt,
               open=close, high=close, low=close, close=close)


# --- quality -----------------------------------------------------------------

def test_quality_detects_defects():
    assert quality.count_duplicate_event_times([1, 1, 2]) == 1
    assert quality.count_out_of_order([1, 2, 1, 3]) == 1
    assert quality.missing_sessions(["d1", "d2", "d3"], ["d1", "d3"]) == ["d2"]
    assert quality.count_future([1, 5, 9], as_of=4) == 2


def test_quality_report_aggregates_and_fails_on_defect():
    clean = quality.build_report("ds", [1, 2, 3], as_of=10,
                                 expected_days=["d1"], present_days=["d1"])
    assert clean.passed is True
    dirty = quality.build_report("ds", [2, 1, 1], as_of=0)  # out-of-order + dup + future(1,1>0... )
    assert dirty.passed is False
    assert dirty.duplicate_count == 1 and dirty.out_of_order_count == 1


# --- normalization / alignment ----------------------------------------------

def test_timezone_alignment_to_utc():
    # 09:30 in +08:00 and the same instant expressed in UTC must align
    local_sh = 1_000_000  # epoch seconds in a +8 frame
    assert normalize_timestamp(local_sh, tz_offset_seconds=8 * 3600) == \
           normalize_timestamp(local_sh - 8 * 3600, tz_offset_seconds=0)


def test_normalize_bar_rejects_bad_row():
    with pytest.raises(ValidationError):
        normalize_bar({"instrument_id": "X", "event_time": 1, "open": 10,
                       "high": 8, "low": 9, "close": 10}, knowledge_time=1)


def test_normalize_bar_rejects_raw_knowledge_time():
    with pytest.raises(ValueError):
        normalize_bar({"instrument_id": "X", "event_time": 1, "knowledge_time": 1,
                       "open": 10, "high": 10, "low": 10, "close": 10}, knowledge_time=2)


def test_reject_future_bar():
    with pytest.raises(FutureDataError):
        reject_if_future(_bar(200, 200), now=100)
    assert reject_if_future(_bar(50, 60), now=100).event_time == 50


def test_align_to_calendar():
    missing, extra = align_to_calendar(["d1", "d3"], ["d1", "d2", "d3"])
    assert missing == ["d2"] and extra == []


# --- PIT-correct corporate actions (adversarial no-leak) ---------------------

def test_split_adjustment_is_point_in_time():
    bar = _bar(100, 100, close=100.0)
    split = CorporateAction(instrument_id="X", event_time=200, knowledge_time=190,
                            action_type=CorporateActionType.SPLIT, ex_date="2026-02-01", ratio=2.0)
    # Before the split is knowable (as_of < 190): NO adjustment (no lookahead).
    assert adjusted_close(bar, [split], as_of=150) == 100.0
    assert split_factor([split], bar.event_time, as_of=150) == 1.0
    # Once knowable (as_of >= 190): adjusted.
    assert adjusted_close(bar, [split], as_of=300) == 50.0


def test_cash_dividend_total_is_point_in_time():
    div = CorporateAction(instrument_id="X", event_time=200, knowledge_time=190,
                          action_type=CorporateActionType.DIVIDEND, ex_date="2026-03-01", amount=0.5)
    # within window AND knowable -> accrued
    assert cash_dividend_total([div], start_exclusive=100, end_inclusive=300, as_of=300) == 0.5
    # not knowable yet (as_of before announcement) -> not accrued (PIT)
    assert cash_dividend_total([div], start_exclusive=100, end_inclusive=300, as_of=150) == 0.0
    # outside ex window -> not accrued
    assert cash_dividend_total([div], start_exclusive=250, end_inclusive=300, as_of=300) == 0.0


def test_split_only_affects_prior_bars():
    later_bar = _bar(250, 250, close=100.0)  # after the split's ex/event_time
    split = CorporateAction(instrument_id="X", event_time=200, knowledge_time=190,
                            action_type=CorporateActionType.SPLIT, ex_date="2026-02-01", ratio=2.0)
    assert adjusted_close(later_bar, [split], as_of=300) == 100.0  # not adjusted


# --- ingestion interface -----------------------------------------------------

def test_in_memory_source_is_market_data_source_and_filters_window():
    src = InMemoryMarketDataSource()
    src.load("X", [_bar(100, 100), _bar(200, 200), _bar(300, 300)])
    assert isinstance(src, MarketDataSource)
    rows = src.fetch_bars("X", start=150, end=250)
    assert [b.event_time for b in rows] == [200]
