"""Fail-closed projection of backtest-derived console evidence.

Console routes are read-only, but their labels and metrics are still consumed by
operators.  A retained V1 record or a deterministic demo replay must therefore
not become a second authority path merely because it is rendered over HTTP.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from services.execution.backtest_authority import (
    LEGACY_INVALIDATION_CODE,
    is_authoritative_backtest_record,
)

DIAGNOSTIC_EVIDENCE_STATUS = "DIAGNOSTIC_ONLY"

_NULL_METRIC_TOKENS = (
    "sharpe",
    "pnl",
    "profit",
    "return",
    "drawdown",
    "capacity",
)
_NULL_METRIC_KEYS = frozenset(
    {
        "ic",
        "rank_ic",
        "icir",
        "pvalue",
        "turnover",
        "composite_score",
        "performance_score",
        "avg_ic",
        "readiness_score",
        "avg_readiness_score",
        "observed_sr",
        "deflated_sharpe",
        "annual_turnover",
        "annual_volatility",
        "avg_net_exposure_pct",
        "current_equity",
        "final_equity",
        "funding_pnl",
        "gross_exposure",
        "initial_equity",
        "net_exposure",
        "n_days",
        "total_funding",
        "total_trades",
        "volatility",
    }
)
_EMPTY_LIST_KEYS = frozenset(
    {
        "runs",
        "history",
        "equity_curve",
        "equity_curve_vt",
        "drawdown_curve",
        "drawdown_periods",
        "monthly_returns",
        "segments",
        "selected_ids",
        "top_candidates",
    }
)
_EMPTY_MAPPING_KEYS = frozenset(
    {
        "latest",
        "weights",
        "portfolio",
        "risk_report",
    }
)
_ZERO_COUNT_KEYS = frozenset(
    {
        "approved",
        "paper_passed",
        "portfolio_candidates",
        "risk_approved",
        "alpha_approved",
        "n_ready",
        "n_runs",
    }
)


def authority_admitted(records: Iterable[Mapping[str, Any]] = ()) -> bool:
    """True only when every member of a non-empty record set is admitted."""

    retained = tuple(records)
    return bool(retained) and all(
        isinstance(record, Mapping) and is_authoritative_backtest_record(record)
        for record in retained
    )


def _positive_status(value: str) -> bool:
    upper = value.strip().upper()
    return (
        upper
        in {
            "OK",
            "READY",
            "PASS",
            "PASSED",
            "APPROVED",
            "PAPER_PASSED",
            "RISK_APPROVED",
            "PAPER",
            "LIVE",
            "SUCCESS",
            "COMPLETED",
            "ELIGIBLE",
        }
        or upper.endswith("_OK")
        or upper.startswith("APPROVED_FOR_")
        or upper.startswith("RISK_APPROVED")
        or upper.startswith("PASS")
        or upper in {"VALIDATED", "PAPER_TRADING", "SHADOW", "ACTIVE"}
        or upper in {
            "FINDINGS_OK",
            "DRY_RUN_OK",
            "GONOGO_EVIDENCE_READY",
            "VALIDATED_NOT_LIVE_READY",
        }
    )


def _scrub(value: Any, *, key: str = "") -> Any:
    normalized = key.lower()
    if normalized in _EMPTY_LIST_KEYS:
        return []
    if normalized in _EMPTY_MAPPING_KEYS:
        return {}
    if normalized in _NULL_METRIC_KEYS or any(
        token in normalized for token in _NULL_METRIC_TOKENS
    ):
        return None
    if normalized in _ZERO_COUNT_KEYS:
        return False if isinstance(value, bool) else 0
    if normalized.endswith("_approved") or normalized.endswith("_passed"):
        return (
            False
            if isinstance(value, bool)
            else 0
            if isinstance(value, int)
            else None
        )
    if isinstance(value, Mapping):
        return {
            str(child_key): _scrub(child, key=str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, list):
        return [_scrub(child) for child in value]
    if isinstance(value, tuple):
        return [_scrub(child) for child in value]
    if isinstance(value, str):
        status_key = normalized in {
            "state",
            "lifecycle_state",
            "status",
            "decision",
            "verdict",
            "readiness_class",
            "recommendation",
            "readiness",
            "stage",
            "outcome",
            "result",
            "classification",
            "eligibility",
            "decision_class",
        } or normalized.endswith("_status")
        if normalized == "recommendation" and value != "NO_LIVE_DATA":
            return LEGACY_INVALIDATION_CODE
        if status_key and _positive_status(value):
            return LEGACY_INVALIDATION_CODE
    return value


def scrub_backtest_payload(
    payload: Mapping[str, Any],
    *,
    authority_records: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return a non-bearing diagnostic projection.

    An admitted record cannot safely release an arbitrary caller-composed
    aggregate: every displayed metric would first need an exact result-hash
    binding to that record.  No such projection builder exists yet, so this
    boundary remains closed even if a future verifier admits ``authority_records``.
    The parameter is retained to keep callers explicit about candidate inputs.
    """

    del authority_records
    scrubbed = _scrub(payload)
    assert isinstance(scrubbed, dict)
    scrubbed.update(
        {
            "authoritative": False,
            "evidence_status": DIAGNOSTIC_EVIDENCE_STATUS,
            "invalidation_code": LEGACY_INVALIDATION_CODE,
        }
    )
    return scrubbed


def invalidated_check(check: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
    """Preserve an operational checklist row while blocking V1 evidence."""

    row = dict(check)
    row["status"] = "BLOCKED"
    row["reason"] = reason
    row["authoritative"] = False
    row["evidence_status"] = DIAGNOSTIC_EVIDENCE_STATUS
    row["invalidation_code"] = LEGACY_INVALIDATION_CODE
    return row


__all__ = [
    "DIAGNOSTIC_EVIDENCE_STATUS",
    "authority_admitted",
    "invalidated_check",
    "scrub_backtest_payload",
]
