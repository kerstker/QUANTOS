"""Phase 1 — canonical contract tests (validation, state transitions, config,
serialization round-trip)."""
import pytest

from quantos.contracts import (
    RESEARCH_SAFE_DEFAULTS,
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
    with pytest.raises(ValueError):
        RiskDecision(RiskDecisionType.REJECTED, ())


def test_approved_forbids_reasons():
    with pytest.raises(ValueError):
        RiskDecision(RiskDecisionType.APPROVED, (RejectionReason.INSUFFICIENT_CASH,))


def test_risk_decision_roundtrip_and_hash():
    d = RiskDecision(RiskDecisionType.REJECTED, (RejectionReason.KILL_SWITCH_ACTIVE,))
    assert d.approved is False
    rt = RiskDecision.from_dict(d.to_dict())
    assert rt == d
    assert rt.reproducibility_hash == d.reproducibility_hash


def test_approved_with_limits_is_approved():
    assert RiskDecision(RiskDecisionType.APPROVED_WITH_LIMITS).approved is True


# --- hashing (CT-3) ----------------------------------------------------------

def test_stable_hash_is_order_independent():
    assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})


def test_stable_hash_canonicalizes_floats():
    # 0.1 + 0.2 != 0.3 in IEEE754, but must hash the same after rounding
    assert stable_hash({"x": 0.1 + 0.2}) == stable_hash({"x": 0.3})


def test_short_hash_length():
    assert len(short_hash({"x": 1})) == 16
