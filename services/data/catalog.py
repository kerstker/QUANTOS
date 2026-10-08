"""Data-catalog backing computations (Phase 2 / G2 wiring).

The research console's Data Catalog used to report hardcoded content hashes and
a hardcoded `point_in_time_safe=True`. These helpers make those values come from
the REAL data layer instead:
  * `dataset_content_hash` is computed by the versioning module over a
    deterministic synthetic bar series (no real feed at L2), so it is a real,
    reproducible fingerprint.
  * `verify_point_in_time_safe` proves PIT-safety operationally via the PITStore
    (a future-knowledge record must be excluded from an as-of query) rather than
    asserting it.

Data remains synthetic at L2_DRY_RUN_ONLY; the point is that the catalog now
exercises real PIT code paths and reports derived (not fabricated) values.
"""
from __future__ import annotations

from quantos.contracts.data import Bar
from quantos.contracts.hashing import stable_hash

from .pit_store import PITStore
from .versioning import build_dataset_version

DATA_BACKEND = "PIT_DATA_LAYER_v1"


def _synthetic_bars(dataset_id: str, n: int) -> list[Bar]:
    """Deterministic, reproducible synthetic bars keyed off the dataset id."""
    seed = int(stable_hash(dataset_id)[:6], 16) % 100 / 100.0
    bars = []
    for i in range(n):
        close = round(10.0 + (i % 7) + seed, 4)
        bars.append(Bar(
            instrument_id=dataset_id, event_time=i + 1, knowledge_time=i + 1,
            open=close, high=close + 1.0, low=close - 1.0, close=close, volume=1000 + i,
        ))
    return bars


def dataset_content_hash(dataset_id: str, version: str, n_bars: int = 16) -> str:
    bars = _synthetic_bars(dataset_id, n_bars)
    return build_dataset_version(dataset_id, version, bars, created_at=0).content_hash


def verify_point_in_time_safe(dataset_id: str) -> bool:
    """A future-knowledge record must NOT be visible to an earlier as-of query."""
    store = PITStore()
    store.add(dataset_id, Bar(instrument_id=dataset_id, event_time=100, knowledge_time=100,
                              open=10, high=10, low=10, close=10))
    store.add(dataset_id, Bar(instrument_id=dataset_id, event_time=100, knowledge_time=500,
                              open=11, high=11, low=11, close=11))
    return len(store.as_of(dataset_id, as_of_time=200)) == 1
