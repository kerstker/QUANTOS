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
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence

from services.research.backtest_engine.metrics import sharpe, generalization_gap

from . import alpha_expression as ax
from . import cs_genome
from .alpha_pipeline import run_alpha_asset_pipeline
from .carry_pnl import economic_return_series
from .expression_leakage import compute_leakage_ok
from .factor_set import incremental_ir

_NEG_INF = float("-inf")


def run_alpha_factory(panel: ax.FieldPanel, fields: Sequence[str], windows: Sequence[int], *,
                      generations: int = 3, population_size: int = 20, elite_k: int = 8,
                      fwd_horizon: int = 1, quantile: float = 0.2, use_funding: Optional[bool] = None,
                      basis_returns: Optional[Mapping[str, Sequence[float]]] = None,
                      basis_min_overlap: int = 60, overfit_lambda: float = 0.0,
                      dataset_version: str = "", timestamp: int = 0) -> dict:
    """Evolve cs-alphas scored by economic L/S Sharpe, then run the full pipeline over the elite set.
    `use_funding` defaults to True when the panel carries a 'funding' matrix (score = carry economic
    return); else the price-only L/S Sharpe.

    AUTONOMOUS ORTHOGONAL DISCOVERY: when `basis_returns` (the KNOWN edges' aligned return streams) is
    given, the search reward switches to the INCREMENTAL IR ORTHOGONAL to that basis (novelty-weighted
    residual IR). A candidate that merely re-derives a basis edge scores ~0 (fully explained), so the
    genome is pushed toward genuinely NEW orthogonal return sources across the field dimensions — rather
    than re-converging on what is already known. `economic_return_series` is full-length per period, so
    candidate and basis streams stay index-aligned. Deterministic; returns the best set + pipeline output."""
    if generations < 1 or population_size < 1 or elite_k < 1:
        raise ValueError("generations / population_size / elite_k must be >= 1")
    with_funding = ("funding" in panel.matrices) if use_funding is None else use_funding
    basis = dict(basis_returns) if basis_returns else None

    scored: dict = {}                                   # alpha_id -> (score, expr)

    def _score(expr) -> float:
        aid = ax.alpha_id(expr)
        if aid in scored:
            return scored[aid][0]
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
            # generalisation pressure (opt-in): SUBTRACT a penalty for an in-sample-vs-out-of-sample Sharpe
            # collapse on the candidate's own stream, so the search is pushed AWAY from selection-overfit
            # candidates. Subtractive (not multiplicative) because this score is an unbounded Sharpe / IR,
            # possibly negative. None gap (too short / degenerate) -> no penalty. Recorded eval untouched.
            gap = generalization_gap(r)
            if gap is not None:
                s = s - overfit_lambda * gap
        scored[aid] = (s, expr)
        return s

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

    for gen in range(generations):
        for expr in pop:
            _score(expr)
        elite = _elite(elite_k)
        # breed the next generation deterministically: elite + their mutations + elite-pair crossovers
        nxt = list(elite)
        for i, e in enumerate(elite):
            nxt.append(cs_genome.mutate(e, fields, windows, gen * 1000 + i))
            mate = elite[(i + 1) % len(elite)]
            nxt.append(cs_genome.crossover(e, mate, fields, windows, gen * 1000 + i))
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
                                            max_k=elite_k)
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
