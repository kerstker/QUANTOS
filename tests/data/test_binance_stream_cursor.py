from __future__ import annotations

import sqlite3
import hashlib
import json

import pytest

from services.data.binance_stream_cursor import (
    StreamCursorState,
    StreamCursorStore,
    StreamCursorStoreError,
)


def _observe(
    store: StreamCursorStore,
    *,
    first: int,
    final: int,
    previous: int,
    event_time_ms: int,
    observed_at_ms: int | None = None,
    channel: str = "depth20",
    instrument_id: str = "BTCUSDT",
):
    return store.observe(
        channel=channel,
        instrument_id=instrument_id,
        first_update_id=first,
        final_update_id=final,
        previous_final_update_id=previous,
        event_time_ms=event_time_ms,
        observed_at_ms=event_time_ms + 1 if observed_at_ms is None else observed_at_ms,
    )


def test_contiguous_cursor_is_persistent_idempotent_and_ignores_old_frames(tmp_path) -> None:
    path = str(tmp_path / "stream.db")
    store = StreamCursorStore(path, create=True)
    try:
        first = _observe(store, first=10, final=12, previous=9, event_time_ms=1_000)
        assert first.state is StreamCursorState.CONTIGUOUS
        assert first.cursor_update_id == 12
        assert store.event_count() == 1

        second = _observe(store, first=13, final=15, previous=12, event_time_ms=1_100)
        assert second.state is StreamCursorState.CONTIGUOUS
        assert second.cursor_update_id == 15
        assert store.event_count() == 2

        assert _observe(
            store, first=13, final=15, previous=12, event_time_ms=1_100
        ) == second
        assert _observe(
            store, first=9, final=11, previous=8, event_time_ms=1_050
        ) == second
        assert store.event_count() == 2
        assert store.verify_chain()["valid"] is True
    finally:
        store.close()

    reopened = StreamCursorStore(path)
    try:
        assert reopened.latest("depth20", "BTCUSDT") == second
    finally:
        reopened.close()


def test_cursor_chain_verification_resumes_from_exact_checkpoint(tmp_path) -> None:
    store = StreamCursorStore(str(tmp_path / "stream.db"), create=True)
    try:
        _observe(store, first=10, final=12, previous=9, event_time_ms=1_000)
        checkpoint = store.verify_chain_from()
        _observe(store, first=13, final=15, previous=12, event_time_ms=1_100)

        suffix = store.verify_chain_from(
            checkpoint_seq=checkpoint["verified_seq"],
            checkpoint_row_hash=checkpoint["verified_row_hash"],
        )

        assert suffix["valid"] is True
        assert suffix["anchor_seq"] == 1
        assert suffix["verified_suffix_events"] == 1
        assert suffix["verified_seq"] == 2
    finally:
        store.close()


def test_watermark_uses_supplied_cursor_chain_verification(tmp_path, monkeypatch) -> None:
    store = StreamCursorStore(str(tmp_path / "stream.db"), create=True)
    try:
        _observe(store, first=10, final=12, previous=9, event_time_ms=1_000)
        verification = store.verify_chain_from()

        def full_scan_must_not_run():
            raise AssertionError("watermark repeated a full cursor-chain scan")

        monkeypatch.setattr(store, "verify_chain", full_scan_must_not_run)
        watermark = store.trading_watermark(
            (("depth20", "BTCUSDT"),),
            chain_verification=verification,
        )

        assert watermark["publishable"] is True
        assert watermark["chain"] == verification
    finally:
        store.close()


def test_watermark_rejects_unverified_cursor_chain_suffix(tmp_path) -> None:
    store = StreamCursorStore(str(tmp_path / "stream.db"), create=True)
    try:
        _observe(store, first=10, final=12, previous=9, event_time_ms=1_000)
        stale = store.verify_chain_from()
        _observe(store, first=13, final=15, previous=12, event_time_ms=1_100)

        watermark = store.trading_watermark(
            (("depth20", "BTCUSDT"),),
            chain_verification=stale,
        )

        assert watermark["publishable"] is False
        assert "CURSOR_CHAIN_UNVERIFIED_SUFFIX" in watermark["blockers"]
    finally:
        store.close()


def test_gap_blocks_watermark_until_complete_rest_snapshot_recovery(tmp_path) -> None:
    store = StreamCursorStore(str(tmp_path / "stream.db"), create=True)
    try:
        _observe(store, first=10, final=12, previous=9, event_time_ms=1_000)
        gap = _observe(store, first=20, final=22, previous=19, event_time_ms=1_100)
        assert gap.state is StreamCursorState.GAP_DETECTED
        assert gap.cursor_update_id == 12
        assert gap.pending_through_update_id == 22
        assert gap.reason_code == "SEQUENCE_GAP"

        blocked = store.trading_watermark((("depth20", "BTCUSDT"),))
        assert blocked["publishable"] is False
        assert blocked["watermark_ms"] is None
        assert blocked["blockers"] == ["depth20:BTCUSDT:GAP_DETECTED"]

        recovering = store.begin_recovery(
            "depth20", "BTCUSDT", observed_at_ms=1_200
        )
        assert recovering.state is StreamCursorState.RECOVERING

        incomplete = store.complete_recovery(
            "depth20",
            "BTCUSDT",
            snapshot_update_id=21,
            event_time_ms=1_210,
            observed_at_ms=1_220,
        )
        assert incomplete.state is StreamCursorState.BLOCKED
        assert incomplete.reason_code == "REST_COVERAGE_INCOMPLETE"
        assert incomplete.cursor_update_id == 12

        store.begin_recovery("depth20", "BTCUSDT", observed_at_ms=1_230)
        recovered = store.complete_recovery(
            "depth20",
            "BTCUSDT",
            snapshot_update_id=25,
            event_time_ms=1_240,
            observed_at_ms=1_250,
        )
        assert recovered.state is StreamCursorState.RECOVERED
        assert recovered.cursor_update_id == 25
        assert recovered.pending_through_update_id is None

        watermark = store.trading_watermark((("depth20", "BTCUSDT"),))
        assert watermark["publishable"] is True
        assert watermark["watermark_ms"] == 1_240

        stale = _observe(
            store,
            first=23,
            final=24,
            previous=22,
            event_time_ms=1_230,
        )
        assert stale.event_id == recovered.event_id

        continued = _observe(
            store,
            first=24,
            final=27,
            previous=23,
            event_time_ms=1_300,
        )
        assert continued.state is StreamCursorState.CONTIGUOUS
        assert continued.cursor_update_id == 27
    finally:
        store.close()


def test_first_post_recovery_event_must_cover_snapshot_or_connect_by_pu(
    tmp_path,
) -> None:
    store = StreamCursorStore(str(tmp_path / "stream.db"), create=True)
    try:
        _observe(store, first=10, final=12, previous=9, event_time_ms=1_000)
        _observe(store, first=20, final=22, previous=19, event_time_ms=1_100)
        store.begin_recovery("depth20", "BTCUSDT", observed_at_ms=1_200)
        recovered = store.complete_recovery(
            "depth20",
            "BTCUSDT",
            snapshot_update_id=25,
            event_time_ms=1_240,
            observed_at_ms=1_250,
        )
        assert recovered.cursor_update_id == 25

        missed = _observe(
            store,
            first=27,
            final=30,
            previous=26,
            event_time_ms=1_300,
        )
        assert missed.state is StreamCursorState.GAP_DETECTED
        assert missed.reason_code == "SEQUENCE_GAP"
    finally:
        store.close()


def test_partial_depth_continuity_uses_previous_final_token_not_dense_ranges(
    tmp_path,
) -> None:
    store = StreamCursorStore(str(tmp_path / "stream.db"), create=True)
    try:
        _observe(store, first=10, final=12, previous=9, event_time_ms=1_000)
        jumped = _observe(
            store,
            first=20,
            final=22,
            previous=12,
            event_time_ms=1_100,
        )
        assert jumped.state is StreamCursorState.CONTIGUOUS
        assert jumped.cursor_update_id == 22
    finally:
        store.close()


@pytest.mark.parametrize("error_code", ["HTTP_429", "TIMEOUT", "EMPTY_REST_RESULT"])
def test_failed_recovery_remains_blocked(error_code, tmp_path) -> None:
    store = StreamCursorStore(str(tmp_path / "stream.db"), create=True)
    try:
        _observe(store, first=10, final=12, previous=9, event_time_ms=1_000)
        _observe(store, first=20, final=22, previous=19, event_time_ms=1_100)
        store.begin_recovery("depth20", "BTCUSDT", observed_at_ms=1_200)
        failed = store.fail_recovery(
            "depth20",
            "BTCUSDT",
            observed_at_ms=1_300,
            error_code=error_code,
        )
        assert failed.state is StreamCursorState.BLOCKED
        assert failed.reason_code == error_code
        assert store.trading_watermark((("depth20", "BTCUSDT"),))["publishable"] is False
    finally:
        store.close()


def test_observation_during_recovery_extends_required_coverage(tmp_path) -> None:
    store = StreamCursorStore(str(tmp_path / "stream.db"), create=True)
    try:
        _observe(store, first=10, final=12, previous=9, event_time_ms=1_000)
        _observe(store, first=20, final=22, previous=19, event_time_ms=1_100)
        store.begin_recovery("depth20", "BTCUSDT", observed_at_ms=1_200)
        pending = _observe(
            store, first=23, final=30, previous=22, event_time_ms=1_250
        )
        assert pending.state is StreamCursorState.RECOVERING
        assert pending.pending_through_update_id == 30

        incomplete = store.complete_recovery(
            "depth20",
            "BTCUSDT",
            snapshot_update_id=25,
            event_time_ms=1_260,
            observed_at_ms=1_270,
        )
        assert incomplete.state is StreamCursorState.BLOCKED
    finally:
        store.close()


def test_event_time_reversal_blocks_even_when_sequence_advances(tmp_path) -> None:
    store = StreamCursorStore(str(tmp_path / "stream.db"), create=True)
    try:
        _observe(store, first=10, final=12, previous=9, event_time_ms=1_000)
        reversed_time = _observe(
            store,
            first=13,
            final=14,
            previous=12,
            event_time_ms=999,
            observed_at_ms=1_100,
        )
        assert reversed_time.state is StreamCursorState.BLOCKED
        assert reversed_time.reason_code == "EVENT_TIME_REVERSED"
        assert reversed_time.cursor_update_id == 12
    finally:
        store.close()


def test_multi_channel_watermark_is_minimum_and_missing_is_blocking(tmp_path) -> None:
    store = StreamCursorStore(str(tmp_path / "stream.db"), create=True)
    try:
        _observe(store, first=10, final=12, previous=9, event_time_ms=1_000)
        _observe(
            store,
            first=20,
            final=22,
            previous=19,
            event_time_ms=900,
            channel="rpiDepth",
        )
        ready = store.trading_watermark(
            (("rpiDepth", "BTCUSDT"), ("depth20", "BTCUSDT"))
        )
        assert ready["publishable"] is True
        assert ready["watermark_ms"] == 900

        missing = store.trading_watermark(
            (("depth20", "BTCUSDT"), ("depth20", "ETHUSDT"))
        )
        assert missing["publishable"] is False
        assert missing["blockers"] == ["depth20:ETHUSDT:MISSING"]
    finally:
        store.close()


def test_cursor_journal_is_append_only_and_tamper_evident(tmp_path) -> None:
    store = StreamCursorStore(str(tmp_path / "stream.db"), create=True)
    try:
        _observe(store, first=10, final=12, previous=9, event_time_ms=1_000)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._conn.execute(  # noqa: SLF001 - verifies database authority
                "DELETE FROM binance_stream_cursor_events"
            )
        store._conn.execute(  # noqa: SLF001 - simulate on-disk tampering
            "DROP TRIGGER binance_stream_cursor_events_no_update"
        )
        store._conn.execute(  # noqa: SLF001 - deliberate corruption probe
            "UPDATE binance_stream_cursor_events SET row_hash=? WHERE seq=1",
            ("0" * 64,),
        )
        store._conn.commit()  # noqa: SLF001
        assert store.verify_chain()["valid"] is False
        assert store.trading_watermark((("depth20", "BTCUSDT"),))["publishable"] is False
    finally:
        store.close()


def test_cursor_chain_rejects_rehashed_payload_with_stale_event_identity(tmp_path) -> None:
    store = StreamCursorStore(str(tmp_path / "stream.db"), create=True)
    try:
        _observe(store, first=10, final=12, previous=9, event_time_ms=1_000)
        row = store._conn.execute(  # noqa: SLF001 - deliberate tamper probe
            "SELECT event_id,payload_json,prev_hash FROM binance_stream_cursor_events"
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload["reason_code"] = "TAMPERED"
        payload_json = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        payload_sha = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        row_hash_payload = json.dumps(
            {
                "event_id": row["event_id"],
                "payload_sha256": payload_sha,
                "prev_hash": row["prev_hash"],
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        row_hash = hashlib.sha256(row_hash_payload.encode("utf-8")).hexdigest()
        store._conn.execute(  # noqa: SLF001
            "DROP TRIGGER binance_stream_cursor_events_no_update"
        )
        store._conn.execute(  # noqa: SLF001
            "UPDATE binance_stream_cursor_events "
            "SET payload_json=?,payload_sha256=?,row_hash=? WHERE seq=1",
            (payload_json, payload_sha, row_hash),
        )
        store._conn.commit()  # noqa: SLF001
        assert store.verify_chain()["valid"] is False
    finally:
        store.close()


def test_malformed_ranges_and_illegal_recovery_transitions_fail_closed(tmp_path) -> None:
    store = StreamCursorStore(str(tmp_path / "stream.db"), create=True)
    try:
        with pytest.raises(StreamCursorStoreError):
            _observe(store, first=12, final=10, previous=9, event_time_ms=1_000)
        _observe(store, first=10, final=12, previous=9, event_time_ms=1_000)
        with pytest.raises(StreamCursorStoreError, match="recovery"):
            store.complete_recovery(
                "depth20",
                "BTCUSDT",
                snapshot_update_id=20,
                event_time_ms=1_100,
                observed_at_ms=1_101,
            )
    finally:
        store.close()
