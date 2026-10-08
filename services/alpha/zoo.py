"""Alpha Zoo (Alpha Factory upgrade Phase 3) — the query surface over the Alpha Registry.

The registry stores and versions assets; the zoo makes them FINDABLE. It filters each alpha's latest
version by the dimensions the audit found missing — name/expression substring, universe, generator,
status, cluster, tag — plus scientific floors (Deflated Sharpe, net Sharpe, promotable-only). All
filters AND together; results are deterministically ordered by alpha_id. Read-only over the registry.
"""
from __future__ import annotations

from typing import Optional

from quantos.contracts.alpha_asset import AlphaAsset
from quantos.contracts.research import AlphaState
from .registry import AlphaAssetRegistry


class AlphaZoo:
    def __init__(self, registry: AlphaAssetRegistry) -> None:
        self.registry = registry

    def search(
        self,
        *,
        name_contains: Optional[str] = None,
        expression_contains: Optional[str] = None,
        universe: Optional[str] = None,
        generator: Optional[str] = None,
        status: Optional[AlphaState] = None,
        cluster_id: Optional[str] = None,
        tag: Optional[str] = None,
        min_dsr: Optional[float] = None,
        min_net_sharpe: Optional[float] = None,
        promotable_only: bool = False,
    ) -> tuple:
        """Latest-version assets matching ALL supplied filters, sorted by alpha_id. Metric floors skip
        assets that lack an evaluation (None metric never satisfies a floor — fail-closed)."""
        out = []
        for a in self.registry.all_latest():
            if name_contains is not None and name_contains not in a.name:
                continue
            if expression_contains is not None and expression_contains not in a.expression:
                continue
            if universe is not None and a.universe != universe:
                continue
            if generator is not None and a.generator != generator:
                continue
            if status is not None and a.status != status:
                continue
            if cluster_id is not None and a.cluster_id != cluster_id:
                continue
            if tag is not None and tag not in a.tags:
                continue
            if min_dsr is not None and (a.deflated_sharpe is None or a.deflated_sharpe < min_dsr):
                continue
            if min_net_sharpe is not None and (a.net_sharpe is None or a.net_sharpe < min_net_sharpe):
                continue
            if promotable_only and not a.is_promotable:
                continue
            out.append(a)
        return tuple(sorted(out, key=lambda x: x.alpha_id))

    def by_cluster(self, cluster_id: str) -> tuple:
        return self.search(cluster_id=cluster_id)

    def by_tag(self, tag: str) -> tuple:
        return self.search(tag=tag)

    def top_by(self, metric: str, n: int = 10) -> tuple:
        """The n assets with the highest value of `metric` (an AlphaAsset attribute/property, e.g.
        'deflated_sharpe' or 'net_sharpe'); assets whose metric is None are excluded. Ties -> alpha_id."""
        scored = []
        for a in self.registry.all_latest():
            v = getattr(a, metric, None)
            if v is not None:
                scored.append((v, a))
        scored.sort(key=lambda t: (-t[0], t[1].alpha_id))
        return tuple(a for _v, a in scored[:max(0, n)])
