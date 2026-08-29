"""Acceptance tests for the only authority-capable perpetual V2 runner.

The fixtures are deliberately small, but every item that is load bearing in
the result has explicit point-in-time provenance.  The runner must derive its
own attestations; callers never provide an ``authoritative=True`` switch.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from decimal import Decimal, ROUND_DOWN, localcontext

import pytest

from services.execution.authoritative_perpetual_backtest import (
    AuthoritativeBacktestInputError,
    CapacitySnapshot,
    CausalDataRecord,
    CostModelSnapshot,
    DecisionRequest,
    ExecutableVenueEvent,
    ExecutionOpportunitySnapshot,
    ExecutionRequest,
    FundingPolicySnapshot,
    HistoricalContractRule,
    ObservedLedgerEvent,
    ResearchRiskEvidence,
    RISK_MARK_MAX_AGE_MS,
    run_authoritative_linear_perpetual_v2,
)
from services.execution.backtest_authority import (
    exact_identity_hash,
    is_authoritative_backtest_record,
)
from services.execution.perpetual_ledger_v2 import (
    BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,
    CapacityAction,
    CapacityPolicy,
    ContractSpec,
    FundingEvent,
    MarginBracket,
    MarginMode,
    MarkEvent,
)
from services.risk.research_risk.domain.models import RiskConfig
from services.risk.research_risk.dry_run_guard import DryRunResearchRiskInputs


D = Decimal
CAPACITY_PERIOD_MS = 86_400_000
DECISION = 2_000_073_600_000
CAPACITY_PERIOD_INDEX = DECISION // CAPACITY_PERIOD_MS


def _record(
    name: str,
    *,
    event_time: int,
    available_time: int,
    ingestion_time: int,
    value: object,
) -> CausalDataRecord:
    return CausalDataRecord(
        record_id=name,
        event_time=event_time,
        available_time=available_time,
        ingestion_time=ingestion_time,
        source_identity=f"archive:{name}:sha256",
        value=value,
    )


def _spec() -> ContractSpec:
    return ContractSpec(
        instrument_id="BTCUSDT",
        contract_type="LINEAR_USDT_PERPETUAL",
        margin_asset="USDT",
        contract_multiplier=D("1"),
        quantity_step=D("0.001"),
        min_quantity=D("0.001"),
        min_notional=D("5"),
        liquidation_fee_rate=D("0.01"),
        settlement_quantum=D("1e-24"),
        margin_brackets=(
            MarginBracket(
                notional_floor=D("0"),
                notional_cap=None,
                initial_margin_rate=D("0.10"),
                maintenance_margin_rate=D("0.05"),
                maintenance_amount=D("0"),
            ),
        ),
    )


def _rule(*, ingestion_time: int = DECISION - 100) -> HistoricalContractRule:
    return HistoricalContractRule(
        rule_id="binance-btcusdt-rules-epoch-7",
        spec=_spec(),
        effective_from=DECISION - 10_000,
        effective_until=DECISION + 10_000,
        observed_time=DECISION - 200,
        available_time=DECISION - 150,
        ingestion_time=ingestion_time,
        source_identity="binance-exchange-info-archive:epoch-7",
    )


def _capacity(
    *,
    maximum_too_small: bool = False,
    adv_notional: str | None = None,
    period_index: int = CAPACITY_PERIOD_INDEX,
    source_identity: str | None = None,
    adv_window_start: int | None = None,
    adv_window_end: int | None = None,
) -> CapacitySnapshot:
    policy = CapacityPolicy(
        venue_id="BINANCE_USDM",
        window_id=f"adv-window-{period_index}",
        period_rule_id=BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,
        period_index=period_index,
        adv_notional=(
            D(adv_notional)
            if adv_notional is not None
            else (D("50") if maximum_too_small else D("1000000000"))
        ),
        max_participation_rate=D("0.10"),
        action=CapacityAction.REJECT,
    )
    return CapacitySnapshot(
        instrument_id="BTCUSDT",
        policy=policy,
        adv_window_start=(
            policy.required_adv_window_start
            if adv_window_start is None
            else adv_window_start
        ),
        adv_window_end=(
            policy.required_adv_window_end
            if adv_window_end is None
            else adv_window_end
        ),
        available_time=policy.required_adv_window_end,
        ingestion_time=policy.required_adv_window_end,
        source_identity=(
            source_identity
            if source_identity is not None
            else f"pit-adv:btc:period-{period_index}:sha256"
        ),
    )


def _cost(*, impact_rate: str = "0.003") -> CostModelSnapshot:
    return CostModelSnapshot(
        model_identity="historical-l2-cost:v5",
        calibration_end=DECISION - 1_000,
        available_time=DECISION - 500,
        ingestion_time=DECISION - 400,
        source_identity="l2-calibration:btc:window-42",
        fee_rate=D("0.001"),
        spread_rate=D("0.002"),
        impact_rate=D(impact_rate),
    )


def _risk(
    *,
    total_aum: float = 1000.0,
    capacity: CapacitySnapshot | None = None,
    config: RiskConfig | None = None,
) -> ResearchRiskEvidence:
    capacity = capacity or _capacity()
    returns = tuple(0.001 if index % 2 == 0 else -0.0005 for index in range(60))
    raw_curve: list[float] = []
    equity = 1.0
    for value in returns:
        equity *= 1.0 + value
        raw_curve.append(equity)
    scale = total_aum / raw_curve[-1]
    initial_equity = scale
    equity_curve = tuple(value * scale for value in raw_curve)
    timestamps = tuple(
        1_600_000_000_000 + index * 7 * 86_400_000 for index in range(60)
    )
    alpha_returns = {
        "alpha_a": returns,
        "alpha_b": tuple(
            0.0003 if index % 3 == 0 else (-0.0002 if index % 3 == 1 else 0.0001)
            for index in range(60)
        ),
    }
    regime_labels = ("normal",) * 60
    alpha_metadata = (
        {
            "alpha_id": "alpha_a",
            "age_days": 10.0,
            "validation_confidence": 0.8,
            "paper_confidence": 0.8,
            "decay_flag": False,
            "overfit_flag": False,
            "leakage_flag": False,
        },
        {
            "alpha_id": "alpha_b",
            "age_days": 12.0,
            "validation_confidence": 0.8,
            "paper_confidence": 0.8,
            "decay_flag": False,
            "overfit_flag": False,
            "leakage_flag": False,
        },
    )
    full_inputs = DryRunResearchRiskInputs(
        capacity_cny={"BTCUSDT": float(capacity.policy.maximum_notional)},
        daily_volume_cny=float(capacity.policy.adv_notional),
        total_aum=total_aum,
        alpha_returns=alpha_returns,
        regime_labels=regime_labels,
        alpha_metadata=alpha_metadata,
        timestamps=timestamps,
    )
    source_values = {
        "portfolio_returns": returns,
        "initial_equity": initial_equity,
        "equity_curve": equity_curve,
        "timestamps": timestamps,
        "alpha_returns": alpha_returns,
        "regime_labels": regime_labels,
        "alpha_metadata": alpha_metadata,
        "capacity_and_aum": {
            "capacity_cny": dict(full_inputs.capacity_cny),
            "daily_volume_cny": full_inputs.daily_volume_cny,
            "total_aum": full_inputs.total_aum,
        },
    }
    source_records = {
        name: _record(
            f"risk-{name}",
            event_time=(
                DECISION - 1_000 if name == "capacity_and_aum" else timestamps[-1]
            ),
            available_time=DECISION - 500,
            ingestion_time=DECISION - 400,
            value=value,
        )
        for name, value in source_values.items()
    }
    return ResearchRiskEvidence(
        returns=returns,
        initial_equity=initial_equity,
        equity_curve=equity_curve,
        timestamps=timestamps,
        full_inputs=full_inputs,
        config=config or RiskConfig(),
        observed_time=timestamps[-1],
        available_time=DECISION - 300,
        ingestion_time=DECISION - 200,
        source_identity="risk-pit-bundle:sha256:42",
        source_records=source_records,
    )


def _risk_for_capacities(
    capacities: tuple[CapacitySnapshot, ...],
    *,
    total_aum: float,
    config: RiskConfig,
    risk_aum: float | None = None,
) -> ResearchRiskEvidence:
    evidence = _risk(
        total_aum=total_aum,
        capacity=capacities[-1],
        config=config,
    )
    capacity_cny = {
        item.instrument_id: float(item.policy.maximum_notional)
        for item in capacities
    }
    daily_volume_cny = float(
        sum((item.policy.adv_notional for item in capacities), D("0"))
    )
    conservative_aum = total_aum if risk_aum is None else risk_aum
    full_inputs = replace(
        evidence.full_inputs,
        capacity_cny=capacity_cny,
        daily_volume_cny=daily_volume_cny,
        total_aum=conservative_aum,
    )
    source_records = dict(evidence.source_records)
    source_records["capacity_and_aum"] = replace(
        source_records["capacity_and_aum"],
        value={
            "capacity_cny": capacity_cny,
            "daily_volume_cny": daily_volume_cny,
            "total_aum": conservative_aum,
        },
    )
    return replace(
        evidence,
        full_inputs=full_inputs,
        source_records=source_records,
    )


def _execution(
    *,
    timestamp: int = DECISION + 10,
    quantity: str = "0.1",
    capacity: CapacitySnapshot | None = None,
    cost: CostModelSnapshot | None = None,
) -> ExecutionRequest:
    capacity = capacity or _capacity()
    return ExecutionRequest(
        execution_id="fill-btc-1",
        timestamp=timestamp,
        instrument_id="BTCUSDT",
        quantity_delta=D(quantity),
        execution_price=D("100"),
        margin_mode=MarginMode.CROSS,
        isolated_margin=None,
        rule_id="binance-btcusdt-rules-epoch-7",
        capacity=capacity,
        cost_model=cost or _cost(),
        opportunity=ExecutionOpportunitySnapshot(
            instrument_id="BTCUSDT",
            venue_id="BINANCE_USDM",
            coverage_start=DECISION - 10,
            coverage_end=timestamp,
            executable_events=(
                ExecutableVenueEvent(DECISION - 10, D("100")),
                ExecutableVenueEvent(timestamp, D("100")),
            ),
            available_time=timestamp,
            ingestion_time=timestamp + 1,
            source_identity=f"binance-tradeable-sequence:{timestamp}",
        ),
        available_time=timestamp,
        ingestion_time=timestamp + 1,
        source_identity="historical-fill:btc:1",
    )


def _decision(
    *,
    execution: ExecutionRequest | None = None,
    signal: CausalDataRecord | None = None,
    risk: ResearchRiskEvidence | None = None,
) -> DecisionRequest:
    execution = execution or _execution()
    return DecisionRequest(
        decision_id="decision-1",
        timestamp=DECISION,
        signal_inputs=(
            signal
            or _record(
                "alpha-signal-1",
                event_time=DECISION - 20,
                available_time=DECISION - 10,
                ingestion_time=DECISION - 5,
                value={"instrument_id": "BTCUSDT", "score": D("0.25")},
            ),
        ),
        execution=execution,
        capacity_snapshots=(execution.capacity,),
        research_risk=risk or _risk(capacity=execution.capacity),
    )


def _market_events(*, include_funding: bool = True) -> tuple[ObservedLedgerEvent, ...]:
    events = [
        ObservedLedgerEvent(
            event=MarkEvent(DECISION - 10, "BTCUSDT", D("100")),
            available_time=DECISION - 9,
            ingestion_time=DECISION - 8,
            source_identity="binance-mark:predecision",
            rule_id="binance-btcusdt-rules-epoch-7",
        ),
        ObservedLedgerEvent(
            event=MarkEvent(DECISION + 30, "BTCUSDT", D("102")),
            available_time=DECISION + 31,
            ingestion_time=DECISION + 32,
            source_identity="binance-mark:terminal",
            rule_id="binance-btcusdt-rules-epoch-7",
        ),
    ]
    if include_funding:
        events.append(
            ObservedLedgerEvent(
                event=FundingEvent(
                    DECISION + 20,
                    "BTCUSDT",
                    funding_rate=D("0.001"),
                    mark_price=D("101"),
                ),
                available_time=DECISION + 20,
                ingestion_time=DECISION + 21,
                source_identity="binance-funding:settlement-1",
                rule_id="binance-btcusdt-rules-epoch-7",
            )
        )
    return tuple(events)


def _funding_policy() -> FundingPolicySnapshot:
    return FundingPolicySnapshot(
        instrument_id="BTCUSDT",
        venue_id="BINANCE_USDM",
        policy_epoch_id="binance-funding-epoch-9",
        coverage_start=DECISION - 10_000,
        coverage_end=DECISION + 10_000,
        settlement_interval_ms=28_800_000,
        settlement_times=(DECISION + 20,),
        observed_time=DECISION - 1_000,
        available_time=DECISION - 900,
        ingestion_time=DECISION - 800,
        source_identity="binance-funding-info:epoch-9",
    )


def _run(
    *,
    decision: DecisionRequest | None = None,
    decisions: tuple[DecisionRequest, ...] | None = None,
    market_events: tuple[ObservedLedgerEvent, ...] | None = None,
    rules: tuple[HistoricalContractRule, ...] | None = None,
    funding_policies: tuple[FundingPolicySnapshot, ...] | None = None,
    universe: tuple[str, ...] | None = None,
    signal_value: str | None = None,
):
    decision = decision or _decision()
    if signal_value is not None:
        decision = replace(
            decision,
            signal_inputs=(
                replace(
                    decision.signal_inputs[0],
                    value={"instrument_id": "BTCUSDT", "score": D(signal_value)},
                ),
            ),
        )
    return run_authoritative_linear_perpetual_v2(
        release_identity={"git_commit": "f" * 40},
        dataset_identity={"dataset": "binance-archive-v12", "checksum": "a" * 64},
        universe_snapshot=_record(
            "pit-universe",
            event_time=DECISION - 2_000,
            available_time=DECISION - 1_900,
            ingestion_time=DECISION - 1_800,
            value=universe if universe is not None else ("BTCUSDT",),
        ),
        alpha_definition={
            "alpha_id": "causal-test-alpha",
            "expression": "score",
            "position_mapping": {
                "schema": "DIRECT_LINEAR_TARGET_QUANTITY_V1",
                "signal_field": "score",
                "quantity_scale": D("0.4"),
                "max_abs_quantity": D("10"),
                "rounding": "FLOOR_ABS_TO_CONTRACT_STEP",
            },
        },
        initial_collateral=D("1000"),
        contract_rules=rules if rules is not None else (_rule(),),
        funding_policies=(
            funding_policies
            if funding_policies is not None
            else (_funding_policy(),)
        ),
        market_events=market_events if market_events is not None else _market_events(),
        decisions=decisions if decisions is not None else (decision,),
        parameters={"rebalance": "event", "rule_epoch_mode": "single"},
        random_seed=7,
    )


def test_runner_produces_non_authoritative_decimal_perpetual_validation_record() -> None:
    record = _run()

    assert is_authoritative_backtest_record(record) is False
    assert record["authoritative"] is False
    assert record["detail"]["evidence_status"] == "TECHNICAL_VALIDATION_ONLY"
    assert record["detail"]["engine_attestations"] == {
        "causal_available_time": True,
        "strict_next_event_execution": True,
        "decimal_contracts": True,
        "settlement_event_funding": True,
        "historical_venue_rules": True,
        "margin_liquidation_state_machine": True,
        "complete_cashflow_identity": True,
        "deployment_equivalent_risk": False,
        "capacity_is_load_bearing": True,
        "full_research_risk_9_of_9": True,
        "risk_history_ledger_replayed": False,
    }
    payload = record["detail"]["result_hash_payload"]
    assert payload["engine_policy"] == {
        "risk_mark_policy_version": "quantos.perpetual-risk-mark.v1",
        "risk_mark_max_age_ms": RISK_MARK_MAX_AGE_MS,
        "existing_position_price_floor": "LAST_CONFIRMED_EXECUTION_PRICE",
        "risk_equity_policy_version": "quantos.perpetual-risk-equity.v1",
            "risk_equity_denominator": (
                "MIN_MARK_EQUITY_AND_CONFIRMED_PRICE_FLOOR_EQUITY"
            ),
            "risk_history_policy": (
                "CALLER_SUPPLIED_CAUSAL_SERIES_NOT_LEDGER_REPLAYED"
            ),
    }
    assert payload["ending_state"]["positions"]["BTCUSDT"]["quantity"] == "0.1"
    assert payload["reconciliation"]["funding"] == "-0.0101"
    assert payload["reconciliation"]["ending_equity"] == "1000.1299"
    assert payload["reconciliation"]["residual"] == "0"
    context = payload["decision_contexts"][0]
    assert context["pretrade_equity"] == "1000"
    assert context["floor_valued_equity"] == "1000"
    assert context["risk_equity"] == "1000"
    assert context["pretrade_positions"] == {}
    assert context["alpha_target_quantity"] == "0.1"
    assert context["decision_marks"] == {"BTCUSDT": "100"}
    assert context["risk_valuation_prices"] == {"BTCUSDT": "100"}
    assert context["last_confirmed_execution_times"] == {"BTCUSDT": None}
    assert context["last_confirmed_execution_prices"] == {"BTCUSDT": None}
    assert context["target_weights"] == {"BTCUSDT": "0.01"}
    assert context["research_risk"]["gates_run"] == 9
    assert (
        record["detail"]["runner_scope"]["source_authenticity"]
        == "EXTERNAL_SIGNED_AUTHORITY_REQUIRED"
    )
    assert record["detail"]["authority_blockers"] == [
        "EXTERNAL_SIGNED_RAW_ARCHIVE_AUTHORITY_MISSING",
        "RISK_HISTORY_NOT_INDEPENDENTLY_LEDGER_REPLAYED",
    ]


def test_runner_replay_is_independent_of_process_decimal_context() -> None:
    baseline = _run()

    with localcontext() as external:
        external.prec = 6
        external.rounding = ROUND_DOWN
        polluted = _run()
        assert external.prec == 6
        assert external.rounding == ROUND_DOWN

    assert polluted["run_id"] == baseline["run_id"]
    assert polluted["detail"]["result_hash"] == baseline["detail"]["result_hash"]
    assert (
        polluted["detail"]["result_hash_payload"]
        == baseline["detail"]["result_hash_payload"]
    )


def test_decision_cannot_use_data_ingested_after_decision() -> None:
    bad = _record(
        "late-signal",
        event_time=DECISION - 20,
        available_time=DECISION - 10,
        ingestion_time=DECISION + 1,
        value={"instrument_id": "BTCUSDT", "score": D("0.25")},
    )
    with pytest.raises(AuthoritativeBacktestInputError, match="ingested after decision"):
        _run(decision=_decision(signal=bad))


def test_predecision_mark_hidden_until_after_decision_fails_closed() -> None:
    events = list(_market_events())
    events[0] = replace(events[0], ingestion_time=DECISION + 1)
    with pytest.raises(AuthoritativeBacktestInputError, match="market event.*not ingested"):
        _run(market_events=tuple(events))


def test_same_t_execution_is_never_renamed_next_open() -> None:
    with pytest.raises(AuthoritativeBacktestInputError, match="strictly after decision"):
        _run(decision=_decision(execution=_execution(timestamp=DECISION)))


def test_execution_must_be_first_archived_venue_opportunity() -> None:
    execution = _execution()
    skipped = replace(
        execution,
        opportunity=replace(
            execution.opportunity,
            executable_events=(
                ExecutableVenueEvent(DECISION - 10, D("100")),
                ExecutableVenueEvent(DECISION + 5, D("100")),
                ExecutableVenueEvent(execution.timestamp, D("100")),
            ),
        ),
    )
    with pytest.raises(AuthoritativeBacktestInputError, match="first archived venue-executable"):
        _run(decision=_decision(execution=skipped, risk=_risk(capacity=skipped.capacity)))


def test_execution_price_must_match_archived_venue_event() -> None:
    execution = replace(_execution(), execution_price=D("50"))

    with pytest.raises(AuthoritativeBacktestInputError, match="time/price"):
        _run(
            decision=_decision(
                execution=execution,
                risk=_risk(capacity=execution.capacity),
            )
        )


def test_execution_quantity_must_reach_alpha_derived_target() -> None:
    signal = _record(
        "alpha-signal-mismatch",
        event_time=DECISION - 20,
        available_time=DECISION - 10,
        ingestion_time=DECISION - 5,
        value={"instrument_id": "BTCUSDT", "score": D("-0.25")},
    )

    with pytest.raises(AuthoritativeBacktestInputError, match="alpha-derived target"):
        _run(decision=_decision(signal=signal))


def test_missing_scheduled_funding_settlement_fails_closed() -> None:
    with pytest.raises(AuthoritativeBacktestInputError, match="funding schedule mismatch"):
        _run(market_events=_market_events(include_funding=False))


def test_empty_or_interval_incomplete_funding_schedule_cannot_self_attest() -> None:
    policy = _funding_policy()
    with pytest.raises(ValueError, match="non-empty.*settlement schedule"):
        replace(policy, settlement_times=())
    with pytest.raises(ValueError, match="omitted settlement"):
        replace(policy, coverage_end=policy.settlement_times[-1] + policy.settlement_interval_ms)


def test_same_timestamp_funding_batch_precedes_trade_in_runner() -> None:
    execution = _execution(timestamp=DECISION + 20)
    record = _run(
        decision=_decision(
            execution=execution,
            risk=_risk(capacity=execution.capacity),
        )
    )

    events = record["detail"]["result_hash_payload"]["ledger_events"]
    settlement = next(item for item in events if item["event_type"] == "FUNDING_SETTLEMENT")
    assert settlement["pre_settlement_quantity"] == "0"
    assert record["detail"]["result_hash_payload"]["reconciliation"]["funding"] == "0"


def test_missing_or_noncausal_historical_rule_fails_closed() -> None:
    with pytest.raises(AuthoritativeBacktestInputError, match="contract rule"):
        _run(rules=())
    with pytest.raises(AuthoritativeBacktestInputError, match="rule.*ingested after decision"):
        _run(rules=(_rule(ingestion_time=DECISION + 1),))


def test_capacity_is_load_bearing_not_a_display_flag() -> None:
    capacity = _capacity(maximum_too_small=True)
    execution = _execution(capacity=capacity)
    with pytest.raises(AuthoritativeBacktestInputError, match="capacity exceeded"):
        _run(decision=_decision(execution=execution, risk=_risk(capacity=capacity)))


def test_capacity_adv_window_must_precede_consumption_window() -> None:
    with pytest.raises(ValueError, match="ADV observation window"):
        replace(_capacity(), adv_window_end=DECISION + 1)


def test_execution_must_lie_inside_capacity_consumption_window() -> None:
    capacity = _capacity()
    with pytest.raises(ValueError, match="outside capacity consumption window"):
        _execution(
            timestamp=capacity.policy.consumption_window_end,
            capacity=capacity,
        )


def test_external_fake_risk_context_cannot_attest_actual_equity() -> None:
    execution = _execution()
    forged = _risk(total_aum=10_000.0, capacity=execution.capacity)
    with pytest.raises(AuthoritativeBacktestInputError, match="actual ledger equity"):
        _run(decision=_decision(execution=execution, risk=forged))


def test_first_risk_return_is_bound_to_independent_initial_equity() -> None:
    evidence = _risk()
    forged_returns = (0.1, *evidence.returns[1:])
    forged_sources = dict(evidence.source_records)
    forged_sources["portfolio_returns"] = replace(
        forged_sources["portfolio_returns"],
        value=forged_returns,
    )
    forged = replace(
        evidence,
        returns=forged_returns,
        source_records=forged_sources,
    )

    with pytest.raises(AuthoritativeBacktestInputError, match="internally aligned"):
        _run(decision=_decision(risk=forged))


@pytest.mark.parametrize(
    "timestamps",
    (
        tuple(range(-60, 0)),
        tuple(float(value) for value in range(60)),
    ),
)
def test_risk_timestamps_must_be_non_negative_exact_integers(timestamps) -> None:
    with pytest.raises(ValueError, match="non-negative integers"):
        replace(_risk(), timestamps=timestamps)


def test_each_nine_engine_risk_input_is_content_bound_to_its_causal_source() -> None:
    evidence = _risk()
    forged_sources = dict(evidence.source_records)
    forged_sources["regime_labels"] = replace(
        forged_sources["regime_labels"],
        value=("fabricated_calm",) * len(evidence.timestamps),
    )
    with pytest.raises(ValueError, match="risk source regime_labels does not bind"):
        replace(evidence, source_records=forged_sources)


def test_mutating_a_shallow_source_mapping_after_construction_is_rechecked() -> None:
    evidence = _risk()
    decision = _decision(risk=evidence)
    evidence.source_records["regime_labels"] = replace(  # type: ignore[index]
        evidence.source_records["regime_labels"],
        value=("fabricated_calm",) * len(evidence.timestamps),
    )

    with pytest.raises(AuthoritativeBacktestInputError, match="binding changed"):
        _run(decision=decision)


def test_capacity_budget_accumulates_across_decisions_in_same_venue_window() -> None:
    capacity = _capacity(adv_notional="150")  # 10% window budget = 15 USDT.
    aliased_capacity = replace(
        capacity,
        policy=replace(capacity.policy, window_id="same-bounds-different-label"),
    )
    config = RiskConfig(min_effective_number_of_bets=0)
    first_execution = _execution(capacity=capacity)
    first = _decision(
        execution=first_execution,
        risk=_risk(capacity=capacity, config=config),
    )

    second_decision_time = DECISION + 100
    second_execution_time = DECISION + 110
    second_execution = replace(
        _execution(
            timestamp=second_execution_time,
            quantity="-0.1",
            capacity=aliased_capacity,
        ),
        execution_id="fill-btc-2",
        opportunity=ExecutionOpportunitySnapshot(
            instrument_id="BTCUSDT",
            venue_id="BINANCE_USDM",
            coverage_start=DECISION - 10,
            coverage_end=second_execution_time,
            executable_events=(
                ExecutableVenueEvent(DECISION - 10, D("100")),
                ExecutableVenueEvent(first_execution.timestamp, D("100")),
                ExecutableVenueEvent(second_execution_time, D("100")),
            ),
            available_time=second_execution_time,
            ingestion_time=second_execution_time + 1,
            source_identity="binance-tradeable-sequence:second",
        ),
        available_time=second_execution_time,
        ingestion_time=second_execution_time + 1,
        source_identity="historical-fill:btc:2",
    )
    second = DecisionRequest(
        decision_id="decision-2",
        timestamp=second_decision_time,
        signal_inputs=(
            _record(
                "alpha-signal-2",
                event_time=second_decision_time - 20,
                available_time=second_decision_time - 10,
                ingestion_time=second_decision_time - 5,
                value={"instrument_id": "BTCUSDT", "score": D("0")},
            ),
        ),
        execution=second_execution,
        capacity_snapshots=(aliased_capacity,),
        research_risk=_risk(
            total_aum=1000.1299,
            capacity=aliased_capacity,
            config=config,
        ),
    )

    with pytest.raises(AuthoritativeBacktestInputError, match="full fill"):
        _run(decisions=(first, second))


def test_changed_source_cannot_reuse_one_adv_end_for_an_adjacent_period() -> None:
    first = _capacity()
    with pytest.raises(ValueError, match="exactly match"):
        _capacity(
            period_index=CAPACITY_PERIOD_INDEX + 1,
            source_identity="pit-adv:btc:forged-fresh-name",
            adv_window_start=first.adv_window_start,
            adv_window_end=first.adv_window_end,
        )


def test_next_reviewed_period_requires_a_new_aligned_adv_interval() -> None:
    first = _capacity()
    second = _capacity(period_index=CAPACITY_PERIOD_INDEX + 1)

    assert second.policy.consumption_window_start == (
        first.policy.consumption_window_end
    )
    assert second.adv_window_start - first.adv_window_start == CAPACITY_PERIOD_MS
    assert second.adv_window_end - first.adv_window_end == CAPACITY_PERIOD_MS
    assert second.source_identity != first.source_identity


def test_real_nine_engine_rejection_blocks_execution_and_authority() -> None:
    # A 20% single-name target exceeds the default 15% concentration limit.
    execution = _execution(quantity="2")
    signal = _record(
        "alpha-signal-large-target",
        event_time=DECISION - 20,
        available_time=DECISION - 10,
        ingestion_time=DECISION - 5,
        value={"instrument_id": "BTCUSDT", "score": D("5")},
    )
    with pytest.raises(AuthoritativeBacktestInputError, match="nine-engine research risk rejected"):
        _run(
            decision=_decision(
                execution=execution,
                signal=signal,
                risk=_risk(capacity=execution.capacity),
            )
        )


def test_low_causal_mark_cannot_understate_actual_execution_exposure() -> None:
    execution = _execution(quantity="2")
    signal = _record(
        "alpha-signal-large-target-low-mark",
        event_time=DECISION - 20,
        available_time=DECISION - 10,
        ingestion_time=DECISION - 5,
        value={"instrument_id": "BTCUSDT", "score": D("5")},
    )
    events = list(_market_events())
    events[0] = replace(
        events[0],
        event=replace(events[0].event, mark_price=D("1")),
    )

    with pytest.raises(AuthoritativeBacktestInputError, match="nine-engine research risk rejected"):
        _run(
            decision=_decision(
                execution=execution,
                signal=signal,
                risk=_risk(capacity=execution.capacity),
            ),
            market_events=tuple(events),
        )


def _two_instrument_existing_position_case(
    *,
    second_decision_time: int,
    btc_post_fill_mark_time: int | None,
    btc_quantity: str = "1.4",
    btc_score: str = "3.5",
    eth_quantity: str = "0.1",
    eth_score: str = "0.25",
    mark_based_equity: float = 860.56,
    risk_equity: float | None = None,
) -> dict[str, object]:
    config = RiskConfig(min_effective_number_of_bets=0)
    btc_capacity = _capacity()
    btc_rule = replace(
        _rule(),
        effective_until=second_decision_time + 1_000,
    )
    first_execution = _execution(quantity=btc_quantity, capacity=btc_capacity)
    first = _decision(
        execution=first_execution,
        signal=_record(
            "alpha-signal-btc-target",
            event_time=DECISION - 20,
            available_time=DECISION - 10,
            ingestion_time=DECISION - 5,
            value={"instrument_id": "BTCUSDT", "score": D(btc_score)},
        ),
        risk=_risk(capacity=btc_capacity, config=config),
    )

    eth_spec = replace(_spec(), instrument_id="ETHUSDT")
    eth_rule = replace(
        btc_rule,
        rule_id="binance-ethusdt-rules-epoch-7",
        spec=eth_spec,
        source_identity="binance-exchange-info-archive:eth-epoch-7",
    )
    eth_capacity = replace(
        btc_capacity,
        instrument_id="ETHUSDT",
        policy=replace(btc_capacity.policy, window_id="adv-window-eth-42"),
        source_identity="pit-adv:eth:window-42",
    )
    second_execution_time = second_decision_time + 10
    second_execution = replace(
        _execution(timestamp=second_execution_time, quantity=eth_quantity),
        execution_id="fill-eth-1",
        instrument_id="ETHUSDT",
        rule_id=eth_rule.rule_id,
        capacity=eth_capacity,
        opportunity=ExecutionOpportunitySnapshot(
            instrument_id="ETHUSDT",
            venue_id="BINANCE_USDM",
            coverage_start=DECISION - 10,
            coverage_end=second_execution_time,
            executable_events=(
                ExecutableVenueEvent(DECISION - 10, D("100")),
                ExecutableVenueEvent(second_execution_time, D("100")),
            ),
            available_time=second_execution_time,
            ingestion_time=second_execution_time + 1,
            source_identity="binance-tradeable-sequence:eth",
        ),
        available_time=second_execution_time,
        ingestion_time=second_execution_time + 1,
        source_identity="historical-fill:eth:1",
    )
    second = DecisionRequest(
        decision_id="decision-eth-2",
        timestamp=second_decision_time,
        signal_inputs=(
            _record(
                "alpha-signal-eth-target",
                event_time=second_decision_time - 20,
                available_time=second_decision_time - 10,
                ingestion_time=second_decision_time - 5,
                value={"instrument_id": "ETHUSDT", "score": D(eth_score)},
            ),
        ),
        execution=second_execution,
        capacity_snapshots=(btc_capacity, eth_capacity),
        research_risk=_risk_for_capacities(
            (btc_capacity, eth_capacity),
            total_aum=mark_based_equity,
            config=config,
            risk_aum=risk_equity,
        ),
    )

    settlement_time = second_execution_time + 90
    btc_funding_policy = replace(
        _funding_policy(),
        coverage_end=second_decision_time + 1_000,
        settlement_times=(settlement_time,),
    )
    eth_funding_policy = replace(
        btc_funding_policy,
        instrument_id="ETHUSDT",
        policy_epoch_id="binance-eth-funding-epoch-9",
        source_identity="binance-funding-info:eth-epoch-9",
    )
    events = [
        ObservedLedgerEvent(
            event=MarkEvent(DECISION - 10, "BTCUSDT", D("1")),
            available_time=DECISION - 9,
            ingestion_time=DECISION - 8,
            source_identity="binance-mark:btc-before-fill",
            rule_id=btc_rule.rule_id,
        ),
        ObservedLedgerEvent(
            event=MarkEvent(DECISION - 10, "ETHUSDT", D("100")),
            available_time=DECISION - 9,
            ingestion_time=DECISION - 8,
            source_identity="binance-mark:eth-predecision",
            rule_id=eth_rule.rule_id,
        ),
    ]
    if btc_post_fill_mark_time is not None:
        events.append(
            ObservedLedgerEvent(
                event=MarkEvent(btc_post_fill_mark_time, "BTCUSDT", D("1")),
                available_time=btc_post_fill_mark_time,
                ingestion_time=btc_post_fill_mark_time + 1,
                source_identity="binance-mark:btc-after-fill-low",
                rule_id=btc_rule.rule_id,
            )
        )
    events.extend(
        [
            ObservedLedgerEvent(
                event=FundingEvent(
                    settlement_time,
                    "BTCUSDT",
                    funding_rate=D("0"),
                    mark_price=D("100"),
                ),
                available_time=settlement_time,
                ingestion_time=settlement_time + 1,
                source_identity="binance-funding:btc-terminal",
                rule_id=btc_rule.rule_id,
            ),
            ObservedLedgerEvent(
                event=FundingEvent(
                    settlement_time,
                    "ETHUSDT",
                    funding_rate=D("0"),
                    mark_price=D("100"),
                ),
                available_time=settlement_time,
                ingestion_time=settlement_time + 1,
                source_identity="binance-funding:eth-terminal",
                rule_id=eth_rule.rule_id,
            ),
            ObservedLedgerEvent(
                event=MarkEvent(settlement_time + 10, "BTCUSDT", D("100")),
                available_time=settlement_time + 11,
                ingestion_time=settlement_time + 12,
                source_identity="binance-mark:btc-terminal",
                rule_id=btc_rule.rule_id,
            ),
            ObservedLedgerEvent(
                event=MarkEvent(settlement_time + 10, "ETHUSDT", D("100")),
                available_time=settlement_time + 11,
                ingestion_time=settlement_time + 12,
                source_identity="binance-mark:eth-terminal",
                rule_id=eth_rule.rule_id,
            ),
        ]
    )

    return {
        "decisions": (first, second),
        "market_events": tuple(events),
        "rules": (btc_rule, eth_rule),
        "funding_policies": (btc_funding_policy, eth_funding_policy),
        "universe": ("BTCUSDT", "ETHUSDT"),
    }


def test_existing_position_requires_strictly_post_execution_risk_mark() -> None:
    case = _two_instrument_existing_position_case(
        second_decision_time=DECISION + 100,
        btc_post_fill_mark_time=None,
    )

    with pytest.raises(
        AuthoritativeBacktestInputError,
        match="strictly post-execution causal risk mark is missing for BTCUSDT",
    ):
        _run(**case)


def test_existing_position_rejects_mark_older_than_fixed_freshness_sla() -> None:
    case = _two_instrument_existing_position_case(
        second_decision_time=DECISION + RISK_MARK_MAX_AGE_MS + 100,
        btc_post_fill_mark_time=DECISION + 11,
    )

    with pytest.raises(
        AuthoritativeBacktestInputError,
        match="causal risk mark exceeds freshness SLA for BTCUSDT",
    ):
        _run(**case)


def test_existing_position_uses_last_confirmed_execution_price_floor() -> None:
    case = _two_instrument_existing_position_case(
        second_decision_time=DECISION + 100,
        btc_post_fill_mark_time=DECISION + 11,
    )

    # BTC was filled at 100 for 1.4 contracts.  A fresh mark of 1 would report
    # only 0.16% concentration; the confirmed-fill floor exposes 16.27% and the
    # real nine-engine gate rejects it against the fixed 15% single-name limit.
    with pytest.raises(
        AuthoritativeBacktestInputError,
        match="nine-engine research risk rejected",
    ):
        _run(**case)


def test_fresh_low_short_mark_cannot_inflate_risk_equity_denominator() -> None:
    case = _two_instrument_existing_position_case(
        second_decision_time=DECISION + 100,
        btc_post_fill_mark_time=DECISION + 11,
        btc_quantity="-1.4",
        btc_score="-3.5",
        eth_quantity="1.7",
        eth_score="4.25",
        mark_based_equity=1137.76,
        risk_equity=999.16,
    )

    # The fresh BTC mark of 1 inflates marked equity for the short.  Revaluing
    # that existing position at its confirmed fill price of 100 restores the
    # conservative 999.16 denominator, making the new 170 USDT ETH target a
    # 17.01% single-name weight rather than 14.94%.
    with pytest.raises(
        AuthoritativeBacktestInputError,
        match=r"Concentration: .*max_single=0\.170",
    ):
        _run(**case)


def test_every_processed_input_changes_manifest_identity() -> None:
    baseline = _run()
    changed_signal = _run(signal_value="0.2500000000000000000000001")
    execution = _execution(cost=_cost(impact_rate="0.003000000000000000000001"))
    changed_cost = _run(
        decision=_decision(execution=execution, risk=_risk(capacity=execution.capacity))
    )

    assert len({baseline["run_id"], changed_signal["run_id"], changed_cost["run_id"]}) == 3


def test_result_hash_makes_pnl_tamper_non_authoritative() -> None:
    record = _run()
    assert record["detail"]["result_hash"] == exact_identity_hash(
        record["detail"]["result_hash_payload"],
        name="authoritative_backtest_result",
    )
    tampered = copy.deepcopy(record)
    tampered["detail"]["result_hash_payload"]["reconciliation"]["ending_equity"] = "999999"

    assert tampered["detail"]["result_hash"] != exact_identity_hash(
        tampered["detail"]["result_hash_payload"],
        name="authoritative_backtest_result",
    )
    assert is_authoritative_backtest_record(tampered) is False


def test_technical_record_cannot_be_upgraded_by_flipping_authority_flags() -> None:
    record = _run()
    record["detail"]["evidence_status"] = "AUTHORITATIVE"
    record["detail"]["engine_attestations"]["deployment_equivalent_risk"] = True

    # The immutable output uses a distinct technical-result schema.  A caller
    # cannot turn it into authority evidence by changing the two display/gate
    # flags while retaining the same manifest and result hash.
    assert (
        record["detail"]["result_hash_payload"]["schema_version"]
        == "TECHNICAL_PERPETUAL_VALIDATION_V1"
    )
    assert is_authoritative_backtest_record(record) is False
