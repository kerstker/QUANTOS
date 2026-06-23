"""Phase 8 — pre-trade risk gate: clean approve, fail-closed, collect-all,
boundaries, post-trade aggregate, kill-switch, no-mutation, audit, approval-ref."""
import pytest

import services.risk.pre_trade.gate as gate_mod
from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.order import OrderState
from quantos.contracts.portfolio import Position
from quantos.contracts.rejection import RejectionReason as R
from quantos.contracts.risk import RiskDecisionType
from quantos.contracts.trading import Order, OrderIntent, OrderSide, OrderType
from services.risk.pre_trade import (
    AccountRiskState, MarketContext, PreTradeRiskGate, RiskContext, RiskLimits, StrategyApproval,
)
from services.risk.pre_trade.gate import approval_ref
from services.risk.pre_trade.order_bridge import apply_decision


def _intent(**o):
    d = dict(instrument_id="AAA", side=OrderSide.BUY, quantity=10,
             order_type=OrderType.MARKET, limit_price=None, strategy_id="s1")
    d.update(o)
    return OrderIntent(**d)


def _market(**o):
    d = dict(instrument_id="AAA", known=True, reference_price=100.0, market_open=True,
             price_as_of=1000, est_daily_volume=1_000_000.0, instrument_vol=0.2)
    d.update(o)
    return MarketContext(**d)


def _account(**o):
    d = dict(account_id="acct1", cash=1_000_000.0, equity=1_000_000.0,
             day_pnl=0.0, peak_equity=1_000_000.0, positions=())
    d.update(o)
    return AccountRiskState(**d)


def _ctx(**o):
    d = dict(account=_account(), market=_market(),
             strategy=StrategyApproval(strategy_id="s1", approved=True, approval_id="ap1"),
             now=1000, max_staleness=86_400)
    d.update(o)
    return RiskContext(**d)


def _limits(**o):
    d = dict(allowed_instruments=frozenset({"AAA"}), allowed_accounts=frozenset({"acct1"}))
    d.update(o)
    return RiskLimits(**d)


def _gate(**o):
    return PreTradeRiskGate(o.pop("limits", None) or _limits(), **o)


def test_clean_order_is_approved():
    d = _gate().evaluate(_intent(), _ctx())
    assert d.decision == RiskDecisionType.APPROVED and d.reasons == () and d.approved


def test_fail_closed_on_check_exception(monkeypatch):
    def boom(intent, ctx, limits):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(gate_mod, "CHECK_REGISTRY", (("boom", "A", boom),))
    d = _gate().evaluate(_intent(), _ctx())
    assert not d.approved and d.reasons == (R.UNKNOWN_RISK_ERROR,)


def test_audit_failure_forces_reject():
    class BadAudit:
        events = []
        def record(self, *a, **k):
            raise RuntimeError("audit down")
    d = _gate(audit_trail=BadAudit()).evaluate(_intent(), _ctx())
    assert not d.approved and d.reasons == (R.UNKNOWN_RISK_ERROR,)


def test_run_all_collects_multiple_reasons_deduped():
    # unknown instrument + unknown account + fat-finger size; POSITION_LIMIT collapses once
    d = _gate(limits=_limits(allowed_instruments=frozenset(), allowed_accounts=frozenset())).evaluate(
        _intent(instrument_id="BBB", quantity=200_000), _ctx())
    assert not d.approved
    assert R.INSTRUMENT_NOT_ALLOWED in d.reasons and R.ACCOUNT_NOT_ALLOWED in d.reasons
    assert R.ORDER_SIZE_EXCEEDED in d.reasons
    assert d.reasons.count(R.POSITION_LIMIT_EXCEEDED) <= 1     # deduped despite gross/net/leverage


def test_position_limit_boundary():
    g = _gate(limits=_limits(default_max_position_qty=1000))
    assert g.evaluate(_intent(quantity=1000), _ctx()).approved          # at limit passes
    assert R.POSITION_LIMIT_EXCEEDED in g.evaluate(_intent(quantity=1001), _ctx()).reasons


def test_daily_loss_rejects_at_limit():
    g = _gate(limits=_limits(daily_loss_limit=100_000.0))
    assert R.DAILY_LOSS_LIMIT_EXCEEDED in g.evaluate(_intent(), _ctx(account=_account(day_pnl=-100_000.0))).reasons
    assert g.evaluate(_intent(), _ctx(account=_account(day_pnl=-99_999.0))).approved


def test_non_finite_inputs_rejected_at_construction():
    with pytest.raises(Exception):
        MarketContext(instrument_id="AAA", reference_price=float("nan"))
    with pytest.raises(Exception):
        OrderIntent(instrument_id="AAA", side=OrderSide.BUY, quantity=10, limit_price=float("inf"))


def test_sell_flip_through_short_cap_rejected():
    acct = _account(positions=(Position(instrument_id="AAA", quantity=500, avg_cost=100, market_value=50_000.0),))
    d = _gate(limits=_limits(default_max_position_qty=1000)).evaluate(
        _intent(side=OrderSide.SELL, quantity=1600), _ctx(account=acct))
    assert R.POSITION_LIMIT_EXCEEDED in d.reasons                       # projected -1100 breaches short cap


def test_post_trade_aggregate_gross_breach():
    acct = _account(positions=(Position(instrument_id="BBB", quantity=1500, avg_cost=100, market_value=150_000.0),))
    d = _gate(limits=_limits(max_gross_exposure=200_000.0)).evaluate(
        _intent(quantity=1000), _ctx(account=acct))   # +100k notional pushes gross to 250k
    assert R.POSITION_LIMIT_EXCEEDED in d.reasons


def test_missing_reference_price_rejects():
    d = _gate().evaluate(_intent(), _ctx(market=_market(reference_price=None)))
    assert R.INVALID_PRICE in d.reasons


def test_stale_or_closed_market_rejects():
    assert R.MARKET_CLOSED in _gate().evaluate(_intent(), _ctx(market=_market(market_open=False))).reasons
    assert R.MARKET_CLOSED in _gate().evaluate(_intent(), _ctx(market=_market(price_as_of=0), now=1_000_000)).reasons


def test_strategy_not_approved_rejects():
    d = _gate().evaluate(_intent(), _ctx(strategy=StrategyApproval(approved=False)))
    assert R.STRATEGY_NOT_APPROVED in d.reasons


def test_kill_switch_blocks_an_otherwise_clean_order():
    g = _gate()
    g.kill_switch.arm(Actor(actor_id="ops", kind=ActorKind.HUMAN), "halt", at=999)
    d = g.evaluate(_intent(), _ctx())
    assert not d.approved and d.reasons == (R.KILL_SWITCH_ACTIVE,)      # checked first, absolute


def test_gate_does_not_mutate_intent():
    intent = _intent()
    before = intent.model_dump()
    _gate().evaluate(intent, _ctx())
    assert intent.model_dump() == before                               # frozen + permission-only


def test_decision_is_deterministic():
    a = _gate().evaluate(_intent(quantity=1001), _ctx())
    b = _gate().evaluate(_intent(quantity=1001), _ctx())
    assert a.reasons == b.reasons


def test_audit_chain_and_order_state_bridge():
    g = _gate()
    d = g.evaluate(_intent(), _ctx())
    assert len(g.audit.events) == 1 and g.audit.chain_valid()
    order = Order(order_id="o1", intent=_intent(), state=OrderState.VALIDATING)
    assert apply_decision(order, d).state == OrderState.RISK_APPROVED
    rej = g.evaluate(_intent(), _ctx(strategy=StrategyApproval(approved=False)))
    assert apply_decision(order, rej).state == OrderState.RISK_REJECTED


def test_approval_ref_only_for_approved_decision():
    g = _gate()
    approved = g.evaluate(_intent(), _ctx())
    rejected = g.evaluate(_intent(), _ctx(strategy=StrategyApproval(approved=False)))
    assert approval_ref(approved, "decision-123") == "decision-123"
    assert approval_ref(rejected, "decision-123") == ""                # P0-1: rejected -> empty ref


def test_gate_refuses_unsafe_construction():
    from quantos.contracts.config import SafetyConfig
    with pytest.raises(Exception):
        PreTradeRiskGate(_limits(), safety_config=SafetyConfig(live_trading_enabled=True))


def test_limit_order_without_reference_price_is_rejected():
    # review P0: a LIMIT order with no reference price cannot be sanity-checked -> reject
    d = _gate().evaluate(
        _intent(order_type=OrderType.LIMIT, limit_price=100.0),
        _ctx(market=_market(reference_price=None)))
    assert R.INVALID_PRICE in d.reasons and not d.approved


def test_intent_bound_ref_is_verifiable_and_not_reusable():
    g = _gate()
    intent, ctx = _intent(), _ctx()
    d = g.evaluate(intent, ctx)
    ref = g.issue_ref(d, intent, ctx)
    assert ref and g.verify_ref(ref, intent, ctx)               # genuine approved ref verifies
    assert not g.verify_ref(ref, _intent(quantity=999), ctx)    # not reusable for another intent
    assert not g.verify_ref("FORGED_NON_EMPTY", intent, ctx)    # random string fails
    rej = g.evaluate(_intent(), _ctx(strategy=StrategyApproval(approved=False)))
    assert g.issue_ref(rej, _intent(), _ctx()) == ""            # rejected -> empty ref
