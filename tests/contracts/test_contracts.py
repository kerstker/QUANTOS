"""Phase 1 — canonical contract tests (validation, state transitions, config,
serialization round-trip)."""
import pytest
from pydantic import ValidationError

from quantos.contracts.hashing import source_file_sha256, stable_json

from quantos.contracts import (
    RESEARCH_SAFE_DEFAULTS,
    OrderIntent,
    Order,
    Fill,
    ExecutionReport,
    OrderSide,
    OrderType,
    SafetyConfig,
    SafetyViolation,
    RejectionReason,
    LEGACY_DRY_RUN_CROSSWALK,
    from_legacy,
    OrderState,
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    can_transition,
    assert_transition,
    is_terminal,
    InvalidOrderTransition,
    RiskDecision,
    RiskDecisionType,
    stable_hash,
    short_hash,
)


# --- SafetyConfig (§1.2) -----------------------------------------------------

def test_safety_defaults_are_safe():
    c = RESEARCH_SAFE_DEFAULTS
    assert c.live_trading_enabled is False
    assert c.real_broker_enabled is False
    assert c.allow_order_submission is False
    assert c.allow_ai_direct_trading is False
    assert c.require_human_approval is True
    assert c.live_trading_armed is False
    c.assert_research_safe()  # must not raise


def test_partial_live_flags_do_not_arm():
    # enabling only some live preconditions must NOT arm live trading
    assert SafetyConfig(live_trading_enabled=True).live_trading_armed is False
    assert SafetyConfig(live_trading_enabled=True, real_broker_enabled=True).live_trading_armed is False


def test_ai_can_never_be_live_actor():
    armed_but_ai = SafetyConfig(
        live_trading_enabled=True, real_broker_enabled=True,
        allow_order_submission=True, allow_ai_direct_trading=True,
    )
    assert armed_but_ai.live_trading_armed is False


def test_research_safe_guard_fails_closed():
    with pytest.raises(SafetyViolation):
        SafetyConfig(live_trading_enabled=True).assert_research_safe()
    with pytest.raises(SafetyViolation):
        SafetyConfig(allow_ai_direct_trading=True).assert_research_safe()


def test_safety_config_roundtrip():
    c = SafetyConfig(real_broker_enabled=True)
    assert SafetyConfig.from_dict(c.to_dict()) == c


# --- RejectionReason (§8) ----------------------------------------------------

def test_all_20_blueprint_reasons_present():
    assert len(list(RejectionReason)) == 20
    for name in ("STRATEGY_NOT_APPROVED", "LIVE_TRADING_DISABLED",
                 "AI_DIRECT_TRADING_FORBIDDEN", "KILL_SWITCH_ACTIVE",
                 "VOLATILITY_LIMIT_EXCEEDED", "UNKNOWN_RISK_ERROR"):
        assert name in RejectionReason.__members__


def test_legacy_crosswalk_targets_are_canonical():
    for legacy, canonical in LEGACY_DRY_RUN_CROSSWALK.items():
        assert isinstance(canonical, RejectionReason)


def test_legacy_crosswalk_key_mappings():
    assert from_legacy("LIVE_NOT_ALLOWED") == RejectionReason.LIVE_TRADING_DISABLED
    assert from_legacy("KILL_SWITCH") == RejectionReason.KILL_SWITCH_ACTIVE
    assert from_legacy("DUPLICATE") == RejectionReason.DUPLICATE_ORDER
    assert from_legacy("FORBIDDEN_INST") == RejectionReason.INSTRUMENT_NOT_ALLOWED
    assert from_legacy("something_unknown") == RejectionReason.UNKNOWN_RISK_ERROR


# --- OrderState machine (§7) -------------------------------------------------

def test_every_state_has_a_transition_entry():
    for s in OrderState:
        assert s in ALLOWED_TRANSITIONS


def test_terminal_states_have_no_outgoing():
    for s in TERMINAL_STATES:
        assert ALLOWED_TRANSITIONS[s] == frozenset()
        assert is_terminal(s)


def test_happy_path_transitions_allowed():
    path = [
        OrderState.CREATED, OrderState.VALIDATING, OrderState.RISK_APPROVED,
        OrderState.SUBMITTED, OrderState.PARTIALLY_FILLED, OrderState.FILLED,
    ]
    for a, b in zip(path, path[1:]):
        assert can_transition(a, b)
        assert_transition(a, b)  # must not raise


def test_illegal_transitions_rejected():
    assert not can_transition(OrderState.CREATED, OrderState.FILLED)
    with pytest.raises(InvalidOrderTransition):
        assert_transition(OrderState.FILLED, OrderState.SUBMITTED)  # terminal


# --- RiskDecision ------------------------------------------------------------

def test_rejected_requires_reasons():
    with pytest.raises(ValidationError):
        RiskDecision(decision=RiskDecisionType.REJECTED, reasons=())


def test_approved_forbids_reasons():
    with pytest.raises(ValidationError):
        RiskDecision(decision=RiskDecisionType.APPROVED, reasons=(RejectionReason.INSUFFICIENT_CASH,))


def test_risk_decision_roundtrip_and_hash():
    d = RiskDecision(decision=RiskDecisionType.REJECTED, reasons=(RejectionReason.KILL_SWITCH_ACTIVE,))
    assert d.approved is False
    rt = RiskDecision.from_dict(d.to_dict())
    assert rt == d
    assert rt.reproducibility_hash == d.reproducibility_hash


def test_approved_with_limits_is_approved():
    assert RiskDecision(decision=RiskDecisionType.APPROVED_WITH_LIMITS).approved is True


# --- Trading domain entities (§6.6) ------------------------------------------

def test_order_intent_rejects_non_positive_quantity():
    with pytest.raises(ValidationError):
        OrderIntent(instrument_id="X", side=OrderSide.BUY, quantity=0)


def test_limit_order_requires_price():
    with pytest.raises(ValidationError):
        OrderIntent(instrument_id="X", side=OrderSide.BUY, quantity=1, order_type=OrderType.LIMIT)
    ok = OrderIntent(instrument_id="X", side=OrderSide.BUY, quantity=1,
                     order_type=OrderType.LIMIT, limit_price=10.5)
    assert ok.limit_price == 10.5


def test_market_order_forbids_limit_price():
    with pytest.raises(ValidationError):
        OrderIntent(instrument_id="X", side=OrderSide.SELL, quantity=1,
                    order_type=OrderType.MARKET, limit_price=10.0)


def test_order_filled_cannot_exceed_intent():
    intent = OrderIntent(instrument_id="X", side=OrderSide.BUY, quantity=100)
    Order(order_id="o1", intent=intent, filled_quantity=100)  # ok
    with pytest.raises(ValidationError):
        Order(order_id="o1", intent=intent, filled_quantity=101)


def test_order_defaults_to_created_state():
    intent = OrderIntent(instrument_id="X", side=OrderSide.BUY, quantity=1)
    assert Order(order_id="o1", intent=intent).state == OrderState.CREATED


def test_trading_entities_roundtrip():
    intent = OrderIntent(instrument_id="X", side=OrderSide.BUY, quantity=5)
    rep = ExecutionReport(report_id="r1", order_id="o1", state=OrderState.FILLED,
                          filled_quantity=5)
    assert ExecutionReport.model_validate(rep.model_dump()) == rep
    assert OrderIntent.model_validate(intent.model_dump()) == intent
    assert Fill(fill_id="f1", order_id="o1", quantity=5, price=10.0).quantity == 5


# --- hashing (CT-3) ----------------------------------------------------------

def test_stable_hash_is_order_independent():
    assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})


def test_stable_hash_canonicalizes_floats():
    # 0.1 + 0.2 != 0.3 in IEEE754, but must hash the same after rounding
    assert stable_hash({"x": 0.1 + 0.2}) == stable_hash({"x": 0.3})


def test_stable_json_exposes_the_exact_hash_input_without_erasing_float_tags():
    assert stable_json({"text": "运行", "ratio": 1.0, "x": 0.1234567890123}) == (
        '{"ratio":1.0,"text":"\\u8fd0\\u884c","x":0.123456789}'
    )
    assert stable_hash({"fill_ratio": 1.0}) == (
        "f6eff70c513aab222e4f21b088eabb62dac5032ad60359c2a5b82a2b9396c8aa"
    )


def test_short_hash_length():
    assert len(short_hash({"x": 1})) == 16


def test_source_file_hash_is_checkout_newline_independent(tmp_path):
    source = tmp_path / "source.py"
    source.write_bytes(b"first = 1\r\nsecond = 2\r\n")
    windows_digest = source_file_sha256(source)

    source.write_bytes(b"first = 1\nsecond = 2\n")
    assert source_file_sha256(source) == windows_digest

    source.write_bytes(b"first = 1\nsecond = 3\n")
    assert source_file_sha256(source) != windows_digest
