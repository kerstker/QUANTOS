"""Execute autonomous realtime Alpha decisions in isolated L2 paper accounts.

This worker consumes only decisions already frozen by the realtime Signal
Shadow.  It admits every evaluated track immediately, uses the first observed
decision as a non-executed startup baseline, and sends each later decision
through the existing pre-trade, simulated SOR, dry-run fill, TCA and
reconciliation path.  The resulting ledger is physically separate from the
official Execution-Paper authority and can never earn promotion or live credit.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from decimal import Decimal
import json
import math
from pathlib import Path
import time
from typing import Any

from quantos.contracts.hashing import stable_hash
from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SAFE_BLOCKED,
    SCHEDULE_EXIT_SUCCESS,
)
from quantos.runtime_paths import runtime_state_root
from services.execution.lot_book import LotSpec, scale_snapshot_to_lots
from services.execution.realtime_engineering_paper_store import (
    RealtimeEngineeringPaperStore,
)
from services.execution.simulated_sor import SORPolicy, route_simulated_intent
from services.research.realtime_alpha_shadow_store import RealtimeAlphaShadowStore
from tools.cache_io import atomic_write_json
from tools.paper_multivenue_sor import (
    DEFAULT_BOOK_STORE,
    MAX_QUOTE_AGE_MS,
    load_persisted_routing_snapshot,
)
from tools.paper_order_preview import build_alpha_preview


ROOT = runtime_state_root()
DEFAULT_SHADOW_STORE = ROOT / "data" / "cache" / "realtime_alpha_forward_shadow.db"
DEFAULT_FEATURE_STATUS = ROOT / "data" / "cache" / "binance_realtime_feature_status.json"
DEFAULT_MARKET_STATUS = ROOT / "data" / "cache" / "binance_realtime_market_status.json"
DEFAULT_EXECUTION_STORE = ROOT / "data" / "cache" / "realtime_alpha_execution_paper.db"
DEFAULT_OUTPUT = ROOT / "data" / "cache" / "realtime_alpha_execution_paper.json"
MAX_STATUS_AGE_MS = 15 * 60_000
MAX_EXECUTION_DELAY_MS = 12 * 60_000
MAX_PENDING_DECISIONS_PER_TRACK = 16
EXECUTION_CLASS = "AUTONOMOUS_REALTIME_ENGINEERING_EXECUTION_PAPER"
EXECUTION_PROFILE = "REAL_PUBLIC_BOOK_LOT_NORMALIZED_SIMULATED_SOR_V1"


class RealtimeAlphaExecutionPaperError(RuntimeError):
    """Realtime execution-paper evidence is stale, unsafe, or conflicted."""


def _read_object(path: str | Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RealtimeAlphaExecutionPaperError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise RealtimeAlphaExecutionPaperError(f"{label} is not an object")
    return value


def load_lot_specs(
    path: str | Path,
    *,
    tickers: Sequence[str],
    now_ms: int,
) -> tuple[dict[str, LotSpec], dict[str, Any]]:
    """Load current public exchange filters as a complete lot authority."""
    status = _read_object(path, "Binance realtime market status")
    generated = status.get("generated_at_ms")
    if (
        status.get("verdict") != "PASS"
        or status.get("complete") is not True
        or type(generated) is not int
        or not 0 <= now_ms - generated <= MAX_STATUS_AGE_MS
    ):
        raise RealtimeAlphaExecutionPaperError(
            "Binance contract-filter status is stale or not PASS"
        )
    latest = status.get("latest")
    if not isinstance(latest, Mapping):
        raise RealtimeAlphaExecutionPaperError("Binance contract filters are missing")
    expected = tuple(str(item).upper() for item in tickers)
    if (
        not expected
        or len(expected) != len(set(expected))
        or any(not item or item.endswith("USDT") for item in expected)
    ):
        raise RealtimeAlphaExecutionPaperError("lot ticker universe is invalid")
    result: dict[str, LotSpec] = {}
    for ticker in sorted(expected):
        row = latest.get(ticker)
        contract = row.get("contract") if isinstance(row, Mapping) else None
        if not isinstance(contract, Mapping):
            raise RealtimeAlphaExecutionPaperError(
                f"contract filter is missing for {ticker}"
            )
        if (
            contract.get("status") != "TRADING"
            or contract.get("contract_type") != "PERPETUAL"
            or contract.get("base_asset") != ticker
            or contract.get("quote_asset") != "USDT"
        ):
            raise RealtimeAlphaExecutionPaperError(
                f"contract identity is invalid for {ticker}"
            )
        try:
            result[ticker] = LotSpec(
                ticker=ticker,
                venue_symbol=f"{ticker}USDT",
                step_size=Decimal(str(contract["step_size"])),
                min_qty=Decimal(str(contract["min_qty"])),
                min_notional=Decimal(str(contract["min_notional"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RealtimeAlphaExecutionPaperError(
                f"contract filter is malformed for {ticker}"
            ) from exc
    return result, status


def _feature_status(path: str | Path, *, now_ms: int) -> dict[str, Any]:
    status = _read_object(path, "realtime feature status")
    generated = status.get("generated_at_ms")
    batch = status.get("batch")
    if (
        status.get("status") != "PASS"
        or type(generated) is not int
        or not 0 <= now_ms - generated <= MAX_STATUS_AGE_MS
        or not isinstance(batch, Mapping)
        or batch.get("chain_valid") is not True
    ):
        raise RealtimeAlphaExecutionPaperError(
            "realtime feature evidence is stale or unsafe"
        )
    return status


def _adapt_decision(
    event: Mapping[str, Any],
    *,
    specs: Mapping[str, LotSpec],
) -> tuple[dict[str, Any], dict[str, dict[int, float]], dict[str, Any]]:
    decision = event.get("payload")
    if not isinstance(decision, Mapping):
        raise RealtimeAlphaExecutionPaperError("shadow decision payload is missing")
    candidate_id = str(decision.get("candidate_id", ""))
    try:
        decision_time = int(decision["decision_time_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RealtimeAlphaExecutionPaperError(
            "shadow decision timestamp is invalid"
        ) from exc
    raw_weights = decision.get("weights")
    raw_marks = decision.get("entry_mid_prices")
    if not isinstance(raw_weights, Mapping) or not isinstance(raw_marks, Mapping):
        raise RealtimeAlphaExecutionPaperError("shadow decision weights/marks are missing")
    weights: dict[str, float] = {}
    marks: dict[str, dict[int, float]] = {}
    quantity_units: dict[str, Any] = {}
    for venue_symbol, raw_weight in raw_weights.items():
        symbol = str(venue_symbol).upper()
        if not symbol.endswith("USDT"):
            raise RealtimeAlphaExecutionPaperError("shadow instrument is not USDT perpetual")
        ticker = symbol.removesuffix("USDT")
        spec = specs.get(ticker)
        if spec is None or spec.venue_symbol != symbol:
            raise RealtimeAlphaExecutionPaperError(
                f"lot authority is missing for {symbol}"
            )
        weight = float(raw_weight)
        native_mark = float(raw_marks.get(symbol, 0.0))
        if (
            not math.isfinite(weight)
            or not math.isfinite(native_mark)
            or native_mark <= 0
        ):
            raise RealtimeAlphaExecutionPaperError(
                f"shadow decision value is invalid for {symbol}"
            )
        weights[ticker] = weight
        marks[ticker] = {decision_time: float(Decimal(str(native_mark)) * spec.step_size)}
        quantity_units[ticker] = {
            **spec.as_dict(),
            "spec_hash": spec.content_hash,
            "position_unit": "INTEGER_EXCHANGE_STEP_LOT",
            "price_unit": "USDT_PER_LOT",
        }
    if not weights:
        raise RealtimeAlphaExecutionPaperError("shadow decision has no executable weights")
    pending = {
        "alpha_id": candidate_id,
        "decided_ts": decision_time,
        "recorded_at": int(event.get("recorded_at_ms", decision_time)),
        "weights_json": json.dumps(
            weights, sort_keys=True, separators=(",", ":"), allow_nan=False
        ),
        "source": "AUTONOMOUS_REALTIME_ENGINEERING_SHADOW",
    }
    return pending, marks, quantity_units


def _admit_preview(
    preview: Mapping[str, Any],
    *,
    source_event: Mapping[str, Any],
    quantity_units: Mapping[str, Any],
    execution_delay_ms: int,
) -> dict[str, Any]:
    if any(
        preview.get(field) is not False
        for field in (
            "live_allowed",
            "order_submission_allowed",
            "broker_connectivity",
            "broker_order_submission",
            "credentials_accessed",
        )
    ):
        raise RealtimeAlphaExecutionPaperError("preview crossed the L2 boundary")
    n_intents = int(preview.get("n_intents", -1))
    n_approved = int(preview.get("n_approved", -1))
    n_rejected = int(preview.get("n_rejected", -1))
    n_fills = int(preview.get("n_paper_fills", -1))
    residual = preview.get("reconciliation_residual")
    tca = preview.get("execution_tca")
    if (
        n_intents <= 0
        or n_intents != n_approved
        or n_rejected != 0
        or n_fills != n_approved
        or not isinstance(residual, (int, float))
        or not math.isfinite(float(residual))
        or abs(float(residual)) > 1e-6
        or not isinstance(tca, Mapping)
        or tca.get("route_tca_available") is not True
    ):
        raise RealtimeAlphaExecutionPaperError(
            "preview did not clear intent/fill/TCA/reconciliation"
        )
    positions_lots = {
        str(key): int(value)
        for key, value in (preview.get("paper_positions") or {}).items()
    }
    positions_native = {
        ticker: str(
            Decimal(lots) * Decimal(str(quantity_units[ticker]["step_size"]))
        )
        for ticker, lots in positions_lots.items()
    }
    result = dict(preview)
    result.update(
        {
            "paper_execution": "PAPER_FILLED",
            "execution_class": EXECUTION_CLASS,
            "execution_profile": EXECUTION_PROFILE,
            "official_paper_execution": "PAPER_BLOCKED",
            "official_research_risk_admission": "FULL_NINE_ENGINE_REQUIRED",
            "promotion_eligible": False,
            "forward_evidence_eligible": False,
            "deployment_eligible": False,
            "quantity_units": dict(quantity_units),
            "paper_positions_native": positions_native,
            "execution_delay_ms": int(execution_delay_ms),
            "source_shadow_event_id": source_event.get("event_id"),
            "source_shadow_row_hash": source_event.get("row_hash"),
            "source_shadow_payload_hash": stable_hash(source_event.get("payload")),
            "engineering_constraints": [
                "POST_ADMISSION_DECISIONS_ONLY",
                "SEPARATE_APPEND_ONLY_LEDGER",
                "NO_PROMOTION_CREDIT",
                "NO_FORWARD_EVIDENCE_CREDIT",
                "NO_DEPLOYMENT_CREDIT",
                "NO_LIVE_AUTHORITY",
                "NO_CROSS_ACCOUNT_DEPTH_DEPLETION",
            ],
        }
    )
    return result


def run_cycle(
    *,
    shadow_store_path: str | Path = DEFAULT_SHADOW_STORE,
    feature_status_path: str | Path = DEFAULT_FEATURE_STATUS,
    market_status_path: str | Path = DEFAULT_MARKET_STATUS,
    book_store_path: str | Path = DEFAULT_BOOK_STORE,
    execution_store_path: str | Path = DEFAULT_EXECUTION_STORE,
    output_path: str | Path = DEFAULT_OUTPUT,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now = int(time.time() * 1_000) if now_ms is None else int(now_ms)
    errors: dict[str, str] = {}
    waiting: dict[str, str] = {}
    tracks: dict[str, dict[str, Any]] = {}
    new_admissions = 0
    new_executions = 0
    source_chain_valid = False
    execution_chain_valid = False
    event_count = 0
    routing_snapshot_id: str | None = None
    routing_as_of_ms: int | None = None
    specs: dict[str, LotSpec] = {}
    admitted_ids: tuple[str, ...] = ()

    try:
        feature_status = _feature_status(feature_status_path, now_ms=now)
        with RealtimeAlphaShadowStore(str(shadow_store_path), create=False) as shadow:
            if not shadow.chain_valid():
                raise RealtimeAlphaExecutionPaperError("Signal-Shadow chain is invalid")
            source_chain_valid = True
            source_ids = shadow.admitted_candidate_ids()
            if not source_ids:
                raise RealtimeAlphaExecutionPaperError("Signal-Shadow has no admitted tracks")
            all_tickers = tuple(
                sorted(
                    {
                        str(symbol).removesuffix("USDT")
                        for candidate_id in source_ids
                        for row in shadow.decisions(candidate_id)
                        for symbol in row["payload"].get("weights", {})
                    }
                )
            )
            specs, market_status = load_lot_specs(
                market_status_path, tickers=all_tickers, now_ms=now
            )
            execution_path = Path(execution_store_path)
            with RealtimeEngineeringPaperStore(
                str(execution_path), create=not execution_path.exists()
            ) as store:
                if not store.chain_valid():
                    raise RealtimeAlphaExecutionPaperError(
                        "engineering execution ledger chain is invalid"
                    )
                for candidate_id in source_ids:
                    if store.admission(candidate_id) is not None:
                        continue
                    source_admission = shadow.admission(candidate_id)
                    decisions = shadow.decisions(candidate_id)
                    if source_admission is None or not decisions:
                        waiting[candidate_id] = "WAITING_FOR_SIGNAL_SHADOW_DECISION"
                        continue
                    baseline = int(decisions[-1]["identity_time_ms"])
                    identity = {
                        "candidate_id": candidate_id,
                        "source_shadow_admission_hash": source_admission["row_hash"],
                        "source_shadow_identity": source_admission["payload"].get("identity"),
                        "execution_class": EXECUTION_CLASS,
                        "execution_profile": EXECUTION_PROFILE,
                    }
                    new_admissions += int(
                        store.admit(
                            candidate_id,
                            identity,
                            baseline_decision_time_ms=baseline,
                            admitted_at_ms=now,
                        )
                    )

                admitted_ids = store.admitted_candidate_ids()
                books = None
                lot_prices = None
                lot_books = None
                for candidate_id in admitted_ids:
                    try:
                        admission = store.admission(candidate_id)
                        assert admission is not None
                        baseline = int(
                            admission["payload"]["baseline_decision_time_ms"]
                        )
                        executed = {
                            int(row["identity_time_ms"])
                            for row in store.executions(candidate_id)
                        }
                        pending_events = [
                            row
                            for row in shadow.decisions(candidate_id)
                            if int(row["identity_time_ms"]) > baseline
                            and int(row["identity_time_ms"]) not in executed
                        ]
                        pending_events.sort(key=lambda row: int(row["identity_time_ms"]))
                        if len(pending_events) > MAX_PENDING_DECISIONS_PER_TRACK:
                            raise RealtimeAlphaExecutionPaperError(
                                "unexecuted post-admission decision backlog exceeds bound"
                            )
                        if pending_events:
                            if books is None:
                                books, native_prices, routing_snapshot_id, routing_as_of_ms = (
                                    load_persisted_routing_snapshot(
                                        book_store_path=str(book_store_path),
                                        tickers=tuple(sorted(specs)),
                                        now_ms=now,
                                    )
                                )
                                lot_books, lot_prices = scale_snapshot_to_lots(
                                    books, native_prices, specs
                                )
                            assert routing_as_of_ms is not None
                            assert lot_books is not None and lot_prices is not None
                            for event in pending_events:
                                decision_time = int(event["identity_time_ms"])
                                if routing_as_of_ms < decision_time:
                                    waiting[candidate_id] = "WAITING_FOR_POST_DECISION_BOOK"
                                    break
                                delay = routing_as_of_ms - decision_time
                                if delay > MAX_EXECUTION_DELAY_MS:
                                    raise RealtimeAlphaExecutionPaperError(
                                        "post-decision routing snapshot is too late"
                                    )
                                pending, marks, units = _adapt_decision(
                                    event, specs=specs
                                )
                                route_policy = SORPolicy(
                                    max_quote_age_ms=MAX_QUOTE_AGE_MS,
                                    min_fill_fraction=Decimal("1"),
                                    max_venue_fraction=Decimal("1"),
                                    latency_penalty_bps_per_ms=Decimal("0"),
                                )

                                def route_planner(intent):
                                    return route_simulated_intent(
                                        intent,
                                        lot_books[intent.instrument_id],
                                        as_of_ms=routing_as_of_ms,
                                        policy=route_policy,
                                    )

                                preview_status = {
                                    "quality_evidence_current": True,
                                    "data_quality_admission": {"verdict": "PASS"},
                                    "alerts": [],
                                    "ran_at": max(
                                        int(feature_status["generated_at_ms"]),
                                        int(market_status["generated_at_ms"]),
                                        int(event["recorded_at_ms"]),
                                    ),
                                }
                                filters = {
                                    ticker: (
                                        1.0,
                                        float(spec.min_lots),
                                        float(spec.min_notional),
                                    )
                                    for ticker, spec in specs.items()
                                }
                                preview = build_alpha_preview(
                                    candidate_id,
                                    pending,
                                    mark_by_ticker=marks,
                                    status=preview_status,
                                    current_positions=store.latest_positions(candidate_id),
                                    aum=float(event["payload"]["shadow_aum_usd"]),
                                    cost_bps=0.0,
                                    generated_at_ms=now,
                                    require_full_research_risk=False,
                                    exchange_filters=filters,
                                    execution_prices=lot_prices,
                                    execution_at_ms=routing_as_of_ms,
                                    route_planner=route_planner,
                                    routing_snapshot_id=str(routing_snapshot_id),
                                    supported_alpha_ids=admitted_ids,
                                )
                                admitted_preview = _admit_preview(
                                    preview,
                                    source_event=event,
                                    quantity_units=units,
                                    execution_delay_ms=delay,
                                )
                                new_executions += int(
                                    store.record_execution(
                                        candidate_id,
                                        admitted_preview,
                                        recorded_at_ms=now,
                                    )
                                )
                        tracks[candidate_id] = store.summary(candidate_id)
                        tracks[candidate_id]["state"] = waiting.get(
                            candidate_id,
                            "EXECUTING" if tracks[candidate_id]["n_executions"] else "ARMED",
                        )
                    except Exception as exc:
                        errors[candidate_id] = f"{type(exc).__name__}: {str(exc)[:200]}"
                execution_chain_valid = store.chain_valid()
                event_count = store.event_count()
    except Exception as exc:
        errors["_system"] = f"{type(exc).__name__}: {str(exc)[:200]}"

    running = bool(
        source_chain_valid
        and execution_chain_valid
        and admitted_ids
        and not errors
    )
    report = {
        "schema_version": 1,
        "generated_at_ms": now,
        "status": "RUNNING" if running else "SAFE_BLOCKED",
        "mode": EXECUTION_CLASS,
        "execution_profile": EXECUTION_PROFILE,
        "candidate_count": len(admitted_ids),
        "cycle": {
            "new_admissions": new_admissions,
            "new_executions": new_executions,
            "waiting_count": len(waiting),
        },
        "routing": {
            "snapshot_id": routing_snapshot_id,
            "as_of_ms": routing_as_of_ms,
            "venues": ["binance_usdm", "bybit_linear"],
            "public_data_only": True,
            "simulation_only": True,
            "fee_calibration": "UNCALIBRATED_RESEARCH_ASSUMPTION",
        },
        "lot_authority": {
            "instrument_count": len(specs),
            "spec_hash": stable_hash(
                {key: value.as_dict() for key, value in sorted(specs.items())}
            ) if specs else None,
            "source": str(Path(market_status_path)),
        },
        "tracks": tracks,
        "waiting": waiting,
        "errors": errors,
        "ledger": {
            "path": str(Path(execution_store_path)),
            "append_only": True,
            "chain_valid": execution_chain_valid,
            "event_count": event_count,
            "source_shadow_chain_valid": source_chain_valid,
            "separate_from_official_paper": True,
        },
        "verdict": {
            "engineering_execution_paper_running": running,
            "production_execution_seam_exercised": any(
                row.get("n_executions", 0) > 0 for row in tracks.values()
            ),
            "official_paper_ready": False,
            "promotion_eligible": False,
            "forward_evidence_eligible": False,
            "deployment_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
        "safety": {
            "paper_only": True,
            "public_market_data_only": True,
            "credentials_accessed": False,
            "private_stream": False,
            "broker_connectivity": False,
            "broker_order_submission": False,
            "allow_ai_direct_trading": False,
        },
    }
    atomic_write_json(str(output_path), report, indent=2)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadow-store", default=str(DEFAULT_SHADOW_STORE))
    parser.add_argument("--feature-status", default=str(DEFAULT_FEATURE_STATUS))
    parser.add_argument("--market-status", default=str(DEFAULT_MARKET_STATUS))
    parser.add_argument("--book-store", default=str(DEFAULT_BOOK_STORE))
    parser.add_argument("--execution-store", default=str(DEFAULT_EXECUTION_STORE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    try:
        report = run_cycle(
            shadow_store_path=args.shadow_store,
            feature_status_path=args.feature_status,
            market_status_path=args.market_status,
            book_store_path=args.book_store,
            execution_store_path=args.execution_store,
            output_path=args.output,
        )
    except Exception as exc:
        print(f"realtime engineering execution paper failed: {type(exc).__name__}: {exc}")
        return SCHEDULE_EXIT_FAILURE
    print(
        "realtime engineering execution paper "
        f"status={report['status']} candidates={report['candidate_count']} "
        f"executions={report['cycle']['new_executions']} errors={len(report['errors'])}"
    )
    return SCHEDULE_EXIT_SUCCESS if report["status"] == "RUNNING" else SCHEDULE_EXIT_SAFE_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_EXECUTION_STORE",
    "DEFAULT_OUTPUT",
    "EXECUTION_CLASS",
    "EXECUTION_PROFILE",
    "RealtimeAlphaExecutionPaperError",
    "load_lot_specs",
    "main",
    "run_cycle",
]
