from decimal import Decimal

import pytest

from services.data.binance_realtime_store import BinanceRealtimeStore
from tools.paper_multivenue_sor import (
    INSTRUMENTS,
    MAX_QUOTE_AGE_MS,
    PaperMultiVenueSORError,
    collect_books,
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
