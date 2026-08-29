"""Quarantined legacy execution diagnostic for ``top_contra``.

``top_contra`` is the third research candidate, but it is not yet a production
alpha.  This driver deliberately exercises the same intent -> pre-trade gate ->
paper-fill -> PnL machinery as the established OI book while omitting the
canonical event bus and ``DryRunStore`` write.  That distinction is important:
the current canonical projection needs human review, so a local diagnostic must
never masquerade as official dry-run evidence or a paper-track period.

The historical implementation remains below so its pure helpers can be tested
while the V2 migration is audited.  The CLI entrypoint is fail-closed before it
reads cached history or writes an artifact: even a labelled diagnostic would
regenerate invalidated V1 profitability claims.
"""
from __future__ import annotations

import os

from services.execution.backtest_authority import quarantine_legacy_backtest
from services.execution.dry_run_pipeline import run_multi_period_dry_run
from services.portfolio.crowding_gate import evaluate_crowding_gate, summarize_gate_decisions
from services.research import alpha_expression as ax
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
from tools.crypto_oi_research import QUANTILE, _legs, build_panel
from tools.crypto_oi_verify import TOP_CONTRA
from live.execution.crowding_score import crowding_zscore as _crowding_zscore


OUTPUT = os.path.join("data", "cache", "top_contra_dry_run_diagnostic.json")
_AUM = 1_000_000.0
_COST_BPS = 10.0
_WEEK_MS = 7 * 24 * 3600 * 1000
_MAX_CROWDING_Z = 3.0


def _weekly_rows(event_times: list[int]) -> list[int]:
    rows: list[int] = []
    last: int | None = None
    for row, event_time in enumerate(event_times):
        if last is None or event_time - last >= _WEEK_MS:
            rows.append(row)
            last = event_time
    return rows


def _crowding_rank(global_ls, row: int, indices: list[int], lookback: int = 4) -> dict[int, float]:
    """Build the scale-free crowding evidence used by the pre-trade diagnostic."""
    means: dict[int, float] = {}
    for index in indices:
        window = [
            global_ls[rr][index]
            for rr in range(max(0, row - lookback + 1), row + 1)
            if global_ls[rr][index] is not None
        ]
        if window:
            means[index] = sum(window) / len(window)
    if len(means) < 2:
        return {}
    ordered = sorted(means, key=means.get)
    denominator = len(ordered) - 1
    return {index: position / denominator for position, index in enumerate(ordered)}


def _prepare(panel):
    instruments = panel.instruments
    close = panel.matrices["close"]
    funding = panel.matrices["funding"]
    global_ls = panel.matrices["global_ls"]
    event_times = panel.event_times
    signal = ax.evaluate(TOP_CONTRA, panel)

    periods: list[int] = []
    weights_by_period: dict[int, dict[str, float]] = {}
    prices_by_period: dict[int, dict[str, float]] = {}
    rate_by_period: dict[int, dict[str, float]] = {}
    gate_decisions = []
    previous_weights: dict[str, float] | None = None

    for row in _weekly_rows(event_times):
        legs = _legs(signal[row], QUANTILE)
        if not legs:
            continue
        timestamp = int(event_times[row])
        target = {instruments[index]: weight for index, weight in legs.items()}
        crowd = {
            instruments[index]: rank
            for index, rank in _crowding_rank(global_ls, row, list(legs)).items()
        }
        z_by_symbol = {
            instruments[index]: z
            for index in legs
            if (z := _crowding_zscore(global_ls, row, index)) is not None
        }
        decision = evaluate_crowding_gate(
            target,
            crowd,
            average_warn_only=True,
            crowding_z_by_symbol=z_by_symbol,
            max_crowding_z=_MAX_CROWDING_Z,
        )
        gate_decisions.append(decision)
        blocked = decision.status == "BLOCK"
        if blocked and previous_weights is not None:
            weights = dict(previous_weights)
        else:
            weights = target
            previous_weights = target
        held_names = set(weights)
        prices_by_period[timestamp] = {
            instrument: close[row][index]
            for index, instrument in enumerate(instruments)
            if instrument in held_names and close[row][index] and close[row][index] > 0
        }
        rate_by_period[timestamp] = {
            instrument: funding[row][index] or 0.0
            for index, instrument in enumerate(instruments)
            if instrument in held_names
        }
        periods.append(timestamp)
        weights_by_period[timestamp] = weights

    return periods, weights_by_period, prices_by_period, rate_by_period, gate_decisions


def main() -> None:
    quarantine_legacy_backtest("tools.top_contra_dry_run_diagnostic")

    # Unreachable while V1 timing/perpetual-ledger semantics are invalidated.
    # Keep the implementation as an explicit migration target and retain its
    # pure helpers for synthetic unit tests.
    panel, instruments = build_panel()
    periods, weights_by_period, prices_by_period, rate_by_period, gate_decisions = _prepare(panel)
    if not periods:
        raise RuntimeError("top_contra diagnostic has no usable weekly periods")
    held_names = sorted({name for weights in weights_by_period.values() for name in weights})

    def cash_flow_fn(held, prices, period):
        rates = rate_by_period.get(period, {})
        return sum(-rates.get(name, 0.0) * quantity * prices.get(name, 0.0)
                   for name, quantity in held.items())

    kill_switch = KillSwitch()
    limits = RiskLimits(
        allowed_instruments=frozenset(held_names),
        allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=10**13,
        max_order_notional=1e14,
        fat_finger_qty=10**13,
        max_gross_exposure=1e14,
        max_net_exposure=1e14,
        max_leverage=1e7,
        max_single_name_pct=1e7,
        max_turnover=1e7,
        max_participation_rate=1e7,
        max_instrument_vol=5.0,
        daily_loss_limit=1e14,
        max_drawdown=1.0,
    )
    gate = PreTradeRiskGate(limits, kill_switch=kill_switch)

    def ctx_factory(intent, prices, period):
        return RiskContext(
            account=AccountRiskState(
                account_id="paper",
                cash=_AUM * 10,
                equity=_AUM * 10,
                day_pnl=0.0,
                peak_equity=_AUM * 10,
                positions=(),
            ),
            market=MarketContext(
                instrument_id=intent.instrument_id,
                known=True,
                reference_price=prices.get(intent.instrument_id, 1.0),
                market_open=True,
                price_as_of=period,
                est_daily_volume=1e12,
                instrument_vol=0.8,
            ),
            strategy=StrategyApproval(
                strategy_id="top_contra",
                approved=True,
                approval_id="research-diagnostic-only",
            ),
            now=period,
            max_staleness=10**15,
        )

    # No DurableEventBus is supplied on purpose.  The core still runs the real
    # pre-trade gate and paper adapter, but no canonical event/projection/store
    # mutation can occur from this diagnostic.
    run = run_multi_period_dry_run(
        periods=periods,
        weights_by_period=weights_by_period,
        prices_by_period=prices_by_period,
        gate=gate,
        ctx_factory=ctx_factory,
        adapter=DryRunAdapterEngine(),
        strategy_id="top_contra",
        aum=_AUM,
        cost_bps=_COST_BPS,
        cash_flow_fn=cash_flow_fn,
        vol_target={"target_ann": 0.10, "lookback": 8, "lev_cap": 3.0, "ppy": 52},
        event_bus=None,
        publish_completion_event=False,
    )
    crowding = summarize_gate_decisions(gate_decisions)
    detail = run.detail
    report = {
        "schema_version": 1,
        "authoritative": False,
        "evidence_class": "EXECUTION_DIAGNOSTIC_ONLY",
        "persistence": "NO_CANONICAL_EVENT_OR_DRY_RUN_STORE_WRITE",
        "readiness": "L2_DRY_RUN_ONLY",
        "strategy_id": "top_contra",
        "expression": "cs_zscore(neg(top_ls))",
        "source": "local:data/cache/crypto_funding.db+crypto_oi_metrics.json",
        "n_instruments": len(instruments),
        "n_periods": len(periods),
        "first_period": periods[0],
        "last_period": periods[-1],
        "gate": crowding,
        "pnl": dict(run.pnl),
        "run_reproducibility_hash": run.reproducibility_hash,
        "research_risk": detail.get("research_risk"),
        "cost": {key: value for key, value in detail.items()
                 if key in {"cost_model", "cost_provenance", "cost_fills", "cost_bps"}},
        "equity_curve": detail.get("equity_curve", []),
        "equity_curve_vt": detail.get("equity_curve_vt", []),
        "blockers": [
            "TOP_CONTRA_UNCONFIRMED",
            "NOT_CANONICAL_DRY_RUN_EVIDENCE",
            "EVENT_AUTHORITY_PROJECTION_REQUIRES_HUMAN_REVIEW",
            "NOT_FORWARD_PAPER_EVIDENCE",
        ],
    }
    atomic_write_json(OUTPUT, report, indent=2)
    print(
        f"top_contra diagnostic: periods={len(periods)} approved={run.n_approved} "
        f"rejected={run.n_rejected} raw_sharpe={run.pnl['sharpe']} "
        f"vt_sharpe={run.pnl.get('sharpe_vt')} residual={run.pnl['residual']}",
        flush=True,
    )
    print(f"-> {OUTPUT}", flush=True)


if __name__ == "__main__":
    main()
