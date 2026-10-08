"""Factor/alpha health propagation (727 §6 / Factor Garden F4, 2026-07-27).

Pure core, FACT-INJECTED: this module never opens a db/json — callers (tools drivers)
read the three authoritative health sources (services/data/admission_store.py
DataQualityAdmissionStore verdicts, services/data/refresh_history.py hash-chained
refresh history, data/cache/live_refresh_status.json watermarks) and inject
{dataset_id: DatasetHealth} facts. UNKNOWN is the honest default: no fact ≠ healthy.

This is the generalization of the per-driver precedent (forward_track_advance's
raw_lineage_block_reason / live_track_advance's batch_skip_reason): a failed
dependency PAUSES the dependent and leaves evidence — dirty data is never traded as
if it were clean.

Fail-closed rulings:
- Any dependency dataset BLOCK **or UNKNOWN** -> factor PAUSED (missing evidence does
  not pass anything); STALE (and nothing worse) -> STALE; all PASS -> HEALTHY.
- Any dependency factor not HEALTHY -> alpha PAUSED with blocking_factors detail.
- A factor with no declared datasets (or unknown to the graph) -> PAUSED, never HEALTHY.

NOT wired to registry writes: this slice is pure evaluation. Writing a verdict back via
FactorGardenRegistry.set_status is the caller's explicit decision — a write-back leaves
a hash-chained audit trail but is also an AUTOMATIC state change, so it stays with the
drivers, not here (docstring-level ruling, design doc F4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from .dependency_graph import DependencyGraph

DATASET_PASS = "PASS"
DATASET_BLOCK = "BLOCK"
DATASET_STALE = "STALE"
DATASET_UNKNOWN = "UNKNOWN"
_DATASET_STATUSES = frozenset({DATASET_PASS, DATASET_BLOCK, DATASET_STALE, DATASET_UNKNOWN})

FACTOR_HEALTHY = "HEALTHY"
FACTOR_STALE = "STALE"
FACTOR_PAUSED = "PAUSED"


@dataclass(frozen=True)
class DatasetHealth:
    dataset_id: str
    status: str = DATASET_UNKNOWN          # PASS | BLOCK | STALE | UNKNOWN
    reason: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_id, str) or not self.dataset_id.strip():
            raise ValueError("dataset_id must be a non-empty string")
        if self.status not in _DATASET_STATUSES:
            raise ValueError(f"unknown dataset health status: {self.status!r}")


@dataclass(frozen=True)
class FactorHealthVerdict:
    factor_id: str
    status: str                            # HEALTHY | STALE | PAUSED
    blocking_datasets: tuple = field(default_factory=tuple)
    reasons: tuple = field(default_factory=tuple)


def _fact_for(facts: Mapping[str, DatasetHealth], dataset_id: str) -> DatasetHealth:
    fact = facts.get(dataset_id)
    if fact is None:
        return DatasetHealth(dataset_id, DATASET_UNKNOWN, "no health fact injected")
    if not isinstance(fact, DatasetHealth):
        raise TypeError(f"facts[{dataset_id!r}] must be a DatasetHealth")
    return fact


def evaluate_factor_health(
    graph: DependencyGraph,
    facts: Mapping[str, DatasetHealth],
    *,
    factor_id: str,
) -> FactorHealthVerdict:
    """One factor's health from its declared dataset dependencies (fail-closed)."""
    if not isinstance(graph, DependencyGraph):
        raise TypeError("graph must be a DependencyGraph")
    datasets = graph.datasets_for_factor(factor_id)
    if not datasets:
        return FactorHealthVerdict(
            factor_id, FACTOR_PAUSED, (),
            (f"factor {factor_id!r} has no declared datasets in the dependency graph",),
        )
    blocking = []
    stale = []
    reasons = []
    for ds in datasets:                          # graph stores datasets sorted — deterministic
        fact = _fact_for(facts, ds)
        note = f"{ds}: {fact.status}" + (f" ({fact.reason})" if fact.reason else "")
        if fact.status in (DATASET_BLOCK, DATASET_UNKNOWN):
            blocking.append(ds)
            reasons.append(note)
        elif fact.status == DATASET_STALE:
            stale.append(ds)
            reasons.append(note)
    if blocking:
        return FactorHealthVerdict(factor_id, FACTOR_PAUSED, tuple(blocking), tuple(reasons))
    if stale:
        return FactorHealthVerdict(factor_id, FACTOR_STALE, (), tuple(reasons))
    return FactorHealthVerdict(factor_id, FACTOR_HEALTHY, (), ())


def propagate(
    graph: DependencyGraph,
    facts: Mapping[str, DatasetHealth],
    *,
    alphas: Optional[Mapping[str, tuple]] = None,
) -> dict:
    """Whole-graph evaluation -> {"factors": {fid: FactorHealthVerdict},
    "alphas": {aid: {"status", "blocking_factors", "reasons"}}}.

    Alphas come from the graph's own alpha links plus the optional `alphas` argument
    (same declaration shape); any dependency factor not HEALTHY pauses the alpha.
    """
    if not isinstance(graph, DependencyGraph):
        raise TypeError("graph must be a DependencyGraph")
    factor_verdicts = {
        fid: evaluate_factor_health(graph, facts, factor_id=fid)
        for fid in graph.topo_factors()
    }
    alpha_links = {aid: graph.factors_for_alpha(aid) for aid in graph.alpha_ids}
    for aid, fs in (alphas or {}).items():
        alpha_links.setdefault(aid, tuple(sorted(set(fs))))
    alpha_verdicts = {}
    for aid in sorted(alpha_links):
        fs = alpha_links[aid]
        if not fs:
            alpha_verdicts[aid] = {
                "status": FACTOR_PAUSED,
                "blocking_factors": (),
                "reasons": (f"alpha {aid!r} declares no dependency factors",),
            }
            continue
        bad = []
        reasons = []
        for fid in fs:                           # sorted at graph construction — deterministic
            verdict = factor_verdicts.get(fid)
            if verdict is None:
                bad.append(fid)
                reasons.append(f"{fid}: not in dependency graph")
            elif verdict.status != FACTOR_HEALTHY:
                bad.append(fid)
                reasons.append(f"{fid}: {verdict.status}")
        alpha_verdicts[aid] = {
            "status": FACTOR_PAUSED if bad else FACTOR_HEALTHY,
            "blocking_factors": tuple(bad),
            "reasons": tuple(reasons),
        }
    return {"factors": factor_verdicts, "alphas": alpha_verdicts}
