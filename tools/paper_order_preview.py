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
from collections.abc import Callable, Mapping, Sequence
from typing import Any

# Keep the documented direct-script entrypoint import-safe from the repository
# root.  This does not alter package imports when the module is run with ``-m``.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from quantos.contracts.hashing import stable_hash
from quantos.contracts.portfolio import Position
from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_SAFE_BLOCKED,
    SCHEDULE_EXIT_SUCCESS,
)
from services.execution.dry_run_pipeline import run_dry_run_pipeline
from services.execution.paper_execution_store import PaperExecutionStore
from services.research.paper_trading.attested_store import AttestedForwardLedger
from services.risk.research_risk.bundle import load_research_risk_bundle
from services.risk.research_risk.dry_run_guard import (
    DryRunResearchRiskInputs,
    FULL_RESEARCH_RISK_ENGINES,
    full_research_risk_approved,
)
from services.risk.research_risk.domain.models import RiskConfig
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
from tools.paper_candidate_universe import paper_candidate_ids


DEFAULT_FORWARD_DB = os.path.join("data", "cache", "paper_track.db")
DEFAULT_MARK_CACHE = os.path.join("data", "cache", "crypto_perp_mark_prices.json")
DEFAULT_STATUS = os.path.join("data", "cache", "forward_track_status.json")
DEFAULT_OUTPUT = os.path.join("data", "cache", "paper_order_preview.json")
DEFAULT_PAPER_STORE = os.path.join("data", "cache", "paper_order_ledger.db")
DEFAULT_RESEARCH_RISK_BUNDLE = os.path.join(
    "data", "cache", "paper_research_risk_inputs.json"
)
DEFAULT_FORWARD_EVALUATIONS = os.path.join(
    "data", "cache", "forward_alpha_evaluations.json"
)
DEFAULT_REALTIME_MARKET_STATUS = os.path.join(
    "data", "cache", "binance_realtime_market_status.json"
)
DEFAULT_ALPHA_PRODUCTION_WORKFLOW = os.path.join(
    "data", "cache", "alpha_production_workflow.json"
)
ALPHA_IDS = ("carry_oi_majors", "crowd_contra", "top_contra")
DEFAULT_AUM = 1_000_000.0
DEFAULT_COST_BPS = 10.0
OFFICIAL_PAPER_PERIODS_PER_YEAR = 52.0
MAX_DECISION_AGE_DAYS = 8
MAX_EXCHANGE_FILTER_AGE_MS = 15 * 60_000


def _load_json(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _workflow_admission(path: str) -> dict[str, Any]:
    """Load the sole authority for creating a new formal paper fill."""
    document = _load_json(path)
    if document.get("authority") != "ALPHA_PRODUCTION_WORKFLOW":
        raise RuntimeError("alpha production workflow authority is invalid")
    if document.get("status") != "VERIFIED":
        raise RuntimeError("alpha production workflow is not VERIFIED")
    active = document.get("active_candidate_ids")
    eligible = document.get("execution_paper_eligible_ids")
    executing = document.get("execution_paper_ids")
    if not all(isinstance(value, list) for value in (active, eligible, executing)):
        raise RuntimeError("alpha production workflow admission lists are invalid")
    if any(not isinstance(item, str) or not item for item in active + eligible + executing):
        raise RuntimeError("alpha production workflow candidate identity is invalid")
    active_ids = frozenset(active)
    eligible_ids = frozenset(eligible)
    execution_ids = frozenset(executing)
    if not eligible_ids.issubset(active_ids) or not execution_ids.issubset(active_ids):
        raise RuntimeError("alpha production workflow admission exceeds active population")
    return {
        "authority": "ALPHA_PRODUCTION_WORKFLOW",
        "status": "VERIFIED",
        "source_path": os.path.normpath(str(path)),
        "source_hash": stable_hash(document),
        "eligible_ids": eligible_ids,
        "execution_ids": execution_ids,
        "authorized_ids": eligible_ids | execution_ids,
    }


def _revalidate_workflow_admission(
    path: str,
    *,
    alpha_id: str,
    expected_hash: str,
) -> None:
    current = _workflow_admission(path)
    if current["source_hash"] != expected_hash:
        raise RuntimeError("alpha production workflow changed during paper admission")
    if alpha_id not in current["authorized_ids"]:
        raise RuntimeError("candidate lost Execution Paper admission before ledger append")


def _forward_pending_preview(
    alpha_id: str,
    *,
    status_path: str,
    research_risk_bundle_path: str,
    generated_at_ms: int | None,
    error: str,
) -> dict[str, Any]:
    """Materialize a visible, non-executable row for an unready challenger.

    A challenger can be discovered before it has an attested decision.  The
    order preview must still expose that candidate and its evidence clock; an
    exception-only entry made the UI look as if the candidate did not exist.
    This row contains no orders, positions, or execution authority.
    """
    status_document = _load_json(status_path)
    status_row = (
        status_document.get("alphas", {}).get(alpha_id)
        if isinstance(status_document.get("alphas"), Mapping)
        else None
    )
    evaluation_document = _load_json(DEFAULT_FORWARD_EVALUATIONS)
    evaluation_row = (
        evaluation_document.get("alphas", {}).get(alpha_id)
        if isinstance(evaluation_document.get("alphas"), Mapping)
        else None
    )
    risk_document = _load_json(research_risk_bundle_path)
    risk_row = (
        risk_document.get("alphas", {}).get(alpha_id)
        if isinstance(risk_document.get("alphas"), Mapping)
        else None
    )
    evaluation = (
        evaluation_row.get("evaluation")
        if isinstance(evaluation_row, Mapping)
        else None
    )
    observed = None
    for source in (evaluation_row, status_row):
        value = source.get("forward_periods") if isinstance(source, Mapping) else None
        if value is None and isinstance(source, Mapping):
            value = source.get("n_forward_periods")
        if isinstance(value, int) and not isinstance(value, bool):
            observed = value
            break
    blockers = [str(error)]
    if not isinstance(evaluation, Mapping) or evaluation.get("verdict") != "PASS":
        blockers.append("INDEPENDENT_EVALUATION_NOT_PASS")
    if observed is None or observed < 8:
        blockers.append("FORWARD_EVIDENCE_MATURITY_PENDING")
    if not isinstance(risk_row, Mapping) or risk_row.get("status") != "READY":
        blockers.append("FULL_NINE_ENGINE_RISK_PACKET_PENDING")
    return {
        "alpha_id": alpha_id,
        "decision_source": "FORWARD_CANDIDATE_PENDING",
        "forward_candidate": True,
        "forward_observation": {
            "observed_periods": observed,
            "required_periods": 8,
            "evaluation_status": (
                evaluation_row.get("status")
                if isinstance(evaluation_row, Mapping)
                else "PENDING"
            ),
            "evaluation_verdict": (
                evaluation.get("verdict")
                if isinstance(evaluation, Mapping)
                else None
            ),
        },
        "orders": [],
        "n_intents": 0,
        "n_approved": 0,
        "n_rejected": 0,
        "rejected_reasons": list(dict.fromkeys(blockers)),
        "paper_execution": "PAPER_BLOCKED",
        "pnl": {
            "total_pnl": 0.0,
            "net_pnl": 0.0,
            "gross_pnl": 0.0,
            "costs": 0.0,
            "equity": DEFAULT_AUM,
            "turnover": 0.0,
            "residual": 0.0,
            "positions": {},
        },
        "research_risk": {
            "decision": "RISK_PENDING",
            "status": "PENDING",
            "gates_passed": 0,
            "gates_run": 0,
            "engines_run": {},
            "reasons": list(dict.fromkeys(blockers)),
            "provenance": "FORWARD_CANDIDATE_PENDING",
            "scope": "PRE_FILL_RESEARCH_EVIDENCE",
        },
        "research_risk_bundle": {
            "status": (
                risk_row.get("status")
                if isinstance(risk_row, Mapping)
                else "PENDING"
            ),
            "path": os.path.normpath(str(research_risk_bundle_path)),
            "alpha_id": alpha_id,
            "missing": list(risk_row.get("missing_inputs") or ())
            if isinstance(risk_row, Mapping)
            else ["forward_decision", "alpha_metadata"],
            "reasons": dict(risk_row.get("missing_reasons") or {})
            if isinstance(risk_row, Mapping)
            else {},
        },
        "generated_at": int(
            generated_at_ms if generated_at_ms is not None else time.time() * 1000
        ),
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "broker_connectivity": False,
        "credentials_accessed": False,
    }


def _workflow_blocked_preview(
    alpha_id: str,
    *,
    decision_ts: int,
    status_path: str,
    research_risk_bundle_path: str,
    generated_at_ms: int | None,
    admission: Mapping[str, Any] | None,
    error: str,
) -> dict[str, Any]:
    row = _forward_pending_preview(
        alpha_id,
        status_path=status_path,
        research_risk_bundle_path=research_risk_bundle_path,
        generated_at_ms=generated_at_ms,
        error=error,
    )
    row.update(
        {
            "decision_source": "ALPHA_PRODUCTION_WORKFLOW_BLOCKED",
            "decision_ts": int(decision_ts),
            "forward_candidate": alpha_id not in ALPHA_IDS,
            "workflow_admission": {
                "authority": (
                    admission.get("authority")
                    if isinstance(admission, Mapping)
                    else None
                ),
                "status": (
                    admission.get("status")
                    if isinstance(admission, Mapping)
                    else "INVALID"
                ),
                "source_path": (
                    admission.get("source_path")
                    if isinstance(admission, Mapping)
                    else None
                ),
                "source_hash": (
                    admission.get("source_hash")
                    if isinstance(admission, Mapping)
                    else None
                ),
                "candidate_authorized": False,
                "reason": error,
            },
        }
    )
    return row


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
    return full_research_risk_approved(risk)


def _load_exchange_filters(
    path: str,
    instruments: set[str],
    *,
    now_ms: int,
) -> dict[str, tuple[float, float, float]]:
    """Load current Binance execution filters from the verified public snapshot."""
    status = _load_json(path)
    if status.get("verdict") != "PASS" or status.get("complete") is not True:
        raise RuntimeError("Binance realtime exchange filters are not PASS")
    generated_at = status.get("generated_at_ms")
    if (
        isinstance(generated_at, bool)
        or not isinstance(generated_at, int)
        or generated_at < 0
        or now_ms - generated_at < 0
        or now_ms - generated_at > MAX_EXCHANGE_FILTER_AGE_MS
    ):
        raise RuntimeError("Binance realtime exchange filters are stale")
    latest = status.get("latest")
    if not isinstance(latest, Mapping):
        raise RuntimeError("Binance realtime exchange filters are missing")
    result: dict[str, tuple[float, float, float]] = {}
    for instrument in sorted(instruments):
        snapshot = latest.get(instrument)
        contract = snapshot.get("contract") if isinstance(snapshot, Mapping) else None
        if not isinstance(contract, Mapping):
            raise RuntimeError(f"Binance exchange filters are missing for {instrument}")
        try:
            step_size = float(contract["step_size"])
            min_qty = float(contract["min_qty"])
            min_notional = float(contract["min_notional"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Binance exchange filters are malformed for {instrument}"
            ) from exc
        if (
            not all(math.isfinite(value) and value > 0 for value in (
                step_size, min_qty, min_notional
            ))
            or contract.get("status") != "TRADING"
            or contract.get("contract_type") != "PERPETUAL"
        ):
            raise RuntimeError(f"Binance exchange filters are invalid for {instrument}")
        result[instrument] = (step_size, min_qty, min_notional)
    return result


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


def _aligned_full_risk_series(
    *,
    alpha_id: str,
    inputs: DryRunResearchRiskInputs,
    initial_capital: float,
) -> tuple[list[float], list[float], list[int] | None]:
    """Build the portfolio curve aligned with the bundle's PIT risk evidence."""
    raw = inputs.alpha_returns.get(alpha_id)
    if not isinstance(raw, (list, tuple)) or not raw:
        raise RuntimeError(f"research-risk bundle has no return history for {alpha_id}")
    returns = [float(value) for value in raw]
    if any(not math.isfinite(value) or value <= -1.0 for value in returns):
        raise RuntimeError(f"research-risk return history is invalid for {alpha_id}")
    if len(returns) != len(inputs.regime_labels):
        raise RuntimeError(
            f"research-risk return/regime history is unaligned for {alpha_id}"
        )
    equity: list[float] = []
    level = float(initial_capital)
    for value in returns:
        level *= 1.0 + value
        if not math.isfinite(level) or level <= 0.0:
            raise RuntimeError(f"research-risk equity history is invalid for {alpha_id}")
        equity.append(level)
    timestamps = (
        [int(value) for value in inputs.timestamps]
        if inputs.timestamps is not None
        else None
    )
    if timestamps is not None and len(timestamps) != len(returns):
        raise RuntimeError(
            f"research-risk return/timestamp history is unaligned for {alpha_id}"
        )
    return returns, equity, timestamps


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
    exchange_filters: dict[str, tuple[float, float, float]] | None = None,
    execution_prices: Mapping[str, float] | None = None,
    execution_at_ms: int | None = None,
    route_planner: Callable | None = None,
    routing_snapshot_id: str = "",
    supported_alpha_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run one attested decision through the real paper execution chain.

    Paper order admission is fail-closed on the complete nine-engine research
    risk result.  ``research_risk_inputs`` must therefore come from the same
    PIT snapshot as the decision when the caller wants a fill.  Omitting it is
    intentional and observable: the pipeline returns a pending full-risk
    result and no paper fills are admitted.
    """
    if supported_alpha_ids is None:
        supported = paper_candidate_ids(
            forward_status=DEFAULT_STATUS,
            evaluations=DEFAULT_FORWARD_EVALUATIONS,
        )
    else:
        supported = tuple(str(item) for item in supported_alpha_ids)
        if (
            not supported
            or len(supported) != len(set(supported))
            or any(not item for item in supported)
        ):
            raise ValueError("supported alpha authority is invalid")
    if alpha_id not in supported:
        raise ValueError(f"unsupported alpha: {alpha_id}")
    if not math.isfinite(float(aum)) or float(aum) <= 0:
        raise ValueError("aum must be a positive finite number")
    if not math.isfinite(float(cost_bps)) or float(cost_bps) < 0:
        raise ValueError("cost_bps must be a non-negative finite number")
    generated_at = int(
        generated_at_ms if generated_at_ms is not None else time.time() * 1000
    )
    decision_ts, weights, prices = _decision_inputs(pending, mark_by_ticker)
    execution_ts = int(execution_at_ms if execution_at_ms is not None else decision_ts)
    if execution_ts < 0:
        raise ValueError("execution_at_ms must be a non-negative integer")
    _status_guard(status, recorded_at=int(pending.get("recorded_at", 0) or 0))
    held = {str(name): int(quantity) for name, quantity in (current_positions or {}).items()}
    outside_held = sorted(set(held) - set(FORWARD_MAJOR_TICKERS))
    if outside_held:
        raise RuntimeError(f"paper positions exceed the major-universe scope: {outside_held}")
    instruments = set(weights) | set(held)
    if execution_prices is not None:
        missing_execution_prices = sorted(instruments - set(execution_prices))
        if missing_execution_prices:
            raise RuntimeError(
                "execution snapshot has no price for "
                + ",".join(missing_execution_prices)
            )
        normalized_prices: dict[str, float] = {}
        for instrument in instruments:
            value = execution_prices[instrument]
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise RuntimeError(
                    f"execution snapshot price is invalid for {instrument}"
                )
            normalized_prices[instrument] = float(value)
        prices = normalized_prices
    else:
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
    gate = _paper_gate(alpha_id=alpha_id, instruments=instruments, aum=float(aum))
    risk_returns: list[float] | None = None
    risk_equity_curve: list[float] | None = None
    risk_timestamps: list[int] | None = None
    if research_risk_inputs is not None:
        risk_returns, risk_equity_curve, risk_timestamps = _aligned_full_risk_series(
            alpha_id=alpha_id,
            inputs=research_risk_inputs,
            initial_capital=float(aum),
        )
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
                price_as_of=execution_ts,
                est_daily_volume=1e18,
                instrument_vol=0.20,
            ),
            strategy=StrategyApproval(
                strategy_id=alpha_id,
                approved=True,
                approval_id=f"paper-preview:{alpha_id}:{decision_ts}",
            ),
            now=execution_ts,
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
        timestamp=execution_ts,
        marks=prices,
        initial_capital=float(aum),
        cost_bps=float(cost_bps),
        filters=exchange_filters,
        route_planner=route_planner,
        routing_snapshot_id=routing_snapshot_id,
        event_bus=None,
        publish_completion_event=False,
        research_risk_inputs=research_risk_inputs,
        research_risk_config=RiskConfig(
            periods_per_year=OFFICIAL_PAPER_PERIODS_PER_YEAR
        ),
        require_full_research_risk=bool(require_full_research_risk),
        research_risk_returns=risk_returns,
        research_risk_equity_curve=risk_equity_curve,
        research_risk_timestamps=risk_timestamps,
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
                "execution_reference_price": price,
                "notional": round(abs(quantity) * price, 6),
                "paper_position_before": int(held.get(instrument, 0)),
                "paper_position_after_fill": positions.get(instrument, 0),
            }
        )
    fill_slices = int(run.detail.get("n_fills", 0))
    route_tca = list(run.detail.get("route_tca") or [])
    filled_orders = len(route_tca) if route_planner is not None else fill_slices
    pretrade_filled = (
        run.n_intents == run.n_approved
        and run.n_rejected == 0
        and filled_orders == run.n_approved
        and not list(run.detail.get("route_rejections") or [])
        and int(run.detail.get("n_adapter_rejected", 0)) == 0
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
    turnover = float(run.pnl.get("turnover", 0.0) or 0.0)
    execution_cost = float(run.pnl.get("costs", 0.0) or 0.0)
    if route_tca:
        route_reference_notional = sum(
            float(row["reference_price"]) * int(row["filled_quantity"])
            for row in route_tca
        )
        aggregate_shortfall_bps = (
            sum(
                float(row["implementation_shortfall_bps"])
                * float(row["reference_price"])
                * int(row["filled_quantity"])
                for row in route_tca
            )
            / route_reference_notional
            if route_reference_notional > 0.0
            else 0.0
        )
    else:
        aggregate_shortfall_bps = (
            execution_cost / turnover * 1e4 if turnover > 0.0 else 0.0
        )
    return {
        "alpha_id": alpha_id,
        "decision_ts": decision_ts,
        "execution_ts": execution_ts,
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
        "n_paper_fills": filled_orders,
        "n_paper_fill_slices": fill_slices,
        "paper_execution": "PAPER_FILLED" if fully_filled else "PAPER_BLOCKED",
        "pnl": dict(run.pnl),
        "reconciliation_residual": run.pnl.get("residual"),
        "execution_tca": {
            "status": "COMPLETE" if pretrade_filled else "INCOMPLETE",
            "simulation_only": True,
            "reference_price": (
                "CONSOLIDATED_CROSS_VENUE_MID"
                if route_tca
                else "DECISION_MARK"
            ),
            "fill_model": (
                "REAL_PUBLIC_BOOK_SIMULATED_SOR"
                if route_tca
                else "DRY_RUN_REFERENCE_PRICE"
            ),
            "routing_snapshot_id": routing_snapshot_id or None,
            "turnover_notional": turnover,
            "execution_cost": execution_cost,
            "implementation_shortfall_bps_including_cost": aggregate_shortfall_bps,
            "route_tca": route_tca,
            "route_tca_available": bool(route_tca),
            "limitations": (
                [
                    "SIMULATION_ONLY_NO_QUEUE_POSITION",
                    "LATENCY_PENALTY_UNCALIBRATED",
                    "PUBLIC_TAKER_FEES_UNCALIBRATED",
                ]
                if route_tca
                else [
                    "NO_ORDER_BOOK_ROUTE_MODEL",
                    "NO_LATENCY_OR_MARKET_IMPACT_SLIPPAGE",
                ]
            ),
        },
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
        "generated_at": generated_at,
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
    alpha_ids: tuple[str, ...] | None = None,
    forward_db: str = DEFAULT_FORWARD_DB,
    mark_cache: str = DEFAULT_MARK_CACHE,
    status_path: str = DEFAULT_STATUS,
    output_path: str = DEFAULT_OUTPUT,
    paper_store_path: str = DEFAULT_PAPER_STORE,
    research_risk_bundle_path: str = DEFAULT_RESEARCH_RISK_BUNDLE,
    realtime_market_status_path: str = DEFAULT_REALTIME_MARKET_STATUS,
    workflow_path: str = DEFAULT_ALPHA_PRODUCTION_WORKFLOW,
    aum: float = DEFAULT_AUM,
    cost_bps: float = DEFAULT_COST_BPS,
    generated_at_ms: int | None = None,
) -> dict[str, Any]:
    """Build previews for the complete current forward-paper universe."""
    selected = (
        paper_candidate_ids(
            forward_status=status_path,
            evaluations=DEFAULT_FORWARD_EVALUATIONS,
        )
        if alpha_ids is None
        else tuple(alpha_ids)
    )
    if not selected or any(
        alpha not in paper_candidate_ids(
            forward_status=status_path,
            evaluations=DEFAULT_FORWARD_EVALUATIONS,
        )
        for alpha in selected
    ):
        raise ValueError("alpha_ids must be a non-empty subset of the attested paper candidates")
    status = _load_json(status_path)
    mark_by_ticker = load_mark_prices(mark_cache)
    workflow_error: str | None = None
    try:
        workflow_admission = _workflow_admission(workflow_path)
    except RuntimeError as exc:
        workflow_admission = None
        workflow_error = str(exc)
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
                existing = store.get_by_decision(alpha_id, decision_ts)
                if existing is None and (
                    workflow_admission is None
                    or alpha_id not in workflow_admission["authorized_ids"]
                ):
                    reason = (
                        "ALPHA_PRODUCTION_WORKFLOW_UNAVAILABLE"
                        if workflow_admission is None
                        else "ALPHA_PRODUCTION_WORKFLOW_NOT_EXECUTION_PAPER_ELIGIBLE"
                    )
                    previews[alpha_id] = _workflow_blocked_preview(
                        alpha_id,
                        decision_ts=decision_ts,
                        status_path=status_path,
                        research_risk_bundle_path=research_risk_bundle_path,
                        generated_at_ms=generated_at_ms,
                        admission=workflow_admission,
                        error=reason,
                    )
                    errors[alpha_id] = {
                        "error_type": "PaperWorkflowAdmissionBlocked",
                        "error": workflow_error
                        or (
                            "candidate is not authorized for Execution Paper by the "
                            "alpha production workflow"
                        ),
                        "reason": reason,
                        "paper_execution": "PAPER_BLOCKED",
                        "live_allowed": False,
                        "order_submission_allowed": False,
                    }
                    continue
                bundle_result = load_research_risk_bundle(
                    research_risk_bundle_path,
                    alpha_id=alpha_id,
                    pending=pending,
                )
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
                    existing["workflow_admission"] = {
                        "authority": "ALPHA_PRODUCTION_WORKFLOW",
                        "status": "HISTORICAL_ROW_REPLAY",
                        "candidate_authorized": bool(
                            workflow_admission is not None
                            and alpha_id in workflow_admission["authorized_ids"]
                        ),
                        "source_hash": (
                            workflow_admission.get("source_hash")
                            if workflow_admission is not None
                            else None
                        ),
                    }
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
                    exchange_filters=(
                        _load_exchange_filters(
                            realtime_market_status_path,
                            set(json.loads(str(pending["weights_json"]))),
                            now_ms=int(
                                generated_at_ms
                                if generated_at_ms is not None
                                else time.time() * 1000
                            ),
                        )
                        if bundle_result.inputs is not None
                        else None
                    ),
                )
                preview["workflow_admission"] = {
                    "authority": workflow_admission["authority"],
                    "status": "AUTHORIZED",
                    "candidate_authorized": True,
                    "authorization_stage": (
                        "EXECUTION_PAPER"
                        if alpha_id in workflow_admission["execution_ids"]
                        else "EXECUTION_PAPER_ELIGIBLE"
                    ),
                    "source_path": workflow_admission["source_path"],
                    "source_hash": workflow_admission["source_hash"],
                }
                if preview.get("paper_execution") == "PAPER_FILLED":
                    _revalidate_workflow_admission(
                        workflow_path,
                        alpha_id=alpha_id,
                        expected_hash=workflow_admission["source_hash"],
                    )
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
                message = str(exc)[:240]
                if (
                    alpha_id not in ALPHA_IDS
                    and alpha_id not in previews
                    and "attested" in message
                ):
                    previews[alpha_id] = _forward_pending_preview(
                        alpha_id,
                        status_path=status_path,
                        research_risk_bundle_path=research_risk_bundle_path,
                        generated_at_ms=generated_at_ms,
                        error=message,
                    )
                    errors[alpha_id] = {
                        "error_type": "ForwardCandidatePending",
                        "error": message,
                        "reason": "FORWARD_CANDIDATE_PENDING",
                        "paper_execution": "PAPER_BLOCKED",
                        "live_allowed": False,
                        "order_submission_allowed": False,
                    }
                else:
                    errors[alpha_id] = {
                        "error_type": type(exc).__name__,
                        "error": message,
                        "paper_execution": "PAPER_BLOCKED",
                        "live_allowed": False,
                        "order_submission_allowed": False,
                    }
    finally:
        store.close()
    # The three established books are the current execution-paper account.
    # Forward challengers are evaluated in the same run, but their immaturity
    # must not make the already-approved paper account unmarkable.  A selected
    # challenger-only invocation remains strict, which keeps focused operator
    # calls fail-closed.
    static_selected = tuple(alpha for alpha in selected if alpha in ALPHA_IDS)
    execution_required = static_selected or selected
    ready = bool(previews) and all(
        alpha_id in previews
        and alpha_id not in errors
        and previews[alpha_id].get("paper_execution") == "PAPER_FILLED"
        and _full_research_risk_approved(previews[alpha_id].get("research_risk"))
        for alpha_id in execution_required
    )
    forward_pending = tuple(
        alpha_id
        for alpha_id in selected
        if alpha_id not in ALPHA_IDS
        and (
            alpha_id in errors
            or previews.get(alpha_id, {}).get("paper_execution") != "PAPER_FILLED"
        )
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
            "paper_execution_universe": list(execution_required),
            "forward_candidates_pending": list(forward_pending),
            "forward_candidate_count": len(selected) - len(static_selected),
            "workflow_admission_valid": workflow_admission is not None,
            "workflow_authorized_ids": sorted(
                workflow_admission["authorized_ids"]
                if workflow_admission is not None
                else ()
            ),
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
    parser.add_argument(
        "--alpha",
        default="all",
        help="registered paper candidate id, or all current forward candidates",
    )
    parser.add_argument("--aum", type=float, default=DEFAULT_AUM, help="paper notional only")
    parser.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    parser.add_argument("--forward-db", default=DEFAULT_FORWARD_DB)
    parser.add_argument("--mark-cache", default=DEFAULT_MARK_CACHE)
    parser.add_argument("--status", default=DEFAULT_STATUS)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--paper-store", default=DEFAULT_PAPER_STORE)
    parser.add_argument(
        "--alpha-production-workflow",
        default=DEFAULT_ALPHA_PRODUCTION_WORKFLOW,
        help="sole workflow authority for admitting a new formal paper fill",
    )
    parser.add_argument(
        "--research-risk-bundle",
        default=DEFAULT_RESEARCH_RISK_BUNDLE,
        help="immutable same-PIT nine-engine risk bundle; missing means pending/block",
    )
    args = parser.parse_args(argv)
    alpha_ids = (
        paper_candidate_ids(
            forward_status=args.status,
            evaluations=DEFAULT_FORWARD_EVALUATIONS,
        )
        if args.alpha == "all"
        else (args.alpha,)
    )
    report = run_preview(
        alpha_ids=alpha_ids,
        forward_db=args.forward_db,
        mark_cache=args.mark_cache,
        status_path=args.status,
        output_path=args.output,
        paper_store_path=args.paper_store,
        research_risk_bundle_path=args.research_risk_bundle,
        workflow_path=args.alpha_production_workflow,
        aum=args.aum,
        cost_bps=args.cost_bps,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"-> {args.output}")
    return (
        SCHEDULE_EXIT_SUCCESS
        if report["verdict"]["paper_preview_ready"]
        else SCHEDULE_EXIT_SAFE_BLOCKED
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ALPHA_IDS",
    "DEFAULT_ALPHA_PRODUCTION_WORKFLOW",
    "DEFAULT_PAPER_STORE",
    "DEFAULT_OUTPUT",
    "FULL_RESEARCH_RISK_ENGINES",
    "DEFAULT_RESEARCH_RISK_BUNDLE",
    "build_alpha_preview",
    "_full_research_risk_approved",
    "run_preview",
]
