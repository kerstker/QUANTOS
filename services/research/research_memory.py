"""Research Memory (Alpha Factory upgrade Phase 7).

The experiment registry stores immutable EVALUATION artifacts (the numbers + a reproducibility hash).
This adds the research-DECISION metadata the audit found missing, as first-class fields:
  * hypothesis / economic_rationale — the natural-language idea being tested;
  * code_version                    — the code revision (git sha), caller-supplied (no subprocess here);
  * dataset_version                 — which data the result is keyed to;
  * lineage                         — parent ids + the mutation operator that produced the alpha;
  * regime_stability                — how stable the edge was across regimes;
  * verdict / reasons               — the gate's call and why;
  * next_action                     — the recommended follow-up.

It is an in-memory, deterministic enrichment layer keyed by alpha_id (latest record wins). It holds no
authority and drives no state — it is the substrate the research-brief synthesizer reads. Pure stdlib.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ResearchNote:
    alpha_id: str
    hypothesis: str = ""
    economic_rationale: str = ""
    code_version: str = ""
    dataset_version: str = ""
    generator: str = ""
    verdict: str = ""                       # ValidationVerdict value or ""
    reasons: tuple = ()
    net_sharpe: Optional[float] = None
    deflated_sharpe: Optional[float] = None
    cluster_id: str = ""
    parent_ids: tuple = ()
    mutation_op: str = ""
    regime_stability: Optional[float] = None
    data_dependencies: tuple = ()
    next_action: str = ""

    @classmethod
    def from_asset(cls, asset, *, hypothesis: str = "", code_version: str = "",
                   next_action: str = "") -> "ResearchNote":
        """Build a note from a rich AlphaAsset, pulling provenance, evidence and lineage. The
        hypothesis defaults to the asset's own; code_version / next_action are caller-supplied."""
        ev = asset.evaluation
        return cls(
            alpha_id=asset.alpha_id,
            hypothesis=hypothesis or asset.hypothesis,
            economic_rationale=asset.economic_rationale,
            code_version=code_version,
            dataset_version=asset.dataset_version,
            generator=asset.generator,
            verdict=ev.verdict.value if ev is not None else "",
            reasons=tuple(ev.reasons) if ev is not None else (),
            net_sharpe=ev.net_sharpe if ev is not None else None,
            deflated_sharpe=ev.deflated_sharpe if ev is not None else None,
            cluster_id=asset.cluster_id,
            parent_ids=tuple(asset.lineage.parent_ids),
            mutation_op=asset.lineage.mutation_op,
            regime_stability=asset.regime_stability,
            data_dependencies=tuple(asset.data_dependencies),
            next_action=next_action,
        )


class ResearchMemory:
    def __init__(self) -> None:
        self._notes: dict[str, ResearchNote] = {}

    def record(self, note: ResearchNote) -> None:
        """Record (or update) the note for an alpha — the latest record wins."""
        self._notes[note.alpha_id] = note

    def get(self, alpha_id: str) -> Optional[ResearchNote]:
        return self._notes.get(alpha_id)

    def all(self) -> tuple:
        return tuple(self._notes[k] for k in sorted(self._notes))

    def by_verdict(self, verdict: str) -> list:
        return [n for n in self.all() if n.verdict == verdict]

    def by_generator(self, generator: str) -> list:
        return [n for n in self.all() if n.generator == generator]
