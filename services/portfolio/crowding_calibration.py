"""Deterministic sensitivity analysis for the load-bearing crowding gate."""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence

from services.portfolio.crowding_gate import evaluate_crowding_gate

SIGNED_MAX_CROWDING_Z = 3.0
SENSITIVITY_THRESHOLDS = (2.0, 2.5, 3.0, 3.5, 4.0)


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _distribution(values: Sequence[float]) -> dict | None:
    ordered = sorted(float(value) for value in values if _finite(value))
    if not ordered:
        return None

    def quantile(fraction: float) -> float:
        index = min(len(ordered) - 1, int(fraction * len(ordered)))
        return ordered[index]

    return {
        "n": len(ordered),
        "mean": round(statistics.fmean(ordered), 6),
        "min": round(ordered[0], 6),
        "p10": round(quantile(0.1), 6),
        "p50": round(quantile(0.5), 6),
        "p90": round(quantile(0.9), 6),
        "max": round(ordered[-1], 6),
    }


def _mapping(record: Mapping[str, object], key: str) -> Mapping[str, float]:
    value = record.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a mapping")
    normalized: dict[str, float] = {}
    for symbol, number in value.items():
        if not isinstance(symbol, str) or not symbol or not _finite(number):
            raise ValueError(f"{key} contains an invalid symbol or value")
        normalized[symbol] = float(number)
    return normalized


def _side_score(
    weights: Mapping[str, float], crowding: Mapping[str, float], *, short: bool
) -> float | None:
    selected = {
        symbol: abs(weight)
        for symbol, weight in weights.items()
        if (weight < 0.0 if short else weight > 0.0) and symbol in crowding
    }
    gross = sum(selected.values())
    if gross <= 0.0:
        return None
    return sum(weight * crowding[symbol] for symbol, weight in selected.items()) / gross


def _decision_summary(decisions: Sequence[tuple[int, object]]) -> dict:
    periods = {
        "PASS": [],
        "WARN": [],
        "BLOCK": [],
    }
    for period, decision in decisions:
        periods[decision.status].append(period)
    total = len(decisions)
    return {
        "n_pass": len(periods["PASS"]),
        "n_warn": len(periods["WARN"]),
        "n_block": len(periods["BLOCK"]),
        "block_rate": round(len(periods["BLOCK"]) / total, 6) if total else 0.0,
        "block_periods": periods["BLOCK"],
    }


def _replay(
    records: Sequence[dict], *, rank_key: str, max_crowding_z: float
) -> list[tuple[int, object]]:
    decisions = []
    for record in records:
        decisions.append(
            (
                record["period"],
                evaluate_crowding_gate(
                    record["weights"],
                    record[rank_key],
                    average_warn_only=True,
                    crowding_z_by_symbol=record["z_by_symbol"],
                    max_crowding_z=max_crowding_z,
                ),
            )
        )
    return decisions


def _normalize_records(records: Sequence[Mapping[str, object]]) -> list[dict]:
    normalized = []
    seen_periods: set[int] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("calibration record must be a mapping")
        period = record.get("period")
        if not isinstance(period, int) or isinstance(period, bool) or period in seen_periods:
            raise ValueError("period must be a unique integer")
        seen_periods.add(period)
        row = {
            "period": period,
            "weights": _mapping(record, "weights"),
            "dry_crowding": _mapping(record, "dry_crowding"),
            "live_crowding": _mapping(record, "live_crowding"),
            "z_by_symbol": _mapping(record, "z_by_symbol"),
        }
        for key in ("short_raw_gls", "short_mean_z", "short_max_z"):
            value = record.get(key)
            if value is not None and not _finite(value):
                raise ValueError(f"{key} must be finite or None")
            row[key] = None if value is None else float(value)
        normalized.append(row)
    return sorted(normalized, key=lambda row: row["period"])


def summarize_calibration_records(
    records: Sequence[Mapping[str, object]],
    *,
    signed_threshold: float = SIGNED_MAX_CROWDING_Z,
    sensitivity_thresholds: Sequence[float] = SENSITIVITY_THRESHOLDS,
) -> dict:
    """Replay frozen per-period inputs through the production crowding gate."""
    if not _finite(signed_threshold):
        raise ValueError("signed_threshold must be finite")
    thresholds = tuple(float(value) for value in sensitivity_thresholds)
    if not thresholds or any(not _finite(value) for value in thresholds):
        raise ValueError("sensitivity thresholds must be finite and non-empty")
    if list(thresholds) != sorted(set(thresholds)):
        raise ValueError("sensitivity thresholds must be unique and increasing")

    rows = _normalize_records(records)
    if not rows:
        raise ValueError("at least one calibration record is required")

    dry_signed = _decision_summary(
        _replay(rows, rank_key="dry_crowding", max_crowding_z=float(signed_threshold))
    )
    live_signed = _decision_summary(
        _replay(rows, rank_key="live_crowding", max_crowding_z=float(signed_threshold))
    )

    sensitivity = {}
    for threshold in thresholds:
        dry = _decision_summary(
            _replay(rows, rank_key="dry_crowding", max_crowding_z=threshold)
        )
        live = _decision_summary(
            _replay(rows, rank_key="live_crowding", max_crowding_z=threshold)
        )
        sensitivity[f"{threshold:.1f}"] = {
            "n_block": dry["n_block"],
            "block_rate": dry["block_rate"],
            "dry_n_block": dry["n_block"],
            "live_n_block": live["n_block"],
            "block_periods_equal": dry["block_periods"] == live["block_periods"],
        }

    dry_decisions = _replay(rows, rank_key="dry_crowding", max_crowding_z=float(signed_threshold))
    live_decisions = _replay(rows, rank_key="live_crowding", max_crowding_z=float(signed_threshold))
    max_weights = [
        max(abs(weight) for weight in row["weights"].values())
        / sum(abs(weight) for weight in row["weights"].values())
        for row in rows
    ]

    return {
        "n_periods": len(rows),
        "min_period": rows[0]["period"],
        "max_period": rows[-1]["period"],
        "signed_max_crowding_z": float(signed_threshold),
        "rank_statistics": {
            "dry_run_average": _distribution([decision.score for _, decision in dry_decisions]),
            "dry_run_short": _distribution(
                [_side_score(row["weights"], row["dry_crowding"], short=True) for row in rows]
            ),
            "dry_run_long": _distribution(
                [_side_score(row["weights"], row["dry_crowding"], short=False) for row in rows]
            ),
            "live_average": _distribution([decision.score for _, decision in live_decisions]),
            "live_short": _distribution(
                [_side_score(row["weights"], row["live_crowding"], short=True) for row in rows]
            ),
            "live_long": _distribution(
                [_side_score(row["weights"], row["live_crowding"], short=False) for row in rows]
            ),
        },
        "max_name_weight": _distribution(max_weights),
        "absolute_statistics": {
            key: _distribution([row[key] for row in rows])
            for key in ("short_raw_gls", "short_mean_z", "short_max_z")
        },
        "gate_at_signed_threshold": {
            "dry_run": dry_signed,
            "live": live_signed,
            "block_periods_equal": dry_signed["block_periods"] == live_signed["block_periods"],
        },
        "z_threshold_sensitivity": sensitivity,
        "provenance": "REAL_GATE_REPLAY_FROM_FROZEN_INPUTS",
    }
