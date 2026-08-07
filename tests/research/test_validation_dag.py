from dataclasses import dataclass
import json
import sqlite3
import threading

import pytest

from services.research.trial_ledger import TrialLedger, TrialLedgerConflict, TrialLedgerError, TrialRecord
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
        artifact = run_validation_dag(candidate, {"rows": [1]}, policy, ledger, event_at=1, lease_now=1)

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
        artifact = run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1, lease_now=1)
    assert artifact.verdict == "BLOCK"
    assert calls == ["cheap"]


def test_provider_exception_blocks_and_is_recorded(tmp_path):
    stages = (ExplodingStage("cheap"), Stage("oos"), Stage("realism"))
    policy = make_policy(*stages)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        artifact = run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1, lease_now=1)
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
        artifact = run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1, lease_now=1)
    assert artifact.verdict == "BLOCK"
    assert artifact.stage_evidence_hashes["cheap"]


def test_policy_hash_mismatch_rejected_before_provider(tmp_path):
    cheap = Stage("cheap")
    policy = make_policy(cheap, Stage("oos"), Stage("realism"))
    candidate = Candidate("a" * 64, "f" * 64, "e" * 64, "0" * 64)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        with pytest.raises(ValueError, match="policy"):
            run_validation_dag(candidate, {}, policy, ledger, event_at=1, lease_now=1)
    assert cheap.calls == []


def test_zero_passers_is_valid_and_retry_is_idempotent(tmp_path):
    calls = []
    stages = tuple(Stage(name, "BLOCK", calls=calls) for name in ("cheap", "oos", "realism"))
    policy = make_policy(*stages)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        first = run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1, lease_now=1)
        second = run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1, lease_now=1)
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
        }, ValidationPolicy(), ledger, event_at=1, lease_now=1)
    assert artifact.verdict == "BLOCK"


def test_stage_version_mismatch_blocks_without_stale_evidence(tmp_path):
    class Stale(Stage):
        def evaluate(self, context):
            return StageEvidence(self.name, "0", "PASS", {"ok": True})
    policy = make_policy(Stale("cheap"), Stage("oos"), Stage("realism"))
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        artifact = run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1, lease_now=1)
    assert artifact.verdict == "BLOCK"


def test_exception_artifact_is_stable_across_ledgers(tmp_path):
    policy = make_policy(ExplodingStage("cheap"), Stage("oos"), Stage("realism"))
    first_path, second_path = tmp_path / "a.db", tmp_path / "b.db"
    with TrialLedger(str(first_path)) as ledger:
        first = run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1, lease_now=1)
    with TrialLedger(str(second_path)) as ledger:
        second = run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1, lease_now=1)
    assert first.trial_id == second.trial_id
    assert first.stage_evidence_hashes == second.stage_evidence_hashes
    assert first.reason == second.reason


def test_execution_snapshot_reuses_frozen_bar_instances():
    from quantos.contracts.data import Bar
    from services.research.validation_dag import _execution_snapshot

    bar = Bar(
        instrument_id="BTC-USD",
        event_time=1,
        knowledge_time=1,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1_000.0,
    )

    snapshot = _execution_snapshot({"bars": (bar,)})

    assert snapshot["bars"][0] is bar


def test_non_string_mapping_keys_are_rejected(tmp_path):
    policy = make_policy(Stage("cheap"), Stage("oos"), Stage("realism"))
    with pytest.raises(TypeError, match="string"):
        run_validation_dag(make_candidate(policy), {1: "collision"}, policy,
                           TrialLedger(str(tmp_path / "ledger.db")), event_at=1, lease_now=1)


def test_definition_identity_drift_is_rejected(tmp_path):
    class DefinitionCandidate(Candidate):
        definition = object()
    policy = make_policy(Stage("cheap"), Stage("oos"), Stage("realism"))
    candidate = DefinitionCandidate("a" * 64, "f" * 64, "e" * 64, policy.digest)
    with pytest.raises(ValueError, match="alpha_id"):
        with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
            run_validation_dag(candidate, {}, policy, ledger, event_at=1, lease_now=1)


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
                results.append(run_validation_dag(candidate, {}, policy, ledger, event_at=1, lease_now=1))
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        final = run_validation_dag(candidate, {}, policy, ledger, event_at=1, lease_now=1)
    assert all(isinstance(error, TrialLedgerConflict) for error in errors)
    assert all(result == final for result in results)
    assert calls == ["cheap", "oos", "realism"]


def test_in_progress_fails_fast_and_retry_reuses_artifact(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    class BlockingStage(Stage):
        def evaluate(self, context):
            entered.set()
            assert release.wait(timeout=2.0)
            return super().evaluate(context)

    policy = make_policy(BlockingStage("cheap"), Stage("oos"), Stage("realism"))
    candidate = make_candidate(policy)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        first_errors = []
        first_results = []
        first = threading.Thread(
            target=lambda: _capture_result(
                first_results,
                first_errors,
                lambda: run_validation_dag(
                    candidate, {}, policy, ledger, event_at=1, lease_now=1
                ),
            )
        )
        first.start()
        assert entered.wait(timeout=1.0)
        with pytest.raises(TrialLedgerConflict, match="in progress"):
            run_validation_dag(
                candidate,
                {},
                policy,
                ledger,
                event_at=1,
                lease_now=1,
            )
        release.set()
        first.join(timeout=2.0)
        retry = run_validation_dag(
            candidate, {}, policy, ledger, event_at=1, lease_now=1
        )

    assert not first_errors
    assert first_results == [retry]


def _capture_error(errors, operation):
    try:
        operation()
    except Exception as exc:
        errors.append(exc)


def _capture_result(results, errors, operation):
    try:
        results.append(operation())
    except Exception as exc:
        errors.append(exc)


def test_terminal_status_and_artifact_publish_roll_back_together(tmp_path, monkeypatch):
    calls = []
    policy = make_policy(*(Stage(name, calls=calls) for name in ("cheap", "oos", "realism")))
    candidate = make_candidate(policy)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        original_insert = ledger._receipt_insert_locked

        def fail_artifact_insert(trial_id, stage, *args, **kwargs):
            if stage == "__artifact__":
                raise sqlite3.OperationalError("injected artifact write failure")
            return original_insert(trial_id, stage, *args, **kwargs)

        monkeypatch.setattr(ledger, "_receipt_insert_locked", fail_artifact_insert)
        with pytest.raises(TrialLedgerError, match="validation artifact"):
            run_validation_dag(
                candidate, {}, policy, ledger, event_at=1, lease_now=1
            )

        trial_ids = {row[0] for row in ledger._rows()}
        assert len(trial_ids) == 1
        aggregate = ledger.load(trial_ids.pop())
        assert aggregate.status == "RUNNING"
        assert "__artifact__" not in ledger.validation_receipt(aggregate.trial_id)
        assert calls == ["cheap", "oos", "realism"]

        monkeypatch.setattr(ledger, "_receipt_insert_locked", original_insert)
        artifact = run_validation_dag(
            candidate, {}, policy, ledger, event_at=1, lease_now=2
        )
        assert artifact.verdict == "PASS"
        assert ledger.load(artifact.trial_id).status == "PASSED"
        assert calls == ["cheap", "oos", "realism"]


def test_settings_and_evidence_are_deeply_immutable(tmp_path):
    settings = {"nested": {"values": [1]}}
    policy = make_policy(Stage("cheap"), Stage("oos"), Stage("realism"))
    frozen = ValidationPolicy(policy.stages, settings)
    with pytest.raises(TypeError):
        frozen.settings["nested"]["values"] = (2,)
    with TrialLedger(str(tmp_path / "ledger.db")) as ledger:
        artifact = run_validation_dag(make_candidate(frozen), {}, frozen, ledger, event_at=1, lease_now=1)
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
            return run_validation_dag(make_candidate(policy), {}, policy, ledger, event_at=1, lease_now=1)
    first = run(tmp_path / "a.db", "one")
    second = run(tmp_path / "b.db", "two")
    assert first.stage_evidence_hashes == second.stage_evidence_hashes
    assert first.reason == second.reason


def test_production_adapters_full_pass_calls_every_engine(tmp_path, monkeypatch):
    from quantos.contracts.data import Bar
    from quantos.contracts.feature import FeatureValue, LeakageCheckResult
    from quantos.contracts.cost import CostModel
    from quantos.contracts.validation import BatchPboEvidence
    from quantos.contracts.hashing import stable_hash
    from services.alpha.family_testing import FamilyAssignment, HierarchicalFdrResult
    from services.research.validation_dag import register_leakage_compute, production_adapters
    import services.features.leakage as leakage
    import services.validation.walk_forward as wfmod
    import services.validation.deflated_sharpe as dsrmod
    import services.validation.catalog_evidence as catalogmod
    import services.validation.pbo_evidence as pbomod
    import services.alpha.multiple_testing as mtmod
    import services.alpha.family_testing as fammod
    import services.research.realism.capacity_curve as capmod
    import services.research.realism.tail_risk as tailmod
    import services.validation.regime as regmod
    calls = {k: 0 for k in ("leakage", "walk", "purged", "dsr", "pbo", "pbo_verify", "bh", "bhy", "family", "cost", "impact", "capacity", "regime_labels", "regime_stats", "regime_prof", "var", "hill", "lower")}
    def compute(rows):
        return [
            FeatureValue(
                feature_id="x",
                instrument_id=row.instrument_id,
                event_time=row.event_time,
                knowledge_time=row.knowledge_time,
                value=row.close,
            )
            for row in rows
        ]
    compute.__name__ = "registered_compute"
    monkeypatch.setattr(leakage, "detect_leakage", lambda *a, **k: (calls.__setitem__("leakage", calls["leakage"] + 1) or LeakageCheckResult(feature_id="x", passed=True)))
    monkeypatch.setattr(wfmod, "walk_forward_splits", lambda *a, **k: (calls.__setitem__("walk", calls["walk"] + 1) or [([0], [1, 2])]))
    monkeypatch.setattr(wfmod, "purged_kfold", lambda *a, **k: (calls.__setitem__("purged", calls["purged"] + 1) or [([0], [1, 2])]))
    monkeypatch.setattr(dsrmod, "deflated_sharpe_ratio", lambda *a, **k: (calls.__setitem__("dsr", calls["dsr"] + 1) or .99))
    monkeypatch.setattr(dsrmod, "pbo_cscv", lambda *a, **k: (calls.__setitem__("pbo", calls["pbo"] + 1) or .1))
    evidence = pbomod.create_batch_pbo_evidence(return_series_by_definition={
        "8032d111eff0e85a23a228312e35d88cc09a3a2fc34e418ef7c5f22047f6934e": tuple((i, float(v)) for i, v in enumerate((.1, .2, .3, .4))),
        "7e6bccdfaa7fb1598a877741379d3f21d572a9e987984644aee3e041c7347a08": tuple((i, float(v)) for i, v in enumerate((.2, .3, .4, .5))),
    })
    monkeypatch.setattr(pbomod, "create_batch_pbo_evidence", lambda **k: (calls.__setitem__("pbo", calls["pbo"] + 1) or evidence))
    monkeypatch.setattr(pbomod, "verify_batch_pbo_evidence", lambda x: (calls.__setitem__("pbo_verify", calls["pbo_verify"] + 1) or True))
    monkeypatch.setattr(mtmod, "benjamini_hochberg", lambda *a, **k: (calls.__setitem__("bh", calls["bh"] + 1) or {0}))
    monkeypatch.setattr(mtmod, "benjamini_hochberg_yekutieli", lambda *a, **k: (calls.__setitem__("bhy", calls["bhy"] + 1) or {0}))
    monkeypatch.setattr(fammod, "hierarchical_fdr", lambda **k: (calls.__setitem__("family", calls["family"] + 1) or HierarchicalFdrResult(frozenset({"alpha-0"}), frozenset({"family-a"}), {"family-a": .01}, {"family-a": .1}, 1, {})))
    monkeypatch.setattr(capmod, "capacity_band", lambda *a, **k: (calls.__setitem__("capacity", calls["capacity"] + 1) or {"aum_low": 10.0, "aum_high": 20.0}))
    monkeypatch.setattr(tailmod, "historical_var_cvar", lambda *a, **k: (calls.__setitem__("var", calls["var"] + 1) or {"var": .1, "cvar": .2}))
    monkeypatch.setattr(tailmod, "hill_tail_index", lambda *a, **k: (calls.__setitem__("hill", calls["hill"] + 1) or 2.0))
    monkeypatch.setattr(tailmod, "lower_tail_dependence", lambda *a, **k: (calls.__setitem__("lower", calls["lower"] + 1) or .1))
    monkeypatch.setattr(regmod, "label_regimes_by_vol", lambda *a, **k: (calls.__setitem__("regime_labels", calls["regime_labels"] + 1) or [None] * 20 + [0, 0, 1, 1, 1]))
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
    registered = register_leakage_compute(
        "tests.registered_compute:1", compute, exact_definition_id=definition_hashes[0]
    )
    subject_returns=tuple(.02 if i % 2 else .01 for i in range(100))
    peer_returns=tuple(.001 if i % 2 else -.001 for i in range(100))
    panel = {"cheap": {"feature_id": "x", "compute": registered, "bars": (Bar(event_time=0, knowledge_time=0, instrument_id="BTC", open=1, high=1, low=1, close=1), Bar(event_time=1, knowledge_time=1, instrument_id="BTC", open=1, high=1, low=1, close=1)), "target_event_time": 0}, "oos": {"n": 100, "train_size": 30, "test_size": 20, "returns": subject_returns, "return_series_by_definition": {definition_hashes[0]: subject_returns, definition_hashes[1]: peer_returns}, "observed_sr": 99.0, "n_obs": 999, "n_trials": 1, "var_sr": .1, "pbo_return_series_by_definition": {definition_hashes[0]: tuple((i, float(v)) for i, v in enumerate(subject_returns)), definition_hashes[1]: tuple((i, float(v)) for i, v in enumerate(peer_returns))}, "candidate_ids": ("alpha-0", "alpha-1"), "families": FamilyAssignment({"alpha-0": "family-a", "alpha-1": "family-a"}, {"alpha-0": "declared", "alpha-1": "declared"}), "candidate_family": "family-a", "pbo_candidate_definition_hashes": definition_hashes, "pbo_candidate_definitions": definitions, "knowledge_times_by_definition": {definition_hashes[0]: tuple(range(100)), definition_hashes[1]: tuple(range(100))}}, "realism": {"gross_returns": (.1, .2), "gross_event_times": (0, 1), "gross_knowledge_times": (0, 1), "turnover_by_period": (((.1, 10.0),), ((.2, 10.0),)), "aum_grid": (10.0, 20.0), "impact_coef_lo_hi": (0.1, 0.2), "returns": tuple(.01 for _ in range(25)), "event_times": tuple(range(25)), "knowledge_times": tuple(range(25)), "market_closes": tuple(float(i + 1) for i in range(25)), "benchmark_returns": tuple(.01 for _ in range(25)), "cost_model": CostModel(), "trade_notional": 10.0, "trade_shares": 1.0, "adv_shares": 10.0}}
    policy = ValidationPolicy()
    panel["oos"]["candidate_id"] = "alpha-0"
    panel["oos"]["candidate_index"] = 0
    context=ValidationContext(None, panel, policy, "a" * 64, "b" * 64, policy.digest, 100, effective_definition_ids=definition_hashes)
    stage_results=[stage.evaluate(context) for stage in production_adapters()]
    oos_result=next(result for result in stage_results if result.stage == "oos")
    assert oos_result.outcome == "PASS", (oos_result.reason, dict(oos_result.evidence))
    assert all(result.outcome == "PASS" for result in stage_results)
    candidate=Candidate("a" * 64, "f" * 64, definition_hashes[0], policy.digest)
    with TrialLedger(str(tmp_path / "production.db")) as ledger:
        peer_trial_id = _digest({"fixture_peer": definition_hashes[1]})
        peer_alpha_id = _digest({"fixture_peer_alpha": definition_hashes[1]})
        ledger.append(TrialRecord(peer_trial_id, peer_alpha_id, candidate.family_id, definition_hashes[1], policy.digest, "PLANNED", 0, 0))
        ledger.append(TrialRecord(peer_trial_id, peer_alpha_id, candidate.family_id, definition_hashes[1], policy.digest, "RUNNING", 1, 0))
        artifact=run_validation_dag(candidate, panel, policy, ledger, event_at=100, lease_now=1, production=True)
        assert artifact.verdict == "PASS"
        assert set(artifact.stage_metadata) == {"cheap", "oos", "realism"}
        receipts=ledger.validation_receipt(artifact.trial_id)
        assert set(receipts) >= {"cheap", "oos", "realism", "__artifact__"}
        assert BatchPboEvidence.model_validate(receipts["oos"]["payload"]["evidence"]["pbo_evidence"])
        from quantos.contracts.validation import CatalogEvidence
        catalog=CatalogEvidence.model_validate(receipts["oos"]["payload"]["evidence"]["catalog_evidence"])
        assert catalog.observed_sr != 99.0 and catalog.n_obs == 100
    assert all(value >= 1 for name,value in calls.items() if name != "dsr"), calls



def test_production_cheap_requires_target_feature_evidence():
    from quantos.contracts.data import Bar
    from services.research.validation_dag import (
        CheapValidationAdapter,
        ValidationContext,
        ValidationPolicy,
        register_leakage_compute,
    )

    def no_evidence(rows):
        return []

    registered = register_leakage_compute(
        "tests.no_target_feature_evidence:1", no_evidence
    )
    policy = ValidationPolicy()
    panel = {
        "cheap": {
            "feature_id": "missing",
            "compute": registered,
            "bars": (
                Bar(
                    event_time=1,
                    knowledge_time=1,
                    instrument_id="BTC",
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                ),
            ),
            "target_event_time": 1,
            "instrument_id": "BTC",
        }
    }
    result = CheapValidationAdapter().evaluate(
        ValidationContext(None, panel, policy, "a" * 64, "b" * 64, policy.digest, 1)
    )

    assert result.outcome == "BLOCK"
    assert result.reason == "missing target feature evidence"

def test_catalog_evidence_recomputes_negative_subject_sharpe():
    from services.validation.catalog_evidence import create_catalog_evidence

    subject="a"*64
    peer="b"*64
    evidence=create_catalog_evidence(
        subject_id="alpha-negative",
        subject_definition_hash=subject,
        trial_definition_hashes=(subject,peer),
        return_series_by_definition={
            subject:(-0.1,-0.2,-0.1,-0.2),
            peer:(0.1,0.2,0.1,0.2),
        },
    )
    assert evidence.observed_sr < 0.0
    assert evidence.deflated_sharpe < 0.95


def test_oos_thresholds_are_policy_owned_and_panel_cannot_weaken_them():
    from services.research.validation_dag import OOSValidationAdapter, ValidationContext

    policy = ValidationPolicy(settings={"oos": {"fdr_q": 0.1, "dsr_floor": 0.95, "pbo_max": 0.5}})
    panel = {"oos": {"fdr_q": 1.0, "dsr_floor": 0.0, "pbo_max": 1.0}}
    result = OOSValidationAdapter().evaluate(
        ValidationContext(None, panel, policy, "a"*64, "b"*64, policy.digest, 1)
    )
    assert result.outcome == "BLOCK"
    assert result.reason == "panel cannot define validation thresholds"


def test_production_and_test_runs_have_distinct_cache_identity(tmp_path):
    stages = tuple(Stage(name) for name in ("cheap", "oos", "realism"))
    test_policy = make_policy(*stages)
    candidate = make_candidate(test_policy)
    with TrialLedger(str(tmp_path / "trust-mode.db")) as ledger:
        test_artifact = run_validation_dag(candidate, {}, test_policy, ledger, event_at=1, lease_now=1)
        production_policy = ValidationPolicy()
        production_candidate = make_candidate(production_policy)
        production_artifact = run_validation_dag(
            production_candidate, {}, production_policy, ledger,
            event_at=1, lease_now=1, production=True,
        )
    assert test_artifact.trial_id != production_artifact.trial_id
    assert production_artifact.verdict == "BLOCK"


def test_production_cheap_block_short_circuits_oos_realism(tmp_path):
    policy=ValidationPolicy(); candidate=make_candidate(policy)
    with TrialLedger(str(tmp_path / "production-block.db")) as ledger:
        artifact=run_validation_dag(candidate, {}, policy, ledger, event_at=1, lease_now=1, production=True)
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
            run_validation_dag(candidate, {}, policy, ledger, event_at=1, lease_now=1, production=True)
    assert CheapValidationAdapter.evaluate is not original



def test_production_rejects_forged_implementation_id(tmp_path):
    from services.research.validation_dag import CheapValidationAdapter
    with pytest.raises(ValueError, match="canonical"):
        CheapValidationAdapter("forged")


def test_artifact_event_at_tamper_fails_closed(tmp_path):
    policy=make_policy(Stage("cheap"), Stage("oos"), Stage("realism")); candidate=make_candidate(policy); path=str(tmp_path / "event-tamper.db")
    with TrialLedger(path) as ledger:
        artifact=run_validation_dag(candidate, {}, policy, ledger, event_at=10, lease_now=10)
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
        first=run_validation_dag(candidate, {}, policy, ledger, event_at=10, lease_now=10)
        second=run_validation_dag(candidate, {}, policy, ledger, event_at=20, lease_now=20)
    assert first.trial_id != second.trial_id


def test_concurrent_cheap_block_returns_same_artifact(tmp_path):
    calls=[]; stages=tuple(Stage(name, "BLOCK" if name == "cheap" else "PASS", calls=calls) for name in ("cheap", "oos", "realism")); policy=make_policy(*stages); candidate=make_candidate(policy)
    with TrialLedger(str(tmp_path / "concurrent-block.db")) as ledger:
        results=[]; errors=[]
        def worker():
            try: results.append(run_validation_dag(candidate, {}, policy, ledger, event_at=1, lease_now=1))
            except Exception as exc: errors.append(exc)
        threads=[threading.Thread(target=worker) for _ in range(4)]
        [x.start() for x in threads]; [x.join() for x in threads]
        final=run_validation_dag(candidate, {}, policy, ledger, event_at=1, lease_now=1)
    assert all(isinstance(error, TrialLedgerConflict) for error in errors)
    assert all(result == final for result in results)
    assert final.verdict == "BLOCK" and calls == ["cheap"]



def test_cross_connection_in_progress_fails_fast_without_duplicate_side_effect(tmp_path):
    calls=[]; entered=threading.Event(); release=threading.Event()
    class Blocking(Stage):
        def evaluate(self, context):
            entered.set(); assert release.wait(timeout=2.0)
            return super().evaluate(context)
    policy=make_policy(Blocking("cheap", calls=calls), Stage("oos", calls=calls), Stage("realism", calls=calls)); candidate=make_candidate(policy); path=str(tmp_path / "cross.db")
    l1=TrialLedger(path); l2=TrialLedger(path); results=[]; errors=[]
    t1=threading.Thread(target=lambda: _capture_result(results, errors, lambda: run_validation_dag(candidate, {}, policy, l1, event_at=1, lease_now=1)))
    t1.start(); assert entered.wait(timeout=1.0)
    with pytest.raises(TrialLedgerConflict, match="in progress"):
        run_validation_dag(candidate, {}, policy, l2, event_at=1, lease_now=1)
    release.set(); t1.join(timeout=2.0)
    retry=run_validation_dag(candidate, {}, policy, l2, event_at=1, lease_now=1)
    l1.close(); l2.close()
    assert not errors and results == [retry] and calls == ["cheap", "oos", "realism"]


def test_cross_connection_block_retry_has_single_side_effect(tmp_path):
    calls=[]; entered=threading.Event(); release=threading.Event()
    class BlockingBlock(Stage):
        def evaluate(self, context):
            calls.append(self.name); entered.set(); assert release.wait(timeout=2.0)
            return StageEvidence(self.name,self.version,"BLOCK",{"blocked": True},implementation_id=self.implementation_id)
    policy=make_policy(BlockingBlock("cheap"), Stage("oos"), Stage("realism")); candidate=make_candidate(policy); path=str(tmp_path / "cross-block.db")
    l1=TrialLedger(path); l2=TrialLedger(path); results=[]; errors=[]
    t1=threading.Thread(target=lambda: _capture_result(results, errors, lambda: run_validation_dag(candidate, {}, policy, l1, event_at=1, lease_now=1)))
    t1.start(); assert entered.wait(timeout=1.0)
    with pytest.raises(TrialLedgerConflict, match="in progress"):
        run_validation_dag(candidate, {}, policy, l2, event_at=1, lease_now=1)
    release.set(); t1.join(timeout=2.0)
    retry=run_validation_dag(candidate, {}, policy, l2, event_at=1, lease_now=1)
    l1.close(); l2.close()
    assert not errors and results == [retry] and retry.verdict == "BLOCK" and calls == ["cheap"]

def test_terminal_receipts_rebuild_artifact_without_provider_rerun(tmp_path):
    calls=[]; stages=tuple(Stage(name, calls=calls) for name in ("cheap", "oos", "realism")); policy=make_policy(*stages); candidate=make_candidate(policy); path=str(tmp_path / "crash.db")
    with TrialLedger(path) as ledger:
        first=run_validation_dag(candidate, {}, policy, ledger, event_at=1, lease_now=1)
        con=sqlite3.connect(path); con.execute("DROP TRIGGER validation_receipts_no_delete"); con.execute("DELETE FROM validation_receipt_events WHERE stage='__artifact__'"); con.execute("UPDATE validation_receipt_heads SET event_count=(SELECT count(*) FROM validation_receipt_events), head_hash=(SELECT coalesce((SELECT row_hash FROM validation_receipt_events ORDER BY global_seq DESC LIMIT 1),'') FROM validation_receipt_events LIMIT 1)"); con.commit(); con.close()
        before=list(calls); second=run_validation_dag(candidate, {}, policy, ledger, event_at=1, lease_now=1)
    assert second == first and calls == before


def test_execution_snapshot_isolated_from_concurrent_family_mutation(tmp_path):
    from services.alpha.family_testing import FamilyAssignment
    entered=threading.Event(); release=threading.Event(); observed=[]
    class ReadFamily(Stage):
        def evaluate(self, context):
            entered.set(); release.wait(2)
            observed.append(dict(context.panel["families"].family_of))
            return super().evaluate(context)
    source={"alpha-0":"family-a"}; assignment=FamilyAssignment(source,{"alpha-0":"DECLARED"})
    policy=make_policy(ReadFamily("cheap"),Stage("oos"),Stage("realism")); candidate=make_candidate(policy); results=[]
    with TrialLedger(str(tmp_path / "snapshot.db")) as ledger:
        worker=threading.Thread(target=lambda: results.append(run_validation_dag(candidate,{"families":assignment},policy,ledger,event_at=1,lease_now=1)))
        worker.start(); assert entered.wait(1); source["alpha-0"]="mutated"; release.set(); worker.join()
    assert results[0].verdict == "PASS" and observed == [{"alpha-0":"family-a"}]


def test_crash_recovery_reuses_first_frozen_trial_set(tmp_path):
    path=str(tmp_path / "frozen-recovery.db")
    calls=[]
    class CrashOnce(Stage):
        def evaluate(self, context):
            calls.append((self.name, context.effective_trial_ids))
            if len(calls) == 1:
                raise KeyboardInterrupt("simulated process death")
            return super().evaluate(context)
    policy=make_policy(Stage("cheap",calls=[]),CrashOnce("oos"),Stage("realism"))
    first_candidate=make_candidate(policy)
    second_candidate=Candidate("b"*64,"f"*64,"d"*64,policy.digest)
    with TrialLedger(path) as ledger:
        with pytest.raises(KeyboardInterrupt):
            run_validation_dag(first_candidate,{},policy,ledger,event_at=1,lease_now=1)
        ledger.append(TrialRecord("9"*64,second_candidate.alpha_id,second_candidate.family_id,second_candidate.exact_definition_id,policy.digest,"PLANNED",0,2))
        ledger.append(TrialRecord("9"*64,second_candidate.alpha_id,second_candidate.family_id,second_candidate.exact_definition_id,policy.digest,"RUNNING",1,2))
        recovered=run_validation_dag(first_candidate,{},policy,ledger,event_at=1,lease_now=62)
    assert recovered.effective_trial_ids == (recovered.trial_id,)
    assert recovered.effective_trial_count == 1


def test_historical_artifact_survives_later_family_trial(tmp_path):
    policy=make_policy(Stage("cheap"),Stage("oos"),Stage("realism")); first_candidate=make_candidate(policy)
    second_candidate=Candidate("b"*64,"f"*64,"d"*64,policy.digest)
    with TrialLedger(str(tmp_path / "historical.db")) as ledger:
        first=run_validation_dag(first_candidate,{},policy,ledger,event_at=1,lease_now=1)
        second=run_validation_dag(second_candidate,{},policy,ledger,event_at=2,lease_now=2)
        restored=run_validation_dag(first_candidate,{},policy,ledger,event_at=1,lease_now=3)
    assert first == restored and first.effective_trial_count == 1 and second.effective_trial_count == 2


def test_canonical_alpha_candidate_uses_definition_bound_policy(tmp_path):
    from services.research.alpha_family import AlphaCandidate
    from services.research.alpha_identity import AlphaDefinition

    policy = ValidationPolicy((Stage("cheap"), Stage("oos"), Stage("realism")))
    definition = AlphaDefinition(
        definition=("ts_mean", ("field", "close"), 3),
        feature_contract={"features": ["close"]},
        data_schema={"dataset": "hourly_bars", "version": 1},
        universe_policy={"name": "dynamic_perpetuals", "version": 1},
        horizon="1h",
        cost_model={"fee_bps": 1.0},
        validation_policy={"digest": policy.digest},
        model="ridge",
        model_version="1",
    )
    candidate = AlphaCandidate(definition)

    with TrialLedger(str(tmp_path / "canonical-candidate.db")) as ledger:
        artifact = run_validation_dag(
            candidate, {}, policy, ledger, event_at=1, lease_now=1
        )

    assert artifact.verdict == "PASS"
    assert artifact.alpha_id == candidate.alpha_id
    assert artifact.family_id == candidate.family_id


def test_canonical_alpha_candidate_rejects_different_validation_policy(tmp_path):
    from services.research.alpha_family import AlphaCandidate
    from services.research.alpha_identity import AlphaDefinition

    policy = ValidationPolicy((Stage("cheap"), Stage("oos"), Stage("realism")))
    definition = AlphaDefinition(
        definition=("ts_mean", ("field", "close"), 3),
        feature_contract={"features": ["close"]},
        data_schema={"dataset": "hourly_bars", "version": 1},
        universe_policy={"name": "dynamic_perpetuals", "version": 1},
        horizon="1h",
        cost_model={"fee_bps": 1.0},
        validation_policy={"digest": "0" * 64},
        model="ridge",
        model_version="1",
    )

    with TrialLedger(str(tmp_path / "wrong-candidate-policy.db")) as ledger:
        with pytest.raises(ValueError, match="validation policy digest mismatch"):
            run_validation_dag(
                AlphaCandidate(definition), {}, policy, ledger, event_at=1, lease_now=1
            )


def test_leakage_registration_identity_cannot_be_rebound():
    from services.research.validation_dag import register_leakage_compute

    def bound_compute(rows):
        return rows

    register_leakage_compute(
        "tests.bound_compute_collision:1",
        bound_compute,
        exact_definition_id="1" * 64,
    )
    with pytest.raises(ValueError, match="identity collision"):
        register_leakage_compute(
            "tests.bound_compute_collision:1",
            bound_compute,
            exact_definition_id="2" * 64,
        )


def test_validation_dag_core_does_not_read_or_sleep_on_wall_clock():
    import ast
    from pathlib import Path
    import services.research.validation_dag as validation_dag

    tree = ast.parse(Path(validation_dag.__file__).read_text(encoding="utf-8"))
    forbidden_calls = {
        (node.func.value.id, node.func.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "time"
        and node.func.attr in {"monotonic", "sleep", "time"}
    }

    assert forbidden_calls == set()
