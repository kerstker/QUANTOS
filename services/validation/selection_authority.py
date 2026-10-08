"""Fail-closed admission boundary for selection-bearing research results.

The research and intelligence layers must not import ``services.execution``
(their no-live-surface boundary is checked transitively).  More importantly,
the current deployment has no independently controlled signed source/result
verifier, so no caller-provided boolean or self-described record can authorize
lifecycle selection.

This predicate is intentionally not configurable.  Replacing the unconditional
denial requires a reviewed trust-root integration, including candidate/result
binding and adversarial tests; internal hashes alone are not authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

LEGACY_INVALIDATION_CODE = "INVALIDATED_PERP_LEDGER_V1"


class SelectionAuthorityBlocked(RuntimeError):
    """Raised before a legacy selection-bearing path performs any scoring."""


def selection_authority_admitted(
    *, requested: bool, authoritative_backtest_record: Mapping[str, Any] | None
) -> bool:
    """Return false until an independent signed verifier is integrated."""

    del requested, authoritative_backtest_record
    return False


def require_diagnostic_selection_seam(component: str, *, diagnostic_only: bool) -> None:
    """Permit only an explicitly requested non-authoritative algorithm seam."""

    if diagnostic_only:
        return
    name = str(component).strip() or "unknown"
    raise SelectionAuthorityBlocked(
        f"{LEGACY_INVALIDATION_CODE}: {name} is selection-bearing and has no "
        "independently controlled signed backtest authority; use the explicit "
        "diagnostic-only seam for pure algorithm tests"
    )


__all__ = [
    "LEGACY_INVALIDATION_CODE",
    "SelectionAuthorityBlocked",
    "require_diagnostic_selection_seam",
    "selection_authority_admitted",
]
