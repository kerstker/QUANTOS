import math

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from services.alpha.catalog import AlphaCatalog
from services.validation.catalog_evidence import (
    verify_catalog_evidence,
    verify_catalog_evidence_against_catalog,
)


def _definition(alpha_id: str, factor_id: str) -> AlphaDefinition:
    return AlphaDefinition(
        alpha_id=alpha_id,
        name=alpha_id,
        factor_ids=(factor_id,),
        universe="test",
    )


def _catalog() -> AlphaCatalog:
    catalog = AlphaCatalog()
    observations = {
        "a": (0.01, 0.02, -0.01, 0.03, 0.00, 0.02),
        "b": (0.011, 0.019, -0.009, 0.031, 0.001, 0.021),
        "c": (-0.02, 0.01, 0.03, -0.01, 0.02, -0.02),
    }
    for alpha_id, returns in observations.items():
        catalog.register(_definition(alpha_id, f"factor-{alpha_id}"))
        catalog.set_returns(alpha_id, returns)
    return catalog


def test_catalog_is_the_mint_for_replayable_dsr_evidence() -> None:
    catalog = _catalog()

    evidence = catalog.evidence("a")

    assert evidence is not None
    assert verify_catalog_evidence(evidence)
    assert verify_catalog_evidence_against_catalog(evidence, catalog, "a")
    assert evidence.subject_definition_hash == catalog.get("a").definition_hash
    assert evidence.catalog_trial_set_hash == catalog.trial_set_hash()
    assert evidence.trial_count == catalog.global_trial_count() == 3
    assert 1 <= evidence.effective_trial_count <= evidence.trial_count
    assert evidence.effective_trial_count < evidence.trial_count
    assert evidence.n_obs == len(catalog.returns("a"))
    assert math.isclose(evidence.observed_sr, catalog._sharpes["a"])
    assert 0.0 <= evidence.deflated_sharpe <= 1.0
    assert len(evidence.catalog_snapshot_hash) == 64
    assert len(evidence.evidence_hash) == 64


def test_catalog_evidence_tamper_is_rejected_even_when_model_copy_bypasses_validation() -> None:
    evidence = _catalog().evidence("a")
    assert evidence is not None

    tampered_dsr = evidence.model_copy(
        update={"deflated_sharpe": 1.0 - evidence.deflated_sharpe}
    )
    tampered_trials = evidence.model_copy(
        update={"effective_trial_count": evidence.trial_count}
    )

    assert not verify_catalog_evidence(tampered_dsr)
    assert not verify_catalog_evidence(tampered_trials)


def test_same_size_trial_set_substitution_invalidates_catalog_evidence() -> None:
    source = _catalog()
    evidence = source.evidence("a")
    assert evidence is not None

    substituted = AlphaCatalog()
    for alpha_id, factor_id, returns in (
        ("a", "factor-a", source.returns("a")),
        ("b", "factor-b", source.returns("b")),
        ("replacement", "factor-replacement", source.returns("c")),
    ):
        substituted.register(_definition(alpha_id, factor_id))
        substituted.set_returns(alpha_id, returns)

    assert substituted.global_trial_count() == source.global_trial_count()
    assert substituted.trial_set_hash() != source.trial_set_hash()
    assert not verify_catalog_evidence_against_catalog(
        evidence, substituted, "a"
    )


def test_catalog_snapshot_hash_binds_sub_rounding_return_changes() -> None:
    catalog = _catalog()
    evidence = catalog.evidence("a")
    assert evidence is not None

    changed_returns = list(catalog.returns("a"))
    changed_returns[0] += 1e-11
    catalog.set_returns("a", tuple(changed_returns))
    changed = catalog.evidence("a")

    assert changed is not None
    assert changed.catalog_trial_set_hash == evidence.catalog_trial_set_hash
    assert changed.catalog_snapshot_hash != evidence.catalog_snapshot_hash
    assert changed.evidence_hash != evidence.evidence_hash
    assert not verify_catalog_evidence_against_catalog(evidence, catalog, "a")


def test_trials_without_return_evidence_are_conservatively_independent() -> None:
    catalog = _catalog()
    for index in range(100):
        catalog.register_trial(stable_hash({"unobserved-trial": index}))

    evidence = catalog.evidence("a")

    assert evidence is not None
    assert evidence.trial_count == 103
    # Only three return series can support correlation evidence. Missing return
    # histories must not collapse a 103-trial ledger to three effective bets.
    assert evidence.effective_trial_count >= 100
    assert verify_catalog_evidence(evidence)


def test_catalog_evidence_requires_an_honest_cross_trial_population() -> None:
    catalog = AlphaCatalog()
    catalog.register(_definition("a", "factor-a"))
    catalog.set_returns("a", (0.01, 0.02, -0.01, 0.03))

    assert catalog.evidence("a") is None


def test_legacy_16_hex_trials_keep_count_but_mint_full_hash_evidence() -> None:
    catalog = AlphaCatalog()
    left = _definition("a", "factor-a")
    right = _definition("b", "factor-b")
    catalog.restore_evaluation_context(
        trials=("0123456789abcdef", left.definition_hash, right.definition_hash),
        historical_returns={},
        historical_pvalues={},
    )
    catalog.register(left)
    catalog.register(right)
    catalog.set_returns("a", (0.01, 0.02, -0.01, 0.03))
    catalog.set_returns("b", (-0.01, 0.01, 0.02, -0.02))

    evidence = catalog.evidence("a")

    assert evidence is not None
    assert catalog.global_trial_count() == evidence.trial_count == 3
    assert all(len(value) == 64 for value in evidence.trial_definition_hashes)
    assert "0123456789abcdef" not in evidence.trial_definition_hashes
    assert evidence.catalog_trial_set_hash == catalog.trial_set_hash()
    assert verify_catalog_evidence_against_catalog(evidence, catalog, "a")
