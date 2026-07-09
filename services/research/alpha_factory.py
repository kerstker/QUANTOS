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
from typing import Mapping, Optional, Sequence

from quantos.contracts.alpha import AlphaDefinition
from services.alpha.catalog import AlphaCatalog
from services.research.backtest_engine.metrics import sharpe, generalization_gap

from . import alpha_expression as ax
from . import cs_genome
from .alpha_pipeline import _fields, run_alpha_asset_pipeline
from .carry_pnl import economic_return_series
from .expression_leakage import compute_leakage_ok
from .factor_set import incremental_ir

_NEG_INF = float("-inf")


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


def run_alpha_factory(panel: ax.FieldPanel, fields: Sequence[str], windows: Sequence[int], *,
                      generations: int = 3, population_size: int = 20, elite_k: int = 8,
                      fwd_horizon: int = 1, quantile: float = 0.2, use_funding: Optional[bool] = None,
                      basis_returns: Optional[Mapping[str, Sequence[float]]] = None,
                      basis_min_overlap: int = 60, overfit_lambda: float = 0.0,
                      dataset_version: str = "", timestamp: int = 0,
                      max_workers: Optional[int] = None,
                      catalog: Optional[AlphaCatalog] = None) -> dict:
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

    `catalog` (2026-07-10, gap audit rank #6): None (default) is byte-identical to the pre-existing
    behavior -- run_alpha_asset_pipeline still gets a fresh in-memory catalog for the elite set, and
    NOTHING from the wider scored population is registered anywhere. When the caller supplies a
    persistent catalog instead, TWO things change: (1) register_trial() is called for EVERY
    genuinely-scored candidate this run (the full `scored` population, not just the elite_k passed to
    the pipeline) -- so global_trial_count() reflects the search's TRUE breadth, closing the gap where
    only the persistent in-gate ResearchAgentLoop path's trials were ever honestly counted; (2) that
    same catalog is passed through to run_alpha_asset_pipeline, so the elite set's full evaluations
    also accumulate into it rather than a disposable one. This function still performs NO IO itself --
    persisting the catalog (if desired) is entirely the caller's responsibility, exactly like every
    other catalog-accepting function in this codebase (AlphaFactory, tools/register_alpha_catalog.py)."""
    if generations < 1 or population_size < 1 or elite_k < 1:
        raise ValueError("generations / population_size / elite_k must be >= 1")
    with_funding = ("funding" in panel.matrices) if use_funding is None else use_funding
    basis = dict(basis_returns) if basis_returns else None

    scored: dict = {}                                   # alpha_id -> (score, expr)

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

    if catalog is not None:
        # 2026-07-10 (gap audit rank #6): register a trial for EVERY genuinely-scored candidate
        # (not just the elite_k passed to the pipeline below) -- the definition_hash construction
        # mirrors alpha_pipeline.py's own (factor_ids=sorted field deps, universe="cs",
        # params={"expr": serialized expression}) EXACTLY, so a candidate that reaches both this
        # loop and the full pipeline evaluation hashes IDENTICALLY and is never double-counted
        # (register_trial is itself idempotent by content hash). The serialized expression in
        # params is load-bearing, not decorative: without it, distinct expressions sharing the
        # same field dependencies collide to the same hash and silently undercount trials (see
        # alpha_pipeline.py's matching comment for the empirical confirmation of this bug).
        for _score, e in scored.values():
            deps = tuple(sorted(_fields(e)))
            catalog.register_trial(AlphaDefinition(
                alpha_id=ax.alpha_id(e), name=ax.alpha_id(e), factor_ids=deps, universe="cs",
                params={"expr": ax.serialize(e)}
            ).definition_hash)

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
