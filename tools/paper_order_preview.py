"""Generate an operator-safe paper order preview for the current Alpha cycle.

This is the smallest usable execution seam for the current L2 release.  It
reads the newest *attested* forward decision, uses only the local public mark
price cache, and sends the resulting intents through the existing pre-trade
risk gate and :class:`DryRunBrokerAdapter`.  It never reads credentials,
opens a broker connection, or submits an order.

The output is an atomic, read-only operator artifact.  A preview is not a live
authorization and does not advance the append-only forward ledger; forward
realization remains a separate evidence step.  Paper fills additionally
require an approved, complete nine-engine research-risk snapshot.  A legacy
four-engine subset remains diagnostic only and is reported as blocked.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import time
from collections.abc import Mapping
from typing import Any

# Keep the documented direct-script entrypoint import-safe from the repository
# root.  This does not alter package imports when the module is run with ``-m``.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from quantos.contracts.portfolio import Position
from services.execution.dry_run_pipeline import run_dry_run_pipeline
from services.execution.paper_execution_store import PaperExecutionStore
from services.research.paper_trading.attested_store import AttestedForwardLedger
from services.risk.research_risk.bundle import load_research_risk_bundle
from services.risk.research_risk.dry_run_guard import DryRunResearchRiskInputs
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (
    AccountRiskState,
    MarketContext,
    RiskContext,
    StrategyApproval,
)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.broker_adapter import DryRunBrokerAdapter
from tools.binance_oi_metrics_fetch import FORWARD_MAJOR_TICKERS
from tools.binance_perp_mark_fetch import load_mark_prices
from tools.cache_io import atomic_write_json


DEFAULT_FORWARD_DB = os.path.join("data", "cache", "paper_track.db")
DEFAULT_MARK_CACHE = os.path.join("data", "cache", "crypto_perp_mark_prices.json")
DEFAULT_STATUS = os.path.join("data", "cache", "forward_track_status.json")
DEFAULT_OUTPUT = os.path.join("data", "cache", "paper_order_preview.json")
DEFAULT_PAPER_STORE = os.path.join("data", "cache", "paper_order_ledger.db")
DEFAULT_RESEARCH_RISK_BUNDLE = os.path.join(
    "data", "cache", "paper_research_risk_inputs.json"
)
ALPHA_IDS = ("carry_oi_majors", "crowd_contra", "top_contra")
DEFAULT_AUM = 1_000_000.0
DEFAULT_COST_BPS = 10.0
MAX_DECISION_AGE_DAYS = 8
FULL_RESEARCH_RISK_ENGINES = frozenset(
    {
        "market",
        "liquidity",
        "concentration",
        "correlation",
        "drawdown",
        "regime",
        "stress",
        "capacity",
        "model_alpha",
    }
)


def _load_json(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _status_guard(status: Mapping[str, Any], *, recorded_at: int) -> None:
    """Refuse a preview when the public evidence snapshot is not current."""
    if status.get("quality_evidence_current") is not True:
        raise RuntimeError("forward quality evidence is not current")
    admission = status.get("data_quality_admission")
    if not isinstance(admission, Mapping) or admission.get("verdict") != "PASS":
        raise RuntimeError("data-quality admission is not PASS")
    if status.get("alerts"):
        raise RuntimeError("forward status contains active alerts")
    ran_at = status.get("ran_at")
    if isinstance(ran_at, int) and ran_at < int(recorded_at):
        raise RuntimeError("forward status predates the attested decision")
    staleness = status.get("staleness_days")
    if isinstance(staleness, Mapping):
        stale_sources = []
        for source, row in staleness.items():
            if not isinstance(row, Mapping):
                stale_sources.append(str(source))
                continue
            worst = row.get("worst_days")
            if not isinstance(worst, (int, float)) or not math.isfinite(float(worst)):
                stale_sources.append(str(source))
            elif float(worst) > MAX_DECISION_AGE_DAYS:
                stale_sources.append(str(source))
        if stale_sources:
            raise RuntimeError(f"forward data is stale: {sorted(stale_sources)}")


def _full_research_risk_approved(risk: object) -> bool:
    """Return whether the complete nine-engine research gate admitted a run.

    The four-engine subset is useful for diagnostics, but it is not an order
    admission decision.  Keep this predicate deliberately structural so a
    stale or hand-edited ``decision`` field cannot turn a partial snapshot into
    a paper fill.
    """
    if not isinstance(risk, Mapping):
        return False
    engines_run = risk.get("engines_run")
    return (
        risk.get("decision") == "RISK_APPROVED"
        and risk.get("status") == "APPROVED"
        and risk.get("provenance") == "REAL_RESEARCH_RISK_FULL"
        and risk.get("gates_run") == len(FULL_RESEARCH_RISK_ENGINES)
        and risk.get("gates_passed") == len(FULL_RESEARCH_RISK_ENGINES)
        and isinstance(engines_run, Mapping)
        and set(engines_run) == set(FULL_RESEARCH_RISK_ENGINES)
        and all(value is True for value in engines_run.values())
        and risk.get("engines_skipped") == {}
        and risk.get("engines_pending") == {}
    )


def _latest_attested_pending(path: str, alpha_id: str) -> tuple[dict[str, Any], int]:
    ledger = AttestedForwardLedger(path, create=False)
    try:
        pending = ledger.attested_pending(alpha_id)
    finally:
        ledger.close()
    if not pending:
        raise RuntimeError(f"no attested pending decision for {alpha_id}")
    # The current worker normally leaves one row.  Refuse an unresolved
    # backlog rather than silently skipping an older rebalance.
    if len(pending) != 1:
        raise RuntimeError(
            f"{alpha_id} has {len(pending)} unresolved attested decisions; "
            "realize or review the backlog before previewing a new order"
        )
    row = dict(pending[0])
    # ``attested_pending`` historically returned a row without alpha_id.  The
    # bundle digest binds the decision identity, so make the identity explicit
    # at this boundary without changing the append-only ledger schema.
    row["alpha_id"] = str(alpha_id)
    return row, len(pending)


def _decision_inputs(
    pending: Mapping[str, Any],
    mark_by_ticker: Mapping[str, Mapping[int, float]],
) -> tuple[int, dict[str, float], dict[str, float]]:
    try:
        decision_ts = int(pending["decided_ts"])
        weights = json.loads(str(pending["weights_json"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("attested decision payload is malformed") from exc
    if not isinstance(weights, Mapping) or not weights:
        raise RuntimeError("attested decision has no weights")
    normalized = {str(name): float(weight) for name, weight in weights.items()}
    if not all(math.isfinite(value) for value in normalized.values()):
        raise RuntimeError("attested decision contains non-finite weights")
    outside = sorted(set(normalized) - set(FORWARD_MAJOR_TICKERS))
    if outside:
        raise RuntimeError(f"decision exceeds the major-universe scope: {outside}")

    prices: dict[str, float] = {}
    missing: list[str] = []
    for instrument in sorted(normalized):
        days = mark_by_ticker.get(instrument)
        value = days.get(decision_ts) if isinstance(days, Mapping) else None
        if value is None or not math.isfinite(float(value)) or float(value) <= 0:
            missing.append(instrument)
        else:
            prices[instrument] = float(value)
    if missing:
        raise RuntimeError(
            f"mark-price cache has no exact decision-time price for {missing}"
        )
    return decision_ts, normalized, prices


def _paper_gate(*, alpha_id: str, instruments: set[str], aum: float) -> PreTradeRiskGate:
    """Build an explicitly paper-scoped gate with no live permissions."""
    limits = RiskLimits(
        allowed_instruments=frozenset(instruments),
        allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=10**9,
        max_order_notional=max(1_000_000.0, aum),
        fat_finger_qty=10**9,
        max_gross_exposure=max(2.0 * aum, 1.0),
        max_net_exposure=max(aum, 1.0),
        max_leverage=2.0,
        max_single_name_pct=0.20,
        max_turnover=2.0,
        max_orders_per_window=50,
        max_participation_rate=0.10,
        max_instrument_vol=1.0,
        daily_loss_limit=max(aum, 1.0),
        max_drawdown=1.0,
    )
    # The gate constructor itself asserts the research-safe SafetyConfig.  A
    # fresh, untripped switch is only for this isolated paper evaluation.
    return PreTradeRiskGate(limits, kill_switch=KillSwitch())


def build_alpha_preview(
    alpha_id: str,
    pending: Mapping[str, Any],
    *,
    mark_by_ticker: Mapping[str, Mapping[int, float]],
    status: Mapping[str, Any],
    current_positions: Mapping[str, int] | None = None,
    aum: float = DEFAULT_AUM,
    cost_bps: float = DEFAULT_COST_BPS,
    generated_at_ms: int | None = None,
    research_risk_inputs: DryRunResearchRiskInputs | None = None,
    research_risk_bundle_status: Mapping[str, Any] | None = None,
    require_full_research_risk: bool = True,
) -> dict[str, Any]:
    """Run one attested decision through the real paper execution chain.

    Paper order admission is fail-closed on the complete nine-engine research
    risk result.  ``research_risk_inputs`` must therefore come from the same
    PIT snapshot as the decision when the caller wants a fill.  Omitting it is
    intentional and observable: the pipeline returns a pending full-risk
    result and no paper fills are admitted.
    """
    if alpha_id not in ALPHA_IDS:
        raise ValueError(f"unsupported alpha: {alpha_id}")
    if not math.isfinite(float(aum)) or float(aum) <= 0:
        raise ValueError("aum must be a positive finite number")
    if not math.isfinite(float(cost_bps)) or float(cost_bps) < 0:
        raise ValueError("cost_bps must be a non-negative finite number")
    decision_ts, weights, prices = _decision_inputs(pending, mark_by_ticker)
    _status_guard(status, recorded_at=int(pending.get("recorded_at", 0) or 0))
    held = {str(name): int(quantity) for name, quantity in (current_positions or {}).items()}
    outside_held = sorted(set(held) - set(FORWARD_MAJOR_TICKERS))
    if outside_held:
        raise RuntimeError(f"paper positions exceed the major-universe scope: {outside_held}")
    missing_held: list[str] = []
    for instrument in held:
        if instrument in prices:
            continue
        days = mark_by_ticker.get(instrument)
        value = days.get(decision_ts) if isinstance(days, Mapping) else None
        if value is None or not math.isfinite(float(value)) or float(value) <= 0:
            missing_held.append(instrument)
        else:
            prices[instrument] = float(value)
    if missing_held:
        raise RuntimeError(
            f"mark-price cache has no exact decision-time price for held {missing_held}"
        )
    instruments = set(weights) | set(held)
    gate = _paper_gate(alpha_id=alpha_id, instruments=instruments, aum=float(aum))
    account_positions = tuple(
        Position(
            instrument_id=instrument,
            quantity=quantity,
            avg_cost=prices[instrument],
            market_value=quantity * prices[instrument],
        )
        for instrument, quantity in sorted(held.items())
        if quantity != 0
    )

    def ctx_factory(intent):
        return RiskContext(
            account=AccountRiskState(
                account_id="paper",
                cash=float(aum),
                equity=float(aum),
                day_pnl=0.0,
                peak_equity=float(aum),
                positions=account_positions,
            ),
            market=MarketContext(
                instrument_id=intent.instrument_id,
                known=True,
                reference_price=prices[intent.instrument_id],
                market_open=True,
                price_as_of=decision_ts,
                est_daily_volume=1e18,
                instrument_vol=0.20,
            ),
            strategy=StrategyApproval(
                strategy_id=alpha_id,
                approved=True,
                approval_id=f"paper-preview:{alpha_id}:{decision_ts}",
            ),
            now=decision_ts,
            max_staleness=10**15,
        )

    run = run_dry_run_pipeline(
        weights=weights,
        prices=prices,
        current_positions=held,
        aum=float(aum),
        gate=gate,
        ctx_factory=ctx_factory,
        adapter=DryRunBrokerAdapter(),
        strategy_id=alpha_id,
        timestamp=decision_ts,
        marks=prices,
        initial_capital=float(aum),
        cost_bps=float(cost_bps),
        event_bus=None,
        publish_completion_event=False,
        research_risk_inputs=research_risk_inputs,
        require_full_research_risk=bool(require_full_research_risk),
    )
    targets = {
        str(name): int(quantity)
        for name, quantity in (run.detail.get("targets") or {}).items()
    }
    filled_delta = {
        str(name): int(quantity)
        for name, quantity in (run.pnl.get("positions") or {}).items()
    }
    positions = {
        instrument: int(held.get(instrument, 0) + filled_delta.get(instrument, 0))
        for instrument in set(held) | set(filled_delta)
        if int(held.get(instrument, 0) + filled_delta.get(instrument, 0)) != 0
    }
    orders = []
    for instrument in sorted(set(targets) | set(held)):
        quantity = int(targets.get(instrument, 0) - held.get(instrument, 0))
        if quantity == 0:
            continue
        price = prices[instrument]
        orders.append(
            {
                "instrument": instrument,
                "side": "BUY" if quantity > 0 else "SELL",
                "quantity": abs(quantity),
                "target_signed_quantity": quantity,
                "decision_mark_price": price,
                "notional": round(abs(quantity) * price, 6),
                "paper_position_before": int(held.get(instrument, 0)),
                "paper_position_after_fill": positions.get(instrument, 0),
            }
        )
    pretrade_filled = (
        run.n_intents == run.n_approved
        and run.n_rejected == 0
        and int(run.detail.get("n_fills", 0)) == run.n_approved
        and abs(float(run.pnl.get("residual", 1.0))) <= 1e-6
    )
    risk_snapshot = dict(run.detail.get("research_risk") or {})
    full_risk_approved = _full_research_risk_approved(risk_snapshot)
    fully_filled = pretrade_filled and full_risk_approved
    rejected_reasons = [
        str(reason) for reason in (getattr(run, "rejected_reasons", []) or [])
    ]
    if not full_risk_approved:
        rejected_reasons.append("FULL_RESEARCH_RISK_NOT_APPROVED")
    return {
        "alpha_id": alpha_id,
        "decision_ts": decision_ts,
        "decision_source": str(pending.get("source", "")),
        "decision_recorded_at": int(pending.get("recorded_at", 0) or 0),
        "scope": sorted(instruments),
        "paper_positions_before": dict(sorted(held.items())),
        "paper_positions": dict(sorted(positions.items())),
        "orders": orders,
        "n_intents": int(run.n_intents),
        "n_approved": int(run.n_approved),
        "n_rejected": int(run.n_rejected),
        "rejected_reasons": rejected_reasons,
        "n_paper_fills": int(run.detail.get("n_fills", 0)),
        "paper_execution": "PAPER_FILLED" if fully_filled else "PAPER_BLOCKED",
        "pnl": dict(run.pnl),
        "reconciliation_residual": run.pnl.get("residual"),
        "research_risk": risk_snapshot,
        "research_risk_bundle": dict(research_risk_bundle_status or {
            "status": "NOT_PROVIDED",
            "provenance": "REAL_RESEARCH_RISK_INPUT_BUNDLE",
            "alpha_id": alpha_id,
            "missing": ["bundle_file"],
            "reasons": ["missing:bundle_file"],
        }),
        "research_risk_admission": (
            "FULL_NINE_ENGINE_APPROVED"
            if full_risk_approved
            else "FULL_NINE_ENGINE_REQUIRED"
        ),
        "run_id": run.run_id,
        "audit_root": run.audit_root,
        "aum": float(aum),
        "cost_bps": float(cost_bps),
        "generated_at": int(generated_at_ms if generated_at_ms is not None else time.time() * 1000),
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "broker_connectivity": False,
        "broker_order_submission": False,
        "credentials_accessed": False,
    }


def _sizing_mismatch_preview(
    alpha_id: str,
    *,
    decision_ts: int,
    existing: Mapping[str, Any],
    aum: float,
    cost_bps: float,
    generated_at_ms: int | None,
) -> dict[str, Any]:
    """Return an explicit block when a paper account profile is reused.

    The append-only store intentionally keys a decision by ``(alpha_id,
    decision_ts)``.  Replacing that row for a different account size would
    silently change the paper account's positions, while replaying it would
    make ``--aum`` lie.  A separate store is the safe operator escape hatch.
    """
    reason = "PAPER_PREVIEW_SIZING_MISMATCH_USE_SEPARATE_PAPER_STORE"
    return {
        "alpha_id": alpha_id,
        "decision_ts": decision_ts,
        "decision_source": str(existing.get("decision_source", "")),
        "decision_recorded_at": int(existing.get("decision_recorded_at", 0) or 0),
        "scope": list(existing.get("scope") or []),
        "paper_positions_before": dict(existing.get("paper_positions_before") or {}),
        "paper_positions": dict(existing.get("paper_positions") or {}),
        "orders": [],
        "n_intents": 0,
        "n_approved": 0,
        "n_rejected": 0,
        "rejected_reasons": [reason],
        "n_paper_fills": 0,
        "paper_execution": "PAPER_BLOCKED",
        "pnl": {},
        "reconciliation_residual": None,
        "research_risk": {},
        "run_id": str(existing.get("run_id", "")),
        "audit_root": str(existing.get("audit_root", "")),
        "aum": float(aum),
        "cost_bps": float(cost_bps),
        "existing_aum": existing.get("aum"),
        "existing_cost_bps": existing.get("cost_bps"),
        "generated_at": int(
            generated_at_ms if generated_at_ms is not None else time.time() * 1000
        ),
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "broker_connectivity": False,
        "broker_order_submission": False,
        "credentials_accessed": False,
    }


def run_preview(
    *,
    alpha_ids: tuple[str, ...] = ALPHA_IDS,
    forward_db: str = DEFAULT_FORWARD_DB,
    mark_cache: str = DEFAULT_MARK_CACHE,
    status_path: str = DEFAULT_STATUS,
    output_path: str = DEFAULT_OUTPUT,
    paper_store_path: str = DEFAULT_PAPER_STORE,
    research_risk_bundle_path: str = DEFAULT_RESEARCH_RISK_BUNDLE,
    aum: float = DEFAULT_AUM,
    cost_bps: float = DEFAULT_COST_BPS,
    generated_at_ms: int | None = None,
) -> dict[str, Any]:
    """Build and atomically publish previews for one or all three Alphas."""
    selected = tuple(alpha_ids)
    if not selected or any(alpha not in ALPHA_IDS for alpha in selected):
        raise ValueError("alpha_ids must be a non-empty subset of the three registered Alphas")
    status = _load_json(status_path)
    mark_by_ticker = load_mark_prices(mark_cache)
    previews: dict[str, Any] = {}
    errors: dict[str, Any] = {}
    store = PaperExecutionStore(paper_store_path, create=True)
    try:
        for alpha_id in selected:
            try:
                pending, _ = _latest_attested_pending(forward_db, alpha_id)
                pending["alpha_id"] = str(alpha_id)
                decision_ts, _, _ = _decision_inputs(pending, mark_by_ticker)
                _status_guard(status, recorded_at=int(pending.get("recorded_at", 0) or 0))
                bundle_result = load_research_risk_bundle(
                    research_risk_bundle_path,
                    alpha_id=alpha_id,
                    pending=pending,
                )
                existing = store.get_by_decision(alpha_id, decision_ts)
                if existing is not None:
                    stored_aum = existing.get("aum")
                    stored_cost_bps = existing.get("cost_bps")
                    profile_matches = (
                        isinstance(stored_aum, (int, float))
                        and not isinstance(stored_aum, bool)
                        and math.isfinite(float(stored_aum))
                        and math.isclose(
                            float(stored_aum), float(aum), rel_tol=0.0, abs_tol=1e-9
                        )
                        and isinstance(stored_cost_bps, (int, float))
                        and not isinstance(stored_cost_bps, bool)
                        and math.isfinite(float(stored_cost_bps))
                        and math.isclose(
                            float(stored_cost_bps),
                            float(cost_bps),
                            rel_tol=0.0,
                            abs_tol=1e-9,
                        )
                    )
                    if not profile_matches:
                        previews[alpha_id] = _sizing_mismatch_preview(
                            alpha_id,
                            decision_ts=decision_ts,
                            existing=existing,
                            aum=aum,
                            cost_bps=cost_bps,
                            generated_at_ms=generated_at_ms,
                        )
                        errors[alpha_id] = {
                            "error_type": "PaperSizingMismatch",
                            "error": (
                                "paper decision already exists at a different sizing "
                                "profile; use a separate --paper-store"
                            ),
                            "reason": "PAPER_PREVIEW_SIZING_MISMATCH_USE_SEPARATE_PAPER_STORE",
                            "existing_aum": existing.get("aum"),
                            "existing_cost_bps": existing.get("cost_bps"),
                            "requested_aum": float(aum),
                            "requested_cost_bps": float(cost_bps),
                            "paper_execution": "PAPER_BLOCKED",
                            "live_allowed": False,
                            "order_submission_allowed": False,
                        }
                        continue
                    if not _full_research_risk_approved(existing.get("research_risk")):
                        # This can only be a legacy subset preview or a stale
                        # artifact produced before the nine-engine contract.
                        # The append-only store must not be rewritten; expose
                        # the historical row as blocked and require a new
                        # decision after complete evidence is available.
                        blocked = dict(existing)
                        blocked["generated_at"] = int(
                            generated_at_ms
                            if generated_at_ms is not None
                            else time.time() * 1000
                        )
                        blocked["replayed_from_store"] = True
                        blocked["paper_execution"] = "PAPER_BLOCKED"
                        reasons = list(blocked.get("rejected_reasons") or [])
                        if "FULL_RESEARCH_RISK_NOT_APPROVED" not in reasons:
                            reasons.append("FULL_RESEARCH_RISK_NOT_APPROVED")
                        blocked["rejected_reasons"] = reasons
                        blocked["research_risk_admission"] = (
                            "FULL_NINE_ENGINE_REQUIRED"
                        )
                        blocked["research_risk_bundle"] = dict(bundle_result.status)
                        previews[alpha_id] = blocked
                        errors[alpha_id] = {
                            "error_type": "PaperResearchRiskNotFull",
                            "error": (
                                "stored paper preview is not backed by a complete "
                                "nine-engine research-risk approval"
                            ),
                            "reason": "FULL_RESEARCH_RISK_NOT_APPROVED",
                            "research_risk": dict(existing.get("research_risk") or {}),
                            "paper_execution": "PAPER_BLOCKED",
                            "live_allowed": False,
                            "order_submission_allowed": False,
                        }
                        continue
                    existing["generated_at"] = int(
                        generated_at_ms if generated_at_ms is not None else time.time() * 1000
                    )
                    existing["replayed_from_store"] = True
                    existing["rejected_reasons"] = list(
                        existing.get("rejected_reasons") or []
                    )
                    existing["research_risk_bundle"] = dict(bundle_result.status)
                    previews[alpha_id] = existing
                    continue
                preview = build_alpha_preview(
                    alpha_id,
                    pending,
                    mark_by_ticker=mark_by_ticker,
                    status=status,
                    current_positions=store.latest_positions(alpha_id),
                    aum=aum,
                    cost_bps=cost_bps,
                    generated_at_ms=generated_at_ms,
                    research_risk_inputs=bundle_result.inputs,
                    research_risk_bundle_status=bundle_result.status,
                )
                if preview.get("paper_execution") == "PAPER_FILLED":
                    store.append_preview(preview)
                previews[alpha_id] = preview
                if preview.get("paper_execution") != "PAPER_FILLED":
                    errors[alpha_id] = {
                        "error_type": "PaperResearchRiskNotFull",
                        "error": (
                            "paper preview is blocked until complete nine-engine "
                            "research-risk evidence is approved"
                        ),
                        "reason": "FULL_RESEARCH_RISK_NOT_APPROVED",
                        "research_risk": dict(preview.get("research_risk") or {}),
                        "research_risk_bundle": dict(
                            preview.get("research_risk_bundle") or {}
                        ),
                        "paper_execution": "PAPER_BLOCKED",
                        "live_allowed": False,
                        "order_submission_allowed": False,
                    }
            except Exception as exc:
                errors[alpha_id] = {
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:240],
                    "paper_execution": "PAPER_BLOCKED",
                    "live_allowed": False,
                    "order_submission_allowed": False,
                }
    finally:
        store.close()
    ready = bool(previews) and not errors and all(
        row.get("paper_execution") == "PAPER_FILLED"
        and _full_research_risk_approved(row.get("research_risk"))
        for row in previews.values()
    )
    report = {
        "schema_version": 1,
        "flow": "PAPER_ORDER_PREVIEW",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at": int(generated_at_ms if generated_at_ms is not None else time.time() * 1000),
        "alphas": previews,
        "errors": errors,
        "verdict": {
            "paper_preview_ready": ready,
            "live_allowed": False,
            "order_submission_allowed": False,
            "human_review_required": True,
        },
        "safety": {
            "paper_only": True,
            "broker_connectivity": False,
            "broker_order_submission": False,
            "credentials_accessed": False,
            "private_binance_access": False,
        },
    }
    atomic_write_json(output_path, report, indent=2)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alpha", choices=("all", *ALPHA_IDS), default="all")
    parser.add_argument("--aum", type=float, default=DEFAULT_AUM, help="paper notional only")
    parser.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    parser.add_argument("--forward-db", default=DEFAULT_FORWARD_DB)
    parser.add_argument("--mark-cache", default=DEFAULT_MARK_CACHE)
    parser.add_argument("--status", default=DEFAULT_STATUS)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--paper-store", default=DEFAULT_PAPER_STORE)
    parser.add_argument(
        "--research-risk-bundle",
        default=DEFAULT_RESEARCH_RISK_BUNDLE,
        help="immutable same-PIT nine-engine risk bundle; missing means pending/block",
    )
    args = parser.parse_args(argv)
    alpha_ids = ALPHA_IDS if args.alpha == "all" else (args.alpha,)
    report = run_preview(
        alpha_ids=alpha_ids,
        forward_db=args.forward_db,
        mark_cache=args.mark_cache,
        status_path=args.status,
        output_path=args.output,
        paper_store_path=args.paper_store,
        research_risk_bundle_path=args.research_risk_bundle,
        aum=args.aum,
        cost_bps=args.cost_bps,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"-> {args.output}")
    return 0 if report["verdict"]["paper_preview_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ALPHA_IDS",
    "DEFAULT_PAPER_STORE",
    "DEFAULT_OUTPUT",
    "FULL_RESEARCH_RISK_ENGINES",
    "DEFAULT_RESEARCH_RISK_BUNDLE",
    "build_alpha_preview",
    "_full_research_risk_approved",
    "run_preview",
]
