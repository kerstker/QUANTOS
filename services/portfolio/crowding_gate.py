"""Fail-closed crowding gate for research and portfolio diagnostics.

This module is a pure decision primitive. It evaluates caller-supplied crowding
evidence for a candidate book and never touches data stores, execution, broker,
pre-trade, lifecycle, credentials, RNG, or wall clock.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

GateStatus = Literal["PASS", "WARN", "BLOCK"]
_EPS = 1e-12


@dataclass(frozen=True)
class CrowdingGateDecision:
    status: GateStatus
    score: float | None
    threshold: float
    reasons: tuple[str, ...]
    exposures: dict[str, float]


def _is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _validate_config(
    *,
    threshold: float,
    warn_threshold: float,
    min_coverage: float,
) -> None:
    if not _is_finite_number(threshold) or not _is_finite_number(warn_threshold):
        raise ValueError("thresholds must be finite numbers")
    if not 0.0 <= warn_threshold <= threshold:
        raise ValueError("warn_threshold must satisfy 0 <= warn_threshold <= threshold")
    if not _is_finite_number(min_coverage) or not 0.0 <= min_coverage <= 1.0:
        raise ValueError("min_coverage must satisfy 0 <= min_coverage <= 1")


def _block(
    score: float | None,
    threshold: float,
    reasons: tuple[str, ...],
    exposures: dict[str, float] | None = None,
) -> CrowdingGateDecision:
    return CrowdingGateDecision(
        status="BLOCK",
        score=score,
        threshold=float(threshold),
        reasons=reasons,
        exposures=dict(exposures or {}),
    )


def evaluate_crowding_gate(
    weights: Mapping[str, float],
    crowding_by_symbol: Mapping[str, float | None],
    *,
    threshold: float = 0.60,
    warn_threshold: float = 0.45,
    min_coverage: float = 0.80,
) -> CrowdingGateDecision:
    """Evaluate a long-short book against supplied crowding evidence."""
    _validate_config(
        threshold=threshold,
        warn_threshold=warn_threshold,
        min_coverage=min_coverage,
    )

    gross = 0.0
    abs_weights: dict[str, float] = {}
    for symbol in sorted(weights):
        weight = weights[symbol]
        if not _is_finite_number(weight):
            return _block(None, threshold, (f"invalid_weight:{symbol}",))
        abs_weight = abs(float(weight))
        if abs_weight > _EPS:
            abs_weights[symbol] = abs_weight
            gross += abs_weight

    if gross <= _EPS:
        return _block(None, threshold, ("empty_or_zero_gross_book",))

    covered_gross = 0.0
    weighted_score = 0.0
    exposures: dict[str, float] = {}
    for symbol, abs_weight in abs_weights.items():
        if symbol not in crowding_by_symbol:
            continue
        value = crowding_by_symbol[symbol]
        if not _is_finite_number(value):
            return _block(None, threshold, (f"invalid_crowding:{symbol}",), exposures)
        crowding = float(value)
        exposures[symbol] = crowding
        covered_gross += abs_weight
        weighted_score += abs_weight * crowding

    coverage = covered_gross / gross if gross > _EPS else 0.0
    if coverage + _EPS < float(min_coverage):
        return _block(
            None,
            threshold,
            (f"insufficient_coverage:{coverage:.6f}<{float(min_coverage):.6f}",),
            exposures,
        )

    if covered_gross <= _EPS:
        return _block(None, threshold, ("no_covered_crowding_evidence",), exposures)

    score = weighted_score / covered_gross
    if score > float(threshold):
        return _block(
            score,
            threshold,
            (f"crowding_score:{score:.6f}>threshold:{float(threshold):.6f}",),
            exposures,
        )
    if score > float(warn_threshold):
        return CrowdingGateDecision(
            status="WARN",
            score=score,
            threshold=float(threshold),
            reasons=(
                f"crowding_score:{score:.6f}>warn_threshold:{float(warn_threshold):.6f}",
            ),
            exposures=exposures,
        )
    return CrowdingGateDecision(
        status="PASS",
        score=score,
        threshold=float(threshold),
        reasons=("crowding_score_ok",),
        exposures=exposures,
    )


def project_symbol_crowding(
    weights: Mapping[str, float],
    factor_exposures: Mapping[str, Mapping[str, float]],
    factor_name: str = "crowding",
) -> dict[str, float]:
    """Extract the named factor exposure map for symbols present in weights."""
    factor = factor_exposures.get(factor_name)
    if not factor:
        return {}
    return {symbol: float(factor[symbol]) for symbol in sorted(weights) if symbol in factor}
