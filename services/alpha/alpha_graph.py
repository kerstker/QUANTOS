"""Alpha Correlation Graph (Alpha Factory upgrade Phase 3) — an EXPLICIT nodes/edges object.

Redundancy used to be an implicit pairwise dict + a flat cluster list. This builds a real, traversable
graph: nodes are alpha ids, undirected edges carry a similarity weight (from any modality —
return-correlation, AST structure, holdings). On top it answers the de-duplication questions the zoo
and population optimizer need:
  * neighbors(node, threshold)        — who is this alpha redundant with;
  * clusters(threshold)               — connected components above the threshold (redundancy groups);
  * near_duplicate_pairs(threshold)   — the deterministically-ordered high-similarity pairs;
  * clusters_as_contracts(threshold)  — AlphaCluster records (member_ids, representative, intra_corr).

Pure-Python stdlib, deterministic (sorted iteration throughout — no RNG, no wall-clock).
"""
from __future__ import annotations

import itertools
from typing import Callable, Mapping, Optional

from quantos.contracts.alpha import AlphaCluster
from .similarity import expression_similarity, return_similarity


class AlphaGraph:
    """Undirected weighted graph over alpha ids. Edges are stored once per unordered pair."""

    def __init__(self, nodes, edges) -> None:
        self.nodes = tuple(sorted(nodes))
        # edge weights keyed by an ordered (lo, hi) pair for symmetric, deterministic lookup
        self._w: dict[tuple, float] = {}
        for a, b, wgt in edges:
            if a == b:
                continue
            self._w[tuple(sorted((a, b)))] = wgt

    def weight(self, a: str, b: str) -> Optional[float]:
        return self._w.get(tuple(sorted((a, b))))

    def neighbors(self, node: str, threshold: float = 0.0) -> set:
        out = set()
        for (a, b), wgt in self._w.items():
            if wgt < threshold:
                continue
            if a == node:
                out.add(b)
            elif b == node:
                out.add(a)
        return out

    def clusters(self, threshold: float) -> list:
        """Connected components in the subgraph of edges with weight >= threshold (each isolated node is
        its own singleton cluster). Deterministic: nodes visited in sorted order."""
        seen: set = set()
        comps: list = []
        for n in self.nodes:
            if n in seen:
                continue
            stack, comp = [n], set()
            while stack:
                cur = stack.pop()
                if cur in comp:
                    continue
                comp.add(cur)
                seen.add(cur)
                for nb in self.neighbors(cur, threshold):
                    if nb not in comp:
                        stack.append(nb)
            comps.append(comp)
        return comps

    def near_duplicate_pairs(self, threshold: float) -> list:
        """(a, b, weight) for every edge with weight >= threshold, sorted by (a, b)."""
        out = [(a, b, wgt) for (a, b), wgt in self._w.items() if wgt >= threshold]
        out.sort(key=lambda e: (e[0], e[1]))
        return out

    def clusters_as_contracts(self, threshold: float,
                              key: Optional[Callable[[str], float]] = None) -> list:
        """Each redundancy cluster as an AlphaCluster: representative = highest `key` (ties -> smallest
        id; default key picks the lexicographically smallest id), intra_corr = the worst (min) pairwise
        weight inside the cluster (None for singletons)."""
        result = []
        comps = sorted(self.clusters(threshold), key=lambda c: sorted(c)[0])
        for i, comp in enumerate(comps):
            members = tuple(sorted(comp))
            if key is not None:
                rep = max(members, key=lambda m: (key(m), m))
            else:
                rep = members[0]
            intra = None
            if len(members) > 1:
                ws = [self.weight(a, b) for a, b in itertools.combinations(members, 2)
                      if self.weight(a, b) is not None]
                intra = min(ws) if ws else None
            result.append(AlphaCluster(cluster_id=f"cl_{i}", member_ids=members,
                                       representative_id=rep, intra_corr=intra))
        return result


def build_graph(items: Mapping[str, object], similarity_fn: Callable[[object, object], float]) -> AlphaGraph:
    """Generic constructor: edge weight = similarity_fn(items[a], items[b]) for every unordered pair."""
    ids = sorted(items)
    edges = [(a, b, similarity_fn(items[a], items[b])) for a, b in itertools.combinations(ids, 2)]
    return AlphaGraph(ids, edges)


def correlation_graph(returns_by_id: Mapping[str, object]) -> AlphaGraph:
    """Graph weighted by |return correlation| — the classic redundancy modality."""
    return build_graph(returns_by_id, return_similarity)


def expression_graph(expr_by_id: Mapping[str, object]) -> AlphaGraph:
    """Graph weighted by shared-subtree (AST) similarity — structural near-duplicates."""
    return build_graph(expr_by_id, expression_similarity)
