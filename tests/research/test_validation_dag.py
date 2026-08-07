from dataclasses import dataclass
import json
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
    validation_policy_digest: str | None = None


class Stage:
    def __init__(self, name, outcome="PASS", *, calls=None, payload=None):
        self.name = name
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
        )


class ExplodingStage(Stage):
    def evaluate(self, context):
        self.calls.append(self.name)
        raise RuntimeError("provider failed")


def make_policy(*stages):
    return ValidationPolicy(stages=tuple(stages))


def make_candidate(policy=None):
    return Candidate("a" * 64, "f" * 64, policy.digest if policy else None)


def test_fixed_order_and_artifact_bindings(tmp_path):
    calls = []
    stages = tuple(Stage(name, calls=calls) for name in ("cheap", "oos", "realism"))
    policy = make_policy(*stages)
    candidate = make_candidate(policy)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        artifact = run_validation_dag(candidate, {"rows": [1]}, policy, ledger)

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
        artifact = run_validation_dag(make_candidate(policy), {}, policy, ledger)
    assert artifact.verdict == "BLOCK"
    assert calls == ["cheap"]


def test_provider_exception_blocks_and_is_recorded(tmp_path):
    stages = (ExplodingStage("cheap"), Stage("oos"), Stage("realism"))
    policy = make_policy(*stages)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        artifact = run_validation_dag(make_candidate(policy), {}, policy, ledger)
        assert artifact.verdict == "BLOCK"
        assert "provider failed" in artifact.reason
        assert ledger.load(artifact.trial_id).status == "FAILED"


def test_missing_or_invalid_evidence_blocks(tmp_path):
    class MissingEvidence(Stage):
        def evaluate(self, context):
            self.calls.append(self.name)
            return None

    stage = MissingEvidence("cheap")
    policy = make_policy(stage, Stage("oos"), Stage("realism"))
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        artifact = run_validation_dag(make_candidate(policy), {}, policy, ledger)
    assert artifact.verdict == "BLOCK"
    assert artifact.stage_evidence_hashes["cheap"]


def test_policy_hash_mismatch_rejected_before_provider(tmp_path):
    cheap = Stage("cheap")
    policy = make_policy(cheap, Stage("oos"), Stage("realism"))
    candidate = Candidate("a" * 64, "f" * 64, "0" * 64)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        with pytest.raises(ValueError, match="policy"):
            run_validation_dag(candidate, {}, policy, ledger)
    assert cheap.calls == []


def test_zero_passers_is_valid_and_retry_is_idempotent(tmp_path):
    calls = []
    stages = tuple(Stage(name, "BLOCK", calls=calls) for name in ("cheap", "oos", "realism"))
    policy = make_policy(*stages)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        first = run_validation_dag(make_candidate(policy), {}, policy, ledger)
        second = run_validation_dag(make_candidate(policy), {}, policy, ledger)
        assert first == second
        assert calls == ["cheap"]
        assert len(ledger.load(first.trial_id).events) == 3


def test_default_policy_rejects_arbitrary_pass_mapping(tmp_path):
    candidate = make_candidate()
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        artifact = run_validation_dag(candidate, {
            "cheap": {"outcome": "PASS", "evidence": {"arbitrary": True}},
            "oos": {"outcome": "PASS", "evidence": {"arbitrary": True}},
            "realism": {"outcome": "PASS", "evidence": {"arbitrary": True}},
        }, ValidationPolicy(), ledger)
    assert artifact.verdict == "BLOCK"


def test_stage_version_mismatch_blocks_without_stale_evidence(tmp_path):
    class Stale(Stage):
        def evaluate(self, context):
            return StageEvidence(self.name, "0", "PASS", {"ok": True})
    policy = make_policy(Stale("cheap"), Stage("oos"), Stage("realism"))
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        artifact = run_validation_dag(make_candidate(policy), {}, policy, ledger)
    assert artifact.verdict == "BLOCK"


def test_exception_artifact_is_stable_across_ledgers(tmp_path):
    policy = make_policy(ExplodingStage("cheap"), Stage("oos"), Stage("realism"))
    first_path, second_path = tmp_path / "a.db", tmp_path / "b.db"
    with TrialLedger(str(first_path)) as ledger:
        first = run_validation_dag(make_candidate(policy), {}, policy, ledger)
    with TrialLedger(str(second_path)) as ledger:
        second = run_validation_dag(make_candidate(policy), {}, policy, ledger)
    assert first.trial_id == second.trial_id
    assert first.stage_evidence_hashes == second.stage_evidence_hashes
    assert first.reason == second.reason


def test_non_string_mapping_keys_are_rejected(tmp_path):
    policy = make_policy(Stage("cheap"), Stage("oos"), Stage("realism"))
    with pytest.raises(TypeError, match="string"):
        run_validation_dag(make_candidate(policy), {1: "collision"}, policy,
                           TrialLedger(str(tmp_path / "ledger.db")))


def test_definition_identity_drift_is_rejected(tmp_path):
    class DefinitionCandidate(Candidate):
        definition = object()
    candidate = DefinitionCandidate("a" * 64, "f" * 64)
    policy = make_policy(Stage("cheap"), Stage("oos"), Stage("realism"))
    with pytest.raises(ValueError, match="alpha_id"):
        with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
            run_validation_dag(candidate, {}, policy, ledger)


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
                results.append(run_validation_dag(candidate, {}, policy, ledger))
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
        artifact = run_validation_dag(make_candidate(frozen), {}, frozen, ledger)
    with pytest.raises(TypeError):
        artifact.stage_evidence_hashes["cheap"] = "0" * 64


def test_builtin_adapter_calls_engine_and_blocks_missing_input(tmp_path):
    from services.research.validation_dag import BuiltinEngineAdapter
    calls = []
    def engine(**inputs):
        calls.append(inputs)
        return {"score": 1}
    adapter = BuiltinEngineAdapter("cheap", "7", "engine.test:7", engine, "leakage")
    policy = make_policy(adapter, Stage("oos"), Stage("realism"))
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        passed = run_validation_dag(make_candidate(policy), {"engine_inputs": {"leakage": {"bars": [1]}}}, policy, ledger)
    assert calls == [{"bars": (1,)}]
    assert passed.verdict == "PASS"
    missing_policy = make_policy(BuiltinEngineAdapter("cheap", "7", "engine.test:7", engine, "leakage"), Stage("oos"), Stage("realism"))
    with TrialLedger(str(tmp_path / "missing.db")) as ledger:
        blocked = run_validation_dag(make_candidate(missing_policy), {}, missing_policy, ledger)
    assert blocked.verdict == "BLOCK"


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
            return run_validation_dag(make_candidate(policy), {}, policy, ledger)
    first = run(tmp_path / "a.db", "one")
    second = run(tmp_path / "b.db", "two")
    assert first.stage_evidence_hashes == second.stage_evidence_hashes
    assert first.reason != second.reason
