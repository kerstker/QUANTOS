"""断点1 (方案A): collect the per-candidate `leakage_ok` evidence the search loops hand to the agent loop.

A periods provider MAY expose `leakage_ok(defn) -> Optional[bool]` — it holds the raw PIT bars + the factor's
compute, which the loop (PanelPeriods only) does not. This builds the `{alpha_id: {"leakage_ok": ...}}` dict
for `ResearchAgentLoop.run(evidence=...)`. Backward-compatible: a provider WITHOUT the hook yields `{}`, so
the evaluator's fail-closed NOT-ASSESSED path is unchanged. A per-defn computation error is omitted (the alpha
stays leakage_ok=None = not assessed), never raised — one bad factor must not abort the search batch.
"""
from __future__ import annotations

from typing import Sequence


def provider_leakage_evidence(provider, candidates: Sequence[tuple]) -> dict:
    """candidates: [(AlphaDefinition, periods), ...]. Returns evidence for ResearchAgentLoop.run."""
    fn = getattr(provider, "leakage_ok", None)
    if not callable(fn):
        return {}
    evidence: dict = {}
    for defn, _periods in candidates:
        try:
            evidence[defn.alpha_id] = {"leakage_ok": fn(defn)}
        except Exception:
            continue                       # uncomputable -> omit -> evaluator stays fail-closed (None)
    return evidence
