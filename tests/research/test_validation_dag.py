from dataclasses import dataclass

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
