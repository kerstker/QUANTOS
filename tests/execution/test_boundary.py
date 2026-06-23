"""Phase 10 — execution boundary: admits only an APPROVED + independently
re-verified + non-duplicate intent (fail-closed; closes the Phase-8 deferred
boundary-provenance gap)."""
from quantos.contracts.trading import OrderIntent, OrderSide, OrderType
from services.execution.boundary import ExecutionBoundary
from services.risk.pre_trade import (
    AccountRiskState, MarketContext, PreTradeRiskGate, RiskContext, RiskLimits, StrategyApproval,
)


def _setup():
    gate = PreTradeRiskGate(RiskLimits(allowed_instruments=frozenset({"AAA"}),
                                       allowed_accounts=frozenset({"acct1"})))
    intent = OrderIntent(instrument_id="AAA", side=OrderSide.BUY, quantity=10,
                         order_type=OrderType.MARKET, strategy_id="s1")
    ctx = RiskContext(
        account=AccountRiskState(account_id="acct1", cash=1e6, equity=1e6, peak_equity=1e6),
        market=MarketContext(instrument_id="AAA", known=True, reference_price=100.0,
                             market_open=True, price_as_of=1000, est_daily_volume=1e6, instrument_vol=0.2),
        strategy=StrategyApproval(strategy_id="s1", approved=True, approval_id="ap1"), now=1000)
    return gate, intent, ctx


def test_admits_approved_and_verified_intent():
    gate, intent, ctx = _setup()
    d = gate.evaluate(intent, ctx)
    ref = gate.issue_ref(d, intent, ctx)
    assert ExecutionBoundary(gate).admit(intent, ctx, d, ref).admitted is True


def test_denies_forged_ref():
    gate, intent, ctx = _setup()
    d = gate.evaluate(intent, ctx)
    res = ExecutionBoundary(gate).admit(intent, ctx, d, "FORGED_NON_EMPTY")
    assert not res.admitted and "REF_UNVERIFIED" in res.reasons


def test_denies_rejected_decision():
    gate, intent, ctx = _setup()
    ctx2 = ctx.model_copy(update={"strategy": StrategyApproval(approved=False)})
    d = gate.evaluate(intent, ctx2)
    ref = gate.issue_ref(d, intent, ctx2)             # "" for a rejected decision
    res = ExecutionBoundary(gate).admit(intent, ctx2, d, ref)
    assert not res.admitted and "NOT_APPROVED" in res.reasons


def test_idempotency_denies_duplicate():
    gate, intent, ctx = _setup()
    d = gate.evaluate(intent, ctx)
    ref = gate.issue_ref(d, intent, ctx)
    b = ExecutionBoundary(gate)
    assert b.admit(intent, ctx, d, ref).admitted is True
    res2 = b.admit(intent, ctx, d, ref)
    assert not res2.admitted and "DUPLICATE" in res2.reasons
