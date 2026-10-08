"""Phase 1 — Data (§6.1) and Feature (§6.2) contract tests, incl. the CT-1
no-lookahead invariant."""
import pytest
from pydantic import ValidationError

from quantos.contracts.temporal import BitemporalRecord
from quantos.contracts.data import (
    AssetClass, Bar, CorporateAction, CorporateActionType, DataQualityCheck,
    DataQualityReport, DatasetVersion, Instrument, Tick, TradingCalendar,
)
from quantos.contracts.feature import (
    FeatureDefinition, FeatureValue, FeatureValidationResult, LeakageCheckResult,
    FeatureRegistryItem,
)


# --- CT-1: no lookahead ------------------------------------------------------

def test_bitemporal_rejects_lookahead():
    with pytest.raises(ValidationError):
        BitemporalRecord(event_time=100, knowledge_time=50)
    assert BitemporalRecord(event_time=100, knowledge_time=100).knowledge_time == 100


def test_bar_and_tick_inherit_no_lookahead():
    with pytest.raises(ValidationError):
        Bar(instrument_id="X", event_time=100, knowledge_time=99,
            open=1, high=1, low=1, close=1)
    with pytest.raises(ValidationError):
        Tick(instrument_id="X", event_time=100, knowledge_time=0, price=1.0)


def test_feature_value_no_lookahead():
    with pytest.raises(ValidationError):
        FeatureValue(feature_id="f", instrument_id="X",
                     event_time=10, knowledge_time=5, value=1.0)


# --- Bar OHLC validation -----------------------------------------------------

def test_bar_rejects_inconsistent_ohlc():
    with pytest.raises(ValidationError):
        Bar(instrument_id="X", event_time=1, knowledge_time=1,
            open=10, high=8, low=9, close=10)  # high < low/open


def test_bar_rejects_non_positive_price():
    with pytest.raises(ValidationError):
        Bar(instrument_id="X", event_time=1, knowledge_time=1,
            open=0, high=1, low=1, close=1)


def test_valid_bar_roundtrip():
    b = Bar(instrument_id="X", event_time=1, knowledge_time=2,
            open=10, high=12, low=9, close=11, volume=1000)
    assert Bar.model_validate(b.model_dump()) == b


# --- Instrument / calendar / dataset / quality -------------------------------

def test_instrument_multiplier_positive():
    with pytest.raises(ValidationError):
        Instrument(instrument_id="i", symbol="S", asset_class=AssetClass.FUTURE, multiplier=0)


def test_trading_calendar_membership():
    cal = TradingCalendar(calendar_id="SSE", trading_days=("2026-06-22", "2026-06-23"))
    assert cal.is_trading_day("2026-06-23")
    assert not cal.is_trading_day("2026-06-24")


def test_dataset_version_row_count_nonneg():
    with pytest.raises(ValidationError):
        DatasetVersion(dataset_id="d", version="v1", created_at=0, row_count=-1)


def test_data_quality_report_passed_aggregates_checks():
    r = DataQualityReport(dataset_id="d", checks=(
        DataQualityCheck(name="missing", passed=True),
        DataQualityCheck(name="dupes", passed=False),
    ))
    assert r.passed is False


def test_corporate_action_ratio_positive():
    ca = CorporateAction(instrument_id="X", event_time=1, knowledge_time=1,
                         action_type=CorporateActionType.SPLIT, ex_date="2026-06-22", ratio=2.0)
    assert ca.ratio == 2.0


# --- Feature -----------------------------------------------------------------

def test_feature_registry_item_roundtrip():
    item = FeatureRegistryItem(
        definition=FeatureDefinition(feature_id="mom_20", name="20d momentum"),
        owner="research",
    )
    assert FeatureRegistryItem.model_validate(item.model_dump()) == item


def test_feature_validation_result_carries_leakage():
    res = FeatureValidationResult(
        feature_id="mom_20", passed=False,
        leakage=LeakageCheckResult(feature_id="mom_20", passed=False, detail="future window"),
    )
    assert res.leakage.passed is False
