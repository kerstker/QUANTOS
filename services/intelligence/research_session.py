"""Guardrailed intelligence composition for the deterministic research session core."""
from __future__ import annotations

from services.intelligence.agent_loop import ResearchAgentLoop
from services.intelligence.evolutionary_search import (
    EvolutionarySearchLoop,
    make_evolution_provider,
)
from services.intelligence.guided_search import GuidedSearchLoop
from services.intelligence.mcts_search import MCTSAlphaSearch
from services.intelligence.search_space import CombinedHypothesisSpace
from services.data.research_adapter import adapt_research_market_data
from services.research.session import (
    ResearchSearchRuntime,
    _inner_loop as _inner_loop_core,
    run_research_session as _run_research_session_core,
)
from services.research.ports.persistence import TrialPersistenceError


_SEARCH_RUNTIME = ResearchSearchRuntime(
    agent_loop=ResearchAgentLoop,
    guided_search_loop=GuidedSearchLoop,
    combined_hypothesis_space=CombinedHypothesisSpace,
    evolutionary_search_loop=EvolutionarySearchLoop,
    make_evolution_provider=make_evolution_provider,
    mcts_alpha_search=MCTSAlphaSearch,
)


def _inner_loop(catalog, regime_evidence: bool, fwd_horizon: int = 1):
    """Compatibility test seam for the fixed guardrailed agent-loop composition."""
    return _inner_loop_core(
        catalog,
        regime_evidence,
        fwd_horizon,
        search_runtime=_SEARCH_RUNTIME,
    )


def run_research_session(*args, **kwargs):
    """Run the research core with the fixed, guardrailed intelligence runtime."""
    if "search_runtime" in kwargs:
        raise TypeError("search_runtime is fixed by the intelligence composition boundary")
    ephemeral = kwargs.pop("ephemeral", False)
    registry = kwargs.get("registry")
    if registry is None and not ephemeral:
        raise ValueError(
            "registry is required unless test-only ephemeral=True is explicit"
        )
    if registry is not None and ephemeral:
        raise ValueError("ephemeral=True cannot be combined with registry")
    search_id = kwargs.pop("search_id", None)
    search_kind = kwargs.pop("search_kind", None)
    search_source = kwargs.pop("search_source", None)
    if registry is not None and not all(
        isinstance(value, str) and value.strip()
        for value in (
            search_id,
            search_kind,
            search_source,
        )
    ):
        raise ValueError(
            "persistent research session requires search_id, search_kind, "
            "and search_source"
        )
    if args:
        args = (adapt_research_market_data(args[0]), *args[1:])
    elif "store" in kwargs:
        kwargs = {
            **kwargs,
            "store": adapt_research_market_data(kwargs["store"]),
        }
    if registry is None:
        return _run_research_session_core(
            *args, search_runtime=_SEARCH_RUNTIME, **kwargs
        )

    timestamp = kwargs.get("timestamp", 0)
    dataset_version = kwargs.get("dataset_version")
    if dataset_version is None:
        adapted_store = args[0] if args else kwargs["store"]
        instruments = args[1] if len(args) > 1 else kwargs["instruments"]
        dataset_version = adapted_store.dataset_version(
            instruments,
            kwargs["event_times"],
        )
        kwargs["dataset_version"] = dataset_version
    registry.begin_search_attempt(
        search_id=search_id,
        search_kind=search_kind,
        source=search_source,
        dataset_version=dataset_version,
        recorded_at=timestamp,
    )

    def persist_candidate(config_hash: str) -> None:
        try:
            registry.register_search_candidates(
                search_id,
                [config_hash],
                registered_at=timestamp,
            )
        except Exception as exc:
            raise TrialPersistenceError(
                f"candidate persistence unavailable: {exc}"
            ) from exc

    try:
        result = _run_research_session_core(
            *args,
            search_runtime=_SEARCH_RUNTIME,
            trial_recorder=persist_candidate,
            search_id=search_id,
            **kwargs,
        )
        registry.finish_search_attempt(
            search_id,
            status="COMPLETED",
            recorded_at=timestamp,
        )
        return result
    except BaseException as original:
        try:
            registry.finish_search_attempt(
                search_id,
                status="FAILED",
                recorded_at=timestamp,
            )
        except BaseException as terminal_error:
            original.add_note(
                f"failed to persist FAILED search terminal: {terminal_error}"
            )
        raise
