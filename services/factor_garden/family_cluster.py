"""Factor family clustering (727 §6.4/§7.3 / Factor Garden F5, 2026-07-27).

727 §7.3: variants of the same economic family / input dependency / return correlation
must NOT count as independent discoveries (rank(global_ls) / zscore(global_ls) /
mean_7d(global_ls) are ONE family) — cluster first, then family-level FDR. This module
assigns every garden factor a deterministic cluster_id under its DECLARED FactorFamily.

Rulings:
- Declared family first: defn.factor_family_id must be a valid FactorFamily — missing/
  invalid raises FactorClusterError (fail-closed, never guesses a family).
- Family-internal correlation clustering: correlations are INJECTED
  ({(fid_a, fid_b): rho}, key-order-insensitive) — computing them needs return/IC
  series, which is the caller's job (this module is a pure core, no matrices, no IO).
  Pairs with |rho| >= rho_cut merge by UNION-FIND (transitive closure: a-b and b-c
  put a, b, c in one cluster). Honest divergence: services/alpha/cluster.py uses
  complete-linkage for alphas precisely to forbid this chaining (P2-3); F5's mandate
  is transitive-closure family grouping, which is the CONSERVATIVE direction for the
  §7.3 use (a wider cluster claims fewer independent bets, never more). Chained
  over-merging is possible by construction and is why rho_cut stays an explicit,
  UNCALIBRATED engineering default (0.5, same convention as W3).
- correlation=None -> every factor SINGLETON: no correlation evidence = no redundancy
  claimed (and none denied either — a singleton is "ungrouped", not "proven unique").
- cluster_id is deterministic: "<FAMILY>#<smallest factor_id in cluster>"
  (insertion-order independent); singleton/undeclared-evidence clusters use
  "<FAMILY>#<factor_id>".

§7.3 seam (interface only, NO integration): cluster_id is the grouping key for
family-level DSR/FDR — trial multiplicity should be counted per cluster_id, not per
factor_id. This module deliberately does NOT touch services/alpha/family_testing.py,
the ranker, or multiple_testing; adopting the key is the caller's decision and a
separate review.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from quantos.contracts.feature import FactorFamily, FeatureDefinition

METHOD_DECLARED = "DECLARED"          # declared family only — no correlation evidence for this factor
METHOD_CORRELATION = "CORRELATION"    # multi-factor cluster joined by injected |rho| >= rho_cut
METHOD_SINGLETON = "SINGLETON"        # alone (correlation=None, or no pair reached the cut)

DEFAULT_RHO_CUT = 0.5                 # UNCALIBRATED engineering default (W3 convention)


class FactorClusterError(ValueError):
    """Clustering input violates the 727 §6.4/§7.3 rulings (fail-closed)."""


@dataclass(frozen=True)
class FactorClusterAssignment:
    factor_id: str
    family: FactorFamily
    cluster_id: str                    # "<FAMILY>#<min member factor_id>" — the §7.3 FDR grouping key
    method: str                        # DECLARED | CORRELATION | SINGLETON


def _family_of_defn(defn: FeatureDefinition) -> FactorFamily:
    try:
        return FactorFamily(defn.factor_family_id)
    except ValueError as exc:
        raise FactorClusterError(
            f"factor {defn.name!r} has no valid declared FactorFamily "
            f"({defn.factor_family_id!r}) — fail-closed, never guessed"
        ) from exc


def family_of(defns: Iterable[FeatureDefinition], factor_id: str) -> FactorFamily:
    """The declared FactorFamily of one factor; unknown factor or invalid/missing
    family -> FactorClusterError (fail-closed)."""
    for defn in defns:
        if not isinstance(defn, FeatureDefinition):
            raise TypeError("defns must contain FeatureDefinition instances")
        if defn.name == factor_id:
            return _family_of_defn(defn)
    raise FactorClusterError(f"unknown factor: {factor_id!r}")


def assign_factor_clusters(
    defns: Iterable[FeatureDefinition],
    *,
    correlation: Optional[Mapping[tuple, float]] = None,
    rho_cut: float = DEFAULT_RHO_CUT,
) -> tuple:
    """Assign deterministic cluster_ids within declared families.

    `correlation` maps (fid_a, fid_b) -> rho (key-order-insensitive); every referenced
    factor must be in `defns` (unknown reference -> FactorClusterError). Cross-family
    pairs never merge (families are hard boundaries, even at |rho| = 0.99).
    """
    defs = tuple(defns)
    if any(not isinstance(d, FeatureDefinition) for d in defs):
        raise TypeError("defns must contain FeatureDefinition instances")
    names = [d.name for d in defs]
    if len(set(names)) != len(names):
        raise FactorClusterError("duplicate factor ids in defns")
    if isinstance(rho_cut, bool) or not isinstance(rho_cut, (int, float)) \
            or not math.isfinite(rho_cut) or not 0.0 <= rho_cut <= 1.0:
        raise FactorClusterError("rho_cut must be a finite number in [0, 1]")

    families = {}                                   # FactorFamily -> sorted factor ids
    for defn in defs:
        families.setdefault(_family_of_defn(defn), []).append(defn.name)
    for fids in families.values():
        fids.sort()

    # normalize + validate the injected correlation map (key-order-insensitive)
    corr = {}
    if correlation is not None:
        for key, rho in correlation.items():
            if not isinstance(key, tuple) or len(key) != 2:
                raise FactorClusterError(f"malformed correlation key: {key!r}")
            a, b = key
            if a not in names or b not in names:
                raise FactorClusterError(
                    f"correlation references unknown factor(s): {key!r}"
                )
            if isinstance(rho, bool) or not isinstance(rho, (int, float)) \
                    or not math.isfinite(rho) or not -1.0 <= rho <= 1.0:
                raise FactorClusterError(f"invalid correlation value for {key!r}: {rho!r}")
            if a == b:
                continue
            corr[(a, b) if a < b else (b, a)] = float(rho)

    def pair_rho(a: str, b: str) -> Optional[float]:
        return corr.get((a, b) if a < b else (b, a))

    has_evidence = {fid: False for fid in names}
    parent = {fid: fid for fid in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        hi, lo = max(ra, rb), min(ra, rb)           # deterministic: smaller id is root
        parent[hi] = lo

    for family, fids in families.items():
        for x in range(len(fids)):
            for y in range(x + 1, len(fids)):
                rho = pair_rho(fids[x], fids[y])
                if rho is None:
                    continue
                has_evidence[fids[x]] = has_evidence[fids[y]] = True
                if abs(rho) >= rho_cut:             # negative high-|rho| also merges
                    union(fids[x], fids[y])         # (crowded-contra variants, §7.3)

    clusters = {}                                   # root -> sorted members
    for fid in names:
        clusters.setdefault(find(fid), []).append(fid)

    assignments = []
    for members in clusters.values():
        members.sort()
        fam = next(f for f, fids in families.items() if members[0] in fids)
        cluster_id = f"{fam.value}#{members[0]}"
        if len(members) > 1:
            method = METHOD_CORRELATION
        elif correlation is None:
            method = METHOD_SINGLETON
        else:
            method = METHOD_SINGLETON if has_evidence[members[0]] else METHOD_DECLARED
        for fid in members:
            assignments.append(FactorClusterAssignment(fid, fam, cluster_id, method))
    assignments.sort(key=lambda a: a.factor_id)
    return tuple(assignments)


def cluster_summary(assignments: Iterable[FactorClusterAssignment]) -> dict:
    """Deterministic per-family / per-cluster counts over an assignment tuple."""
    items = tuple(assignments)
    if any(not isinstance(a, FactorClusterAssignment) for a in items):
        raise TypeError("assignments must contain FactorClusterAssignment instances")
    by_cluster = {}
    for a in items:
        by_cluster.setdefault(a.cluster_id, []).append(a.factor_id)
    by_family = {}
    for fam in sorted({a.family for a in items}, key=lambda f: f.value):
        cids = sorted({a.cluster_id for a in items if a.family is fam})
        by_family[fam.value] = {
            "factors": sum(1 for a in items if a.family is fam),
            "clusters": len(cids),
            "cluster_ids": tuple(cids),
        }
    return {
        "n_factors": len(items),
        "n_clusters": len(by_cluster),
        "by_family": by_family,
        "by_cluster": {cid: tuple(sorted(m)) for cid, m in sorted(by_cluster.items())},
    }
