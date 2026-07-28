"""Bounded composition root for read-only artifact snapshot routes."""

from typing import Dict

from services.console.api.artifact_paper_views import (
    IC_PANEL_SNAPSHOT,
    _FORWARD_EVIDENCE_BASELINE,
    _PAPER_SHADOW_AUTO,
    _PAPER_SHADOW_REALTIME,
    _live_trading_payload as _paper_live_trading_payload,
    _paper_shadow_payload as _paper_shadow_snapshot,
    router,
)


def _paper_shadow_payload() -> Dict:
    """Compatibility seam for tests that inject isolated snapshot paths."""
    return _paper_shadow_snapshot(
        auto_path=_PAPER_SHADOW_AUTO,
        realtime_path=_PAPER_SHADOW_REALTIME,
    )


def _live_trading_payload(now_ms: int | None = None) -> Dict:
    """Compatibility seam for an injected attested forward-evidence path."""
    return _paper_live_trading_payload(
        now_ms=now_ms,
        forward_evidence_path=_FORWARD_EVIDENCE_BASELINE,
    )


__all__ = ["IC_PANEL_SNAPSHOT", "router"]
