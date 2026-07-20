"""Canonical reproducibility binding for ``AlphaEvaluation`` artifacts."""

from __future__ import annotations

from quantos.contracts.alpha import AlphaEvaluation
from quantos.contracts.canonical import canonical_sha256


def evaluation_reproducibility_hash(
    evaluation: AlphaEvaluation,
    *,
    dataset_version: str | None = None,
) -> str:
    """Hash every decision field plus the exact dataset identity.

    ``dataset_version`` is accepted separately for store replay.  If the model
    already carries a non-empty value it must agree, preventing an artifact from
    being rebound to another dataset at persistence time.
    """

    bound_dataset_version = (
        evaluation.dataset_version
        if dataset_version is None
        else dataset_version
    )
    if (
        evaluation.dataset_version
        and evaluation.dataset_version != bound_dataset_version
    ):
        raise ValueError("evaluation dataset version mismatch")
    payload = evaluation.model_dump(
        mode="json", exclude={"reproducibility_hash"}
    )
    payload["dataset_version"] = bound_dataset_version
    return canonical_sha256(
        {
            "schema_version": "ALPHA_EVALUATION_REPRODUCIBILITY_V2",
            "evaluation": payload,
        }
    )


def bind_evaluation_reproducibility(
    evaluation: AlphaEvaluation,
    *,
    dataset_version: str,
) -> AlphaEvaluation:
    """Return a fully validated evaluation with its canonical hash attached."""

    payload = evaluation.model_dump(mode="python")
    payload["dataset_version"] = dataset_version
    payload["reproducibility_hash"] = ""
    normalized = AlphaEvaluation.model_validate(payload)
    payload["reproducibility_hash"] = evaluation_reproducibility_hash(
        normalized, dataset_version=dataset_version
    )
    return AlphaEvaluation.model_validate(payload)


def verify_evaluation_reproducibility(
    evaluation: AlphaEvaluation,
    *,
    dataset_version: str,
) -> bool:
    try:
        if not evaluation.reproducibility_hash:
            return False
        expected = evaluation_reproducibility_hash(
            evaluation, dataset_version=dataset_version
        )
        return expected == evaluation.reproducibility_hash
    except (TypeError, ValueError):
        return False


__all__ = [
    "bind_evaluation_reproducibility",
    "evaluation_reproducibility_hash",
    "verify_evaluation_reproducibility",
]
