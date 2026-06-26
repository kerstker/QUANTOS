"""Declarative multi-source data adapter (offline, no network).

Today, adding a data source means writing a bespoke parser (coingecko_fetch.parse_ohlc,
market_csv...). This adapter makes a NEW source a DECLARATION: a SourceSpec (field map +
time unit + knowledge lag) turns any tabular record into canonical OHLCV(+volume) rows the
existing fail-closed ingest seam already accepts. So a richer feed (volume, another exchange,
equities) plugs in with data, not code. Network stays out-of-gate; this layer is pure/offline.
"""
import pytest
from services.data.adapters import (
    SourceSpec, normalize_records, register_source, get_source, list_sources)
from services.data.market_provider import ingest_provider_rows
from services.data.persistent_store import SqlitePITStore

SPEC = SourceSpec(name="demo-ohlcv-ms", asset_class="CRYPTO", time_unit="ms",
                  field_map={"instrument_id": "sym", "event_time": "ts", "open": "o",
                             "high": "h", "low": "l", "close": "c", "volume": "v"},
                  knowledge_lag=60000)


def test_normalize_maps_and_carries_volume():
    raw = [{"sym": "BTC", "ts": 1700000000000, "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 1234.5}]
    rows = normalize_records(raw, SPEC)
    assert rows[0]["instrument_id"] == "BTC" and rows[0]["volume"] == 1234.5
    assert rows[0]["knowledge_time"] == 1700000000000 + 60000     # knowable at close + lag


def test_single_instrument_spec_without_id_column():
    spec = SourceSpec(name="eq", asset_class="EQUITY", time_unit="yyyymmdd",
                      field_map={"event_time": "date", "open": "o", "high": "h",
                                 "low": "l", "close": "c", "volume": "v"},
                      instrument_id="AAPL")
    rows = normalize_records([{"date": 20240102, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}], spec)
    assert rows[0]["instrument_id"] == "AAPL" and rows[0]["event_time"] == 20240102


def test_spec_validates_required_fields():
    with pytest.raises(ValueError):
        SourceSpec(name="bad", asset_class="X", time_unit="ms", field_map={"open": "o"})  # missing keys
    with pytest.raises(ValueError):
        SourceSpec(name="noid", asset_class="X", time_unit="ms",
                   field_map={"event_time": "t", "open": "o", "high": "h", "low": "l", "close": "c"})


def test_normalize_fail_closed_on_missing_raw_field():
    with pytest.raises(ValueError):
        normalize_records([{"sym": "BTC", "ts": 1, "o": 1, "h": 2, "l": 0.5}], SPEC)   # no 'c'


def test_registry_register_get_list():
    register_source(SPEC)
    assert "demo-ohlcv-ms" in list_sources()
    assert get_source("demo-ohlcv-ms").asset_class == "CRYPTO"
    with pytest.raises(ValueError):
        register_source(SPEC)                                     # duplicate name fails closed


def test_end_to_end_normalize_then_ingest_with_volume():
    store = SqlitePITStore(":memory:", create=True)
    raw = [{"sym": "BTC", "ts": 1700000000000, "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 1000},
           {"sym": "BTC", "ts": 1700003600000, "o": 100.5, "h": 102, "l": 100, "c": 101, "v": 1100}]
    rep = ingest_provider_rows(normalize_records(raw, SPEC), store, time_unit="ms")
    assert rep.bars_ingested == 2 and rep.rejected == ()
    bars = store.as_of("BTC", as_of_time=9_999_999_999_999)
    assert any(b.volume == 1000.0 for b in bars)                  # volume carried into the PIT store
