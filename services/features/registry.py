"""Feature registry (Phase 3) — versioned, with status transitions."""
from __future__ import annotations

from quantos.contracts.feature import FeatureRegistryItem, FeatureStatus


class FeatureRegistry:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], FeatureRegistryItem] = {}

    def register(self, item: FeatureRegistryItem) -> None:
        key = (item.definition.feature_id, item.version)
        if key in self._items:
            raise ValueError(f"already registered: {key}")
        self._items[key] = item

    def get(self, feature_id: str, version: str) -> FeatureRegistryItem:
        return self._items[(feature_id, version)]

    def has(self, feature_id: str) -> bool:
        return any(fid == feature_id for fid, _ in self._items)

    def versions(self, feature_id: str) -> list[str]:
        return sorted(v for fid, v in self._items if fid == feature_id)

    def set_status(self, feature_id: str, version: str, status: FeatureStatus) -> FeatureRegistryItem:
        key = (feature_id, version)
        updated = self._items[key].model_copy(update={"status": status})
        self._items[key] = updated
        return updated
