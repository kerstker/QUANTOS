"""Original shared daily ADV capacity and original simulated account scale.

No execution imports: execution already depends on validation. This adapter
checks the canonical recorded snapshot geometry and amounts, not a second ADV,
fill, cash or capacity consumption ledger. Actual ADV computation stays in the
original public converter; ten-percent research capacity never changes the
original one-percent execution policy. Monetary values stay explicitly USDT.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from quantos.contracts.decimal_arithmetic import canonical_decimal, ledger_decimal_context
from quantos.contracts.gold_market_capacity_policy import POLICY, validate, policy_sha256
from quantos.contracts.gold_net_allocation import NAMES
from quantos.streaming_identity import StreamArray, exact_stream_identity_hash as digest
from services.validation.gold_net_attribution import partition, exact_sum

HISTORY_POLICY = "GOLD_ORIGINAL_PUBLIC_ADV_CAPACITY_SNAPSHOTS_V1"
SCHEMA = "QUANTOS_GOLD_CAPACITY_INPUTS_V1"
DAY = 86400000


def require(ok, reason):
    if not ok:
        raise ValueError("GOLD_CAPACITY_INPUTS_" + reason)


def time(value):
    require(type(value) is int and 0 <= value < 2**63, "TIME")
    return value


def number(value):
    require(type(value) is str and 0 < len(value) <= 256, "NUMBER")
    try:
        value = Decimal(value)
    except InvalidOperation:
        raise ValueError("GOLD_CAPACITY_INPUTS_NUMBER") from None
    require(value.is_finite() and (not value or -96 <= value.adjusted() <= 96), "NUMBER")
    return value


@ledger_decimal_context()
def snapshot(row):
    require(
        isinstance(row, Mapping)
        and set(row)
        == {
            "instrument_id",
            "policy",
            "adv_window_start",
            "adv_window_end",
            "available_time",
            "ingestion_time",
            "source_identity",
        },
        "SNAPSHOT_FIELDS",
    )
    require(
        row["instrument_id"] == POLICY["instrument_id"]
        and type(row["source_identity"]) is str
        and 0 < len(row["source_identity"]) <= 1024,
        "SNAPSHOT_IDENTITY",
    )
    p = row["policy"]
    require(
        isinstance(p, Mapping)
        and set(p)
        == {
            "venue_id",
            "window_id",
            "period_rule_id",
            "period_index",
            "consumption_window_start",
            "consumption_window_end",
            "consumption_window_duration_ms",
            "consumption_period_epoch_ms",
            "required_adv_observation_periods",
            "required_adv_window_start",
            "required_adv_window_end",
            "adv_notional",
            "max_participation_rate",
            "maximum_notional",
            "action",
        },
        "POLICY",
    )
    day = time(p["period_index"]) * DAY
    require(day + DAY < 2**63 and day >= 30 * DAY, "PERIOD")
    adv, rate, limit = (number(p[k]) for k in ("adv_notional", "max_participation_rate", "maximum_notional"))
    require(
        adv > 0
        and len(adv.as_tuple().digits) <= 48
        and adv.adjusted() <= 24
        and adv.as_tuple().exponent >= -22
        and rate == Decimal(POLICY["original_execution_participation"])
        and limit == adv * rate,
        "ORIGINAL_CAPACITY_AMOUNT",
    )
    expected = dict(
        venue_id=POLICY["venue_id"],
        window_id=p.get("window_id"),
        period_rule_id=POLICY["period_rule_id"],
        period_index=p["period_index"],
        consumption_window_start=day,
        consumption_window_end=day + DAY,
        consumption_window_duration_ms=DAY,
        consumption_period_epoch_ms=0,
        required_adv_observation_periods=30,
        required_adv_window_start=day - 30 * DAY,
        required_adv_window_end=day,
        adv_notional=p["adv_notional"],
        max_participation_rate=p["max_participation_rate"],
        maximum_notional=p["maximum_notional"],
        action="REJECT",
    )
    require(
        set(p) == set(expected)
        and digest(p) == digest(expected)
        and type(p["window_id"]) is str
        and 0 < len(p["window_id"]) <= 1024,
        "ORIGINAL_POLICY_GEOMETRY",
    )
    require(
        time(row["adv_window_start"]) == day - 30 * DAY
        and time(row["adv_window_end"]) == day
        and day <= time(row["available_time"]) < day + DAY
        and time(row["available_time"]) <= time(row["ingestion_time"]),
        "ORIGINAL_ADV_CLOCK",
    )
    return dict(row)


def snapshots(values):
    previous = -1
    for row in values:
        value = snapshot(row)
        available = value["ingestion_time"]
        require(available > previous, "SNAPSHOT_ORDER_DUPLICATE")
        previous = available
        yield value


def account_rows(stream, maximum):
    previous = -1
    for index, row in enumerate(stream.items()):
        require(
            index < maximum and isinstance(row, Mapping) and {"timestamp", "sequence", "equity"} <= set(row),
            "ACCOUNT_SHAPE_BUDGET",
        )
        stamp = time(row["timestamp"])
        require(stamp >= previous and type(row["sequence"]) is int and row["sequence"] == index, "ACCOUNT_AXIS")
        number(row["equity"])
        previous = stamp
        yield dict(timestamp=stamp, sequence=index, equity=row["equity"])


def allocation_rows(stream, maximum):
    previous = -1
    if stream is None:
        return
    for index, row in enumerate(stream.items()):
        require(
            index < maximum
            and isinstance(row, Mapping)
            and {"timestamp", "available_at_ms", "sequence", "equity", "signed_weights"} <= set(row),
            "ALLOCATION_SHAPE_BUDGET",
        )
        known = time(row["available_at_ms"])
        require(
            time(row["timestamp"]) <= known
            and known >= previous
            and type(row["sequence"]) is int
            and row["sequence"] == index,
            "ALLOCATION_AXIS",
        )
        number(row["equity"])
        weights = row["signed_weights"]
        if weights is not None:
            require(isinstance(weights, Mapping) and set(weights) == set(NAMES), "ALLOCATION_COMPONENTS")
            for value in weights.values():
                number(value)
        previous = known
        yield dict(
            timestamp=row["timestamp"],
            available_at_ms=known,
            sequence=index,
            equity=row["equity"],
            signed_weights=None if weights is None else dict(weights),
        )


@dataclass(frozen=True)
class CapacityInputs:
    projection: dict
    observations: StreamArray
    engine_inputs: tuple[dict, ...] | None


def build_capacity_inputs(
    policy,
    *,
    capacity_history,
    capacity_history_policy,
    sample,
    account_observations,
    allocation_observations,
    account_returns_available,
    maximum_snapshots,
    maximum_intervals,
    maximum_points=100000,
):
    bound = validate(policy, sample=sample)
    require(capacity_history_policy == HISTORY_POLICY, "HISTORY_POLICY")
    require(
        type(account_returns_available) is bool
        and all(
            type(v) is int and 0 < v <= 100_000_000 for v in (maximum_snapshots, maximum_intervals, maximum_points)
        ),
        "BUDGET",
    )
    require(
        isinstance(capacity_history, Sequence)
        and not isinstance(capacity_history, (str, bytes))
        and len(capacity_history) <= maximum_snapshots,
        "SNAPSHOT_BUDGET",
    )
    require(
        isinstance(account_observations, StreamArray)
        and (allocation_observations is None or isinstance(allocation_observations, StreamArray)),
        "ACCOUNT_STREAM",
    )
    start, end, frequency = (sample[k] for k in ("start_ms", "end_ms", "frequency_ms"))
    count = (end - start) // frequency
    require(count <= maximum_intervals, "GRID_BUDGET")
    source_hash = digest(StreamArray(lambda: snapshots(capacity_history)))
    account_hash = digest(StreamArray(lambda: account_rows(account_observations, maximum_points)))
    allocation_hash = (
        None
        if allocation_observations is None
        else digest(StreamArray(lambda: allocation_rows(allocation_observations, maximum_points)))
    )

    @ledger_decimal_context()
    def observation(decision, current_cap, current_account, current_allocation):
        status = "AVAILABLE"
        adv = execution_limit = research_capacity = alpha_capacity = equity = None
        if (
            current_cap is None
            or not current_cap["policy"]["consumption_window_start"]
            <= decision
            < current_cap["policy"]["consumption_window_end"]
        ):
            status = "UNAVAILABLE_CURRENT_UTC_DAY_CAPACITY"
        else:
            adv = current_cap["policy"]["adv_notional"]
            execution_limit = current_cap["policy"]["maximum_notional"]
            research_capacity = canonical_decimal(number(adv) * Decimal(POLICY["research_capacity_fraction"]))
        if current_account is None:
            status = "UNAVAILABLE_ACCOUNT_ANCHOR"
        else:
            equity = current_account["equity"]
            if number(equity) <= 0:
                status = "UNAVAILABLE_NONPOSITIVE_EQUITY"
            elif current_allocation is None or current_allocation["signed_weights"] is None:
                status = "UNAVAILABLE_ALPHA_ALLOCATION" if status == "AVAILABLE" else status
            else:
                require(
                    current_allocation["sequence"] == current_account["sequence"]
                    and current_allocation["available_at_ms"] == current_account["timestamp"]
                    and number(current_allocation["equity"]) == number(equity),
                    "ALLOCATION_ACCOUNT_BINDING",
                )
                if research_capacity is not None:
                    basis = {n: number(v).copy_abs() for n, v in current_allocation["signed_weights"].items()}
                    allocated = partition(number(research_capacity), basis) if any(basis.values()) else {}
                    alpha_capacity = {n: canonical_decimal(v) for n, v in allocated.items() if basis[n] != 0}
                    require(
                        not alpha_capacity
                        or exact_sum(number(v) for v in alpha_capacity.values()) == number(research_capacity),
                        "SHARED_POOL_CONSERVATION",
                    )
        return dict(
            decision_at_ms=decision,
            return_end_ms=decision + frequency,
            status=status,
            currency="USDT",
            original_daily_adv=adv,
            original_execution_daily_limit=execution_limit,
            research_daily_capacity=research_capacity,
            alpha_daily_capacity=alpha_capacity,
            account_equity=equity,
            account_sequence=None if current_account is None else current_account["sequence"],
            capacity_snapshot_sha256=None if current_cap is None else digest(current_cap),
            pool_instrument="XAUUSDT",
        )

    def observations():
        if not account_returns_available:
            return
        caps = iter(snapshots(capacity_history))
        next_cap = next(caps, None)
        current_cap = None
        accounts = iter(account_rows(account_observations, maximum_points))
        next_account = next(accounts, None)
        current_account = None
        assigned = iter(allocation_rows(allocation_observations, maximum_points))
        next_allocation = next(assigned, None)
        current_allocation = None
        for decision in range(start, end, frequency):
            while next_cap is not None and next_cap["ingestion_time"] <= decision:
                current_cap = next_cap
                next_cap = next(caps, None)
            while next_account is not None and next_account["timestamp"] <= decision:
                current_account = next_account
                next_account = next(accounts, None)
            while next_allocation is not None and next_allocation["available_at_ms"] <= decision:
                current_allocation = next_allocation
                next_allocation = next(assigned, None)
            yield observation(decision, current_cap, current_account, current_allocation)

    stream = StreamArray(observations)
    rows = []
    statuses = Counter()
    for row in stream.items():
        rows.append(row)
        statuses[row["status"]] += 1
    missing = count if not account_returns_available else sum(n for k, n in statuses.items() if k != "AVAILABLE")
    ready = account_returns_available and missing == 0
    inputs = tuple(rows) if ready else None
    projection = dict(
        schema=SCHEMA,
        status="AVAILABLE"
        if ready
        else "UNAVAILABLE_ACCOUNT_SERIES"
        if not account_returns_available
        else "UNAVAILABLE_CAPACITY_OR_ACCOUNT_INPUTS",
        sample=dict(sample),
        currency="USDT",
        policy_sha256=policy_sha256(bound),
        capacity_history_policy=HISTORY_POLICY,
        original_snapshot_count=len(capacity_history),
        original_snapshots_sha256=source_hash,
        account_observations_sha256=account_hash,
        allocation_observations_sha256=allocation_hash,
        observation_sha256=digest(stream),
        engine_inputs_sha256=None if inputs is None else digest(inputs),
        interval_count=count,
        observation_count=len(rows),
        missing_intervals=missing,
        status_counts=dict(sorted(statuses.items())),
        aum_kind="ORIGINAL_SIMULATED_NET_ACCOUNT_RECEIPT_AVAILABLE_EQUITY",
        capacity_clock=POLICY["capacity_clock"],
        shared_capacity_pool="XAUUSDT",
        original_execution_policy_changed=False,
        independent_verification=False,
        limitations=[
            "SAME_ORIGINAL_PUBLIC_ADV_NOT_INDEPENDENT_MATHEMATICS",
            "SIMULATED_ACCOUNT_SCALE_IS_NOT_BROKER_AUM_ATTESTATION",
            "SHARED_INSTRUMENT_CAPACITY_NOT_INDEPENDENT_STRATEGY_CAPACITIES",
            "RESEARCH_TEN_PERCENT_ADV_NEVER_CHANGES_EXECUTION_ONE_PERCENT",
            "ADV_NOT_ORDER_BOOK_DEPTH_OR_REAL_FILL_COST_CALIBRATION",
            "NO_SOURCE_OR_TIME_AUTHORITY",
        ],
        economic_authority=False,
        submission_enabled=False,
    )
    return CapacityInputs(projection, stream, inputs)
