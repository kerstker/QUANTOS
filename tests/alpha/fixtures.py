"""Test fixtures for promotion and lifecycle tests."""
from __future__ import annotations

from typing import Any

from quantos.contracts.alpha import AlphaEvaluation, PboAlignment
from quantos.contracts.hashing import stable_hash
from quantos.contracts.research import ValidationVerdict
from services.validation.pbo_evidence import create_batch_pbo_evidence


def bound_pbo_evidence(catalog, alpha_id: str):
    """Small deterministic evidence object for lifecycle/promotion unit tests."""

    definition_hash = catalog.get(alpha_id).definition_hash
    support_hash = stable_hash({"test_support_candidate": alpha_id})
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
    evidence = bound_pbo_evidence(catalog, alpha_id)
    return AlphaEvaluation(
        alpha_id=alpha_id,
        verdict=verdict,
        deflated_sharpe=deflated_sharpe,
        pbo=evidence.pbo,
        pbo_evidence=evidence,
        pbo_evidence_hash=evidence.evidence_hash,
        catalog_trial_count=(catalog.global_trial_count()
                             if catalog_trial_count is None else catalog_trial_count),
        catalog_trial_set_hash=(catalog.trial_set_hash()
                                if catalog_trial_set_hash is None else catalog_trial_set_hash),
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
