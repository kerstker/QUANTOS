"""Read-only attribution of one original Gold account; never recompute its P&L.

Original material authentication precedes this adapter. Allocation is a fixed,
prospective convention for shared inventory, not causal/standalone alpha P&L.
It consumes original filled quantities, atomic equity changes and cash facts.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from itertools import chain

from quantos.contracts.decimal_arithmetic import canonical_decimal, ledger_decimal_context
from quantos.contracts.gold_net_allocation import NAMES, POLICY
from quantos.streaming_identity import StreamArray, exact_stream_identity_hash as digest

ZERO = Decimal(0)
CASH = ("net_pnl", "fee", "spread", "impact", "funding", "liquidation_fee")


def require(condition, reason):
    if not condition:
        raise ValueError("GOLD_NET_ATTRIBUTION_" + reason)


def number(value):
    require(type(value) is str and 0 < len(value) <= 512, "NUMBER")
    result = Decimal(value)
    require(
        result.is_finite() and (not result or -96 <= result.adjusted() <= 96) and canonical_decimal(result) == value,
        "NUMBER",
    )
    return result


def exact_sum(values):
    # Exact conservation of a bounded set of already rounded Decimal amounts.
    # No valuation, cost basis, FX or cash-flow computation takes place here.
    with localcontext() as context:
        context.prec = 512
        return sum(values, ZERO)


def partition(total, basis):
    result = {n: ZERO for n in NAMES}
    active = [n for n in NAMES if basis[n] != 0]
    if not active:
        require(total == 0, "UNOWNED_CHANGE")
        return result
    denominator = exact_sum(basis[n].copy_abs() for n in active)
    for name in active[:-1]:
        result[name] = total * (abs(basis[name]) / denominator)
    result[active[-1]] = exact_sum(chain((total,), (result[n].copy_negate() for n in active[:-1])))
    return result


def quantity(row):
    positions = row["positions"]
    require(isinstance(positions, Mapping) and set(positions) <= {"XAUUSDT"}, "POSITION_SCOPE")
    return number(positions["XAUUSDT"]["quantity"]) if positions else ZERO


def rows(value, maximum):
    require(isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) <= maximum, "BUDGET")
    return iter(value)


def intents(portfolio, simulation, maximum):
    """Merge original decisions/recipes/target trace in constant memory."""
    recipes = rows(portfolio["allocation_intents"], maximum)
    trace = rows(portfolio["target_trace"], maximum)
    current_trace = next(trace, None)
    counters = dict(normal=0, stop=0)
    last_time = -1
    for decision in rows(simulation["decision_events"], maximum):
        timestamp = decision["timestamp"]
        require(type(timestamp) is int and timestamp > last_time, "DECISION_CLOCK")
        last_time = timestamp
        for action in decision["actions"]:
            if "quantity_delta" not in action:
                continue
            require(action["instrument_id"] == "XAUUSDT", "INTENT_SCOPE")
            delta = number(action["quantity_delta"])
            require(delta != 0 and type(action["reduce_only"]) is bool, "INTENT_QUANTITY")
            order_id = action["order_id"]
            require(type(order_id) is str and 0 < len(order_id) <= 256, "ORDER_ID")
            recipe = None
            if order_id.startswith("gold-portfolio-"):
                counters["normal"] += 1
                require(order_id == "gold-portfolio-" + str(counters["normal"]), "ORDER_SEQUENCE")
                recipe = next(recipes, None)
                require(
                    isinstance(recipe, Mapping)
                    and set(recipe) == {"timestamp", "order_id", "desired", "actual_quantity", "net_target"},
                    "INTENT_RECIPE",
                )
                require(recipe["order_id"] == order_id and recipe["timestamp"] == timestamp, "INTENT_BINDING")
                while current_trace is not None and current_trace["timestamp"] < timestamp:
                    current_trace = next(trace, None)
                require(
                    current_trace is not None
                    and current_trace["timestamp"] == timestamp
                    and dict(current_trace) == {k: v for k, v in recipe.items() if k != "order_id"},
                    "TARGET_BINDING",
                )
                desired = recipe["desired"]
                require(isinstance(desired, Mapping) and set(desired) == set(NAMES), "TARGET_SCOPE")
                target = exact_sum(number(desired[n]) for n in NAMES)
                current = quantity(decision)
                require(
                    number(recipe["actual_quantity"]) == current and number(recipe["net_target"]) == target,
                    "TARGET_SUM",
                )
                expected = -current if current * target < 0 else target - current
                reducing = current != 0 and (current * target <= 0 or abs(target) < abs(current))
                require(delta == expected and action["reduce_only"] == reducing, "ACTION_TARGET")
            else:
                counters["stop"] += 1
                loss = portfolio["loss_control"]
                require(
                    order_id == "gold-loss-stop-" + str(counters["stop"])
                    and loss["trigger"] is not None
                    and timestamp >= loss["trigger"]["timestamp"]
                    and action["reduce_only"]
                    and delta == -quantity(decision),
                    "STOP_BINDING",
                )
            yield dict(
                order_id=order_id, timestamp=timestamp, delta=delta, reduce_only=action["reduce_only"], recipe=recipe
            )
    require(next(recipes, None) is None, "EXTRA_RECIPE")


def events(simulation, maximum):
    for event in rows(simulation["ledger_events"], maximum):
        kind = event["event_type"]
        if kind == "TRADE" and number(event["filled_quantity"]) == 0:
            continue
        if kind == "CONTRACT_RULE_TRANSITION":
            continue  # Actual quantity continuity is checked on every equity row.
        require(
            kind in ("TRADE", "FUNDING_SETTLEMENT", "MARK", "LIQUIDATION") and event["instrument_id"] == "XAUUSDT",
            "EVENT_SCOPE",
        )
        yield event


@dataclass(frozen=True)
class NetAttribution:
    projection: dict
    history: StreamArray
    weights: dict | None
    contribution_returns: dict | None


@ledger_decimal_context()
def build_net_attribution(
    portfolio, simulation, *, observations, points, end, covered, nonpositive, maximum_points, maximum_events
):
    if portfolio is None:
        return None
    require(isinstance(portfolio, Mapping), "PORTFOLIO")
    construction = portfolio["construction"]
    policy = construction.get("net_allocation_policy")
    if policy is None:
        require(
            construction["version"] == "GOLD_FIXED_NET_TARGET_PORTFOLIO_V1" and "allocation_intents" not in portfolio,
            "LEGACY_RECIPE",
        )
        return None
    require(
        construction["version"] == "GOLD_FIXED_NET_TARGET_PORTFOLIO_V2"
        and policy == POLICY
        and portfolio["simulation"]["result_hash"] == simulation["result_hash"]
        and simulation["position_history_policy"] == "ACTUAL_LEDGER_POSITION_MARKS_V1",
        "POLICY_BINDING",
    )
    require(all(type(v) is int and 0 < v <= 100_000_000 for v in (maximum_points, maximum_events)), "BUDGET")

    def replay():
        # Generator bodies enter their own context on each lazy consumption.
        with ledger_decimal_context():
            event_rows = events(simulation, maximum_events)
            upcoming = next(event_rows, None)
            actions = intents(portfolio, simulation, maximum_events)
            action = None
            filled = ZERO
            owned = {n: ZERO for n in NAMES}
            totals = {n: {k: ZERO for k in CASH} for n in NAMES}
            adjustment = ZERO
            previous = opening = None
            for index, (row, known) in enumerate(
                zip(rows(simulation["equity_history"], maximum_points), observations(), strict=True)
            ):
                require(row["sequence"] == index and type(row["sequence"]) is int, "SEQUENCE")
                equity = number(row["equity"])
                actual = quantity(row)
                before = exact_sum(owned.values())
                phase = row["phase"]
                cash = {k: ZERO for k in CASH}
                basis = dict(owned)
                event = None
                backstop = ZERO
                if phase == "INITIAL":
                    require(index == 0 and actual == 0 and equity > 0, "INITIAL")
                    opening = previous = equity
                elif phase in ("FILL", "FUNDING", "MARK"):
                    event = upcoming
                    expected = dict(FILL="TRADE", FUNDING="FUNDING_SETTLEMENT", MARK="MARK")[phase]
                    require(
                        event is not None
                        and event["event_type"] == expected
                        and event["timestamp"] == row["timestamp"],
                        "EVENT_PHASE",
                    )
                    upcoming = next(event_rows, None)
                    if phase == "FILL":
                        require(event["quantity_basis"] == "ADMITTED_ORDER_FILL", "FILL_BASIS")
                        if action is None or action["order_id"] != event["order_id"]:
                            action = next(actions, None)
                            while action is not None and action["order_id"] != event["order_id"]:
                                action = next(actions, None)
                            filled = ZERO
                        require(action is not None and action["timestamp"] < row["timestamp"], "FILL_INTENT")
                        delta = number(event["filled_quantity"])
                        filled = exact_sum((filled, delta))
                        require(
                            delta * action["delta"] > 0
                            and abs(filled) <= abs(action["delta"])
                            and actual == exact_sum((before, delta)),
                            "FILL_QUANTITY",
                        )
                        if action["reduce_only"]:
                            require(before * delta < 0 and abs(delta) <= abs(before), "REDUCTION")
                        else:
                            require(before == 0 or before * delta > 0, "OPENING_REVERSAL")
                            recipe = action["recipe"]
                            require(recipe is not None, "OPENING_RECIPE")
                            basis = {
                                n: number(recipe["desired"][n]) if number(recipe["desired"][n]) * delta > 0 else ZERO
                                for n in NAMES
                            }
                        # A complete original reduction consumes every exact
                        # allocated quantity, including earlier division remainders.
                        shares = (
                            {n: owned[n].copy_negate() for n in NAMES}
                            if action["reduce_only"] and actual == 0
                            else partition(delta, basis)
                        )
                        owned = {n: exact_sum((owned[n], shares[n])) for n in NAMES}
                        for key, original in (
                            ("fee", "applied_fee"),
                            ("spread", "applied_spread_cost"),
                            ("impact", "applied_impact_cost"),
                        ):
                            cash[key] = number(event[original])
                            require(cash[key] >= 0, "NEGATIVE_COST")
                    elif phase == "FUNDING":
                        require(number(event["pre_settlement_quantity"]) == before, "FUNDING_QUANTITY")
                        cash["funding"] = number(event["cash_flow"])
                    if upcoming is not None and upcoming["event_type"] == "LIQUIDATION":
                        liquidation = upcoming
                        require(
                            phase != "FILL"
                            and liquidation["timestamp"] == row["timestamp"]
                            and number(liquidation["quantity"]) == before
                            and actual == 0,
                            "LIQUIDATION",
                        )
                        cash["liquidation_fee"] = number(liquidation["liquidation_fee"])
                        backstop = exact_sum(
                            (number(liquidation["insurance_adjustment"]), number(liquidation["adl_adjustment"]))
                        )
                        owned = {n: ZERO for n in NAMES}
                        upcoming = next(event_rows, None)
                else:
                    require(phase == "MARKET" and previous == equity, "UNKNOWN_CHANGE")
                require(
                    actual == exact_sum(owned.values()) and all(q == 0 or q * actual > 0 for q in owned.values()),
                    "INVENTORY_CONSERVATION",
                )
                cash["net_pnl"] = exact_sum((equity, previous.copy_negate(), backstop.copy_negate()))
                adjustment = exact_sum((adjustment, backstop))
                for key, value in cash.items():
                    allocated = partition(value, basis)
                    for name in NAMES:
                        totals[name][key] = exact_sum((totals[name][key], allocated[name]))
                require(
                    exact_sum(chain((adjustment, opening), (totals[n]["net_pnl"] for n in NAMES))) == equity,
                    "CASH_CONSERVATION",
                )
                notionals = partition(
                    number(row["positions"]["XAUUSDT"]["notional"]) if row["positions"] else ZERO, owned
                )
                signed_notional = exact_sum(notionals.values()) * (-1 if actual < 0 else 1)
                weights = partition(signed_notional / equity, owned) if equity > 0 else None
                previous = equity
                yield dict(
                    timestamp=row["timestamp"],
                    available_at_ms=known["timestamp"],
                    sequence=index,
                    phase=phase,
                    equity=row["equity"],
                    quantities={n: canonical_decimal(owned[n]) for n in NAMES},
                    signed_weights=None if weights is None else {n: canonical_decimal(weights[n]) for n in NAMES},
                    totals={n: {k: canonical_decimal(totals[n][k]) for k in CASH} for n in NAMES},
                    account_adjustment_pnl=canonical_decimal(adjustment),
                )
            require(upcoming is None, "UNCONSUMED_EVENT")
            for _ in actions:
                pass  # Even rejected/unfilled orders require their original recipe.
            identity = simulation["reconciliation"]
            require(
                opening == number(identity["initial_equity"])
                and previous == number(identity["ending_equity"])
                and number(identity["residual"]) == 0,
                "ORIGINAL_CASH_IDENTITY",
            )
            for key in CASH[1:]:
                require(exact_sum(totals[n][key] for n in NAMES) == number(identity[key]), "ORIGINAL_" + key.upper())
            require(
                adjustment == exact_sum((number(identity["insurance_adjustment"]), number(identity["adl_adjustment"]))),
                "ORIGINAL_ADJUSTMENT",
            )

    latest = terminal = None
    count = 0
    adjustments_observed = False
    active_components = set()
    for row in replay():
        terminal = row
        if row["available_at_ms"] <= end:
            latest = row
            count += 1
            adjustments_observed |= number(row["account_adjustment_pnl"]) != 0
            active_components.update(
                n
                for n in NAMES
                if number(row["quantities"][n]) != 0 or any(number(v) != 0 for v in row["totals"][n].values())
            )
    require(latest is not None and terminal is not None, "EMPTY_HISTORY")
    causal = StreamArray(lambda: (r for r in replay() if r["available_at_ms"] <= end))
    weight_status = (
        "UNAVAILABLE_SAMPLE_COVERAGE"
        if not covered
        else "UNAVAILABLE_NONPOSITIVE_EQUITY"
        if nonpositive
        else "AVAILABLE"
    )
    returns_status = (
        "UNAVAILABLE_ACCOUNT_ADJUSTMENT"
        if adjustments_observed
        else weight_status
        if weight_status != "AVAILABLE"
        else "UNAVAILABLE_EMPTY_RETURNS"
        if len(points) < 2
        else "AVAILABLE"
    )
    contributions = None
    if returns_status == "AVAILABLE":
        contributions = {n: [] for n in NAMES}
        observed = iter(causal.items())
        upcoming = next(observed, None)
        anchor = previous_point = None
        for point in points:
            while upcoming is not None and upcoming["available_at_ms"] <= point["timestamp"]:
                anchor = upcoming
                upcoming = next(observed, None)
            require(anchor is not None and anchor["equity"] == point["equity"], "GRID_BINDING")
            if previous_point is not None:
                old_point, old_anchor = previous_point
                changes = {
                    n: exact_sum(
                        (
                            number(anchor["totals"][n]["net_pnl"]),
                            number(old_anchor["totals"][n]["net_pnl"]).copy_negate(),
                        )
                    )
                    for n in NAMES
                }
                active = [n for n in NAMES if changes[n] != 0]
                amounts = {n: ZERO for n in NAMES}
                for name in active[:-1]:
                    amounts[name] = changes[name] / number(old_point["equity"])
                if active:
                    amounts[active[-1]] = exact_sum(
                        chain((number(point["net_return"]),), (amounts[n].copy_negate() for n in active[:-1]))
                    )
                else:
                    require(number(point["net_return"]) == 0, "UNATTRIBUTED_RETURN")
                for name in NAMES:
                    contributions[name].append(amounts[name])
            previous_point = point, anchor
        contributions = {n: tuple(v) for n, v in contributions.items()}
    weights = {n: number(latest["signed_weights"][n]) for n in NAMES} if weight_status == "AVAILABLE" else None
    projection = dict(
        schema="QUANTOS_GOLD_NET_ACCOUNT_ATTRIBUTION_V1",
        status=returns_status,
        policy=dict(POLICY),
        policy_sha256=digest(POLICY),
        native_result_hash=simulation["result_hash"],
        construction_sha256=digest(construction),
        intents_sha256=digest(portfolio["allocation_intents"]),
        history_sha256=digest(causal),
        observation_count=count,
        as_of_ms=end,
        latest=latest,
        weights_status=weight_status,
        totals=latest["totals"],
        account_adjustment_pnl=latest["account_adjustment_pnl"],
        cash_conservation_residual="0",
        return_kind=POLICY["returns"],
        weight_kind="SIGNED_ALLOCATED_ORIGINAL_MARK_NOTIONAL_OVER_ACCOUNT_EQUITY",
        active_component_ids=sorted(active_components),
        independent_strategy_pnl=False,
        independent_verification=False,
        contribution_returns_sha256=None
        if contributions is None
        else digest({n: tuple(canonical_decimal(v) for v in values) for n, values in contributions.items()}),
        limitations=[
            "ALLOCATION_CONVENTION_NOT_CAUSAL_ALPHA_PNL",
            "SHARED_ACCOUNT_NOT_INDEPENDENT_ALPHA_BOOKS",
            "INACTIVE_ZERO_CONTRIBUTIONS_ARE_NOT_DIVERSIFICATION",
            "SOURCE_AND_POLICY_TIME_REQUIRE_INDEPENDENT_VERIFICATION",
        ],
        economic_authority=False,
        submission_enabled=False,
    )
    return NetAttribution(projection, causal, weights, contributions)
