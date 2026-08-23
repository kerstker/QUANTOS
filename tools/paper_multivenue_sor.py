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
from decimal import Decimal, ROUND_CEILING
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
from services.features.realtime_feature_batch_store import (
    trusted_source_chain_checkpoint,
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
from tools.binance_oi_metrics_fetch import FORWARD_MAJOR_TICKERS
from tools.cache_io import atomic_write_json
from tools.domain_event_bus import open_domain_event_bus, save_and_project_dry_run


BYBIT_BASE_URL = "https://api.bybit.com"
BINANCE_BASE_URL = "https://fapi.binance.com"
DEFAULT_BINANCE_STORE = os.path.join(
    "data", "cache", "binance_realtime_stream.db"
)
DEFAULT_FEATURE_BATCH_STORE = os.path.join(
    "data", "cache", "binance_realtime_feature_batches.db"
)
DEFAULT_BOOK_STORE = os.path.join(
    "data", "cache", "cross_venue_order_books.db"
)
DEFAULT_DRY_RUN_STORE = os.path.join("data", "cache", "dry_run.db")
DEFAULT_STATUS = os.path.join(
    "data", "cache", "paper_multivenue_sor_status.json"
)
INSTRUMENTS = ("DOGEUSDT", "XRPUSDT", "ADAUSDT", "DOTUSDT")
PAPER_INSTRUMENTS = tuple(f"{ticker}USDT" for ticker in FORWARD_MAJOR_TICKERS)
CANARY_MAX_TARGET_NOTIONAL = Decimal("100")
TARGET_NOTIONAL = CANARY_MAX_TARGET_NOTIONAL
MAX_QUOTE_AGE_MS = 10 * 60_000
MAX_VENUE_SKEW_MS = 10 * 60_000
MAX_FUTURE_SKEW_MS = 30_000
FULL_CHAIN_AUDIT_INTERVAL_MS = 24 * 60 * 60_000
BYBIT_DEPTH_LIMITS = (20, 50, 200, 500)
FEE_POLICY_VERSION = "public-paper-fees.v1"
FEE_ASSUMPTIONS_BPS = {
    "binance_usdm": Decimal("5"),
    "bybit_linear": Decimal("5.5"),
}
PUBLIC_BOOK_SOURCES = {
    "binance_usdm": frozenset(
        {
            "BINANCE_PUBLIC_USDM_WEBSOCKET",
            "BINANCE_PUBLIC_USDM_REST_FAILOVER",
        }
    ),
    "bybit_linear": frozenset({"BYBIT_PUBLIC_LINEAR_REST"}),
}
_LAST_RELEASE_RUN_ATTESTATION: dict[str, Any] | None = None


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
    cumulative_quantity = Decimal("0")
    emitted_quantity = 0
    for index, row in enumerate(rows):
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) < 2:
            raise PaperMultiVenueSORError(f"{field}[{index}] is malformed")
        price = _decimal(row[0], f"{field}[{index}].price")
        quantity_decimal = _decimal(row[1], f"{field}[{index}].quantity")
        # Venue books carry fractional contract quantities while the current
        # paper execution contract is whole-unit.  Floor each level separately
        # would discard executable depth (especially BTC/ETH).  Accumulate and
        # emit newly completed whole units at the current, therefore
        # conservative, worst price level.
        cumulative_quantity += quantity_decimal
        quantity = int(cumulative_quantity) - emitted_quantity
        if quantity > 0:
            levels.append(BookLevel(price=price, quantity=quantity))
            emitted_quantity += quantity
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
        latency_ms=max(0, now_ms - observed_at_ms),
        bids=_levels(depth.get("bids"), "binance.bids", descending=True),
        asks=_levels(depth.get("asks"), "binance.asks", descending=False),
    )


def parse_binance_rest_book(
    payload: object,
    instrument_id: str,
    *,
    observed_at_ms: int,
) -> VenueBook:
    """Parse a current public REST depth snapshot as a visible failover.

    The resident WebSocket chain remains preferred.  REST is used only when
    that source is missing or stale, and the bundle records the source choice.
    A bounded exchange-clock lead is accepted without weakening the age gate.
    """
    if not isinstance(payload, Mapping):
        raise PaperMultiVenueSORError("Binance REST order book is missing")
    try:
        output_time_ms = int(payload["E"])
        source_time_ms = int(payload["T"])
        update_id = int(payload["lastUpdateId"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PaperMultiVenueSORError(
            "Binance REST order-book identity is invalid"
        ) from exc
    if (
        output_time_ms < source_time_ms
        or update_id < 0
        or source_time_ms > observed_at_ms + MAX_FUTURE_SKEW_MS
        or observed_at_ms - source_time_ms > MAX_QUOTE_AGE_MS
    ):
        raise PaperMultiVenueSORError(
            "Binance REST order book is stale or in the future"
        )
    bids = _levels(payload.get("bids"), "binance_rest.bids", descending=True)
    asks = _levels(payload.get("asks"), "binance_rest.asks", descending=False)
    if asks[0].price <= bids[0].price:
        raise PaperMultiVenueSORError("Binance REST order book is crossed or locked")
    return VenueBook(
        venue_id="binance_usdm",
        observed_at_ms=source_time_ms,
        taker_fee_bps=FEE_ASSUMPTIONS_BPS["binance_usdm"],
        latency_ms=max(0, observed_at_ms - source_time_ms),
        bids=bids,
        asks=asks,
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
        source_time_ms > observed_at_ms + MAX_FUTURE_SKEW_MS
        or observed_at_ms - source_time_ms > MAX_QUOTE_AGE_MS
    ):
        raise PaperMultiVenueSORError("Bybit order book is stale or in the future")
    return VenueBook(
        venue_id="bybit_linear",
        observed_at_ms=source_time_ms,
        taker_fee_bps=FEE_ASSUMPTIONS_BPS["bybit_linear"],
        latency_ms=max(0, observed_at_ms - source_time_ms),
        bids=_levels(result.get("b"), "bybit.bids", descending=True),
        asks=_levels(result.get("a"), "bybit.asks", descending=False),
    )


def _fetch_bybit_book_with_bounded_depth(
    instrument_id: str,
    *,
    now_ms: int,
    http_get: Callable[[str], object],
    read_clock: Callable[[], int],
) -> VenueBook:
    """Fetch the smallest bounded public depth that proves whole-unit liquidity.

    The integer SOR contract intentionally aggregates fractional native depth at
    the worst contributing price.  Expensive instruments can have less than one
    native unit inside Bybit's first 20 levels, so retry only that narrow depth
    shortfall with progressively deeper, API-bounded snapshots.  Any malformed,
    stale, crossed or otherwise invalid response still fails immediately.
    """

    last_depth_error: PaperMultiVenueSORError | None = None
    for limit in BYBIT_DEPTH_LIMITS:
        url = (
            f"{BYBIT_BASE_URL}/v5/market/orderbook?"
            f"{urlencode({'category': 'linear', 'symbol': instrument_id, 'limit': limit})}"
        )
        payload = http_get(url)
        observed_at_ms = max(now_ms, int(read_clock()))
        try:
            return parse_bybit_book(
                payload,
                instrument_id,
                observed_at_ms=observed_at_ms,
            )
        except PaperMultiVenueSORError as exc:
            if "has no whole-unit depth" not in str(exc):
                raise
            last_depth_error = exc
    assert last_depth_error is not None
    raise PaperMultiVenueSORError(
        f"Bybit bounded depth is insufficient for {instrument_id}"
    ) from last_depth_error


def _book_payload(
    instrument_id: str,
    book: VenueBook,
    *,
    source: str,
) -> dict[str, Any]:
    return {
        "instrument_id": instrument_id,
        "venue": book.venue_id,
        "source": source,
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


def _canonical_sha256(value: object) -> str:
    body = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _is_sha256(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _build_release_run_attestation(
    status: Mapping[str, Any],
    bundle: Mapping[str, Any],
    run: Any,
    *,
    output_path: str | Path,
) -> dict[str, Any]:
    """Bind one paper SOR run to its exact public inputs and outputs."""
    release_commit = os.environ.get("QUANTOS_RELEASE_COMMIT")
    if isinstance(release_commit, str):
        release_commit = release_commit.strip().lower()
    snapshot_id = bundle.get("snapshot_id")
    books = bundle.get("books")
    bundle_quality = bundle.get("quality")
    bundle_quality = (
        bundle_quality if isinstance(bundle_quality, Mapping) else {}
    )
    source_chain = bundle.get("source_chain")
    source_chain = source_chain if isinstance(source_chain, Mapping) else {}
    source_checkpoint = source_chain.get("checkpoint")
    source_checkpoint = (
        source_checkpoint if isinstance(source_checkpoint, Mapping) else {}
    )
    source_verification = source_chain.get("verification")
    source_verification = (
        source_verification if isinstance(source_verification, Mapping) else {}
    )
    routing_contract = {
        "paper_instruments": list(PAPER_INSTRUMENTS),
        "canary_instruments": list(INSTRUMENTS),
        "target_notional": str(TARGET_NOTIONAL),
        "max_quote_age_ms": MAX_QUOTE_AGE_MS,
        "max_venue_skew_ms": MAX_VENUE_SKEW_MS,
        "fee_policy_version": FEE_POLICY_VERSION,
        "fee_assumptions_bps": {
            name: str(value)
            for name, value in sorted(FEE_ASSUMPTIONS_BPS.items())
        },
    }
    input_hashes = {
        "public_cross_venue_book_bundle": _canonical_sha256(bundle),
        "public_cross_venue_books": snapshot_id,
        "public_source_chain_view": _canonical_sha256(source_chain),
        "routing_input_contract": _canonical_sha256(routing_contract),
    }
    output_hashes = {
        "paper_sor_status_file": _sha256_file(output_path),
        "dry_run_reproducibility": getattr(run, "reproducibility_hash", None),
    }
    safety = status.get("safety")
    safety = safety if isinstance(safety, Mapping) else {}
    safety_contract = {
        "paper_only": status.get("simulation_only"),
        "live_allowed": safety.get("live_trading"),
        "order_submission_allowed": safety.get("order_submission"),
        "credentials_accessed": safety.get("credentials"),
        "broker_connectivity": safety.get("broker_connectivity"),
    }
    expected_safety = {
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credentials_accessed": False,
        "broker_connectivity": False,
    }
    complete = bool(
        isinstance(release_commit, str)
        and len(release_commit) == 40
        and all(character in "0123456789abcdef" for character in release_commit)
        and status.get("status") == "PASS"
        and status.get("snapshot_id") == snapshot_id
        and bundle.get("provenance") == "REAL_PUBLIC_CROSS_VENUE_ORDER_BOOKS"
        and bundle.get("fee_policy_version") == FEE_POLICY_VERSION
        and bundle.get("fee_calibration") == "UNCALIBRATED_RESEARCH_ASSUMPTION"
        and bundle_quality.get("complete") is True
        and bundle_quality.get("simulation_only") is True
        and bundle_quality.get("private_access") is False
        and bundle_quality.get("broker_connectivity") is False
        and bundle_quality.get("order_submission") is False
        and source_verification.get("valid") is True
        and source_checkpoint.get("verification_mode")
        in {"FULL_EXPLICIT", "INCREMENTAL_FROM_TRUSTED_BATCH"}
        and isinstance(books, list)
        and _is_sha256(snapshot_id)
        and _snapshot_id(books) == snapshot_id
        and all(_is_sha256(value) for value in input_hashes.values())
        and all(_is_sha256(value) for value in output_hashes.values())
        and safety_contract == expected_safety
    )
    return {
        "schema_version": 1,
        "module": "paper_multivenue_sor",
        "release_commit": release_commit,
        "complete": complete,
        "input_hashes": input_hashes,
        "output_hashes": output_hashes,
        "safety": safety_contract,
    }


def release_run_attestation() -> dict[str, Any] | None:
    """Return a detached copy for the immutable L2 launcher."""
    if _LAST_RELEASE_RUN_ATTESTATION is None:
        return None
    return json.loads(
        json.dumps(
            _LAST_RELEASE_RUN_ATTESTATION,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _trusted_source_checkpoint(path: str, *, now_ms: int) -> dict[str, Any]:
    """Require the fresh full-audit anchor published by the feature worker."""
    if not Path(path).is_file():
        raise PaperMultiVenueSORError("trusted source chain checkpoint is unavailable")
    try:
        checkpoint = trusted_source_chain_checkpoint(
            path,
            now_ms=now_ms,
            full_chain_audit_interval_ms=FULL_CHAIN_AUDIT_INTERVAL_MS,
            create=False,
        )
    except Exception as exc:
        raise PaperMultiVenueSORError(
            "trusted source chain checkpoint is invalid"
        ) from exc
    if checkpoint.get("checkpoint_seq") == 0:
        raise PaperMultiVenueSORError(
            "trusted source chain checkpoint requires a full audit"
        )
    return checkpoint


def _verify_source_chain(
    store: BinanceRealtimeStore,
    checkpoint: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if checkpoint is None:
        checkpoint_seq = 0
        checkpoint_row_hash = ""
        checkpoint_view: dict[str, Any] = {
            "checkpoint_seq": 0,
            "checkpoint_row_hash": "",
            "full_verified_at_ms": None,
            "verification_mode": "FULL_EXPLICIT",
            "batch_authority": None,
        }
    else:
        checkpoint_seq = checkpoint.get("checkpoint_seq")
        checkpoint_row_hash = checkpoint.get("checkpoint_row_hash")
        if (
            isinstance(checkpoint_seq, bool)
            or not isinstance(checkpoint_seq, int)
            or checkpoint_seq <= 0
            or not isinstance(checkpoint_row_hash, str)
        ):
            raise PaperMultiVenueSORError(
                "trusted source chain checkpoint is malformed"
            )
        checkpoint_view = {
            key: checkpoint.get(key)
            for key in (
                "checkpoint_seq",
                "checkpoint_row_hash",
                "full_verified_at_ms",
                "verification_mode",
                "batch_authority",
            )
        }
    verification = store.verify_chain_from(
        checkpoint_seq=checkpoint_seq,
        checkpoint_row_hash=checkpoint_row_hash,
    )
    if verification.get("valid") is not True:
        raise PaperMultiVenueSORError("Binance realtime chain is invalid")
    return {
        "checkpoint": checkpoint_view,
        "verification": verification,
    }


def collect_books(
    *,
    binance_store_path: str,
    now_ms: int,
    http_get: Callable[[str], object] = _http_get_json,
    clock_ms: Callable[[], int] | None = None,
    instruments: Sequence[str] = INSTRUMENTS,
    source_chain_checkpoint: Mapping[str, Any] | None = None,
) -> tuple[dict[str, tuple[VenueBook, VenueBook]], dict[str, Any]]:
    read_clock = (
        (lambda: int(time.time() * 1000))
        if clock_ms is None
        else clock_ms
    )
    store = BinanceRealtimeStore(binance_store_path, create=False)
    try:
        source_chain = _verify_source_chain(store, source_chain_checkpoint)
        # Full-chain verification can take materially longer than the public
        # quote freshness window on a large authority store.  Re-anchor the
        # collection clock after verification so Binance and Bybit are selected
        # against the same decision time instead of manufacturing venue skew.
        now_ms = max(now_ms, int(read_clock()))
        output: dict[str, tuple[VenueBook, VenueBook]] = {}
        rows: list[dict[str, Any]] = []
        binance_sources: dict[str, str] = {}
        requested = tuple(str(item).upper() for item in instruments)
        if (
            not requested
            or len(requested) != len(set(requested))
            or any(not item.endswith("USDT") for item in requested)
        ):
            raise PaperMultiVenueSORError("routing instrument universe is invalid")
        for instrument_id in requested:
            snapshot = store.latest_for_provider(
                instrument_id,
                "BINANCE_PUBLIC_USDM_WEBSOCKET",
                as_of_ms=now_ms,
            )
            try:
                if snapshot is None:
                    raise PaperMultiVenueSORError(
                        f"Binance book is missing for {instrument_id}"
                    )
                binance_book = parse_binance_book(snapshot, now_ms=now_ms)
                binance_source = "BINANCE_PUBLIC_USDM_WEBSOCKET"
            except PaperMultiVenueSORError as websocket_error:
                binance_url = (
                    f"{BINANCE_BASE_URL}/fapi/v1/depth?"
                    f"{urlencode({'symbol': instrument_id, 'limit': 20})}"
                )
                try:
                    binance_payload = http_get(binance_url)
                    binance_observed_at_ms = max(now_ms, int(read_clock()))
                    binance_book = parse_binance_rest_book(
                        binance_payload,
                        instrument_id,
                        observed_at_ms=binance_observed_at_ms,
                    )
                except Exception as rest_error:
                    raise PaperMultiVenueSORError(
                        f"Binance WebSocket unavailable ({websocket_error}); "
                        f"REST failover unavailable ({rest_error})"
                    ) from rest_error
                binance_source = "BINANCE_PUBLIC_USDM_REST_FAILOVER"
            binance_sources[instrument_id] = binance_source
            bybit_book = _fetch_bybit_book_with_bounded_depth(
                instrument_id,
                now_ms=now_ms,
                http_get=http_get,
                read_clock=read_clock,
            )
            skew = abs(binance_book.observed_at_ms - bybit_book.observed_at_ms)
            if skew > MAX_VENUE_SKEW_MS:
                raise PaperMultiVenueSORError(
                    f"cross-venue quote skew is too large for {instrument_id}"
                )
            output[instrument_id] = (binance_book, bybit_book)
            rows.extend(
                (
                    _book_payload(
                        instrument_id,
                        binance_book,
                        source=binance_source,
                    ),
                    _book_payload(
                        instrument_id,
                        bybit_book,
                        source="BYBIT_PUBLIC_LINEAR_REST",
                    ),
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
        "binance_sources": dict(sorted(binance_sources.items())),
        "source_chain": source_chain,
        "books": rows,
        "quality": {
            "complete": True,
            "binance_rest_failover_used": any(
                source == "BINANCE_PUBLIC_USDM_REST_FAILOVER"
                for source in binance_sources.values()
            ),
            "simulation_only": True,
            "private_access": False,
            "broker_connectivity": False,
            "order_submission": False,
        },
    }


def refresh_persisted_routing_snapshot(
    *,
    now_ms: int | None = None,
    binance_store_path: str = DEFAULT_BINANCE_STORE,
    feature_batch_store_path: str | None = DEFAULT_FEATURE_BATCH_STORE,
    book_store_path: str = DEFAULT_BOOK_STORE,
    http_get: Callable[[str], object] = _http_get_json,
    clock_ms: Callable[[], int] | None = None,
    instruments: Sequence[str] = PAPER_INSTRUMENTS,
) -> tuple[dict[str, tuple[VenueBook, VenueBook]], dict[str, Any], bool, int]:
    """Fetch and append one fresh public-book bundle without running a canary.

    The one-minute engineering execution worker uses this narrow seam when
    the five-minute SOR artifact is stale or missing. It deliberately only
    refreshes the append-only public market-data store; route simulation and
    dry-run evidence remain the caller's responsibility.
    """
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    source_chain_checkpoint = (
        None
        if feature_batch_store_path is None
        else _trusted_source_checkpoint(feature_batch_store_path, now_ms=now)
    )
    books, bundle = collect_books(
        binance_store_path=binance_store_path,
        now_ms=now,
        http_get=http_get,
        clock_ms=clock_ms,
        instruments=instruments,
        source_chain_checkpoint=source_chain_checkpoint,
    )
    Path(book_store_path).parent.mkdir(parents=True, exist_ok=True)
    book_store = CrossVenueOrderBookStore(book_store_path, create=True)
    try:
        appended = book_store.append_bundle(bundle)
        if not book_store.chain_valid():
            raise PaperMultiVenueSORError("cross-venue book chain is invalid")
        book_rows = book_store.row_count()
    finally:
        book_store.close()
    return books, bundle, appended, book_rows


def _persisted_book(row: Mapping[str, Any], *, as_of_ms: int) -> VenueBook:
    instrument_id = str(row.get("instrument_id", "")).upper()
    venue_id = str(row.get("venue", ""))
    if not instrument_id.endswith("USDT") or venue_id not in FEE_ASSUMPTIONS_BPS:
        raise PaperMultiVenueSORError("persisted routing book identity is invalid")
    source = str(row.get("source", ""))
    if source not in PUBLIC_BOOK_SOURCES[venue_id]:
        raise PaperMultiVenueSORError("persisted routing book source is invalid")
    try:
        observed_at_ms = int(row["observed_at_ms"])
        latency_ms = int(row["latency_ms"])
        taker_fee_bps = Decimal(str(row["taker_fee_bps"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise PaperMultiVenueSORError(
            "persisted routing book metadata is invalid"
        ) from exc
    if (
        observed_at_ms > as_of_ms
        or as_of_ms - observed_at_ms > MAX_QUOTE_AGE_MS
        or latency_ms < 0
        or taker_fee_bps != FEE_ASSUMPTIONS_BPS[venue_id]
    ):
        raise PaperMultiVenueSORError("persisted routing book is stale or invalid")
    return VenueBook(
        venue_id=venue_id,
        observed_at_ms=observed_at_ms,
        taker_fee_bps=taker_fee_bps,
        latency_ms=latency_ms,
        bids=_levels(row.get("bids"), "persisted.bids", descending=True),
        asks=_levels(row.get("asks"), "persisted.asks", descending=False),
    )


def load_persisted_routing_snapshot(
    *,
    book_store_path: str = DEFAULT_BOOK_STORE,
    tickers: Sequence[str] = FORWARD_MAJOR_TICKERS,
    now_ms: int,
) -> tuple[dict[str, tuple[VenueBook, VenueBook]], dict[str, float], str, int]:
    """Load one exact, hash-verified full-universe routing snapshot.

    The returned books are keyed by QUANTOS ticker (``BTC``), while the
    persisted public venue identifiers remain ``BTCUSDT``.  Missing, stale,
    duplicated, fee-drifted, or partial venue coverage fails closed.
    """
    expected_tickers = tuple(str(item).upper() for item in tickers)
    if (
        not expected_tickers
        or len(expected_tickers) != len(set(expected_tickers))
        or any(not item or item.endswith("USDT") for item in expected_tickers)
    ):
        raise PaperMultiVenueSORError("paper routing ticker universe is invalid")
    store = CrossVenueOrderBookStore(book_store_path, create=False)
    try:
        if not store.chain_valid():
            raise PaperMultiVenueSORError("cross-venue order-book chain is invalid")
        bundle = store.latest()
    finally:
        store.close()
    if not isinstance(bundle, Mapping):
        raise PaperMultiVenueSORError("cross-venue routing snapshot is missing")
    try:
        snapshot_id = str(bundle["snapshot_id"])
        as_of_ms = int(bundle["observed_at_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PaperMultiVenueSORError("routing snapshot identity is invalid") from exc
    if (
        not snapshot_id
        or as_of_ms > int(now_ms) + MAX_FUTURE_SKEW_MS
        or int(now_ms) - as_of_ms > MAX_QUOTE_AGE_MS
    ):
        raise PaperMultiVenueSORError("routing snapshot is stale or in the future")
    rows = bundle.get("books")
    if not isinstance(rows, list):
        raise PaperMultiVenueSORError("routing snapshot books are missing")
    expected_instruments = {f"{ticker}USDT" for ticker in expected_tickers}
    grouped: dict[str, dict[str, VenueBook]] = {
        ticker: {} for ticker in expected_tickers
    }
    for row in rows:
        if not isinstance(row, Mapping):
            raise PaperMultiVenueSORError("persisted routing book is malformed")
        instrument_id = str(row.get("instrument_id", "")).upper()
        if instrument_id not in expected_instruments:
            continue
        ticker = instrument_id.removesuffix("USDT")
        book = _persisted_book(row, as_of_ms=as_of_ms)
        if book.venue_id in grouped[ticker]:
            raise PaperMultiVenueSORError("duplicate venue in routing snapshot")
        grouped[ticker][book.venue_id] = book
    books: dict[str, tuple[VenueBook, VenueBook]] = {}
    prices: dict[str, float] = {}
    expected_venues = set(FEE_ASSUMPTIONS_BPS)
    for ticker in expected_tickers:
        by_venue = grouped[ticker]
        if set(by_venue) != expected_venues:
            raise PaperMultiVenueSORError(
                f"routing snapshot is incomplete for {ticker}"
            )
        ordered = tuple(by_venue[name] for name in sorted(by_venue))
        if abs(ordered[0].observed_at_ms - ordered[1].observed_at_ms) > MAX_VENUE_SKEW_MS:
            raise PaperMultiVenueSORError(
                f"cross-venue quote skew is too large for {ticker}"
            )
        books[ticker] = ordered
        prices[ticker] = float(sum((_mid(book) for book in ordered), Decimal("0")) / 2)
    return books, prices, snapshot_id, as_of_ms


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
    # Quantity is not comparable across instruments.  Derive the strict integer
    # ceiling from the fixed authorized canary notional and current reference
    # prices; keep the notional gate fixed even if a caller mutates the requested
    # target.  This lets a USD 100 low-priced-coin canary run without turning the
    # quantity limit into an unbounded or price-dependent bypass.
    max_canary_quantity = max(
        int(
            (
                CANARY_MAX_TARGET_NOTIONAL
                / Decimal(str(prices[instrument_id]))
            ).to_integral_value(rounding=ROUND_CEILING)
        )
        for instrument_id in signs
    )
    # P2-1 fix: realistic limits (paper SOR is still a canary test, not production gate)
    limits = RiskLimits(
        allowed_instruments=frozenset(INSTRUMENTS),
        allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=max_canary_quantity,
        max_order_notional=float(
            CANARY_MAX_TARGET_NOTIONAL * Decimal("1.01")
        ),
        fat_finger_qty=max_canary_quantity,
        max_gross_exposure=10**7,
        max_net_exposure=10**7,
        max_leverage=5.0,  # 5x (was 100.0)
        max_single_name_pct=1.0,
        max_turnover=10.0,
        max_participation_rate=0.10,  # 10% (was 1.0 = 100%)
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
                price_as_of=books[intent.instrument_id][0].observed_at_ms,
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
    feature_batch_store_path: str | None = DEFAULT_FEATURE_BATCH_STORE,
    book_store_path: str = DEFAULT_BOOK_STORE,
    dry_run_store_path: str = DEFAULT_DRY_RUN_STORE,
    status_path: str = DEFAULT_STATUS,
    http_get: Callable[[str], object] = _http_get_json,
) -> dict[str, Any]:
    global _LAST_RELEASE_RUN_ATTESTATION
    _LAST_RELEASE_RUN_ATTESTATION = None
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    books, bundle, _appended, book_rows = refresh_persisted_routing_snapshot(
        binance_store_path=binance_store_path,
        feature_batch_store_path=feature_batch_store_path,
        book_store_path=book_store_path,
        now_ms=now,
        http_get=http_get,
        instruments=PAPER_INSTRUMENTS,
    )

    run = run_canary(
        now_ms=int(bundle["observed_at_ms"]),
        books={instrument: books[instrument] for instrument in INSTRUMENTS},
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
    canary_count = len(INSTRUMENTS)
    all_canaries_routed = (
        run.n_intents == canary_count
        and run.n_approved == canary_count
        and run.n_rejected == 0
        and len(routing) == canary_count
        and len(route_tca) == canary_count
        and len(venues) >= 2
    )
    status = {
        "schema_version": 1,
        "generated_at_ms": now,
        "status": "PASS" if all_canaries_routed else "BLOCKED",
        "mode": "L2_PAPER_EXECUTION_CANARY",
        "simulation_only": True,
        "strategy_edge_claim": False,
        "snapshot_id": bundle["snapshot_id"],
        "run_id": run.run_id,
        "instruments": list(PAPER_INSTRUMENTS),
        "canary_instruments": list(INSTRUMENTS),
        "venues": venues,
        "route_plan_count": len(routing),
        "route_tca_count": len(route_tca),
        "n_intents": run.n_intents,
        "n_approved": run.n_approved,
        "n_rejected": run.n_rejected,
        "rejected_reasons": list(run.rejected_reasons),
        "book_bundle_count": book_rows,
        "fee_policy_version": FEE_POLICY_VERSION,
        "fee_calibration": "UNCALIBRATED_RESEARCH_ASSUMPTION",
        "binance_sources": bundle["binance_sources"],
        "binance_rest_failover_used": bundle["quality"][
            "binance_rest_failover_used"
        ],
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
        raise PaperMultiVenueSORError(
            "paper SOR canary did not route every required instrument"
        )
    _LAST_RELEASE_RUN_ATTESTATION = _build_release_run_attestation(
        status,
        bundle,
        run,
        output_path=status_path,
    )
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
