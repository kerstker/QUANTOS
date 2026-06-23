"""Phase 5 — validation gate: walk-forward/purged CV, Deflated Sharpe, PBO, and
the promotion gate (incl. adversarial: overfit/leak/single-regime cannot pass)."""
import pytest

from quantos.contracts.research import ValidationVerdict
from services.research.validation.deflated_sharpe import (
    deflated_sharpe_ratio, expected_max_sharpe, min_track_record_length, pbo_cscv,
)
from services.research.validation.gate import ValidationConfig, ValidationGate
from services.research.validation.walk_forward import purged_kfold, walk_forward_splits


# --- walk-forward / purged CV ------------------------------------------------

def test_walk_forward_is_sequential_and_non_overlapping():
    splits = walk_forward_splits(n=10, train_size=4, test_size=2)
    for train, test in splits:
        assert max(train) < min(test)              # test strictly after train
        assert not (set(train) & set(test))
    assert splits[0] == (list(range(0, 4)), [4, 5])


def test_purged_kfold_embargoes_around_test():
    folds = purged_kfold(n=12, k=3, embargo=1)
    for train, test in folds:
        assert not (set(train) & set(test))
        # no train index within the embargo band adjacent to the test fold
        lo, hi = min(test) - 1, max(test) + 1
        assert lo not in train and hi not in train


# --- Deflated Sharpe / PBO ---------------------------------------------------

def test_deflated_sharpe_decreases_with_more_trials():
    base = dict(observed_sr=0.12, n_obs=252, var_sr=0.01)
    dsr_few = deflated_sharpe_ratio(n_trials=2, **base)
    dsr_many = deflated_sharpe_ratio(n_trials=500, **base)
    assert dsr_few > dsr_many                       # multiple testing deflates
    assert 0.0 <= dsr_many <= dsr_few <= 1.0


def test_expected_max_sharpe_grows_with_trials():
    assert expected_max_sharpe(500, 0.01) > expected_max_sharpe(5, 0.01) > 0


def test_min_track_record_length_infinite_when_no_edge():
    assert min_track_record_length(observed_sr=0.1, target_sr=0.1) == float("inf")
    assert min_track_record_length(observed_sr=0.2, target_sr=0.1) > 0


def test_pbo_high_for_overfit_low_for_genuine():
    # genuine: strategy 0 is consistently best across all blocks
    genuine = [[3.0, 1.0, 0.5] for _ in range(6)]
    assert pbo_cscv(genuine) < 0.5
    # overfit: best-in-sample flips block to block (no persistent skill)
    overfit = [
        [5, 0, 0], [0, 5, 0], [0, 0, 5],
        [5, 0, 0], [0, 5, 0], [0, 0, 5],
    ]
    assert pbo_cscv(overfit) >= 0.5


# --- promotion gate (adversarial) --------------------------------------------

GATE = ValidationGate()
CLEAN = dict(oos_sharpe=1.0, deflated_sharpe=0.95, pbo=0.1, trial_count=5,
             leakage_ok=True, regime_profitable_frac=0.8, walk_forward_passed=True)


def test_clean_strategy_passes():
    res = GATE.evaluate("alpha-good", **CLEAN)
    assert res.verdict == ValidationVerdict.PASS and res.promotable


def test_overfit_strategy_fails():
    res = GATE.evaluate("alpha-overfit", **{**CLEAN, "deflated_sharpe": 0.2, "pbo": 0.8})
    assert res.verdict == ValidationVerdict.FAIL and not res.promotable


def test_leaky_strategy_fails():
    res = GATE.evaluate("alpha-leak", **{**CLEAN, "leakage_ok": False})
    assert res.verdict == ValidationVerdict.FAIL
    assert any("LEAKAGE" in r for r in res.reasons)


def test_single_regime_strategy_does_not_pass():
    res = GATE.evaluate("alpha-regime", **{**CLEAN, "regime_profitable_frac": 0.2})
    assert res.verdict == ValidationVerdict.FAIL and not res.promotable


def test_high_trial_count_forces_human_review():
    res = GATE.evaluate("alpha-many-trials", **{**CLEAN, "trial_count": 200})
    assert res.verdict == ValidationVerdict.REQUIRES_HUMAN_REVIEW and not res.promotable


def test_single_soft_failure_is_warn_and_not_promotable():
    res = GATE.evaluate("alpha-warn", **{**CLEAN, "walk_forward_passed": False})
    assert res.verdict == ValidationVerdict.WARN and not res.promotable


def test_gate_rejects_invalid_inputs():
    res = GATE.evaluate("bad", **{**CLEAN, "deflated_sharpe": 1.5, "pbo": -0.1})
    assert res.verdict == ValidationVerdict.FAIL
    assert any("INVALID_INPUT" in r for r in res.reasons)


def test_marginal_trials_force_human_review():
    res = GATE.evaluate("marg", **{**CLEAN, "trial_count": 25, "deflated_sharpe": 0.65})
    assert res.verdict == ValidationVerdict.REQUIRES_HUMAN_REVIEW and not res.promotable


def test_dsr_requires_positive_var_sr():
    with pytest.raises(ValueError):
        expected_max_sharpe(100, 0.0)


def test_dsr_rejects_extreme_skew_kurt():
    with pytest.raises(ValueError):
        deflated_sharpe_ratio(0.5, 252, 10, var_sr=0.01, kurt=0.5)  # excess kurt < -2


def test_pbo_rejects_ragged_matrix():
    with pytest.raises(ValueError):
        pbo_cscv([[1.0, 2.0, 3.0], [4.0, 5.0]])
