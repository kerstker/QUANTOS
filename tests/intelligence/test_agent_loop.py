"""Phase 11 — research agent loop: advances to PAPER_TRADING + human handoff,
and the agent can NEVER approve or reset the kill-switch."""
import pytest

from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition, AlphaEvaluation
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.alpha.panel_backtest import PanelPeriod
from services.intelligence import agent_loop as agent_loop_module
from services.intelligence.agent_loop import ResearchAgentLoop
from services.intelligence.guardrails import ForbiddenAgentAction
from services.research.ports.persistence import TrialPersistenceError


class _StubEval:
    def __init__(self, catalog, verdict):
        self.catalog, self.verdict = catalog, verdict

    def evaluate(self, alpha_id, periods, **k):
        self.catalog.set_returns(alpha_id, (0.01, 0.0, 0.02, -0.01))
        pbo_evidence = k.get("pbo_evidence")
        return AlphaEvaluation(alpha_id=alpha_id, verdict=self.verdict, deflated_sharpe=0.8,
                               pbo=pbo_evidence.pbo if pbo_evidence is not None else 0.0,
                               pbo_evidence=pbo_evidence,
                               pbo_evidence_hash=(pbo_evidence.evidence_hash
                                                  if pbo_evidence is not None else ""),
                               catalog_trial_count=self.catalog.global_trial_count(),
                               catalog_trial_set_hash=self.catalog.trial_set_hash(),
                               marginal_ir=0.5)


def _loop(verdict):
    cat = AlphaCatalog()
    return ResearchAgentLoop(catalog=cat, evaluator=_StubEval(cat, verdict))


def _cands(n=2):
    candidates = []
    for i in range(n):
        periods = [
            PanelPeriod(
                instruments=("x", "y", "z", "w"),
                signals=(1.0 + i, -1.0, 0.5, -0.5 - i),
                forward_returns=(0.01 * ((t + i) % 3), -0.005, 0.002, -0.001),
                event_time=t,
            )
            for t in range(12)
        ]
        candidates.append(
            (AlphaDefinition(alpha_id=f"a{i}", name=f"A{i}", factor_ids=(f"f{i}",)), periods)
        )
    return candidates


def test_loop_advances_pass_to_paper_and_queues_for_human():
    loop = _loop(ValidationVerdict.PASS)
    out = loop.run(_cands(2))
    assert all(loop.catalog.state(f"a{i}") == AlphaState.PAPER_TRADING for i in range(2))
    assert set(out["human_review_queue"]) == {"a0", "a1"}
    assert all(loop.catalog.state(a) != AlphaState.APPROVED for a in loop.catalog.all_ids())  # never approved


def test_loop_rejects_fail():
    loop = _loop(ValidationVerdict.FAIL)
    loop.run(_cands(1))
    assert loop.catalog.state("a0") == AlphaState.REJECTED


def test_agent_cannot_approve_or_reset_via_loop():
    loop = _loop(ValidationVerdict.PASS)
    with pytest.raises(ForbiddenAgentAction):
        loop.try_approve("a0")
    with pytest.raises(ForbiddenAgentAction):
        loop.try_reset_kill_switch(None)


def test_agent_can_only_request_human_approval():
    loop = _loop(ValidationVerdict.PASS)
    assert loop.request_human_approval("a0") == "APPROVAL_REQUESTED:a0"


def test_loop_isolates_a_failing_candidate():
    cat = AlphaCatalog()

    class _FlakyEval:
        def __init__(self, catalog):
            self.catalog = catalog

        def evaluate(self, alpha_id, periods, **k):
            if alpha_id == "a1":
                raise RuntimeError("boom")
            self.catalog.set_returns(alpha_id, (0.01, 0.0, 0.02, -0.01))
            pbo_evidence = k.get("pbo_evidence")
            return AlphaEvaluation(alpha_id=alpha_id, verdict=ValidationVerdict.PASS, deflated_sharpe=0.8,
                                   pbo=pbo_evidence.pbo,
                                   pbo_evidence=pbo_evidence,
                                   pbo_evidence_hash=pbo_evidence.evidence_hash,
                                   catalog_trial_count=self.catalog.global_trial_count(),
                                   catalog_trial_set_hash=self.catalog.trial_set_hash(),
                                   marginal_ir=0.5)

    loop = ResearchAgentLoop(catalog=cat, evaluator=_FlakyEval(cat))
    out = loop.run(_cands(2))                              # a0 ok, a1's eval raises
    assert "error" in out["report"]["a1"]                 # recorded, not propagated
    assert cat.state("a0") == AlphaState.PAPER_TRADING     # a0 still fully processed (not orphaned)
    assert "a0" in out["human_review_queue"]
    assert cat.state("a1") != AlphaState.APPROVED          # failed alpha never advanced (fail-closed)


def test_complete_batch_is_recorded_before_selection_pbo(monkeypatch):
    recorded = []
    cat = AlphaCatalog(trial_recorder=recorded.append)
    loop = ResearchAgentLoop(
        catalog=cat,
        evaluator=_StubEval(cat, ValidationVerdict.REQUIRES_HUMAN_REVIEW),
    )
    candidates = _cands(2)

    def assert_recorded_before_backtest(received, **_kwargs):
        assert tuple(received) == tuple(candidates)
        assert recorded == [definition.definition_hash for definition, _ in candidates]
        assert cat.global_trial_count() == len(candidates)
        return None

    monkeypatch.setattr(agent_loop_module, "batch_pbo", assert_recorded_before_backtest)
    loop.run(candidates)


def test_trial_persistence_failure_prevents_selection_pbo(monkeypatch):
    pbo_called = False

    def fail_recording(_config_hash):
        raise TrialPersistenceError("ledger unavailable")

    def observe_pbo(*_args, **_kwargs):
        nonlocal pbo_called
        pbo_called = True

    cat = AlphaCatalog(trial_recorder=fail_recording)
    loop = ResearchAgentLoop(
        catalog=cat,
        evaluator=_StubEval(cat, ValidationVerdict.REQUIRES_HUMAN_REVIEW),
    )
    monkeypatch.setattr(agent_loop_module, "batch_pbo", observe_pbo)

    with pytest.raises(TrialPersistenceError, match="ledger unavailable"):
        loop.run(_cands(1))
    assert pbo_called is False


def test_loop_must_run_as_non_human():
    with pytest.raises(ValueError):
        ResearchAgentLoop(actor=Actor(actor_id="alice", kind=ActorKind.HUMAN))


class _EvidenceEval:
    """Evaluator that PASSes only when real gauntlet evidence (leakage_ok=True) is supplied — exactly the
    evaluator's fail-closed contract: un-supplied leakage stays REQUIRES_HUMAN_REVIEW. Records the kwargs it
    received so the test can assert the loop threads evidence (leakage/regime/PBO) into evaluate()."""
    def __init__(self, catalog):
        self.catalog, self.seen = catalog, {}

    def evaluate(self, alpha_id, periods, *, leakage_ok=None, regime_profitable_frac=None,
                 pbo_evidence=None, **k):
        self.seen[alpha_id] = {
            "leakage_ok": leakage_ok,
            "regime": regime_profitable_frac,
            "pbo_evidence": pbo_evidence,
        }
        self.catalog.set_returns(alpha_id, (0.01, 0.0, 0.02, -0.01))
        verdict = (ValidationVerdict.PASS if leakage_ok is True
                   else ValidationVerdict.REQUIRES_HUMAN_REVIEW)
        return AlphaEvaluation(
            alpha_id=alpha_id,
            verdict=verdict,
            deflated_sharpe=0.8,
            pbo=pbo_evidence.pbo if pbo_evidence is not None else 0.0,
            pbo_evidence=pbo_evidence,
            pbo_evidence_hash=(pbo_evidence.evidence_hash if pbo_evidence is not None else ""),
            catalog_trial_count=self.catalog.global_trial_count(),
            catalog_trial_set_hash=self.catalog.trial_set_hash(),
            marginal_ir=0.5,
        )


def test_loop_advances_when_real_evidence_is_supplied():
    # the fix: supplying real gauntlet evidence (leakage_ok=True + regime) lets a genuinely-passing alpha
    # advance TESTING -> VALIDATED -> PAPER_TRADING through the agent loop (the previously-frozen chain).
    cat = AlphaCatalog()
    ev = _EvidenceEval(cat)
    loop = ResearchAgentLoop(catalog=cat, evaluator=ev)
    out = loop.run(_cands(2), evidence={"a0": {"leakage_ok": True, "regime_profitable_frac": 0.8}})
    assert cat.state("a0") == AlphaState.PAPER_TRADING
    assert "a0" in out["human_review_queue"]
    assert ev.seen["a0"]["leakage_ok"] is True and ev.seen["a0"]["regime"] == 0.8  # evidence threaded
    assert ev.seen["a0"]["pbo_evidence"] is not None
    assert cat.state("a0") != AlphaState.APPROVED                                  # containment intact


def test_loop_stays_fail_closed_without_evidence():
    # no evidence supplied -> leakage_ok None -> REQUIRES_HUMAN_REVIEW -> parked in TESTING (unchanged).
    cat = AlphaCatalog()
    loop = ResearchAgentLoop(catalog=cat, evaluator=_EvidenceEval(cat))
    out = loop.run(_cands(2))
    assert cat.state("a0") == AlphaState.TESTING
    assert out["human_review_queue"] == []
    assert cat.state("a0") != AlphaState.APPROVED
