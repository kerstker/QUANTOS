"""相关性聚类原语(Phase 6 step 6b;2026-09-20 审计 P1-9 下沉到中立包)。

原先住在 ``services/alpha/cluster.py``。它并不依赖 alpha 的任何业务状态,却被
风险层与研究工作流使用,从而制造 risk → alpha 的倒置依赖;此处只保留纯函数实现,
``services/alpha/cluster.py`` 成为兼容转发。

Group redundant alphas.

Complete-linkage agglomerative clustering on the correlation distance
d = sqrt(2(1-|rho|)). Two clusters merge only when EVERY cross-member pair is
correlated above rho_cut (complete linkage), which avoids the single-linkage
chaining the review flagged (P2-3): a string of pairwise-correlated-but-
end-to-end-uncorrelated alphas does NOT collapse into one cluster.

Deterministic: candidates sorted by alpha_id; ties broken by sorted member ids;
cluster_id = stable_hash(sorted members). One representative per cluster (highest
supplied score, else first id).
"""
from __future__ import annotations

from typing import Optional

from quantos.contracts.alpha import AlphaCluster
from quantos.contracts.hashing import stable_hash

from .correlation import pairwise_correlation


def cluster_alphas(
    return_series: dict[str, tuple[float, ...]],
    rho_cut: float = 0.70,
    min_overlap: int = 60,
    scores: Optional[dict[str, float]] = None,
) -> list[AlphaCluster]:
    ids = sorted(return_series)
    if not ids:
        return []

    corr = pairwise_correlation(return_series, min_overlap=min_overlap)

    def pair_abs_corr(a: str, b: str) -> Optional[float]:
        key = (a, b) if a < b else (b, a)
        c = corr.get(key)
        return None if c is None else abs(c)

    # start: each alpha its own cluster
    clusters: list[list[str]] = [[i] for i in ids]

    def complete_linkage(c1: list[str], c2: list[str]) -> Optional[float]:
        """min |corr| across all cross pairs; None if any pair has no overlap."""
        worst = 1.0
        for a in c1:
            for b in c2:
                c = pair_abs_corr(a, b)
                if c is None:
                    return None          # can't confirm full linkage -> don't merge
                worst = min(worst, c)
        return worst

    merged = True
    while merged and len(clusters) > 1:
        merged = False
        best_link = -1.0
        best_pair = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                link = complete_linkage(clusters[i], clusters[j])
                if link is not None and link >= rho_cut and link > best_link:
                    best_link, best_pair = link, (i, j)
        if best_pair is not None:
            i, j = best_pair
            clusters[i] = sorted(clusters[i] + clusters[j])
            del clusters[j]
            merged = True

    out: list[AlphaCluster] = []
    for members in sorted(clusters, key=lambda m: sorted(m)[0]):
        members = sorted(members)
        if scores:
            rep = max(members, key=lambda a: (scores.get(a, float("-inf")), a))
        else:
            rep = members[0]
        # worst intra-cluster |corr| (None for singletons or missing overlap)
        intra: Optional[float] = None
        if len(members) > 1:
            vals = [pair_abs_corr(members[x], members[y])
                    for x in range(len(members)) for y in range(x + 1, len(members))]
            vals = [v for v in vals if v is not None]
            intra = min(vals) if vals else None
        out.append(AlphaCluster(
            cluster_id=stable_hash(members),
            member_ids=tuple(members),
            representative_id=rep,
            intra_corr=intra,
        ))
    return out
