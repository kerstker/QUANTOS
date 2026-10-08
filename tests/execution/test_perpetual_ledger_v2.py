"""Contract tests for the isolated v2 linear-USDT perpetual event ledger.

The v1 paper/dry-run contracts intentionally remain untouched.  These tests pin
the exchange economics that were absent from that path before any authority is
allowed to consume the new engine.
"""

from dataclasses import replace
from decimal import (
    Decimal,
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_UP,
    getcontext,
    localcontext,
)

import pytest

from services.execution.perpetual_ledger_v2 import (
    BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,
    CapacityAction,
    CapacityPolicy,
    CashQuantumViolation,
    ContractSpec,
    DECIMAL_CONTEXT_VERSION,
    FundingEvent,
    LinearPerpetualLedger,
    LiquidationBackstop,
    MarginBracket,
    MarginConfigurationError,
    MarginMode,
    MarkEvent,
    InsufficientLiquidationBackstop,
    MissingLiquidationBackstop,
    QuantityRuleViolation,
    TradeEvent,
    UnsupportedContract,
    EventOrderViolation,
    InsufficientMargin,
    ledger_decimal_context,
)


D = Decimal


def _brackets() -> tuple[MarginBracket, ...]:
    return (
        MarginBracket(
            notional_floor=D("0"),
            notional_cap=D("1000"),
            initial_margin_rate=D("0.10"),
            maintenance_margin_rate=D("0.05"),
            maintenance_amount=D("0"),
        ),
        MarginBracket(
            notional_floor=D("1000"),
            notional_cap=None,
            initial_margin_rate=D("0.20"),
            maintenance_margin_rate=D("0.10"),
            maintenance_amount=D("50"),
        ),
    )


def _spec(
    instrument_id: str = "BTCUSDT",
    *,
    liquidation_fee_rate: Decimal = D("0.01"),
    brackets: tuple[MarginBracket, ...] | None = None,
) -> ContractSpec:
    return ContractSpec(
        instrument_id=instrument_id,
        contract_type="LINEAR_USDT_PERPETUAL",
        margin_asset="USDT",
        contract_multiplier=D("1"),
        quantity_step=D("0.001"),
        min_quantity=D("0.001"),
        min_notional=D("5"),
        liquidation_fee_rate=liquidation_fee_rate,
        settlement_quantum=D("1e-24"),
        margin_brackets=brackets or _brackets(),
    )


def _capacity(
    *,
    adv: Decimal = D("1000000"),
    rate: Decimal = D("0.10"),
    action: CapacityAction = CapacityAction.REJECT,
    window_id: str = "window-1",
    period_rule_id: str = BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,
    period_index: int = 0,
) -> CapacityPolicy:
    return CapacityPolicy(
        venue_id="BINANCE_USDM",
        window_id=window_id,
        period_rule_id=period_rule_id,
        period_index=period_index,
        adv_notional=adv,
        max_participation_rate=rate,
        action=action,
    )


def _trade(
    timestamp: int,
    quantity_delta: str,
    price: str,
    *,
    instrument_id: str = "BTCUSDT",
    margin_mode: MarginMode = MarginMode.CROSS,
    isolated_margin: str | None = None,
    fee: str = "0",
    spread: str = "0",
    impact: str = "0",
    capacity: CapacityPolicy | None = None,
) -> TradeEvent:
    return TradeEvent(
        timestamp=timestamp,
        instrument_id=instrument_id,
        quantity_delta=D(quantity_delta),
        execution_price=D(price),
        margin_mode=margin_mode,
        isolated_margin=None if isolated_margin is None else D(isolated_margin),
        fee=D(fee),
        spread_cost=D(spread),
        impact_cost=D(impact),
        capacity=capacity or _capacity(),
    )


ZERO_BACKSTOP = LiquidationBackstop(
    insurance_adjustment=D("0"),
    adl_adjustment=D("0"),
)


def test_decimal_quantity_preserves_point_001_btc_and_canonical_strings() -> None:
    spec = _spec()
    assert spec.validate_quantity(D("0.001"), D("100000")) == D("0.001")

    ledger = LinearPerpetualLedger((spec,), initial_collateral=D("1000"))
    result = ledger.apply_event(_trade(1, "0.001", "100000"))

    assert result.status == "FILLED"
    assert result.filled_quantity == D("0.001")
    assert ledger.position("BTCUSDT").quantity == D("0.001")
    assert result.as_canonical_dict()["filled_quantity"] == "0.001"
    assert ledger.state_as_canonical_dict()["positions"]["BTCUSDT"]["quantity"] == "0.001"


def test_settlement_quantum_and_unit_count_are_reviewed_bounds() -> None:
    with pytest.raises(MarginConfigurationError, match="between 1e-24 and 1"):
        replace(_spec(), settlement_quantum=D("1e-60"))

    with pytest.raises(CashQuantumViolation, match="settlement-unit bound"):
        LinearPerpetualLedger((_spec(),), initial_collateral=D("1e13"))


def test_tiny_close_fee_cannot_be_masked_by_realized_profit() -> None:
    ledger = LinearPerpetualLedger(
        (_spec(liquidation_fee_rate=D("0")),), initial_collateral=D("1000")
    )
    ledger.apply_event(_trade(1, "1", "5"))
    before = ledger.state_as_canonical_dict()

    with pytest.raises(CashQuantumViolation, match="settlement grid"):
        ledger.apply_event(_trade(2, "-1", "6", fee="1e-60"))

    assert ledger.state_as_canonical_dict() == before
    assert ledger.reconciliation_snapshot().residual == D("0")


@pytest.mark.parametrize("quantity", ["0.0005", "0.0015"])
def test_quantity_below_minimum_or_off_step_is_rejected(quantity: str) -> None:
    ledger = LinearPerpetualLedger((_spec(),), initial_collateral=D("1000"))
    with pytest.raises(QuantityRuleViolation):
        ledger.apply_event(_trade(1, quantity, "100000"))
    assert ledger.position("BTCUSDT") is None


def test_same_timestamp_funding_precedes_trade_so_new_position_gets_no_old_funding() -> None:
    ledger = LinearPerpetualLedger((_spec(),), initial_collateral=D("1000"))
    # Deliberately pass the trade first.  The event engine must sort settlement
    # before execution at an identical venue timestamp.
    ledger.process_events(
        (
            _trade(100, "0.001", "100000"),
            FundingEvent(
                timestamp=100,
                instrument_id="BTCUSDT",
                funding_rate=D("0.001"),
                mark_price=D("100000"),
            ),
        )
    )
    assert ledger.reconciliation_snapshot().funding == D("0")

    ledger.apply_event(
        FundingEvent(
            timestamp=200,
            instrument_id="BTCUSDT",
            funding_rate=D("0.001"),
            mark_price=D("100000"),
        )
    )
    assert ledger.reconciliation_snapshot().funding == D("-0.1")


def test_capacity_policy_rejects_or_partially_fills_instead_of_only_flagging() -> None:
    spec = _spec(liquidation_fee_rate=D("0"))
    rejected = LinearPerpetualLedger((spec,), initial_collateral=D("1000"))
    result = rejected.apply_event(
        _trade(
            1,
            "2",
            "100",
            capacity=_capacity(adv=D("1000"), rate=D("0.10"), action=CapacityAction.REJECT),
        )
    )
    assert result.status == "REJECTED"
    assert result.reason == "CAPACITY_EXCEEDED"
    assert rejected.position("BTCUSDT") is None

    partial = LinearPerpetualLedger((spec,), initial_collateral=D("1000"))
    result = partial.apply_event(
        _trade(
            1,
            "2",
            "100",
            capacity=_capacity(adv=D("1000"), rate=D("0.10"), action=CapacityAction.PARTIAL),
        )
    )
    assert result.status == "PARTIALLY_FILLED"
    assert result.filled_quantity == D("1")
    assert partial.position("BTCUSDT").quantity == D("1")


def test_partial_fill_scales_requested_cost_cashflows_to_actual_fill() -> None:
    ledger = LinearPerpetualLedger(
        (_spec(liquidation_fee_rate=D("0")),), initial_collateral=D("1000")
    )
    result = ledger.apply_event(
        _trade(
            1,
            "2",
            "100",
            fee="2",
            spread="4",
            impact="6",
            capacity=_capacity(
                adv=D("1000"), rate=D("0.10"), action=CapacityAction.PARTIAL
            ),
        )
    )

    assert result.filled_quantity == D("1")
    snapshot = ledger.reconciliation_snapshot()
    assert snapshot.fee == D("1")
    assert snapshot.spread == D("2")
    assert snapshot.impact == D("3")
    assert snapshot.residual == D("0")


def test_trade_does_not_replace_existing_venue_mark_with_execution_price() -> None:
    spec = _spec(
        liquidation_fee_rate=D("0"),
        brackets=(MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("0")),),
    )
    ledger = LinearPerpetualLedger((spec,), initial_collateral=D("1000"))
    ledger.apply_event(_trade(1, "1", "100"))
    ledger.apply_event(MarkEvent(2, "BTCUSDT", D("110")))
    ledger.apply_event(_trade(3, "1", "105"))

    position = ledger.position("BTCUSDT")
    assert position.mark_price == D("110")
    assert position.average_entry_price == D("102.5")
    assert ledger.account_equity() == D("1015.0")


def test_flat_account_retains_pretrade_mark_for_new_position_margin() -> None:
    spec = _spec(
        liquidation_fee_rate=D("0"),
        brackets=(MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("0")),),
    )
    ledger = LinearPerpetualLedger((spec,), initial_collateral=D("1000"))

    # The engine sorts the explicit venue mark before the same-timestamp trade.
    # At mark 94 this proposed 100-contract long has equity 400 and requires
    # 940 initial margin.  Substituting execution 100 as its mark would
    # incorrectly let it pass with equity == required margin == 1000.
    with pytest.raises(InsufficientMargin):
        ledger.process_events(
            (
                _trade(2, "100", "100"),
                MarkEvent(2, "BTCUSDT", D("94")),
            )
        )

    assert ledger.position("BTCUSDT") is None
    assert ledger.state_as_canonical_dict()["mark_prices"] == {"BTCUSDT": "94"}
    assert ledger.account_equity() == D("1000")


def test_same_timestamp_cross_instrument_funding_is_atomic_and_order_independent() -> None:
    brackets = (MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("0")),)
    events = (
        FundingEvent(2, "A", D("0.10"), D("100")),
        FundingEvent(2, "B", D("-0.10"), D("100")),
    )

    states = []
    for ordered_events in (events, tuple(reversed(events))):
        ledger = LinearPerpetualLedger(
            (_spec("A", liquidation_fee_rate=D("0"), brackets=brackets),
             _spec("B", liquidation_fee_rate=D("0"), brackets=brackets)),
            initial_collateral=D("1000"),
        )
        ledger.apply_event(_trade(1, "50", "100", instrument_id="A"))
        ledger.apply_event(_trade(1, "50", "100", instrument_id="B"))
        ledger.process_events(ordered_events)
        assert ledger.position("A") is not None
        assert ledger.position("B") is not None
        assert ledger.liquidations == ()
        states.append(ledger.state_as_canonical_dict())

    assert states[0] == states[1]


def test_same_timestamp_cross_instrument_marks_are_atomic_and_order_independent() -> None:
    brackets = (MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("0")),)
    events = (
        MarkEvent(2, "A", D("88")),
        MarkEvent(2, "B", D("112")),
    )

    states = []
    for ordered_events in (events, tuple(reversed(events))):
        ledger = LinearPerpetualLedger(
            (_spec("A", liquidation_fee_rate=D("0"), brackets=brackets),
             _spec("B", liquidation_fee_rate=D("0"), brackets=brackets)),
            initial_collateral=D("1000"),
        )
        ledger.apply_event(_trade(1, "50", "100", instrument_id="A"))
        ledger.apply_event(_trade(1, "50", "100", instrument_id="B"))
        ledger.process_events(ordered_events)
        assert ledger.position("A") is not None
        assert ledger.position("B") is not None
        assert ledger.liquidations == ()
        states.append(ledger.state_as_canonical_dict())

    assert states[0] == states[1]


def test_duplicate_same_timestamp_funding_fails_closed_and_rolls_back_phase() -> None:
    ledger = LinearPerpetualLedger((_spec(),), initial_collateral=D("1000"))
    ledger.apply_event(_trade(1, "0.01", "100000"))
    before = ledger.state_as_canonical_dict()

    with pytest.raises(EventOrderViolation, match="duplicate"):
        ledger.process_events(
            (
                FundingEvent(2, "BTCUSDT", D("0.001"), D("100000")),
                FundingEvent(2, "BTCUSDT", D("0.001"), D("100000")),
            )
        )

    assert ledger.state_as_canonical_dict() == before


def test_closed_market_phase_rejects_split_same_timestamp_calls() -> None:
    ledger = LinearPerpetualLedger((_spec(),), initial_collateral=D("1000"))
    ledger.apply_event(_trade(1, "0.01", "100000"))
    ledger.apply_event(FundingEvent(2, "BTCUSDT", D("0.001"), D("100000")))
    before = ledger.state_as_canonical_dict()

    with pytest.raises(EventOrderViolation, match="already closed"):
        ledger.process_events(
            (FundingEvent(2, "BTCUSDT", D("0.001"), D("100000")),)
        )

    assert ledger.state_as_canonical_dict() == before


def test_capacity_budget_accumulates_across_orders_in_same_venue_window() -> None:
    ledger = LinearPerpetualLedger(
        (_spec(liquidation_fee_rate=D("0")),), initial_collateral=D("1000")
    )
    capacity = _capacity(adv=D("1000"), rate=D("0.10"))

    first = ledger.apply_event(_trade(1, "1", "100", capacity=capacity))
    second = ledger.apply_event(_trade(2, "1", "100", capacity=capacity))

    assert first.status == "FILLED"
    assert second.status == "REJECTED"
    assert second.reason == "CAPACITY_EXCEEDED"
    assert ledger.position("BTCUSDT").quantity == D("1")
    assert ledger.state_as_canonical_dict()["capacity_consumed"] == {
        "BINANCE_USDM|BTCUSDT|BINANCE_USDM.UTC_DAY.v1|0": "100"
    }


def test_new_capacity_window_has_an_independent_budget() -> None:
    ledger = LinearPerpetualLedger(
        (_spec(liquidation_fee_rate=D("0")),), initial_collateral=D("1000")
    )
    ledger.apply_event(
        _trade(
            1,
            "1",
            "100",
            capacity=_capacity(
                adv=D("1000"),
                rate=D("0.10"),
                period_index=0,
            ),
        )
    )
    result = ledger.apply_event(
        _trade(
            86_400_000,
            "1",
            "100",
            capacity=_capacity(
                adv=D("1000"),
                rate=D("0.10"),
                window_id="window-2",
                period_index=1,
            ),
        )
    )

    assert result.status == "FILLED"
    assert ledger.position("BTCUSDT").quantity == D("2")


def test_capacity_window_alias_cannot_reset_consumed_budget() -> None:
    ledger = LinearPerpetualLedger(
        (_spec(liquidation_fee_rate=D("0")),), initial_collateral=D("1000")
    )
    first = _capacity(
        adv=D("1000"), rate=D("0.10"), window_id="venue-window-original"
    )
    alias = _capacity(
        adv=D("1000"), rate=D("0.10"), window_id="venue-window-alias"
    )

    assert ledger.apply_event(_trade(1, "1", "100", capacity=first)).status == "FILLED"
    rejected = ledger.apply_event(_trade(2, "1", "100", capacity=alias))

    assert rejected.status == "REJECTED"
    assert rejected.reason == "CAPACITY_EXCEEDED"
    assert ledger.state_as_canonical_dict()["capacity_consumed"] == {
        "BINANCE_USDM|BTCUSDT|BINANCE_USDM.UTC_DAY.v1|0": "100"
    }


def test_capacity_period_geometry_cannot_be_caller_sliced() -> None:
    policy = _capacity()
    assert policy.consumption_window_start == 0
    assert policy.consumption_window_end == 86_400_000

    with pytest.raises(MarginConfigurationError, match="reviewed registry"):
        _capacity(period_rule_id="CALLER_DEFINED.TEN_MILLISECONDS.v1")


def test_trade_must_lie_inside_capacity_consumption_window() -> None:
    with pytest.raises(ValueError, match="outside capacity consumption window"):
        _trade(
            86_400_000,
            "1",
            "100",
            capacity=_capacity(period_index=0),
        )


def test_margin_bracket_selects_tier_and_applies_cumulative_maintenance_amount() -> None:
    spec = _spec()
    first = spec.margin_requirement(D("999"))
    second = spec.margin_requirement(D("2000"))

    assert first.initial_margin == D("99.90")
    assert first.maintenance_margin == D("49.95")
    assert second.initial_margin == D("400.00")
    assert second.maintenance_margin == D("150.00")
    assert second.bracket.notional_floor == D("1000")


@pytest.mark.parametrize(
    ("brackets", "message"),
    (
        (
            (MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("1")),),
            "first margin bracket maintenance_amount",
        ),
        (
            (
                MarginBracket(D("0"), D("1000"), D("0.20"), D("0.05")),
                MarginBracket(D("1000"), None, D("0.10"), D("0.05")),
            ),
            "initial margin rates cannot decrease",
        ),
        (
            (
                MarginBracket(D("0"), D("1000"), D("0.20"), D("0.10")),
                MarginBracket(D("1000"), None, D("0.20"), D("0.05")),
            ),
            "maintenance margin rates cannot decrease",
        ),
        (
            (
                MarginBracket(D("0"), D("1000"), D("0.10"), D("0.05")),
                MarginBracket(D("1000"), None, D("0.20"), D("0.10"), D("0")),
            ),
            "maintenance margin must be continuous",
        ),
    ),
)
def test_malformed_margin_tiers_fail_closed_at_contract_construction(
    brackets: tuple[MarginBracket, ...], message: str
) -> None:
    with pytest.raises(MarginConfigurationError, match=message):
        _spec(brackets=brackets)


def _decimal_context_signature() -> tuple[object, ...]:
    context = getcontext()
    return (
        context.prec,
        context.rounding,
        context.Emin,
        context.Emax,
        context.capitals,
        context.clamp,
        dict(context.flags),
        dict(context.traps),
    )


def _replay_under_external_decimal_context(
    *, precision: int, rounding: str
) -> tuple[dict[str, object], Decimal]:
    with localcontext() as context:
        context.prec = precision
        context.rounding = rounding
        before = _decimal_context_signature()

        ledger = LinearPerpetualLedger((_spec(),), initial_collateral=D("1000"))
        ledger.apply_event(
            _trade(
                1,
                "1.111",
                "33.333333",
                fee="0.123456",
                spread="0.234567",
                impact="0.345678",
            )
        )
        ledger.apply_event(MarkEvent(2, "BTCUSDT", D("37.777777")))
        ledger.apply_event(
            FundingEvent(3, "BTCUSDT", D("0.001234567"), D("37.777777"))
        )
        state = ledger.state_as_canonical_dict()
        residual = ledger.reconciliation_snapshot().residual

        assert _decimal_context_signature() == before
        return state, residual


def test_external_decimal_context_cannot_change_replay_or_conservation() -> None:
    low_precision = _replay_under_external_decimal_context(
        precision=6, rounding=ROUND_UP
    )
    high_precision = _replay_under_external_decimal_context(
        precision=40, rounding=ROUND_DOWN
    )

    assert low_precision == high_precision
    state, residual = low_precision
    assert state["decimal_context"] == DECIMAL_CONTEXT_VERSION
    assert residual == D("0")


def test_public_decimal_scope_binds_v1_parameters_without_exposing_context() -> None:
    before = _decimal_context_signature()

    with ledger_decimal_context() as exposed_context:
        context = getcontext()
        assert exposed_context is None
        assert DECIMAL_CONTEXT_VERSION == "quantos.linear-perpetual-decimal-context.v1"
        assert context.prec == 50
        assert context.rounding == ROUND_HALF_EVEN
        assert context.Emin == -999_999
        assert context.Emax == 999_999
        assert context.clamp == 0

    assert _decimal_context_signature() == before


def test_10x_crash_liquidates_at_mark_before_account_equity_turns_negative() -> None:
    spec = _spec(brackets=(
        MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("0")),
    ))
    ledger = LinearPerpetualLedger((spec,), initial_collateral=D("1000"))
    ledger.apply_event(_trade(1, "100", "100"))

    with pytest.raises(MissingLiquidationBackstop):
        ledger.apply_event(MarkEvent(2, "BTCUSDT", D("94")))
    # Fail-closed means the triggering observation did not partially mutate state.
    assert ledger.position("BTCUSDT").mark_price == D("100")

    ledger.apply_event(
        MarkEvent(2, "BTCUSDT", D("94"), liquidation_backstop=ZERO_BACKSTOP)
    )
    assert ledger.position("BTCUSDT") is None
    assert ledger.account_equity() == D("306")
    assert ledger.account_equity() > 0
    snap = ledger.reconciliation_snapshot()
    assert snap.price_pnl == D("-600")
    assert snap.liquidation_fee == D("94")
    assert snap.residual == D("0")


def test_funding_cashflow_can_trigger_liquidation_of_pre_settlement_position() -> None:
    spec = _spec(brackets=(
        MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("0")),
    ))
    ledger = LinearPerpetualLedger((spec,), initial_collateral=D("1000"))
    ledger.apply_event(_trade(1, "100", "100"))
    ledger.apply_event(
        FundingEvent(
            timestamp=2,
            instrument_id="BTCUSDT",
            funding_rate=D("0.06"),
            mark_price=D("100"),
            liquidation_backstop=ZERO_BACKSTOP,
        )
    )

    assert ledger.position("BTCUSDT") is None
    snap = ledger.reconciliation_snapshot()
    assert snap.funding == D("-600")
    assert snap.liquidation_fee == D("100")
    assert snap.ending_equity == D("300")
    assert snap.residual == D("0")


def test_cross_collateral_can_absorb_loss_that_liquidates_same_isolated_position() -> None:
    spec = _spec(
        liquidation_fee_rate=D("0"),
        brackets=(MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("0")),),
    )
    cross = LinearPerpetualLedger((spec,), initial_collateral=D("1000"))
    cross.apply_event(_trade(1, "5", "100", margin_mode=MarginMode.CROSS))
    cross.apply_event(
        MarkEvent(2, "BTCUSDT", D("80"), liquidation_backstop=ZERO_BACKSTOP)
    )
    assert cross.position("BTCUSDT") is not None
    assert cross.account_equity() == D("900")

    isolated = LinearPerpetualLedger((spec,), initial_collateral=D("1000"))
    isolated.apply_event(
        _trade(1, "5", "100", margin_mode=MarginMode.ISOLATED, isolated_margin="100")
    )
    isolated.apply_event(
        MarkEvent(2, "BTCUSDT", D("80"), liquidation_backstop=ZERO_BACKSTOP)
    )
    assert isolated.position("BTCUSDT") is None
    assert isolated.account_equity() == D("900")
    assert len(isolated.liquidations) == 1


def test_isolated_margin_allocation_cannot_exceed_available_wallet() -> None:
    spec = _spec(
        liquidation_fee_rate=D("0"),
        brackets=(MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("0")),),
    )
    ledger = LinearPerpetualLedger((spec,), initial_collateral=D("1000"))

    with pytest.raises(InsufficientMargin, match="available wallet"):
        ledger.apply_event(
            _trade(
                1,
                "5",
                "100",
                margin_mode=MarginMode.ISOLATED,
                isolated_margin="1001",
            )
        )

    assert ledger.position("BTCUSDT") is None
    assert ledger.account_equity() == D("1000")


@pytest.mark.parametrize(
    ("stale_mark", "quantity", "execution_price"),
    (
        ("1", "-100", "100"),
        ("100", "100", "1"),
    ),
)
def test_stale_flat_mark_cannot_fund_new_exposure_at_a_distant_fill(
    stale_mark: str, quantity: str, execution_price: str
) -> None:
    """A favorable stale mark cannot create collateral during the fill itself.

    The short/low-mark and long/high-mark mirrors both used to clear because the
    ledger valued the new position solely at the retained flat-account mark.
    """
    spec = _spec(
        liquidation_fee_rate=D("0"),
        brackets=(
            MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("0")),
        ),
    )
    ledger = LinearPerpetualLedger((spec,), initial_collateral=D("100"))
    ledger.apply_event(MarkEvent(1, "BTCUSDT", D(stale_mark)))
    before = ledger.state_as_canonical_dict()

    with pytest.raises(InsufficientMargin, match="cross initial margin"):
        ledger.apply_event(_trade(2, quantity, execution_price))

    assert ledger.state_as_canonical_dict() == before
    assert ledger.position("BTCUSDT") is None


def test_isolated_full_close_cannot_charge_bankruptcy_to_main_wallet() -> None:
    spec = _spec(
        liquidation_fee_rate=D("0"),
        brackets=(MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("0")),),
    )
    ledger = LinearPerpetualLedger((spec,), initial_collateral=D("1000"))
    ledger.apply_event(
        _trade(1, "5", "100", margin_mode=MarginMode.ISOLATED, isolated_margin="100")
    )
    before = ledger.state_as_canonical_dict()

    with pytest.raises(MissingLiquidationBackstop, match="bankruptcy deficit"):
        ledger.apply_event(
            _trade(2, "-5", "50", margin_mode=MarginMode.ISOLATED)
        )

    assert ledger.state_as_canonical_dict() == before


def test_isolated_bankruptcy_deficit_cannot_consume_unrelated_wallet_balance() -> None:
    spec = _spec(
        liquidation_fee_rate=D("0"),
        brackets=(MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("0")),),
    )
    ledger = LinearPerpetualLedger((spec,), initial_collateral=D("1000"))
    ledger.apply_event(
        _trade(1, "5", "100", margin_mode=MarginMode.ISOLATED, isolated_margin="100")
    )

    # The isolated sub-account is 150 USDT underwater, while 900 USDT remains
    # outside it.  Isolation must not silently turn that unrelated wallet into
    # the liquidation backstop.
    with pytest.raises(InsufficientLiquidationBackstop):
        ledger.apply_event(
            MarkEvent(2, "BTCUSDT", D("50"), liquidation_backstop=ZERO_BACKSTOP)
        )
    assert ledger.position("BTCUSDT").mark_price == D("100")

    ledger.apply_event(
        MarkEvent(
            2,
            "BTCUSDT",
            D("50"),
            liquidation_backstop=LiquidationBackstop(
                insurance_adjustment=D("150"),
                adl_adjustment=D("0"),
            ),
        )
    )
    assert ledger.position("BTCUSDT") is None
    assert ledger.account_equity() == D("900")
    assert ledger.reconciliation_snapshot().residual == D("0")


@pytest.mark.parametrize("reverse_input_order", (False, True))
@pytest.mark.parametrize(
    ("isolated_instrument", "cross_instrument"),
    (("A", "B"), ("B", "A")),
)
def test_released_isolated_surplus_offsets_same_batch_cross_bankruptcy(
    reverse_input_order: bool,
    isolated_instrument: str,
    cross_instrument: str,
) -> None:
    brackets = (MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("0")),)
    specs = (
        _spec("A", liquidation_fee_rate=D("0"), brackets=brackets),
        _spec("B", liquidation_fee_rate=D("0"), brackets=brackets),
    )
    ledger = LinearPerpetualLedger(specs, initial_collateral=D("1000"))
    ledger.apply_event(
        _trade(
            1,
            "5",
            "100",
            instrument_id=isolated_instrument,
            margin_mode=MarginMode.ISOLATED,
            isolated_margin="100",
        )
    )
    ledger.apply_event(_trade(1, "90", "100", instrument_id=cross_instrument))
    before = ledger.state_as_canonical_dict()

    surplus = LiquidationBackstop(D("90"), D("0"))
    surplus_events = (
        MarkEvent(
            2, isolated_instrument, D("82"), liquidation_backstop=surplus
        ),
        MarkEvent(2, cross_instrument, D("89"), liquidation_backstop=surplus),
    )
    if reverse_input_order:
        surplus_events = tuple(reversed(surplus_events))
    with pytest.raises(InsufficientLiquidationBackstop, match="surplus injection"):
        ledger.process_events(surplus_events)
    assert ledger.state_as_canonical_dict() == before

    exact = LiquidationBackstop(D("80"), D("0"))
    exact_events = (
        MarkEvent(2, isolated_instrument, D("82"), liquidation_backstop=exact),
        MarkEvent(2, cross_instrument, D("89"), liquidation_backstop=exact),
    )
    if reverse_input_order:
        exact_events = tuple(reversed(exact_events))
    ledger.process_events(exact_events)
    assert ledger.position(isolated_instrument) is None
    assert ledger.position(cross_instrument) is None
    assert ledger.account_equity() == D("0")
    assert ledger.reconciliation_snapshot().residual == D("0")
    records = {record.instrument_id: record for record in ledger.liquidations}
    assert records[isolated_instrument].bankruptcy_deficit_allocation == D("0")
    assert records[isolated_instrument].insurance_adjustment == D("0")
    assert records[isolated_instrument].adl_adjustment == D("0")
    assert records[isolated_instrument].backstop_allocation_basis == (
        "NO_BANKRUPTCY_DEFICIT_ALLOCATION_V1"
    )
    assert records[cross_instrument].bankruptcy_deficit_allocation == D("80")
    assert records[cross_instrument].insurance_adjustment == D("80")
    assert records[cross_instrument].adl_adjustment == D("0")
    assert records[cross_instrument].backstop_allocation_basis == (
        "CROSS_ACCOUNT_DEFICIT_PRO_RATA_NEGATIVE_CASHFLOW_V1"
    )
    assert sum(
        (record.insurance_adjustment for record in ledger.liquidations), D("0")
    ) == D("80")
    assert sum(
        (record.adl_adjustment for record in ledger.liquidations), D("0")
    ) == D("0")


def test_mixed_unequal_liquidation_deficits_are_order_invariant_and_conserved() -> None:
    brackets = (MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("0")),)
    specs = (
        _spec("A", liquidation_fee_rate=D("0"), brackets=brackets),
        _spec("B", liquidation_fee_rate=D("0"), brackets=brackets),
    )

    def replay(*, reverse: bool) -> dict[str, object]:
        ledger = LinearPerpetualLedger(specs, initial_collateral=D("1000"))
        ledger.apply_event(
            _trade(
                1,
                "5",
                "100",
                instrument_id="A",
                margin_mode=MarginMode.ISOLATED,
                isolated_margin="100",
            )
        )
        ledger.apply_event(_trade(1, "90", "100", instrument_id="B"))
        backstop = LiquidationBackstop(D("144"), D("96"))
        events = (
            MarkEvent(2, "A", D("50"), liquidation_backstop=backstop),
            MarkEvent(2, "B", D("89"), liquidation_backstop=backstop),
        )
        ledger.process_events(tuple(reversed(events)) if reverse else events)
        return ledger.state_as_canonical_dict()

    forward = replay(reverse=False)
    assert replay(reverse=True) == forward
    records = {item["instrument_id"]: item for item in forward["liquidations"]}
    assert records["A"]["bankruptcy_deficit_allocation"] == "150"
    assert records["A"]["insurance_adjustment"] == "90"
    assert records["A"]["adl_adjustment"] == "60"
    assert records["B"]["bankruptcy_deficit_allocation"] == "90"
    assert records["B"]["insurance_adjustment"] == "54"
    assert records["B"]["adl_adjustment"] == "36"
    assert forward["cashflow_identity"]["residual"] == "0"


def test_cross_bankruptcy_backstop_is_deterministically_pro_rated() -> None:
    brackets = (MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("0")),)
    specs = (
        _spec("A", liquidation_fee_rate=D("0"), brackets=brackets),
        _spec("B", liquidation_fee_rate=D("0"), brackets=brackets),
    )
    ledger = LinearPerpetualLedger(specs, initial_collateral=D("1000"))
    ledger.apply_event(_trade(1, "50", "100", instrument_id="A"))
    ledger.apply_event(_trade(1, "50", "100", instrument_id="B"))
    backstop = LiquidationBackstop(D("600"), D("400"))

    ledger.process_events(
        (
            MarkEvent(2, "B", D("80"), liquidation_backstop=backstop),
            MarkEvent(2, "A", D("80"), liquidation_backstop=backstop),
        )
    )

    records = {record.instrument_id: record for record in ledger.liquidations}
    for instrument_id in ("A", "B"):
        record = records[instrument_id]
        assert record.bankruptcy_deficit_allocation == D("500")
        assert record.insurance_adjustment == D("300")
        assert record.adl_adjustment == D("200")
        assert record.backstop_allocation_basis == (
            "CROSS_ACCOUNT_DEFICIT_PRO_RATA_NEGATIVE_CASHFLOW_V1"
        )
    assert ledger.account_equity() == D("0")
    assert ledger.reconciliation_snapshot().residual == D("0")


def test_liquidation_uses_mark_price_not_observed_trade_price() -> None:
    spec = _spec(brackets=(
        MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("0")),
    ))
    ledger = LinearPerpetualLedger((spec,), initial_collateral=D("1000"))
    ledger.apply_event(_trade(1, "100", "100"))
    ledger.apply_event(
        MarkEvent(
            timestamp=2,
            instrument_id="BTCUSDT",
            mark_price=D("100"),
            observed_trade_price=D("1"),
        )
    )
    assert ledger.position("BTCUSDT") is not None

    ledger.apply_event(
        MarkEvent(3, "BTCUSDT", D("94"), liquidation_backstop=ZERO_BACKSTOP)
    )
    assert ledger.position("BTCUSDT") is None


def test_full_cashflow_identity_includes_every_component_and_detects_tamper() -> None:
    spec = _spec(liquidation_fee_rate=D("0"))
    ledger = LinearPerpetualLedger((spec,), initial_collateral=D("1000"))
    ledger.apply_event(
        _trade(1, "1", "100", fee="1", spread="2", impact="3")
    )
    ledger.apply_event(MarkEvent(2, "BTCUSDT", D("110")))
    ledger.apply_event(
        FundingEvent(3, "BTCUSDT", funding_rate=D("0.001"), mark_price=D("110"))
    )
    snap = ledger.reconciliation_snapshot()

    assert snap.price_pnl == D("10")
    assert snap.fee == D("1")
    assert snap.spread == D("2")
    assert snap.impact == D("3")
    assert snap.funding == D("-0.110")
    assert snap.liquidation_fee == D("0")
    assert snap.insurance_adjustment == D("0")
    assert snap.adl_adjustment == D("0")
    assert snap.residual == D("0")
    assert snap.as_canonical_dict()["ending_equity"] == "1003.89"

    tampered = replace(snap, funding=snap.funding + D("1"))
    assert tampered.residual == D("1")


def test_explicit_insurance_and_adl_adjustments_are_in_reconciliation_identity() -> None:
    spec = _spec(
        liquidation_fee_rate=D("0"),
        brackets=(MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("0")),),
    )
    ledger = LinearPerpetualLedger((spec,), initial_collateral=D("1000"))
    ledger.apply_event(_trade(1, "100", "100"))
    ledger.apply_event(
        MarkEvent(
            2,
            "BTCUSDT",
            D("94"),
            liquidation_backstop=LiquidationBackstop(
                insurance_adjustment=D("10"),
                adl_adjustment=D("-10"),
            ),
        )
    )
    snap = ledger.reconciliation_snapshot()
    assert snap.insurance_adjustment == D("10")
    assert snap.adl_adjustment == D("-10")
    assert snap.ending_equity == D("400")
    assert snap.residual == D("0")


def test_liquidation_backstop_cannot_inject_unexplained_profit() -> None:
    spec = _spec(
        liquidation_fee_rate=D("0"),
        brackets=(MarginBracket(D("0"), None, D("0.10"), D("0.05"), D("0")),),
    )
    ledger = LinearPerpetualLedger((spec,), initial_collateral=D("1000"))
    ledger.apply_event(_trade(1, "100", "100"))

    with pytest.raises(InsufficientLiquidationBackstop, match="surplus injection"):
        ledger.apply_event(
            MarkEvent(
                2,
                "BTCUSDT",
                D("94"),
                liquidation_backstop=LiquidationBackstop(D("1"), D("0")),
            )
        )


def test_inverse_contract_fails_closed_at_construction() -> None:
    with pytest.raises(UnsupportedContract):
        ContractSpec(
            instrument_id="BTCUSD_PERP",
            contract_type="INVERSE_COIN_PERPETUAL",
            margin_asset="BTC",
            contract_multiplier=D("100"),
            quantity_step=D("1"),
            min_quantity=D("1"),
            min_notional=D("100"),
            liquidation_fee_rate=D("0.01"),
            settlement_quantum=D("1e-24"),
            margin_brackets=_brackets(),
        )


def test_usdc_uses_same_exact_accounting_without_currency_conversion():
    spec = replace(_spec(), instrument_id="BTCUSDC",
                   contract_type="LINEAR_USDC_PERPETUAL", margin_asset="USDC")
    ledger = LinearPerpetualLedger((spec,), initial_collateral=D("1000"))
    ledger.apply_event(_trade(1, "1", "100", instrument_id="BTCUSDC", fee=".05"))
    ledger.apply_event(FundingEvent(2, "BTCUSDC", D(".001"), D("110")))
    ledger.apply_event(_trade(3, "-1", "110", instrument_id="BTCUSDC", fee=".055"))
    assert ledger.account_equity() == D("1009.785")
    assert ledger.reconciliation_snapshot().residual == D("0")
    assert ledger.state_as_canonical_dict()["schema"] == "quantos.linear-usdc-perpetual-ledger.v2"


def test_usdt_and_usdc_cannot_share_a_collateral_wallet():
    usdc = replace(_spec(), instrument_id="BTCUSDC",
                   contract_type="LINEAR_USDC_PERPETUAL", margin_asset="USDC")
    with pytest.raises(UnsupportedContract, match="single margin asset"):
        LinearPerpetualLedger((_spec(), usdc), initial_collateral=D("1000"))


@pytest.mark.parametrize("contract_type,asset", [
    ("LINEAR_USDT_PERPETUAL", "USDC"), ("LINEAR_USDC_PERPETUAL", "USDT"),
    ("LINEAR_USDC_PERPETUAL", "BTC"),
])
def test_linear_contract_currency_must_match(contract_type, asset):
    with pytest.raises(UnsupportedContract):
        replace(_spec(), contract_type=contract_type, margin_asset=asset)
