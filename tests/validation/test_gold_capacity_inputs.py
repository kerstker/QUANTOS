"""Original public ADV and original net account, without defaults or FX."""

from copy import deepcopy
from decimal import Decimal, localcontext

from quantos.streaming_identity import StreamArray
from tests.execution.test_perpetual_public_replay import START, DAY

import pytest


@pytest.mark.parametrize("engine", ["capacity", "liquidity"])
@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "wrong_key",
        "zero",
        "negative",
        "nan",
        "inf",
        "bool",
        "string",
        "aum_zero",
        "aum_negative",
        "aum_nan",
        "aum_bool",
        "weight_bool",
        "weight_nan",
        "zero_book",
    ],
)
def test_original_money_risk_rejects_missing_or_invalid_capacity(engine, damage):
    from services.risk.research_risk.domain.risk_modules import CapacityRiskEngine, LiquidityRiskEngine
    from services.risk.research_risk.domain.models import RiskConfig

    weights = {"alpha": 0.0001}
    capacity = {"alpha": 1e6}
    aum = 1.0
    if damage == "missing":
        capacity = {}
    elif damage == "wrong_key":
        capacity = {"other": 1e6}
    elif damage == "zero":
        capacity["alpha"] = 0.0
    elif damage == "negative":
        capacity["alpha"] = -1.0
    elif damage == "nan":
        capacity["alpha"] = float("nan")
    elif damage == "inf":
        capacity["alpha"] = float("inf")
    elif damage == "bool":
        capacity["alpha"] = True
    elif damage == "string":
        capacity["alpha"] = "1000000"
    elif damage == "aum_zero":
        aum = 0.0
    elif damage == "aum_negative":
        aum = -1.0
    elif damage == "aum_nan":
        aum = float("nan")
    elif damage == "aum_bool":
        aum = True
    elif damage == "weight_bool":
        weights["alpha"] = True
    elif damage == "weight_nan":
        weights["alpha"] = float("nan")
    else:
        weights["alpha"] = 0.0
    report = (
        CapacityRiskEngine().evaluate(weights, capacity, aum, RiskConfig())
        if engine == "capacity"
        else LiquidityRiskEngine().evaluate(weights, capacity, 1e7, aum, RiskConfig())
    )
    assert report.passed is False


@pytest.mark.parametrize("volume", [0.0, -1.0, float("nan"), float("inf"), True, "1000000"])
def test_original_liquidity_rejects_unavailable_daily_volume(volume):
    from services.risk.research_risk.domain.risk_modules import LiquidityRiskEngine
    from services.risk.research_risk.domain.models import RiskConfig

    assert LiquidityRiskEngine().evaluate({"alpha": 0.0001}, {"alpha": 1e6}, volume, 1.0, RiskConfig()).passed is False


def test_valid_original_capacity_math_is_preserved():
    from services.risk.research_risk.domain.risk_modules import CapacityRiskEngine, LiquidityRiskEngine
    from services.risk.research_risk.domain.models import RiskConfig

    weights = {"a": 0.125, "b": -0.125}
    capacities = {"a": 14e6, "b": 7e6}
    aum = 7e6
    cap = CapacityRiskEngine().evaluate(weights, capacities, aum, RiskConfig())
    liq = LiquidityRiskEngine().evaluate(weights, capacities, 210e6, aum, RiskConfig())
    assert cap.capacity_usage == pytest.approx(0.125)
    assert liq.participation_rate == pytest.approx(0.0125)
    assert liq.avg_daily_volume_usage == pytest.approx(1.75e6 / 210e6)
    assert cap.passed is liq.passed is True


def test_subunit_capacity_is_not_rounded_up_to_one_money_unit():
    from services.risk.research_risk.domain.risk_modules import CapacityRiskEngine, LiquidityRiskEngine
    from services.risk.research_risk.domain.models import RiskConfig

    cap = CapacityRiskEngine().evaluate({"a": 0.02}, {"a": 0.01}, 1.0, RiskConfig())
    liq = LiquidityRiskEngine().evaluate({"a": 0.02}, {"a": 0.01}, 0.1, 1.0, RiskConfig())
    assert cap.capacity_usage == pytest.approx(2.0) and cap.passed is False
    assert liq.participation_rate == pytest.approx(0.2) and liq.passed is False


def test_subunit_daily_volume_is_not_rounded_up_to_one_money_unit():
    from services.risk.research_risk.domain.risk_modules import LiquidityRiskEngine
    from services.risk.research_risk.domain.models import RiskConfig

    value = LiquidityRiskEngine().evaluate({"a": 0.005}, {"a": 1e6}, 0.01, 1.0, RiskConfig())
    assert value.avg_daily_volume_usage == pytest.approx(0.5)
    assert value.passed is False


SAMPLE = dict(start_ms=START, end_ms=START + 3000, frequency_ms=1000)


def policy(sample=None, *, declared=None):
    from quantos.contracts.gold_market_capacity_policy import SCHEMA, POLICY

    axis = dict(sample or SAMPLE)
    return dict(
        schema=SCHEMA,
        declared_at_ms=axis["start_ms"] - 1_100_000 if declared is None else declared,
        sample=axis,
        currency="USDT",
        method=deepcopy(POLICY),
        independent_verification=False,
        economic_authority=False,
        submission_enabled=False,
    )


def snapshots():
    from tests.execution.test_perpetual_public_replay import gold_capture_fixture, prepare

    return [prepare(gold_capture_fixture())["frames"][0].capacity_snapshots[0].as_canonical_dict()]


def account(sample=None):
    axis = sample or SAMPLE
    return [
        dict(timestamp=axis["start_ms"] - 1, sequence=0, equity="1000", phase="INITIAL"),
        dict(timestamp=axis["end_ms"], sequence=1, equity="1001", phase="MARK"),
    ]


def allocations(sample=None):
    return [
        dict(
            timestamp=row["timestamp"],
            available_at_ms=row["timestamp"],
            sequence=row["sequence"],
            equity=row["equity"],
            signed_weights=dict(channel="0.3", reversion="0.1", trend="0"),
        )
        for row in account(sample)
    ]


def inputs(values=None, *, sample=None, available=True, assigned=True, account_rows=None, allocation_rows=None):
    from services.validation.gold_capacity_inputs import build_capacity_inputs

    return build_capacity_inputs(
        policy(sample),
        capacity_history=snapshots() if values is None else values,
        capacity_history_policy="GOLD_ORIGINAL_PUBLIC_ADV_CAPACITY_SNAPSHOTS_V1",
        sample=dict(sample or SAMPLE),
        account_observations=StreamArray(lambda: iter(account_rows or account(sample))),
        allocation_observations=StreamArray(lambda: iter(allocation_rows or allocations(sample))) if assigned else None,
        account_returns_available=available,
        maximum_snapshots=100000,
        maximum_intervals=100000,
    )


def test_original_adv_pool_and_actual_account_scale_are_not_multiplied_per_strategy():
    value = inputs()
    rows = list(value.observations.items())
    assert value.projection["status"] == "AVAILABLE"
    assert len(rows) == 3
    row = rows[0]
    assert row["decision_at_ms"] == START and row["return_end_ms"] == START + 1000
    assert row["account_equity"] == "1000"
    assert row["original_daily_adv"] == "1000000"
    assert row["original_execution_daily_limit"] == "10000"
    assert row["research_daily_capacity"] == "100000"
    assert row["alpha_daily_capacity"] == dict(channel="75000", reversion="25000")
    assert sum(Decimal(v) for v in row["alpha_daily_capacity"].values()) == Decimal(row["research_daily_capacity"])
    assert "trend" not in row["alpha_daily_capacity"]
    assert value.projection["currency"] == "USDT"
    assert value.projection["independent_verification"] is value.projection["economic_authority"] is False
    assert value.projection["submission_enabled"] is False


def test_midnight_expires_prior_day_adv_until_original_next_day_receipt():
    values = snapshots()
    old = values[0]
    day = old["policy"]["consumption_window_end"]
    from services.execution.perpetual_ledger_v2 import (
        CapacityPolicy,
        CapacityAction,
        BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,
    )
    from services.execution.authoritative_perpetual_backtest import CapacitySnapshot

    new_policy = CapacityPolicy(
        "BINANCE_USDM",
        "new-received-day",
        BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,
        day // DAY,
        Decimal("2000000"),
        Decimal(".01"),
        CapacityAction.REJECT,
    )
    new = CapacitySnapshot(
        "XAUUSDT", new_policy, day - 30 * DAY, day, day + 1000, day + 1000, "new-received-day"
    ).as_canonical_dict()
    axis = dict(start_ms=day - 1000, end_ms=day + 2000, frequency_ms=1000)
    value = inputs([old, new], sample=axis)
    rows = list(value.observations.items())
    assert rows[0]["original_daily_adv"] == "1000000"
    assert rows[1]["status"] == "UNAVAILABLE_CURRENT_UTC_DAY_CAPACITY" and rows[1]["original_daily_adv"] is None
    assert rows[2]["original_daily_adv"] == "2000000"
    assert value.projection["missing_intervals"] == 1 and value.projection["status"].startswith("UNAVAILABLE_")
    assert value.engine_inputs is None


def test_late_account_and_allocation_points_are_known_only_at_interval_start():
    ar = account()
    ar.insert(1, dict(timestamp=START + 1500, sequence=1, equity="900", phase="FUNDING"))
    ar[-1]["sequence"] = 2
    al = allocations()
    al.insert(
        1,
        dict(
            timestamp=START + 1000,
            available_at_ms=START + 1500,
            sequence=1,
            equity="900",
            signed_weights=dict(channel="0.2", reversion="0.2", trend="0"),
        ),
    )
    al[-1]["sequence"] = 2
    rows = list(inputs(account_rows=ar, allocation_rows=al).observations.items())
    assert [row["account_equity"] for row in rows] == ["1000", "1000", "900"]
    assert rows[2]["account_sequence"] == 1 and rows[2]["alpha_daily_capacity"] == dict(
        channel="50000", reversion="50000"
    )


@pytest.mark.parametrize("damage", ["empty", "unassigned", "account", "negative_equity"])
def test_missing_inputs_remain_unavailable_without_default_capacity_or_aum(damage):
    values = snapshots()
    assigned = True
    available = True
    ar = None
    if damage == "empty":
        values = []
    elif damage == "unassigned":
        assigned = False
    elif damage == "account":
        available = False
    else:
        ar = account()
        ar[0]["equity"] = "-1"
    value = inputs(values, assigned=assigned, available=available, account_rows=ar)
    assert value.projection["status"].startswith("UNAVAILABLE_") and value.engine_inputs is None
    assert value.projection["engine_inputs_sha256"] is None


@pytest.mark.parametrize(
    "damage",
    ["window", "available", "ingestion", "period", "amount", "venue", "instrument", "duplicate", "order", "unit"],
)
def test_malformed_capacity_originals_or_unit_mismatch_reject(damage):
    values = snapshots()
    row = values[0]
    if damage == "window":
        row["adv_window_start"] += 1
    elif damage == "available":
        row["available_time"] = row["adv_window_end"] - 1
    elif damage == "ingestion":
        row["ingestion_time"] = row["available_time"] - 1
    elif damage == "period":
        row["policy"]["consumption_window_end"] += 1
    elif damage == "amount":
        row["policy"]["maximum_notional"] = "1"
    elif damage == "venue":
        row["policy"]["venue_id"] = "OTHER"
    elif damage == "instrument":
        row["instrument_id"] = "BTCUSDT"
    elif damage == "duplicate":
        values.append(deepcopy(row))
    elif damage == "order":
        row["available_time"] = True
    else:
        from services.validation.gold_capacity_inputs import build_capacity_inputs

        p = policy()
        p["currency"] = "CNY"
        with pytest.raises(ValueError):
            build_capacity_inputs(
                p,
                capacity_history=values,
                capacity_history_policy="GOLD_ORIGINAL_PUBLIC_ADV_CAPACITY_SNAPSHOTS_V1",
                sample=SAMPLE,
                account_observations=StreamArray(lambda: iter(account())),
                allocation_observations=StreamArray(lambda: iter(allocations())),
                account_returns_available=True,
                maximum_snapshots=100000,
                maximum_intervals=100000,
            )
        return
    with pytest.raises(ValueError):
        inputs(values)


@pytest.mark.parametrize("precision", [6, 28, 80])
def test_shared_capacity_remainder_and_lazy_replay_ignore_decimal_context(precision):
    al = allocations()
    for row in al:
        row["signed_weights"] = dict(channel="0.1", reversion="0.1", trend="0.1")
    expected = inputs(allocation_rows=al)
    with localcontext() as context:
        context.prec = precision
        actual = inputs(allocation_rows=deepcopy(al))
        rows = list(actual.observations.items())
    assert actual.projection == expected.projection and rows == list(expected.observations.items())
    with localcontext() as context:
        context.prec = 512
        assert sum(Decimal(v) for v in rows[0]["alpha_daily_capacity"].values()) == Decimal("100000")


def test_original_execution_participation_remains_one_percent():
    from quantos.contracts.gold_market_capacity_policy import POLICY
    from services.execution.perpetual_ledger_v2 import BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID

    row = snapshots()[0]
    assert POLICY["period_rule_id"] == BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID
    assert row["policy"]["max_participation_rate"] == "0.01"
    assert inputs().projection["original_execution_policy_changed"] is False


@pytest.mark.parametrize("precision", [6, 28, 80])
def test_repeating_original_adv_replays_under_explicit_ledger_precision(precision):
    from tests.execution.test_perpetual_public_replay import gold_capture_fixture, sealed, prepare
    from quantos.contracts.decimal_arithmetic import ledger_decimal_context, canonical_decimal

    document = gold_capture_fixture()
    document["bootstrap"]["candles"]["payload"][0][7] = "1000001"
    row = prepare(sealed(document))["frames"][0].capacity_snapshots[0].as_canonical_dict()
    expected = inputs([row])
    with localcontext() as context:
        context.prec = precision
        actual = inputs([deepcopy(row)])
        observed = list(actual.observations.items())
    assert actual.projection == expected.projection
    assert observed == list(expected.observations.items())
    with ledger_decimal_context():
        assert observed[0]["research_daily_capacity"] == canonical_decimal(
            Decimal(row["policy"]["adv_notional"]) * Decimal(".1")
        )


def test_capacity_recipe_requires_full_prior_risk_and_market_design_and_old_schema_cannot_inherit():
    from quantos.contracts.gold_forward_evaluation_design import declare, validate
    from tests.contracts.test_gold_forward_evaluation_design import START as design_start, SOURCE, CONFIG, INTERVAL
    from tests.validation.test_gold_research_risk_policy import policy as risk_policy
    from tests.validation.test_gold_market_states import policy as market_policy

    sample = dict(start_ms=design_start, end_ms=design_start + 10000, frequency_ms=10000)
    declared = design_start - INTERVAL - 180000
    options = dict(
        program_id=1,
        attempt=1,
        source_identity=SOURCE,
        configuration=dict(CONFIG),
        first_close_ms=design_start,
        window_bars=1,
        declared_at_ms=declared,
        long_window=True,
        selected_candidate="channel",
        proposal_sha256="a" * 64,
        research_risk_policy=risk_policy(),
        market_state_policy=market_policy(sample),
        capacity_policy=policy(sample, declared=declared),
    )
    value = declare(**options)
    assert value["schema"] == "QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V7"
    assert validate(value) == value
    for schema in ("QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V5", "QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V6"):
        with pytest.raises(ValueError):
            validate(dict(value, schema=schema))
    for field in ("market_state_policy", "research_risk_policy"):
        with pytest.raises(ValueError):
            declare(**dict(options, **{field: None}))
    changed = deepcopy(value)
    changed["risk_policy"]["capacity_policy"]["declared_at_ms"] = declared + 1
    with pytest.raises(ValueError):
        validate(changed)


def test_capacity_recipe_is_saved_before_public_capture_or_job(tmp_path, monkeypatch):
    from tests.tools.test_gold_forward_collection import options, prepare
    from tests.contracts.test_gold_forward_evaluation_design import START as design_start, INTERVAL
    from tests.validation.test_gold_research_risk_policy import policy as risk_policy
    from tests.validation.test_gold_market_states import policy as market_policy

    settings = options()
    sample = dict(
        start_ms=design_start,
        end_ms=design_start + (settings["window_bars"] - 1) * INTERVAL + 10000,
        frequency_ms=10000,
    )
    declared = design_start - INTERVAL - 180000
    coordinator, value = prepare(
        tmp_path / "prospective-capacity",
        monkeypatch,
        research_risk_policy=risk_policy(start=sample["start_ms"], end=sample["end_ms"], frequency=10000),
        market_state_policy=market_policy(sample),
        capacity_policy=policy(sample, declared=declared),
    )
    assert value["forward_design"]["schema"] == "QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V7"
    assert coordinator.read_plan(tmp_path / "prospective-capacity") == value
    assert {p.name for p in (tmp_path / "prospective-capacity").iterdir()} == {"plan.json"}


def test_original_report_retains_exact_execution_snapshots_and_streamed_identity():
    from tools.gold_bar_cost_replay import compare_public, _CapacityHistory
    from tests.tools.test_gold_bar_cost_replay import inputs as public_inputs
    from quantos.streaming_identity import exact_stream_identity_hash as digest
    from tests.execution.test_perpetual_public_replay import prepare

    request = public_inputs()
    report = compare_public(**request)
    prepared = prepare(request["capture"])
    lazy = _CapacityHistory(prepared["frames"])
    expected = [prepared["frames"][0].capacity_snapshots[0].as_canonical_dict()]
    assert len(lazy) == 1 and list(lazy) == expected
    assert report["capacity_history"] == expected
    assert digest(lazy) == digest(expected)
    assert report["capacity_history_policy"] == "GOLD_ORIGINAL_PUBLIC_ADV_CAPACITY_SNAPSHOTS_V1"
    assert report["submission_enabled"] is report["authoritative"] is False


def test_real_net_account_capacity_bridge_uses_original_equity_and_active_shared_pool(monkeypatch):
    from services.validation.gold_account_risk_inputs import build_account_risk_inputs
    from tests.validation.test_gold_net_attribution import portfolio
    from tests.research.test_gold_v2_strategy import T
    from services.execution.perpetual_ledger_v2 import (
        CapacityPolicy,
        CapacityAction,
        BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,
    )
    from services.execution.authoritative_perpetual_backtest import CapacitySnapshot

    p = portfolio(monkeypatch)
    sample = dict(start_ms=T, end_ms=T + 40, frequency_ms=10)
    day = T // DAY * DAY
    original = CapacitySnapshot(
        "XAUUSDT",
        CapacityPolicy(
            "BINANCE_USDM",
            "explicit-synthetic-capacity",
            BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,
            T // DAY,
            Decimal("1000000"),
            Decimal(".01"),
            CapacityAction.REJECT,
        ),
        day - 30 * DAY,
        day,
        T,
        T,
        "explicit-synthetic-capacity",
    ).as_canonical_dict()
    value = build_account_risk_inputs(
        p["simulation"],
        portfolio=p,
        sample=sample,
        settlements=[],
        account_currency="USDT",
        capacity_policy=policy(sample),
        capacity_history=[original],
        capacity_history_policy="GOLD_ORIGINAL_PUBLIC_ADV_CAPACITY_SNAPSHOTS_V1",
    )
    projection = value.projection
    assert projection["schema"] == "QUANTOS_GOLD_ACCOUNT_RISK_INPUTS_V6"
    assert projection["source_groups"]["capacity_and_aum"]["status"] == "AVAILABLE"
    assert projection["capacity_inputs"]["aum_kind"] == "ORIGINAL_SIMULATED_NET_ACCOUNT_RECEIPT_AVAILABLE_EQUITY"
    assert projection["full_research_risk_ready"] is projection["economic_authority"] is False
    assert projection["submission_enabled"] is False
    rows = list(value.capacity_inputs.observations.items())
    assert rows[0]["account_equity"] == "1000" and rows[0]["alpha_daily_capacity"] == {}
    assert rows[1]["account_equity"] == "999.98"
    assert set(rows[1]["alpha_daily_capacity"]) == {"channel", "trend"}
    with localcontext() as context:
        context.prec = 512
        shares = [Decimal(v) for v in rows[1]["alpha_daily_capacity"].values()]
        assert sum(shares) == Decimal("100000")
        assert all(abs(v - Decimal("50000")) < Decimal("1e-43") for v in shares)
    assert len(value.capacity_inputs.engine_inputs) == len(value.returns) == 4


def test_lazy_capacity_does_not_leak_ledger_context_to_consumer():
    value = inputs()
    with localcontext() as context:
        context.prec = 6
        stream = value.observations.items()
        next(stream)
        assert context.prec == 6
        assert Decimal("1") / Decimal("7") == Decimal("0.142857")


@pytest.mark.parametrize("damage", ["too_precise", "too_large", "off_source_quantum", "policy_extra", "bool_period"])
def test_original_adv_numeric_budget_and_exact_snapshot_fields_reject(damage):
    row = snapshots()[0]
    if damage == "too_precise":
        row["policy"]["adv_notional"] = "1." + "1" * 49
    elif damage == "too_large":
        row["policy"]["adv_notional"] = "1" + "0" * 25
    elif damage == "off_source_quantum":
        row["policy"]["adv_notional"] = "0." + "0" * 22 + "1"
    elif damage == "policy_extra":
        row["policy"]["invented_capacity"] = "1000000"
    else:
        row["policy"]["period_index"] = True
    with pytest.raises(ValueError):
        inputs([row])


@pytest.mark.parametrize("damage", ["missing_policy_field", "receipt_after_original_day"])
def test_malformed_original_capacity_fields_and_expired_receipt_are_rejected(damage):
    row = snapshots()[0]
    if damage == "missing_policy_field":
        del row["policy"]["period_index"]
    else:
        row["available_time"] = row["ingestion_time"] = row["policy"]["consumption_window_end"]
    with pytest.raises(ValueError):
        inputs([row])
