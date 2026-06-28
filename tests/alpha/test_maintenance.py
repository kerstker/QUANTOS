"""断点4 — the maintenance pass: the missing caller that lets decay/degradation actually DRIVE state
(DEGRADED/PAUSED/RETIRED) through the audited manager, as a non-human actor that can never APPROVE."""
import pytest

from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition, DecayReport
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.alpha.lifecycle import AlphaLifecycleManager
from services.alpha.maintenance import run_maintenance

AGENT = Actor(actor_id="maintenance", kind=ActorKind.AGENT)
CFG = AlphaFactoryConfig()


def _mgr(states: dict):
    cat = AlphaCatalog()
    for aid, st in states.items():
        cat.register(AlphaDefinition(alpha_id=aid, name=aid, factor_ids=(f"f_{aid}",)))
        cat._set_state(aid, st)
    return AlphaLifecycleManager(cat, CFG), cat


def test_maintenance_retires_a_decayed_live_alpha_through_the_audited_manager():
    mgr, cat = _mgr({"decayed": AlphaState.PAPER_TRADING, "healthy": AlphaState.APPROVED})
    report = run_maintenance(mgr, AGENT, timestamp=9, decay_evidence_by_alpha={
        "decayed": {"decay": DecayReport(alpha_id="decayed", decayed=True)},
        "healthy": {"decay": DecayReport(alpha_id="healthy", decayed=False)},
    })
    assert cat.state("decayed") == AlphaState.RETIRED
    assert cat.state("healthy") == AlphaState.APPROVED
    assert report["decayed"]["action"] == "RETIRED"
    assert mgr.audit_chain_valid()                       # the transition was audited, not a silent poke


def test_maintenance_degrades_on_live_vs_backtest_decay():
    mgr, cat = _mgr({"weak": AlphaState.APPROVED})
    # live IC/return collapsed vs backtest -> degradation_monitor recommends DEGRADED
    report = run_maintenance(mgr, AGENT, timestamp=3, health_by_alpha={
        "weak": {"backtest": {"ic": 0.04, "return": 0.10}, "live": {"ic": 0.005, "return": 0.02}},
    })
    assert cat.state("weak") == AlphaState.DEGRADED
    assert report["weak"]["action"] == "DEGRADED"


def test_maintenance_is_fail_closed_without_evidence():
    mgr, cat = _mgr({"a": AlphaState.PAPER_TRADING})
    report = run_maintenance(mgr, AGENT, timestamp=1)    # no evidence supplied
    assert cat.state("a") == AlphaState.PAPER_TRADING    # untouched
    assert report == {}


def test_maintenance_ignores_non_live_alphas():
    mgr, cat = _mgr({"t": AlphaState.TESTING})
    run_maintenance(mgr, AGENT, timestamp=1, decay_evidence_by_alpha={
        "t": {"decay": DecayReport(alpha_id="t", decayed=True)}})
    assert cat.state("t") == AlphaState.TESTING          # TESTING is not a live state -> not swept


def test_maintenance_never_approves_and_rejects_a_human_actor():
    mgr, cat = _mgr({"a": AlphaState.PAPER_TRADING})
    # containment: even with decay+health evidence, the pass only demotes/retires — never APPROVED
    run_maintenance(mgr, AGENT, timestamp=1,
                    health_by_alpha={"a": {"backtest": {"ic": 0.04, "return": 0.1}, "live": {"ic": -0.01, "return": -0.05}}},
                    decay_evidence_by_alpha={"a": {"decay": DecayReport(alpha_id="a", decayed=True)}})
    assert cat.state("a") != AlphaState.APPROVED
    # a human actor is rejected — maintenance is an automated non-human pass
    with pytest.raises(ValueError):
        run_maintenance(mgr, Actor(actor_id="alice", kind=ActorKind.HUMAN), timestamp=1)
