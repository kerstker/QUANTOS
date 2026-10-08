"""Actual shared-account attribution; no independently capitalized alpha books."""

import copy
from dataclasses import replace
from decimal import Decimal as D, localcontext

import pytest

from services.execution.perpetual_ledger_v2 import FundingEvent
from services.execution.perpetual_simulation import run_linear_perpetual_simulation, SimulationCostModel
from services.research.gold_loss_control import GoldResearchLossControl
from services.research.gold_net_portfolio import GoldNetTargetPortfolio
from tests.execution.test_perpetual_simulation import F
from tests.research.test_gold_v2_strategy import T, frame

NAMES = ("channel", "reversion", "trend")


def portfolio(monkeypatch, *, frames=None, sides=None, fee=".001", cash="1000", quantities=None):
    sides = sides or {"channel": "BUY", "trend": "BUY"}

    def signal(bars, *, candidate, as_of_ms):
        side = sides(as_of_ms, candidate) if callable(sides) else sides.get(candidate)
        return dict(
            status="READY",
            entry_side=side,
            exit_long=side == "SELL",
            exit_short=side == "BUY",
            stop_distance="50",
            max_holding_bars=16,
            decision_id=str(as_of_ms // 20) + candidate,
        )

    monkeypatch.setattr("services.research.gold_net_portfolio.evaluate", signal)
    strategy = GoldNetTargetPortfolio((), quantities=quantities or {n: D(".1") for n in NAMES}, max_notional=D("100"))
    control = GoldResearchLossControl(strategy, initial_equity=D(cash))
    rule = replace(F["_rule"](), rule_id="synthetic-gold-rules", spec=replace(F["_spec"](), instrument_id="XAUUSDT"))
    policy = replace(F["_funding_policy"](), instrument_id="XAUUSDT", settlement_times=(T + 1000,))

    def risk(ctx, intent):
        verdict = control.risk(ctx, intent)
        return strategy.risk(ctx, intent) if verdict.approved else verdict

    simulation = run_linear_perpetual_simulation(
        release_identity={"fixture": True},
        dataset_identity={"synthetic": True},
        strategy_identity=strategy.identity,
        strategy=control,
        contract_rules=(rule,),
        funding_policies=(policy,),
        frames=frames or tuple(frame(i) for i in (0, 10, 20, 30, 40)),
        initial_collateral=D(cash),
        costs=SimulationCostModel(fee_rate=D(fee)),
        risk_policy=risk,
        risk_policy_identity=dict(loss=control.identity, exposure=strategy.identity),
    )
    return dict(
        construction=strategy.identity,
        simulation=simulation,
        target_trace=strategy.trace,
        allocation_intents=strategy.allocation_intents,
        loss_control=control.report(simulation),
    )


def inputs(value, *, end=40, frequency=10, settlements=None):
    from services.validation.gold_account_risk_inputs import build_account_risk_inputs

    return build_account_risk_inputs(
        value["simulation"],
        portfolio=value,
        sample=dict(start_ms=T, end_ms=T + end, frequency_ms=frequency),
        settlements=settlements or [],
        account_currency="USDT",
    )


def test_net_actual_inventory_and_original_fees_are_apportioned_once(monkeypatch):
    p = portfolio(monkeypatch)
    before = copy.deepcopy(p)
    value = inputs(p)
    alpha = value.net_attribution
    projected = value.projection["net_attribution"]
    assert projected["status"] == "AVAILABLE"
    assert projected["independent_strategy_pnl"] is False
    assert projected["return_kind"] == "NET_CONTRIBUTION_OVER_PREVIOUS_ACCOUNT_EQUITY"
    assert projected["cash_conservation_residual"] == "0"
    assert projected["totals"]["channel"]["fee"] == projected["totals"]["trend"]["fee"] == "0.01"
    assert projected["totals"]["reversion"]["fee"] == "0"
    from quantos.contracts.decimal_arithmetic import ledger_decimal_context
    from services.validation.gold_net_attribution import exact_sum

    with ledger_decimal_context():
        total_weight = D("20") / D("999.98")
        assert alpha.weights["channel"] == total_weight * D(".5")
        assert exact_sum(alpha.weights.values()) == total_weight
    assert all(sum(alpha.contribution_returns[n][i] for n in NAMES) == r for i, r in enumerate(value.returns))
    actual = next(r for r in alpha.history.items() if r["phase"] == "FILL")
    assert actual["quantities"] == dict(channel="0.1", reversion="0", trend="0.1")
    assert p == before
    assert value.projection["source_groups"]["weights"]["status"] == "AVAILABLE"
    assert value.projection["source_groups"]["alpha_returns"]["status"] == "AVAILABLE"
    assert value.projection["full_research_risk_ready"] is False


def test_opposing_targets_create_no_virtual_inventory_or_fees(monkeypatch):
    p = portfolio(monkeypatch, sides={"channel": "BUY", "reversion": "SELL"})
    value = inputs(p)
    assert p["simulation"]["orders"] == []
    assert all(v == 0 for v in value.net_attribution.weights.values())
    assert all(r == 0 for rows in value.net_attribution.contribution_returns.values() for r in rows)
    assert all(all(D(q) == 0 for q in row["quantities"].values()) for row in value.net_attribution.history.items())


def test_partial_fill_uses_actual_quantity_and_retains_frozen_order_recipe(monkeypatch):
    p = portfolio(monkeypatch, frames=(frame(0), frame(10, size=".04"), frame(20, size=".06"), frame(30), frame(40)))
    rows = [r for r in inputs(p).net_attribution.history.items() if r["phase"] == "FILL"]
    assert rows[0]["quantities"] == dict(channel="0.02", reversion="0", trend="0.02")
    assert rows[1]["quantities"] == dict(channel="0.05", reversion="0", trend="0.05")
    assert rows[-1]["quantities"] == dict(channel="0.1", reversion="0", trend="0.1")
    assert D(inputs(p).projection["net_attribution"]["totals"]["channel"]["fee"]) == D(".01")


def test_stop_reduces_allocated_actual_inventory_and_preserves_both_fees(monkeypatch):
    p = portfolio(monkeypatch, fee=".5", cash="200")
    value = inputs(p)
    assert p["loss_control"]["status"] == "STOPPED_FLAT"
    assert all(q == 0 for q in value.net_attribution.weights.values())
    totals = value.projection["net_attribution"]["totals"]
    assert totals["channel"]["fee"] == totals["trend"]["fee"] == "10"
    assert totals["channel"]["net_pnl"] == totals["trend"]["net_pnl"] == "-10"
    assert sum(D(row["net_pnl"]) for row in totals.values()) == D("-20")


def test_future_funding_receipt_never_leaks_into_early_attribution(monkeypatch):
    settlement = replace(
        frame(1000),
        market_events=(FundingEvent(T + 1000, "XAUUSDT", D(".1"), D("100")), *frame(1000).market_events),
        funding_available_at=T + 1010,
    )
    p = portfolio(monkeypatch, frames=(frame(0), frame(10), settlement, frame(1010), frame(1020)))
    bindings = [
        dict(
            settlement_at_ms=T + 1000,
            requested_at_ms=T + 1000,
            available_at_ms=T + 1010,
            instrument_id="XAUUSDT",
            funding_rate="0.1",
            mark_price="100",
        )
    ]
    early = inputs(p, end=1000, frequency=1000, settlements=bindings)
    late = inputs(p, end=1010, frequency=1010, settlements=bindings)
    assert early.projection["net_attribution"]["totals"]["channel"]["funding"] == "0"
    assert late.projection["net_attribution"]["totals"]["channel"]["funding"] == "-1"
    assert all(r["available_at_ms"] <= T + 1000 for r in early.net_attribution.history.items())
    assert (
        late.net_attribution.contribution_returns["channel"][0]
        < early.net_attribution.contribution_returns["channel"][0]
    )


@pytest.mark.parametrize(
    "damage",
    [
        "policy",
        "intent_missing",
        "intent_future",
        "intent_target",
        "fill_id",
        "fill_quantity",
        "extra_intent",
        "point_quantity",
        "fee",
    ],
)
def test_corrupted_allocation_or_original_fill_fails_closed(monkeypatch, damage):
    p = portfolio(monkeypatch)
    if damage == "policy":
        p["construction"]["net_allocation_policy"]["opening"] = "INVENTED"
    elif damage == "intent_missing":
        p["allocation_intents"].clear()
    elif damage == "intent_future":
        p["allocation_intents"][0]["timestamp"] += 10000
    elif damage == "intent_target":
        p["allocation_intents"][0]["desired"]["reversion"] = "0.1"
    elif damage == "extra_intent":
        p["allocation_intents"].append(copy.deepcopy(p["allocation_intents"][0]))
    elif damage == "point_quantity":
        next(r for r in p["simulation"]["equity_history"] if r["phase"] == "FILL")["positions"]["XAUUSDT"][
            "quantity"
        ] = "0.3"
    else:
        trade = next(e for e in p["simulation"]["ledger_events"] if e["event_type"] == "TRADE")
        trade[{"fill_id": "order_id", "fill_quantity": "filled_quantity", "fee": "applied_fee"}[damage]] = (
            "wrong" if damage == "fill_id" else "0.3"
        )
    with pytest.raises(ValueError):
        inputs(p)


def test_legacy_portfolio_stays_unknown_and_cannot_receive_a_later_policy(monkeypatch):
    p = portfolio(monkeypatch)
    p["construction"]["version"] = "GOLD_FIXED_NET_TARGET_PORTFOLIO_V1"
    policy = p["construction"].pop("net_allocation_policy")
    p.pop("allocation_intents")
    assert inputs(p).net_attribution is None
    assert inputs(p).projection["source_groups"]["weights"]["status"] == "MISSING"
    p["construction"]["net_allocation_policy"] = policy
    with pytest.raises(ValueError):
        inputs(p)


@pytest.mark.parametrize("precision", [6, 28, 80])
def test_lazy_allocation_and_three_way_remainders_ignore_caller_precision(monkeypatch, precision):
    p = portfolio(
        monkeypatch,
        sides={n: "BUY" for n in NAMES},
        frames=(frame(0), frame(10, size=".07"), frame(20), frame(30), frame(40)),
    )
    expected = inputs(p)
    with localcontext() as ctx:
        ctx.prec = precision
        observed = inputs(p)
        lazy = list(observed.net_attribution.history.items())
    assert observed.projection == expected.projection
    assert lazy == list(expected.net_attribution.history.items())
    assert observed.net_attribution.contribution_returns == expected.net_attribution.contribution_returns
    assert observed.projection["net_attribution"]["cash_conservation_residual"] == "0"


def test_rejected_orders_never_assign_inventory_or_income(monkeypatch):
    p = portfolio(monkeypatch, cash="15")
    value = inputs(p)
    assert all(o["status"] == "REJECTED" for o in p["simulation"]["orders"])
    assert all(q == 0 for q in value.net_attribution.weights.values())
    assert all(D(t["net_pnl"]) == 0 for t in value.projection["net_attribution"]["totals"].values())


def test_new_preferences_without_actual_trade_do_not_transfer_old_inventory(monkeypatch):
    def sides(timestamp, name):
        if timestamp < T + 20:
            return "BUY" if name in ("channel", "trend") else None
        return "SELL" if name == "channel" else "BUY"

    p = portfolio(monkeypatch, sides=sides, frames=tuple(frame(i) for i in (0, 10, 20, 30)))
    value = inputs(p, end=30)
    assert len(p["simulation"]["orders"]) == 1
    assert p["target_trace"][-1]["desired"] == dict(channel="0", reversion="0.1", trend="0.1")
    assert value.projection["net_attribution"]["latest"]["quantities"] == dict(
        channel="0.1", reversion="0", trend="0.1"
    )
    assert value.net_attribution.weights["reversion"] == 0


def test_short_allocations_and_reversal_follow_actual_reduce_then_open(monkeypatch):
    def sides(timestamp, name):
        if name != "channel":
            return None
        return "BUY" if timestamp < T + 20 else "SELL"

    p = portfolio(monkeypatch, sides=sides, frames=tuple(frame(i) for i in range(0, 80, 10)))
    value = inputs(p, end=70)
    fills = [r for r in value.net_attribution.history.items() if r["phase"] == "FILL"]
    assert [r["quantities"]["channel"] for r in fills] == ["0.1", "0", "-0.1"]
    assert value.net_attribution.weights["channel"] < 0
    assert all(r == 0 for n in ("reversion", "trend") for r in value.net_attribution.contribution_returns[n])


def test_uncovered_sample_does_not_become_available_from_a_bound_recipe(monkeypatch):
    value = inputs(portfolio(monkeypatch), end=100)
    assert value.net_attribution.weights is value.net_attribution.contribution_returns is None
    assert value.projection["net_attribution"]["status"] == "UNAVAILABLE_SAMPLE_COVERAGE"
    assert value.projection["source_groups"]["weights"]["status"] == "UNAVAILABLE_SAMPLE_COVERAGE"


def test_net_portfolio_can_be_predeclared_only_when_original_cohort_contains_it():
    from quantos.contracts.gold_forward_evaluation_design import declare
    from tests.contracts.test_gold_forward_evaluation_design import SOURCE, CONFIG, START, INTERVAL

    configuration = copy.deepcopy(CONFIG)
    options = dict(
        program_id=1,
        attempt=1,
        source_identity=SOURCE,
        first_close_ms=START,
        window_bars=1,
        declared_at_ms=START - INTERVAL - 180000,
        selected_candidate="net_portfolio",
        proposal_sha256="a" * 64,
    )
    with pytest.raises(ValueError, match="SELECTION"):
        declare(configuration=configuration, **options)
    configuration["portfolio_plan"] = dict(quantities={n: "0.1" for n in NAMES}, max_notional="100")
    declared = declare(configuration=configuration, **options)
    assert declared["selection"]["candidate_id"] == "net_portfolio"
    assert declared["budget"]["max_trials"] == 4
    assert declared["cohort"][-1]["candidate_id"] == "net_portfolio"
    assert declared["economic_authority"] is declared["submission_enabled"] is False


@pytest.mark.parametrize("precision", [6, 28, 80])
def test_three_way_partial_inventory_flattens_exactly_without_rounding_ghosts(monkeypatch, precision):
    p = portfolio(
        monkeypatch,
        sides={n: "BUY" for n in NAMES},
        cash="400",
        fee=".6",
        frames=(
            frame(0),
            frame(10, size=".01"),
            frame(20, size=".01"),
            frame(30),
            frame(40, price="90"),
            frame(50, price="90"),
            frame(60, price="90"),
        ),
    )
    assert p["loss_control"]["status"] == "STOPPED_FLAT"
    with localcontext() as context:
        context.prec = precision
        value = inputs(p, end=60)
        final = list(value.net_attribution.history.items())[-1]
    assert final["quantities"] == {n: "0" for n in NAMES}
    assert value.projection["net_attribution"]["latest"]["quantities"] == {n: "0" for n in NAMES}
    assert value.projection["net_attribution"]["cash_conservation_residual"] == "0"
