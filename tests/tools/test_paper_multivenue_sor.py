from decimal import Decimal

import pytest

from services.data.binance_realtime_store import BinanceRealtimeStore
from services.data.cross_venue_order_book_store import CrossVenueOrderBookStore
from tools.paper_multivenue_sor import (
    INSTRUMENTS,
    MAX_FUTURE_SKEW_MS,
    MAX_QUOTE_AGE_MS,
    PaperMultiVenueSORError,
    collect_books,
    load_persisted_routing_snapshot,
    parse_binance_rest_book,
    parse_bybit_book,
    run_canary,
)


def _depth(base: str) -> dict:
    price = Decimal(base)
    return {
        "event_time_ms": 1_000,
        "transaction_time_ms": 1_000,
        "first_update_id": 1,
        "final_update_id": 2,
        "previous_final_update_id": 0,
        "bids": [
            [str(price), "100000"],
            [str(price - Decimal("0.001")), "100000"],
        ],
        "asks": [
            [str(price + Decimal("0.001")), "100000"],
            [str(price + Decimal("0.002")), "100000"],
        ],
    }


def _binance_snapshot(instrument_id: str, base: str, now_ms: int) -> dict:
    return {
        "schema_version": 1,
        "provider": "BINANCE_PUBLIC_USDM_WEBSOCKET",
        "venue": "binance_usdm",
        "instrument_id": instrument_id,
        "event_time_ms": now_ms - 100,
        "knowledge_time_ms": now_ms - 100,
        "observed_at_ms": now_ms - 100,
        "depth20": _depth(base),
        "quality": {
            "complete_core": True,
            "missing_channels": [],
            "private_access": False,
            "broker_connectivity": False,
            "order_submission": False,
        },
    }


def _bybit_payload(instrument_id: str, base: str, now_ms: int) -> dict:
    price = Decimal(base)
    return {
        "retCode": 0,
        "result": {
            "s": instrument_id,
            "ts": now_ms - 50,
            "u": 10,
            "seq": 20,
            "b": [
                [str(price - Decimal("0.0005")), "100000"],
                [str(price - Decimal("0.0015")), "100000"],
            ],
            "a": [
                [str(price + Decimal("0.0005")), "100000"],
                [str(price + Decimal("0.0015")), "100000"],
            ],
        },
    }


def _binance_rest_payload(base: str, now_ms: int) -> dict:
    price = Decimal(base)
    return {
        "lastUpdateId": 99,
        "E": now_ms - 40,
        "T": now_ms - 50,
        "bids": [
            [str(price), "100000"],
            [str(price - Decimal("0.001")), "100000"],
        ],
        "asks": [
            [str(price + Decimal("0.001")), "100000"],
            [str(price + Decimal("0.002")), "100000"],
        ],
    }


def test_parse_bybit_book_rejects_stale_and_preserves_depth():
    now_ms = 1_000_000
    payload = _bybit_payload("DOGEUSDT", "0.20", now_ms)
    book = parse_bybit_book(payload, "DOGEUSDT", observed_at_ms=now_ms)
    assert book.venue_id == "bybit_linear"
    assert len(book.bids) == 2
    assert book.bids[0].price > book.bids[1].price

    payload["result"]["ts"] = now_ms - MAX_QUOTE_AGE_MS - 1
    with pytest.raises(PaperMultiVenueSORError, match="stale"):
        parse_bybit_book(payload, "DOGEUSDT", observed_at_ms=now_ms)


def test_fractional_contract_depth_is_conservatively_aggregated_to_whole_units():
    now_ms = 1_000_000
    payload = _bybit_payload("BTCUSDT", "50000", now_ms)
    payload["result"]["b"] = [["50000", "0.6"], ["49999", "0.6"]]
    payload["result"]["a"] = [["50001", "0.6"], ["50002", "0.6"]]

    book = parse_bybit_book(payload, "BTCUSDT", observed_at_ms=now_ms)

    assert [(row.price, row.quantity) for row in book.bids] == [
        (Decimal("49999"), 1)
    ]
    assert [(row.price, row.quantity) for row in book.asks] == [
        (Decimal("50002"), 1)
    ]


def test_public_rest_parsers_allow_only_bounded_exchange_clock_lead():
    now_ms = 1_000_000
    binance = _binance_rest_payload("0.20", now_ms)
    binance["T"] = now_ms + 2_000
    binance["E"] = now_ms + 2_005
    assert parse_binance_rest_book(
        binance, "DOGEUSDT", observed_at_ms=now_ms
    ).observed_at_ms == now_ms + 2_000
    assert parse_binance_rest_book(
        binance, "DOGEUSDT", observed_at_ms=now_ms
    ).latency_ms == 0
    binance["T"] = now_ms + MAX_FUTURE_SKEW_MS + 1
    binance["E"] = binance["T"]
    with pytest.raises(PaperMultiVenueSORError, match="future"):
        parse_binance_rest_book(binance, "DOGEUSDT", observed_at_ms=now_ms)

    bybit = _bybit_payload("DOGEUSDT", "0.20", now_ms)
    bybit["result"]["ts"] = now_ms + 2_000
    assert parse_bybit_book(
        bybit, "DOGEUSDT", observed_at_ms=now_ms
    ).observed_at_ms == now_ms + 2_000
    assert parse_bybit_book(
        bybit, "DOGEUSDT", observed_at_ms=now_ms
    ).latency_ms == 0
    bybit["result"]["ts"] = now_ms + MAX_FUTURE_SKEW_MS + 1
    with pytest.raises(PaperMultiVenueSORError, match="future"):
        parse_bybit_book(bybit, "DOGEUSDT", observed_at_ms=now_ms)


def test_collect_and_run_canary_use_two_real_public_venues(tmp_path):
    now_ms = 2_000_000
    bases = {
        "DOGEUSDT": "0.20",
        "XRPUSDT": "0.50",
        "ADAUSDT": "0.40",
        "DOTUSDT": "2.00",
    }
    path = tmp_path / "binance.db"
    store = BinanceRealtimeStore(str(path), create=True)
    try:
        for instrument_id, base in bases.items():
            store.append_snapshot(_binance_snapshot(instrument_id, base, now_ms))
        rest_row = _binance_snapshot("DOGEUSDT", bases["DOGEUSDT"], now_ms)
        rest_row["provider"] = "BINANCE_PUBLIC_USDM"
        rest_row["observed_at_ms"] = now_ms
        rest_row["knowledge_time_ms"] = now_ms
        store.append_snapshot(rest_row)
    finally:
        store.close()

    def fake_get(url: str):
        instrument_id = next(item for item in INSTRUMENTS if item in url)
        return _bybit_payload(instrument_id, bases[instrument_id], now_ms)

    books, bundle = collect_books(
        binance_store_path=str(path),
        now_ms=now_ms,
        http_get=fake_get,
        clock_ms=lambda: now_ms,
    )
    assert len(bundle["books"]) == 8
    assert bundle["quality"]["simulation_only"] is True

    run = run_canary(
        now_ms=now_ms,
        books=books,
        snapshot_id=bundle["snapshot_id"],
    )
    assert run.n_approved == 4
    assert len(run.detail["route_tca"]) == 4
    assert {
        item["venue_id"]
        for route in run.detail["routing"]
        for item in route["slices"]
    } == {"binance_usdm", "bybit_linear"}
    assert all(
        route["status"] == "COMPLETE" for route in run.detail["routing"]
    )


def test_collect_books_uses_visible_binance_rest_failover_when_stream_is_stale(
    tmp_path,
):
    now_ms = 2_000_000
    bases = {
        "DOGEUSDT": "0.20",
        "XRPUSDT": "0.50",
        "ADAUSDT": "0.40",
        "DOTUSDT": "2.00",
    }
    path = tmp_path / "binance.db"
    store = BinanceRealtimeStore(str(path), create=True)
    try:
        for instrument_id, base in bases.items():
            store.append_snapshot(
                _binance_snapshot(
                    instrument_id,
                    base,
                    now_ms - MAX_QUOTE_AGE_MS - 1,
                )
            )
    finally:
        store.close()

    def fake_get(url: str):
        instrument_id = next(item for item in INSTRUMENTS if item in url)
        if "fapi.binance.com" in url:
            return _binance_rest_payload(bases[instrument_id], now_ms)
        return _bybit_payload(instrument_id, bases[instrument_id], now_ms)

    books, bundle = collect_books(
        binance_store_path=str(path),
        now_ms=now_ms,
        http_get=fake_get,
        clock_ms=lambda: now_ms,
    )

    assert set(books) == set(INSTRUMENTS)
    assert bundle["quality"]["binance_rest_failover_used"] is True
    assert set(bundle["binance_sources"].values()) == {
        "BINANCE_PUBLIC_USDM_REST_FAILOVER"
    }
    assert {
        row["source"] for row in bundle["books"] if row["venue"] == "binance_usdm"
    } == {"BINANCE_PUBLIC_USDM_REST_FAILOVER"}


def test_persisted_routing_snapshot_requires_two_fresh_venues_per_ticker(tmp_path):
    now_ms = 2_000_000
    bases = {"DOGEUSDT": "0.20", "XRPUSDT": "0.50"}
    realtime_path = tmp_path / "binance.db"
    realtime = BinanceRealtimeStore(str(realtime_path), create=True)
    try:
        for instrument_id, base in bases.items():
            realtime.append_snapshot(_binance_snapshot(instrument_id, base, now_ms))
    finally:
        realtime.close()

    def fake_get(url: str):
        instrument_id = next(item for item in bases if item in url)
        return _bybit_payload(instrument_id, bases[instrument_id], now_ms)

    _, bundle = collect_books(
        binance_store_path=str(realtime_path),
        now_ms=now_ms,
        http_get=fake_get,
        clock_ms=lambda: now_ms,
        instruments=tuple(bases),
    )
    book_path = tmp_path / "books.db"
    store = CrossVenueOrderBookStore(str(book_path), create=True)
    try:
        store.append_bundle(bundle)
    finally:
        store.close()

    books, prices, snapshot_id, as_of_ms = load_persisted_routing_snapshot(
        book_store_path=str(book_path),
        tickers=("DOGE", "XRP"),
        now_ms=now_ms,
    )

    assert set(books) == {"DOGE", "XRP"}
    assert all(len(rows) == 2 for rows in books.values())
    assert prices["DOGE"] > 0
    assert snapshot_id == bundle["snapshot_id"]
    assert as_of_ms == bundle["observed_at_ms"]


def test_persisted_routing_snapshot_rejects_unapproved_public_source(tmp_path):
    now_ms = 2_000_000
    realtime_path = tmp_path / "binance.db"
    realtime = BinanceRealtimeStore(str(realtime_path), create=True)
    try:
        realtime.append_snapshot(_binance_snapshot("BTCUSDT", "60000", now_ms))
    finally:
        realtime.close()

    _, bundle = collect_books(
        binance_store_path=str(realtime_path),
        now_ms=now_ms,
        http_get=lambda _url: _bybit_payload("BTCUSDT", "60000", now_ms),
        clock_ms=lambda: now_ms,
        instruments=("BTCUSDT",),
    )
    bundle["books"][0]["source"] = "UNVERIFIED_PROXY"
    book_path = tmp_path / "books.db"
    store = CrossVenueOrderBookStore(str(book_path), create=True)
    try:
        store.append_bundle(bundle)
    finally:
        store.close()

    with pytest.raises(PaperMultiVenueSORError, match="source is invalid"):
        load_persisted_routing_snapshot(
            book_store_path=str(book_path),
            tickers=("BTC",),
            now_ms=now_ms,
        )
