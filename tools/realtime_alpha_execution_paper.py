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
import hashlib
import json
import math
import os
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
from services.execution.backtest_authority import (
    BacktestIntegrityBlocked,
    LEGACY_INVALIDATION_CODE,
    quarantine_legacy_backtest,
)
from services.execution.lot_book import LotSpec, scale_snapshot_to_lots
from services.execution.realtime_engineering_paper_store import (
    RealtimeEngineeringPaperStore,
)
from services.execution.simulated_sor import SORPolicy, route_simulated_intent
from services.research.realtime_alpha_shadow_store import RealtimeAlphaShadowStore
from services.features.realtime_status import assess_realtime_feature_freshness
from tools.cache_io import atomic_write_json
from tools.paper_multivenue_sor import (
    DEFAULT_BINANCE_STORE,
    DEFAULT_BOOK_STORE,
    MAX_QUOTE_AGE_MS,
    load_persisted_routing_snapshot,
    refresh_persisted_routing_snapshot,
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
MAX_DECISION_RESOLUTIONS_PER_TRACK_PER_CYCLE = 64
_ROUTING_REFRESHABLE_ERRORS = frozenset(
    {
        "cross-venue routing snapshot is missing",
        "routing snapshot is stale or in the future",
    }
)
EXECUTION_CLASS = "AUTONOMOUS_REALTIME_ENGINEERING_EXECUTION_PAPER"
EXECUTION_PROFILE = "REAL_PUBLIC_BOOK_LOT_NORMALIZED_SIMULATED_SOR_V1"
_LAST_RELEASE_RUN_ATTESTATION: dict[str, Any] | None = None


class RealtimeAlphaExecutionPaperError(RuntimeError):
    """Realtime execution-paper evidence is stale, unsafe, or conflicted."""


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _is_sha256(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _execution_input_contract_hash() -> str:
    return stable_hash(
        {
            "schema_version": 1,
            "execution_class": EXECUTION_CLASS,
            "execution_profile": EXECUTION_PROFILE,
            "max_status_age_ms": MAX_STATUS_AGE_MS,
            "max_execution_delay_ms": MAX_EXECUTION_DELAY_MS,
            "max_decision_resolutions_per_track_per_cycle": (
                MAX_DECISION_RESOLUTIONS_PER_TRACK_PER_CYCLE
            ),
            "routing": {
                "max_quote_age_ms": MAX_QUOTE_AGE_MS,
                "min_fill_fraction": "1",
                "max_venue_fraction": "1",
                "latency_penalty_bps_per_ms": "0",
            },
            "require_full_research_risk": False,
            "public_market_data_only": True,
            "simulation_only": True,
        }
    )


def _build_release_run_attestation(
    report: Mapping[str, Any],
    *,
    input_hashes: Mapping[str, str],
    output_hashes: Mapping[str, str],
    output_path: str | Path,
) -> dict[str, Any]:
    release_commit = os.environ.get("QUANTOS_RELEASE_COMMIT")
    if isinstance(release_commit, str):
        release_commit = release_commit.strip().lower()
    inputs = dict(sorted(input_hashes.items()))
    outputs = dict(sorted(output_hashes.items()))
    outputs["execution_paper_report_file"] = _sha256_file(output_path)
    safety = report.get("safety")
    safety = safety if isinstance(safety, Mapping) else {}
    verdict = report.get("verdict")
    verdict = verdict if isinstance(verdict, Mapping) else {}
    safety_contract = {
        "paper_only": safety.get("paper_only"),
        "live_allowed": verdict.get("live_allowed"),
        "order_submission_allowed": verdict.get("order_submission_allowed"),
        "credentials_accessed": safety.get("credentials_accessed"),
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
        and report.get("status") in {"RUNNING", "SAFE_BLOCKED"}
        and inputs
        and outputs
        and all(_is_sha256(value) for value in inputs.values())
        and all(_is_sha256(value) for value in outputs.values())
        and safety_contract == expected_safety
    )
    return {
        "schema_version": 1,
        "module": "realtime_alpha_execution_paper",
        "release_commit": release_commit,
        "complete": complete,
        "input_hashes": inputs,
        "output_hashes": outputs,
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


def _is_routing_refreshable(error: BaseException) -> bool:
    return str(error) in _ROUTING_REFRESHABLE_ERRORS


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
    observed_hashes: dict[str, str] | None = None,
) -> tuple[dict[str, LotSpec], dict[str, Any]]:
    """Load current public exchange filters as a complete lot authority."""
    status = _read_object(path, "Binance realtime market status")
    if observed_hashes is not None:
        observed_hashes["binance_contract_filter_status"] = stable_hash(status)
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


def _feature_status(
    path: str | Path,
    *,
    now_ms: int,
    observed_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    status = _read_object(path, "realtime feature status")
    if observed_hashes is not None:
        observed_hashes["realtime_feature_status"] = stable_hash(status)
    freshness = assess_realtime_feature_freshness(
        status,
        now_ms=now_ms,
        max_age_ms=MAX_STATUS_AGE_MS,
    )
    if not freshness.ready:
        raise RealtimeAlphaExecutionPaperError(
            "realtime feature evidence is stale or unsafe: " + freshness.reason
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


def _admit_no_action(
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
    counters = tuple(
        int(preview.get(field, -1))
        for field in (
            "n_intents",
            "n_approved",
            "n_rejected",
            "n_paper_fills",
        )
    )
    residual = preview.get("reconciliation_residual")
    if (
        counters != (0, 0, 0, 0)
        or not isinstance(residual, (int, float))
        or not math.isfinite(float(residual))
        or abs(float(residual)) > 1e-6
        or not isinstance(preview.get("paper_positions"), Mapping)
    ):
        raise RealtimeAlphaExecutionPaperError(
            "zero-intent preview did not clear no-action reconciliation"
        )
    result = dict(preview)
    result.update(
        {
            "candidate_id": preview.get("alpha_id"),
            "resolution": "NO_ACTION",
            "reason_code": "NO_ACTIONABLE_INTENT",
            "paper_execution": "PAPER_NO_ACTION",
            "execution_class": EXECUTION_CLASS,
            "execution_profile": EXECUTION_PROFILE,
            "official_paper_execution": "PAPER_BLOCKED",
            "official_research_risk_admission": "FULL_NINE_ENGINE_REQUIRED",
            "promotion_eligible": False,
            "forward_evidence_eligible": False,
            "deployment_eligible": False,
            "quantity_units": dict(quantity_units),
            "execution_delay_ms": int(execution_delay_ms),
            "source_shadow_event_id": source_event.get("event_id"),
            "source_shadow_row_hash": source_event.get("row_hash"),
            "source_shadow_payload_hash": stable_hash(source_event.get("payload")),
            "paper_only": True,
            "live_allowed": False,
            "order_submission_allowed": False,
        }
    )
    return result


def run_cycle(
    *,
    shadow_store_path: str | Path = DEFAULT_SHADOW_STORE,
    feature_status_path: str | Path = DEFAULT_FEATURE_STATUS,
    market_status_path: str | Path = DEFAULT_MARKET_STATUS,
    binance_store_path: str | Path = DEFAULT_BINANCE_STORE,
    book_store_path: str | Path = DEFAULT_BOOK_STORE,
    execution_store_path: str | Path = DEFAULT_EXECUTION_STORE,
    output_path: str | Path = DEFAULT_OUTPUT,
    now_ms: int | None = None,
) -> dict[str, Any]:
    global _LAST_RELEASE_RUN_ATTESTATION
    _LAST_RELEASE_RUN_ATTESTATION = None
    now = int(time.time() * 1_000) if now_ms is None else int(now_ms)
    input_hashes = {
        "execution_input_contract": _execution_input_contract_hash(),
    }
    output_hashes: dict[str, str] = {}
    errors: dict[str, str] = {}
    waiting: dict[str, str] = {}
    tracks: dict[str, dict[str, Any]] = {}
    new_admissions = 0
    new_executions = 0
    new_no_actions = 0
    new_missed_execution_windows = 0
    source_chain_valid = False
    execution_chain_valid = False
    event_count = 0
    routing_snapshot_id: str | None = None
    routing_as_of_ms: int | None = None
    routing_refresh_attempted = False
    routing_refresh_succeeded = False
    routing_refresh_reason: str | None = None
    routing_refresh_error: str | None = None
    routing_load_error: str | None = None
    specs: dict[str, LotSpec] = {}
    admitted_ids: tuple[str, ...] = ()

    def load_routing_snapshot_for_cycle():
        nonlocal routing_refresh_attempted
        nonlocal routing_refresh_succeeded
        nonlocal routing_refresh_reason
        nonlocal routing_refresh_error
        nonlocal routing_load_error
        if routing_load_error is not None:
            raise RealtimeAlphaExecutionPaperError(routing_load_error)
        try:
            return load_persisted_routing_snapshot(
                book_store_path=str(book_store_path),
                tickers=tuple(sorted(specs)),
                now_ms=now,
            )
        except Exception as initial_error:
            store_missing = False
            try:
                store_missing = (
                    not Path(book_store_path).exists()
                    or Path(book_store_path).stat().st_size == 0
                )
            except OSError:
                store_missing = True
            if not (_is_routing_refreshable(initial_error) or store_missing):
                raise
            if routing_refresh_attempted:
                raise RealtimeAlphaExecutionPaperError(
                    "routing snapshot refresh already failed this cycle"
                ) from initial_error
            routing_refresh_attempted = True
            routing_refresh_reason = (
                "PERSISTED_BOOK_STORE_MISSING"
                if store_missing
                else str(initial_error)
            )
            try:
                refresh_persisted_routing_snapshot(
                    now_ms=now,
                    binance_store_path=str(binance_store_path),
                    book_store_path=str(book_store_path),
                )
                # A public REST failover can take longer than one execution
                # cycle.  Validate the freshly appended snapshot against the
                # refresh-completion clock, otherwise a fresh quote can be
                # rejected as future relative to the cycle-start timestamp.
                reload_now_ms = max(now, int(time.time() * 1000))
                result = load_persisted_routing_snapshot(
                    book_store_path=str(book_store_path),
                    tickers=tuple(sorted(specs)),
                    now_ms=reload_now_ms,
                )
                routing_refresh_succeeded = True
                return result
            except Exception as refresh_error:
                routing_refresh_error = (
                    f"{type(refresh_error).__name__}: {str(refresh_error)[:200]}"
                )
                routing_load_error = (
                    f"routing snapshot refresh failed: {routing_refresh_error}"
                )
                raise RealtimeAlphaExecutionPaperError(
                    routing_load_error
                ) from refresh_error

    try:
        feature_status = _feature_status(
            feature_status_path,
            now_ms=now,
            observed_hashes=input_hashes,
        )
        with RealtimeAlphaShadowStore(str(shadow_store_path), create=False) as shadow:
            with shadow.read_snapshot():
                input_hashes["signal_shadow_authority"] = shadow.authority_hash()
                source_ids = shadow.admitted_candidate_ids()
                source_admissions = {
                    candidate_id: shadow.admission(candidate_id)
                    for candidate_id in source_ids
                }
                source_decisions = {
                    candidate_id: shadow.decisions(candidate_id)
                    for candidate_id in source_ids
                }
            source_chain_valid = True
            if not source_ids:
                raise RealtimeAlphaExecutionPaperError("Signal-Shadow has no admitted tracks")
            all_tickers = tuple(
                sorted(
                    {
                        str(symbol).removesuffix("USDT")
                        for candidate_id in source_ids
                        for row in source_decisions[candidate_id]
                        for symbol in row["payload"].get("weights", {})
                    }
                )
            )
            specs, market_status = load_lot_specs(
                market_status_path,
                tickers=all_tickers,
                now_ms=now,
                observed_hashes=input_hashes,
            )
            execution_path = Path(execution_store_path)
            with RealtimeEngineeringPaperStore(
                str(execution_path),
                # Recover only the zero-byte artifact left by an interrupted
                # first initialization.  A non-empty invalid ledger must
                # remain fail-closed.
                create=(
                    not execution_path.exists()
                    or execution_path.stat().st_size == 0
                ),
            ) as store:
                input_hashes["execution_authority_before"] = store.authority_hash()
                for candidate_id in source_ids:
                    if store.admission(candidate_id) is not None:
                        continue
                    source_admission = source_admissions[candidate_id]
                    decisions = source_decisions[candidate_id]
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
                        resolved = store.resolved_decision_times(candidate_id)
                        pending_events = [
                            row
                            for row in source_decisions[candidate_id]
                            if int(row["identity_time_ms"]) > baseline
                            and int(row["identity_time_ms"]) not in resolved
                        ]
                        pending_events.sort(key=lambda row: int(row["identity_time_ms"]))
                        backlog_remaining = max(
                            0,
                            len(pending_events)
                            - MAX_DECISION_RESOLUTIONS_PER_TRACK_PER_CYCLE,
                        )
                        pending_events = pending_events[
                            :MAX_DECISION_RESOLUTIONS_PER_TRACK_PER_CYCLE
                        ]
                        if pending_events:
                            if books is None:
                                books, native_prices, routing_snapshot_id, routing_as_of_ms = (
                                    load_routing_snapshot_for_cycle()
                                )
                                input_hashes["routing_snapshot"] = str(
                                    routing_snapshot_id
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
                                    missed_payload = {
                                        "candidate_id": candidate_id,
                                        "decision_time_ms": decision_time,
                                        "resolution": "MISSED_EXECUTION_WINDOW",
                                        "reason_code": "POST_DECISION_BOOK_DELAY_EXCEEDED",
                                        "routing_snapshot_id": routing_snapshot_id,
                                        "routing_as_of_ms": routing_as_of_ms,
                                        "execution_delay_ms": delay,
                                        "max_execution_delay_ms": MAX_EXECUTION_DELAY_MS,
                                        "source_shadow_event_id": event.get("event_id"),
                                        "source_shadow_row_hash": event.get("row_hash"),
                                        "source_shadow_payload_hash": stable_hash(
                                            event.get("payload")
                                        ),
                                        "paper_only": True,
                                        "promotion_eligible": False,
                                        "forward_evidence_eligible": False,
                                        "deployment_eligible": False,
                                        "live_allowed": False,
                                        "order_submission_allowed": False,
                                    }
                                    new_missed_execution_windows += int(
                                        store.record_missed_decision(
                                            candidate_id,
                                            missed_payload,
                                            decision_time_ms=decision_time,
                                            recorded_at_ms=now,
                                        )
                                    )
                                    continue
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
                                if int(preview.get("n_intents", -1)) == 0:
                                    no_action = _admit_no_action(
                                        preview,
                                        source_event=event,
                                        quantity_units=units,
                                        execution_delay_ms=delay,
                                    )
                                    new_no_actions += int(
                                        store.record_no_action(
                                            candidate_id,
                                            no_action,
                                            decision_time_ms=decision_time,
                                            recorded_at_ms=now,
                                        )
                                    )
                                else:
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
                            if backlog_remaining:
                                waiting[candidate_id] = (
                                    "CATCHING_UP_DECISION_BACKLOG:"
                                    f"{backlog_remaining}"
                                )
                        tracks[candidate_id] = store.summary(candidate_id)
                        tracks[candidate_id]["state"] = waiting.get(
                            candidate_id,
                            "EXECUTING" if tracks[candidate_id]["n_executions"] else "ARMED",
                        )
                    except Exception as exc:
                        errors[candidate_id] = f"{type(exc).__name__}: {str(exc)[:200]}"
                output_hashes["execution_authority_after"] = store.authority_hash()
                execution_chain_valid = True
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
            "new_no_actions": new_no_actions,
            "new_missed_execution_windows": new_missed_execution_windows,
            "waiting_count": len(waiting),
        },
        "routing": {
            "snapshot_id": routing_snapshot_id,
            "as_of_ms": routing_as_of_ms,
            "venues": ["binance_usdm", "bybit_linear"],
            "public_data_only": True,
            "simulation_only": True,
            "fee_calibration": "UNCALIBRATED_RESEARCH_ASSUMPTION",
            "snapshot_refresh": {
                "attempted": routing_refresh_attempted,
                "succeeded": routing_refresh_succeeded,
                "reason": routing_refresh_reason,
                "error": routing_refresh_error,
                "source": (
                    "BINANCE_PUBLIC_USDM_WEBSOCKET_OR_REST_FAILOVER_AND_BYBIT_PUBLIC_LINEAR_REST"
                    if routing_refresh_attempted
                    else None
                ),
            },
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
            "missed_execution_window_count": sum(
                int(row.get("n_missed_execution_windows", 0))
                for row in tracks.values()
            ),
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
    _LAST_RELEASE_RUN_ATTESTATION = _build_release_run_attestation(
        report,
        input_hashes=input_hashes,
        output_hashes=output_hashes,
        output_path=output_path,
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadow-store", default=str(DEFAULT_SHADOW_STORE))
    parser.add_argument("--feature-status", default=str(DEFAULT_FEATURE_STATUS))
    parser.add_argument("--market-status", default=str(DEFAULT_MARKET_STATUS))
    parser.add_argument("--binance-store", default=str(DEFAULT_BINANCE_STORE))
    parser.add_argument("--book-store", default=str(DEFAULT_BOOK_STORE))
    parser.add_argument("--execution-store", default=str(DEFAULT_EXECUTION_STORE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    global _LAST_RELEASE_RUN_ATTESTATION
    _LAST_RELEASE_RUN_ATTESTATION = None
    try:
        quarantine_legacy_backtest("realtime_alpha_execution_paper")
    except BacktestIntegrityBlocked:
        report = {
            "schema_version": 2,
            "flow": "REALTIME_ALPHA_EXECUTION_PAPER_QUARANTINE",
            "status": "SAFE_BLOCKED",
            "generated_at_ms": int(time.time() * 1_000),
            "candidate_count": 0,
            "cycle": {
                "new_admissions": 0,
                "new_executions": 0,
                "new_no_actions": 0,
                "new_missed_execution_windows": 0,
            },
            "errors": {"_system": LEGACY_INVALIDATION_CODE},
            "ledger": {
                "path": str(Path(args.execution_store)),
                "write_attempted": False,
                "chain_valid": None,
                "legacy_v1_retained_read_only": True,
            },
            "verdict": {
                "engineering_execution_paper_running": False,
                "production_execution_seam_exercised": False,
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
                "raw_collection_allowed": True,
                "read_only_forward_observation_allowed": True,
            },
        }
        atomic_write_json(str(args.output), report, indent=2)
        print(
            "realtime engineering execution paper status=SAFE_BLOCKED "
            f"reason={LEGACY_INVALIDATION_CODE}"
        )
        return SCHEDULE_EXIT_SAFE_BLOCKED
    raise AssertionError("legacy realtime execution-paper quarantine unexpectedly returned")

    try:
        report = run_cycle(
            shadow_store_path=args.shadow_store,
            feature_status_path=args.feature_status,
            market_status_path=args.market_status,
            binance_store_path=args.binance_store,
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
        f"executions={report['cycle']['new_executions']} "
        f"no_actions={report['cycle']['new_no_actions']} "
        f"errors={len(report['errors'])}"
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
    "release_run_attestation",
    "run_cycle",
]
