"""Phase 8 — coverage capstone: all 20 §8 RejectionReason are reachable in the
pre-trade gate (19 from named checks + UNKNOWN_RISK_ERROR from the fail-closed wrapper)."""
from types import SimpleNamespace

import services.risk.pre_trade.gate as gate_mod
from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.config import SafetyConfig
from quantos.contracts.killswitch import KillSwitchState
from quantos.contracts.rejection import RejectionReason as R
from quantos.contracts.trading import OrderIntent, OrderSide, OrderType
from services.risk.pre_trade import checks as C
from services.risk.pre_trade import (
    AccountRiskState, MarketContext, PreTradeRiskGate, RiskContext, RiskLimits, StrategyApproval,
)


def _intent(**o):
    d = dict(instrument_id="AAA", side=OrderSide.BUY, quantity=10, order_type=OrderType.MARKET, strategy_id="s1")
    d.update(o)
    return OrderIntent(**d)


def _ctx(**o):
    d = dict(account=AccountRiskState(account_id="acct1", cash=1e6, equity=1e6, peak_equity=1e6),
             market=MarketContext(instrument_id="AAA", known=True, reference_price=100.0, market_open=True,
                                  price_as_of=1000, est_daily_volume=1e6, instrument_vol=0.2),
             strategy=StrategyApproval(strategy_id="s1", approved=True, approval_id="ap1"), now=1000)
    d.update(o)
    return RiskContext(**d)


L = RiskLimits(allowed_instruments=frozenset({"AAA"}), allowed_accounts=frozenset({"acct1"}))


def test_all_twenty_rejection_reasons_reachable():
    got = set()

    # Stage A
    got |= set(C.check_strategy_approved(_intent(), _ctx(strategy=StrategyApproval(approved=False)), L))
    got |= set(C.check_live_trading_disabled(_intent(), _ctx(safety_config=SafetyConfig(live_trading_enabled=True)), L))
    got |= set(C.check_ai_direct_trading(_intent(), _ctx(safety_config=SafetyConfig(allow_ai_direct_trading=True)), L))
    got |= set(C.check_instrument_allowed(_intent(), _ctx(market=MarketContext(instrument_id="AAA", known=False)), L))
    got |= set(C.check_account_allowed(_intent(), _ctx(account=AccountRiskState(account_id="nope")), L))
    got |= set(C.check_quantity(SimpleNamespace(quantity=0), _ctx(), L))                 # bypass contract for the check
    got |= set(C.check_price_sanity(_intent(), _ctx(market=MarketContext(instrument_id="AAA", known=True, reference_price=None, market_open=True)), L))
    dup = _ctx()
    got |= set(C.check_duplicate(_intent(), _ctx(recent_order_fingerprints=(dup.fingerprint(_intent()),)), L))
    got |= set(C.check_market_session(_intent(), _ctx(market=MarketContext(instrument_id="AAA", known=True, reference_price=100.0, market_open=False)), L))

    # Stage B
    got |= set(C.check_cash(_intent(quantity=10), _ctx(account=AccountRiskState(account_id="acct1", cash=0.0)), L))
    got |= set(C.check_position_limit(_intent(quantity=5000), _ctx(), L))
    got |= set(C.check_order_size(_intent(quantity=200_000), _ctx(), L))
    got |= set(C.check_concentration(_intent(quantity=5000), _ctx(account=AccountRiskState(account_id="acct1", equity=1000.0, peak_equity=1000.0)), L))
    got |= set(C.check_daily_loss(_intent(), _ctx(account=AccountRiskState(account_id="acct1", day_pnl=-1e9, equity=1e6, peak_equity=1e6)), L))
    got |= set(C.check_drawdown(_intent(), _ctx(account=AccountRiskState(account_id="acct1", equity=1.0, peak_equity=1e6)), L))
    got |= set(C.check_turnover(_intent(), _ctx(plan_turnover=5.0), L))
    got |= set(C.check_liquidity(_intent(), _ctx(market=MarketContext(instrument_id="AAA", known=True, reference_price=100.0, market_open=True, est_daily_volume=None)), L))
    got |= set(C.check_volatility(_intent(), _ctx(market=MarketContext(instrument_id="AAA", known=True, reference_price=100.0, market_open=True, instrument_vol=None)), L))
    got |= set(C.check_kill_switch(_intent(), _ctx(kill_switch=KillSwitchState(tripped=True)), L))

    # UNKNOWN_RISK_ERROR via the gate's fail-closed wrapper
    import pytest  # noqa
    g = PreTradeRiskGate(L)
    orig = gate_mod.CHECK_REGISTRY

    def boom(i, c, l):
        raise RuntimeError("x")
    try:
        gate_mod.CHECK_REGISTRY = (("boom", "A", boom),)
        got |= set(g.evaluate(_intent(), _ctx()).reasons)
    finally:
        gate_mod.CHECK_REGISTRY = orig

    assert got == set(R), f"unreachable reasons: {set(R) - got}"
