"""Rich Alpha Asset (blueprint §04 / Alpha Factory upgrade Phase 3).

Turns a bare alpha into a MANAGED research asset. It COMPOSES the existing frozen entities — the
inert `AlphaDefinition` (spec) and the reproducible `AlphaEvaluation` (scientific report) — rather
than duplicating their ~25 fields (adapt, don't rewrite, blueprint rule). On top it carries the
institutional metadata the audit found missing:

  * provenance — version, owner, generator (which search produced it), created_ts, dataset_version;
  * intent     — expression (serialized DSL), hypothesis, economic_rationale, tags;
  * dependencies — the data kinds (close/volume/funding/...) and operators it uses;
  * evidence   — the embedded AlphaEvaluation + the Phase-2 CPCV OOS distribution + regime_stability;
  * portfolio  — cluster_id, exposures, capacity_notional, post_cost_ir, marginal_contribution;
  * lineage    — parents, the mutation operator that produced it, and the performance delta vs parent;
  * lifecycle  — status, rejection_reasons, and references to the hash-chained AlphaTransition events.

Frozen + content-hashable for audit. This is a pure data contract: it holds no authority and drives no
state — the lifecycle manager + registry own transitions, exactly as before (containment preserved).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .alpha import AlphaDefinition, AlphaEvaluation
from .hashing import stable_hash
from .research import AlphaState, ValidationVerdict


class AlphaLineage(BaseModel):
    """How this alpha was derived: its parents, the operator that produced it, and the metric delta."""
    model_config = ConfigDict(frozen=True)
    parent_ids: tuple[str, ...] = ()
    mutation_op: str = ""                 # e.g. mutate / crossover / seed / manual / llm
    perf_delta: Optional[float] = None    # child metric - parent metric (e.g. dIR); None if unknown


class AlphaAsset(BaseModel):
    model_config = ConfigDict(frozen=True)

    # --- identity & provenance ---
    alpha_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    name: str = ""
    owner: str = ""                       # actor_id responsible (human or agent)
    generator: str = ""                   # guided / evolutionary / gp / mcts / cs_genome / llm / manual
    created_ts: int = Field(default=0, ge=0)
    dataset_version: str = ""

    # --- intent ---
    expression: str = ""                  # serialized DSL (alpha_expression / gp_tree)
    hypothesis: str = ""
    economic_rationale: str = ""
    tags: tuple[str, ...] = ()
    universe: str = ""

    # --- dependencies ---
    data_dependencies: tuple[str, ...] = ()       # data kinds / fields used
    operator_dependencies: tuple[str, ...] = ()

    # --- scientific evidence (compose the existing report + Phase-2 distribution) ---
    evaluation: Optional[AlphaEvaluation] = None
    cpcv: dict = Field(default_factory=dict)      # CPCV OOS-Sharpe distribution summary (Phase 2)
    regime_stability: Optional[float] = None      # fraction of regimes profitable / stability score
    decay_half_life: Optional[float] = None       # decay-curve summary (periods to half the baseline IC)
    validation_report_id: str = ""                # ref to the immutable experiment-registry artifact

    # --- portfolio context ---
    cluster_id: str = ""
    exposures: dict[str, float] = Field(default_factory=dict)
    capacity_notional: Optional[float] = None
    post_cost_ir: Optional[float] = None
    marginal_contribution: Optional[float] = None  # incremental IR vs the approved set

    # --- lineage ---
    lineage: AlphaLineage = Field(default_factory=AlphaLineage)

    # --- lifecycle ---
    status: AlphaState = AlphaState.DRAFT
    rejection_reasons: tuple[str, ...] = ()
    lifecycle_event_ids: tuple[str, ...] = ()     # refs to hash-chained AlphaTransition entries

    # --- derived (delegate to the embedded evaluation, fail-safe when absent) ---
    @property
    def verdict(self) -> Optional[ValidationVerdict]:
        return self.evaluation.verdict if self.evaluation is not None else None

    @property
    def deflated_sharpe(self) -> Optional[float]:
        return self.evaluation.deflated_sharpe if self.evaluation is not None else None

    @property
    def net_sharpe(self) -> Optional[float]:
        return self.evaluation.net_sharpe if self.evaluation is not None else None

    @property
    def is_promotable(self) -> bool:
        return self.evaluation is not None and self.evaluation.verdict == ValidationVerdict.PASS

    @property
    def asset_hash(self) -> str:
        """Content hash over the ENTIRE asset (every field, including the embedded evaluation). Any
        change — version, expression, owner, status, name, tags, metrics — yields a different hash, so
        it is a sound audit commitment / non-repudiation handle (review fix: it covered only 7 fields,
        letting assets that differed in owner/status/tags collide)."""
        return stable_hash(self.model_dump(mode="json"))

    @classmethod
    def from_definition(cls, definition: AlphaDefinition, *, evaluation: Optional[AlphaEvaluation] = None,
                        **meta) -> "AlphaAsset":
        """Build an asset from an existing AlphaDefinition (copying alpha_id/name/universe and using its
        factor_ids as the default data dependencies) plus optional evaluation and any asset metadata.

        CONTAINMENT: a derived alpha is always a DRAFT — this convenience factory REFUSES to set the
        privileged lifecycle fields (status / lifecycle_event_ids / rejection_reasons). Promotion is the
        lifecycle manager's job (human-only for APPROVED); it can never be forged here."""
        forbidden = _FORBIDDEN_FROM_DEFINITION & set(meta)
        if forbidden:
            raise ValueError(f"from_definition cannot set privileged lifecycle field(s) "
                             f"{sorted(forbidden)}; a derived alpha is a DRAFT — promote via "
                             "AlphaLifecycleManager")
        base = {
            "alpha_id": definition.alpha_id,
            "name": definition.name,
            "universe": definition.universe,
            "data_dependencies": definition.factor_ids,
        }
        base.update(meta)                 # caller-supplied metadata overrides the definition-derived defaults
        return cls(evaluation=evaluation, **base)


_FORBIDDEN_FROM_DEFINITION = frozenset({"status", "lifecycle_event_ids", "rejection_reasons"})
