"""Rank #27 — kill-switch DRILL: one end-to-end test that exercises the whole safety chain, so the
runbook (docs/KILL_SWITCH_DRILL.md) is executable, not aspirational:
  arm (any actor) -> the pre-trade gate rejects EVERY intent -> a multi-party human grant resets it
  -> the durable audit chain records arm+reset and stays valid -> the incident ledger opens+resolves.
Proves the halt is fail-closed, the reset is HUMAN + multi-party only, and both leave a trail."""
import os

from quantos.contracts.alpha import Actor, ActorKind
from services.execution.dry_run_pipeline import screen_intents
from services.execution.intent_builder import build_intents
from services.governance.approval import approve, consume, open_request, review
from services.monitoring.audit_store import DurableAuditLog
from services.monitoring.incident_ledger import IncidentLedger
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch

_OPS = Actor(actor_id="ops", kind=ActorKind.SYSTEM)
_HUMAN = Actor(actor_id="alice", kind=ActorKind.HUMAN)
_B = Actor(actor_id="bob", kind=ActorKind.HUMAN)
_C = Actor(actor_id="carol", kind=ActorKind.HUMAN)


def _gate(ks):
    limits = RiskLimits(
        allowed_instruments=frozenset({"BTC", "ETH"}), allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=10 ** 9, max_order_notional=1e15, fat_finger_qty=10 ** 9,
        max_gross_exposure=1e15, max_net_exposure=1e15, max_leverage=1e9, max_single_name_pct=1e9,
        max_turnover=1e9, max_participation_rate=1e9, max_instrument_vol=10.0, daily_loss_limit=1e15,
        max_drawdown=1.0)
    return PreTradeRiskGate(limits, kill_switch=ks)


def _ctx(prices):
    def make(intent):
        return RiskContext(
            account=AccountRiskState(account_id="paper", cash=1e9, equity=1e9, day_pnl=0.0,
                                     peak_equity=1e9, positions=()),
            market=MarketContext(instrument_id=intent.instrument_id, known=True,
                                 reference_price=prices.get(intent.instrument_id, 1.0), market_open=True,
                                 price_as_of=1000, est_daily_volume=1e12, instrument_vol=0.3),
            strategy=StrategyApproval(strategy_id="drill", approved=True, approval_id="ok"),
            now=1000, max_staleness=10 ** 12)
    return make


def test_kill_switch_drill_end_to_end(tmp_path):
    audit = DurableAuditLog(os.path.join(str(tmp_path), "audit.db"))
    ledger = IncidentLedger(os.path.join(str(tmp_path), "incidents.db"), create=True)
    ks = KillSwitch()
    gate = _gate(ks)

    # 1) ARM — any actor may halt; the trip is recorded on the durable audit chain + incident ledger
    ks.arm(_OPS, "drill: simulated anomaly", at=1000, audit_sink=audit)
    incident = ks.state.incident_id
    ledger.open(incident_id=incident, severity="CRITICAL", kind="kill_switch_trip",
                opened_by=_OPS.actor_id, opened_ts=1000, description="drill")
    assert ks.is_active() and ledger.open_count() == 1

    # 2) HALT — every intent is fail-closed rejected while latched
    intents = build_intents({"BTC": 10, "ETH": -5}, {}, strategy_id="drill")
    out = screen_intents(intents, gate=gate, ctx_factory=_ctx({"BTC": 100.0, "ETH": 50.0}))
    assert not out["approved"] and len(out["rejected"]) == 2
    assert all("KILL_SWITCH" in r for _, reasons in out["rejected"] for r in reasons)

    # 3) RESET — HUMAN + multi-party grant bound to THIS incident (agents/single-human cannot)
    grant = consume(approve(review(open_request("req-drill", incident, _HUMAN, at=1), _B, at=2), _C, at=3),
                    incident, _HUMAN, at=4)
    ks.reset_with_grant(_HUMAN, grant, at=2000, audit_sink=audit)
    ledger.resolve(incident, resolved_by=_HUMAN.actor_id, resolved_ts=2000, resolution="multi-party grant reset")
    assert not ks.is_active() and ledger.open_count() == 0

    # 4) TRAIL — the durable audit chain records arm + reset and stays valid; trading resumes clean
    actions = [e.action for e in audit.events]
    assert actions == ["kill_switch.arm", "kill_switch.reset"]
    assert audit.chain_valid()
    resumed = screen_intents(build_intents({"BTC": 1}, {}, strategy_id="drill"),
                             gate=gate, ctx_factory=_ctx({"BTC": 100.0}))
    assert len(resumed["approved"]) == 1 and not resumed["rejected"]


def test_agent_cannot_reset_the_drill():
    ks = KillSwitch()
    ks.arm(_OPS, "halt", at=1)
    agent = Actor(actor_id="bot", kind=ActorKind.AGENT)
    grant = consume(approve(review(open_request("r", ks.state.incident_id, _HUMAN, at=1), _B, at=2), _C, at=3),
                    ks.state.incident_id, _HUMAN, at=4)
    import pytest
    with pytest.raises(Exception):                       # a non-human actor can never clear the halt
        ks.reset_with_grant(agent, grant, at=2)
    assert ks.is_active()
