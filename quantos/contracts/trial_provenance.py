"""Global trial provenance (727 §7.1) — the TrialRecord that makes multiple
testing auditable.

The trial LEDGER already exists and is strong: `experiment_registry/store.py`
owns 13 append-only tables with three independent hash chains, and
`tools/search_trial_ledger_history.py` retains verified snapshots. What was
missing is the RECORD's own provenance — the `trials` table carries only
(config_hash, first_seen_ts), so a config's actor, model, prompt, hypothesis,
canonical expression, parameter set, code/policy versions, elimination reason,
and — critically — how many OOS boundaries its author had already looked at were
all unrecorded.

This module is the pure domain half: a frozen `TrialProvenance` value with a
canonical payload and a hash-chain entry hash. No I/O, no wall clock, no RNG —
every timestamp and identifier is caller-supplied, so a replay is bit-identical.

Honest boundary: recording provenance does NOT by itself deflate anything. It
makes the deflation AUDITABLE (§7.2 — the DSR/PBO/FDR trial count must come from
the authoritative ledger, and `viewed_oos_boundaries` is precisely the number the
doc forbids leaving unrecorded).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from quantos.contracts.hashing import stable_hash


class ActorType(str, Enum):
    """Who produced this trial. Kept separate from `actor_id` so a non-human
    actor can never be laundered into a human one by string formatting."""

    HUMAN = "HUMAN"
    AI_AGENT = "AI_AGENT"
    AUTOMATED_SEARCH = "AUTOMATED_SEARCH"


class TrialStatus(str, Enum):
    """Terminal disposition of a trial.

    The ELIMINATED_* members are the 727 §8.3 funnel stages: "每一层淘汰都必须
    写明原因" — a candidate that dies must record WHICH layer killed it, so a
    later audit can tell a semantic rejection from a post-cost failure from an
    overfitting rejection. RECORDED means registered but not yet judged.
    """

    RECORDED = "RECORDED"
    ELIMINATED_SEMANTIC = "ELIMINATED_SEMANTIC"
    ELIMINATED_DUPLICATE = "ELIMINATED_DUPLICATE"
    ELIMINATED_DATA_UNAVAILABLE = "ELIMINATED_DATA_UNAVAILABLE"
    ELIMINATED_PIT_LAG = "ELIMINATED_PIT_LAG"
    ELIMINATED_PRESCREEN = "ELIMINATED_PRESCREEN"
    ELIMINATED_PRECOST_UNSTABLE = "ELIMINATED_PRECOST_UNSTABLE"
    ELIMINATED_POSTCOST = "ELIMINATED_POSTCOST"
    ELIMINATED_WALKFORWARD = "ELIMINATED_WALKFORWARD"
    ELIMINATED_MULTIPLE_TESTING = "ELIMINATED_MULTIPLE_TESTING"
    ELIMINATED_PARAM_PERTURBATION = "ELIMINATED_PARAM_PERTURBATION"
    ELIMINATED_REGIME = "ELIMINATED_REGIME"
    ELIMINATED_CAPACITY = "ELIMINATED_CAPACITY"
    ELIMINATED_CHALLENGE = "ELIMINATED_CHALLENGE"
    PASSED_TO_CATALOG = "PASSED_TO_CATALOG"
    FAILED_ERROR = "FAILED_ERROR"


_TERMINAL_FAILURES = frozenset(
    status for status in TrialStatus if status.value.startswith("ELIMINATED_")
) | {TrialStatus.FAILED_ERROR}

_MAX_TEXT = 512


def _text(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if len(value) > _MAX_TEXT:
        raise ValueError(f"{field} exceeds {_MAX_TEXT} characters")
    if value != value.strip():
        raise ValueError(f"{field} must not have surrounding whitespace")
    if not value and not allow_empty:
        raise ValueError(f"{field} must not be empty")
    return value


def _time(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _tuple_of_text(value: object, field: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be a sequence of strings")
    return tuple(_text(item, f"{field}[]") for item in value)


def _sorted_unique(value: object, field: str) -> tuple[str, ...]:
    """Canonical ordering for set-like fields so the entry hash is stable
    regardless of the caller's iteration order."""
    items = _tuple_of_text(value, field)
    return tuple(sorted(set(items)))


def _mapping_of_text(value: object, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return {
        _text(key, f"{field} key"): _text(item, f"{field}[{key}]", allow_empty=True)
        for key, item in value.items()
    }


def _mapping_of_number(value: object, field: str) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    out: dict[str, float] = {}
    for key, item in value.items():
        name = _text(key, f"{field} key")
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"{field}[{name}] must be a real number")
        number = float(item)
        # NaN/Inf would make the canonical JSON non-round-trippable and the
        # entry hash unverifiable after a reload.
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError(f"{field}[{name}] must be finite")
        out[name] = number
    return out


@dataclass(frozen=True)
class TrialProvenance:
    """One immutable TrialRecord (727 §7.1).

    Identity is `trial_id`. `config_hash` is the JOIN KEY back into the existing
    `trials` table — deliberately NOT the identity, because §7.2 requires every
    ATTEMPT to be ledgered ("重复候选也必须记账", "参数扫描的每个配置都计数")
    while `trials` de-duplicates by config for the trial COUNT. Two attempts at
    the same config therefore produce two provenance rows joined to one trial row,
    which is the honest reading of both rules.
    """

    trial_id: str
    research_session_id: str
    config_hash: str
    actor_id: str
    actor_type: ActorType
    status: TrialStatus
    started_at: int
    completed_at: int
    # --- lineage -----------------------------------------------------------
    parent_trial_id: str = ""
    hypothesis_id: str = ""
    # --- generator provenance ----------------------------------------------
    model_name: str = ""
    model_version: str = ""
    prompt_hash: str = ""
    # --- what was actually tried -------------------------------------------
    factor_ids: tuple[str, ...] = ()
    alpha_expression: str = ""
    canonical_expression: str = ""
    expression_hash: str = ""
    parameter_set: Mapping[str, str] = None  # type: ignore[assignment]
    parameter_hash: str = ""
    # --- reproducibility pins ----------------------------------------------
    dataset_versions: tuple[str, ...] = ()
    universe_version: str = ""
    cost_model_version: str = ""
    validation_policy_version: str = ""
    code_hash: str = ""
    # --- outcome -----------------------------------------------------------
    metrics: Mapping[str, float] = None  # type: ignore[assignment]
    failure_reasons: tuple[str, ...] = ()
    artifact_paths: tuple[str, ...] = ()
    artifact_hashes: tuple[str, ...] = ()
    # --- the number the doc forbids leaving unrecorded (§7.2) ---------------
    viewed_oos_boundaries: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "trial_id", _text(self.trial_id, "trial_id"))
        object.__setattr__(
            self,
            "research_session_id",
            _text(self.research_session_id, "research_session_id"),
        )
        object.__setattr__(self, "config_hash", _text(self.config_hash, "config_hash"))
        object.__setattr__(self, "actor_id", _text(self.actor_id, "actor_id"))
        if not isinstance(self.actor_type, ActorType):
            raise ValueError("actor_type must be an ActorType")
        if not isinstance(self.status, TrialStatus):
            raise ValueError("status must be a TrialStatus")
        object.__setattr__(self, "started_at", _time(self.started_at, "started_at"))
        object.__setattr__(
            self, "completed_at", _time(self.completed_at, "completed_at")
        )
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")

        for field in (
            "parent_trial_id",
            "hypothesis_id",
            "model_name",
            "model_version",
            "prompt_hash",
            "alpha_expression",
            "canonical_expression",
            "expression_hash",
            "parameter_hash",
            "universe_version",
            "cost_model_version",
            "validation_policy_version",
            "code_hash",
        ):
            object.__setattr__(
                self, field, _text(getattr(self, field), field, allow_empty=True)
            )
        if self.parent_trial_id == self.trial_id:
            raise ValueError("parent_trial_id must not be the trial's own id")

        object.__setattr__(self, "factor_ids", _sorted_unique(self.factor_ids, "factor_ids"))
        object.__setattr__(
            self, "dataset_versions", _sorted_unique(self.dataset_versions, "dataset_versions")
        )
        object.__setattr__(
            self,
            "viewed_oos_boundaries",
            _sorted_unique(self.viewed_oos_boundaries, "viewed_oos_boundaries"),
        )
        # failure_reasons stays ORDERED (the order the funnel produced them is
        # information); it is only de-duplicated by the caller if it wants to be.
        object.__setattr__(
            self, "failure_reasons", _tuple_of_text(self.failure_reasons, "failure_reasons")
        )
        object.__setattr__(
            self, "artifact_paths", _tuple_of_text(self.artifact_paths, "artifact_paths")
        )
        object.__setattr__(
            self, "artifact_hashes", _tuple_of_text(self.artifact_hashes, "artifact_hashes")
        )
        if len(self.artifact_paths) != len(self.artifact_hashes):
            raise ValueError("artifact_paths and artifact_hashes must be 1:1")

        object.__setattr__(
            self,
            "parameter_set",
            _mapping_of_text({} if self.parameter_set is None else self.parameter_set, "parameter_set"),
        )
        object.__setattr__(
            self,
            "metrics",
            _mapping_of_number({} if self.metrics is None else self.metrics, "metrics"),
        )

        # Fail-closed honesty: a trial that died must say why. Without this a
        # caller could record ELIMINATED_POSTCOST with no reason and the audit
        # trail would show an elimination nobody can review (§7.2, §22.11).
        if self.status in _TERMINAL_FAILURES and not self.failure_reasons:
            raise ValueError(f"status {self.status.value} requires failure_reasons")

    @property
    def eliminated(self) -> bool:
        return self.status in _TERMINAL_FAILURES

    @property
    def oos_peek_count(self) -> int:
        """How many DISTINCT out-of-sample boundaries this trial's author had
        already looked at. §0.4.7 forbids calling repeatedly-inspected OOS data
        "unseen"; this is the count that makes that violation visible."""
        return len(self.viewed_oos_boundaries)

    def canonical_payload(self) -> dict[str, Any]:
        """Deterministic dict for hashing and durable storage. Enum members are
        reduced to their values so a reload reproduces the same hash."""
        return {
            "actor_id": self.actor_id,
            "actor_type": self.actor_type.value,
            "alpha_expression": self.alpha_expression,
            "artifact_hashes": list(self.artifact_hashes),
            "artifact_paths": list(self.artifact_paths),
            "canonical_expression": self.canonical_expression,
            "code_hash": self.code_hash,
            "completed_at": self.completed_at,
            "config_hash": self.config_hash,
            "cost_model_version": self.cost_model_version,
            "dataset_versions": list(self.dataset_versions),
            "expression_hash": self.expression_hash,
            "factor_ids": list(self.factor_ids),
            "failure_reasons": list(self.failure_reasons),
            "hypothesis_id": self.hypothesis_id,
            "metrics": dict(self.metrics),
            "model_name": self.model_name,
            "model_version": self.model_version,
            "parameter_hash": self.parameter_hash,
            "parameter_set": dict(self.parameter_set),
            "parent_trial_id": self.parent_trial_id,
            "prompt_hash": self.prompt_hash,
            "research_session_id": self.research_session_id,
            "started_at": self.started_at,
            "status": self.status.value,
            "trial_id": self.trial_id,
            "universe_version": self.universe_version,
            "validation_policy_version": self.validation_policy_version,
            "viewed_oos_boundaries": list(self.viewed_oos_boundaries),
        }

    def entry_hash(self, prev_hash: str) -> str:
        """This record's link in the trial-provenance hash chain.

        `prev_hash` is the previous entry's hash ("" for the first), so any
        retroactive edit or deletion of an earlier record invalidates every
        later one — the same tamper-evidence the other four ledger chains use.
        """
        if not isinstance(prev_hash, str):
            raise ValueError("prev_hash must be a string")
        return stable_hash({"entry": self.canonical_payload(), "prev_hash": prev_hash})


def chain_head(records: Sequence[TrialProvenance]) -> str:
    """Recompute the chain head from scratch; the verify half of the chain."""
    head = ""
    for record in records:
        head = record.entry_hash(head)
    return head


def trial_id_for_attempt(research_session_id: str, config_hash: str) -> str:
    """Deterministic attempt id for the canonical search entries.

    One attempt = one (session, config) pair: a search session registers each
    distinct config at most once, so this id is unique per recorded attempt and
    stable on replay — no uuid, no wall clock. Two DIFFERENT sessions attempting
    the same config still produce two rows joined to one trial, exactly as §7.2
    requires ("重复候选也必须记账").
    """
    if not research_session_id or not config_hash:
        raise ValueError("research_session_id and config_hash must be non-empty")
    return f"{research_session_id}:{config_hash}"


def trial_provenance_for_config(
    config_hash: str,
    *,
    research_session_id: str,
    actor_id: str,
    dataset_version: str = "",
    recorded_at: int = 0,
    actor_type: ActorType = ActorType.AUTOMATED_SEARCH,
    status: TrialStatus = TrialStatus.RECORDED,
    factor_ids: Sequence[str] = (),
    parameter_set: Mapping[str, str] | None = None,
    universe_version: str = "",
    hypothesis_id: str = "",
    model_name: str = "",
    code_hash: str = "",
) -> TrialProvenance:
    """Build the honest minimum ``TrialRecord`` for a known config hash.

    Some research drivers own a richer candidate object while others only
    expose the canonical config hash.  The latter must still ledger the
    attempt, but this helper deliberately leaves unavailable fields empty
    instead of guessing an expression, model, or hypothesis.  Callers that do
    have a definition should prefer :func:`trial_provenance_for_definition`.
    All identity, time, and version values remain caller supplied.
    """
    if parameter_set is None:
        parameter_set = {}
    if not isinstance(parameter_set, Mapping):
        raise ValueError("parameter_set must be a mapping")
    normalized_parameters: dict[str, str] = {}
    for key, value in parameter_set.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("parameter_set keys and values must be strings")
        normalized_parameters[key] = value
    return TrialProvenance(
        trial_id=trial_id_for_attempt(research_session_id, config_hash),
        research_session_id=research_session_id,
        config_hash=config_hash,
        actor_id=actor_id,
        actor_type=actor_type,
        status=status,
        started_at=recorded_at,
        completed_at=recorded_at,
        factor_ids=tuple(factor_ids),
        parameter_set=normalized_parameters,
        parameter_hash=stable_hash(normalized_parameters),
        dataset_versions=(dataset_version,) if dataset_version else (),
        universe_version=universe_version,
        hypothesis_id=hypothesis_id,
        model_name=model_name,
        code_hash=code_hash,
    )


def trial_provenance_for_definition(
    definition: Any,
    *,
    research_session_id: str,
    actor_id: str,
    dataset_version: str = "",
    recorded_at: int = 0,
    actor_type: ActorType = ActorType.AUTOMATED_SEARCH,
    status: TrialStatus = TrialStatus.RECORDED,
    hypothesis_id: str = "",
    model_name: str = "",
    code_hash: str = "",
) -> TrialProvenance:
    """Copy only auditable fields from an ``AlphaDefinition``-like value.

    The helper intentionally does not infer a DSL expression from arbitrary
    params.  A caller with a real expression can add it explicitly through a
    richer ``TrialProvenance`` construction; this generic session path records
    the definition hash, factors, universe, and exact string parameters.
    """
    try:
        config_hash = definition.definition_hash
        factor_ids = tuple(definition.factor_ids)
        universe_version = definition.universe
        parameter_set = dict(definition.params)
    except AttributeError as exc:
        raise ValueError("definition must expose definition_hash, factor_ids, universe, and params") from exc
    return trial_provenance_for_config(
        config_hash,
        research_session_id=research_session_id,
        actor_id=actor_id,
        dataset_version=dataset_version,
        recorded_at=recorded_at,
        actor_type=actor_type,
        status=status,
        factor_ids=factor_ids,
        parameter_set=parameter_set,
        universe_version=universe_version,
        hypothesis_id=hypothesis_id,
        model_name=model_name,
        code_hash=code_hash,
    )
