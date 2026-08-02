"""Run an execution-only L2 paper SOR canary on real public order books.

Binance depth comes from the existing hash-chained WebSocket store.  Bybit
depth is fetched from its public V5 order-book endpoint.  The resulting route
plans and TCA are simulations only: this worker has no credentials, account
access, broker SDK, or order-submission capability.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SUCCESS,
)
from services.data.binance_realtime_store import BinanceRealtimeStore
from services.data.cross_venue_order_book_store import CrossVenueOrderBookStore
from services.execution.dry_run_pipeline import run_dry_run_pipeline
from services.execution.dry_run_store import DryRunStore
from services.execution.simulated_sor import (
    BookLevel,
    SORPolicy,
    VenueBook,
    route_simulated_intent,
)
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (
    AccountRiskState,
    MarketContext,
    RiskContext,
    StrategyApproval,
)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from tools.cache_io import atomic_write_json
from tools.domain_event_bus import open_domain_event_bus, save_and_project_dry_run


BYBIT_BASE_URL = "https://api.bybit.com"
DEFAULT_BINANCE_STORE = os.path.join(
    "data", "cache", "binance_realtime_stream.db"
)
DEFAULT_BOOK_STORE = os.path.join(
    "data", "cache", "cross_venue_order_books.db"
)
DEFAULT_DRY_RUN_STORE = os.path.join("data", "cache", "dry_run.db")
DEFAULT_STATUS = os.path.join(
    "data", "cache", "paper_multivenue_sor_status.json"
)
INSTRUMENTS = ("DOGEUSDT", "XRPUSDT", "ADAUSDT", "DOTUSDT")
TARGET_NOTIONAL = Decimal("1000")
MAX_QUOTE_AGE_MS = 10 * 60_000
MAX_VENUE_SKEW_MS = 10 * 60_000
FEE_POLICY_VERSION = "public-paper-fees.v1"
FEE_ASSUMPTIONS_BPS = {
    "binance_usdm": Decimal("5"),
    "bybit_linear": Decimal("5.5"),
}


class PaperMultiVenueSORError(RuntimeError):
    """Public books are missing, malformed, stale, or unsafe for the canary."""


def _http_get_json(url: str) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "quantos-l2-public-paper-sor",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise PaperMultiVenueSORError(f"{field} is not numeric") from exc
    if not result.is_finite() or result <= 0:
        raise PaperMultiVenueSORError(f"{field} is not positive")
    return result


def _levels(
    rows: object,
    field: str,
    *,
    descending: bool,
) -> tuple[BookLevel, ...]:
    if not isinstance(rows, list) or not rows:
        raise PaperMultiVenueSORError(f"{field} has no depth")
    levels: list[BookLevel] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) < 2:
            raise PaperMultiVenueSORError(f"{field}[{index}] is malformed")
        price = _decimal(row[0], f"{field}[{index}].price")
        quantity_decimal = _decimal(row[1], f"{field}[{index}].quantity")
        quantity = int(quantity_decimal)
        if quantity > 0:
            levels.append(BookLevel(price=price, quantity=quantity))
    if not levels:
        raise PaperMultiVenueSORError(f"{field} has no whole-unit depth")
    expected = sorted(
        levels,
        key=lambda item: item.price,
        reverse=descending,
    )
    if levels != expected or len({item.price for item in levels}) != len(levels):
        raise PaperMultiVenueSORError(f"{field} is not strictly sorted")
    return tuple(levels)


def parse_binance_book(
    snapshot: Mapping[str, Any],
    *,
    now_ms: int,
) -> VenueBook:
    if (
        snapshot.get("provider") != "BINANCE_PUBLIC_USDM_WEBSOCKET"
        or snapshot.get("venue") != "binance_usdm"
    ):
        raise PaperMultiVenueSORError("Binance book provenance is invalid")
    quality = snapshot.get("quality")
    depth = snapshot.get("depth20")
    if (
        not isinstance(quality, Mapping)
        or quality.get("complete_core") is not True
        or not isinstance(depth, Mapping)
    ):
        raise PaperMultiVenueSORError("Binance core book is incomplete")
    observed_at_ms = int(snapshot.get("observed_at_ms", -1))
    if observed_at_ms > now_ms or now_ms - observed_at_ms > MAX_QUOTE_AGE_MS:
        raise PaperMultiVenueSORError("Binance book is stale or in the future")
    return VenueBook(
        venue_id="binance_usdm",
        observed_at_ms=observed_at_ms,
        taker_fee_bps=FEE_ASSUMPTIONS_BPS["binance_usdm"],
        latency_ms=0,
        bids=_levels(depth.get("bids"), "binance.bids", descending=True),
        asks=_levels(depth.get("asks"), "binance.asks", descending=False),
    )


def parse_bybit_book(
    payload: object,
    instrument_id: str,
    *,
    observed_at_ms: int,
) -> VenueBook:
    if not isinstance(payload, Mapping) or payload.get("retCode") != 0:
        raise PaperMultiVenueSORError("Bybit order-book response failed")
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise PaperMultiVenueSORError("Bybit order-book result is missing")
    if str(result.get("s", "")).upper() != instrument_id:
        raise PaperMultiVenueSORError("Bybit order-book symbol mismatch")
    try:
        source_time_ms = int(result["ts"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PaperMultiVenueSORError("Bybit order-book timestamp is invalid") from exc
    if (
        source_time_ms > observed_at_ms
        or observed_at_ms - source_time_ms > MAX_QUOTE_AGE_MS
    ):
        raise PaperMultiVenueSORError("Bybit order book is stale or in the future")
    return VenueBook(
        venue_id="bybit_linear",
        observed_at_ms=source_time_ms,
        taker_fee_bps=FEE_ASSUMPTIONS_BPS["bybit_linear"],
        latency_ms=0,
        bids=_levels(result.get("b"), "bybit.bids", descending=True),
        asks=_levels(result.get("a"), "bybit.asks", descending=False),
    )


def _book_payload(instrument_id: str, book: VenueBook) -> dict[str, Any]:
    return {
        "instrument_id": instrument_id,
        "venue": book.venue_id,
        "observed_at_ms": book.observed_at_ms,
        "bids": [[str(level.price), level.quantity] for level in book.bids],
        "asks": [[str(level.price), level.quantity] for level in book.asks],
        "taker_fee_bps": str(book.taker_fee_bps),
        "latency_ms": book.latency_ms,
    }


def _snapshot_id(books: Sequence[dict[str, Any]]) -> str:
    body = json.dumps(
        books,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def collect_books(
    *,
    binance_store_path: str,
    now_ms: int,
    http_get: Callable[[str], object] = _http_get_json,
    clock_ms: Callable[[], int] | None = None,
) -> tuple[dict[str, tuple[VenueBook, VenueBook]], dict[str, Any]]:
    read_clock = (
        (lambda: int(time.time() * 1000))
        if clock_ms is None
        else clock_ms
    )
    store = BinanceRealtimeStore(binance_store_path, create=False)
    try:
        if not store.chain_valid():
            raise PaperMultiVenueSORError("Binance realtime chain is invalid")
        output: dict[str, tuple[VenueBook, VenueBook]] = {}
        rows: list[dict[str, Any]] = []
        for instrument_id in INSTRUMENTS:
            snapshot = store.latest_for_provider(
                instrument_id,
                "BINANCE_PUBLIC_USDM_WEBSOCKET",
                as_of_ms=now_ms,
            )
            if snapshot is None:
                raise PaperMultiVenueSORError(
                    f"Binance book is missing for {instrument_id}"
                )
            binance_book = parse_binance_book(snapshot, now_ms=now_ms)
            url = (
                f"{BYBIT_BASE_URL}/v5/market/orderbook?"
                f"{urlencode({'category': 'linear', 'symbol': instrument_id, 'limit': 20})}"
            )
            bybit_payload = http_get(url)
            response_observed_at_ms = max(now_ms, int(read_clock()))
            bybit_book = parse_bybit_book(
                bybit_payload,
                instrument_id,
                observed_at_ms=response_observed_at_ms,
            )
            skew = abs(binance_book.observed_at_ms - bybit_book.observed_at_ms)
            if skew > MAX_VENUE_SKEW_MS:
                raise PaperMultiVenueSORError(
                    f"cross-venue quote skew is too large for {instrument_id}"
                )
            output[instrument_id] = (binance_book, bybit_book)
            rows.extend(
                (
                    _book_payload(instrument_id, binance_book),
                    _book_payload(instrument_id, bybit_book),
                )
            )
    finally:
        store.close()
    rows.sort(key=lambda row: (row["instrument_id"], row["venue"]))
    snapshot_id = _snapshot_id(rows)
    bundle_observed_at_ms = max(
        now_ms,
        *(int(row["observed_at_ms"]) for row in rows),
    )
    return output, {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "observed_at_ms": bundle_observed_at_ms,
        "provenance": "REAL_PUBLIC_CROSS_VENUE_ORDER_BOOKS",
        "fee_policy_version": FEE_POLICY_VERSION,
        "fee_calibration": "UNCALIBRATED_RESEARCH_ASSUMPTION",
        "books": rows,
        "quality": {
            "complete": True,
            "simulation_only": True,
            "private_access": False,
            "broker_connectivity": False,
            "order_submission": False,
        },
    }


def _mid(book: VenueBook) -> Decimal:
    return (book.bids[0].price + book.asks[0].price) / Decimal("2")


def run_canary(
    *,
    now_ms: int,
    books: Mapping[str, tuple[VenueBook, VenueBook]],
    snapshot_id: str,
):
    prices = {
        instrument_id: float(_mid(venue_books[0]))
        for instrument_id, venue_books in books.items()
    }
    signs = {
        "DOGEUSDT": Decimal("1"),
        "XRPUSDT": Decimal("1"),
        "ADAUSDT": Decimal("-1"),
        "DOTUSDT": Decimal("-1"),
    }
    aum = float(TARGET_NOTIONAL * len(signs))
    weights = {
        instrument_id: float(sign * TARGET_NOTIONAL / Decimal(str(aum)))
        for instrument_id, sign in signs.items()
    }
    limits = RiskLimits(
        allowed_instruments=frozenset(INSTRUMENTS),
        allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=10**9,
        max_order_notional=10**6,
        fat_finger_qty=10**9,
        max_gross_exposure=10**7,
        max_net_exposure=10**7,
        max_leverage=100.0,
        max_single_name_pct=1.0,
        max_turnover=10.0,
        max_participation_rate=1.0,
        max_instrument_vol=5.0,
        daily_loss_limit=10**7,
        max_drawdown=1.0,
    )
    gate = PreTradeRiskGate(limits, kill_switch=KillSwitch())

    def ctx_factory(intent):
        return RiskContext(
            account=AccountRiskState(
                account_id="paper",
                cash=aum * 5,
                equity=aum * 5,
                day_pnl=0.0,
                peak_equity=aum * 5,
                positions=(),
            ),
            market=MarketContext(
                instrument_id=intent.instrument_id,
                known=True,
                reference_price=prices[intent.instrument_id],
                market_open=True,
                price_as_of=now_ms,
                est_daily_volume=10**12,
                instrument_vol=0.8,
            ),
            strategy=StrategyApproval(
                strategy_id="paper_sor_canary",
                approved=True,
                approval_id="l2-execution-canary",
            ),
            now=now_ms,
            max_staleness=MAX_QUOTE_AGE_MS,
        )

    policy = SORPolicy(
        max_quote_age_ms=MAX_QUOTE_AGE_MS,
        min_fill_fraction=Decimal("1"),
        max_venue_fraction=Decimal("0.60"),
        latency_penalty_bps_per_ms=Decimal("0"),
    )

    def route_planner(intent):
        return route_simulated_intent(
            intent,
            books[intent.instrument_id],
            as_of_ms=now_ms,
            policy=policy,
        )

    return run_dry_run_pipeline(
        weights=weights,
        prices=prices,
        current_positions={},
        aum=aum,
        gate=gate,
        ctx_factory=ctx_factory,
        adapter=DryRunAdapterEngine(),
        strategy_id="paper_sor_canary",
        timestamp=now_ms,
        marks=prices,
        initial_capital=aum * 5,
        cost_bps=0.0,
        route_planner=route_planner,
        routing_snapshot_id=snapshot_id,
        publish_completion_event=False,
    )


def execute(
    *,
    now_ms: int | None = None,
    binance_store_path: str = DEFAULT_BINANCE_STORE,
    book_store_path: str = DEFAULT_BOOK_STORE,
    dry_run_store_path: str = DEFAULT_DRY_RUN_STORE,
    status_path: str = DEFAULT_STATUS,
    http_get: Callable[[str], object] = _http_get_json,
) -> dict[str, Any]:
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    books, bundle = collect_books(
        binance_store_path=binance_store_path,
        now_ms=now,
        http_get=http_get,
    )
    Path(book_store_path).parent.mkdir(parents=True, exist_ok=True)
    book_store = CrossVenueOrderBookStore(book_store_path, create=True)
    try:
        book_store.append_bundle(bundle)
        if not book_store.chain_valid():
            raise PaperMultiVenueSORError("cross-venue book chain is invalid")
        book_rows = book_store.row_count()
    finally:
        book_store.close()

    run = run_canary(
        now_ms=int(bundle["observed_at_ms"]),
        books=books,
        snapshot_id=str(bundle["snapshot_id"]),
    )
    event_bus = open_domain_event_bus()
    dry_store = DryRunStore(dry_run_store_path, create=True)
    try:
        persistence = save_and_project_dry_run(
            dry_store,
            run,
            event_bus,
            now_ms=int(bundle["observed_at_ms"]),
        )
    finally:
        dry_store.close()
        event_bus.close()

    route_tca = run.detail.get("route_tca", [])
    routing = run.detail.get("routing", [])
    venues = sorted(
        {
            str(route["venue_id"])
            for item in routing
            if isinstance(item, Mapping)
            for route in item.get("slices", [])
            if isinstance(route, Mapping) and route.get("venue_id")
        }
    )
    status = {
        "schema_version": 1,
        "generated_at_ms": now,
        "status": "PASS" if route_tca and len(venues) >= 2 else "BLOCKED",
        "mode": "L2_PAPER_EXECUTION_CANARY",
        "simulation_only": True,
        "strategy_edge_claim": False,
        "snapshot_id": bundle["snapshot_id"],
        "run_id": run.run_id,
        "instruments": list(INSTRUMENTS),
        "venues": venues,
        "route_plan_count": len(routing),
        "route_tca_count": len(route_tca),
        "book_bundle_count": book_rows,
        "fee_policy_version": FEE_POLICY_VERSION,
        "fee_calibration": "UNCALIBRATED_RESEARCH_ASSUMPTION",
        "safety": {
            "private_access": False,
            "credentials": False,
            "broker_connectivity": False,
            "order_submission": False,
            "live_trading": False,
        },
        "persistence": persistence,
    }
    atomic_write_json(status_path, status)
    if status["status"] != "PASS":
        raise PaperMultiVenueSORError("paper SOR canary produced no multi-venue TCA")
    return status


def main() -> int:
    try:
        status = execute()
    except Exception as exc:
        failure = {
            "schema_version": 1,
            "generated_at_ms": int(time.time() * 1000),
            "status": "BLOCKED",
            "mode": "L2_PAPER_EXECUTION_CANARY",
            "simulation_only": True,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "safety": {
                "private_access": False,
                "credentials": False,
                "broker_connectivity": False,
                "order_submission": False,
                "live_trading": False,
            },
        }
        atomic_write_json(DEFAULT_STATUS, failure)
        print(json.dumps(failure, ensure_ascii=False, sort_keys=True))
        return SCHEDULE_EXIT_FAILURE
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    return SCHEDULE_EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
