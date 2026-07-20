"""Catalog-wide, replayable Deflated-Sharpe evidence.

The alpha catalog is the only mint.  This module is deliberately pure: it
turns an exact trial ledger plus exact OOS return observations into an immutable
``CatalogEvidence`` value and can replay every derived number later without a
database, wall clock, RNG, or network dependency.

Missing return histories are conservative.  They remain independent trials in
the effective-trial denominator, so missing evidence can never reduce the
multiple-testing penalty.
"""

from __future__ import annotations

import math
import statistics
from typing import Mapping, Sequence

from quantos.contracts.canonical import canonical_sha256
from quantos.contracts.hashing import stable_hash
from quantos.contracts.validation import (
    CatalogCorrelationObservation,
    CatalogEvidence,
    CatalogReturnObservation,
    CatalogSharpeObservation,
)
from services.validation.deflated_sharpe import deflated_sharpe_ratio
from services.validation.effective_trials import effective_trials

_VAR_SR_FLOOR = 1e-4
_ABS_TOL = 1e-12


def _is_sha256(value: str) -> bool:
    return (
        len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _observed_sr(returns: Sequence[float]) -> float | None:
    if len(returns) < 2:
        return None
    standard_deviation = statistics.pstdev(returns)
    if standard_deviation <= 0.0:
        return None
    return statistics.fmean(returns) / standard_deviation


def _abs_pearson(
    left: Sequence[float], right: Sequence[float]
) -> float | None:
    """Absolute Pearson correlation for an exact equal-length positional pair."""

    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    left_centered = tuple(value - left_mean for value in left)
    right_centered = tuple(value - right_mean for value in right)
    left_energy = sum(value * value for value in left_centered)
    right_energy = sum(value * value for value in right_centered)
    if left_energy <= 0.0 or right_energy <= 0.0:
        return None
    correlation = sum(
        left_value * right_value
        for left_value, right_value in zip(
            left_centered, right_centered, strict=True
        )
    ) / math.sqrt(left_energy * right_energy)
    if not math.isfinite(correlation):
        return None
    return min(1.0, abs(correlation))


def _effective_trial_count(trial_count: int, mean_abs_correlation: float) -> int:
    effective = effective_trials(trial_count, mean_abs_correlation)
    return max(1, min(trial_count, math.ceil(effective - _ABS_TOL)))


def _conservative_effective_trial_count(
    trial_count: int,
    correlations: Sequence[CatalogCorrelationObservation],
) -> tuple[float, int]:
    """Discount only the subset with actual comparable return evidence.

    Applying an equicorrelation average over the entire ledger can make unknown
    trials inherit the discount of a tiny observed cluster.  We instead count
    every unobserved/uncomparable trial one-for-one and apply ``effective_trials``
    only to the correlation-supported subset.
    """

    correlated_ids = {
        observation_id
        for item in correlations
        for observation_id in (item.left_id, item.right_id)
    }
    correlated_count = len(correlated_ids)
    if correlated_count == 0:
        return 0.0, trial_count
    comparable_pairs = correlated_count * (correlated_count - 1) / 2.0
    mean_abs_correlation = (
        sum(item.abs_correlation for item in correlations) / comparable_pairs
    )
    discounted = _effective_trial_count(
        correlated_count, mean_abs_correlation
    )
    return (
        mean_abs_correlation,
        trial_count - correlated_count + discounted,
    )


def _return_observations(
    return_series_by_definition: Mapping[str, Sequence[float]],
) -> tuple[CatalogReturnObservation, ...]:
    observations: list[CatalogReturnObservation] = []
    for definition_hash in sorted(return_series_by_definition):
        if not _is_sha256(definition_hash):
            raise ValueError("catalog return series keys must be definition hashes")
        values = tuple(
            float(value) for value in return_series_by_definition[definition_hash]
        )
        if len(values) < 2:
            continue
        if any(not math.isfinite(value) for value in values):
            raise ValueError("catalog returns must contain only finite values")
        observations.append(
            CatalogReturnObservation(
                observation_id=definition_hash,
                observation_count=len(values),
                returns=values,
                returns_sha256=canonical_sha256(list(values)),
            )
        )
    return tuple(observations)


def _sharpe_observations(
    returns: Sequence[CatalogReturnObservation],
) -> tuple[CatalogSharpeObservation, ...]:
    observations = []
    for item in returns:
        observed = _observed_sr(item.returns)
        if observed is not None:
            observations.append(
                CatalogSharpeObservation(
                    observation_id=item.observation_id,
                    sharpe=observed,
                )
            )
    return tuple(observations)


def _correlation_observations(
    returns: Sequence[CatalogReturnObservation],
) -> tuple[CatalogCorrelationObservation, ...]:
    observations: list[CatalogCorrelationObservation] = []
    for left_index, left in enumerate(returns):
        for right in returns[left_index + 1 :]:
            correlation = _abs_pearson(left.returns, right.returns)
            if correlation is None:
                continue
            observations.append(
                CatalogCorrelationObservation(
                    left_id=left.observation_id,
                    right_id=right.observation_id,
                    aligned_observation_count=left.observation_count,
                    abs_correlation=correlation,
                )
            )
    return tuple(observations)


def _snapshot_hash(
    *,
    trials: Sequence[str],
    sharpes: Sequence[CatalogSharpeObservation],
    returns: Sequence[CatalogReturnObservation],
    correlations: Sequence[CatalogCorrelationObservation],
) -> str:
    return canonical_sha256(
        {
            "trial_definition_hashes": list(trials),
            "sharpe_observations": [
                item.model_dump(mode="json") for item in sharpes
            ],
            "return_observations": [
                item.model_dump(mode="json") for item in returns
            ],
            "pairwise_abs_correlations": [
                item.model_dump(mode="json") for item in correlations
            ],
        }
    )


def create_catalog_evidence(
    *,
    subject_id: str,
    subject_definition_hash: str,
    trial_definition_hashes: Sequence[str],
    return_series_by_definition: Mapping[str, Sequence[float]],
) -> CatalogEvidence:
    """Mint one exact catalog snapshot and its replayable DSR decision input."""

    trials = tuple(sorted(set(trial_definition_hashes)))
    if len(trials) < 2:
        raise ValueError("catalog evidence requires at least two distinct trials")
    if any(not _is_sha256(value) for value in trials):
        raise ValueError("catalog trial ledger must contain definition hashes")
    if subject_definition_hash not in trials:
        raise ValueError("catalog subject is absent from the trial ledger")

    returns = _return_observations(return_series_by_definition)
    sharpes = _sharpe_observations(returns)
    if len(sharpes) < 2:
        raise ValueError("catalog evidence requires two honest Sharpe observations")
    sharpe_by_definition = {
        item.observation_id: item.sharpe for item in sharpes
    }
    return_by_definition = {
        item.observation_id: item for item in returns
    }
    if subject_definition_hash not in sharpe_by_definition:
        raise ValueError("catalog subject has no non-constant OOS return series")

    correlations = _correlation_observations(returns)
    mean_abs_correlation, effective_count = (
        _conservative_effective_trial_count(len(trials), correlations)
    )
    var_sr = max(
        statistics.pvariance(item.sharpe for item in sharpes),
        _VAR_SR_FLOOR,
    )
    subject_returns = return_by_definition[subject_definition_hash]
    observed_sr = sharpe_by_definition[subject_definition_hash]
    dsr = deflated_sharpe_ratio(
        observed_sr,
        subject_returns.observation_count,
        effective_count,
        var_sr,
    )
    evidence = CatalogEvidence(
        subject_id=subject_id,
        subject_definition_hash=subject_definition_hash,
        subject_returns_sha256=subject_returns.returns_sha256,
        n_obs=subject_returns.observation_count,
        trial_definition_hashes=trials,
        trial_count=len(trials),
        effective_trial_count=effective_count,
        sharpe_observations=sharpes,
        return_observations=returns,
        pairwise_abs_correlations=correlations,
        mean_abs_correlation=mean_abs_correlation,
        var_sr=var_sr,
        observed_sr=observed_sr,
        catalog_trial_set_hash=stable_hash(list(trials)),
        catalog_snapshot_hash=_snapshot_hash(
            trials=trials,
            sharpes=sharpes,
            returns=returns,
            correlations=correlations,
        ),
        deflated_sharpe=dsr,
    )
    if not verify_catalog_evidence(evidence):
        raise ValueError("constructed catalog evidence did not replay")
    return evidence


def verify_catalog_evidence(evidence: object) -> bool:
    """Revalidate exact inputs, effective trials, and Deflated Sharpe output."""

    try:
        if not isinstance(evidence, CatalogEvidence):
            return False
        validated = CatalogEvidence.model_validate(
            evidence.model_dump(mode="python")
        )
        expected_sharpes = _sharpe_observations(validated.return_observations)
        if expected_sharpes != validated.sharpe_observations:
            return False
        expected_correlations = _correlation_observations(
            validated.return_observations
        )
        if len(expected_correlations) != len(
            validated.pairwise_abs_correlations
        ):
            return False
        for expected, retained in zip(
            expected_correlations,
            validated.pairwise_abs_correlations,
            strict=True,
        ):
            if (
                expected.left_id != retained.left_id
                or expected.right_id != retained.right_id
                or expected.aligned_observation_count
                != retained.aligned_observation_count
                or not math.isclose(
                    expected.abs_correlation,
                    retained.abs_correlation,
                    rel_tol=0.0,
                    abs_tol=_ABS_TOL,
                )
            ):
                return False
        mean_abs_correlation, effective_count = (
            _conservative_effective_trial_count(
                validated.trial_count, expected_correlations
            )
        )
        expected_dsr = deflated_sharpe_ratio(
            validated.observed_sr,
            validated.n_obs,
            effective_count,
            validated.var_sr,
        )
        return (
            effective_count == validated.effective_trial_count
            and math.isclose(
                mean_abs_correlation,
                validated.mean_abs_correlation,
                rel_tol=0.0,
                abs_tol=_ABS_TOL,
            )
            and math.isclose(
                expected_dsr,
                validated.deflated_sharpe,
                rel_tol=0.0,
                abs_tol=_ABS_TOL,
            )
        )
    except (TypeError, ValueError, ArithmeticError, statistics.StatisticsError):
        return False


def verify_catalog_evidence_against_catalog(
    evidence: object, catalog: object, alpha_id: str
) -> bool:
    """Require mathematical replay plus an exact fresh catalog-mint match."""

    try:
        if not verify_catalog_evidence(evidence):
            return False
        if not isinstance(evidence, CatalogEvidence):
            return False
        if evidence.subject_id != alpha_id:
            return False
        minted = catalog.evidence(alpha_id)
        return (
            minted is not None
            and minted.evidence_hash == evidence.evidence_hash
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


__all__ = [
    "create_catalog_evidence",
    "verify_catalog_evidence",
    "verify_catalog_evidence_against_catalog",
]
