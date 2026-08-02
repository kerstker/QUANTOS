"""Durable composition for evaluating an already-enumerated alpha pool."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.alpha.evaluator import AlphaEvaluator
from services.research import alpha_expression as ax
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.research_budget import (
    BudgetExhausted,
    ResearchBudget,
    check_budget,
)
from services.research.trial_provenance import (
    ActorType,
    TrialProvenance,
    TrialStatus,
    trial_id_for_attempt,
)

from .agent_loop import ResearchAgentLoop


def _required_text(name: str, value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _dsl_identity(params: Mapping[str, str]) -> tuple[str, str]:
    """(canonical_expression, expression_hash) from a candidate's DSL param.

    Fail-soft and HONEST: a candidate whose params carry no parseable tuple-DSL
    `expr` records ("", "") — the batch evaluates pools from many drivers and
    never fabricates an expression identity it cannot derive.
    """
    serialized = params.get("expr")
    if not isinstance(serialized, str) or not serialized:
        return "", ""
    try:
        expr = ax.deserialize(serialized)
    except Exception:
        return "", ""
    return ax.serialize(ax.canonicalize(expr)), ax.alpha_id(expr)


def _record_batch_provenance(
    registry: ExperimentRegistry,
    *,
    pool: tuple[tuple, ...],
    search_id: str,
    search_source: str,
    dataset_version: str,
    recorded_at: int,
    hypotheses: Mapping[str, str],
    code_hash: str,
) -> None:
    """Write the 727 §7.1 TrialRecord for every pre-registered candidate.

    RECORDED is the only status the batch can attest honestly: elimination
    happens later inside the evaluator/Judge and is not re-attributed here.
    model_name stays "" — this entry EVALUATES a pool enumerated by an external
    driver; claiming a generator would be fabrication. Idempotent on trial_id.
    """
    record_provenance = getattr(registry, "record_trial_provenance", None)
    if record_provenance is None:
        return
    for definition, _periods in pool:
        canonical_expression, expression_hash = _dsl_identity(definition.params)
        record_provenance(
            TrialProvenance(
                trial_id=trial_id_for_attempt(
                    search_id, definition.definition_hash
                ),
                research_session_id=search_id,
                config_hash=definition.definition_hash,
                actor_id=search_source,
                actor_type=ActorType.AUTOMATED_SEARCH,
                status=TrialStatus.RECORDED,
                started_at=recorded_at,
                completed_at=recorded_at,
                hypothesis_id=hypotheses.get(definition.alpha_id, ""),
                factor_ids=definition.factor_ids,
                alpha_expression=str(definition.params.get("expr", "")),
                canonical_expression=canonical_expression,
                expression_hash=expression_hash,
                parameter_set=dict(definition.params),
                parameter_hash=stable_hash(dict(definition.params)),
                dataset_versions=(dataset_version,),
                code_hash=code_hash,
            )
        )


def _materialize_candidates(candidates: Sequence[tuple]) -> tuple[tuple, ...]:
    try:
        materialized = tuple(candidates)
    except TypeError as exc:
        raise TypeError("candidates must be an iterable candidate pool") from exc
    if not materialized:
        raise ValueError("candidates must contain at least one alpha definition")

    normalized: list[tuple] = []
    alpha_ids: set[str] = set()
    for item in materialized:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise TypeError("each candidate must be an (AlphaDefinition, periods) pair")
        definition, periods = item
        if not isinstance(definition, AlphaDefinition):
            raise TypeError("candidate definition must be an AlphaDefinition")
        if definition.alpha_id in alpha_ids:
            raise ValueError(f"duplicate alpha_id in candidate pool: {definition.alpha_id}")
        alpha_ids.add(definition.alpha_id)
        try:
            normalized_periods = tuple(periods)
        except TypeError as exc:
            raise TypeError("candidate periods must be iterable") from exc
        normalized.append((definition, normalized_periods))
    return tuple(normalized)


def run_registered_candidate_batch(
    candidates: Sequence[tuple],
    *,
    registry: ExperimentRegistry,
    search_id: str,
    search_kind: str,
    search_source: str,
    dataset_version: str,
    recorded_at: int,
    stale_search_timeout_ms: int | None = None,
    periods_per_year: int = 252,
    config: AlphaFactoryConfig | None = None,
    evidence: Mapping[str, Mapping[str, Any]] | None = None,
    audit_log=None,
    budget: ResearchBudget | None = None,
    parent_hypothesis: str | None = None,
    search_space: str | None = None,
    data_source: str | None = None,
    uses_locked_oos: bool | None = None,
    hypothesis_by_candidate: Mapping[str, str] | None = None,
    code_hash: str = "",
) -> dict:
    """Pre-register a complete pool, then evaluate it through the agent loop.

    Candidate hashes enter both the append-only search ledger and the global
    trial population in one transaction before the catalog or evaluator can
    observe the batch. Caller-supplied time keeps the composition deterministic.

    W10 (spec §14.1/§14.2): when ``budget`` is provided, the batch FIRST checks
    the declared ResearchBudget and refuses with ``BudgetExhausted`` once
    exhausted — before a single byte enters the durable ledger (no STARTED
    event, no candidates, no trials). ``parent_hypothesis`` and ``search_space``
    are then REQUIRED (every experiment records them). Usage semantics:
    ``trials_used`` = this batch's declared family size (the pre-registration
    contract bounds the enumerated family, not incremental novelty — growing
    the family past the declaration is a NEW experiment and must raise the
    budget deliberately); ``feature_count`` / ``parameter_dimensions`` = the
    max cardinality over the pool's factor_ids / params; compute hours are NOT
    metered here, so a declared ``max_compute_hours`` is refused fail-closed as
    ``unverified_dimension``. ``uses_locked_oos`` / ``data_source`` are caller
    attestations checked against ``locked_oos_period`` / ``allowed_data_sources``.
    When ``budget`` is None the remaining budget kwargs must be None as well,
    and behavior is byte-identical to the pre-budget composition.

    P5-1 (727 §7.1): every pre-registered candidate ALSO gets a TrialRecord via
    ``record_trial_provenance`` at registration time. ``hypothesis_by_candidate``
    (optional, keyed by alpha_id) binds candidates to their pre-registered §8.1
    Hypothesis ids; absent entries record ``hypothesis_id=""`` honestly — no
    hypothesis is fabricated for a legacy caller. ``code_hash`` is the
    caller-supplied code version pin (default "" = UNPINNED).
    """

    if not isinstance(registry, ExperimentRegistry):
        raise TypeError("registry must be an ExperimentRegistry")
    search_id = _required_text("search_id", search_id)
    search_kind = _required_text("search_kind", search_kind)
    search_source = _required_text("search_source", search_source)
    dataset_version = _required_text("dataset_version", dataset_version)
    if isinstance(recorded_at, bool) or not isinstance(recorded_at, int):
        raise TypeError("recorded_at must be an integer")
    if recorded_at < 0:
        raise ValueError("recorded_at must be non-negative")
    if stale_search_timeout_ms is not None:
        raise ValueError(
            "automatic stale recovery is disabled; use the explicit registry recovery API"
        )
    if config is not None and not isinstance(config, AlphaFactoryConfig):
        raise TypeError("config must be an AlphaFactoryConfig or None")
    if (
        isinstance(periods_per_year, bool)
        or not isinstance(periods_per_year, int)
        or periods_per_year <= 0
    ):
        raise ValueError("periods_per_year must be a positive integer")
    if evidence is not None and not isinstance(evidence, Mapping):
        raise TypeError("evidence must be a mapping or None")
    if hypothesis_by_candidate is not None and not isinstance(
        hypothesis_by_candidate, Mapping
    ):
        raise TypeError("hypothesis_by_candidate must be a mapping or None")
    if not isinstance(code_hash, str):
        raise TypeError("code_hash must be a string")
    if budget is None:
        if (
            parent_hypothesis is not None
            or search_space is not None
            or data_source is not None
            or uses_locked_oos is not None
        ):
            raise ValueError(
                "parent_hypothesis/search_space/data_source/uses_locked_oos "
                "require budget="
            )
    else:
        if not isinstance(budget, ResearchBudget):
            raise TypeError("budget must be a ResearchBudget or None")
        parent_hypothesis = _required_text("parent_hypothesis", parent_hypothesis)
        search_space = _required_text("search_space", search_space)
        if uses_locked_oos is not None and not isinstance(uses_locked_oos, bool):
            raise TypeError("uses_locked_oos must be a bool or None")

    pool = _materialize_candidates(candidates)

    budget_declaration: dict | None = None
    if budget is not None:
        usage = {
            "trials_used": len(pool),
            # compute_hours_used deliberately absent: the registrar meters no
            # wall clock, so a declared compute budget fails closed as
            # unverified_dimension (only declare verifiable dimensions).
            "feature_count": max(len(definition.factor_ids) for definition, _ in pool),
            "parameter_dimensions": max(len(definition.params) for definition, _ in pool),
        }
        budget_check = check_budget(
            budget,
            usage,
            now_known_oos=uses_locked_oos,
            data_source=data_source,
        )
        if budget_check.exhausted:
            raise BudgetExhausted(budget_check)
        budget_declaration = {
            "budget": budget.metadata(),
            "parent_hypothesis": parent_hypothesis,
            "search_space": search_space,
            "data_source": data_source,
            "uses_locked_oos": uses_locked_oos,
            "usage": dict(usage),
            "check": budget_check.to_dict(),
        }

    definitions_by_alpha_id = {
        definition.alpha_id: definition for definition, _periods in pool
    }
    config_hashes = tuple(
        definition.definition_hash for definition, _periods in pool
    )
    recovered_search_ids: tuple[str, ...] = ()
    registry.begin_search_attempt(
        search_id=search_id,
        search_kind=search_kind,
        source=search_source,
        dataset_version=dataset_version,
        recorded_at=recorded_at,
    )
    try:
        registry.register_search_candidates(
            search_id,
            config_hashes,
            registered_at=recorded_at,
        )
        # 727 §7.1: one TrialRecord per pre-registered candidate, written at
        # registration time (same semantics as the search ledger rows above).
        _record_batch_provenance(
            registry,
            pool=pool,
            search_id=search_id,
            search_source=search_source,
            dataset_version=dataset_version,
            recorded_at=recorded_at,
            hypotheses=(
                dict(hypothesis_by_candidate)
                if hypothesis_by_candidate
                else {}
            ),
            code_hash=code_hash,
        )

        # Load only after registration so the first evaluation is deflated by
        # the complete current pool, not merely the candidates seen so far.
        catalog = AlphaCatalog()
        registry.load_for_evaluation(catalog)
        evaluator = AlphaEvaluator(
            catalog,
            config,
            periods_per_year=periods_per_year,
        )
        loop = ResearchAgentLoop(
            catalog=catalog,
            config=config,
            evaluator=evaluator,
        )
        result = loop.run(
            pool,
            timestamp=recorded_at,
            evidence=None if evidence is None else dict(evidence),
        )

        recorded_run_ids: list[str] = []
        report = result.get("report")
        if not isinstance(report, dict):
            raise ValueError("ResearchAgentLoop returned an invalid report")
        for alpha_id, entry in sorted(report.items()):
            if not isinstance(entry, dict):
                raise ValueError("ResearchAgentLoop returned an invalid report entry")
            evaluation = entry.get("evaluation")
            if evaluation is not None:
                definition = definitions_by_alpha_id.get(alpha_id)
                if (
                    definition is None
                    or getattr(evaluation, "alpha_id", None) != alpha_id
                ):
                    raise ValueError(
                        "ResearchAgentLoop evaluation is not bound to an exact "
                        "registered candidate definition"
                    )
                recorded_run_ids.append(
                    registry.record_evaluation(
                        evaluation,
                        dataset_version=dataset_version,
                        recorded_ts=recorded_at,
                        search_id=search_id,
                        config_hash=definition.definition_hash,
                        audit_log=audit_log,
                    )
                )
        registry.save_evaluation_context(catalog, now=recorded_at)
        registry.finish_search_attempt(
            search_id,
            status="COMPLETED",
            recorded_at=recorded_at,
        )
        completed = {
            **result,
            "recorded_run_ids": recorded_run_ids,
            "registered_candidate_count": len(set(config_hashes)),
            "recovered_search_ids": recovered_search_ids,
        }
        if budget_declaration is not None:
            # Additive run-artifact fields (spec §14.1): budget metadata +
            # parent_hypothesis + search_space, recorded only when declared.
            completed["budget_declaration"] = budget_declaration
        return completed
    except BaseException as original:
        try:
            registry.finish_search_attempt(
                search_id,
                status="FAILED",
                recorded_at=recorded_at,
            )
        except BaseException as terminal_error:
            original.add_note(
                "failed to persist FAILED search terminal: "
                f"{type(terminal_error).__name__}: {terminal_error}"
            )
        raise


__all__ = ["run_registered_candidate_batch"]
