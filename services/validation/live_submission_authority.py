"""Fail-closed authority boundary for ordinary live order submission.

An in-process mandate, validation attestation, boolean, or self-described
record is not an independent authority.  The current deployment has no
externally controlled signer/verifier bound to the exact deployment, market
snapshot, order batch, and one-time approval.  Ordinary live submission is
therefore unavailable.

This module is deliberately neutral: it imports neither ``live`` nor an
execution domain.  Opening the boundary requires a reviewed external trust-root
integration and adversarial binding/replay tests, not a configuration toggle.
The independently constrained emergency reduce-only channel is outside this
ordinary-submission boundary.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE = "LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE"


class LiveSubmissionAuthorityBlocked(RuntimeError):
    """Raised before an ordinary live-submission collaborator is touched."""


def live_submission_authority_admitted(
    *, requested: bool, authority_record: Mapping[str, Any] | None
) -> bool:
    """Admit no caller claim until an independent verifier is integrated."""

    del requested, authority_record
    return False


def require_live_submission_authority(component: str) -> None:
    """Reject ordinary live submission under the current trust posture."""

    name = str(component).strip() or "unknown"
    raise LiveSubmissionAuthorityBlocked(
        f"{LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE}: {name} has no independently "
        "controlled live-submission authority"
    )


def require_live_submission_diagnostic_seam(
    component: str, *, diagnostic_only: bool
) -> None:
    """Permit the private execution algorithm only for explicit offline tests."""

    if diagnostic_only is True:
        return
    name = str(component).strip() or "unknown"
    raise LiveSubmissionAuthorityBlocked(
        f"{LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE}: {name} is a private "
        "technical-diagnostic seam"
    )


__all__ = [
    "LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE",
    "LiveSubmissionAuthorityBlocked",
    "live_submission_authority_admitted",
    "require_live_submission_authority",
    "require_live_submission_diagnostic_seam",
]
