"""Test fixtures for promotion and lifecycle tests."""
from __future__ import annotations

from typing import Any

from quantos.contracts.alpha import (
    AlphaDefinition,
    AlphaEvaluation,
    PboAlignment,
)
from quantos.contracts.research import ValidationVerdict
from services.validation.pbo_evidence import create_batch_pbo_evidence


def seed_catalog_evidence(catalog, alpha_id: str) -> None:
    """Give lifecycle fixtures a real two-hypothesis catalog snapshot."""

    alternating = tuple(-1.0 if index % 2 else 1.0 for index in range(400))

    def series(mean: float) -> tuple[float, ...]:
        return tuple(mean + 0.01 * sign for sign in alternating)

    if not catalog.returns(alpha_id):
        catalog.set_returns(alpha_id, series(0.005))
    if catalog.evidence(alpha_id) is not None:
        return
    support_id = f"__evidence_support__:{alpha_id}"
    try:
        catalog.get(support_id)
    except KeyError:
        catalog.register(
            AlphaDefinition(
                alpha_id=support_id,
                name="catalog evidence support",
                factor_ids=(f"support-{alpha_id}",),
                universe="test",
            )
        )
    catalog.set_returns(support_id, series(0.0045))


def bound_pbo_evidence(catalog, alpha_id: str):
    """Small deterministic evidence object for lifecycle/promotion unit tests."""

    seed_catalog_evidence(catalog, alpha_id)
    definition_hash = catalog.get(alpha_id).definition_hash
    support_hash = next(
        trial_hash
        for trial_hash in catalog.snapshot().trials
        if trial_hash != definition_hash
    )
    return create_batch_pbo_evidence(
        candidate_definition_hashes=(definition_hash, support_hash),
        performance_matrix=((0.04, 0.043), (-0.03, -0.027),
                            (0.05, 0.053), (-0.01, -0.007)),
        alignment=PboAlignment.POSITIONAL_LEGACY,
        aligned_period_count=8,
    )


def bound_alpha_evaluation(
    catalog,
    alpha_id: str,
    *,
    verdict: ValidationVerdict = ValidationVerdict.PASS,
    deflated_sharpe: float = 0.8,
    catalog_trial_count: int | None = None,
    catalog_trial_set_hash: str | None = None,
    marginal_ir: float | None = 0.5,
    **kwargs,
) -> AlphaEvaluation:
    seed_catalog_evidence(catalog, alpha_id)
    evidence = bound_pbo_evidence(catalog, alpha_id)
    catalog_evidence = catalog.evidence(alpha_id)
    assert catalog_evidence is not None
    evaluation = AlphaEvaluation(
        alpha_id=alpha_id,
        verdict=verdict,
        observed_sr=catalog_evidence.observed_sr,
        deflated_sharpe=catalog_evidence.deflated_sharpe,
        catalog_evidence=catalog_evidence,
        catalog_evidence_hash=catalog_evidence.evidence_hash,
        pbo=evidence.pbo,
        pbo_evidence=evidence,
        pbo_evidence_hash=evidence.evidence_hash,
        catalog_trial_count=catalog.global_trial_count(),
        catalog_trial_set_hash=catalog.trial_set_hash(),
        marginal_ir=marginal_ir,
        **kwargs,
    )
    updates = {}
    if catalog_trial_count is not None:
        updates["catalog_trial_count"] = catalog_trial_count
    if catalog_trial_set_hash is not None:
        updates["catalog_trial_set_hash"] = catalog_trial_set_hash
    if deflated_sharpe != 0.8:
        updates["deflated_sharpe"] = deflated_sharpe
    return evaluation.model_copy(update=updates) if updates else evaluation


def catalog_bound_evaluation(
    catalog,
    alpha_id: str,
    *,
    verdict: ValidationVerdict,
    pbo_evidence,
    marginal_ir: float | None = 0.5,
    **kwargs,
) -> AlphaEvaluation:
    """Build a structurally valid evaluation from an already-frozen batch."""

    catalog_evidence = catalog.evidence(alpha_id)
    assert catalog_evidence is not None
    return AlphaEvaluation(
        alpha_id=alpha_id,
        verdict=verdict,
        observed_sr=catalog_evidence.observed_sr,
        deflated_sharpe=catalog_evidence.deflated_sharpe,
        catalog_evidence=catalog_evidence,
        catalog_evidence_hash=catalog_evidence.evidence_hash,
        pbo=pbo_evidence.pbo if pbo_evidence is not None else 0.0,
        pbo_evidence=pbo_evidence,
        pbo_evidence_hash=(
            pbo_evidence.evidence_hash if pbo_evidence is not None else ""
        ),
        catalog_trial_count=catalog_evidence.trial_count,
        catalog_trial_set_hash=catalog_evidence.catalog_trial_set_hash,
        marginal_ir=marginal_ir,
        **kwargs,
    )


class MockAlphaCatalog:
    """Mock catalog for promotion tests."""

    def __init__(self):
        self._alphas: dict[str, dict[str, Any]] = {}

    def get(self, alpha_id: str) -> dict[str, Any] | None:
        return self._alphas.get(alpha_id)

    def _set_state(self, alpha_id: str, state: Any) -> None:
        if alpha_id in self._alphas:
            self._alphas[alpha_id]["state"] = state


class MockExperimentRegistry:
    """Mock experiment registry for promotion tests."""

    def __init__(self, db_path: str, base_dir: str = ""):
        self._db_path = db_path
        self._base_dir = base_dir
        self._evaluations: dict[str, dict[str, Any]] = {}

    def load_into(self, catalog: MockAlphaCatalog) -> None:
        pass

    def get_evaluation(self, alpha_id: str) -> dict[str, Any] | None:
        return self._evaluations.get(alpha_id)

    def verify_evaluation(self, run_id: str) -> str:
        return "mock_content_hash"


class MockPaperStore:
    """Mock paper store for promotion tests."""

    def get_alpha_recommendation(self, alpha_id: str) -> dict[str, Any]:
        return {
            "alpha_id": alpha_id,
            "live_ic": 0.5,
            "trade_count": 100,
        }
