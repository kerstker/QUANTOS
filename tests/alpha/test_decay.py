"""Phase 6 step 9 — decay tracking: AR(1)/horizon half-life and DEBOUNCED
retirement triggers (sustained decay fires; single-window noise does not)."""
import math

import pytest

from services.alpha.config import AlphaFactoryConfig
from services.alpha.decay import (
    ar1_half_life, detect_decay, half_life_from_phi, horizon_half_life,
)

CFG = AlphaFactoryConfig()  # confirm_windows=3, min_ic_retention=0.3


def test_half_life_from_phi_recovers_known_ar1():
    assert half_life_from_phi(0.5) == pytest.approx(1.0)
    assert half_life_from_phi(1.0) == math.inf      # non-stationary / unstable
    assert half_life_from_phi(0.0) == math.inf      # no persistence
    assert half_life_from_phi(0.25) == pytest.approx(0.5)


def test_ar1_half_life_distinguishes_persistence():
    # anti-persistent (alternating) -> negative autocorr -> no AR(1) decay structure
    antipersist = [0.10, -0.08, 0.09, -0.07, 0.08, -0.06, 0.07, -0.05]
    assert ar1_half_life(antipersist) == math.inf
    # positively autocorrelated (smooth drift, phi~0.98) -> finite half-life
    persistent = [0.10, 0.105, 0.11, 0.112, 0.118, 0.12, 0.125, 0.13]
    assert math.isfinite(ar1_half_life(persistent))


def test_horizon_half_life_recovers_exp_decay():
    ic_by_h = {1: 0.10, 2: 0.05, 3: 0.025, 4: 0.0125}   # halves each horizon step
    assert horizon_half_life(ic_by_h) == pytest.approx(1.0, rel=1e-6)


def test_sustained_decay_triggers():
    # healthy baseline then 3 sustained-low windows
    rolling = [0.10, 0.11, 0.09, 0.10, 0.02, 0.01, 0.02]
    r = detect_decay("a1", rolling, config=CFG, baseline=0.10)
    assert r.decayed is True
    assert "IC_RETENTION_BELOW_MIN" in r.reasons


def test_single_window_noise_does_not_trigger():
    # one dip in the last window only -> not all of last 3 below -> no retire
    rolling = [0.10, 0.11, 0.09, 0.10, 0.11, 0.10, 0.01]
    r = detect_decay("a1", rolling, config=CFG, baseline=0.10)
    assert r.decayed is False


def test_sustained_average_bleed_triggers_even_if_not_all_below():
    # recent avg 0.0233 < 0.03 floor though 0.04 is above it — the old all()-below
    # logic missed this sawtooth bleed; the mean-based debounce catches it (review P1)
    rolling = [0.10, 0.11, 0.09, 0.10, 0.04, 0.01, 0.02]
    r = detect_decay("a1", rolling, config=CFG, baseline=0.10)
    assert r.decayed is True and "IC_RETENTION_BELOW_MIN" in r.reasons


def test_anti_persistent_breakdown_triggers_decay():
    # oscillatory breakdown: retention avg is above floor, but phi << 0 = fast decay
    rolling = [0.10, -0.09, 0.10, -0.08, 0.09, -0.08, 0.09]
    r = detect_decay("a1", rolling, config=CFG, baseline=0.10)
    assert r.decayed is True and "ANTI_PERSISTENT_BREAKDOWN" in r.reasons


def test_performance_gap_triggers_decay():
    rolling = [0.10, 0.11, 0.09, 0.10, 0.10, 0.11, 0.10]  # IC fine
    r = detect_decay("a1", rolling, config=CFG, baseline=0.10, perf_gap=0.8)  # paper << backtest
    assert r.decayed is True and "PERF_GAP_EXCEEDS_MAX" in r.reasons


def test_insufficient_history_is_not_decayed():
    r = detect_decay("a1", [0.1, 0.1], config=CFG)
    assert r.decayed is False and "INSUFFICIENT_HISTORY" in r.reasons
