import hashlib
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from services.data.binance_realtime_store import BinanceRealtimeStore
from services.data.cross_venue_order_book_store import CrossVenueOrderBookStore
from services.features.realtime_feature_batch_store import RealtimeFeatureBatchStore
import tools.paper_multivenue_sor as paper_sor
from tools.paper_multivenue_sor import (
    INSTRUMENTS,
    MAX_FUTURE_SKEW_MS,
    MAX_QUOTE_AGE_MS,
    MAX_VENUE_SKEW_MS,
    PaperMultiVenueSORError,
    collect_books,
    execute,
    load_persisted_routing_snapshot,
    parse_binance_rest_book,
    parse_bybit_book,
    refresh_persisted_routing_snapshot,
    release_run_attestation,
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
        "DOGEUSDT": "0.09",
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
    assert run.rejected_reasons == []


def test_canary_pretrade_gate_rejects_stale_public_books(tmp_path):
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
    stale_at = now_ms - MAX_QUOTE_AGE_MS - 1
    stale_books = {
        instrument_id: tuple(
            replace(book, observed_at_ms=stale_at) for book in venue_books
        )
        for instrument_id, venue_books in books.items()
    }

    run = run_canary(
        now_ms=now_ms,
        books=stale_books,
        snapshot_id=bundle["snapshot_id"],
    )

    assert run.n_approved == 0
    assert run.rejected_reasons == ["MARKET_CLOSED"] * 4


def test_canary_quantity_limit_remains_fail_closed(tmp_path, monkeypatch):
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
    monkeypatch.setattr(paper_sor, "TARGET_NOTIONAL", Decimal("2100"))

    run = paper_sor.run_canary(
        now_ms=now_ms,
        books=books,
        snapshot_id=bundle["snapshot_id"],
    )

    assert run.n_approved == 0
    assert run.n_rejected == 4
    assert run.rejected_reasons.count("ORDER_SIZE_EXCEEDED") == 4


def test_collect_books_reanchors_snapshot_time_after_chain_verification(tmp_path):
    started_at_ms = 2_000_000
    verified_at_ms = started_at_ms + MAX_VENUE_SKEW_MS + 1_000
    instrument_id = "BTCUSDT"
    path = tmp_path / "binance.db"
    store = BinanceRealtimeStore(str(path), create=True)
    try:
        store.append_snapshot(
            _binance_snapshot(instrument_id, "60000", started_at_ms)
        )
        store.append_snapshot(
            _binance_snapshot(instrument_id, "60000", verified_at_ms)
        )
    finally:
        store.close()

    books, bundle = collect_books(
        binance_store_path=str(path),
        now_ms=started_at_ms,
        http_get=lambda _url: _bybit_payload(
            instrument_id, "60000", verified_at_ms
        ),
        clock_ms=lambda: verified_at_ms,
        instruments=(instrument_id,),
    )

    assert set(books) == {instrument_id}
    assert bundle["observed_at_ms"] == verified_at_ms
    assert bundle["quality"]["binance_rest_failover_used"] is False


def test_collect_books_deepens_bybit_snapshot_only_for_whole_unit_shortfall(
    tmp_path,
):
    now_ms = 2_000_000
    instrument_id = "BTCUSDT"
    path = tmp_path / "binance.db"
    store = BinanceRealtimeStore(str(path), create=True)
    try:
        store.append_snapshot(
            _binance_snapshot(instrument_id, "60000", now_ms)
        )
    finally:
        store.close()

    requested_limits: list[int] = []

    def fake_get(url: str):
        limit = int(url.rsplit("limit=", 1)[1])
        requested_limits.append(limit)
        payload = _bybit_payload(instrument_id, "60000", now_ms)
        quantity = "0.20" if limit == 20 else "0.60"
        payload["result"]["b"] = [
            ["59999", quantity],
            ["59998", quantity],
        ]
        payload["result"]["a"] = [
            ["60001", quantity],
            ["60002", quantity],
        ]
        return payload

    books, _bundle = collect_books(
        binance_store_path=str(path),
        now_ms=now_ms,
        http_get=fake_get,
        clock_ms=lambda: now_ms,
        instruments=(instrument_id,),
    )

    assert requested_limits == [20, 50]
    assert books[instrument_id][1].bids[0].quantity == 1
    assert books[instrument_id][1].asks[0].quantity == 1


def test_collect_books_verifies_only_suffix_from_trusted_checkpoint(
    tmp_path,
    monkeypatch,
):
    first_ms = 2_000_000
    second_ms = first_ms + 1_000
    instrument_id = "BTCUSDT"
    path = tmp_path / "binance.db"
    store = BinanceRealtimeStore(str(path), create=True)
    try:
        store.append_snapshot(_binance_snapshot(instrument_id, "60000", first_ms))
        anchor = store.verify_chain_from(
            checkpoint_seq=0,
            checkpoint_row_hash="",
        )
        store.append_snapshot(_binance_snapshot(instrument_id, "60000", second_ms))
    finally:
        store.close()

    checkpoint = {
        "checkpoint_seq": anchor["verified_seq"],
        "checkpoint_row_hash": anchor["verified_row_hash"],
        "full_verified_at_ms": first_ms,
        "verification_mode": "INCREMENTAL_FROM_TRUSTED_BATCH",
        "batch_authority": {"chain_valid": True, "batch_count": 1},
    }
    calls = []
    original_verify = BinanceRealtimeStore.verify_chain_from

    def verify_suffix(self, *, checkpoint_seq, checkpoint_row_hash):
        calls.append((checkpoint_seq, checkpoint_row_hash))
        return original_verify(
            self,
            checkpoint_seq=checkpoint_seq,
            checkpoint_row_hash=checkpoint_row_hash,
        )

    monkeypatch.setattr(BinanceRealtimeStore, "verify_chain_from", verify_suffix)
    monkeypatch.setattr(
        BinanceRealtimeStore,
        "chain_valid",
        lambda _self: pytest.fail("trusted consumers must not rescan from genesis"),
    )

    books, bundle = collect_books(
        binance_store_path=str(path),
        now_ms=second_ms,
        http_get=lambda _url: _bybit_payload(
            instrument_id,
            "60000",
            second_ms,
        ),
        clock_ms=lambda: second_ms,
        instruments=(instrument_id,),
        source_chain_checkpoint=checkpoint,
    )

    assert set(books) == {instrument_id}
    assert calls == [(anchor["verified_seq"], anchor["verified_row_hash"])]
    assert bundle["source_chain"]["verification"]["verified_suffix_rows"] == 1
    assert bundle["source_chain"]["verification"]["valid"] is True


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


def test_collect_books_deepens_binance_rest_only_for_whole_unit_shortfall(
    tmp_path,
):
    now_ms = 2_000_000
    instrument_id = "BTCUSDT"
    path = tmp_path / "binance.db"
    snapshot = _binance_snapshot(instrument_id, "60000", now_ms)
    snapshot["depth20"]["bids"] = [
        ["60000", "0.20"],
        ["59999", "0.20"],
    ]
    snapshot["depth20"]["asks"] = [
        ["60001", "0.20"],
        ["60002", "0.20"],
    ]
    store = BinanceRealtimeStore(str(path), create=True)
    try:
        store.append_snapshot(snapshot)
    finally:
        store.close()

    requested_limits: list[int] = []

    def fake_get(url: str):
        if "fapi.binance.com" not in url:
            return _bybit_payload(instrument_id, "60000", now_ms)
        limit = int(url.rsplit("limit=", 1)[1])
        requested_limits.append(limit)
        payload = _binance_rest_payload("60000", now_ms)
        quantity = "0.20" if limit == 20 else "0.60"
        payload["bids"] = [
            ["60000", quantity],
            ["59999", quantity],
        ]
        payload["asks"] = [
            ["60001", quantity],
            ["60002", quantity],
        ]
        return payload

    books, bundle = collect_books(
        binance_store_path=str(path),
        now_ms=now_ms,
        http_get=fake_get,
        clock_ms=lambda: now_ms,
        instruments=(instrument_id,),
    )

    assert requested_limits == [20, 50]
    assert bundle["binance_sources"][instrument_id] == (
        "BINANCE_PUBLIC_USDM_REST_FAILOVER"
    )
    assert books[instrument_id][0].bids[0].quantity == 1
    assert books[instrument_id][0].asks[0].quantity == 1


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


def test_refresh_persisted_routing_snapshot_only_appends_public_books(tmp_path):
    now_ms = 2_000_000
    bases = {
        "DOGEUSDT": "0.20",
        "XRPUSDT": "0.50",
        "ADAUSDT": "0.40",
        "DOTUSDT": "2.00",
    }
    realtime_path = tmp_path / "binance.db"
    realtime = BinanceRealtimeStore(str(realtime_path), create=True)
    try:
        for instrument_id, base in bases.items():
            realtime.append_snapshot(_binance_snapshot(instrument_id, base, now_ms))
        anchor = realtime.verify_chain_from(
            checkpoint_seq=0,
            checkpoint_row_hash="",
        )
    finally:
        realtime.close()

    book_path = tmp_path / "books.db"
    batch_path = tmp_path / "feature_batches.db"
    batches = RealtimeFeatureBatchStore(str(batch_path), create=True)
    try:
        batches.append(
            {
                "batch_id": "test-feature-batch",
                "observed_at_ms": now_ms,
                "source": {
                    "chain_checkpoint": {
                        "schema_version": 1,
                        "verified_seq": anchor["verified_seq"],
                        "verified_row_hash": anchor["verified_row_hash"],
                        "full_verified_at_ms": now_ms,
                    }
                },
            }
        )
    finally:
        batches.close()

    def fake_get(url: str):
        instrument_id = next(item for item in bases if item in url)
        return _bybit_payload(instrument_id, bases[instrument_id], now_ms)

    _books, bundle, appended, row_count = refresh_persisted_routing_snapshot(
        now_ms=now_ms,
        binance_store_path=str(realtime_path),
        feature_batch_store_path=str(batch_path),
        book_store_path=str(book_path),
        http_get=fake_get,
        clock_ms=lambda: now_ms,
        instruments=tuple(bases),
    )

    assert appended is True
    assert row_count == 1
    assert bundle["quality"]["simulation_only"] is True
    assert not (tmp_path / "dry_run.db").exists()
    _loaded, _prices, snapshot_id, _as_of_ms = load_persisted_routing_snapshot(
        book_store_path=str(book_path),
        tickers=tuple(item.removesuffix("USDT") for item in bases),
        now_ms=now_ms,
    )
    assert snapshot_id == bundle["snapshot_id"]


def test_refresh_persisted_routing_snapshot_rejects_missing_checkpoint_before_network(
    tmp_path,
):
    calls = []

    def unexpected_http(url: str):
        calls.append(url)
        pytest.fail("checkpoint rejection must happen before public HTTP")

    with pytest.raises(PaperMultiVenueSORError, match="checkpoint is unavailable"):
        refresh_persisted_routing_snapshot(
            now_ms=2_000_000,
            binance_store_path=str(tmp_path / "binance.db"),
            feature_batch_store_path=str(tmp_path / "missing-feature-batches.db"),
            book_store_path=str(tmp_path / "books.db"),
            http_get=unexpected_http,
            instruments=("BTCUSDT",),
        )

    assert calls == []


def test_refresh_persisted_routing_snapshot_rejects_stale_checkpoint_before_network(
    tmp_path,
):
    now_ms = paper_sor.FULL_CHAIN_AUDIT_INTERVAL_MS + 2_000_000
    batch_path = tmp_path / "feature-batches.db"
    batches = RealtimeFeatureBatchStore(str(batch_path), create=True)
    try:
        batches.append(
            {
                "batch_id": "stale-feature-batch",
                "observed_at_ms": 2_000_000,
                "source": {
                    "chain_checkpoint": {
                        "schema_version": 1,
                        "verified_seq": 1,
                        "verified_row_hash": "a" * 64,
                        "full_verified_at_ms": 2_000_000,
                    }
                },
            }
        )
    finally:
        batches.close()

    calls = []

    def unexpected_http(url: str):
        calls.append(url)
        pytest.fail("checkpoint rejection must happen before public HTTP")

    with pytest.raises(PaperMultiVenueSORError, match="requires a full audit"):
        refresh_persisted_routing_snapshot(
            now_ms=now_ms,
            binance_store_path=str(tmp_path / "binance.db"),
            feature_batch_store_path=str(batch_path),
            book_store_path=str(tmp_path / "books.db"),
            http_get=unexpected_http,
            instruments=("BTCUSDT",),
        )

    assert calls == []


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


def test_execute_emits_hash_bound_release_run_attestation(tmp_path, monkeypatch):
    rows = [
        {
            "instrument_id": instrument_id,
            "venue": venue,
            "source": source,
            "observed_at_ms": 2_000_000,
            "bids": [["1", 1]],
            "asks": [["2", 1]],
            "taker_fee_bps": fee,
            "latency_ms": 0,
        }
        for instrument_id in paper_sor.PAPER_INSTRUMENTS
        for venue, source, fee in (
            ("binance_usdm", "BINANCE_PUBLIC_USDM_WEBSOCKET", "5"),
            ("bybit_linear", "BYBIT_PUBLIC_LINEAR_REST", "5.5"),
        )
    ]
    bundle = {
        "schema_version": 1,
        "snapshot_id": paper_sor._snapshot_id(rows),
        "observed_at_ms": 2_000_000,
        "provenance": "REAL_PUBLIC_CROSS_VENUE_ORDER_BOOKS",
        "fee_policy_version": paper_sor.FEE_POLICY_VERSION,
        "fee_calibration": "UNCALIBRATED_RESEARCH_ASSUMPTION",
        "binance_sources": {
            instrument_id: "BINANCE_PUBLIC_USDM_WEBSOCKET"
            for instrument_id in paper_sor.PAPER_INSTRUMENTS
        },
        "source_chain": {
            "checkpoint": {
                "checkpoint_seq": 1,
                "checkpoint_row_hash": "b" * 64,
                "full_verified_at_ms": 2_000_000,
                "verification_mode": "INCREMENTAL_FROM_TRUSTED_BATCH",
                "batch_authority": "d" * 64,
            },
            "verification": {
                "valid": True,
                "anchor_seq": 1,
                "verified_suffix_rows": 0,
                "verified_seq": 1,
                "verified_row_hash": "b" * 64,
            },
        },
        "books": rows,
        "quality": {
            "complete": True,
            "binance_rest_failover_used": False,
            "simulation_only": True,
            "private_access": False,
            "broker_connectivity": False,
            "order_submission": False,
        },
    }
    run = SimpleNamespace(
        run_id="paper-sor-run",
        reproducibility_hash="c" * 64,
        n_intents=len(paper_sor.INSTRUMENTS),
        n_approved=len(paper_sor.INSTRUMENTS),
        n_rejected=0,
        rejected_reasons=[],
        detail={
            "route_tca": [
                {"instrument_id": instrument_id}
                for instrument_id in paper_sor.INSTRUMENTS
            ],
            "routing": [
                {
                    "instrument_id": instrument_id,
                    "slices": [
                        {"venue_id": "binance_usdm"},
                        {"venue_id": "bybit_linear"},
                    ]
                }
                for instrument_id in paper_sor.INSTRUMENTS
            ],
        },
    )
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", "a" * 40)
    monkeypatch.setattr(
        paper_sor,
        "refresh_persisted_routing_snapshot",
        lambda **_kwargs: (
            {instrument_id: () for instrument_id in paper_sor.INSTRUMENTS},
            bundle,
            True,
            1,
        ),
    )
    monkeypatch.setattr(paper_sor, "run_canary", lambda **_kwargs: run)
    monkeypatch.setattr(
        paper_sor,
        "open_domain_event_bus",
        lambda: SimpleNamespace(close=lambda: None),
    )
    monkeypatch.setattr(
        paper_sor,
        "save_and_project_dry_run",
        lambda *_args, **_kwargs: {"stored": True},
    )
    status_path = tmp_path / "paper_sor_status.json"

    status = execute(
        now_ms=2_000_000,
        dry_run_store_path=str(tmp_path / "dry_run.db"),
        status_path=str(status_path),
    )
    attestation = release_run_attestation()

    assert attestation is not None
    assert attestation["complete"] is True
    assert attestation["module"] == "paper_multivenue_sor"
    assert attestation["release_commit"] == "a" * 40
    assert attestation["input_hashes"]["public_cross_venue_books"] == status[
        "snapshot_id"
    ]
    assert attestation["input_hashes"]["public_source_chain_view"] == (
        paper_sor._canonical_sha256(bundle["source_chain"])
    )
    assert attestation["output_hashes"]["dry_run_reproducibility"] == "c" * 64
    assert attestation["output_hashes"]["paper_sor_status_file"] == hashlib.sha256(
        status_path.read_bytes()
    ).hexdigest()
    assert attestation["safety"] == {
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credentials_accessed": False,
        "broker_connectivity": False,
    }
    assert status["n_approved"] == len(paper_sor.INSTRUMENTS)
    assert status["n_rejected"] == 0

    bundle["quality"]["private_access"] = True
    assert paper_sor._build_release_run_attestation(
        status,
        bundle,
        run,
        output_path=status_path,
    )["complete"] is False


def test_execute_clears_stale_attestation_before_failed_run(tmp_path, monkeypatch):
    paper_sor._LAST_RELEASE_RUN_ATTESTATION = {"complete": True}
    monkeypatch.setattr(
        paper_sor,
        "refresh_persisted_routing_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(
            PaperMultiVenueSORError("fresh books unavailable")
        ),
    )

    with pytest.raises(PaperMultiVenueSORError, match="fresh books unavailable"):
        execute(
            now_ms=2_000_000,
            dry_run_store_path=str(tmp_path / "dry_run.db"),
            status_path=str(tmp_path / "paper_sor_status.json"),
        )

    assert release_run_attestation() is None
