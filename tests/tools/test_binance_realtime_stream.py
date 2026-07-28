from tools.binance_realtime_stream import StreamState, stream_urls


_NOW = 10_000


def _frame(stream, data):
    return {"stream": stream, "data": data}


def test_stream_urls_use_official_routed_public_and_market_endpoints():
    urls = stream_urls(("BTC", "ETH"))
    assert urls["public"].startswith("wss://fstream.binance.com/public/stream?streams=")
    assert "btcusdt@bookTicker" in urls["public"]
    assert "ethusdt@depth20@500ms" in urls["public"]
    assert urls["market"].startswith("wss://fstream.binance.com/market/stream?streams=")
    assert "btcusdt@markPrice@1s" in urls["market"]
    assert "ethusdt@aggTrade" in urls["market"]
    assert "!forceOrder@arr" in urls["market"]
    assert "/private/" not in urls["public"] + urls["market"]


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
    assert snapshot["trade_window"]["buy_taker_notional"] == 200.0
    assert snapshot["liquidation_window"]["long_liquidation_notional"] == 300.0
    assert snapshot["quality"]["order_submission"] is False
    assert state.trade_buckets == {}
    assert state.liquidation_buckets == {}


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
