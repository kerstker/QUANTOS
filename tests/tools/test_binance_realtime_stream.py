import asyncio
import threading
from types import SimpleNamespace

import pytest

from services.data.binance_realtime_store import BinanceRealtimeStore
from services.data.binance_stream_cursor import StreamCursorStore
from services.features.realtime_feature_batch_store import RealtimeFeatureBatchStore
from tools import binance_realtime_stream as realtime
from tools.binance_realtime_stream import (
    BinanceStreamDataError,
    StreamState,
    _recover_depth_cursor,
    _recover_depth_cursor_async,
    _status,
    _verify_cursor_chain_subprocess,
    _verify_store_chain,
    stream_urls,
)


_NOW = 10_000


def _frame(stream, data):
    return {"stream": stream, "data": data}


def test_stream_urls_use_official_routed_public_and_market_endpoints():
    urls = stream_urls(("BTC", "ETH"))
    assert urls["public"].startswith("wss://fstream.binance.com/public/stream?streams=")
    assert "btcusdt@bookTicker" in urls["public"]
    assert "ethusdt@depth20@500ms" in urls["public"]
    assert "btcusdt@rpiDepth@500ms" in urls["public"]
    assert urls["market"].startswith("wss://fstream.binance.com/market/stream?streams=")
    assert "btcusdt@markPrice@1s" in urls["market"]
    assert "ethusdt@aggTrade" in urls["market"]
    assert "!forceOrder@arr" in urls["market"]
    assert "!contractInfo" in urls["market"]
    assert "/private/" not in urls["public"] + urls["market"]


def test_full_chain_verification_uses_an_isolated_connection_and_is_timestamped(
    tmp_path,
):
    path = str(tmp_path / "stream.db")
    store = BinanceRealtimeStore(path, create=True)
    store.close()

    verification = _verify_store_chain(path)
    assert verification["valid"] is True
    assert verification["anchor_seq"] == 0
    assert verification["verified_suffix_rows"] == 0

    state = StreamState(("BTC",))
    status = _status(
        state,
        observed_at_ms=_NOW,
        snapshots={},
        store_path=path,
        row_count=0,
        chain_valid=None,
        chain_verified_at_ms=None,
    )
    assert status["verdict"] == "WARMING_OR_RECONNECTING"
    assert status["store"]["chain_valid"] is None
    assert status["store"]["chain_verified_at_ms"] is None


def test_chain_verification_resumes_from_trusted_checkpoint(tmp_path):
    path = str(tmp_path / "stream.db")
    store = BinanceRealtimeStore(path, create=True)
    try:
        assert store.append_snapshot(
            {
                "instrument_id": "BTCUSDT",
                "observed_at_ms": 100,
                "event_time_ms": 99,
            }
        ) is True
        checkpoint = store.verify_chain_from()
        assert store.append_snapshot(
            {
                "instrument_id": "ETHUSDT",
                "observed_at_ms": 101,
                "event_time_ms": 100,
            }
        ) is True
    finally:
        store.close()

    verification = _verify_store_chain(
        path,
        checkpoint_seq=checkpoint["verified_seq"],
        checkpoint_row_hash=checkpoint["verified_row_hash"],
    )

    assert verification["valid"] is True
    assert verification["anchor_seq"] == 1
    assert verification["verified_suffix_rows"] == 1
    assert verification["verified_seq"] == 2


def test_cursor_chain_full_audit_runs_in_isolated_subprocess(tmp_path):
    path = str(tmp_path / "stream.db")
    cursor = StreamCursorStore(path, create=True)
    try:
        cursor.observe(
            channel="depth20",
            instrument_id="BTCUSDT",
            first_update_id=10,
            final_update_id=11,
            previous_final_update_id=9,
            event_time_ms=99,
            observed_at_ms=100,
        )
    finally:
        cursor.close()

    verification = _verify_cursor_chain_subprocess(path)

    assert verification["valid"] is True
    assert verification["anchor_seq"] == 0
    assert verification["verified_suffix_events"] == 1
    assert verification["verified_seq"] == 1


def test_cursor_chain_subprocess_rejects_malformed_output(tmp_path, monkeypatch):
    monkeypatch.setattr(
        realtime.binance_cursor_chain_audit_process.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="not-json",
            returncode=0,
        ),
    )

    with pytest.raises(BinanceStreamDataError, match="malformed output"):
        _verify_cursor_chain_subprocess(str(tmp_path / "stream.db"))


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    (
        (0, '{"valid": false}'),
        (1, '{"valid": true}'),
    ),
)
def test_cursor_chain_subprocess_rejects_inconsistent_exit_status(
    tmp_path,
    monkeypatch,
    returncode,
    stdout,
):
    monkeypatch.setattr(
        realtime.binance_cursor_chain_audit_process.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=stdout,
            returncode=returncode,
        ),
    )

    with pytest.raises(BinanceStreamDataError, match="inconsistent result"):
        _verify_cursor_chain_subprocess(str(tmp_path / "stream.db"))


def test_cursor_chain_subprocess_uses_fixed_module_without_shell(tmp_path, monkeypatch):
    observed = {}

    def run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(
            stdout=(
                '{"valid":true,"verified_seq":0,'
                '"verified_row_hash":""}'
            ),
            returncode=0,
        )

    monkeypatch.setattr(
        realtime.binance_cursor_chain_audit_process.subprocess,
        "run",
        run,
    )

    verification = _verify_cursor_chain_subprocess(str(tmp_path / "stream.db"))

    assert verification["valid"] is True
    assert observed["command"][1:4] == [
        "-m",
        "tools.binance_realtime_stream",
        "--verify-cursor-chain",
    ]
    assert observed["kwargs"]["shell"] is False


@pytest.mark.asyncio
async def test_stream_verifier_starts_from_trusted_batch_checkpoint(
    tmp_path,
    monkeypatch,
):
    batch_path = tmp_path / "batches.db"
    now_ms = int(realtime.time.time() * 1_000)
    batch_store = RealtimeFeatureBatchStore(str(batch_path), create=True)
    batch_store.append(
        {
            "batch_id": "verified-source",
            "observed_at_ms": 100,
            "source": {
                "chain_checkpoint": {
                    "schema_version": 1,
                    "verified_seq": 123,
                    "verified_row_hash": "b" * 64,
                    "full_verified_at_ms": now_ms,
                }
            },
        }
    )
    batch_store.close()
    calls = []

    def verify(_path, *, checkpoint_seq, checkpoint_row_hash):
        calls.append((checkpoint_seq, checkpoint_row_hash))
        return {
            "valid": True,
            "anchor_seq": checkpoint_seq,
            "verified_suffix_rows": 2,
            "verified_seq": 125,
            "verified_row_hash": "c" * 64,
        }

    async def consume(_endpoint, _url, *, state, stop, deadline):
        del deadline
        state.connections[_endpoint] = True
        await stop.wait()

    monkeypatch.setattr(realtime, "_verify_store_chain", verify)
    monkeypatch.setattr(
        realtime,
        "_verify_cursor_chain_subprocess",
        lambda *_args, **_kwargs: {
            "valid": True,
            "anchor_seq": 0,
            "verified_suffix_events": 0,
            "verified_seq": 0,
            "verified_row_hash": "",
        },
    )
    monkeypatch.setattr(realtime, "_consume", consume)

    status = await realtime.run_stream(
        tickers=("BTC",),
        duration_seconds=0.03,
        flush_seconds=0.01,
        store_path=str(tmp_path / "stream.db"),
        status_path=str(tmp_path / "status.json"),
        batch_store_path=str(batch_path),
    )

    assert calls[0] == (123, "b" * 64)
    assert status["store"]["chain_valid"] is True
    assert status["store"]["chain_verification"]["anchor_seq"] == 123
    assert (
        status["store"]["chain_verification"]["verification_mode"]
        == "INCREMENTAL_FROM_TRUSTED_BATCH"
    )


def test_state_reduces_complete_stream_surface_into_one_snapshot():
    state = StreamState(("BTC",))
    state.handle(
        _frame(
            "btcusdt@bookTicker",
            {
                "e": "bookTicker",
                "E": 9_900,
                "T": 9_899,
                "s": "BTCUSDT",
                "u": 10,
                "b": "99.9",
                "B": "5",
                "a": "100.1",
                "A": "4",
                "st": 1,
            },
        ),
        received_at_ms=_NOW,
    )
    state.handle(
        _frame(
            "btcusdt@depth20@500ms",
            {
                "e": "depthUpdate",
                "E": 9_901,
                "T": 9_900,
                "s": "BTCUSDT",
                "U": 11,
                "u": 12,
                "pu": 10,
                "b": [["99.9", "5"], ["99.8", "6"]],
                "a": [["100.1", "4"], ["100.2", "7"]],
                "st": 1,
            },
        ),
        received_at_ms=_NOW,
    )
    state.handle(
        _frame(
            "btcusdt@rpiDepth@500ms",
            {
                "e": "depthUpdate",
                "E": 9_901,
                "T": 9_900,
                "s": "BTCUSDT",
                "U": 21,
                "u": 22,
                "pu": 20,
                "b": [["99.95", "2"], ["99.9", "0"]],
                "a": [["100.05", "3"]],
                "st": 1,
            },
        ),
        received_at_ms=_NOW,
    )
    state.handle(
        _frame(
            "btcusdt@markPrice@1s",
            {
                "e": "markPriceUpdate",
                "E": 9_902,
                "s": "BTCUSDT",
                "p": "100",
                "i": "99.5",
                "P": "99.8",
                "r": "0.0001",
                "T": 20_000,
                "st": 1,
            },
        ),
        received_at_ms=_NOW,
    )
    state.handle(
        _frame(
            "btcusdt@kline_1m",
            {
                "e": "kline",
                "E": 9_903,
                "s": "BTCUSDT",
                "k": {
                    "t": 9_000,
                    "T": 9_999,
                    "o": "99",
                    "h": "101",
                    "l": "98",
                    "c": "100",
                    "v": "10",
                    "q": "1000",
                    "n": 20,
                    "V": "6",
                    "Q": "600",
                    "x": False,
                },
                "st": 1,
            },
        ),
        received_at_ms=_NOW,
    )
    state.handle(
        _frame(
            "btcusdt@aggTrade",
            {
                "e": "aggTrade",
                "E": 9_904,
                "T": 9_904,
                "s": "BTCUSDT",
                "p": "100",
                "q": "2",
                "m": False,
                "st": 1,
            },
        ),
        received_at_ms=_NOW,
    )
    state.handle(
        _frame(
            "!forceOrder@arr",
            {
                "e": "forceOrder",
                "E": 9_905,
                "st": 1,
                "o": {
                    "s": "BTCUSDT",
                    "S": "SELL",
                    "q": "3",
                    "z": "3",
                    "p": "100",
                    "ap": "100",
                    "T": 9_905,
                },
            },
        ),
        received_at_ms=_NOW,
    )

    snapshot = state.snapshots(_NOW)["BTCUSDT"]
    assert snapshot["quality"]["complete_core"] is True
    assert snapshot["book"]["spread_bps"] > 0
    assert len(snapshot["depth20"]["bids"]) == 2
    assert snapshot["rpi_depth"]["rpi_orders_included"] is True
    assert snapshot["rpi_depth"]["paper_fill_eligible"] is False
    assert snapshot["rpi_depth_window"]["bid_delete_count"] == 1
    assert snapshot["rpi_depth_window"]["continuity_breaks"] == 0
    assert snapshot["trade_window"]["buy_taker_notional"] == 200.0
    assert snapshot["liquidation_window"]["long_liquidation_notional"] == 300.0
    assert snapshot["quality"]["order_submission"] is False
    assert state.trade_buckets == {}
    assert state.liquidation_buckets == {}
    assert state.rpi_delta_buckets == {}


def test_rpi_depth_detects_sequence_break_without_claiming_reconstructed_book():
    state = StreamState(("BTC",))
    first = {
        "e": "depthUpdate",
        "E": 9_900,
        "T": 9_899,
        "s": "BTCUSDT",
        "U": 10,
        "u": 11,
        "pu": 9,
        "b": [["99", "2"]],
        "a": [],
        "st": 1,
    }
    state.handle(
        _frame("btcusdt@rpiDepth@500ms", first),
        received_at_ms=_NOW,
    )
    second = dict(first)
    second.update(
        {
            "E": 9_901,
            "T": 9_900,
            "U": 20,
            "u": 21,
            "pu": 19,
            "b": [],
            "a": [["101", "3"]],
        }
    )
    state.handle(
        _frame("btcusdt@rpiDepth@500ms", second),
        received_at_ms=_NOW,
    )

    snapshot = state.snapshots(_NOW)["BTCUSDT"]
    assert snapshot["rpi_depth"]["continuity_ok"] is False
    assert snapshot["rpi_depth"]["book_reconstruction"] is False
    assert snapshot["rpi_depth_window"]["continuity_breaks"] == 1


def test_durable_cursor_gap_blocks_snapshot_and_status(tmp_path):
    cursor = StreamCursorStore(str(tmp_path / "stream.db"), create=True)
    try:
        state = StreamState(("BTC",), cursor_store=cursor)
        base = {
            "e": "depthUpdate",
            "E": 9_900,
            "T": 9_899,
            "s": "BTCUSDT",
            "U": 10,
            "u": 11,
            "pu": 9,
            "b": [["99", "2"]],
            "a": [["101", "3"]],
            "st": 1,
        }
        state.handle(_frame("btcusdt@depth20@500ms", base), received_at_ms=_NOW)
        gap = dict(base)
        gap.update({"E": 9_901, "T": 9_900, "U": 20, "u": 21, "pu": 19})
        state.handle(_frame("btcusdt@depth20@500ms", gap), received_at_ms=_NOW)

        snapshot = state.snapshots(_NOW)["BTCUSDT"]
        assert snapshot["depth20"]["cursor_state"] == "GAP_DETECTED"
        assert snapshot["quality"]["sequence_contiguous"] is False

        state.connections = {"public": True, "market": True}
        cursor_status = cursor.trading_watermark((("depth20", "BTCUSDT"),))
        status = _status(
            state,
            observed_at_ms=_NOW,
            snapshots={
                "BTCUSDT": {
                    "quality": {"complete_core": True, "sequence_contiguous": False}
                }
            },
            store_path=str(tmp_path / "stream.db"),
            row_count=1,
            chain_valid=True,
            chain_verified_at_ms=_NOW,
            cursor_status=cursor_status,
        )
        assert status["verdict"] == "BLOCKED_SEQUENCE_GAP"
        assert status["cursor"]["publishable"] is False
    finally:
        cursor.close()


def test_malformed_depth_frame_does_not_advance_durable_cursor(tmp_path):
    cursor = StreamCursorStore(str(tmp_path / "stream.db"), create=True)
    try:
        state = StreamState(("BTC",), cursor_store=cursor)
        base = {
            "e": "depthUpdate",
            "E": 9_900,
            "T": 9_899,
            "s": "BTCUSDT",
            "U": 10,
            "u": 11,
            "pu": 9,
            "b": [["99", "2"]],
            "a": [["101", "3"]],
            "st": 1,
        }
        state.handle(_frame("btcusdt@depth20@500ms", base), received_at_ms=_NOW)
        malformed = dict(base)
        malformed.update({"E": 9_901, "T": 9_900, "U": 12, "u": 13, "pu": 11})
        malformed["b"] = [["nan", "2"]]
        with pytest.raises(BinanceStreamDataError):
            state.handle(
                _frame("btcusdt@depth20@500ms", malformed),
                received_at_ms=_NOW,
            )
        assert cursor.latest("depth20", "BTCUSDT").cursor_update_id == 11
        assert cursor.event_count() == 1
    finally:
        cursor.close()


def test_snapshot_retains_bounded_exchange_clock_skew_without_breaking_pit(tmp_path):
    state = StreamState(("BTC",))
    state.handle(
        _frame(
            "btcusdt@depth20@500ms",
            {
                "e": "depthUpdate",
                "E": _NOW + 10,
                "T": _NOW + 9,
                "s": "BTCUSDT",
                "U": 10,
                "u": 11,
                "pu": 9,
                "b": [["99", "2"]],
                "a": [["101", "3"]],
                "st": 1,
            },
        ),
        received_at_ms=_NOW,
    )
    snapshot = state.snapshots(_NOW)["BTCUSDT"]
    assert snapshot["event_time_ms"] == _NOW + 10
    assert snapshot["observed_at_ms"] == _NOW
    assert snapshot["knowledge_time_ms"] == _NOW + 10
    assert snapshot["quality"]["exchange_clock_skew_ms"] == 10

    store = BinanceRealtimeStore(str(tmp_path / "stream.db"), create=True)
    try:
        assert store.append_snapshot(snapshot) is True
    finally:
        store.close()


def test_depth_cursor_recovery_requires_snapshot_to_cover_gap(tmp_path):
    cursor = StreamCursorStore(str(tmp_path / "stream.db"), create=True)
    try:
        cursor.observe(
            channel="depth20",
            instrument_id="BTCUSDT",
            first_update_id=10,
            final_update_id=11,
            previous_final_update_id=9,
            event_time_ms=9_900,
            observed_at_ms=10_000,
        )
        cursor.observe(
            channel="depth20",
            instrument_id="BTCUSDT",
            first_update_id=20,
            final_update_id=21,
            previous_final_update_id=19,
            event_time_ms=9_901,
            observed_at_ms=10_000,
        )

        blocked = _recover_depth_cursor(
            cursor,
            "BTCUSDT",
            observed_at_ms=10_001,
            rest_depth_fetch=lambda *_args, **_kwargs: {
                "last_update_id": 20,
                "event_time_ms": 9_902,
            },
        )
        assert blocked.state.value == "BLOCKED"
        assert blocked.reason_code == "REST_COVERAGE_INCOMPLETE"

        recovered = _recover_depth_cursor(
            cursor,
            "BTCUSDT",
            observed_at_ms=10_002,
            rest_depth_fetch=lambda *_args, **_kwargs: {
                "last_update_id": 21,
                "event_time_ms": 9_903,
            },
        )
        assert recovered.state.value == "RECOVERED"
        assert recovered.publishable is True
    finally:
        cursor.close()


@pytest.mark.asyncio
async def test_async_recovery_keeps_cursor_writes_on_event_loop(tmp_path):
    cursor = StreamCursorStore(str(tmp_path / "stream.db"), create=True)
    release = threading.Event()
    try:
        cursor.observe(
            channel="depth20",
            instrument_id="BTCUSDT",
            first_update_id=10,
            final_update_id=11,
            previous_final_update_id=9,
            event_time_ms=9_900,
            observed_at_ms=10_000,
        )
        cursor.observe(
            channel="depth20",
            instrument_id="BTCUSDT",
            first_update_id=20,
            final_update_id=21,
            previous_final_update_id=19,
            event_time_ms=9_901,
            observed_at_ms=10_000,
        )

        def fetch(*_args, **_kwargs):
            assert release.wait(timeout=2)
            return {"last_update_id": 30, "event_time_ms": 9_903}

        task = asyncio.create_task(
            _recover_depth_cursor_async(
                cursor,
                "BTCUSDT",
                observed_at_ms=10_002,
                rest_depth_fetch=fetch,
            )
        )
        while cursor.latest("depth20", "BTCUSDT").state.value != "RECOVERING":
            await asyncio.sleep(0)
        pending = cursor.observe(
            channel="depth20",
            instrument_id="BTCUSDT",
            first_update_id=22,
            final_update_id=30,
            previous_final_update_id=21,
            event_time_ms=9_902,
            observed_at_ms=10_001,
        )
        assert pending.pending_through_update_id == 30
        release.set()
        recovered = await task
        assert recovered.state.value == "RECOVERED"
        assert recovered.cursor_update_id == 30
    finally:
        release.set()
        cursor.close()


@pytest.mark.asyncio
async def test_run_stream_resumes_persisted_recovering_cursor(tmp_path, monkeypatch):
    store_path = str(tmp_path / "stream.db")
    snapshot_store = BinanceRealtimeStore(store_path, create=True)
    snapshot_store.close()
    cursor = StreamCursorStore(store_path, create=True)
    try:
        cursor.observe(
            channel="depth20",
            instrument_id="BTCUSDT",
            first_update_id=10,
            final_update_id=11,
            previous_final_update_id=9,
            event_time_ms=9_900,
            observed_at_ms=10_000,
        )
        cursor.observe(
            channel="depth20",
            instrument_id="BTCUSDT",
            first_update_id=20,
            final_update_id=21,
            previous_final_update_id=19,
            event_time_ms=9_901,
            observed_at_ms=10_000,
        )
        cursor.begin_recovery(
            "depth20",
            "BTCUSDT",
            observed_at_ms=10_001,
        )
        initial_verification = cursor.verify_chain_from()
    finally:
        cursor.close()

    async def consume(endpoint, _url, *, state, stop, deadline):
        del deadline
        state.connections[endpoint] = True
        await stop.wait()

    monkeypatch.setattr(realtime, "_consume", consume)
    monkeypatch.setattr(
        realtime,
        "_verify_cursor_chain_subprocess",
        lambda *_args, **_kwargs: dict(initial_verification),
    )

    await realtime.run_stream(
        tickers=("BTC",),
        duration_seconds=0.08,
        flush_seconds=0.01,
        store_path=store_path,
        status_path=str(tmp_path / "status.json"),
        batch_store_path=str(tmp_path / "batches.db"),
        rest_depth_fetch=lambda *_args, **_kwargs: {
            "last_update_id": 21,
            "event_time_ms": 9_902,
        },
    )

    reopened = StreamCursorStore(store_path)
    try:
        recovered = reopened.latest("depth20", "BTCUSDT")
        assert recovered is not None
        assert recovered.state is realtime.StreamCursorState.RECOVERED
        assert recovered.publishable is True
    finally:
        reopened.close()


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (TimeoutError("slow"), "REST_TIMEOUT"),
        (RuntimeError("HTTP 429"), "HTTP_429"),
        (ValueError("empty result"), "EMPTY_REST_RESULT"),
    ],
)
def test_depth_cursor_recovery_failures_remain_blocked(tmp_path, failure, reason):
    cursor = StreamCursorStore(str(tmp_path / f"{reason}.db"), create=True)
    try:
        cursor.observe(
            channel="depth20",
            instrument_id="BTCUSDT",
            first_update_id=10,
            final_update_id=11,
            previous_final_update_id=9,
            event_time_ms=9_900,
            observed_at_ms=10_000,
        )
        cursor.observe(
            channel="depth20",
            instrument_id="BTCUSDT",
            first_update_id=20,
            final_update_id=21,
            previous_final_update_id=19,
            event_time_ms=9_901,
            observed_at_ms=10_000,
        )

        def fail(*_args, **_kwargs):
            raise failure

        result = _recover_depth_cursor(
            cursor,
            "BTCUSDT",
            observed_at_ms=10_001,
            rest_depth_fetch=fail,
        )
        assert result.state.value == "BLOCKED"
        assert result.reason_code == reason
        assert result.publishable is False
    finally:
        cursor.close()


def test_contract_info_retains_status_and_bracket_changes_for_selected_symbol():
    state = StreamState(("BTC",))
    state.handle(
        _frame(
            "!contractInfo",
            {
                "e": "contractInfo",
                "E": 9_900,
                "s": "BTCUSDT",
                "ct": "PERPETUAL",
                "dt": 20_000,
                "ot": 1_000,
                "cs": "TRADING",
                "bks": [
                    {
                        "bs": 1,
                        "bnf": 0,
                        "bnc": 5000,
                        "mmr": 0.01,
                        "cf": 0,
                        "mi": 21,
                        "ma": 50,
                    }
                ],
                "st": 1,
            },
        ),
        received_at_ms=_NOW,
    )

    info = state.latest["BTCUSDT"]["contract_info"]
    assert info["contract_status"] == "TRADING"
    assert info["paper_eligible"] is True
    assert info["notional_brackets"][0]["cap_notional"] == 5000.0
    assert state.event_counts["contractInfo"] == 1


def test_cm_migration_rows_are_ignored_for_usdm_stream():
    state = StreamState(("BTC",))
    state.handle(
        _frame(
            "btcusdt@bookTicker",
            {
                "e": "bookTicker",
                "E": 9_900,
                "T": 9_899,
                "s": "BTCUSDT",
                "u": 10,
                "b": "99.9",
                "B": "5",
                "a": "100.1",
                "A": "4",
                "st": 2,
            },
        ),
        received_at_ms=_NOW,
    )
    assert state.latest["BTCUSDT"] == {}
