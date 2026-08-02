"""Factor/data dependency graph (727 §6 / Factor Garden F4, 2026-07-27).

The graph generalizes what the drivers hard-code today (forward_track_advance's
raw_lineage_block_reason / live_track_advance's batch_skip_reason: a failed dependency
pauses the decision). Edges: dataset -> factor (from the registry's required_datasets)
and factor -> alpha (declared by the caller — alphas are not garden objects).

Pure, immutable after construction, deterministic (every read returns sorted tuples).
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Iterable, Mapping, Optional

from quantos.contracts.feature import FeatureDefinition


class DependencyGraph:
    """Immutable dataset->factor->alpha adjacency built from garden definitions."""

    def __init__(
        self,
        factor_datasets: Mapping[str, Iterable[str]],
        alpha_factors: Optional[Mapping[str, Iterable[str]]] = None,
        known_datasets: Optional[Iterable[str]] = None,
    ) -> None:
        fd = {}
        for fid, datasets in factor_datasets.items():
            if not isinstance(fid, str) or not fid.strip():
                raise ValueError("factor ids must be non-empty strings")
            ds = tuple(datasets)
            if any(not isinstance(d, str) or not d.strip() for d in ds):
                raise ValueError(f"datasets for factor {fid!r} must be non-empty strings")
            fd[fid] = tuple(sorted(set(ds)))
        af = {}
        for aid, factors in (alpha_factors or {}).items():
            if not isinstance(aid, str) or not aid.strip():
                raise ValueError("alpha ids must be non-empty strings")
            fs = tuple(factors)
            if any(not isinstance(f, str) or not f.strip() for f in fs):
                raise ValueError(f"factors for alpha {aid!r} must be non-empty strings")
            af[aid] = tuple(sorted(set(fs)))
        self._factor_datasets = MappingProxyType(fd)
        self._alpha_factors = MappingProxyType(af)
        self._known_datasets = (
            None if known_datasets is None else frozenset(known_datasets)
        )

    @classmethod
    def from_definitions(
        cls,
        defns: Iterable[FeatureDefinition],
        alpha_links: Optional[Mapping[str, Iterable[str]]] = None,
        known_datasets: Optional[Iterable[str]] = None,
    ) -> "DependencyGraph":
        """Build from registered garden definitions (factor_id -> required_datasets)."""
        defs = tuple(defns)
        if any(not isinstance(d, FeatureDefinition) for d in defs):
            raise TypeError("defns must contain FeatureDefinition instances")
        return cls(
            {d.name: d.required_datasets for d in defs},
            alpha_factors=alpha_links,
            known_datasets=known_datasets,
        )

    # ---- reads -----------------------------------------------------------------
    @property
    def factor_ids(self) -> tuple:
        return tuple(sorted(self._factor_datasets))

    @property
    def alpha_ids(self) -> tuple:
        return tuple(sorted(self._alpha_factors))

    def factors_for_dataset(self, dataset_id: str) -> tuple:
        """Factors that declare `dataset_id` in required_datasets (sorted)."""
        return tuple(sorted(
            fid for fid, ds in self._factor_datasets.items() if dataset_id in ds
        ))

    def datasets_for_factor(self, factor_id: str) -> tuple:
        return self._factor_datasets.get(factor_id, ())

    def factors_for_alpha(self, alpha_id: str) -> tuple:
        return self._alpha_factors.get(alpha_id, ())

    def dependents_of_dataset(self, dataset_id: str) -> tuple:
        """(factor_ids, alpha_ids) impacted when `dataset_id` fails — factors directly,
        alphas transitively through any impacted factor. Both sorted tuples."""
        factors = self.factors_for_dataset(dataset_id)
        impacted = set(factors)
        alphas = tuple(sorted(
            aid for aid, fs in self._alpha_factors.items()
            if impacted & set(fs)
        ))
        return factors, alphas

    def topo_factors(self) -> tuple:
        """Deterministic dependency order of factors. Honest note: the §6 graph has NO
        factor->factor edges (composite-of-factors is not a garden concept yet), so the
        topological order is simply the sorted factor ids — this exists so callers have
        one stable evaluation order to build on, not to claim real depth."""
        return self.factor_ids

    # ---- validation --------------------------------------------------------------
    def validate(self) -> dict:
        """Dangling references, fail-closed for the caller to reject on. Returns
        {"alphas_to_missing_factors": tuple, "factors_to_missing_datasets": tuple}
        (both sorted; empty dict = valid). Dataset-level dangling can only be judged
        when known_datasets was declared at construction — without it the external
        dataset universe is honestly not the graph's to judge (check skipped)."""
        issues = {}
        missing_f = tuple(sorted(
            (aid, f)
            for aid, fs in self._alpha_factors.items()
            for f in fs if f not in self._factor_datasets
        ))
        if missing_f:
            issues["alphas_to_missing_factors"] = missing_f
        if self._known_datasets is not None:
            missing_d = tuple(sorted(
                (fid, d)
                for fid, ds in self._factor_datasets.items()
                for d in ds if d not in self._known_datasets
            ))
            if missing_d:
                issues["factors_to_missing_datasets"] = missing_d
        return issues
