"""Phase 3 — feature layer: PIT calculation, leakage detection (static +
dynamic), registry/versioning, set builder, validation."""
import math

import pytest

from quantos.contracts.data import Bar
from quantos.contracts.feature import (
    FeatureDefinition, FeatureRegistryItem, FeatureStatus, FeatureValue, LeakageCheckResult,
)
from services.features.calculation import momentum, rolling_feature, zscore
from services.features.leakage import detect_leakage, static_lookback_ok
from services.features.registry import FeatureRegistry
from services.features.feature_set import build_feature_set
from services.features.validation import (
    count_missing, count_non_finite, is_time_aligned, validate_feature,
)


def _bars(closes, kt_offset=0):
    return [Bar(instrument_id="X", event_time=i + 1, knowledge_time=i + 1 + kt_offset,
                open=c, high=c + 1, low=c - 1, close=c, volume=100) for i, c in enumerate(closes)]


# --- PIT calculation ---------------------------------------------------------

def test_rolling_feature_uses_only_past_data():
    bars = _bars([10, 12, 14, 16])
    fvs = rolling_feature("mean2", bars, window=2)
    assert fvs[0].value is None                      # warmup
    assert fvs[1].value == 11.0                      # mean(10,12)
    # value at t=2 must not change if a future bar is appended/changed
    bars2 = bars[:2] + [Bar(instrument_id="X", event_time=3, knowledge_time=3,
                            open=999, high=1000, low=998, close=999, volume=1)]
    fvs2 = rolling_feature("mean2", bars2, window=2)
    assert fvs2[1].value == 11.0


def test_feature_knowledge_time_is_max_of_window_inputs():
    # second bar is a restatement knowable later (kt=50) than its event_time (2)
    bars = [
        Bar(instrument_id="X", event_time=1, knowledge_time=1, open=10, high=11, low=9, close=10),
        Bar(instrument_id="X", event_time=2, knowledge_time=50, open=12, high=13, low=11, close=12),
    ]
    fvs = rolling_feature("mean2", bars, window=2)
    assert fvs[1].knowledge_time == 50   # knowable only once the late input is known


def test_calculation_is_reproducible():
    bars = _bars([10, 12, 14, 16])
    assert [f.value for f in rolling_feature("m", bars, 2)] == \
           [f.value for f in rolling_feature("m", bars, 2)]


# --- leakage -----------------------------------------------------------------

def test_static_lookback_guard():
    assert static_lookback_ok(5) and not static_lookback_ok(-1)


def test_good_feature_has_no_leakage():
    bars = _bars([10, 12, 14, 16, 18])
    res = detect_leakage("good", lambda b: rolling_feature("good", b, 2), bars, target_event_time=3)
    assert res.passed is True


def test_leaky_feature_is_detected():
    bars = _bars([10, 12, 14, 16, 18])

    def leaky(rows):
        rows = sorted(rows, key=lambda b: (b.event_time, b.knowledge_time))
        out = []
        for i, b in enumerate(rows):
            peek = rows[i + 1].close if i + 1 < len(rows) else b.close  # reads the FUTURE
            out.append(FeatureValue(feature_id="leaky", instrument_id=b.instrument_id,
                                    event_time=b.event_time, knowledge_time=b.knowledge_time, value=peek))
        return out

    res = detect_leakage("leaky", leaky, bars, target_event_time=3)
    assert res.passed is False
    assert "LEAKAGE" in res.detail


def test_leakage_via_future_volume_is_detected():
    bars = _bars([10, 12, 14, 16, 18])

    def vol_leak(rows):
        rows = sorted(rows, key=lambda b: (b.event_time, b.knowledge_time))
        out = []
        for i, b in enumerate(rows):
            nxt = rows[i + 1].volume if i + 1 < len(rows) else b.volume
            out.append(FeatureValue(feature_id="vl", instrument_id=b.instrument_id,
                                    event_time=b.event_time, knowledge_time=b.knowledge_time, value=float(nxt)))
        return out

    assert detect_leakage("vl", vol_leak, bars, target_event_time=3).passed is False


def test_leakage_via_future_knowledge_time_is_detected():
    bars = _bars([10, 12, 14, 16, 18])

    def kt_leak(rows):
        rows = sorted(rows, key=lambda b: (b.event_time, b.knowledge_time))
        out = []
        for i, b in enumerate(rows):
            nxt = rows[i + 1].knowledge_time if i + 1 < len(rows) else b.knowledge_time
            out.append(FeatureValue(feature_id="kl", instrument_id=b.instrument_id,
                                    event_time=b.event_time, knowledge_time=b.knowledge_time, value=float(nxt)))
        return out

    assert detect_leakage("kl", kt_leak, bars, target_event_time=3).passed is False


def test_multi_instrument_leakage_in_non_first_instrument_is_detected():
    bars = []
    for inst, closes in (("A", [10, 11, 12]), ("B", [20, 21, 22])):
        for i, c in enumerate(closes):
            bars.append(Bar(instrument_id=inst, event_time=i + 1, knowledge_time=i + 1,
                            open=c, high=c + 1, low=c - 1, close=c, volume=100))

    def b_only_leak(rows):
        out, by = [], {}
        for b in rows:
            by.setdefault(b.instrument_id, []).append(b)
        for inst, bs in by.items():
            bs = sorted(bs, key=lambda b: b.event_time)
            for i, b in enumerate(bs):
                val = bs[i + 1].close if (inst == "B" and i + 1 < len(bs)) else b.close
                out.append(FeatureValue(feature_id="ml", instrument_id=inst,
                                        event_time=b.event_time, knowledge_time=b.knowledge_time, value=val))
        return out

    assert detect_leakage("ml", b_only_leak, bars, target_event_time=1).passed is False


def test_detect_leakage_requires_pit_deduplicated_input():
    dup = [
        Bar(instrument_id="X", event_time=1, knowledge_time=1, open=10, high=11, low=9, close=10),
        Bar(instrument_id="X", event_time=1, knowledge_time=50, open=11, high=12, low=10, close=11),
    ]
    with pytest.raises(ValueError):
        detect_leakage("x", lambda b: [], dup, target_event_time=1)


# --- registry / versioning ---------------------------------------------------

def test_registry_versioning_and_status():
    reg = FeatureRegistry()
    d = FeatureDefinition(feature_id="mom_20", name="20d momentum")
    reg.register(FeatureRegistryItem(definition=d, version="v1"))
    reg.register(FeatureRegistryItem(definition=d, version="v2"))
    assert reg.versions("mom_20") == ["v1", "v2"]
    assert reg.get("mom_20", "v1").status == FeatureStatus.DRAFT
    updated = reg.set_status("mom_20", "v1", FeatureStatus.VALIDATED)
    assert updated.status == FeatureStatus.VALIDATED
    with pytest.raises(ValueError):
        reg.register(FeatureRegistryItem(definition=d, version="v1"))  # duplicate


def test_feature_set_requires_known_features():
    reg = FeatureRegistry()
    reg.register(FeatureRegistryItem(definition=FeatureDefinition(feature_id="a", name="A"), version="v1"))
    fs = build_feature_set("set1", ["a"], reg)
    assert fs.feature_ids == ("a",)
    with pytest.raises(ValueError):
        build_feature_set("set2", ["a", "missing"], reg)


# --- validation --------------------------------------------------------------

def test_validation_missing_and_infinite():
    vals = [
        FeatureValue(feature_id="f", instrument_id="X", event_time=1, knowledge_time=1, value=None),  # warmup
        FeatureValue(feature_id="f", instrument_id="X", event_time=2, knowledge_time=2, value=1.0),
        FeatureValue(feature_id="f", instrument_id="X", event_time=3, knowledge_time=3, value=None),  # missing
        FeatureValue(feature_id="f", instrument_id="X", event_time=4, knowledge_time=4, value=float("inf")),
    ]
    assert count_missing(vals) == 1          # warmup excluded, middle None counted
    assert count_non_finite(vals) == 1       # inf
    assert validate_feature("f", vals).passed is False


def test_validation_detects_misalignment():
    vals = [
        FeatureValue(feature_id="f", instrument_id="X", event_time=2, knowledge_time=2, value=1.0),
        FeatureValue(feature_id="f", instrument_id="X", event_time=1, knowledge_time=1, value=2.0),  # goes backwards
    ]
    assert is_time_aligned(vals) is False


def test_validation_passes_clean_feature():
    bars = _bars([10, 12, 14, 16])
    fvs = [f for f in rolling_feature("m", bars, 2) if f.value is not None]
    leak = LeakageCheckResult(feature_id="m", passed=True)
    assert validate_feature("m", fvs, leak).passed is True


def test_momentum_and_zscore_helpers():
    assert momentum([10, 11]) == pytest.approx(0.1)
    assert zscore([1, 2, 3, 4, 100]) > 0
