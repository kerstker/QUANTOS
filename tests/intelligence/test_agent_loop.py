"""Phase 11 research loop: evaluation is diagnostic without signed authority."""
import pytest

from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition, AlphaEvaluation
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.alpha.panel_backtest import PanelPeriod
from services.intelligence import agent_loop as agent_loop_module
from services.intelligence.agent_loop import ResearchAgentLoop
from services.intelligence.guardrails import ForbiddenAgentAction
from services.research.ports.persistence import TrialPersistenceError
from services.validation.selection_authority import SelectionAuthorityBlocked
from tests.alpha.fixtures import catalog_bound_evaluation


class _StubEval:
    def __init__(self, catalog, verdict):
        self.catalog, self.verdict = catalog, verdict

    def prepare_catalog_observation(self, alpha_id, periods):
        del periods
        self.catalog.set_returns(alpha_id, (0.011, 0.0, 0.021, -0.01))

    def evaluate(self, alpha_id, periods, **k):
        pbo_evidence = k.get("pbo_evidence")
        if self.catalog.evidence(alpha_id) is None:
            return AlphaEvaluation(
                alpha_id=alpha_id,
                verdict=self.verdict,
                catalog_trial_count=self.catalog.global_trial_count(),
                catalog_trial_set_hash=self.catalog.trial_set_hash(),
            )
        return catalog_bound_evaluation(
            self.catalog,
            alpha_id,
            verdict=self.verdict,
            pbo_evidence=pbo_evidence,
        )


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


def _diagnostic_run(loop, candidates, **kwargs):
    return loop.run(candidates, diagnostic_only=True, **kwargs)


def test_public_loop_blocks_before_trial_or_catalog_registration():
    recorded = []
    cat = AlphaCatalog(trial_recorder=recorded.append)
    loop = ResearchAgentLoop(
        catalog=cat,
        evaluator=_StubEval(cat, ValidationVerdict.PASS),
    )
    with pytest.raises(SelectionAuthorityBlocked, match="INVALIDATED_PERP_LEDGER_V1"):
        loop.run(_cands(1))
    assert recorded == []
    assert cat.all_ids() == []


def test_loop_keeps_pass_diagnostic_and_review_queue_empty_without_authority():
    loop = _loop(ValidationVerdict.PASS)
    out = _diagnostic_run(loop, _cands(2))
    assert all(loop.catalog.state(f"a{i}") == AlphaState.DRAFT for i in range(2))
    assert out["human_review_queue"] == []
    assert all(row["diagnostic_only"] is True for row in out["report"].values())
    assert all(row["selection_authority"] is False for row in out["report"].values())
    assert all(
        row["invalidation_code"] == "INVALIDATED_PERP_LEDGER_V1"
        for row in out["report"].values()
    )
    assert all(loop.catalog.state(a) != AlphaState.APPROVED for a in loop.catalog.all_ids())  # never approved


def test_loop_keeps_fail_diagnostic_without_authority():
    loop = _loop(ValidationVerdict.FAIL)
    out = _diagnostic_run(loop, _cands(1))
    assert loop.catalog.state("a0") == AlphaState.DRAFT
    assert out["report"]["a0"]["verdict"] == ValidationVerdict.FAIL.value


def test_caller_boolean_and_self_described_record_cannot_restore_selection():
    loop = _loop(ValidationVerdict.PASS)
    out = _diagnostic_run(
        loop,
        _cands(1),
        selection_authorized=True,
        authoritative_backtest_record={"detail": {"evidence_status": "AUTHORITATIVE"}},
    )
    assert loop.catalog.state("a0") == AlphaState.DRAFT
    assert out["human_review_queue"] == []
    assert out["report"]["a0"]["selection_authority"] is False


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

        def prepare_catalog_observation(self, alpha_id, periods):
            del periods
            self.catalog.set_returns(
                alpha_id, (0.011, 0.0, 0.021, -0.01)
            )

        def evaluate(self, alpha_id, periods, **k):
            if alpha_id == "a1":
                raise RuntimeError("boom")
            pbo_evidence = k.get("pbo_evidence")
            return catalog_bound_evaluation(
                self.catalog,
                alpha_id,
                verdict=ValidationVerdict.PASS,
                pbo_evidence=pbo_evidence,
            )

    loop = ResearchAgentLoop(catalog=cat, evaluator=_FlakyEval(cat))
    out = _diagnostic_run(loop, _cands(2))                 # a0 ok, a1's eval raises
    assert "error" in out["report"]["a1"]                 # recorded, not propagated
    assert cat.state("a0") == AlphaState.DRAFT             # evaluated but not selected
    assert out["human_review_queue"] == []
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
    _diagnostic_run(loop, candidates)


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
        _diagnostic_run(loop, _cands(1))
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

    def prepare_catalog_observation(self, alpha_id, periods):
        del periods
        self.catalog.set_returns(alpha_id, (0.011, 0.0, 0.021, -0.01))

    def evaluate(self, alpha_id, periods, *, leakage_ok=None, regime_profitable_frac=None,
                 pbo_evidence=None, **k):
        self.seen[alpha_id] = {
            "leakage_ok": leakage_ok,
            "regime": regime_profitable_frac,
            "pbo_evidence": pbo_evidence,
        }
        verdict = (ValidationVerdict.PASS if leakage_ok is True
                   else ValidationVerdict.REQUIRES_HUMAN_REVIEW)
        return catalog_bound_evaluation(
            self.catalog,
            alpha_id,
            verdict=verdict,
            pbo_evidence=pbo_evidence,
        )


def test_real_gate_evidence_does_not_substitute_for_selection_authority():
    # Leakage/regime/PBO evidence still reaches the evaluator, but it is not an
    # independently controlled signed backtest authority and cannot select.
    cat = AlphaCatalog()
    ev = _EvidenceEval(cat)
    loop = ResearchAgentLoop(catalog=cat, evaluator=ev)
    out = _diagnostic_run(
        loop,
        _cands(2),
        evidence={"a0": {"leakage_ok": True, "regime_profitable_frac": 0.8}},
    )
    assert cat.state("a0") == AlphaState.DRAFT
    assert out["human_review_queue"] == []
    assert out["report"]["a0"]["diagnostic_only"] is True
    assert ev.seen["a0"]["leakage_ok"] is True and ev.seen["a0"]["regime"] == 0.8  # evidence threaded
    assert ev.seen["a0"]["pbo_evidence"] is not None
    assert cat.state("a0") != AlphaState.APPROVED                                  # containment intact
    first = out["report"]["a0"]["evaluation"].catalog_evidence
    second = out["report"]["a1"]["evaluation"].catalog_evidence
    assert first is not None and second is not None
    assert first.catalog_snapshot_hash == second.catalog_snapshot_hash


def test_loop_stays_fail_closed_without_evidence():
    # no evidence supplied -> leakage_ok None and the lifecycle remains untouched.
    cat = AlphaCatalog()
    loop = ResearchAgentLoop(catalog=cat, evaluator=_EvidenceEval(cat))
    out = _diagnostic_run(loop, _cands(2))
    assert cat.state("a0") == AlphaState.DRAFT
    assert out["human_review_queue"] == []
    assert cat.state("a0") != AlphaState.APPROVED
