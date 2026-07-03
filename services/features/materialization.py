"""Feature materialization bridge (Rank #15 adoption).

Rolling features are recomputed from raw bars on every research run. This writes a computed rolling
feature THROUGH to the PIT value store once, so later runs read it back point-in-time (as_of) instead
of recomputing — the read is a faithful cache of the direct computation. Warmup (None) rows are not
materialized (nothing to cache). Pure/deterministic (the store does its own idempotent INSERT)."""
from __future__ import annotations

import statistics
from typing import Callable, Optional, Sequence

from quantos.contracts.data import Bar
from services.features.calculation import rolling_feature
from services.features.value_store import SqliteFeatureValueStore


def materialize_rolling_feature(store: SqliteFeatureValueStore, feature_id: str, version: str,
                                bars: Sequence[Bar], window: int,
                                agg: Callable[[list], float] = statistics.fmean) -> int:
    """Compute a rolling feature and write its (non-warmup) values through to the PIT value store.
    Returns the number of values materialized. Idempotent (the store's INSERT OR IGNORE)."""
    values = rolling_feature(feature_id, bars, window, agg)
    return store.add_many([v for v in values if v.value is not None], version=version)


def read_feature_as_of(store: SqliteFeatureValueStore, feature_id: str, version: str,
                       instrument_id: str, as_of_time: int) -> Optional[float]:
    """Latest materialized value for (feature, version, instrument) knowable by as_of_time (PIT),
    or None if not materialized / not yet knowable."""
    fv = store.latest_as_of(feature_id, version, instrument_id, as_of_time)
    return fv.value if fv is not None else None
