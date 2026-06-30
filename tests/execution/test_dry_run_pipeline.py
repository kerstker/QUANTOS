from quantos.contracts.alpha import Actor, ActorKind

from services.execution.dry_run_pipeline import plan_target_quantities, screen_intents
from services.execution.intent_builder import build_intents
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch


def _gate(kill_switch=None, fat_finger=10 ** 9):
    """A permissive-but-real gate: allow-lists populated, all numeric limits set wide so the ONLY thing
    that can reject a benign intent is what each test intends (kill-switch / fat-finger)."""
    limits = RiskLimits(
        allowed_instruments=frozenset({"BTC", "ETH"}), allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=10 ** 9, max_order_notional=1e15, fat_finger_qty=fat_finger,
        max_gross_exposure=1e15, max_net_exposure=1e15, max_leverage=1e9, max_single_name_pct=1e9,
        max_turnover=1e9, max_participation_rate=1e9, max_instrument_vol=10.0, daily_loss_limit=1e15,
        max_drawdown=1.0)
    return PreTradeRiskGate(limits, kill_switch=kill_switch)


def _ctx_factory(prices):
    def make(intent):
        return RiskContext(
            account=AccountRiskState(account_id="paper", cash=1e9, equity=1e9, day_pnl=0.0,
                                     peak_equity=1e9, positions=()),
            market=MarketContext(instrument_id=intent.instrument_id, known=True,
                                 reference_price=prices.get(intent.instrument_id, 1.0), market_open=True,
                                 price_as_of=1000, est_daily_volume=1e12, instrument_vol=0.3),
            strategy=StrategyApproval(strategy_id="carry", approved=True, approval_id="ok"),
            now=1000, max_staleness=10 ** 12)
    return make


def test_benign_intents_approved():
    intents = build_intents({"BTC": 10, "ETH": -5}, {}, strategy_id="carry")
    out = screen_intents(intents, gate=_gate(), ctx_factory=_ctx_factory({"BTC": 100.0, "ETH": 50.0}))
    assert len(out["approved"]) == 2 and not out["rejected"]
    assert all(ref for _, ref in out["approved"])  # APPROVED -> non-empty intent-bound risk ref


def test_kill_switch_latched_rejects_all():
    ks = KillSwitch()
    gate = _gate(kill_switch=ks)  # gate binds THIS switch's state at evaluate-time
    ks.arm(Actor(actor_id="ops", kind=ActorKind.SYSTEM), "halt", at=1000)  # GLOBAL trip
    intents = build_intents({"BTC": 10, "ETH": -5}, {}, strategy_id="carry")
    out = screen_intents(intents, gate=gate, ctx_factory=_ctx_factory({"BTC": 100.0, "ETH": 50.0}))
    assert not out["approved"] and len(out["rejected"]) == 2
    assert all("KILL_SWITCH" in r for _, reasons in out["rejected"] for r in reasons)


def test_fat_finger_rejected_fail_closed():
    intents = build_intents({"BTC": 1000}, {}, strategy_id="carry")  # qty 1000 >> fat_finger 5
    out = screen_intents(intents, gate=_gate(fat_finger=5), ctx_factory=_ctx_factory({"BTC": 100.0}))
    assert not out["approved"] and out["rejected"]


def test_target_quantities_round_and_skip_bad_price():
    w = {"BTC": 0.5, "ETH": -0.5, "DOGE": 0.1}
    px = {"BTC": 100.0, "ETH": 50.0, "DOGE": 0.0}  # DOGE price 0 -> skipped
    q = plan_target_quantities(w, px, aum=10000.0)
    assert q == {"BTC": 50, "ETH": -100}  # 0.5*1e4/100=50 ; -0.5*1e4/50=-100 ; DOGE dropped


def test_target_quantities_drops_zero_and_missing_price():
    w = {"BTC": 0.0001, "ETH": 0.5, "XRP": 0.5}
    px = {"BTC": 100.0, "ETH": 50.0}  # XRP price missing
    q = plan_target_quantities(w, px, aum=1000.0)
    assert q == {"ETH": 10}  # BTC rounds to 0 -> dropped ; XRP missing -> dropped
