"""Rank #11 — participation-driven impact cost in the dry-run pipeline.

The flat cost_bps ignores trade size; the optional Almgren-Chriss impact model charges a
participation-driven cost (temp sqrt + perm linear) per fill when ADV is supplied, falling back
to flat bps per-name when it isn't. commission cancels on both sides of the reconciliation
identity, so the residual stays ~0. Provenance + a TCA-lite calibration band land in run detail."""
import pytest

from services.execution.dry_run_pipeline import (
    reconcile_run, run_multi_period_dry_run, simulate_fills)
from services.execution.impact import AlmgrenChrissImpact
from services.execution.intent_builder import build_intents
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine


def _gate():
    limits = RiskLimits(
        allowed_instruments=frozenset({"BTC", "ETH"}), allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=10 ** 9, max_order_notional=1e15, fat_finger_qty=10 ** 9,
        max_gross_exposure=1e15, max_net_exposure=1e15, max_leverage=1e9, max_single_name_pct=1e9,
        max_turnover=1e9, max_participation_rate=1e9, max_instrument_vol=10.0, daily_loss_limit=1e15,
        max_drawdown=1.0)
    return PreTradeRiskGate(limits, kill_switch=None)


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


def test_impact_cost_charged_when_adv_supplied():
    intents = build_intents({"BTC": 10}, {}, strategy_id="carry")
    imp = AlmgrenChrissImpact(temp_coef=10.0, perm_coef=5.0, temp_exponent=0.5)
    # notional 1000, ADV 100000 -> participation 0.01; temp = 10*sqrt(0.01)=1.0bps, perm=5*0.01=0.05bps
    out = simulate_fills([(intents[0], "ref")], {"BTC": 100.0}, adapter=DryRunAdapterEngine(),
                         run_id="r", timestamp=1000, impact=imp, adv={"BTC": 100_000.0})
    expected = 1000.0 * (1.0 + 0.05) / 1e4
    assert out["fills"][0].commission == pytest.approx(expected, rel=1e-9)
    assert out["n_impact"] == 1 and out["n_flat"] == 0
    assert out["impact_trades"] == [(1000.0, 100_000.0)]


def test_impact_differs_from_flat_bps():
    intents = build_intents({"BTC": 10}, {}, strategy_id="carry")
    imp = AlmgrenChrissImpact()
    flat = simulate_fills([(intents[0], "ref")], {"BTC": 100.0}, adapter=DryRunAdapterEngine(),
                          run_id="r", timestamp=1000, cost_bps=10.0)["fills"][0].commission
    impacted = simulate_fills([(intents[0], "ref")], {"BTC": 100.0}, adapter=DryRunAdapterEngine(),
                              run_id="r", timestamp=1000, cost_bps=10.0,
                              impact=imp, adv={"BTC": 1_000.0})["fills"][0].commission
    assert impacted != flat                          # size-aware != flat


def test_missing_adv_falls_back_to_flat_bps_per_name():
    intents = build_intents({"BTC": 10}, {}, strategy_id="carry")
    imp = AlmgrenChrissImpact()
    out = simulate_fills([(intents[0], "ref")], {"BTC": 100.0}, adapter=DryRunAdapterEngine(),
                         run_id="r", timestamp=1000, cost_bps=7.0, impact=imp, adv={})  # no ADV for BTC
    assert out["n_impact"] == 0 and out["n_flat"] == 1
    assert out["fills"][0].commission == pytest.approx(1000.0 * 7.0 / 1e4)   # flat fallback


def test_impact_cost_preserves_reconciliation_identity():
    intents = build_intents({"BTC": 10}, {}, strategy_id="carry")
    imp = AlmgrenChrissImpact(temp_coef=50.0, perm_coef=25.0)   # large impact
    out = simulate_fills([(intents[0], "ref")], {"BTC": 100.0}, adapter=DryRunAdapterEngine(),
                         run_id="r", timestamp=1000, impact=imp, adv={"BTC": 500.0})
    pnl = reconcile_run(out["fills"], {"BTC": 101.0}, initial_capital=1e7)
    assert pnl["residual"] < 1e-6                     # commission cancels on both sides of the identity
    assert pnl["costs"] > 0.0


def _multi(**kw):
    periods = [1, 2]
    weights = {1: {"BTC": 0.5, "ETH": -0.5}, 2: {"BTC": 0.6, "ETH": -0.4}}
    prices = {1: {"BTC": 100.0, "ETH": 50.0}, 2: {"BTC": 101.0, "ETH": 49.0}}
    return run_multi_period_dry_run(
        periods=periods, weights_by_period=weights, prices_by_period=prices, gate=_gate(),
        ctx_factory=lambda i, p, t: _ctx_factory(p)(i), adapter=DryRunAdapterEngine(),
        strategy_id="carry", aum=1e6, **kw)


def test_multi_period_flat_default_marks_flat_bps_provenance():
    run = _multi(cost_bps=10.0)
    assert run.detail["cost_model"] == "FLAT_BPS"
    assert run.detail["cost_provenance"] == "FLAT_BPS"
    assert "tca" not in run.detail


def test_multi_period_impact_marks_provenance_and_tca_band():
    imp = AlmgrenChrissImpact()          # the base scenario coefs -> charged sits inside the band
    adv = {1: {"BTC": 5e5, "ETH": 5e5}, 2: {"BTC": 5e5, "ETH": 5e5}}
    run = _multi(cost_bps=10.0, impact=imp, adv_by_period=adv)
    assert run.detail["cost_model"] == "IMPACT_AC"
    assert run.detail["cost_provenance"] == "UNCALIBRATED_PRINCIPLED"
    assert run.detail["cost_fills"]["impact"] > 0
    tca = run.detail["tca"]
    assert tca["band_lo"] <= tca["assumed"] <= tca["band_hi"] and tca["in_band"] is True
    assert run.pnl["residual"] < 1e-6
