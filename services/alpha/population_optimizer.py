"""Alpha Population Optimizer (Alpha Factory upgrade Phase 4).

Selecting a portfolio of alphas is not ranking them by single-factor IC: the second-best alpha is
often a near-copy of the best and adds no information, and the best SET trades off edge against
redundancy, drawdown, turnover, complexity and crowding. This optimizer:

  * SELECTS a set — greedy forward selection by incremental IR (reusing factor_set), then drops
    near-duplicates via the Phase-3 correlation graph at `rho_cut`;
  * WEIGHTS it — equal / inverse-vol (risk-parity) / IR-proportional, normalised to gross 1;
  * SCORES the set with a portfolio-level OBJECTIVE — post-cost IR + diversity - complexity - crowding;
  * REPORTS marginal contributions per alpha INCLUDING drawdown (drop-one), and the excluded alphas
    with reasons.

`method='evolutionary'` refines the greedy set by deterministic local search (single add/remove/swap,
accept only improving moves) — it never scores below greedy. Pure-Python stdlib, deterministic (no RNG,
no wall-clock). Cost here is a per-period haircut PROXY; the real cost/capacity backtest is Phase 5.
"""
from __future__ import annotations

import itertools
import math
import statistics
from typing import Mapping, Optional, Sequence

from .factor_set import select_synergistic_set
from .similarity import return_similarity

_EPS = 1e-12


# ---- core portfolio math --------------------------------------------------------------------
def _align(returns_by_id: Mapping[str, Sequence[float]]):
    series = {k: list(v) for k, v in returns_by_id.items()}
    if not series:
        return {}, 0
    length = min(len(v) for v in series.values())
    return {k: v[-length:] for k, v in series.items()}, length


def combine_returns(returns_by_id: Mapping[str, Sequence[float]], weights: Mapping[str, float]) -> list:
    """Weighted portfolio return series (aligned on the candidates' common trailing length)."""
    aligned, length = _align(returns_by_id)
    if length == 0:
        return []
    return [sum(weights.get(k, 0.0) * aligned[k][t] for k in aligned) for t in range(length)]


def information_ratio(returns: Sequence[float]) -> float:
    """Per-period IR (mean/stdev). A constant positive series is a perfect (+inf) edge; constant zero
    is no edge (0.0). Matches the project's other zero-variance conventions."""
    xs = [r for r in returns if r is not None and math.isfinite(r)]
    if len(xs) < 2:
        return 0.0
    sd = statistics.pstdev(xs)
    if sd <= _EPS:
        m = statistics.fmean(xs)
        return math.inf if m > 0 else (-math.inf if m < 0 else 0.0)
    return statistics.fmean(xs) / sd


def max_drawdown(returns: Sequence[float]) -> float:
    """Worst peak-to-trough decline of the cumulative-return equity curve, as a positive fraction."""
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in returns:
        if r is None or isinstance(r, bool) or not math.isfinite(r):
            continue
        if eq <= 0.0:
            mdd = 1.0
            continue
        eq = max(0.0, eq * (1.0 + r))
        peak = max(peak, eq)
        if peak > 0:
            mdd = max(mdd, (peak - eq) / peak)
    return mdd


def weights_for(returns_by_id: Mapping[str, Sequence[float]], scheme: str = "inverse_vol") -> dict:
    """Gross-1 weights. equal: uniform; inverse_vol: 1/stdev (risk parity); ir: max(0, standalone IR).
    A degenerate scheme (all-zero raw) falls back to equal so the portfolio is never empty."""
    aligned, length = _align(returns_by_id)
    ids = sorted(aligned)
    if not ids:
        return {}
    if scheme == "equal":
        raw = {k: 1.0 for k in ids}
    elif scheme == "inverse_vol":
        raw = {}
        for k in ids:
            clean_k = [x for x in aligned[k] if x is not None and not isinstance(x, bool) and math.isfinite(x)]
            sd = statistics.pstdev(clean_k) if len(clean_k) > 1 else 0.0
            raw[k] = (1.0 / sd) if math.isfinite(sd) and sd > _EPS else 0.0
    elif scheme == "ir":
        raw = {k: max(0.0, information_ratio(aligned[k])) for k in ids}
        raw = {k: (v if math.isfinite(v) else 0.0) for k, v in raw.items()}
    else:
        raise ValueError(f"unknown weighting scheme: {scheme!r}")
    total = sum(raw.values())
    if not math.isfinite(total) or total <= _EPS:
        return {k: 1.0 / len(ids) for k in ids}
    return {k: raw[k] / total for k in ids}


def marginal_contributions(returns_by_id: Mapping[str, Sequence[float]],
                           weights: Mapping[str, float]) -> dict:
    """Per-alpha drop-one contribution: marginal_ir = portfolio IR with the alpha minus IR without it
    (remaining weights renormalised); marginal_dd = portfolio max-drawdown WITH minus WITHOUT (positive
    => the alpha worsens drawdown). A higher marginal_ir / lower marginal_dd alpha earns its place."""
    full = combine_returns(returns_by_id, weights)
    full_ir, full_dd = information_ratio(full), max_drawdown(full)
    out = {}
    for k in returns_by_id:
        rest = {j: returns_by_id[j] for j in returns_by_id if j != k}
        if rest:
            sw = sum(weights.get(j, 0.0) for j in rest)
            rw = {j: (weights.get(j, 0.0) / sw if sw > _EPS else 1.0 / len(rest)) for j in rest}
            wo = combine_returns(rest, rw)
            wo_ir, wo_dd = information_ratio(wo), max_drawdown(wo)
        else:
            wo_ir, wo_dd = 0.0, 0.0
        if math.isinf(full_ir) and math.isinf(wo_ir) and (full_ir > 0) == (wo_ir > 0):
            mir = 0.0          # inf - inf (same sign) is undefined; equal infinite edge => 0 incremental
        else:
            mir = full_ir - wo_ir
        out[k] = {"marginal_ir": round(mir, 6) if math.isfinite(mir) else mir,
                  "marginal_dd": round(full_dd - wo_dd, 6)}
    return out


# ---- diversity / objective ------------------------------------------------------------------
def _mean_abs_corr(returns_by_id: Mapping[str, Sequence[float]]) -> float:
    ids = sorted(returns_by_id)
    pairs = list(itertools.combinations(ids, 2))
    if not pairs:
        return 0.0
    return statistics.fmean(return_similarity(returns_by_id[a], returns_by_id[b]) for a, b in pairs)


def diversity(returns_by_id: Mapping[str, Sequence[float]]) -> float:
    """1 - mean pairwise |corr| over the set: 1.0 = fully orthogonal / singleton, 0.0 = all identical."""
    return 1.0 - _mean_abs_corr(returns_by_id)


def _objective(returns_by_id, ids, *, scheme, cost_bps, w_diversity, w_complexity, w_crowding,
               complexity_by_id, crowding_by_id) -> dict:
    """Score a candidate set: post-cost IR + w_div*diversity - w_cplx*mean_complexity
    - w_crowd*mean_crowding. Returns the full metric bundle (so the caller need not recompute)."""
    if not ids:
        return {"objective": -math.inf, "weights": {}, "portfolio_ir": 0.0, "post_cost_ir": 0.0,
                "max_drawdown": 0.0, "diversity": 0.0}
    sub = {k: returns_by_id[k] for k in ids}
    weights = weights_for(sub, scheme)
    port = combine_returns(sub, weights)
    p_ir = information_ratio(port)
    cost = cost_bps / 10_000.0
    post = information_ratio([r - cost for r in port])
    div = diversity(sub)
    mean_cplx = statistics.fmean(complexity_by_id.get(k, 0.0) for k in ids) if complexity_by_id else 0.0
    mean_crowd = statistics.fmean(crowding_by_id.get(k, 0.0) for k in ids) if crowding_by_id else 0.0
    if math.isnan(post):
        base = -10.0
    else:
        base = min(10.0, max(-10.0, post))    # clamp (inf -> 10) so a degenerate set can't dominate via 1e6
    obj = base + w_diversity * div - w_complexity * mean_cplx - w_crowding * mean_crowd
    return {"objective": obj, "weights": weights, "portfolio_ir": p_ir, "post_cost_ir": post,
            "max_drawdown": max_drawdown(port), "diversity": div}


# ---- selection ------------------------------------------------------------------------------
def _dedupe_by_correlation(returns_by_id, ordered_ids, rho_cut: float) -> tuple:
    """Walk the candidates in (incremental-IR) order, keeping one only if it is NOT |corr| >= rho_cut
    with an already-kept alpha. Returns (kept, dropped) — the redundancy filter over the graph."""
    kept, dropped = [], []
    for aid in ordered_ids:
        if any(return_similarity(returns_by_id[aid], returns_by_id[k]) >= rho_cut for k in kept):
            dropped.append(aid)
        else:
            kept.append(aid)
    return kept, dropped


def optimize_population(returns_by_id: Mapping[str, Sequence[float]], *, max_k: int,
                        scheme: str = "inverse_vol", method: str = "greedy", cost_bps: float = 0.0,
                        rho_cut: float = 0.7, min_overlap: int = 20,
                        min_incremental_ir: float = float("-inf"),
                        complexity_by_id: Optional[Mapping[str, float]] = None,
                        crowding_by_id: Optional[Mapping[str, float]] = None,
                        w_diversity: float = 0.5, w_complexity: float = 0.05,
                        w_crowding: float = 0.5) -> dict:
    """Select + weight an alpha portfolio. method='greedy' = incremental-IR forward selection then a
    correlation de-dup; method='evolutionary' = greedy seed refined by deterministic local search
    (single add/remove/swap, accept only objective improvements). Returns the Alpha Portfolio."""
    if max_k < 1:
        raise ValueError("max_k must be >= 1")
    obj_kw = dict(scheme=scheme, cost_bps=cost_bps, w_diversity=w_diversity, w_complexity=w_complexity,
                  w_crowding=w_crowding, complexity_by_id=complexity_by_id, crowding_by_id=crowding_by_id)

    if not returns_by_id:
        return _empty_portfolio(method)

    greedy = select_synergistic_set(returns_by_id, max_k=len(returns_by_id),
                                    min_incremental_ir=min_incremental_ir, min_overlap=min_overlap)
    kept, dropped_corr = _dedupe_by_correlation(returns_by_id, greedy["selected_ids"], rho_cut)
    selected = kept[:max_k]

    dropped_evo: set = set()
    if method == "evolutionary":
        before = set(selected)
        selected = _local_search(returns_by_id, selected, max_k=max_k, obj_kw=obj_kw)
        dropped_evo = before - set(selected)
    elif method != "greedy":
        raise ValueError(f"unknown method: {method!r}")

    excluded = []
    chosen = set(selected)
    dropped_corr_set = set(dropped_corr)
    for aid in sorted(returns_by_id):
        if aid in chosen:
            continue
        if aid in dropped_corr_set:
            reason = "REDUNDANT_CORRELATED"
        elif aid in dropped_evo:
            reason = "DROPPED_BY_EVOLUTIONARY"
        elif aid not in greedy["selected_ids"]:
            reason = "DROPPED_LOW_INCREMENTAL_IR"
        else:
            reason = "BEYOND_MAX_K"
        excluded.append({"id": aid, "reason": reason})

    scored = _objective(returns_by_id, selected, **obj_kw)
    sub = {k: returns_by_id[k] for k in selected}
    margins = marginal_contributions(sub, scored["weights"]) if selected else {}
    expected_turnover = _weighted_mean(scored["weights"], crowding_by_id and None, returns_by_id, key=None)
    return {
        "method": method,
        "n_candidates": len(returns_by_id),
        "max_k": max_k,
        "selected_ids": list(selected),
        "selected": [{"id": k, "weight": round(scored["weights"].get(k, 0.0), 6),
                      "marginal_ir": margins[k]["marginal_ir"], "marginal_dd": margins[k]["marginal_dd"]}
                     for k in selected],
        "weights": {k: round(v, 6) for k, v in scored["weights"].items()},
        "excluded": excluded,
        "portfolio_ir": _r(scored["portfolio_ir"]),
        "post_cost_ir": _r(scored["post_cost_ir"]),
        "max_drawdown": round(scored["max_drawdown"], 6),
        "diversity": round(scored["diversity"], 6),
        "objective": _r(scored["objective"]),
        "expected_turnover": expected_turnover,
    }


def _local_search(returns_by_id, seed, *, max_k, obj_kw, max_iter: int = 50) -> list:
    """Deterministic hill-climb from `seed`: repeatedly try every single add (if < max_k), remove, and
    swap; apply the change that most improves the objective; stop when none improves or iters exhausted."""
    all_ids = sorted(returns_by_id)
    current = list(seed)
    best = _objective(returns_by_id, current, **obj_kw)["objective"]
    for _ in range(max_iter):
        candidates = []
        if len(current) < max_k:
            for aid in all_ids:
                if aid not in current:
                    candidates.append(sorted(current + [aid]))
        for aid in current:
            candidates.append([x for x in current if x != aid])
        for out_id in current:
            for in_id in all_ids:
                if in_id not in current:
                    candidates.append(sorted([x for x in current if x != out_id] + [in_id]))
        best_move, best_obj = None, best
        for cand in candidates:
            if not cand:
                continue
            o = _objective(returns_by_id, cand, **obj_kw)["objective"]
            if o > best_obj + 1e-12 or (abs(o - best_obj) <= 1e-12
                                        and (best_move is None or cand < best_move)):
                best_move, best_obj = cand, o
        if best_move is None or best_obj <= best + 1e-12:
            break
        current, best = best_move, best_obj
    return current


def _weighted_mean(weights, _unused, returns_by_id, key) -> float:
    # placeholder expected-turnover proxy: 0.0 until real holdings/turnover are supplied (Phase 5)
    return 0.0


def _r(x: float):
    return round(x, 6) if math.isfinite(x) else x


def _empty_portfolio(method: str) -> dict:
    return {"method": method, "n_candidates": 0, "max_k": 0, "selected_ids": [], "selected": [],
            "weights": {}, "excluded": [], "portfolio_ir": 0.0, "post_cost_ir": 0.0,
            "max_drawdown": 0.0, "diversity": 0.0, "objective": 0.0, "expected_turnover": 0.0}
