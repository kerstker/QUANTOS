"""AI Alpha Factory (727 §8) — hypothesis-first, falsifiable alpha production.

727 §8.1: every alpha search must start from an explicit, falsifiable economic
Hypothesis — "Sharpe 可能更高" alone is forbidden. This package is the services
-side home for that layer:

- `hypothesis.py` — the frozen Hypothesis value object (all §8.1 fields) with a
  content-derived id and a fail-closed builder;
- `hypothesis_store.py` — the append-only, hash-chained pre-registration ledger;
- `economic_rules.py` — the reusable rule checker the P5-2 search controller
  applies to hypotheses it did not construct.

Boundaries (non-negotiable):
- DETERMINISTIC — every timestamp is caller-supplied int ms; no wall clock, no
  uuid, no RNG; ids are content-derived.
- NO LLM CLIENT IN services/ (727 §8.4) — an LLM-produced hypothesis may only
  ever ENTER the system as structured data via tools/ drivers. Final state
  transitions belong to the Deterministic Judge + Governance Plane, never to a
  language-model vote.
- This package drives NO promotion, NO lifecycle transition, and NO order path;
  registering a hypothesis changes no gate (diagnostic, not a gate).
- Dependency direction: this package must NOT import services/intelligence.
"""

from .economic_rules import check_hypothesis_rules
from .hypothesis import (
    Hypothesis,
    HypothesisError,
    create_hypothesis,
    hypothesis_content_hash,
)
from .hypothesis_store import (
    HypothesisConflictError,
    HypothesisNotFoundError,
    HypothesisStoreError,
    SqliteHypothesisStore,
)

__all__ = [
    "Hypothesis",
    "HypothesisConflictError",
    "HypothesisError",
    "HypothesisNotFoundError",
    "HypothesisStoreError",
    "SqliteHypothesisStore",
    "check_hypothesis_rules",
    "create_hypothesis",
    "hypothesis_content_hash",
]
