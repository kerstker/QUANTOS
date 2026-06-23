"""Robust, diversity-aware alpha ranker (Phase 6, step 8).

No single noisy metric can float an alpha to the top, and you are not rewarded
for redundant or crowded alpha:

1. Per metric, a ROBUST z-score (median / MAD, scale 1.4826) CLIPPED to +/-Z_CAP
   — the clip is what bounds any one metric's contribution, so a wildly inflated
   single metric cannot dominate (critique-friendly; P1-1 non-finite guarded).
2. Weighted composite over {ICIR, IC t-stat, capacity-adj Sharpe, hit-rate, DSR,
   marginal-IR, -turnover} (weights tilt to the hardest-to-overfit metrics),
   times a rank-stability haircut.
3. Greedy MMR selection: each pick is penalized by its max correlation to the
   ALREADY-selected set (redundancy) and by its crowding score, so duplicates and
   public-factor clones sink. Deterministic tie-break by alpha_id.
"""
from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence

from quantos.contracts.alpha import AlphaCluster, AlphaRanking
from quantos.contracts.hashing import stable_hash

from .config import AlphaFactoryConfig

Z_CAP = 3.0

# composite-weight key -> how to pull the (sign-oriented, higher=better) value
_METRIC_KEYS = {
    "icir": lambda e: e.get("icir"),
    "ic_tstat": lambda e: e.get("ic_tstat"),
    "capacity_adj_sharpe": lambda e: e.get("capacity_adj_sharpe"),
    "hit_rate": lambda e: e.get("hit_rate"),
    "deflated_sharpe": lambda e: e.get("deflated_sharpe"),
    "marginal_ir": lambda e: e.get("marginal_ir"),
    "neg_turnover": lambda e: (None if e.get("turnover") is None else -e["turnover"]),
}


def _finite(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


def _robust_z(pairs: list[tuple[str, float]]) -> dict[str, float]:
    """Median/MAD z, clipped to +/-Z_CAP. MAD==0 -> all zeros."""
    vals = [v for _, v in pairs]
    med = statistics.median(vals)
    mad = statistics.median([abs(v - med) for v in vals])
    scale = 1.4826 * mad
    out = {}
    for aid, v in pairs:
        if scale > 0:
            out[aid] = max(-Z_CAP, min(Z_CAP, (v - med) / scale))
        else:
            out[aid] = 0.0
    return out


def _max_corr_to(aid: str, selected: list[str], correlations: dict) -> float:
    best = 0.0
    for s in selected:
        key = (aid, s) if aid < s else (s, aid)
        c = correlations.get(key)
        if c is not None:
            best = max(best, abs(c))
    return best


def rank_alphas(
    evaluations: dict[str, dict],
    *,
    config: Optional[AlphaFactoryConfig] = None,
    correlations: Optional[dict] = None,
    crowding: Optional[dict[str, Optional[float]]] = None,
    rank_stability: Optional[dict[str, float]] = None,
    clusters: Optional[Sequence[AlphaCluster]] = None,
    beta: float = 0.5,
    gamma: float = 0.3,
) -> list[AlphaRanking]:
    config = config or AlphaFactoryConfig()
    correlations = correlations or {}
    crowding = crowding or {}
    rank_stability = rank_stability or {}
    weights = config.composite_weights
    # accept either plain dicts or AlphaEvaluation (pydantic) objects
    evaluations = {aid: (e if isinstance(e, dict) else e.model_dump())
                   for aid, e in evaluations.items()}
    ids = sorted(evaluations)
    if not ids:
        return []

    # 1) per-metric robust z (only over alphas with a finite value)
    z_by_metric: dict[str, dict[str, float]] = {}
    for mkey, extract in _METRIC_KEYS.items():
        pairs = [(aid, float(extract(evaluations[aid]))) for aid in ids if _finite(extract(evaluations[aid]))]
        if pairs:
            z_by_metric[mkey] = _robust_z(pairs)

    # 2) weighted composite + per-alpha z decomposition, times stability haircut
    raw: dict[str, float] = {}
    z_components: dict[str, dict[str, float]] = {}
    for aid in ids:
        comp, comps = 0.0, {}
        for mkey, w in weights.items():
            z = z_by_metric.get(mkey, {}).get(aid)
            if z is not None:
                comp += w * z
                comps[mkey] = round(z, 6)
        stab = rank_stability.get(aid, 1.0)
        raw[aid] = comp
        z_components[aid] = comps
    base = {aid: raw[aid] * rank_stability.get(aid, 1.0) for aid in ids}

    rep_ids = None
    cluster_of: dict[str, str] = {}
    if clusters:
        rep_ids = {c.representative_id for c in clusters}
        for c in clusters:
            for m in c.member_ids:
                cluster_of[m] = c.cluster_id

    # 3) greedy MMR: penalize correlation to already-selected + crowding
    remaining = set(ids)
    selected: list[str] = []
    ordered: list[tuple] = []
    while remaining:
        best_aid, best_score, best_red, best_crowd = None, -math.inf, 0.0, 0.0
        for aid in sorted(remaining):                      # deterministic tie-break
            red = _max_corr_to(aid, selected, correlations)
            cr = crowding.get(aid)
            cr = cr if (cr is not None and math.isfinite(cr)) else 0.0
            score = base[aid] - beta * red - gamma * cr
            if score > best_score:
                best_aid, best_score, best_red, best_crowd = aid, score, red, cr
        ordered.append((best_aid, best_score, best_red, best_crowd))
        selected.append(best_aid)
        remaining.remove(best_aid)

    out: list[AlphaRanking] = []
    for rank, (aid, score, red, cr) in enumerate(ordered, start=1):
        out.append(AlphaRanking(
            rank=rank, alpha_id=aid,
            composite_score=round(score, 6), raw_score=round(base[aid], 6),
            rank_stability=rank_stability.get(aid, 1.0),
            redundancy_penalty=round(beta * red, 6),
            crowding_penalty=round(gamma * cr, 6),
            z_components=z_components[aid],
            cluster_id=cluster_of.get(aid, ""),
            is_representative=(rep_ids is None or aid in rep_ids),
            reproducibility_hash=stable_hash({"alpha_id": aid, "score": round(score, 6),
                                              "z": z_components[aid]}),
        ))
    return out
