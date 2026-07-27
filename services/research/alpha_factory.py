"""Alpha mass-production loop (Alpha Factory upgrade — cs_genome drives the pipeline).

The deterministic cs_genome (seed / mutate / crossover over the cross-sectional DSL, hashlib-seeded —
no RNG) is the GENERATOR; this evolves a population of cs-alphas, SCORES each by its economic L/S
Sharpe (price + funding collected — the real money metric), keeps the elite, breeds the next
generation, and after the last generation runs the FULL contained pipeline (run_alpha_asset_pipeline)
over the best set to produce the registry / portfolio / brief. Because the score is the economic carry
return, the search evolves TOWARD the funding carry on its own.

It drives NO lifecycle transition (no APPROVED surface) and is fully deterministic. The in-sample
economic score is a SELECTION signal — the caller must still confirm the best via a no-peek OOS
(the pipeline exposes the per-alpha returns for exactly that). Pure-Python stdlib.

Population scoring is OPTIONALLY parallel (`max_workers`, 2026-07-08 top-tier gap audit S4.3/S4.8:
"mining regime is single-threaded, historical evaluation count is toy-scale even though scoring is a
pure function"). `_score_expr` below is the per-candidate fitness function lifted to MODULE scope
(not a closure) specifically so it is picklable-by-reference for `ProcessPoolExecutor` under Windows'
'spawn' start method. It is a pure function of its arguments only — no RNG, no wall-clock, no shared
mutable state across candidates — so evaluating it in a worker process is bit-identical to evaluating
it inline; see tests/research/test_alpha_factory_parallel.py for the sequential-vs-parallel proof.
"""
from __future__ import annotations

import contextlib
from concurrent.futures import ProcessPoolExecutor
from typing import Callable, Mapping, Optional, Sequence

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from services.alpha.catalog import AlphaCatalog
from services.alpha.factor_set import incremental_ir
from services.validation.performance import generalization_gap, sharpe

from . import alpha_expression as ax
from . import cs_genome
from .alpha_pipeline import _fields, run_alpha_asset_pipeline
from .carry_pnl import economic_return_series
from .expression_leakage import compute_leakage_ok
from .trial_provenance import (
    ActorType,
    TrialProvenance,
    TrialStatus,
    trial_id_for_attempt,
)

_NEG_INF = float("-inf")

# Truthful generator label for factory-produced TrialRecords: the deterministic
# cs_genome evolutionary engine, not a language model (prompt_hash stays "").
_FACTORY_MODEL_NAME = "cs_genome-evolution"


def _score_expr(expr, panel, fwd_horizon, quantile, with_funding, basis, basis_min_overlap,
                overfit_lambda) -> float:
    """Pure per-candidate fitness score (the exact arithmetic that used to live inline in the
    sequential closure). Depends ONLY on its arguments -- no RNG, no wall-clock, no memoization,
    no mutation of `panel` -- so it is safe to run in a ProcessPoolExecutor worker: the result is
    bit-identical whether called in-process or across a process boundary."""
    matrix = ax.evaluate(expr, panel)
    r = economic_return_series(matrix, panel, fwd_horizon=fwd_horizon, quantile=quantile,
                               with_funding=with_funding)
    if basis is not None:                           # orthogonal-novelty reward: incremental IR vs basis
        inc = incremental_ir(r, basis, min_overlap=basis_min_overlap) if len(r) >= 2 else None
        s = inc if (inc is not None and inc == inc) else _NEG_INF
    else:
        s = sharpe(r) if len(r) >= 2 else _NEG_INF
        if s != s:                                  # NaN guard
            s = _NEG_INF
    if overfit_lambda > 0.0 and s != _NEG_INF:
        gap = generalization_gap(r)
        if gap is not None:
            s = s - overfit_lambda * gap
    return s


# ---- ProcessPoolExecutor worker-process state (Windows 'spawn'-safe) -----------------------
# The (read-only) panel + fixed scoring knobs are sent to each worker process EXACTLY ONCE, via
# the pool's `initializer` -- not re-pickled on every task -- so per-task cost is just the small
# `expr` tuple. Process-local global, set once by `_init_worker`, never mutated afterwards.
_worker_ctx: Optional[tuple] = None


def _init_worker(panel, fwd_horizon, quantile, with_funding, basis, basis_min_overlap,
                 overfit_lambda) -> None:
    global _worker_ctx
    _worker_ctx = (panel, fwd_horizon, quantile, with_funding, basis, basis_min_overlap, overfit_lambda)


def _score_expr_in_worker(expr) -> float:
    panel, fwd_horizon, quantile, with_funding, basis, basis_min_overlap, overfit_lambda = _worker_ctx
    return _score_expr(expr, panel, fwd_horizon, quantile, with_funding, basis, basis_min_overlap,
                       overfit_lambda)


def run_alpha_factory(
    panel: ax.FieldPanel,
    fields: Sequence[str],
    windows: Sequence[int],
    *,
    generations: int = 3,
    population_size: int = 20,
    elite_k: int = 8,
    fwd_horizon: int = 1,
    quantile: float = 0.2,
    use_funding: Optional[bool] = None,
    basis_returns: Optional[Mapping[str, Sequence[float]]] = None,
    basis_min_overlap: int = 60,
    overfit_lambda: float = 0.0,
    dataset_version: str = "",
    timestamp: int = 0,
    max_workers: Optional[int] = None,
    catalog: Optional[AlphaCatalog] = None,
    search_registry=None,
    search_id: Optional[str] = None,
    search_kind: Optional[str] = None,
    search_source: Optional[str] = None,
    ephemeral: bool = False,
    hypothesis_by_candidate: Optional[Mapping[str, str]] = None,
    code_hash: str = "",
) -> dict:
    """Run a selection-bearing factory search with durable trial provenance.

    Production callers must provide an experiment registry and explicit search
    identity. Tests may opt into a process-local run with ``ephemeral=True``;
    omission is never interpreted as permission to discard the trial population.

    ``hypothesis_by_candidate`` (optional, keyed by alpha_id) binds each
    candidate to its pre-registered 727 §8.1 Hypothesis id; candidates without
    an entry record ``hypothesis_id=""`` HONESTLY — no hypothesis is ever
    fabricated for a legacy caller. ``code_hash`` is the caller-supplied code
    version pin (e.g. a git sha resolved by the driver); the deterministic core
    does no filesystem/git IO itself, so the default "" means UNPINNED, not
    "unknown but filled in".
    """
    if search_registry is None:
        if not ephemeral:
            raise ValueError(
                "search_registry is required unless test-only ephemeral=True is explicit"
            )
        if any(value is not None for value in (search_id, search_kind, search_source)):
            raise ValueError("ephemeral search must not declare persistent search identity")
        if hypothesis_by_candidate is not None or code_hash:
            raise ValueError("ephemeral search must not declare durable provenance pins")
        effective_catalog = catalog if catalog is not None else AlphaCatalog()
        return _run_alpha_factory_impl(
            panel,
            fields,
            windows,
            generations=generations,
            population_size=population_size,
            elite_k=elite_k,
            fwd_horizon=fwd_horizon,
            quantile=quantile,
            use_funding=use_funding,
            basis_returns=basis_returns,
            basis_min_overlap=basis_min_overlap,
            overfit_lambda=overfit_lambda,
            dataset_version=dataset_version,
            timestamp=timestamp,
            max_workers=max_workers,
            catalog=effective_catalog,
        )
    if ephemeral:
        raise ValueError("ephemeral=True cannot be combined with search_registry")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (search_id, search_kind, search_source, dataset_version)
    ):
        raise ValueError(
            "persistent search requires search_id, search_kind, search_source, and dataset_version"
        )

    effective_catalog = catalog if catalog is not None else AlphaCatalog()
    search_registry.load_for_evaluation(effective_catalog)
    search_registry.begin_search_attempt(
        search_id=search_id,
        search_kind=search_kind,
        source=search_source,
        dataset_version=dataset_version,
        recorded_at=timestamp,
    )

    def persist_candidates(config_hashes: Sequence[str]) -> None:
        search_registry.register_search_candidates(
            search_id,
            config_hashes,
            registered_at=timestamp,
        )

    hypotheses = dict(hypothesis_by_candidate) if hypothesis_by_candidate else {}
    record_provenance = getattr(search_registry, "record_trial_provenance", None)

    def persist_provenance(definitions, exprs) -> None:
        """Write the 727 §7.1 TrialRecord for every registered candidate.

        RECORDED is the only status the factory can attest honestly: it
        registers and scores candidates but tracks NO per-candidate elimination
        stage (alpha_id de-duplication happens BEFORE ledger registration and is
        deliberately not fabricated into an ELIMINATED_DUPLICATE record).
        record_trial_provenance is idempotent on trial_id, so a replayed run
        never double-counts.
        """
        if record_provenance is None:
            return
        for definition, expr in zip(definitions, exprs):
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
                    started_at=timestamp,
                    completed_at=timestamp,
                    hypothesis_id=hypotheses.get(definition.alpha_id, ""),
                    model_name=_FACTORY_MODEL_NAME,
                    factor_ids=definition.factor_ids,
                    alpha_expression=ax.serialize(expr),
                    canonical_expression=ax.serialize(ax.canonicalize(expr)),
                    expression_hash=ax.alpha_id(expr),
                    parameter_set=dict(definition.params),
                    parameter_hash=stable_hash(dict(definition.params)),
                    dataset_versions=(dataset_version,),
                    code_hash=code_hash,
                )
            )

    try:
        result = _run_alpha_factory_impl(
            panel,
            fields,
            windows,
            generations=generations,
            population_size=population_size,
            elite_k=elite_k,
            fwd_horizon=fwd_horizon,
            quantile=quantile,
            use_funding=use_funding,
            basis_returns=basis_returns,
            basis_min_overlap=basis_min_overlap,
            overfit_lambda=overfit_lambda,
            dataset_version=dataset_version,
            timestamp=timestamp,
            max_workers=max_workers,
            catalog=effective_catalog,
            candidate_recorder=persist_candidates,
            provenance_recorder=persist_provenance,
        )
        recorded_run_ids = []
        for asset in result["pipeline"]["registry"].all_latest():
            if asset.evaluation is None:
                continue
            if asset.evaluation.alpha_id != asset.alpha_id:
                raise ValueError(
                    "factory evaluation alpha_id does not match its alpha asset"
                )
            recorded_run_ids.append(
                search_registry.record_evaluation(
                    asset.evaluation,
                    dataset_version=dataset_version,
                    recorded_ts=timestamp,
                    search_id=search_id,
                    config_hash=effective_catalog.get(
                        asset.alpha_id
                    ).definition_hash,
                )
            )
        result["recorded_run_ids"] = recorded_run_ids
        search_registry.save_evaluation_context(effective_catalog, now=timestamp)
        search_registry.finish_search_attempt(
            search_id,
            status="COMPLETED",
            recorded_at=timestamp,
        )
        return result
    except BaseException as original:
        try:
            search_registry.finish_search_attempt(
                search_id,
                status="FAILED",
                recorded_at=timestamp,
            )
        except BaseException as terminal_error:
            original.add_note(
                f"failed to persist FAILED search terminal: {terminal_error}"
            )
        raise


def _run_alpha_factory_impl(
    panel: ax.FieldPanel,
    fields: Sequence[str],
    windows: Sequence[int],
    *,
    generations: int,
    population_size: int,
    elite_k: int,
    fwd_horizon: int,
    quantile: float,
    use_funding: Optional[bool],
    basis_returns: Optional[Mapping[str, Sequence[float]]],
    basis_min_overlap: int,
    overfit_lambda: float,
    dataset_version: str,
    timestamp: int,
    max_workers: Optional[int],
    catalog: AlphaCatalog,
    candidate_recorder: Optional[Callable[[Sequence[str]], None]] = None,
    provenance_recorder: Optional[Callable[[Sequence, Sequence], None]] = None,
) -> dict:
    """Evolve cs-alphas scored by economic L/S Sharpe, then run the full pipeline over the elite set.
    `use_funding` defaults to True when the panel carries a 'funding' matrix (score = carry economic
    return); else the price-only L/S Sharpe.

    AUTONOMOUS ORTHOGONAL DISCOVERY: when `basis_returns` (the KNOWN edges' aligned return streams) is
    given, the search reward switches to the INCREMENTAL IR ORTHOGONAL to that basis (novelty-weighted
    residual IR). A candidate that merely re-derives a basis edge scores ~0 (fully explained), so the
    genome is pushed toward genuinely NEW orthogonal return sources across the field dimensions — rather
    than re-converging on what is already known. `economic_return_series` is full-length per period, so
    candidate and basis streams stay index-aligned. Deterministic; returns the best set + pipeline output.

    `max_workers`: None (default) scores each generation's population SEQUENTIALLY, in-process --
    byte-identical to the pre-parallel implementation, so every existing caller is unaffected. An int
    > 1 scores that generation's UNSCORED candidates (after the same alpha_id de-duplication the
    sequential path always applied, so a candidate is never computed twice) in a `ProcessPoolExecutor`
    of that size, via `executor.map` (ORDER-PRESERVING -- deliberately not `as_completed`, whose
    completion order is nondeterministic and would make any downstream tie-break order-dependent on
    scheduling). Because `_score_expr` is a pure function, the parallel and sequential paths produce
    bit-identical results (see tests/research/test_alpha_factory_parallel.py). `max_workers=1` behaves
    like None (no pool is spun up).

    Every pending candidate is registered in the catalog, and through the optional
    durable recorder, before its score is computed. A failed scorer therefore cannot
    erase an attempted trial from the multiple-testing population."""
    if generations < 1 or population_size < 1 or elite_k < 1:
        raise ValueError("generations / population_size / elite_k must be >= 1")
    with_funding = ("funding" in panel.matrices) if use_funding is None else use_funding
    basis = dict(basis_returns) if basis_returns else None

    scored: dict = {}                                   # alpha_id -> (score, expr)

    def _candidate_definition(expr) -> AlphaDefinition:
        alpha_id = ax.alpha_id(expr)
        return AlphaDefinition(
            alpha_id=alpha_id,
            name=alpha_id,
            factor_ids=tuple(sorted(_fields(expr))),
            universe="cs",
            params={"expr": ax.serialize(expr)},
        )

    def _pending(exprs):
        """First-occurrence-order (alpha_id, expr) pairs not yet in `scored` -- mirrors the original
        sequential `_score`'s 'first expr seen for a given alpha_id wins, later duplicates are a
        no-op' semantics exactly, so de-duplication happens BEFORE dispatch (a duplicate is never
        (re)computed, whether sequentially or in a worker)."""
        seen, out = set(), []
        for e in exprs:
            aid = ax.alpha_id(e)
            if aid in scored or aid in seen:
                continue
            seen.add(aid)
            out.append((aid, e))
        return out

    def _score_population(exprs, executor) -> None:
        todo = _pending(exprs)
        if not todo:
            return
        definitions = [_candidate_definition(expr) for _alpha_id, expr in todo]
        config_hashes = [definition.definition_hash for definition in definitions]
        if candidate_recorder is not None:
            candidate_recorder(config_hashes)
        if provenance_recorder is not None:
            # 727 §7.1 TrialRecord per candidate, written BEFORE scoring (same
            # registration-time semantics as candidate_recorder).
            provenance_recorder(definitions, [e for _aid, e in todo])
        for config_hash in config_hashes:
            catalog.register_trial(config_hash)
        if executor is None:
            results = [_score_expr(e, panel, fwd_horizon, quantile, with_funding, basis,
                                   basis_min_overlap, overfit_lambda) for _aid, e in todo]
        else:
            # order-preserving by construction (concurrent.futures.Executor.map contract) --
            # NEVER as_completed here, which would make result order scheduling-dependent.
            results = list(executor.map(_score_expr_in_worker, [e for _aid, e in todo]))
        for (aid, e), s in zip(todo, results):
            scored[aid] = (s, e)

    def _elite(k):
        return [e for _s, e in sorted(scored.values(), key=lambda sv: (-sv[0], ax.serialize(sv[1])))[:k]]

    # seed population. When fewer slots than seeds, take an EVENLY-SPACED subset (not the first N, which
    # would bias toward the first field/operator family and silently drop the rest) — preserves the
    # seed diversity deterministically. n_seeds is surfaced so the truncation is never silent.
    seeds = cs_genome.seed_alphas(fields, windows)
    n_seeds = len(seeds)
    if n_seeds > population_size:
        pop = [seeds[(i * n_seeds) // population_size] for i in range(population_size)]
    else:
        pop = list(seeds)

    bred_by: dict = {}                                  # P3-4:child_aid -> (parent_ids, op),育种谱系
    # One pool for the WHOLE run (reused across generations), not one per generation: worker-process
    # spawn + the one-time initializer panel transfer are the dominant fixed costs, so amortizing them
    # over every generation's scoring batch is what makes parallelism a net win. contextlib.nullcontext
    # keeps the sequential path (max_workers is None/1) exactly as before -- no pool, no processes.
    pool_cm = (ProcessPoolExecutor(max_workers=max_workers, initializer=_init_worker,
                                   initargs=(panel, fwd_horizon, quantile, with_funding, basis,
                                            basis_min_overlap, overfit_lambda))
              if max_workers and max_workers > 1 else contextlib.nullcontext())
    with pool_cm as executor:
        for gen in range(generations):
            _score_population(pop, executor)
            elite = _elite(elite_k)
            # breed the next generation deterministically: elite + their mutations + elite-pair crossovers
            nxt = list(elite)
            for i, e in enumerate(elite):
                e_aid = ax.alpha_id(e)
                child_m = cs_genome.mutate(e, fields, windows, gen * 1000 + i)
                nxt.append(child_m)
                bred_by.setdefault(ax.alpha_id(child_m), ((e_aid,), "mutate"))       # 首见 dedup 保确定性
                mate = elite[(i + 1) % len(elite)]
                child_c = cs_genome.crossover(e, mate, fields, windows, gen * 1000 + i)
                nxt.append(child_c)
                bred_by.setdefault(ax.alpha_id(child_c), ((e_aid, ax.alpha_id(mate)), "crossover"))
            seen, pop = set(), []
            for e in nxt:
                a = ax.alpha_id(e)
                if a not in seen:
                    seen.add(a)
                    pop.append(e)
                if len(pop) >= population_size:
                    break

    best = sorted(scored.values(), key=lambda sv: (-sv[0], ax.serialize(sv[1])))[:elite_k]
    best_exprs = [e for _s, e in best]
    pipeline_out = run_alpha_asset_pipeline(panel, best_exprs, fwd_horizon=fwd_horizon,
                                            dataset_version=dataset_version, timestamp=timestamp,
                                            max_k=elite_k, bred_by=bred_by, catalog=catalog)   # P3-4:育种子代记谱系
    return {
        "generations": generations,
        "population_size": population_size,
        "n_seeds": n_seeds,                             # seed_alphas count (truncation to pop is not silent)
        "n_evaluated": len(scored),
        "scored_by": ("orthogonal_incremental_ir" if basis is not None
                      else "economic" if with_funding else "price"),
        "best": [{"alpha_id": ax.alpha_id(e), "expr": ax.serialize(e), "score": round(s, 4),
                  "leakage_ok": compute_leakage_ok(e, panel)} for s, e in best],
        "pipeline": pipeline_out,
    }
