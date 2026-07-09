from quantos.contracts.alpha import Actor, ActorKind

from services.execution.dry_run_pipeline import (_ann_sharpe, _max_drawdown_from_returns, _vol_target_returns,
                                                 plan_target_quantities, reconcile_run, run_dry_run_pipeline,
                                                 run_multi_period_dry_run, screen_intents, simulate_fills)
from services.execution.intent_builder import build_intents
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
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


def _armed_gate():
    """A gate whose kill-switch is already latched (GLOBAL) — the gate binds THIS switch's state."""
    ks = KillSwitch()
    gate = _gate(kill_switch=ks)
    ks.arm(Actor(actor_id="ops", kind=ActorKind.SYSTEM), "halt", at=1000)
    return gate


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


def test_simulate_fills_produces_paper_fills_no_live():
    intents = build_intents({"BTC": 10}, {}, strategy_id="carry")
    out = simulate_fills([(intents[0], "ref-1")], {"BTC": 100.0}, adapter=DryRunAdapterEngine(),
                         run_id="r1", timestamp=1000)
    assert len(out["fills"]) == 1
    f = out["fills"][0]
    assert f.instrument_id == "BTC" and f.quantity == 10 and f.price == 100.0
    assert all(r.live_trading_allowed is False and r.dry_run is True for r in out["reports"])  # never live


def test_simulate_fills_with_cost_charges_commission():
    intents = build_intents({"BTC": 10}, {}, strategy_id="carry")
    out = simulate_fills([(intents[0], "ref-1")], {"BTC": 100.0}, adapter=DryRunAdapterEngine(),
                         run_id="r1", timestamp=1000, cost_bps=5.0)
    assert round(out["fills"][0].commission, 6) == 0.5  # notional 1000 * 5bps = 0.5


def test_reconcile_run_returns_pnl_summary():
    intents = build_intents({"BTC": 10}, {}, strategy_id="carry")
    out = simulate_fills([(intents[0], "ref-1")], {"BTC": 100.0}, adapter=DryRunAdapterEngine(),
                         run_id="r1", timestamp=1000)
    pnl = reconcile_run(out["fills"], {"BTC": 101.0}, initial_capital=1e7)
    # bought 10 @100, marked 101 -> unrealized +10 ; reconciliation identity holds (residual ~0)
    assert pnl["total_pnl"] == 10.0 and pnl["residual"] < 1e-6 and pnl["positions"] == {"BTC": 10}


def _pipeline(ks=None):
    return run_dry_run_pipeline(
        weights={"BTC": 0.5, "ETH": -0.5}, prices={"BTC": 100.0, "ETH": 50.0}, current_positions={},
        aum=1e6, gate=_gate(kill_switch=ks), ctx_factory=_ctx_factory({"BTC": 100.0, "ETH": 50.0}),
        adapter=DryRunAdapterEngine(), strategy_id="carry", timestamp=1000,
        marks={"BTC": 101.0, "ETH": 49.0})


def test_end_to_end_benign_produces_run():
    r = _pipeline()
    assert r.strategy_id == "carry" and r.n_intents == 2 and r.n_approved == 2 and r.n_rejected == 0
    assert r.detail["n_fills"] == 2 and r.audit_root and isinstance(r.pnl, dict)
    assert r.pnl["residual"] < 1e-6  # reconciliation identity holds end-to-end


def test_kill_switch_latched_zero_fills():
    # gate holds a latched kill-switch -> every intent rejected at the chokepoint -> zero fills
    r = run_dry_run_pipeline(
        weights={"BTC": 0.5}, prices={"BTC": 100.0}, current_positions={}, aum=1e6,
        gate=_armed_gate(), ctx_factory=_ctx_factory({"BTC": 100.0}), adapter=DryRunAdapterEngine(),
        strategy_id="carry", timestamp=1000)
    assert r.n_approved == 0 and r.detail["n_fills"] == 0 and r.n_rejected >= 1


def test_deterministic_same_input_same_run():
    a, b = _pipeline(), _pipeline()
    assert (a.run_id, a.n_intents, a.n_approved, a.n_rejected, a.audit_root, a.pnl, a.detail) == \
           (b.run_id, b.n_intents, b.n_approved, b.n_rejected, b.audit_root, b.pnl, b.detail)


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


# --- plan_target_quantities filters (F10 audit: exchange step/min-qty/min-notional) ----------

def test_target_quantities_filters_none_matches_unfiltered_baseline():
    """filters=None (default) must reproduce the exact pre-existing behavior -- the head-line
    acceptance bar for this optional addition."""
    w = {"BTC": 0.5, "ETH": -0.5, "DOGE": 0.1}
    px = {"BTC": 100.0, "ETH": 50.0, "DOGE": 0.0}
    implicit = plan_target_quantities(w, px, aum=10000.0)
    explicit = plan_target_quantities(w, px, aum=10000.0, filters=None)
    assert implicit == explicit == {"BTC": 50, "ETH": -100}


def test_target_quantities_filters_rounds_to_step():
    """A step_size that doesn't evenly divide the raw quantity must actually change the result
    (floor to the grid), not pass the raw rounded value through untouched."""
    unfiltered = plan_target_quantities({"BTC": 0.53}, {"BTC": 100.0}, aum=10000.0)
    assert unfiltered == {"BTC": 53}                          # baseline: plain round(), no step

    filters = {"BTC": (10.0, 0.0, 0.0)}                       # step_size=10, no min_qty/min_notional
    filtered = plan_target_quantities({"BTC": 0.53}, {"BTC": 100.0}, aum=10000.0, filters=filters)
    assert filtered == {"BTC": 50}                             # 53 -> floor to nearest 10 -> 50


def test_target_quantities_filters_drops_below_min_notional():
    """A leg whose rounded quantity*price falls below min_notional is excluded THIS PERIOD ONLY
    (fail-soft) -- other legs, including ones with no filter entry, proceed normally."""
    filters = {"BTC": (1.0, 0.0, 1e6)}                         # impossibly high min_notional -> BTC dropped
    w = {"BTC": 0.1, "ETH": 0.5}
    px = {"BTC": 100.0, "ETH": 50.0}
    q = plan_target_quantities(w, px, aum=10000.0, filters=filters)
    assert "BTC" not in q
    assert q["ETH"] == 100                                     # unaffected: no filter entry for ETH


def test_target_quantities_filters_drops_below_min_qty():
    filters = {"BTC": (1.0, 100.0, 0.0)}                       # min_qty=100 but raw qty ~53
    q = plan_target_quantities({"BTC": 0.53}, {"BTC": 100.0}, aum=10000.0, filters=filters)
    assert "BTC" not in q


def test_target_quantities_filters_missing_instrument_passthrough():
    """Missing filter data for an instrument must NOT block quantization of the others -- and must
    NOT itself be quantized (honest 'data incomplete, don't apply' stance)."""
    filters = {"ETH": (10.0, 0.0, 0.0)}                        # only ETH has filter data
    w = {"BTC": 0.53, "ETH": 0.53}
    px = {"BTC": 100.0, "ETH": 100.0}
    q = plan_target_quantities(w, px, aum=10000.0, filters=filters)
    assert q["BTC"] == 53                                      # no filter for BTC -> untouched passthrough
    assert q["ETH"] == 50                                      # ETH quantized via step_size=10


# --- Multi-period rolling dry-run --------------------------------------------

def _mp_ctx(intent, prices, period):
    return RiskContext(
        account=AccountRiskState(account_id="paper", cash=1e9, equity=1e9, day_pnl=0.0, peak_equity=1e9,
                                 positions=()),
        market=MarketContext(instrument_id=intent.instrument_id, known=True,
                             reference_price=prices.get(intent.instrument_id, 1.0), market_open=True,
                             price_as_of=period, est_daily_volume=1e12, instrument_vol=0.3),
        strategy=StrategyApproval(strategy_id="carry", approved=True, approval_id="ok"),
        now=period, max_staleness=10 ** 15)


_PERIODS = [1, 2, 3]
_W = {1: {"BTC": 0.5, "ETH": -0.5}, 2: {"BTC": 0.5, "ETH": -0.5}, 3: {"BTC": 0.4, "ETH": -0.6}}
_PX = {1: {"BTC": 100.0, "ETH": 50.0}, 2: {"BTC": 110.0, "ETH": 48.0}, 3: {"BTC": 105.0, "ETH": 52.0}}


def _mp(gate=None, cash_flow_fn=None, vol_target=None):
    return run_multi_period_dry_run(
        periods=_PERIODS, weights_by_period=_W, prices_by_period=_PX, gate=gate or _gate(),
        ctx_factory=_mp_ctx, adapter=DryRunAdapterEngine(), strategy_id="carry", aum=1e6,
        cost_bps=5.0, cash_flow_fn=cash_flow_fn, vol_target=vol_target)


def test_multi_period_benign_curve():
    r = _mp()
    assert r.detail["mode"] == "multi_period" and r.detail["n_periods"] == 3
    assert len(r.detail["equity_curve"]) == 3
    assert r.pnl["residual"] < 1e-6                       # price-leg reconciliation identity holds
    assert r.pnl["final_equity"] == r.detail["equity_curve"][-1]["equity"]
    assert r.n_approved > 0 and r.audit_root


def test_multi_period_kill_switch_latched_zero_fills():
    ks = KillSwitch()
    gate = _gate(kill_switch=ks)
    ks.arm(Actor(actor_id="ops", kind=ActorKind.SYSTEM), "halt", at=1)  # latched (permanent) before the run
    r = _mp(gate=gate)
    assert r.n_approved == 0 and all(p["fills"] == 0 for p in r.detail["equity_curve"])
    assert all(abs(p["equity"] - 1e6) < 1e-6 for p in r.detail["equity_curve"])  # flat at aum, no fills/funding


def test_multi_period_funding_flows_into_equity():
    flat = _mp(cash_flow_fn=None).pnl["final_equity"]
    with_fund = _mp(cash_flow_fn=lambda held, prices, period: 1000.0 if held else 0.0).pnl["final_equity"]
    assert with_fund > flat


def test_multi_period_deterministic():
    a, b = _mp(), _mp()
    assert (a.audit_root, a.pnl, a.detail["equity_curve"]) == (b.audit_root, b.pnl, b.detail["equity_curve"])


def test_vol_target_returns_passthrough_then_scales():
    rets = [0.01, -0.01, 0.02, -0.02, 0.03]
    out = _vol_target_returns(rets, target_ann=0.10, lookback=2, lev_cap=3.0, ppy=52)
    assert out[:2] == rets[:2]                     # warmup passthrough
    assert len(out) == len(rets)
    assert out[2] != rets[2]                       # scaled once trailing vol is available


def test_multi_period_vol_target_curve_present():
    r = _mp(vol_target={"target_ann": 0.10, "lookback": 1, "lev_cap": 3.0, "ppy": 52})
    assert len(r.detail["equity_curve_vt"]) == len(r.detail["equity_curve"]) == 3


def test_multi_period_vol_target_sharpe_and_drawdown_computed():
    """sharpe_vt/max_drawdown_vt are the same pure helpers applied to the vt-curve's OWN `ret` series
    (not the raw returns) -- reproducibility of the "RAW -> VT" figures quoted in docs."""
    vt = {"target_ann": 0.10, "lookback": 1, "lev_cap": 3.0, "ppy": 52}
    r = _mp(vol_target=vt)
    vt_returns = [p["ret"] for p in r.detail["equity_curve_vt"]]
    assert r.pnl["sharpe_vt"] == _ann_sharpe(vt_returns)
    assert r.pnl["max_drawdown_vt"] == _max_drawdown_from_returns(vt_returns)
    # sanity: vol-target actually reshaped the series here (lookback=1 << n periods=3), else this test
    # wouldn't distinguish "computed from vt curve" from "computed from raw curve"
    raw_returns = [p["ret"] for p in r.detail["equity_curve"]]
    assert vt_returns != raw_returns


def test_multi_period_vol_target_passthrough_matches_raw_exactly():
    """lookback (10) >> n periods (3) -> _vol_target_returns is pure passthrough, so the vt curve is
    IDENTICAL to the raw curve and sharpe_vt/max_drawdown_vt must equal the raw fields exactly."""
    vt = {"target_ann": 0.10, "lookback": 10, "lev_cap": 3.0, "ppy": 52}
    r = _mp(vol_target=vt)
    assert r.pnl["sharpe_vt"] == r.pnl["sharpe"]
    assert r.pnl["max_drawdown_vt"] == r.pnl["max_drawdown"]


def test_multi_period_no_vol_target_sharpe_vt_is_none():
    """vol_target=None must NOT silently omit the keys or coerce to 0 -- callers need to tell "not computed"
    apart from "computed as zero"."""
    r = _mp(vol_target=None)
    assert r.detail["equity_curve_vt"] == []
    assert "sharpe_vt" in r.pnl and "max_drawdown_vt" in r.pnl
    assert r.pnl["sharpe_vt"] is None
    assert r.pnl["max_drawdown_vt"] is None


def test_multi_period_vol_target_single_period_matches_raw_edge_behavior():
    """Single-period run: _ann_sharpe defines len(returns)<2 -> 0.0 for RAW (insufficient sample);
    sharpe_vt must follow the identical edge behavior. lookback(1) >= n returns(1) means
    _vol_target_returns is pure passthrough, so max_drawdown_vt must equal the raw max_drawdown
    exactly (not invent a new edge semantic) -- _max_drawdown_from_returns has NO len<2 special case."""
    r = run_multi_period_dry_run(
        periods=[1], weights_by_period={1: {"BTC": 0.5}}, prices_by_period={1: {"BTC": 100.0}},
        gate=_gate(), ctx_factory=_mp_ctx, adapter=DryRunAdapterEngine(), strategy_id="carry", aum=1e6,
        cost_bps=5.0, vol_target={"target_ann": 0.10, "lookback": 1, "lev_cap": 3.0, "ppy": 52})
    assert r.pnl["sharpe"] == 0.0
    assert r.pnl["sharpe_vt"] == 0.0
    assert r.pnl["max_drawdown_vt"] == r.pnl["max_drawdown"]


def test_multi_period_vol_target_empty_periods_no_crash():
    """periods=[] boundary: RAW already reduces to 0.0/0.0 (empty returns); vt must not crash and must
    mirror the same edge behavior (equity_curve_vt also stays empty)."""
    r = run_multi_period_dry_run(
        periods=[], weights_by_period={}, prices_by_period={}, gate=_gate(), ctx_factory=_mp_ctx,
        adapter=DryRunAdapterEngine(), strategy_id="carry", aum=1e6, cost_bps=5.0,
        vol_target={"target_ann": 0.10, "lookback": 1, "lev_cap": 3.0, "ppy": 52})
    assert r.detail["equity_curve_vt"] == []
    assert r.pnl["sharpe_vt"] == 0.0 and r.pnl["max_drawdown_vt"] == 0.0


def test_multi_period_records_net_exposure():
    r = _mp()
    assert all("net" in p and "gross" in p for p in r.detail["equity_curve"])
    # period 1: BTC +5000@100 / ETH -10000@50 = +500k -500k = 0 (dollar-neutral at clean-lot entry)
    assert abs(r.detail["equity_curve"][0]["net"]) < 1.0
    assert "avg_net_exposure_pct" in r.pnl and r.pnl["avg_net_exposure_pct"] >= 0.0


# --- run_multi_period_dry_run `filters` (F10 audit: exchange-quantization plumbed end-to-end) --

def test_multi_period_filters_none_matches_unfiltered_exactly():
    """filters=None (default) must produce byte-identical output to the call that omits the
    parameter entirely -- the head-line acceptance bar for this optional addition."""
    baseline = _mp()
    explicit_none = run_multi_period_dry_run(
        periods=_PERIODS, weights_by_period=_W, prices_by_period=_PX, gate=_gate(),
        ctx_factory=_mp_ctx, adapter=DryRunAdapterEngine(), strategy_id="carry", aum=1e6,
        cost_bps=5.0, filters=None)
    assert (baseline.run_id, baseline.audit_root, baseline.pnl, baseline.detail) == \
           (explicit_none.run_id, explicit_none.audit_root, explicit_none.pnl, explicit_none.detail)


def test_multi_period_filters_changes_quantities_end_to_end():
    """Passing filters through the full pipeline (not just plan_target_quantities in isolation)
    must actually change the traded quantities -- proven via the recorded gross notional, which
    is a direct function of the (now step-quantized) fill quantities."""
    filters = {"BTC": (7.0, 0.0, 0.0), "ETH": (7.0, 0.0, 0.0)}
    unfiltered = _mp()
    filtered = run_multi_period_dry_run(
        periods=_PERIODS, weights_by_period=_W, prices_by_period=_PX, gate=_gate(),
        ctx_factory=_mp_ctx, adapter=DryRunAdapterEngine(), strategy_id="carry", aum=1e6,
        cost_bps=5.0, filters=filters)
    # t=1: BTC raw target 5000 -> step 7 floors to 4998 ; ETH raw target -10000 -> floors to -9996
    # gross notional therefore differs from the unfiltered (exact round-number) baseline.
    assert unfiltered.detail["equity_curve"][0]["gross"] != filtered.detail["equity_curve"][0]["gross"]
    assert filtered.detail["equity_curve"][0]["gross"] == 4998 * 100 + 9996 * 50


def test_multi_period_filters_min_notional_drops_only_affected_leg():
    """A leg whose quantized size falls below min_notional is excluded fail-soft (this leg only,
    this period only) -- it must NOT fail or block the other leg's fill."""
    filters = {"BTC": (1.0, 0.0, 1e15)}  # BTC always below min_notional -> dropped
    r = run_multi_period_dry_run(
        periods=[1], weights_by_period={1: {"BTC": 0.5, "ETH": -0.5}},
        prices_by_period={1: {"BTC": 100.0, "ETH": 50.0}},
        gate=_gate(), ctx_factory=_mp_ctx, adapter=DryRunAdapterEngine(), strategy_id="carry",
        aum=1e6, cost_bps=5.0, filters=filters)
    assert r.detail["equity_curve"][0]["fills"] == 1  # only ETH fills; BTC excluded fail-soft


# --- broker adapter seam guard (轨道3 子项目1) ---------------------------------
import pytest as _pytest  # noqa: E402
from quantos.contracts.config import SafetyViolation  # noqa: E402
from services.trading.broker_adapter import DryRunBrokerAdapter  # noqa: E402


def test_simulate_fills_rejects_unknown_adapter_fail_closed():
    intents = build_intents({"BTC": 10}, {}, strategy_id="carry")
    with _pytest.raises(SafetyViolation):
        simulate_fills([(intents[0], "ref-1")], {"BTC": 100.0}, adapter=object(),
                       run_id="r1", timestamp=1000)


def test_simulate_fills_accepts_dry_run_broker_adapter():
    intents = build_intents({"BTC": 10}, {}, strategy_id="carry")
    out = simulate_fills([(intents[0], "ref-1")], {"BTC": 100.0}, adapter=DryRunBrokerAdapter(),
                         run_id="r1", timestamp=1000)
    assert len(out["fills"]) == 1 and out["fills"][0].instrument_id == "BTC"
