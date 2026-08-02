from services.data.binance_realtime_store import BinanceRealtimeStore
from tools.binance_realtime_stream import (
    StreamState,
    _status,
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

    assert _verify_store_chain(path) is True

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
