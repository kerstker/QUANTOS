"""Feature-set builder (Phase 3)."""
from __future__ import annotations

from typing import Sequence

from quantos.contracts.feature import FeatureSet

from .registry import FeatureRegistry


def build_feature_set(set_id: str, feature_ids: Sequence[str], registry: FeatureRegistry,
                      version: str = "v1") -> FeatureSet:
    missing = [fid for fid in feature_ids if not registry.has(fid)]
    if missing:
        raise ValueError(f"unknown features (not in registry): {missing}")
    return FeatureSet(set_id=set_id, feature_ids=tuple(feature_ids), version=version)
