"""Durable equity observations and fail-closed peak-equity validation."""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Mapping, Optional

from live.execution.state_io import atomic_write_json


@dataclass(frozen=True)
class PeakEquityValidation:
    peak_equity: float
    valid: bool
    reason: str
    observed_max_equity: Optional[float]


def _positive_finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def append_equity_observation(path: str, observation: Mapping[str, object]) -> None:
    """Append one accepted account-equity observation to the evidence log."""
    equity = observation.get("equity")
    if not _positive_finite(equity):
        raise ValueError("equity observation must be positive and finite")
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    payload = dict(observation)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def max_observed_equity(path: str) -> Optional[float]:
    """Return the maximum valid positive equity in an append-only observation log."""
    if not os.path.exists(path):
        return None
    maximum: Optional[float] = None
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(row, dict) or not _positive_finite(row.get("equity")):
                    continue
                equity = float(row["equity"])
                maximum = equity if maximum is None else max(maximum, equity)
    except OSError:
        return None
    return maximum


def _upper_bound_tolerance(value: float) -> float:
    return max(1e-9, abs(value) * 1e-12)


def load_validated_peak_equity(
    path: str,
    *,
    current_equity: float,
    observed_max_equity: Optional[float],
) -> PeakEquityValidation:
    """Validate a persisted peak against independently appended equity observations.

    A malformed or unsupported persisted state is not silently reset. The returned peak is
    zero so the existing risk monitor emits ``peak_equity_unknown`` and refuses automatic
    flattening based on untrusted state. A missing state is safe to reconstruct because the
    caller must first append the current observation.
    """
    if not _positive_finite(current_equity):
        return PeakEquityValidation(0.0, False, "invalid_current_equity", observed_max_equity)
    if not _positive_finite(observed_max_equity):
        return PeakEquityValidation(0.0, False, "equity_history_unavailable", observed_max_equity)

    observed_max = float(observed_max_equity)
    if float(current_equity) > observed_max + _upper_bound_tolerance(observed_max):
        return PeakEquityValidation(0.0, False, "current_equity_missing_from_history", observed_max)

    if not os.path.exists(path):
        reason = (
            "initialized_from_current_equity"
            if math.isclose(observed_max, float(current_equity), rel_tol=1e-12, abs_tol=1e-9)
            else "reconstructed_from_equity_history"
        )
        return PeakEquityValidation(observed_max, True, reason, observed_max)

    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return PeakEquityValidation(0.0, False, "unreadable_peak_equity_state", observed_max)
    if not isinstance(payload, dict) or not _positive_finite(payload.get("peak_equity")):
        return PeakEquityValidation(0.0, False, "invalid_persisted_peak_equity", observed_max)

    persisted_peak = float(payload["peak_equity"])
    if persisted_peak > observed_max + _upper_bound_tolerance(observed_max):
        return PeakEquityValidation(
            0.0,
            False,
            "peak_equity_exceeds_observed_history",
            observed_max,
        )

    return PeakEquityValidation(
        observed_max,
        True,
        "verified_against_equity_history",
        observed_max,
    )


def save_peak_equity(
    path: str,
    value: float,
    *,
    observed_equity: Optional[float] = None,
    observed_at_ms: Optional[int] = None,
    evidence_max_equity: Optional[float] = None,
) -> None:
    """Atomically persist a monotonic peak plus its latest observation provenance."""
    if not _positive_finite(value):
        raise ValueError("peak equity must be positive and finite")

    peak = float(value)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                existing = json.load(fh)
            existing_peak = existing.get("peak_equity") if isinstance(existing, dict) else None
            if _positive_finite(existing_peak):
                peak = max(peak, float(existing_peak))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass

    payload = {"schema_version": 2, "peak_equity": peak}
    if _positive_finite(observed_equity):
        payload["last_observed_equity"] = float(observed_equity)
    if isinstance(observed_at_ms, int) and not isinstance(observed_at_ms, bool):
        payload["last_observed_at_ms"] = observed_at_ms
    if _positive_finite(evidence_max_equity):
        payload["evidence_max_equity"] = float(evidence_max_equity)
    atomic_write_json(path, payload)


def record_and_validate_peak_equity(
    *,
    state_path: str,
    history_path: str,
    observation: Mapping[str, object],
) -> PeakEquityValidation:
    """Append one observation, validate the persisted peak, and save only valid state."""
    append_equity_observation(history_path, observation)
    current_equity = observation.get("equity")
    observed_max = max_observed_equity(history_path)
    checked = load_validated_peak_equity(
        state_path,
        current_equity=float(current_equity),
        observed_max_equity=observed_max,
    )
    if not checked.valid:
        return checked

    observed_at = observation.get("ts")
    save_peak_equity(
        state_path,
        checked.peak_equity,
        observed_equity=float(current_equity),
        observed_at_ms=(
            int(observed_at)
            if isinstance(observed_at, int) and not isinstance(observed_at, bool)
            else None
        ),
        evidence_max_equity=checked.observed_max_equity,
    )
    return checked
