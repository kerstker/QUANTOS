from __future__ import annotations

import sqlite3

import pytest

from services.data.binance_systemic_store import (
    BinanceSystemicStore,
    BinanceSystemicStoreError,
)


def _observation(*, event_time_ms: int = 1000, value: str = "LOW"):
    return {
        "schema_version": 1,
        "provider": "BINANCE_PUBLIC_USDM",
        "dataset": "adl_risk",
        "instrument_id": "BTCUSDT",
        "event_time_ms": event_time_ms,
        "knowledge_time_ms": 2000,
        "value": value,
    }


def test_store_is_idempotent_hash_chained_and_pit_readable(tmp_path):
    store = BinanceSystemicStore(str(tmp_path / "systemic.db"), create=True)
    try:
        assert store.append(_observation()) is True
        assert store.append(_observation()) is False
        assert store.append(_observation(event_time_ms=1500, value="MEDIUM")) is True
        assert store.row_count() == 2
        assert store.row_count("adl_risk") == 2
        assert store.latest_event_time("adl_risk", "BTCUSDT") == 1500
        assert store.latest("adl_risk", "BTCUSDT")["value"] == "MEDIUM"
        assert store.latest("adl_risk", "BTCUSDT", as_of_ms=1999) is None
        assert store.latest("adl_risk", "BTCUSDT", as_of_ms=2000)["value"] == "MEDIUM"
        assert store.chain_valid() is True
    finally:
        store.close()


def test_store_rejects_identity_reuse_and_mutation(tmp_path):
    path = str(tmp_path / "systemic.db")
    store = BinanceSystemicStore(path, create=True)
    try:
        store.append(_observation())
        with pytest.raises(BinanceSystemicStoreError, match="different content"):
            store.append(_observation(value="HIGH"))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._conn.execute(  # noqa: SLF001 - adversarial storage contract test
                "UPDATE binance_systemic_observations SET instrument_id='ETHUSDT'"
            )
    finally:
        store.close()


def test_store_rejects_future_knowledge_and_detects_tamper(tmp_path):
    path = str(tmp_path / "systemic.db")
    store = BinanceSystemicStore(path, create=True)
    try:
        invalid = _observation(event_time_ms=3000)
        with pytest.raises(BinanceSystemicStoreError, match="identity"):
            store.append(invalid)
        store.append(_observation())
        store._conn.execute("DROP TRIGGER binance_systemic_observations_no_update")
        store._conn.execute(
            "UPDATE binance_systemic_observations SET payload_json='{}'"
        )
        store._conn.commit()
        assert store.chain_valid() is False
    finally:
        store.close()
