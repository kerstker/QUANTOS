"""Rank #15 adoption — materialized rolling features read back bit-identically to a direct
recompute, point-in-time. This makes the value store a faithful cache (compute once, read PIT)."""
import os
import statistics

from quantos.contracts.data import Bar
from services.features.calculation import rolling_feature
from services.features.materialization import materialize_rolling_feature, read_feature_as_of
from services.features.value_store import SqliteFeatureValueStore


def _bars(closes, inst="BTC"):
    return [Bar(instrument_id=inst, event_time=100 + i, knowledge_time=100 + i,
                open=c, high=c, low=c, close=c, volume=1.0) for i, c in enumerate(closes)]


def test_materialized_reads_match_direct_compute_bit_identical():
    store = SqliteFeatureValueStore(":memory:", create=True)
    bars = _bars([10.0, 11.0, 12.0, 13.0, 14.0])
    n = materialize_rolling_feature(store, "sma_3", "v1", bars, window=3)
    computed = [v for v in rolling_feature("sma_3", bars, window=3) if v.value is not None]
    assert n == len(computed)                                # 3 non-warmup values (positions 2,3,4)
    for cv in computed:
        got = read_feature_as_of(store, "sma_3", "v1", "BTC", as_of_time=cv.knowledge_time)
        assert got == cv.value                               # bit-identical, no drift


def test_materialized_read_is_point_in_time():
    store = SqliteFeatureValueStore(":memory:", create=True)
    bars = _bars([10.0, 11.0, 12.0, 13.0, 14.0])             # event_times 100..104
    materialize_rolling_feature(store, "sma_3", "v1", bars, window=3)
    # at as_of 102 only the first materialized value (kt=102) is knowable
    assert read_feature_as_of(store, "sma_3", "v1", "BTC", 102) == statistics.fmean([10.0, 11.0, 12.0])
    assert read_feature_as_of(store, "sma_3", "v1", "BTC", 101) is None   # before any non-warmup value


def test_materialization_is_idempotent():
    store = SqliteFeatureValueStore(":memory:", create=True)
    bars = _bars([10.0, 11.0, 12.0, 13.0])
    first = materialize_rolling_feature(store, "sma_2", "v1", bars, window=2)
    second = materialize_rolling_feature(store, "sma_2", "v1", bars, window=2)
    assert first > 0 and second == 0                         # re-materialization is a no-op (INSERT OR IGNORE)


def test_versions_do_not_collide():
    store = SqliteFeatureValueStore(":memory:", create=True)
    bars = _bars([10.0, 20.0, 30.0])
    materialize_rolling_feature(store, "f", "v1", bars, window=2, agg=statistics.fmean)
    materialize_rolling_feature(store, "f", "v2", bars, window=2, agg=max)
    assert read_feature_as_of(store, "f", "v1", "BTC", 101) == 15.0     # mean(10,20)
    assert read_feature_as_of(store, "f", "v2", "BTC", 101) == 20.0     # max(10,20)
