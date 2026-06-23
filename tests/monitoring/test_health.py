"""Phase 13 — monitoring snapshot: integrity = audit chain; anomaly flags."""
from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.monitoring.audit_log import GlobalAuditLog
from services.monitoring.health import build_snapshot
from services.risk.pre_trade.kill_switch import KillSwitch


def _log():
    log = GlobalAuditLog()
    log.append(event_id="e1", actor="a", action="A", timestamp=1)
    log.append(event_id="e2", actor="b", action="B", timestamp=2)
    return log


def test_clean_system_is_healthy():
    snap = build_snapshot(timestamp=10, audit_log=_log())
    assert snap.healthy and snap.anomalies == () and snap.audit_chain_valid


def test_broken_audit_chain_is_unhealthy():
    log = _log()
    log.events[0] = log.events[0].model_copy(update={"actor": "X"})   # tamper
    snap = build_snapshot(timestamp=10, audit_log=log)
    assert not snap.healthy and "AUDIT_CHAIN_BROKEN" in snap.anomalies


def test_kill_switch_active_is_anomaly_but_integrity_intact():
    ks = KillSwitch()
    ks.arm(Actor(actor_id="ops", kind=ActorKind.HUMAN), "halt", at=1)
    snap = build_snapshot(timestamp=10, audit_log=_log(), kill_switch=ks)
    assert "KILL_SWITCH_ACTIVE" in snap.anomalies and snap.kill_switch_active
    assert snap.healthy                                              # audit integrity still ok


def test_alpha_state_counts_and_rejects():
    cat = AlphaCatalog()
    for a in ("a1", "a2", "a3"):
        cat.register(AlphaDefinition(alpha_id=a, name=a, factor_ids=(a,)))
    cat._set_state("a1", AlphaState.PAPER_TRADING)
    cat._set_state("a2", AlphaState.PAPER_TRADING)
    snap = build_snapshot(timestamp=10, audit_log=_log(), catalog=cat, rejected_decisions=5)
    assert snap.alpha_state_counts.get("PAPER_TRADING") == 2
    assert snap.alpha_state_counts.get("DRAFT") == 1
    assert snap.rejected_decisions == 5
