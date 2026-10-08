"""Phase 5 — validation gate: walk-forward/purged CV, Deflated Sharpe, PBO, and
the promotion gate (incl. adversarial: overfit/leak/single-regime cannot pass)."""
import pytest

from quantos.contracts.hashing import stable_hash
from quantos.contracts.research import ValidationVerdict
from services.research.validation.deflated_sharpe import (
    deflated_sharpe_ratio, expected_max_sharpe, min_track_record_length, pbo_cscv,
)
from services.research.validation.gate import ValidationConfig, ValidationGate
from services.research.validation.walk_forward import purged_kfold, walk_forward_splits
from services.validation.catalog_evidence import create_catalog_evidence
from services.validation.pbo_evidence import create_batch_pbo_evidence


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


def _gate_inputs(
    subject_id: str,
    *,
    trial_count: int = 5,
    low_dsr: bool = False,
    high_pbo: bool = False,
    **overrides,
):
    subject_hash = stable_hash({"subject": subject_id})
    trial_hashes = [subject_hash]
    trial_hashes.extend(
        stable_hash({"subject": subject_id, "trial": index})
        for index in range(1, trial_count)
    )
    alternating = tuple(-1.0 if index % 2 else 1.0 for index in range(400))

    def series(mean: float) -> tuple[float, ...]:
        return tuple(mean + 0.01 * sign for sign in alternating)

    catalog_evidence = create_catalog_evidence(
        subject_id=subject_id,
        subject_definition_hash=subject_hash,
        trial_definition_hashes=trial_hashes,
        return_series_by_definition={
            subject_hash: series(0.0002 if low_dsr else 0.005),
            trial_hashes[1]: series(0.0045),
            trial_hashes[2]: series(0.0055),
        },
    )
    pbo_values = (
        ((5.0, 0.0, 5.0, 0.0), (0.0, 5.0, 0.0, 5.0))
        if high_pbo
        else ((3.0, 3.0, 3.0, 3.0), (1.0, 1.0, 1.0, 1.0))
    )
    pbo_evidence = create_batch_pbo_evidence(
        return_series_by_definition={
            definition_hash: tuple((event_time, float(value)) for event_time, value in enumerate(values))
            for definition_hash, values in zip((subject_hash, trial_hashes[1]), pbo_values)
        },
    )
    inputs = dict(
        oos_sharpe=1.0,
        catalog_evidence=catalog_evidence,
        pbo_evidence=pbo_evidence,
        leakage_ok=True,
        regime_profitable_frac=0.8,
        walk_forward_passed=True,
    )
    inputs.update(overrides)
    return inputs


def test_clean_strategy_passes():
    res = GATE.evaluate("alpha-good", **_gate_inputs("alpha-good"))
    assert res.verdict == ValidationVerdict.PASS and res.promotable


def test_overfit_strategy_fails():
    res = GATE.evaluate(
        "alpha-overfit",
        **_gate_inputs("alpha-overfit", low_dsr=True, high_pbo=True),
    )
    assert res.verdict == ValidationVerdict.FAIL and not res.promotable


def test_leaky_strategy_fails():
    res = GATE.evaluate(
        "alpha-leak", **_gate_inputs("alpha-leak", leakage_ok=False)
    )
    assert res.verdict == ValidationVerdict.FAIL
    assert any("LEAKAGE" in r for r in res.reasons)


def test_single_regime_strategy_does_not_pass():
    res = GATE.evaluate(
        "alpha-regime",
        **_gate_inputs("alpha-regime", regime_profitable_frac=0.2),
    )
    assert res.verdict == ValidationVerdict.FAIL and not res.promotable


def test_high_trial_count_forces_human_review():
    res = GATE.evaluate(
        "alpha-many-trials",
        **_gate_inputs("alpha-many-trials", trial_count=200),
    )
    assert res.verdict == ValidationVerdict.REQUIRES_HUMAN_REVIEW and not res.promotable


def test_single_soft_failure_is_warn_and_not_promotable():
    # a single SOFT failure (low OOS Sharpe) -> WARN, not promotable
    res = GATE.evaluate(
        "alpha-warn", **_gate_inputs("alpha-warn", oos_sharpe=-0.5)
    )
    assert res.verdict == ValidationVerdict.WARN and not res.promotable


def test_walk_forward_failure_is_a_hard_fail():
    # a real out-of-sample walk-forward failure is serious -> FAIL (review)
    res = GATE.evaluate(
        "alpha-wf",
        **_gate_inputs("alpha-wf", walk_forward_passed=False),
    )
    assert res.verdict == ValidationVerdict.FAIL and not res.promotable


def test_gate_rejects_invalid_inputs():
    inputs = _gate_inputs("bad")
    inputs["catalog_evidence"] = inputs["catalog_evidence"].model_copy(
        update={"deflated_sharpe": 1.5}
    )
    inputs["pbo_evidence"] = inputs["pbo_evidence"].model_copy(
        update={"pbo": -0.1}
    )
    res = GATE.evaluate("bad", **inputs)
    assert res.verdict == ValidationVerdict.FAIL
    assert any("EVIDENCE_INVALID" in r for r in res.reasons)


def test_marginal_trials_force_human_review():
    cfg = ValidationConfig(marginal_deflated_sharpe=1.01)
    res = ValidationGate(cfg).evaluate(
        "marg", **_gate_inputs("marg", trial_count=25)
    )
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


# --- capacity soft-fail (2026-07-10, gap audit rank #5) -----------------------

def test_capacity_ratio_none_is_a_complete_noop():
    """Default (not assessed) must match pre-fix behavior byte-for-byte — every existing caller
    (and every real currently-recorded evaluation) never passes capacity_ratio."""
    res = GATE.evaluate("alpha-good", **_gate_inputs("alpha-good"))
    assert res.verdict == ValidationVerdict.PASS
    assert not any("CAPACITY" in r for r in res.reasons)


def test_low_capacity_ratio_is_a_soft_warn():
    res = GATE.evaluate(
        "alpha-thin-capacity",
        **_gate_inputs("alpha-thin-capacity", capacity_ratio=0.1),
    )
    assert res.verdict == ValidationVerdict.WARN and not res.promotable
    assert any("CAPACITY_BELOW_TARGET_AUM" in r for r in res.reasons)


def test_capacity_ratio_above_floor_does_not_warn():
    res = GATE.evaluate(
        "alpha-ample-capacity",
        **_gate_inputs("alpha-ample-capacity", capacity_ratio=2.0),
    )
    assert res.verdict == ValidationVerdict.PASS
    assert not any("CAPACITY" in r for r in res.reasons)


def test_capacity_ratio_exactly_at_floor_does_not_warn():
    cfg = ValidationConfig()
    res = ValidationGate(cfg).evaluate(
        "alpha-at-floor",
        **_gate_inputs(
            "alpha-at-floor", capacity_ratio=cfg.min_capacity_ratio
        ),
    )
    assert not any("CAPACITY" in r for r in res.reasons)   # strict <, not <=


def test_low_capacity_combined_with_another_soft_failure_is_a_hard_fail():
    # two soft failures (low OOS sharpe + low capacity) escalate to FAIL, same as the existing
    # 2-soft-failures-escalate rule for any other pair of soft criteria.
    res = GATE.evaluate(
        "alpha-double-soft",
        **_gate_inputs(
            "alpha-double-soft", oos_sharpe=-0.5, capacity_ratio=0.1
        ),
    )
    assert res.verdict == ValidationVerdict.FAIL and not res.promotable


def _pbo_v3_source(first=0.1):
    return {
        "a" * 64: tuple((event_time, float(value)) for event_time, value in enumerate((first, 0.2, 0.3, 0.4))),
        "b" * 64: tuple((event_time, float(value)) for event_time, value in enumerate((0.2, 0.3, 0.4, 0.5))),
    }


def test_pbo_evidence_commitment_rejects_identity_source_and_matrix_mutation():
    from services.validation.pbo_evidence import create_batch_pbo_evidence, verify_batch_pbo_evidence
    evidence=create_batch_pbo_evidence(return_series_by_definition=_pbo_v3_source())
    assert verify_batch_pbo_evidence(evidence)
    assert not verify_batch_pbo_evidence(evidence.model_copy(update={"candidate_definition_hashes": ("c"*64,"b"*64)}))
    assert not verify_batch_pbo_evidence(evidence.model_copy(update={"performance_matrix": tuple(tuple(reversed(row)) for row in evidence.performance_matrix)}))
    assert not verify_batch_pbo_evidence(evidence.model_copy(update={"pbo": min(1.0,evidence.pbo+.1)}))


def test_pbo_evidence_v3_rejects_legacy_schemas_and_missing_commitment():
    from pydantic import ValidationError
    from quantos.contracts.validation import BatchPboEvidence
    from services.validation.pbo_evidence import create_batch_pbo_evidence, verify_batch_pbo_evidence
    evidence=create_batch_pbo_evidence(return_series_by_definition=_pbo_v3_source())
    assert evidence.schema_version == 3 and verify_batch_pbo_evidence(evidence)
    for schema in (1, 2):
        legacy=evidence.model_dump(mode="python"); legacy["schema_version"]=schema
        with pytest.raises(ValidationError, match="schema_version"):
            BatchPboEvidence.model_validate(legacy)
    missing=evidence.model_dump(mode="python"); missing.pop("identity_commitment")
    with pytest.raises(ValidationError): BatchPboEvidence.model_validate(missing)
    missing_schema=evidence.model_dump(mode="python"); missing_schema.pop("schema_version")
    with pytest.raises(ValidationError, match="schema_version"):
        BatchPboEvidence.model_validate(missing_schema)


def test_pbo_evidence_rejects_coerced_source_numbers_and_duplicate_times():
    from services.validation.pbo_evidence import create_batch_pbo_evidence
    source=_pbo_v3_source()
    bad=dict(source); bad["a"*64]=((0, True), *bad["a"*64][1:])
    with pytest.raises(TypeError, match="exact float"):
        create_batch_pbo_evidence(return_series_by_definition=bad)
    bad=dict(source); bad["a"*64]=((0, 0.1), (0, 0.2), *bad["a"*64][2:])
    with pytest.raises(ValueError, match="sorted and unique"):
        create_batch_pbo_evidence(return_series_by_definition=bad)


def test_pbo_commitment_binds_exact_timestamped_float64():
    from services.validation.pbo_evidence import create_batch_pbo_evidence, verify_batch_pbo_evidence
    first=create_batch_pbo_evidence(return_series_by_definition=_pbo_v3_source())
    second=create_batch_pbo_evidence(return_series_by_definition=_pbo_v3_source(0.100000000001))
    assert first.identity_commitment != second.identity_commitment
    assert not verify_batch_pbo_evidence(first.model_copy(update={"schema_version": 2}))
