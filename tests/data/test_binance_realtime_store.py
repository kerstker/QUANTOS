import sqlite3

import pytest

from services.data.binance_realtime_store import (
    BinanceRealtimeStore,
    BinanceRealtimeStoreError,
)


def _snapshot(observed_at_ms=2_000):
    return {
        "schema_version": 1,
        "instrument_id": "BTCUSDT",
        "event_time_ms": 1_900,
        "knowledge_time_ms": observed_at_ms,
        "observed_at_ms": observed_at_ms,
        "book": {"bid": 100.0, "ask": 100.1},
    }


def test_store_is_idempotent_hash_chained_and_append_only(tmp_path):
    path = str(tmp_path / "binance.db")
    store = BinanceRealtimeStore(path, create=True)
    try:
        assert store.append_snapshot(_snapshot()) is True
        assert store.append_snapshot(_snapshot()) is False
        assert store.append_snapshot(_snapshot(3_000)) is True
        assert store.row_count() == 2
        assert store.latest("BTCUSDT")["observed_at_ms"] == 3_000
        assert store.chain_valid() is True
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._conn.execute(  # noqa: SLF001 - verifies the database authority
                "DELETE FROM binance_market_snapshots"
            )
    finally:
        store.close()


def test_store_rejects_conflicting_identity_and_future_event_time(tmp_path):
    store = BinanceRealtimeStore(str(tmp_path / "binance.db"), create=True)
    try:
        assert store.append_snapshot(_snapshot()) is True
        changed = _snapshot()
        changed["book"] = {"bid": 99.0, "ask": 101.0}
        with pytest.raises(BinanceRealtimeStoreError, match="reused"):
            store.append_snapshot(changed)
        future = _snapshot(4_000)
        future["event_time_ms"] = 4_001
        with pytest.raises(BinanceRealtimeStoreError, match="identity"):
            store.append_snapshot(future)

        bounded = _snapshot(5_000)
        bounded["event_time_ms"] = 5_010
        bounded["knowledge_time_ms"] = 5_010
        assert store.append_snapshot(bounded) is True

        excessive = _snapshot(6_000)
        excessive["event_time_ms"] = 36_001
        excessive["knowledge_time_ms"] = 36_001
        with pytest.raises(BinanceRealtimeStoreError, match="identity"):
            store.append_snapshot(excessive)
    finally:
        store.close()


def test_websocket_store_requires_bounded_receive_and_process_clocks(tmp_path):
    store = BinanceRealtimeStore(str(tmp_path / "binance.db"), create=True)
    try:
        complete = _snapshot(2_000)
        complete.update(
            {
                "provider": "BINANCE_PUBLIC_USDM_WEBSOCKET",
                "received_at_ms": 1_950,
                "processed_at_ms": 2_000,
            }
        )
        assert store.append_snapshot(complete) is True

        missing = _snapshot(3_000)
        missing["provider"] = "BINANCE_PUBLIC_USDM_WEBSOCKET"
        with pytest.raises(BinanceRealtimeStoreError, match="three clocks"):
            store.append_snapshot(missing)

        reversed_clocks = _snapshot(4_000)
        reversed_clocks.update(
            {
                "provider": "BINANCE_PUBLIC_USDM_WEBSOCKET",
                "received_at_ms": 4_100,
                "processed_at_ms": 4_000,
            }
        )
        with pytest.raises(BinanceRealtimeStoreError, match="three clocks"):
            store.append_snapshot(reversed_clocks)
    finally:
        store.close()


def test_existing_store_reopens_as_read_only_consumer(tmp_path):
    path = str(tmp_path / "binance.db")
    writer = BinanceRealtimeStore(path, create=True)
    try:
        assert writer.append_snapshot(_snapshot()) is True
        reader = BinanceRealtimeStore(path, create=False)
        try:
            assert reader.latest("BTCUSDT")["observed_at_ms"] == 2_000
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                reader.append_snapshot(_snapshot(3_000))
        finally:
            reader.close()
    finally:
        writer.close()


def test_latest_for_provider_uses_observation_time_not_append_order(tmp_path):
    store = BinanceRealtimeStore(str(tmp_path / "binance.db"), create=True)
    try:
        newest = _snapshot(3_000)
        newest.update(
            {
                "provider": "BINANCE_PUBLIC_USDM_WEBSOCKET",
                "received_at_ms": 2_900,
                "processed_at_ms": 3_000,
            }
        )
        delayed_older = _snapshot(2_000)
        delayed_older.update(
            {
                "provider": "BINANCE_PUBLIC_USDM_WEBSOCKET",
                "received_at_ms": 1_950,
                "processed_at_ms": 2_000,
            }
        )
        assert store.append_snapshot(newest) is True
        assert store.append_snapshot(delayed_older) is True

        selected = store.latest_for_provider(
            "BTCUSDT",
            "BINANCE_PUBLIC_USDM_WEBSOCKET",
            as_of_ms=3_000,
        )

        assert selected is not None
        assert selected["observed_at_ms"] == 3_000
    finally:
        store.close()


def test_verified_recent_rows_advances_from_trusted_checkpoint(tmp_path):
    store = BinanceRealtimeStore(str(tmp_path / "binance.db"), create=True)
    try:
        for observed in range(2_000, 7_000, 1_000):
            store.append_snapshot(_snapshot(observed))
        rows, full = store.verified_recent_rows(3)
        assert [row["observed_at_ms"] for row in rows] == [4_000, 5_000, 6_000]
        assert full == {
            "valid": True,
            "anchor_seq": 0,
            "verified_suffix_rows": 5,
            "verified_seq": 5,
            "verified_row_hash": full["verified_row_hash"],
        }

        store.append_snapshot(_snapshot(7_000))
        store.append_snapshot(_snapshot(8_000))
        rows, suffix = store.verified_recent_rows(
            2,
            checkpoint_seq=full["verified_seq"],
            checkpoint_row_hash=full["verified_row_hash"],
        )
        assert [row["observed_at_ms"] for row in rows] == [7_000, 8_000]
        assert suffix["valid"] is True
        assert suffix["anchor_seq"] == 5
        assert suffix["verified_suffix_rows"] == 2
        assert suffix["verified_seq"] == 7
    finally:
        store.close()


def test_chain_checkpoint_mismatch_fails_closed(tmp_path):
    store = BinanceRealtimeStore(str(tmp_path / "binance.db"), create=True)
    try:
        store.append_snapshot(_snapshot())
        result = store.verify_chain_from(
            checkpoint_seq=1,
            checkpoint_row_hash="0" * 64,
        )
        assert result["valid"] is False
        assert "anchor mismatch" in result["error"]
        with pytest.raises(BinanceRealtimeStoreError, match="anchor mismatch"):
            store.verified_recent_rows(
                1,
                checkpoint_seq=1,
                checkpoint_row_hash="0" * 64,
            )
    finally:
        store.close()
