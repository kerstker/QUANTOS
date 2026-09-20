"""P1-8 — core data/trading contracts are frozen AND non-finite-rejecting.

`gt=0` / `ge=0` Field constraints only reject NaN; a NaN comparison is always
False, so NaN used to be rejected only INCIDENTALLY, and `+inf` passed every one
of those constraints outright (`Bar(volume=inf)`, `Instrument(multiplier=inf)`,
`OrderIntent(order_type=LIMIT, limit_price=inf)` all validated). A stored `inf`
then reaches downstream comparisons where `value > limit` silently evaluates
False — the same failure mode `services/risk/pre_trade/context.py::_FROZEN_FINITE`
guards against at the risk boundary.

These models deliberately stay LAX on type coercion (no `strict=True`): stored
JSON payloads are read back through `model_validate`/`model_validate_json`, so a
round-trip of every legitimate payload must keep validating.
"""
import pytest
from pydantic import ValidationError

from quantos.contracts.data import (
    Bar,
    CorporateAction,
    DataQualityReport,
    DatasetVersion,
    Instrument,
    Tick,
    TradingCalendar,
)
from quantos.contracts.trading import ExecutionReport, Fill, Order, OrderIntent, OrderSide

_NON_FINITE = (float("inf"), float("-inf"), float("nan"))


def _bar_kwargs(**overrides) -> dict:
    fields = dict(instrument_id="BTC", event_time=100, knowledge_time=100,
                  open=100.0, high=101.0, low=99.0, close=100.5, volume=10.0)
    fields.update(overrides)
    return fields


# --- market-data contracts --------------------------------------------------

@pytest.mark.parametrize("bad", _NON_FINITE)
def test_bar_rejects_non_finite_volume(bad):
    with pytest.raises(ValidationError):
        Bar(**_bar_kwargs(volume=bad))


@pytest.mark.parametrize("bad", _NON_FINITE)
def test_bar_rejects_non_finite_high(bad):
    with pytest.raises(ValidationError):
        Bar(**_bar_kwargs(high=bad))


@pytest.mark.parametrize("bad", _NON_FINITE)
def test_bar_rejects_non_finite_price_even_when_ohlc_bounds_hold(bad):
    # high is set to the same non-finite value so the OHLC consistency check
    # cannot be what rejects the record (inf bounds inf) — finiteness must.
    with pytest.raises(ValidationError):
        Bar(**_bar_kwargs(open=bad, high=bad))


@pytest.mark.parametrize("field", ["price", "size", "bid", "ask"])
@pytest.mark.parametrize("bad", _NON_FINITE)
def test_tick_rejects_non_finite_values(field, bad):
    fields = dict(instrument_id="BTC", event_time=1, knowledge_time=1, price=100.0)
    fields[field] = bad
    with pytest.raises(ValidationError):
        Tick(**fields)


@pytest.mark.parametrize("bad", _NON_FINITE)
def test_instrument_rejects_non_finite_multiplier(bad):
    with pytest.raises(ValidationError):
        Instrument(instrument_id="BTC", symbol="BTCUSDT", asset_class="CRYPTO",
                   multiplier=bad)


@pytest.mark.parametrize("field", ["ratio", "amount"])
@pytest.mark.parametrize("bad", _NON_FINITE)
def test_corporate_action_rejects_non_finite_amounts(field, bad):
    fields = dict(instrument_id="AAPL", action_type="DIVIDEND", knowledge_time=10,
                  event_time=5, ex_date="2026-01-01")
    fields[field] = bad
    with pytest.raises(ValidationError):
        CorporateAction(**fields)


def test_bitemporal_invariant_still_rejects_lookahead():
    with pytest.raises(ValidationError):
        Bar(**_bar_kwargs(event_time=100, knowledge_time=99))


# --- trading contracts ------------------------------------------------------

@pytest.mark.parametrize("bad", _NON_FINITE)
def test_limit_order_intent_rejects_non_finite_limit_price(bad):
    # A MARKET intent carrying limit_price is rejected by the structural rule, so
    # LIMIT is the branch that actually exercises price finiteness.
    with pytest.raises(ValidationError):
        OrderIntent(instrument_id="BTC", side=OrderSide.BUY, quantity=1,
                    order_type="LIMIT", limit_price=bad)


@pytest.mark.parametrize("bad", _NON_FINITE)
def test_order_rejects_non_finite_avg_fill_price(bad):
    intent = OrderIntent(instrument_id="BTC", side=OrderSide.BUY, quantity=1)
    with pytest.raises(ValidationError):
        Order(order_id="o1", intent=intent, avg_fill_price=bad)


@pytest.mark.parametrize("bad", _NON_FINITE)
def test_fill_rejects_non_finite_price(bad):
    with pytest.raises(ValidationError):
        Fill(fill_id="f1", order_id="o1", quantity=1, price=bad)


@pytest.mark.parametrize("model", [Instrument, Bar, Tick, CorporateAction,
                                   DatasetVersion, TradingCalendar,
                                   DataQualityReport, OrderIntent, Order, Fill,
                                   ExecutionReport])
def test_core_contract_config_rejects_non_finite_floats(model):
    assert model.model_config.get("allow_inf_nan") is False


# --- regression: legitimate payloads keep validating ------------------------

def test_valid_data_records_round_trip_laxly():
    bar = Bar(**_bar_kwargs())
    assert Bar.model_validate(bar.model_dump()) == bar
    assert Bar.model_validate_json(bar.model_dump_json()) == bar
    assert Instrument(instrument_id="BTC", symbol="BTCUSDT", asset_class="CRYPTO")
    assert Tick(instrument_id="BTC", event_time=1, knowledge_time=1, price=100.0)


def test_lax_type_coercion_is_intentionally_preserved():
    bar = Bar(**_bar_kwargs(open="100.0", close="100.5"))     # str -> float
    assert bar.open == 100.0 and bar.close == 100.5


def test_valid_trading_records_still_construct():
    intent = OrderIntent(instrument_id="BTC", side=OrderSide.BUY, quantity=2,
                         order_type="LIMIT", limit_price=60_000.0)
    order = Order(order_id="o1", intent=intent, avg_fill_price=60_000.0)
    assert Order.model_validate(order.model_dump()) == order
    assert Fill(fill_id="f1", order_id="o1", quantity=1, price=60_000.0)
    assert ExecutionReport(report_id="r1", order_id="o1", state="CREATED")
