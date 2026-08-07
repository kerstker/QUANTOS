from dataclasses import dataclass
import json
import sqlite3
import threading

import pytest

from services.research.trial_ledger import TrialLedger
from services.research.validation_dag import (
    StageEvidence,
    ValidationPolicy,
    ValidationStage,
    run_validation_dag,
)


@dataclass(frozen=True)
class Candidate:
    alpha_id: str
    family_id: str
    exact_definition_id: str
    validation_policy_digest: str | None = None


class Stage:
    def __init__(self, name, outcome="PASS", *, calls=None, payload=None):
        self.name = name
        self.implementation_id = f"tests.Stage:{name}:1"
        self.version = "1"
        self.outcome = outcome
        self.calls = calls if calls is not None else []
        self.payload = payload or {"ok": True}

    def evaluate(self, context):
        self.calls.append(self.name)
        return StageEvidence(
            stage=self.name,
            version=self.version,
            outcome=self.outcome,
            evidence=self.payload,
            implementation_id=self.implementation_id,
        )

class ExplodingStage(Stage):
    def evaluate(self, context):
        self.calls.append(self.name)
        raise RuntimeError("provider failed")


def make_policy(*stages):
    return ValidationPolicy(stages=tuple(stages))


def make_candidate(policy=None):
    return Candidate("a" * 64, "f" * 64, "e" * 64, policy.digest if policy else None)


def test_fixed_order_and_artifact_bindings(tmp_path):
    calls = []
    stages = tuple(Stage(name, calls=calls) for name in ("cheap", "oos", "realism"))
    policy = make_policy(*stages)
    candidate = make_candidate(policy)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        artifact = run_validation_dag(candidate, {"rows": [1]}, policy, ledger, event_at=1)

    assert calls == ["cheap", "oos", "realism"]
    assert artifact.verdict == "PASS"
    assert artifact.alpha_id == candidate.alpha_id
    assert artifact.panel_digest
    assert artifact.policy_digest == policy.digest
    assert artifact.stage_evidence_hashes.keys() == {"cheap", "oos", "realism"}
    assert artifact.effective_trial_count == 1


def test_cheap_block_short_circuits_later_stages(tmp_path):
    calls = []
    stages = tuple(Stage(name, "BLOCK" if name == "cheap" else "PASS", calls=calls)
                   for name in ("cheap", "oos", "realism"))
    policy = make_policy(*stages)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        artifact = run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1)
    assert artifact.verdict == "BLOCK"
    assert calls == ["cheap"]


def test_provider_exception_blocks_and_is_recorded(tmp_path):
    stages = (ExplodingStage("cheap"), Stage("oos"), Stage("realism"))
    policy = make_policy(*stages)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        artifact = run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1)
        assert artifact.verdict == "BLOCK"
        assert artifact.reason == "provider exception: RuntimeError"
        assert ledger.load(artifact.trial_id).status == "FAILED"


def test_missing_or_invalid_evidence_blocks(tmp_path):
    class MissingEvidence(Stage):
        def evaluate(self, context):
            self.calls.append(self.name)
            return None

    stage = MissingEvidence("cheap")
    policy = make_policy(stage, Stage("oos"), Stage("realism"))
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        artifact = run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1)
    assert artifact.verdict == "BLOCK"
    assert artifact.stage_evidence_hashes["cheap"]


def test_policy_hash_mismatch_rejected_before_provider(tmp_path):
    cheap = Stage("cheap")
    policy = make_policy(cheap, Stage("oos"), Stage("realism"))
    candidate = Candidate("a" * 64, "f" * 64, "e" * 64, "0" * 64)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        with pytest.raises(ValueError, match="policy"):
            run_validation_dag(candidate, {}, policy, ledger, event_at=1)
    assert cheap.calls == []


def test_zero_passers_is_valid_and_retry_is_idempotent(tmp_path):
    calls = []
    stages = tuple(Stage(name, "BLOCK", calls=calls) for name in ("cheap", "oos", "realism"))
    policy = make_policy(*stages)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        first = run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1)
        second = run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1)
        assert first == second
        assert calls == ["cheap"]
        assert len(ledger.load(first.trial_id).events) == 3


def test_default_policy_rejects_arbitrary_pass_mapping(tmp_path):
    policy = ValidationPolicy()
    candidate = make_candidate(policy)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        artifact = run_validation_dag(candidate, {
            "cheap": {"outcome": "PASS", "evidence": {"arbitrary": True}},
            "oos": {"outcome": "PASS", "evidence": {"arbitrary": True}},
            "realism": {"outcome": "PASS", "evidence": {"arbitrary": True}},
        }, ValidationPolicy(), ledger, event_at=1)
    assert artifact.verdict == "BLOCK"


def test_stage_version_mismatch_blocks_without_stale_evidence(tmp_path):
    class Stale(Stage):
        def evaluate(self, context):
            return StageEvidence(self.name, "0", "PASS", {"ok": True})
    policy = make_policy(Stale("cheap"), Stage("oos"), Stage("realism"))
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        artifact = run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1)
    assert artifact.verdict == "BLOCK"


def test_exception_artifact_is_stable_across_ledgers(tmp_path):
    policy = make_policy(ExplodingStage("cheap"), Stage("oos"), Stage("realism"))
    first_path, second_path = tmp_path / "a.db", tmp_path / "b.db"
    with TrialLedger(str(first_path)) as ledger:
        first = run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1)
    with TrialLedger(str(second_path)) as ledger:
        second = run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1)
    assert first.trial_id == second.trial_id
    assert first.stage_evidence_hashes == second.stage_evidence_hashes
    assert first.reason == second.reason


def test_non_string_mapping_keys_are_rejected(tmp_path):
    policy = make_policy(Stage("cheap"), Stage("oos"), Stage("realism"))
    with pytest.raises(TypeError, match="string"):
        run_validation_dag(make_candidate(policy), {1: "collision"}, policy,
                           TrialLedger(str(tmp_path / "ledger.db")), event_at=1)


def test_definition_identity_drift_is_rejected(tmp_path):
    class DefinitionCandidate(Candidate):
        definition = object()
    policy = make_policy(Stage("cheap"), Stage("oos"), Stage("realism"))
    candidate = DefinitionCandidate("a" * 64, "f" * 64, "e" * 64, policy.digest)
    with pytest.raises(ValueError, match="alpha_id"):
        with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
            run_validation_dag(candidate, {}, policy, ledger, event_at=1)


def test_concurrent_calls_only_execute_each_stage_once(tmp_path):
    calls = []
    stages = tuple(Stage(name, calls=calls) for name in ("cheap", "oos", "realism"))
    policy = make_policy(*stages)
    candidate = make_candidate(policy)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        results = []
        errors = []
        def worker():
            try:
                results.append(run_validation_dag(candidate, {}, policy, ledger, event_at=1))
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
    assert not errors
    assert len({result.trial_id for result in results}) == 1
    assert calls == ["cheap", "oos", "realism"]


def test_settings_and_evidence_are_deeply_immutable(tmp_path):
    settings = {"nested": {"values": [1]}}
    policy = make_policy(Stage("cheap"), Stage("oos"), Stage("realism"))
    frozen = ValidationPolicy(policy.stages, settings)
    with pytest.raises(TypeError):
        frozen.settings["nested"]["values"] = (2,)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        artifact = run_validation_dag(make_candidate(frozen), {}, frozen, ledger, event_at=1)
    with pytest.raises(TypeError):
        artifact.stage_evidence_hashes["cheap"] = "0" * 64


def test_exception_hash_ignores_message(tmp_path):
    class Boom(Stage):
        def __init__(self, message):
            super().__init__("cheap")
            self.message = message
        def evaluate(self, context):
            raise RuntimeError(self.message)
    def run(path, message):
        policy = make_policy(Boom(message), Stage("oos"), Stage("realism"))
        with TrialLedger(str(path)) as ledger:
            return run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1)
    first = run(tmp_path / "a.db", "one")
    second = run(tmp_path / "b.db", "two")
    assert first.stage_evidence_hashes == second.stage_evidence_hashes
    assert first.reason == second.reason


def test_production_adapters_full_pass_calls_every_engine(tmp_path, monkeypatch):
    from quantos.contracts.data import Bar
    from quantos.contracts.feature import LeakageCheckResult
    from quantos.contracts.cost import CostModel
    from quantos.contracts.validation import BatchPboEvidence, PboAlignment
    from quantos.contracts.hashing import stable_hash
    from services.alpha.family_testing import FamilyAssignment, HierarchicalFdrResult
    from services.research.validation_dag import register_leakage_compute, production_adapters
    import services.features.leakage as leakage
    import services.validation.walk_forward as wfmod
    import services.validation.deflated_sharpe as dsrmod
    import services.validation.pbo_evidence as pbomod
    import services.alpha.multiple_testing as mtmod
    import services.alpha.family_testing as fammod
    import services.research.realism.capacity_curve as capmod
    import services.research.realism.tail_risk as tailmod
    import services.validation.regime as regmod
    calls = {k: 0 for k in ("leakage", "walk", "purged", "dsr", "pbo", "pbo_verify", "bh", "bhy", "family", "cost", "impact", "capacity", "regime_labels", "regime_stats", "regime_prof", "var", "hill", "lower")}
    def compute(rows): return []
    compute.__name__ = "registered_compute"
    registered = register_leakage_compute("tests.registered_compute:1", compute)
    monkeypatch.setattr(leakage, "detect_leakage", lambda *a, **k: (calls.__setitem__("leakage", calls["leakage"] + 1) or LeakageCheckResult(feature_id="x", passed=True)))
    monkeypatch.setattr(wfmod, "walk_forward_splits", lambda *a, **k: (calls.__setitem__("walk", calls["walk"] + 1) or [([0], [1])]))
    monkeypatch.setattr(wfmod, "purged_kfold", lambda *a, **k: (calls.__setitem__("purged", calls["purged"] + 1) or [([0], [1])]))
    monkeypatch.setattr(dsrmod, "deflated_sharpe_ratio", lambda *a, **k: (calls.__setitem__("dsr", calls["dsr"] + 1) or .99))
    monkeypatch.setattr(dsrmod, "pbo_cscv", lambda *a, **k: (calls.__setitem__("pbo", calls["pbo"] + 1) or .1))
    evidence = BatchPboEvidence(candidate_definition_hashes=("8032d111eff0e85a23a228312e35d88cc09a3a2fc34e418ef7c5f22047f6934e", "7e6bccdfaa7fb1598a877741379d3f21d572a9e987984644aee3e041c7347a08"), performance_matrix=((.1, .2), (.2, .3), (.3, .4), (.4, .5)), alignment=PboAlignment.POSITIONAL_LEGACY, aligned_period_count=4, aligned_event_times=(), aligned_event_times_sha256=stable_hash({"alignment": "POSITIONAL_LEGACY", "aligned_period_count": 4}), pbo=.1)
    monkeypatch.setattr(pbomod, "create_batch_pbo_evidence", lambda **k: (calls.__setitem__("pbo", calls["pbo"] + 1) or evidence))
    monkeypatch.setattr(pbomod, "verify_batch_pbo_evidence", lambda x: (calls.__setitem__("pbo_verify", calls["pbo_verify"] + 1) or True))
    monkeypatch.setattr(mtmod, "benjamini_hochberg", lambda *a, **k: (calls.__setitem__("bh", calls["bh"] + 1) or {0}))
    monkeypatch.setattr(mtmod, "benjamini_hochberg_yekutieli", lambda *a, **k: (calls.__setitem__("bhy", calls["bhy"] + 1) or {0}))
    monkeypatch.setattr(fammod, "hierarchical_fdr", lambda **k: (calls.__setitem__("family", calls["family"] + 1) or HierarchicalFdrResult(frozenset({"alpha-0"}), frozenset({"family-a"}), {"family-a": .01}, {"family-a": .1}, 1, {})))
    monkeypatch.setattr(capmod, "capacity_band", lambda *a, **k: (calls.__setitem__("capacity", calls["capacity"] + 1) or {"aum_low": 10.0, "aum_high": 20.0}))
    monkeypatch.setattr(tailmod, "historical_var_cvar", lambda *a, **k: (calls.__setitem__("var", calls["var"] + 1) or {"var": .1, "cvar": .2}))
    monkeypatch.setattr(tailmod, "hill_tail_index", lambda *a, **k: (calls.__setitem__("hill", calls["hill"] + 1) or 2.0))
    monkeypatch.setattr(tailmod, "lower_tail_dependence", lambda *a, **k: (calls.__setitem__("lower", calls["lower"] + 1) or .1))
    monkeypatch.setattr(regmod, "label_regimes_by_vol", lambda *a, **k: (calls.__setitem__("regime_labels", calls["regime_labels"] + 1) or [0, 1]))
    monkeypatch.setattr(regmod, "regime_conditional_stats", lambda *a, **k: (calls.__setitem__("regime_stats", calls["regime_stats"] + 1) or {"0": {"sharpe": .1}}))
    monkeypatch.setattr(regmod, "regime_profitable_frac", lambda *a, **k: (calls.__setitem__("regime_prof", calls["regime_prof"] + 1) or .5))
    original_trade_cost=CostModel.trade_cost; original_impact_bps=CostModel.impact_bps
    monkeypatch.setattr(CostModel, "trade_cost", lambda self, *a, **k: (calls.__setitem__("cost", calls["cost"] + 1) or original_trade_cost(self, *a, **k)))
    monkeypatch.setattr(CostModel, "impact_bps", lambda self, *a, **k: (calls.__setitem__("impact", calls["impact"] + 1) or original_impact_bps(self, *a, **k)))
    from services.research.validation_dag import ValidationContext, ValidationPolicy, _digest, _canonical
    definitions=(
        {"candidate_id": "alpha-0", "definition": {"name": "candidate-0", "version": 1}},
        {"candidate_id": "alpha-1", "definition": {"name": "candidate-1", "version": 1}},
    )
    definition_hashes=tuple(_digest({"canonical_definition": _canonical(item["definition"])}) for item in definitions)
    panel = {"cheap": {"feature_id": "x", "compute": registered, "bars": (Bar(event_time=0, knowledge_time=0, instrument_id="BTC", open=1, high=1, low=1, close=1),), "target_event_time": 0}, "oos": {"n": 10, "train_size": 3, "test_size": 2, "returns": (.1, .2), "observed_sr": 2.0, "n_obs": 20, "n_trials": 2, "var_sr": .1, "perf_matrix": ((.1, .2), (.2, .3), (.3, .4), (.4, .5)), "pvalues": (.001, .9), "candidate_ids": ("alpha-0", "alpha-1"), "family_pvalues": {"family-a": .001}, "families": FamilyAssignment({"alpha-0": "family-a", "alpha-1": "family-a"}, {"alpha-0": "declared", "alpha-1": "declared"}), "candidate_family": "family-a", "pbo_alignment": PboAlignment.POSITIONAL_LEGACY, "pbo_periods": 4, "pbo_candidate_definition_hashes": definition_hashes, "pbo_candidate_definitions": definitions}, "realism": {"gross_returns": (.1, .2), "turnover_by_period": (((.1, 10.0),),), "aum_grid": (10.0, 20.0), "impact_coef_lo_hi": (0.1, 0.2), "returns": (.1, .2), "regime_labels": (0, 1), "market_closes": tuple(float(i + 1) for i in range(25)), "benchmark_returns": (.1, .2), "cost_model": CostModel(), "trade_notional": 10.0, "trade_shares": 1.0, "adv_shares": 10.0}}
    policy = ValidationPolicy()
    panel["oos"]["candidate_id"] = "alpha-0"
    panel["oos"]["candidate_index"] = 0
    assert all(stage.evaluate(ValidationContext(None, panel, policy, "a" * 64, "b" * 64, policy.digest, 1)).outcome == "PASS" for stage in production_adapters())
    candidate=Candidate("a" * 64, "f" * 64, definition_hashes[0], policy.digest)
    with TrialLedger(str(tmp_path / "production.db")) as ledger:
        artifact=run_validation_dag(candidate, panel, policy, ledger, event_at=1, production=True)
        assert artifact.verdict == "PASS"
        assert set(artifact.stage_metadata) == {"cheap", "oos", "realism"}
        assert set(ledger.validation_receipt(artifact.trial_id)) >= {"cheap", "oos", "realism", "__artifact__"}
    assert all(v >= 1 for v in calls.values()), calls


def test_production_cheap_block_short_circuits_oos_realism(tmp_path):
    policy=ValidationPolicy(); candidate=make_candidate(policy)
    with TrialLedger(str(tmp_path / "production-block.db")) as ledger:
        artifact=run_validation_dag(candidate, {}, policy, ledger, event_at=1, production=True)
        assert artifact.verdict == "BLOCK"
        receipt=ledger.validation_receipt(artifact.trial_id)
    assert receipt["cheap"]["payload"]["outcome"] == "BLOCK"
    assert receipt["oos"]["payload"]["evidence"].get("short_circuit") is True
    assert receipt["realism"]["payload"]["evidence"].get("short_circuit") is True


def test_production_rejects_class_monkeypatch(tmp_path, monkeypatch):
    policy=ValidationPolicy(); candidate=make_candidate(policy)
    from services.research.validation_dag import CheapValidationAdapter
    original=CheapValidationAdapter.evaluate
    monkeypatch.setattr(CheapValidationAdapter, "evaluate", lambda self, context: StageEvidence("cheap", "1", "PASS", {"forged": True}, implementation_id=self.implementation_id))
    with TrialLedger(str(tmp_path / "sealed.db")) as ledger:
        with pytest.raises(ValueError, match="sealed"):
            run_validation_dag(candidate, {}, policy, ledger, event_at=1, production=True)
    assert CheapValidationAdapter.evaluate is not original



def test_production_rejects_forged_implementation_id(tmp_path):
    from services.research.validation_dag import CheapValidationAdapter
    with pytest.raises(ValueError, match="canonical"):
        CheapValidationAdapter("forged")


def test_artifact_event_at_tamper_fails_closed(tmp_path):
    policy=make_policy(Stage("cheap"), Stage("oos"), Stage("realism")); candidate=make_candidate(policy); path=str(tmp_path / "event-tamper.db")
    with TrialLedger(path) as ledger:
        artifact=run_validation_dag(candidate, {}, policy, ledger, event_at=10)
        con=sqlite3.connect(path); con.execute("DROP TRIGGER validation_receipts_no_update")
        row=con.execute("SELECT payload_json FROM validation_receipt_events WHERE trial_id=? AND stage='__artifact__'", (artifact.trial_id,)).fetchone()
        payload=json.loads(row[0]); payload["event_at"]=11
        raw=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=True)
        con.execute("UPDATE validation_receipt_events SET payload_json=?, payload_hash=? WHERE trial_id=? AND stage='__artifact__'", (raw, __import__('hashlib').sha256(raw.encode()).hexdigest(), artifact.trial_id))
        con.commit(); con.close()
        with pytest.raises(Exception): ledger.load_validation_artifact(artifact.trial_id)

def test_event_at_change_never_reuses_artifact(tmp_path):
    policy=make_policy(Stage("cheap"), Stage("oos"), Stage("realism")); candidate=make_candidate(policy)
    with TrialLedger(str(tmp_path / "events.db")) as ledger:
        first=run_validation_dag(candidate, {}, policy, ledger, event_at=10)
        second=run_validation_dag(candidate, {}, policy, ledger, event_at=20)
    assert first.trial_id != second.trial_id


def test_concurrent_cheap_block_returns_same_artifact(tmp_path):
    calls=[]; stages=tuple(Stage(name, "BLOCK" if name == "cheap" else "PASS", calls=calls) for name in ("cheap", "oos", "realism")); policy=make_policy(*stages); candidate=make_candidate(policy)
    with TrialLedger(str(tmp_path / "concurrent-block.db")) as ledger:
        results=[]; errors=[]
        def worker():
            try: results.append(run_validation_dag(candidate, {}, policy, ledger, event_at=1))
            except Exception as exc: errors.append(exc)
        threads=[threading.Thread(target=worker) for _ in range(4)]
        [x.start() for x in threads]; [x.join() for x in threads]
    assert not errors and len(results) == 4 and len({x.trial_id for x in results}) == 1 and all(x.verdict == "BLOCK" for x in results)



def test_cross_connection_slow_stage_single_side_effect(tmp_path):
    import time
    calls=[]
    class Slow(Stage):
        def evaluate(self, context):
            calls.append(self.name)
            time.sleep(0.35)
            return super().evaluate(context)
    policy=make_policy(Slow("cheap"), Stage("oos"), Stage("realism")); candidate=make_candidate(policy); path=str(tmp_path / "cross.db")
    l1=TrialLedger(path); l2=TrialLedger(path); results=[]; errors=[]
    def worker(ledger):
        try: results.append(run_validation_dag(candidate, {}, policy, ledger, event_at=1))
        except Exception as exc: errors.append(exc)
    import threading
    t1=threading.Thread(target=worker,args=(l1,)); t2=threading.Thread(target=worker,args=(l2,)); t1.start(); t2.start(); t1.join(); t2.join(); l1.close(); l2.close()
    assert not errors and len(results)==2 and results[0] == results[1] and calls.count("cheap") == 1

def test_terminal_receipts_rebuild_artifact_without_provider_rerun(tmp_path):
    calls=[]; stages=tuple(Stage(name, calls=calls) for name in ("cheap", "oos", "realism")); policy=make_policy(*stages); candidate=make_candidate(policy); path=str(tmp_path / "crash.db")
    with TrialLedger(path) as ledger:
        first=run_validation_dag(candidate, {}, policy, ledger, event_at=1)
        con=sqlite3.connect(path); con.execute("DROP TRIGGER validation_receipts_no_delete"); con.execute("DELETE FROM validation_receipt_events WHERE stage='__artifact__'"); con.execute("UPDATE validation_receipt_heads SET event_count=(SELECT count(*) FROM validation_receipt_events), head_hash=(SELECT coalesce((SELECT row_hash FROM validation_receipt_events ORDER BY global_seq DESC LIMIT 1),'') FROM validation_receipt_events LIMIT 1)"); con.commit(); con.close()
        before=list(calls); second=run_validation_dag(candidate, {}, policy, ledger, event_at=1)
    assert second == first and calls == before
