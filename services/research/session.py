"""Reproducible research session orchestrator (#2 remainder).

One deterministic call that composes the integrated subsystems: derive a
dataset_version from the persisted bars, optionally rehydrate the catalog from the
experiment registry (cross-session multiple-testing state), run the guided search
on REAL persisted-data PanelPeriods through the UNCHANGED guardrailed path (the
agent still can never reach APPROVED), record each evaluation as an immutable
registry artifact keyed by dataset_version, and persist the evolved catalog.

Composes existing entry points; changes none of them. Pure-Python, deterministic,
fail-closed, no new authority. (validation/pipeline.py is a separate single-
instrument validation tool, not consolidated here.)"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.alpha.factor_set import select_synergistic_set
from services.validation.selection_authority import (
    LEGACY_INVALIDATION_CODE,
    require_diagnostic_selection_seam,
)

from .fundamental_factors import make_combined_periods_provider
from .panel_builder import make_data_periods_provider, universe_data_gaps
from .ports.market_data import VersionedMarketDataPort


@dataclass(frozen=True)
class ResearchSearchRuntime:
    """Concrete search components injected by the intelligence composition layer."""

    agent_loop: Callable
    guided_search_loop: Callable
    combined_hypothesis_space: Callable
    evolutionary_search_loop: Callable
    make_evolution_provider: Callable
    mcts_alpha_search: Callable


class _TrialRecordingProvider:
    """Record a definition before the wrapped provider can inspect market data."""

    def __init__(
        self,
        provider: Callable,
        catalog: AlphaCatalog,
        trial_provenance_recorder: Optional[Callable] = None,
    ) -> None:
        self._provider = provider
        self._catalog = catalog
        self._trial_provenance_recorder = trial_provenance_recorder

    def __call__(self, definition):
        self._catalog.register_trial(definition.definition_hash)
        if self._trial_provenance_recorder is not None:
            # Persist before touching market data.  A recorder failure is
            # intentionally fatal: a candidate that escaped its immutable
            # provenance boundary must not continue into evaluation.
            self._trial_provenance_recorder(definition)
        return self._provider(definition)

    def __getattr__(self, name: str):
        # Provider evidence hooks (notably leakage_ok) remain visible.
        return getattr(self._provider, name)


def _inner_loop(
    catalog,
    regime_evidence: bool,
    fwd_horizon: int = 1,
    *,
    search_runtime: ResearchSearchRuntime,
):
    """The contained ResearchAgentLoop. The forward-label horizon sets the walk-forward EMBARGO
    (overlapping-label purge = horizon-1, Lopez de Prado): when labels look ahead h periods, a
    fold's opening points share their label window with the prior fold, so they are purged before
    scoring. horizon=1 -> embargo 0 (the previous behaviour, nothing moves). Opt-in regime-evidence
    flows to the evaluator the same way."""
    cfg = AlphaFactoryConfig(compute_regime_evidence=regime_evidence,
                             wf_embargo=max(0, int(fwd_horizon) - 1))
    return search_runtime.agent_loop(catalog=catalog, config=cfg)


def _synergistic_set(catalog, k: int) -> dict:
    """De-redundant best SET from every evaluated factor's stored returns (incremental IR).
    Post-search and read-only over the catalog, so it never touches the contained loop."""
    returns_by_id = {aid: catalog.returns(aid) for aid in catalog.all_ids()
                     if len(catalog.returns(aid)) >= 2}
    return select_synergistic_set(returns_by_id, max_k=max(1, k), min_incremental_ir=0.0)


def _factor_returns(catalog) -> dict:
    """Every evaluated factor's REAL per-period net return series (read-only over the catalog). These are
    the dollar-neutral net-of-cost panel returns the evaluator computed and stored via set_returns — the
    only honest source of a per-factor return stream for downstream consumers (e.g. the console snapshot
    enrichment that lets the read-only API run the real risk/PnL engines on real data instead of synthetic)."""
    return {aid: list(catalog.returns(aid)) for aid in catalog.all_ids()
            if len(catalog.returns(aid)) >= 2}


def _oos_validate(catalog, *, embargo: int = 1):
    """Close the factory loop: no-peek walk-forward SELECTION OOS over the run's OWN discovered
    factors (their stored net returns), so the pipeline validates itself instead of relying on
    an external script. Read-only over the catalog; aligns factors on their common trailing
    length. None when too few factors / too short to split honestly (fail-closed)."""
    from services.research.oos_validation import walk_forward_selection_oos
    rbi = {aid: catalog.returns(aid) for aid in catalog.all_ids() if len(catalog.returns(aid)) >= 2}
    if len(rbi) < 2:
        return None
    length = min(len(r) for r in rbi.values())
    if length < 24:                                        # too short for a meaningful train/test split
        return None
    aligned = {aid: tuple(r[-length:]) for aid, r in rbi.items()}
    test_size = max(8, length // 8)
    train_size = max(20, length // 2)
    try:
        return walk_forward_selection_oos(aligned, train_size=train_size, test_size=test_size,
                                          step=test_size, embargo=embargo)
    except ValueError:
        return None


def _cpcv_validate(catalog, *, embargo: int = 1, n_groups: int = 6, k_test: int = 2):
    """Combinatorial purged-CV OOS DISTRIBUTION over the run's OWN discovered factors (their stored net
    returns, aligned on common trailing length) — a richer companion to _oos_validate's single no-peek
    path: it exposes selection-overfit as a distribution of OOS Sharpes across all C(n_groups, k_test)
    splits. Read-only over the catalog; None when < 2 factors or too short (fail-closed)."""
    from services.validation.cpcv import cpcv_summary
    rbi = {aid: catalog.returns(aid) for aid in catalog.all_ids() if len(catalog.returns(aid)) >= 2}
    if len(rbi) < 2:
        return None
    length = min(len(r) for r in rbi.values())
    if length < n_groups * 2:
        return None
    aligned = {aid: tuple(r[-length:]) for aid, r in rbi.items()}
    return cpcv_summary(aligned, n_groups=n_groups, k_test=k_test, embargo=embargo)


def _run_oos_validations(catalog, *, enabled: bool, embargo: int, recorder=None):
    """Run validators, then account for only the boundaries they actually used."""
    if not enabled:
        return None, None
    oos = _oos_validate(catalog, embargo=embargo)
    cpcv = _cpcv_validate(catalog, embargo=embargo)
    accesses: dict[str, tuple[str, ...]] = {}
    if oos is not None:
        accesses["WALK_FORWARD_NO_PEEK_SELECTION_OOS"] = tuple(
            f"index:[{fold['test_range'][0]},{fold['test_range'][1]})"
            for fold in oos.get("folds", ())
        )
    if cpcv is not None:
        accesses["CPCV_OOS_DISTRIBUTION"] = tuple(
            f"index:[{lo},{hi})"
            for split in cpcv.get("test_boundaries", ())
            for lo, hi in split
        )
    accesses = {
        method: tuple(sorted(set(boundaries)))
        for method, boundaries in accesses.items()
        if boundaries
    }
    if accesses and recorder is not None:
        recorder(accesses)
    return oos, cpcv


def _derive_dataset_version(
    store: VersionedMarketDataPort,
    instruments: Sequence[str],
    event_times: Sequence[int],
) -> str:
    """Dataset identity persisted with this run's registry artifacts (review P1-7).

    A bare content fingerprint does not bind the instrument set, the event grid or the
    knowledge clock, so two runs over different data contracts would share one accounting
    identity (multiple-testing / bandit state). The data layer owns that identity
    (`PITResearchDataAdapter.dataset_identity`), so prefer it when the port can supply one;
    a port that cannot keeps its previous `dataset_version` behaviour (nothing is invented
    here; this module must not import the data-layer implementation)."""
    identity = getattr(store, "dataset_identity", None)
    if callable(identity):
        return identity(instruments, event_times)
    return store.dataset_version(instruments, event_times)


def run_research_session(store: VersionedMarketDataPort, instruments: Sequence[str], *, event_times: Sequence[int],
                         fwd_horizon: int, space, rounds: int, batch_size: int,
                         timestamp: int = 0, registry=None,
                         dataset_version: Optional[str] = None, audit_log=None, universe=None,
                         fund_store=None, metrics: tuple = (), modes: tuple = ("ratio_to_price",),
                         evolutionary: bool = False, evo_generations: int = 2,
                         evo_population: int = 8, evo_elite: int = 3,
                         gp: bool = False, parsimony_lambda: float = 0.0,
                         parsimony_mu: float = 0.0, synergistic_k: int = 8,
                         redundancy_lambda: float = 0.0, regime_evidence: bool = False,
                         mcts: bool = False, mcts_iterations: int = 60,
                         diversity_lambda: float = 0.0,
                         oos_validate: bool = False, oos_embargo: int = 1,
                         trial_recorder=None,
                         trial_provenance_recorder=None,
                         oos_access_recorder=None,
                         search_id: Optional[str] = None,
                         diagnostic_only: bool = False,
                         search_runtime: ResearchSearchRuntime) -> dict:
    require_diagnostic_selection_seam(
        "run_research_session", diagnostic_only=diagnostic_only
    )
    if dataset_version is None:
        dataset_version = _derive_dataset_version(store, instruments, event_times)
    if registry is not None and (
        not isinstance(search_id, str) or not search_id.strip()
    ):
        raise ValueError("persistent research session requires an exact search_id")
    if registry is None and search_id is not None:
        raise ValueError("search_id requires a persistent experiment registry")

    catalog = AlphaCatalog(trial_recorder=trial_recorder)
    if registry is not None:
        registry.load_for_evaluation(catalog)  # trials + historical stats, no stale lifecycle state

    if mcts:
        return _run_mcts(
            store, instruments, event_times=event_times, fwd_horizon=fwd_horizon, space=space,
            metrics=metrics, modes=modes, fund_store=fund_store, universe=universe,
            catalog=catalog, registry=registry, dataset_version=dataset_version,
            timestamp=timestamp, audit_log=audit_log, iterations=mcts_iterations, gp=gp,
            synergistic_k=synergistic_k, regime_evidence=regime_evidence,
            oos_validate=oos_validate, oos_embargo=oos_embargo,
            search_id=search_id,
            trial_provenance_recorder=trial_provenance_recorder,
            oos_access_recorder=oos_access_recorder,
            search_runtime=search_runtime)

    if evolutionary or gp:
        return _run_evolutionary(
            store, instruments, event_times=event_times, fwd_horizon=fwd_horizon, space=space,
            metrics=metrics, modes=modes, fund_store=fund_store, universe=universe,
            catalog=catalog, registry=registry, dataset_version=dataset_version,
            timestamp=timestamp, audit_log=audit_log,
            generations=evo_generations, population_size=evo_population, elite_k=evo_elite,
            gp=gp, parsimony_lambda=parsimony_lambda, parsimony_mu=parsimony_mu,
            synergistic_k=synergistic_k, redundancy_lambda=redundancy_lambda,
            regime_evidence=regime_evidence, diversity_lambda=diversity_lambda,
            oos_validate=oos_validate, oos_embargo=oos_embargo,
            search_id=search_id,
            trial_provenance_recorder=trial_provenance_recorder,
            oos_access_recorder=oos_access_recorder,
            search_runtime=search_runtime)

    # opt-in unified search: with a fundamentals store + metrics, ONE bandit searches
    # price factors AND fundamental value factors (E/P, B/M). Otherwise price-only (unchanged).
    if fund_store is not None and metrics:
        effective_space = search_runtime.combined_hypothesis_space(
            space, tuple(metrics), tuple(modes)
        )
        provider = make_combined_periods_provider(store, fund_store, instruments,
                                                  event_times=event_times, fwd_horizon=fwd_horizon,
                                                  universe=universe)
    else:
        effective_space = space
        provider = make_data_periods_provider(store, instruments, event_times=event_times,
                                              fwd_horizon=fwd_horizon, universe=universe)
    provider = _TrialRecordingProvider(
        provider, catalog, trial_provenance_recorder
    )

    loop = search_runtime.guided_search_loop(
        effective_space,
        inner_loop=_inner_loop(
            catalog,
            regime_evidence,
            fwd_horizon,
            search_runtime=search_runtime,
        ),
    )
    if registry is not None:
        registry.load_bandit_into(
            loop.strategy,
            dataset_version=dataset_version,
        )
    out = loop.search(
        rounds,
        batch_size,
        provider,
        timestamp=timestamp,
        diagnostic_only=True,
    )

    # honesty signal: members with no bar on a trading day (survivorship-free run only)
    data_gaps = universe_data_gaps(store, universe, event_times) if universe is not None \
        else {"total_gaps": 0, "by_date": {}}

    recorded = []
    if registry is not None:
        for alpha_id, entry in sorted(out["report"].items()):
            ev = entry.get("evaluation")
            if ev is not None:
                if getattr(ev, "alpha_id", None) != alpha_id:
                    raise ValueError(
                        "guided-search evaluation alpha_id does not match its report key"
                    )
                recorded.append(registry.record_evaluation(
                    ev, dataset_version=dataset_version, recorded_ts=timestamp,
                    search_id=search_id,
                    config_hash=catalog.get(alpha_id).definition_hash,
                    audit_log=audit_log))
        registry.save_evaluation_context(catalog, now=timestamp)
        registry.save_bandit(
            loop.strategy,
            dataset_version=dataset_version,
        )

    oos, cpcv = _run_oos_validations(
        catalog,
        enabled=oos_validate,
        embargo=oos_embargo,
        recorder=oos_access_recorder,
    )
    return {
        "dataset_version": dataset_version,
        "mode": "guided",            # discriminator: guided has arm_means; evolutionary has best
        "arm_means": out["arm_means"],
        "human_review_queue": out["human_review_queue"],
        "recorded_run_ids": recorded,
        "n_evaluated": len(out["report"]),
        "report": out["report"],
        "data_gaps": data_gaps,
        "synergistic_set": _synergistic_set(catalog, synergistic_k),
        "factor_returns": _factor_returns(catalog),
        "oos": oos,
        "cpcv": cpcv,
        "diagnostic_only": True,
        "selection_authority": False,
        "invalidation_code": LEGACY_INVALIDATION_CODE,
    }


def _record_report(registry, report, *, catalog, dataset_version, timestamp,
                   search_id, audit_log) -> list:
    """Record every report entry that carries an evaluation as an immutable registry
    artifact. Deterministic order. Returns the recorded run ids."""
    recorded = []
    if registry is None:
        return recorded
    for alpha_id, entry in sorted(report.items()):
        ev = entry.get("evaluation")
        if ev is not None:
            if getattr(ev, "alpha_id", None) != alpha_id:
                raise ValueError(
                    "search evaluation alpha_id does not match its report key"
                )
            recorded.append(registry.record_evaluation(
                ev, dataset_version=dataset_version, recorded_ts=timestamp,
                search_id=search_id,
                config_hash=catalog.get(alpha_id).definition_hash,
                audit_log=audit_log))
    return recorded


def _run_evolutionary(store, instruments, *, event_times, fwd_horizon, space, metrics, modes,
                      fund_store, universe, catalog, registry, dataset_version, timestamp,
                      audit_log, generations, population_size, elite_k,
                      gp=False, parsimony_lambda=0.0, parsimony_mu=0.0, synergistic_k=8,
                      redundancy_lambda=0.0, regime_evidence=False, diversity_lambda=0.0,
                      oos_validate=False, oos_embargo=1,
                      search_id=None,
                      trial_provenance_recorder=None,
                      oos_access_recorder=None,
                      search_runtime: ResearchSearchRuntime = None) -> dict:
    """Evolutionary mode: machine-discover alpha through the UNCHANGED guardrailed
    ResearchAgentLoop. Two genome families share the loop:
      - flat (default): price/value atoms + crossover-into-2-leg-interaction genomes;
      - gp=True: GP EXPRESSION TREES ("T") — arbitrary-depth scalar trees over atom leaves,
        seeded with leaves AND depth-1 binary trees so crossover (subtree splice) can grow
        depth; an opt-in parsimony penalty biases the SEARCH reward toward smaller trees
        (the recorded evaluation stays honest). Containment + multiple-testing honesty are
        identical (each distinct canonical tree = one catalog trial). No bandit -> no save_bandit."""
    if search_runtime is None:
        raise ValueError("search_runtime is required")
    provider = search_runtime.make_evolution_provider(
        store,
        fund_store,
        instruments,
        event_times=event_times,
        fwd_horizon=fwd_horizon,
        universe=universe,
    )
    provider = _TrialRecordingProvider(
        provider, catalog, trial_provenance_recorder
    )
    if gp:
        ops = tuple(space.operators)
        windows = tuple(str(w) for w in space.windows)
        seed = [("T", ("leaf", op, w)) for op in ops for w in windows]
        # depth-1 binary seeds give crossover multi-node subtrees to graft (growth engine)
        for binop in ("add", "sub", "mul", "pdiv"):
            for o1, o2 in zip(ops, ops[1:]):
                seed.append(("T", (binop, ("leaf", o1, "5"), ("leaf", o2, "5"))))
        vocab = {"operators": ops, "windows": windows,
                 "parsimony_lambda": parsimony_lambda, "parsimony_mu": parsimony_mu,
                 "redundancy_lambda": redundancy_lambda, "diversity_lambda": diversity_lambda}
    else:
        seed = [("P", op, str(w)) for op in space.operators for w in space.windows]
        if fund_store is not None and metrics:
            seed += [("F", m, mode) for m in metrics for mode in modes]
        vocab = {
            "operators": tuple(space.operators),
            "windows": tuple(str(w) for w in space.windows),
            "metrics": tuple(metrics) or ("eps",),
            "modes": tuple(modes),
            "combines": ("sum", "product"),
            "redundancy_lambda": redundancy_lambda,
        }
    loop = search_runtime.evolutionary_search_loop(
        provider,
        inner_loop=_inner_loop(
            catalog,
            regime_evidence,
            fwd_horizon,
            search_runtime=search_runtime,
        ),
        vocab=vocab,
    )
    out = loop.run(seed, generations=generations, population_size=population_size,
                   elite_k=elite_k, timestamp=timestamp, diagnostic_only=True)

    data_gaps = universe_data_gaps(store, universe, event_times) if universe is not None \
        else {"total_gaps": 0, "by_date": {}}
    review_queue = [aid for aid, e in sorted(out["report"].items()) if e.get("state") == "PAPER_TRADING"]
    recorded = _record_report(registry, out["report"], catalog=catalog,
                              dataset_version=dataset_version,
                              timestamp=timestamp, search_id=search_id,
                              audit_log=audit_log)
    if registry is not None:
        registry.save_evaluation_context(catalog, now=timestamp)
    oos, cpcv = _run_oos_validations(
        catalog,
        enabled=oos_validate,
        embargo=oos_embargo,
        recorder=oos_access_recorder,
    )
    return {
        "dataset_version": dataset_version,
        "mode": "gp" if gp else "evolutionary",
        "best": out["best"],
        "human_review_queue": review_queue,
        "recorded_run_ids": recorded,
        "n_evaluated": len(out["report"]),
        "report": out["report"],
        "data_gaps": data_gaps,
        "synergistic_set": _synergistic_set(catalog, synergistic_k),
        "factor_returns": _factor_returns(catalog),
        "oos": oos,
        "cpcv": cpcv,
        "diagnostic_only": True,
        "selection_authority": False,
        "invalidation_code": LEGACY_INVALIDATION_CODE,
    }


def _run_mcts(store, instruments, *, event_times, fwd_horizon, space, metrics, modes,
              fund_store, universe, catalog, registry, dataset_version, timestamp, audit_log,
              iterations, gp=False, synergistic_k=8, regime_evidence=False,
              oos_validate=False, oos_embargo=1,
              search_id=None,
              trial_provenance_recorder=None,
              oos_access_recorder=None,
              search_runtime: ResearchSearchRuntime = None) -> dict:
    """MCTS/UCT mode: navigate the formula-tree space with UCT instead of generational GP.
    Same contained ResearchAgentLoop + multiple-testing honesty; seeds match the evolutionary
    mode (GP-tree leaves + depth-1 binaries when gp, else price/value atoms)."""
    if search_runtime is None:
        raise ValueError("search_runtime is required")
    provider = search_runtime.make_evolution_provider(
        store,
        fund_store,
        instruments,
        event_times=event_times,
        fwd_horizon=fwd_horizon,
        universe=universe,
    )
    provider = _TrialRecordingProvider(
        provider, catalog, trial_provenance_recorder
    )
    if gp:
        ops = tuple(space.operators)
        windows = tuple(str(w) for w in space.windows)
        seed = [("T", ("leaf", op, w)) for op in ops for w in windows]
        for binop in ("add", "sub", "mul", "pdiv"):
            for o1, o2 in zip(ops, ops[1:]):
                seed.append(("T", (binop, ("leaf", o1, "5"), ("leaf", o2, "5"))))
        vocab = {"operators": ops, "windows": windows}
    else:
        seed = [("P", op, str(w)) for op in space.operators for w in space.windows]
        if fund_store is not None and metrics:
            seed += [("F", m, mode) for m in metrics for mode in modes]
        vocab = {"operators": tuple(space.operators), "windows": tuple(str(w) for w in space.windows),
                 "metrics": tuple(metrics) or ("eps",), "modes": tuple(modes), "combines": ("sum", "product")}
    search = search_runtime.mcts_alpha_search(
        provider,
        inner_loop=_inner_loop(
            catalog,
            regime_evidence,
            fwd_horizon,
            search_runtime=search_runtime,
        ),
        vocab=vocab,
    )
    out = search.run(
        seed, iterations=iterations, timestamp=timestamp, diagnostic_only=True
    )

    data_gaps = universe_data_gaps(store, universe, event_times) if universe is not None \
        else {"total_gaps": 0, "by_date": {}}
    review_queue = [aid for aid, e in sorted(out["report"].items()) if e.get("state") == "PAPER_TRADING"]
    recorded = _record_report(registry, out["report"], catalog=catalog,
                              dataset_version=dataset_version,
                              timestamp=timestamp, search_id=search_id,
                              audit_log=audit_log)
    if registry is not None:
        registry.save_evaluation_context(catalog, now=timestamp)
    oos, cpcv = _run_oos_validations(
        catalog,
        enabled=oos_validate,
        embargo=oos_embargo,
        recorder=oos_access_recorder,
    )
    return {
        "dataset_version": dataset_version,
        "mode": "mcts",
        "best": out["best"],
        "human_review_queue": review_queue,
        "recorded_run_ids": recorded,
        "n_evaluated": len(out["report"]),
        "report": out["report"],
        "data_gaps": data_gaps,
        "synergistic_set": _synergistic_set(catalog, synergistic_k),
        "factor_returns": _factor_returns(catalog),
        "oos": oos,
        "cpcv": cpcv,
        "diagnostic_only": True,
        "selection_authority": False,
        "invalidation_code": LEGACY_INVALIDATION_CODE,
    }
