"""727 C3 — the health machine goes live: degradation_monitor / maintenance /
retirement switch to the orthogonal machines (design doc §6, the ONLY commit in
the plan that changes runtime behavior).

What is pinned here:
* advance_health records health + reason WITHOUT clobbering the deployment
  position (the §3.4 composite states the mixed field could not express);
* FAILED de-risks the deployment dimension (§3.4 invariant 3 / §11.2);
* the legacy mixed state and the audit/transition chains look EXACTLY as they
  did before C3 (every unswitched reader is untouched);
* maintenance report action strings are unchanged (live_linkage consumers);
* divergent triples survive the C2 persistence round-trip, loss markers and all.
"""

import pytest

from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition, DecayReport
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.alpha.degradation_monitor import health_verdict_for
from services.alpha.lifecycle import AlphaLifecycleManager
from services.alpha.live_linkage import maintenance_skip_reason, strategy_status_from_report
from services.alpha.maintenance import run_maintenance
from services.lifecycle import (
    DeploymentState,
    HealthReason,
    HealthState,
    HealthVerdict,
    ResearchState,
)
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore

AGENT = Actor(actor_id="maintenance", kind=ActorKind.AGENT)
CFG = AlphaFactoryConfig()


def _mgr(states: dict):
    cat = AlphaCatalog()
    for aid, st in states.items():
        cat.register(AlphaDefinition(alpha_id=aid, name=aid, factor_ids=(f"f_{aid}",)))
        cat._set_state(aid, st)
    return AlphaLifecycleManager(cat, CFG), cat


def test_health_write_preserves_deployment_position_and_reason():
    mgr, cat = _mgr({"a": AlphaState.PAPER_TRADING})
    verdict = HealthVerdict(HealthState.DEGRADED, HealthReason.SIGNAL_DECAY)
    mgr.advance_health("a", verdict, AGENT, timestamp=7)

    # The §3.4 composite the mixed field could never express:
    # research-valid + still deployed + degraded, with a machine-readable cause.
    triple = cat.triple("a")
    assert triple.research is ResearchState.VALIDATED
    assert triple.deployment is DeploymentState.PAPER
    assert triple.health == verdict
    # deployment/health are now fully recorded; the research-challenged marker
    # stays — C3 recorded nothing about challenge history, so it remains lost.
    assert triple.lossy == ("research_challenged",)
    # …while the legacy field and the audit chain look exactly like pre-C3.
    assert cat.state("a") is AlphaState.DEGRADED
    assert mgr.audit_chain_valid()
    health_events = [e for e in mgr.audit if e.action == "ALPHA_HEALTH_DEGRADED"]
    assert len(health_events) == 1
    assert health_events[0].metadata["reason"] == "SIGNAL_DECAY"
    assert health_events[0].metadata["deployment"] == "PAPER"


def test_failed_verdict_de_risks_deployment_to_paused():
    mgr, cat = _mgr({"a": AlphaState.SHADOW})
    verdict = HealthVerdict(HealthState.FAILED, HealthReason.SLIPPAGE_BREACH)
    mgr.advance_health("a", verdict, AGENT, timestamp=8)

    triple = cat.triple("a")
    assert triple.deployment is DeploymentState.PAUSED   # §3.4 invariant 3
    assert triple.health == verdict
    assert cat.state("a") is AlphaState.PAUSED           # legacy overlap write


def test_health_re_observation_is_idempotent_and_reason_update_is_audited():
    mgr, cat = _mgr({"a": AlphaState.PAPER_TRADING})
    v1 = HealthVerdict(HealthState.DEGRADED, HealthReason.SIGNAL_DECAY)
    mgr.advance_health("a", v1, AGENT, timestamp=1)
    n_audit = len(mgr.audit)
    assert mgr.advance_health("a", v1, AGENT, timestamp=2) is None
    assert len(mgr.audit) == n_audit                     # no duplicate record

    # Same state, sharper diagnosis: a correction, not a transition.
    v2 = HealthVerdict(HealthState.DEGRADED, HealthReason.COST_INFLATION)
    mgr.advance_health("a", v2, AGENT, timestamp=3)
    assert cat.health("a") == v2
    assert cat.state("a") is AlphaState.DEGRADED         # legacy untouched
    assert mgr.audit[-1].metadata["legacy_write"] == "skipped_same_state"
    assert mgr.audit_chain_valid()


def test_healthy_recovery_is_not_wired_in_c3():
    mgr, cat = _mgr({"a": AlphaState.PAPER_TRADING})
    mgr.advance_health(
        "a", HealthVerdict(HealthState.DEGRADED, HealthReason.SIGNAL_DECAY),
        AGENT, timestamp=1,
    )
    with pytest.raises(ValueError, match="human-gated"):
        mgr.advance_health("a", HealthVerdict(HealthState.HEALTHY), AGENT, timestamp=2)


def test_health_edges_are_agent_reachable_but_legality_is_checked():
    mgr, cat = _mgr({"a": AlphaState.PAPER_TRADING})
    # A non-human actor may record any health verdict (§11.2 de-risking is always
    # permitted) — including FAILED, the most severe.
    mgr.advance_health(
        "a", HealthVerdict(HealthState.FAILED, HealthReason.RISK_BREACH),
        AGENT, timestamp=1,
    )
    assert cat.health("a").state is HealthState.FAILED
    with pytest.raises(TypeError):
        mgr.advance_health("a", "FAILED", AGENT, timestamp=2)  # not a verdict


def test_recovery_edge_the_legacy_field_cannot_express_keeps_severe_legacy():
    mgr, cat = _mgr({"a": AlphaState.PAPER_TRADING})
    mgr.advance_health(
        "a", HealthVerdict(HealthState.FAILED, HealthReason.RISK_BREACH),
        AGENT, timestamp=1,
    )
    assert cat.state("a") is AlphaState.PAUSED
    # FAILED -> DEGRADED is a legal health recovery, but legacy PAUSED ->
    # DEGRADED does not exist. The health machine records the improvement and
    # the legacy field stays at the more severe value (fail-safe direction).
    mgr.advance_health(
        "a", HealthVerdict(HealthState.DEGRADED, HealthReason.SIGNAL_DECAY),
        AGENT, timestamp=2,
    )
    assert cat.health("a").state is HealthState.DEGRADED
    assert cat.state("a") is AlphaState.PAUSED
    assert mgr.audit[-1].metadata["legacy_write"] == "skipped_illegal_legacy_edge"
    assert mgr.audit_chain_valid()


def test_health_verdict_for_maps_monitor_output():
    alerts = [
        {"metric": "slippage", "severity": "PAUSED", "detail": "..."},
        {"metric": "ic_retention", "severity": "DEGRADED", "detail": "..."},
    ]
    assert health_verdict_for("PAUSED", alerts) == HealthVerdict(
        HealthState.FAILED, HealthReason.SLIPPAGE_BREACH
    )
    assert health_verdict_for("DEGRADED", alerts) == HealthVerdict(
        HealthState.DEGRADED, HealthReason.SIGNAL_DECAY
    )
    assert health_verdict_for("OK", alerts) is None
    assert health_verdict_for("RETIRED", alerts) is None   # retirement's job
    assert health_verdict_for(None, alerts) is None


def test_maintenance_routes_degradation_through_health_machine():
    mgr, cat = _mgr({"weak": AlphaState.PAPER_TRADING})
    report = run_maintenance(mgr, AGENT, timestamp=3, health_by_alpha={
        "weak": {"backtest": {"ic": 0.04, "return": 0.10},
                 "live": {"ic": 0.005, "return": 0.02}},
    })
    assert report["weak"]["action"] == "DEGRADED"          # unchanged for consumers
    assert cat.state("weak") is AlphaState.DEGRADED        # legacy unchanged
    triple = cat.triple("weak")
    assert triple.deployment is DeploymentState.PAPER      # position PRESERVED
    assert triple.health == HealthVerdict(HealthState.DEGRADED, HealthReason.SIGNAL_DECAY)


def test_maintenance_pause_maps_to_health_failed_with_de_risk():
    mgr, cat = _mgr({"a": AlphaState.PAPER_TRADING})
    report = run_maintenance(mgr, AGENT, timestamp=4, health_by_alpha={
        "a": {"backtest": {"ic": 0.04, "return": 0.1, "slippage_bps": 5.0},
              "live": {"ic": 0.04, "return": 0.1, "slippage_bps": 25.0}},
    })
    assert report["a"]["action"] == "PAUSED"               # unchanged for consumers
    assert cat.state("a") is AlphaState.PAUSED
    triple = cat.triple("a")
    assert triple.health.state is HealthState.FAILED
    assert triple.health.reason is HealthReason.SLIPPAGE_BREACH
    assert triple.deployment is DeploymentState.PAUSED
    # The live-side skip gate reads the SAME report shape as before C3.
    status = {"strategies": strategy_status_from_report(report)}
    assert maintenance_skip_reason(status, "a").startswith("maintenance_paused:")


def test_maintenance_is_idempotent_across_sweeps():
    mgr, cat = _mgr({"weak": AlphaState.PAPER_TRADING})
    evidence = {"weak": {"backtest": {"ic": 0.04, "return": 0.10},
                         "live": {"ic": 0.005, "return": 0.02}}}
    first = run_maintenance(mgr, AGENT, timestamp=3, health_by_alpha=evidence)
    second = run_maintenance(mgr, AGENT, timestamp=4, health_by_alpha=evidence)
    assert first["weak"]["action"] == "DEGRADED"
    assert "weak" not in second                            # converged, no churn
    assert cat.health("weak").state is HealthState.DEGRADED


def test_legacy_degraded_alpha_stays_in_the_sweep_set():
    mgr, cat = _mgr({"d": AlphaState.DEGRADED})
    # A pre-C3 DEGRADED row: deployment position lost to the mixed field.
    assert cat.deployment_state("d") is DeploymentState.UNKNOWN_LEGACY
    report = run_maintenance(mgr, AGENT, timestamp=5, health_by_alpha={
        "d": {"backtest": {"ic": 0.04, "return": 0.1, "slippage_bps": 5.0},
              "live": {"ic": 0.04, "return": 0.1, "slippage_bps": 25.0}},
    })
    assert report["d"]["action"] == "PAUSED"               # still monitored
    assert cat.state("d") is AlphaState.PAUSED


def test_maintenance_rejects_legacy_states_in_the_sweep_parameter():
    mgr, _ = _mgr({"a": AlphaState.PAPER_TRADING})
    with pytest.raises(TypeError, match="DeploymentState"):
        run_maintenance(mgr, AGENT, timestamp=1,
                        live_states=(AlphaState.PAPER_TRADING,))


def test_retirement_sweep_uses_deployment_states_and_still_retires():
    mgr, cat = _mgr({"decayed": AlphaState.PAPER_TRADING,
                     "bench": AlphaState.TESTING})
    report = run_maintenance(mgr, AGENT, timestamp=9, decay_evidence_by_alpha={
        "decayed": {"decay": DecayReport(alpha_id="decayed", decayed=True)},
        "bench": {"decay": DecayReport(alpha_id="bench", decayed=True)},
    })
    assert report["decayed"]["action"] == "RETIRED"
    assert "bench" not in report                           # OFFLINE, not swept
    triple = cat.triple("decayed")
    assert triple.research is ResearchState.RESEARCH_RETIRED
    assert triple.deployment is DeploymentState.CLOSED


def test_divergent_triple_survives_persistence_round_trip():
    mgr, cat = _mgr({"a": AlphaState.PAPER_TRADING})
    mgr.advance_health(
        "a", HealthVerdict(HealthState.DEGRADED, HealthReason.SIGNAL_DECAY),
        AGENT, timestamp=7,
    )
    store = SqliteExperimentStore(":memory:", create=True)
    reg = ExperimentRegistry(store)
    reg.save(cat, now=1)

    dst = AlphaCatalog()
    reg.load_into(dst)
    triple = dst.triple("a")
    assert triple.research is ResearchState.VALIDATED
    assert triple.deployment is DeploymentState.PAPER      # NOT the projection's
    assert triple.health == HealthVerdict(               #  UNKNOWN_LEGACY/DEGRADED
        HealthState.DEGRADED, HealthReason.SIGNAL_DECAY
    )
    # The research-challenged marker round-trips too: C3 never recorded
    # challenge history, so the marker honestly persists.
    assert triple.lossy == ("research_challenged",)
    assert dst.state("a") is AlphaState.DEGRADED         # legacy path intact
