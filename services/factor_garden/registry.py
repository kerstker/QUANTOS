"""Factor Garden registry (727 §6) — a thin garden-invariant boundary over the
battle-tested :class:`services.features.registry_store.SqliteFeatureRegistryStore`.

The store does persistence, versioning, and the tamper-evident hash-chained history;
this wrapper only re-enforces the 727 §6.1/§8.1 garden invariants at the boundary
(defense in depth: a hand-built FeatureDefinition missing hypothesis/family/datasets
is rejected even though the contract alone allows it) and exposes family-scoped reads.

No wall clock: every mutating call takes a caller-supplied ``recorded_at_ms``.
"""
from __future__ import annotations

from typing import Union

from quantos.contracts.feature import (
    FactorFamily,
    FeatureDefinition,
    FeatureRegistryItem,
    FeatureStatus,
)
from services.features.registry_store import SqliteFeatureRegistryStore

from .definitions import FactorDefinitionError, factor_content_hash


class FactorGardenRegistry:
    """Garden boundary over SqliteFeatureRegistryStore (persistence/history/chain)."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        self._store = SqliteFeatureRegistryStore(path, create=create)

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> "FactorGardenRegistry":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    @staticmethod
    def _enforce_garden(defn: FeatureDefinition) -> None:
        """Re-check the 727 §6.1/§8.1 invariants — a non-garden-built definition fails."""
        if not isinstance(defn, FeatureDefinition):
            raise TypeError("defn must be a FeatureDefinition")
        if defn.factor_family_id is None:
            raise FactorDefinitionError("garden factor requires factor_family_id")
        if not defn.economic_hypothesis.strip():
            raise FactorDefinitionError("garden factor requires a non-empty economic_hypothesis")
        if not defn.required_datasets:
            raise FactorDefinitionError("garden factor requires non-empty required_datasets")
        if not defn.expression_hash:
            raise FactorDefinitionError("garden factor requires a non-empty expression_hash")

    def register(
        self,
        defn: FeatureDefinition,
        *,
        factor_version: str = "1.0.0",
        recorded_at_ms: int,
        leakage_passed: bool = False,
        source_dataset_id: str = "",
        source_version: str = "",
    ) -> FeatureRegistryItem:
        """Register a garden factor; duplicate (factor_id, factor_version) is rejected."""
        self._enforce_garden(defn)
        if not isinstance(factor_version, str) or not factor_version.strip():
            raise FactorDefinitionError("factor_version must be a non-empty string")
        item = FeatureRegistryItem(
            definition=defn,
            version=factor_version.strip(),
            owner=defn.creator_actor,
            status=FeatureStatus.DRAFT,
        )
        lineage = {
            "factor_content_hash": factor_content_hash(defn, factor_version=item.version),
        }
        self._store.register(
            item,
            leakage_passed=bool(leakage_passed),
            source_dataset_id=source_dataset_id or None,
            source_version=source_version or None,
            lineage=lineage,
            recorded_at_ms=recorded_at_ms,
        )
        return self._store.get(defn.feature_id, item.version)

    def set_status(
        self,
        factor_id: str,
        factor_version: str,
        status: FeatureStatus,
        *,
        recorded_at_ms: int = 0,
    ) -> FeatureRegistryItem:
        return self._store.set_status(
            factor_id, factor_version, status, recorded_at_ms=recorded_at_ms
        )

    def get(self, factor_id: str, factor_version: str) -> FeatureRegistryItem:
        return self._store.get(factor_id, factor_version)

    def versions(self, factor_id: str) -> list[str]:
        return self._store.versions(factor_id)

    def by_family(
        self, family: Union[FactorFamily, str]
    ) -> list[FeatureDefinition]:
        """Current definitions in one 727 §6.4 family (any version, id/version order)."""
        if isinstance(family, FactorFamily):
            value = family.value
        elif isinstance(family, str) and family in {f.value for f in FactorFamily}:
            value = family
        else:
            raise FactorDefinitionError(f"unknown factor family: {family!r}")
        return [
            item.definition
            for item in self._store.all_items()
            if item.definition.factor_family_id == value
        ]

    def audit(self, factor_id: str, factor_version: str) -> dict:
        return self._store.audit(factor_id, factor_version)
