"""Alpha Factory contracts (blueprint §04 / Phase 6).

Entities + the alpha LIFECYCLE state machine (mirrors order.py). The state
machine encodes a hard safety property: only a HUMAN actor can drive an alpha to
APPROVED; agents/automation may advance up to PAPER_TRADING and may de-risk to
RETIRED, but can never self-approve (critique P0-3 fix — the actor carries a
verifiable kind).
"""
from __future__ import annotations

import math
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .hashing import stable_hash
from .research import AlphaState, ValidationVerdict


class ActorKind(str, Enum):
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class Actor(BaseModel):
    model_config = ConfigDict(frozen=True)
    actor_id: str = Field(min_length=1)
    kind: ActorKind

    @property
    def is_human(self) -> bool:
        return self.kind == ActorKind.HUMAN

    @property
    def is_agent(self) -> bool:
        return self.kind == ActorKind.AGENT


class AlphaDefinition(BaseModel):
    """An inert candidate alpha spec produced by the (proposal-only) generator."""
    model_config = ConfigDict(frozen=True)
    alpha_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    factor_ids: tuple[str, ...] = ()
    universe: str = ""
    params: dict[str, str] = Field(default_factory=dict)

    @property
    def definition_hash(self) -> str:
        return stable_hash({
            "factor_ids": list(self.factor_ids),
            "universe": self.universe,
            "params": self.params,
        })


class PboAlignment(str, Enum):
    EVENT_TIME_INTERSECTION = "EVENT_TIME_INTERSECTION"
    POSITIONAL_LEGACY = "POSITIONAL_LEGACY"


class BatchPboEvidence(BaseModel):
    """Replayable CSCV input retained with each evaluated candidate.

    The performance matrix is deliberately the exact input to the existing
    ``pbo_cscv`` implementation: rows are time blocks and columns follow
    ``candidate_definition_hashes``.  The model validates structure and calendar
    binding; ``services.validation.pbo_evidence`` performs the mathematical replay.
    """

    model_config = ConfigDict(frozen=True)
    schema_version: Literal[1] = 1
    algorithm: Literal["CSCV_V1"] = "CSCV_V1"
    candidate_definition_hashes: tuple[str, ...]
    performance_matrix: tuple[tuple[float, ...], ...]
    alignment: PboAlignment
    aligned_period_count: int = Field(ge=4)
    aligned_event_times: tuple[int, ...] = ()
    aligned_event_times_sha256: str = Field(min_length=64, max_length=64)
    pbo: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_evidence_shape(self) -> "BatchPboEvidence":
        hashes = self.candidate_definition_hashes
        if len(hashes) < 2 or len(set(hashes)) != len(hashes):
            raise ValueError("PBO evidence requires at least two distinct candidate definitions")
        if any(
            len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            for value in hashes
        ):
            raise ValueError("candidate definition hashes must be SHA-256 hex digests")
        rows = self.performance_matrix
        if not (2 <= len(rows) <= 8):
            raise ValueError("PBO performance matrix must contain between 2 and 8 blocks")
        if any(len(row) != len(hashes) for row in rows):
            raise ValueError("PBO performance matrix must be rectangular and candidate-bound")
        if any(not math.isfinite(value) for row in rows for value in row):
            raise ValueError("PBO performance matrix must contain only finite values")
        if len(rows) > self.aligned_period_count:
            raise ValueError("PBO block count cannot exceed aligned period count")

        if self.alignment == PboAlignment.EVENT_TIME_INTERSECTION:
            times = self.aligned_event_times
            if (
                len(times) != self.aligned_period_count
                or any(value < 0 for value in times)
                or tuple(sorted(set(times))) != times
            ):
                raise ValueError("aligned event times must be exact, sorted, unique, and complete")
            expected_calendar_hash = stable_hash(list(times))
        else:
            if self.aligned_event_times:
                raise ValueError("positional legacy evidence cannot claim event-time alignment")
            expected_calendar_hash = stable_hash(
                {
                    "alignment": self.alignment.value,
                    "aligned_period_count": self.aligned_period_count,
                }
            )
        if self.aligned_event_times_sha256 != expected_calendar_hash:
            raise ValueError("aligned event-time digest mismatch")
        return self

    @property
    def evidence_hash(self) -> str:
        return stable_hash(self.model_dump(mode="json"))


class AlphaEvaluation(BaseModel):
    """Frozen, reproducible evaluation report for one alpha. Proposes a verdict;
    promotion is the lifecycle manager's job, never this report's."""
    model_config = ConfigDict(frozen=True)
    alpha_id: str = Field(min_length=1)
    ic_mean: Optional[float] = None
    icir: Optional[float] = None
    ic_tstat: Optional[float] = None
    ic_pvalue: Optional[float] = None
    hit_rate: Optional[float] = None
    ic_autocorr: Optional[float] = None
    mean_breadth: float = 0.0
    n_periods: int = Field(default=0, ge=0)
    coverage_ok: bool = False
    net_sharpe: float = 0.0
    capacity_adj_sharpe: float = 0.0          # net_sharpe haircut by capacity
    observed_sr: float = 0.0
    turnover: float = Field(default=0.0, ge=0)
    marginal_ir: Optional[float] = None      # incremental edge vs approved set (step 6/13)
    max_corr_vs_approved: Optional[float] = None   # 2026-07-10: max |pairwise corr| vs the
                                              # approved set — a narrower, single-pair check
                                              # marginal_ir's multivariate Gram-Schmidt residual
                                              # can miss (a candidate with strong-enough OWN edge
                                              # can clear marginal_ir even at rho~0.95 to one
                                              # approved alpha). None = not assessed (no approved
                                              # set yet, or insufficient overlap with every member).
    crowding_score: Optional[float] = None    # public-factor R^2 (None = unknown)
    deflated_sharpe: float = Field(default=0.0, ge=0, le=1)
    pbo: float = Field(default=0.0, ge=0, le=1)
    pbo_evidence: Optional[BatchPboEvidence] = None
    pbo_evidence_hash: str = ""
    catalog_trial_count: int = Field(default=1, ge=1)
    catalog_trial_set_hash: str = ""
    verdict: ValidationVerdict
    reasons: tuple[str, ...] = ()
    as_of: int = Field(default=0, ge=0)
    reproducibility_hash: str = ""

    @model_validator(mode="after")
    def _validate_bound_pbo_fields(self) -> "AlphaEvaluation":
        if self.pbo_evidence is None:
            if self.pbo_evidence_hash:
                raise ValueError("PBO evidence hash cannot exist without retained evidence")
            return self
        if self.pbo_evidence_hash != self.pbo_evidence.evidence_hash:
            raise ValueError("PBO evidence hash mismatch")
        if not math.isclose(self.pbo, self.pbo_evidence.pbo, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("PBO scalar does not match retained evidence")
        if len(self.catalog_trial_set_hash) != 64:
            raise ValueError("bound PBO evaluation requires a catalog trial-set digest")
        return self


class AlphaCluster(BaseModel):
    """A group of mutually-correlated (redundant) alphas + one representative."""
    model_config = ConfigDict(frozen=True)
    cluster_id: str = Field(min_length=1)
    member_ids: tuple[str, ...]
    representative_id: str = Field(min_length=1)
    intra_corr: Optional[float] = None   # worst (min) pairwise |corr| within the cluster


class DecayReport(BaseModel):
    """Whether an alpha's predictive power has eroded (debounced over windows)."""
    model_config = ConfigDict(frozen=True)
    alpha_id: str = Field(min_length=1)
    ic_now: Optional[float] = None
    ic_baseline: Optional[float] = None
    ic_retention_ratio: Optional[float] = None
    half_life_time: Optional[float] = None
    half_life_horizon: Optional[float] = None
    ic_trend_slope: Optional[float] = None
    decayed: bool = False
    reasons: tuple[str, ...] = ()
    as_of: int = Field(default=0, ge=0)


class RetirementDecision(BaseModel):
    """Whether an alpha should be retired, with deterministically-ordered reasons."""
    model_config = ConfigDict(frozen=True)
    alpha_id: str = Field(min_length=1)
    retire: bool = False
    reasons: tuple[str, ...] = ()


class AlphaRanking(BaseModel):
    """One alpha's place in the robust, diversity-aware ranking. Fully
    explainable: the per-metric z-scores and the penalties are exposed."""
    model_config = ConfigDict(frozen=True)
    rank: int = Field(ge=1)
    alpha_id: str = Field(min_length=1)
    composite_score: float               # final: stability-weighted, penalized
    raw_score: float                     # pre-penalty weighted robust-z composite
    rank_stability: float = 1.0
    redundancy_penalty: float = 0.0
    crowding_penalty: float = 0.0
    z_components: dict[str, float] = Field(default_factory=dict)
    cluster_id: str = ""
    is_representative: bool = True
    reproducibility_hash: str = ""


# --- lifecycle state machine -------------------------------------------------

ALPHA_ALLOWED_TRANSITIONS: dict[AlphaState, frozenset[AlphaState]] = {
    AlphaState.DRAFT: frozenset({AlphaState.TESTING, AlphaState.REJECTED}),
    AlphaState.TESTING: frozenset({AlphaState.VALIDATED, AlphaState.REJECTED}),
    AlphaState.VALIDATED: frozenset({AlphaState.PAPER_TRADING, AlphaState.REJECTED}),
    # paper may deepen into SHADOW (dry-run), be approved (human), de-risk, or be flagged
    AlphaState.PAPER_TRADING: frozenset({AlphaState.SHADOW, AlphaState.APPROVED, AlphaState.REJECTED,
                                         AlphaState.RETIRED, AlphaState.PAUSED, AlphaState.DEGRADED}),
    AlphaState.SHADOW: frozenset({AlphaState.APPROVED, AlphaState.DEGRADED, AlphaState.PAUSED,
                                  AlphaState.RETIRED, AlphaState.REJECTED}),
    AlphaState.APPROVED: frozenset({AlphaState.DEGRADED, AlphaState.PAUSED, AlphaState.RETIRED}),
    # a degraded alpha that recovers re-enters dry-run OBSERVATION (re-approval is a fresh human gate),
    # or is paused / retired — it can never jump straight back to live
    AlphaState.DEGRADED: frozenset({AlphaState.SHADOW, AlphaState.PAUSED, AlphaState.RETIRED}),
    # a paused alpha resumes to observation or retires; a human may resume it to APPROVED
    AlphaState.PAUSED: frozenset({AlphaState.SHADOW, AlphaState.APPROVED, AlphaState.RETIRED}),
    AlphaState.RETIRED: frozenset(),
    AlphaState.REJECTED: frozenset(),
}

ALPHA_TERMINAL_STATES: frozenset[AlphaState] = frozenset({AlphaState.RETIRED, AlphaState.REJECTED})

# States a non-human actor may drive INTO. APPROVED is deliberately excluded: promotion to APPROVED is
# human-only. SHADOW / DEGRADED / PAUSED ARE agent-reachable — they are dry-run observation / de-risking
# states that grant NO live authority (the live gate is APPROVED), so an agent moving an alpha into them
# can never grant it live capability.
AGENT_REACHABLE: frozenset[AlphaState] = frozenset({
    AlphaState.TESTING, AlphaState.VALIDATED, AlphaState.PAPER_TRADING, AlphaState.SHADOW,
    AlphaState.DEGRADED, AlphaState.PAUSED, AlphaState.REJECTED, AlphaState.RETIRED,
})


class InvalidAlphaTransition(ValueError):
    def __init__(self, src: AlphaState, dst: AlphaState):
        super().__init__(f"illegal alpha transition: {src.value} -> {dst.value}")
        self.src, self.dst = src, dst


class UnauthorizedPromotion(PermissionError):
    def __init__(self, actor: "Actor", dst: AlphaState):
        super().__init__(f"actor kind {actor.kind.value} may not drive transition to {dst.value}")
        self.actor, self.dst = actor, dst


def can_alpha_transition(src: AlphaState, dst: AlphaState) -> bool:
    return dst in ALPHA_ALLOWED_TRANSITIONS.get(src, frozenset())


def assert_alpha_transition(src: AlphaState, dst: AlphaState, actor: Actor) -> None:
    """Legality first, then the human-only authorization for non-AGENT_REACHABLE
    targets (i.e. APPROVED). Evidence/approval-token verification happens in the
    lifecycle guards; this is the structural first lock."""
    if not can_alpha_transition(src, dst):
        raise InvalidAlphaTransition(src, dst)
    if not actor.is_human and dst not in AGENT_REACHABLE:
        raise UnauthorizedPromotion(actor, dst)


def is_alpha_terminal(state: AlphaState) -> bool:
    return state in ALPHA_TERMINAL_STATES


class AlphaTransition(BaseModel):
    """Immutable, hash-chained record of one lifecycle transition (audit)."""
    model_config = ConfigDict(frozen=True)
    transition_id: str = Field(min_length=1)
    alpha_id: str = Field(min_length=1)
    src_state: AlphaState
    dst_state: AlphaState
    actor: Actor
    evidence_hash: str = ""
    approval_id: str = ""
    dataset_version: str = ""
    timestamp: int = Field(ge=0)
    prev_hash: str = ""

    @property
    def entry_hash(self) -> str:
        return stable_hash({
            "transition_id": self.transition_id, "alpha_id": self.alpha_id,
            "src": self.src_state.value, "dst": self.dst_state.value,
            "actor": self.actor.actor_id, "actor_kind": self.actor.kind.value,
            "evidence_hash": self.evidence_hash, "approval_id": self.approval_id,
            "dataset_version": self.dataset_version, "timestamp": self.timestamp,
            "prev_hash": self.prev_hash,
        })
