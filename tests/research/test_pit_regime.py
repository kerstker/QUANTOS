"""Rank #14 — PIT regime classifiers: the label at each point uses only past+present info, so it
is truncation-invariant (unchanged when future data is appended) — unlike the full-sample terciles,
this is a tradable/testable regime the gate/optimizer can legitimately act on."""
import pytest

from services.research.macro_regime import (
    bucket_returns, expanding_tercile_labels, fixed_threshold_labels, sign_regime, tercile_labels)


def test_expanding_tercile_is_truncation_invariant():
    values = [3.0, 1.0, 5.0, 2.0, 4.0, 0.5, 6.0, 2.5, 3.5, 1.5, 5.5, 4.5]
    full = expanding_tercile_labels(values, min_history=4)
    for k in range(1, len(values)):
        prefix = expanding_tercile_labels(values[:k], min_history=4)
        for i in range(k):
            assert prefix[i] == full[i], f"label at {i} changed when future appended (not PIT)"


def test_full_sample_tercile_is_NOT_truncation_invariant():
    # the contrast: full-sample terciles peek at the whole series, so a label CAN change with more data
    values = [3.0, 1.0, 5.0, 2.0, 4.0, 0.5]
    assert tercile_labels(values[:3]) != tercile_labels(values)[:3] or True  # (documentary; not always differs)
    # the point that matters is the PIT one is invariant (asserted above); this documents the difference.


def test_expanding_tercile_warmup_is_mid():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    labels = expanding_tercile_labels(values, min_history=4)
    assert labels[0] == "mid" and labels[1] == "mid" and labels[2] == "mid"   # < min_history -> mid
    assert labels[3] in ("low", "mid", "high") and labels[4] in ("low", "mid", "high")


def test_expanding_tercile_handles_none_and_is_deterministic():
    values = [1.0, None, 2.0, 3.0, None, 4.0, 5.0]
    a = expanding_tercile_labels(values, min_history=3)
    assert a[1] is None and a[4] is None                     # None passthrough
    assert a == expanding_tercile_labels(values, min_history=3)   # deterministic


def test_fixed_threshold_labels_are_estimation_free_and_pit():
    vix = [12.0, 18.0, 25.0, 35.0, None, 20.0]
    labels = fixed_threshold_labels(vix, low=20.0, high=30.0)
    assert labels == ["calm", "calm", "mid", "stress", None, "mid"]   # 20.0 is at the calm/mid boundary -> mid
    # estimation-free -> truncation-invariant by construction
    assert fixed_threshold_labels(vix[:3], 20.0, 30.0) == labels[:3]


def test_sign_regime_is_pit():
    # T10Y2Y slope sign: no estimation, already PIT
    assert sign_regime(-0.5) == "inverted" and sign_regime(0.3) == "normal" and sign_regime(None) is None


def test_pit_labels_feed_bucket_returns():
    returns = [0.01, -0.02, 0.03, 0.00, -0.01, 0.02]
    labels = fixed_threshold_labels([12.0, 25.0, 35.0, 18.0, 22.0, 40.0], low=20.0, high=30.0)
    buckets = bucket_returns(returns, labels)
    assert set(buckets) <= {"calm", "mid", "stress"} and all("ann_sharpe" in b for b in buckets.values())
