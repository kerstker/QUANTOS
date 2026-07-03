"""Fail-closed crowding gate for research and portfolio diagnostics.

This module is a pure decision primitive. It evaluates caller-supplied crowding
evidence for a candidate book and never touches data stores, execution, broker,
pre-trade, lifecycle, credentials, RNG, or wall clock.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

GateStatus = Literal["PASS", "WARN", "BLOCK"]
_EPS = 1e-12
_SEVERITY = {"PASS": 0, "WARN": 1, "BLOCK": 2}


def _worst(a: GateStatus, b: GateStatus) -> GateStatus:
    """The more severe of two statuses (PASS < WARN < BLOCK) — escalation never de-escalates."""
    return a if _SEVERITY[a] >= _SEVERITY[b] else b


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
    max_name_weight: float = 0.25,
    concentrated_crowding_block: float | None = None,
    adv_by_symbol: Mapping[str, float] | None = None,
    book_notional: float | None = None,
    max_exit_days: float = 5.0,
) -> CrowdingGateDecision:
    """Evaluate a long-short book against supplied crowding evidence.

    Beyond the gross-weighted average crowding score, two Rank #10a criteria (UNCALIBRATED
    thresholds, calibration is R-1) evaluate independently so a few extreme names cannot hide
    behind the average:
    - concentration: a name that is BOTH concentrated (abs_weight/gross > max_name_weight) AND
      crowded (> concentrated_crowding_block, default = threshold) trips BLOCK on its own;
    - liquidity (optional, needs adv_by_symbol + book_notional): exit_days = position_notional/ADV
      over max_exit_days -> WARN; a non-finite/non-positive ADV for a covered name fails closed.
    """
    _validate_config(
        threshold=threshold,
        warn_threshold=warn_threshold,
        min_coverage=min_coverage,
    )
    if not _is_finite_number(max_name_weight) or not 0.0 < float(max_name_weight) <= 1.0:
        raise ValueError("max_name_weight must satisfy 0 < max_name_weight <= 1")
    if concentrated_crowding_block is not None and not _is_finite_number(concentrated_crowding_block):
        raise ValueError("concentrated_crowding_block must be a finite number or None")
    if not _is_finite_number(max_exit_days) or float(max_exit_days) <= 0.0:
        raise ValueError("max_exit_days must be a positive finite number")

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

    # base verdict from the gross-weighted average
    status: GateStatus = "PASS"
    reasons: list[str] = []
    if score > float(threshold):
        status = "BLOCK"
        reasons.append(f"crowding_score:{score:.6f}>threshold:{float(threshold):.6f}")
    elif score > float(warn_threshold):
        status = "WARN"
        reasons.append(f"crowding_score:{score:.6f}>warn_threshold:{float(warn_threshold):.6f}")

    # Rank #10a — max-single-name concentration criterion (dilution fix): a concentrated AND
    # crowded name trips independently of the (dilutable) average.
    block_hi = float(concentrated_crowding_block) if concentrated_crowding_block is not None else float(threshold)
    for symbol in sorted(exposures):
        conc = abs_weights[symbol] / gross
        if conc <= float(max_name_weight):
            continue
        crowding = exposures[symbol]
        if crowding > block_hi:
            status = _worst(status, "BLOCK")
            reasons.append(f"concentrated_crowded_block:{symbol}:w={conc:.4f},c={crowding:.4f}>{block_hi:.4f}")
        elif crowding > float(warn_threshold):
            status = _worst(status, "WARN")
            reasons.append(f"concentrated_crowded_warn:{symbol}:w={conc:.4f},c={crowding:.4f}")

    # Rank #10a — optional ADV / liquidity dimension: exit_days = position_notional / ADV.
    if adv_by_symbol is not None and book_notional is not None:
        bn = float(book_notional)
        for symbol in sorted(abs_weights):
            if symbol not in adv_by_symbol:
                continue
            adv = adv_by_symbol[symbol]
            if not _is_finite_number(adv) or float(adv) <= 0.0:
                return _block(score, threshold, (f"invalid_adv:{symbol}",), exposures)   # fail-closed
            exit_days = (abs_weights[symbol] * bn) / float(adv)
            if exit_days > float(max_exit_days):
                status = _worst(status, "WARN")
                reasons.append(f"exit_days:{symbol}:{exit_days:.2f}>{float(max_exit_days):.2f}")

    if status == "PASS" and not reasons:
        reasons = ["crowding_score_ok"]
    return CrowdingGateDecision(
        status=status,
        score=score,
        threshold=float(threshold),
        reasons=tuple(reasons),
        exposures=exposures,
    )


def summarize_gate_decisions(decisions: Sequence[CrowdingGateDecision]) -> dict:
    """Aggregate per-period crowding-gate decisions into a run-detail summary (Rank #10 wiring),
    so a dry-run book's crowding profile is surfaced read-only on the console. Pure/deterministic."""
    by_status = {"PASS": 0, "WARN": 0, "BLOCK": 0}
    worst: float | None = None
    reasons: list[str] = []
    for decision in decisions:
        by_status[decision.status] = by_status.get(decision.status, 0) + 1
        if decision.score is not None and (worst is None or decision.score > worst):
            worst = decision.score
        if decision.status in ("WARN", "BLOCK"):
            reasons.extend(decision.reasons)
    return {
        "n_periods": len(decisions),
        "n_pass": by_status["PASS"],
        "n_warn": by_status["WARN"],
        "n_block": by_status["BLOCK"],
        "worst_score": worst,
        "sample_reasons": sorted(set(reasons))[:10],
        "provenance": "REAL_CROWDING_GATE",
    }


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
