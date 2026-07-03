"""治理决策留痕(路线图 Rank #20 接线):kill-switch 搬闸/复位 与 部署门放行/拒绝
这两类治理决策,传入可选 audit_sink= 时必须各产生一条可验证的哈希链事件;不传 sink 时
行为一字不变(零回归)。用内存 GlobalAuditLog 当 duck-typed sink 做逻辑验证,另用落盘
DurableAuditLog 做一次端到端持久化验证。"""
import os

from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS
from quantos.contracts.governance import (
    ApprovalRecord, ConfigVersion, Environment, StrategyVersion,
)
from services.governance.approval import approve, consume, open_request, review
from services.deployment.gate import evaluate_deployment
from services.monitoring.audit_log import GlobalAuditLog
from services.monitoring.audit_store import DurableAuditLog
from services.risk.pre_trade.kill_switch import KillSwitch

HUMAN = Actor(actor_id="alice", kind=ActorKind.HUMAN)
AGENT = Actor(actor_id="bot", kind=ActorKind.AGENT)
_B = Actor(actor_id="bob", kind=ActorKind.HUMAN)
_C = Actor(actor_id="carol", kind=ActorKind.HUMAN)


def _grant(subject: str):
    r = review(open_request("req-1", subject, HUMAN, at=1), _B, at=2)
    return consume(approve(r, _C, at=3), subject, HUMAN, at=4)


# ---- kill-switch ----

def test_arm_emits_auditable_event():
    sink = GlobalAuditLog()
    ks = KillSwitch()
    ks.arm(AGENT, "anomaly", at=10, audit_sink=sink)
    assert ks.is_active()                                     # halt stands
    evs = sink.events
    assert len(evs) == 1 and evs[0].action == "kill_switch.arm"
    assert evs[0].actor == "bot" and evs[0].target == ks.state.incident_id
    assert evs[0].metadata["reason"] == "anomaly"
    assert sink.chain_valid()


def test_reset_emits_auditable_event():
    sink = GlobalAuditLog()
    ks = KillSwitch()
    ks.arm(AGENT, "halt", at=1, audit_sink=sink)
    ok = ApprovalRecord(approval_id="ap", subject_id=ks.state.incident_id,
                        approver="alice", approved=True, timestamp=2)
    ks.reset(HUMAN, ok, at=4, audit_sink=sink)
    assert not ks.is_active()
    evs = sink.events
    assert [e.action for e in evs] == ["kill_switch.arm", "kill_switch.reset"]
    assert evs[1].actor == "alice"
    assert sink.chain_valid()


def test_reset_with_grant_emits_auditable_event():
    sink = GlobalAuditLog()
    ks = KillSwitch()
    ks.arm(AGENT, "halt", at=1)                               # arm without sink
    ks.reset_with_grant(HUMAN, _grant(ks.state.incident_id), at=5, audit_sink=sink)
    assert not ks.is_active()
    evs = sink.events
    assert len(evs) == 1 and evs[0].action == "kill_switch.reset"
    assert evs[0].metadata["reset_via"] == "multi_party"


def test_kill_switch_without_sink_is_zero_regression():
    ks = KillSwitch()
    ks.arm(AGENT, "halt", at=1)                               # no audit_sink -> unchanged behaviour
    ok = ApprovalRecord(approval_id="ap", subject_id=ks.state.incident_id,
                        approver="alice", approved=True, timestamp=2)
    st = ks.reset(HUMAN, ok, at=4)
    assert st.tripped is False and st.reset_by == HUMAN


# ---- deployment gate ----

def _pinned():
    versions = [ConfigVersion(config_id="cfg", version="1", content_hash="a" * 16),
                StrategyVersion(strategy_id="strat", version="1", content_hash="b" * 16, approved=True)]
    return versions, {"cfg": "a" * 16, "strat": "b" * 16}


def test_deployment_decision_emits_auditable_event():
    sink = GlobalAuditLog()
    d = evaluate_deployment(Environment.PAPER, subject="deploy-1", grant=_grant("deploy-1"),
                            versions=_pinned()[0], approved_pins=_pinned()[1],
                            audit_sink=sink, at=99)
    assert d.allowed is True
    evs = sink.events
    assert len(evs) == 1 and evs[0].action == "deployment.evaluate"
    assert evs[0].target == "PAPER:deploy-1"
    assert evs[0].metadata["allowed"] == "True"
    assert sink.chain_valid()


def test_deployment_denial_is_audited_with_reasons():
    sink = GlobalAuditLog()
    d = evaluate_deployment(Environment.PAPER, subject="deploy-2", grant=_grant("deploy-2"),
                            audit_sink=sink, at=100)                # no version pins -> denied
    assert d.allowed is False and "VERSION_PIN_REQUIRED" in d.reasons
    ev = sink.events[0]
    assert ev.metadata["allowed"] == "False"
    assert "VERSION_PIN_REQUIRED" in ev.metadata["reasons"]


def test_deployment_without_sink_is_zero_regression():
    versions, pins = _pinned()
    d = evaluate_deployment(Environment.PAPER, subject="deploy-3", grant=_grant("deploy-3"),
                            versions=versions, approved_pins=pins)  # no audit_sink / at
    assert d.allowed is True


# ---- end-to-end durable: both decisions land in ONE persisted chain ----

def test_both_decisions_share_one_durable_chain(tmp_path):
    path = os.path.join(str(tmp_path), "gov_audit.db")
    sink = DurableAuditLog(path)
    ks = KillSwitch()
    ks.arm(HUMAN, "drill", at=1, audit_sink=sink)
    evaluate_deployment(Environment.PAPER, subject="deploy-x", grant=_grant("deploy-x"),
                        versions=_pinned()[0], approved_pins=_pinned()[1], audit_sink=sink, at=2)
    # reopen from disk: both governance decisions survived and the chain validates
    reopened = DurableAuditLog(path)
    assert [e.action for e in reopened.events] == ["kill_switch.arm", "deployment.evaluate"]
    assert reopened.chain_valid()
