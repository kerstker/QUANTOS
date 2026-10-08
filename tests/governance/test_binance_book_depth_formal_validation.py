import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_book_depth_formal_validation_is_frozen_before_confirmation() -> None:
    from services.research.validation_dag import _canonical, _digest

    contract = json.loads(
        (
            ROOT
            / "config"
            / "binance_book_depth_formal_validation_v1.json"
        ).read_text(encoding="utf-8")
    )

    assert contract["status"] == "FROZEN_BEFORE_CONFIRMATION_OUTCOME_ACCESS"
    assert contract["subject"]["direction"] == -1
    assert contract["comparable_control"]["direction"] == 1
    assert contract["comparable_control"]["selection_role"] == (
        "PREDECLARED_NEGATIVE_DIRECTION_CONTROL_NOT_ELIGIBLE_TO_REPLACE_SUBJECT"
    )
    for role in ("subject", "comparable_control"):
        expected = _digest(
            {
                "canonical_definition": _canonical(
                    contract[role]["formal_definition"]
                )
            }
        )
        assert contract[role]["formal_definition_hash"] == expected
    population = contract["trial_population"]
    assert population["experiment_registry_trial_count"] == 2823
    assert population["formal_validation_envelope_count"] == 2
    assert population["effective_trial_count"] == 2825
    assert population["all_registry_trials_count_even_without_comparable_return_histories"] is True
    assert population["missing_histories_receive_no_correlation_discount"] is True
    oos = contract["validation_policy"]["oos"]
    assert oos == {
        "n": 84,
        "train_size": 36,
        "test_size": 12,
        "step": 12,
        "purged_kfold_k": 6,
        "embargo_periods": 1,
        "fdr_q": 0.1,
        "dsr_floor": 0.95,
        "pbo_max": 0.5,
        "min_oos_fold_mean": 0.0,
        "subject_only_can_pass": True,
        "control_is_never_admission_eligible": True,
    }
    liquidity = contract["liquidity_evidence"]
    assert liquidity["source_transport"] == "PUBLIC_HTTPS_NO_CREDENTIAL"
    assert liquidity["official_checksum_required"] is True
    assert liquidity["timeout_or_429_or_404_or_checksum_mismatch_or_empty_result"] == "BLOCK"
    assert all(value is False for value in contract["authorization"].values())
