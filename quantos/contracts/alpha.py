"""Alpha Factory contracts (blueprint §04 / Phase 6).

Entities + the alpha LIFECYCLE state machine (mirrors order.py). The state
machine encodes a hard safety property: only a HUMAN actor can drive an alpha to
APPROVED; agents/automation may advance up to PAPER_TRADING and may de-risk to
RETIRED, but can never self-approve (critique P0-3 fix — the actor carries a
verifiable kind).
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from typing import Optional

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
    observed_sr: float = 0.0
    turnover: float = Field(default=0.0, ge=0)
    deflated_sharpe: float = Field(default=0.0, ge=0, le=1)
    pbo: float = Field(default=0.0, ge=0, le=1)
    catalog_trial_count: int = Field(default=1, ge=1)
    verdict: ValidationVerdict
    reasons: tuple[str, ...] = ()
    as_of: int = Field(default=0, ge=0)
    reproducibility_hash: str = ""


class AlphaCluster(BaseModel):
    """A group of mutually-correlated (redundant) alphas + one representative."""
    model_config = ConfigDict(frozen=True)
    cluster_id: str = Field(min_length=1)
    member_ids: tuple[str, ...]
    representative_id: str = Field(min_length=1)
    intra_corr: Optional[float] = None   # worst (min) pairwise |corr| within the cluster


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
    AlphaState.PAPER_TRADING: frozenset({AlphaState.APPROVED, AlphaState.REJECTED, AlphaState.RETIRED}),
    AlphaState.APPROVED: frozenset({AlphaState.RETIRED}),
    AlphaState.RETIRED: frozenset(),
    AlphaState.REJECTED: frozenset(),
}

ALPHA_TERMINAL_STATES: frozenset[AlphaState] = frozenset({AlphaState.RETIRED, AlphaState.REJECTED})

# States a non-human actor may drive INTO. APPROVED is deliberately excluded:
# promotion to APPROVED is human-only.
AGENT_REACHABLE: frozenset[AlphaState] = frozenset({
    AlphaState.TESTING, AlphaState.VALIDATED, AlphaState.PAPER_TRADING,
    AlphaState.REJECTED, AlphaState.RETIRED,
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
