import pytest

from tools.validation_dag_production_check import run_production_validation_check


def _seed_peer_trials(ledger, panel, candidate, policy):
    from services.research.trial_ledger import TrialRecord
    from services.research.validation_dag import _digest

    for index, definition_hash in enumerate(
        panel["oos"]["pbo_candidate_definition_hashes"][1:],
        start=1,
    ):
        trial_id = _digest({"fixture_peer": index, "definition": definition_hash})
        alpha_id = _digest({"fixture_peer_alpha": index, "definition": definition_hash})
        ledger.append(TrialRecord(trial_id, alpha_id, candidate.family_id, definition_hash, policy.digest, "PLANNED", 0, 0))
        ledger.append(TrialRecord(trial_id, alpha_id, candidate.family_id, definition_hash, policy.digest, "RUNNING", 1, 0))


def test_zero_monkeypatch_production_validation_check_passes_and_blocks(tmp_path):
    report = run_production_validation_check(tmp_path)

    assert report["pass"]["verdict"] == "PASS"
    assert report["pass"]["stages"] == ["cheap", "oos", "realism"]
    assert report["self_reported_positive"]["verdict"] == "BLOCK"
    assert report["self_reported_positive"]["blocked_stage"] == "oos"
    assert report["missing"]["verdict"] == "BLOCK"
    assert report["missing"]["blocked_stage"] == "cheap"
    assert report["malformed"]["verdict"] == "BLOCK"
    assert report["malformed"]["blocked_stage"] == "oos"
    assert report["n_mismatch"]["verdict"] == "BLOCK"
    assert report["n_mismatch"]["blocked_stage"] == "oos"
    assert report["ordered_decay"]["verdict"] == "BLOCK"
    assert report["ordered_decay"]["blocked_stage"] == "oos"
    assert report["malformed_realism"]["verdict"] == "BLOCK"
    assert report["malformed_realism"]["blocked_stage"] == "realism"

def test_production_rejects_external_fdr_pvalues(tmp_path):
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import ValidationPolicy, run_validation_dag
    from tools.validation_dag_production_check import _Candidate, _valid_panel

    panel, exact_definition_id = _valid_panel()
    panel["oos"]["pvalues"] = {
        candidate_id: 0.0
        for candidate_id in panel["oos"]["candidate_ids"]
    }
    policy = ValidationPolicy()
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)

    with TrialLedger(str(tmp_path / "external-pvalues.db")) as ledger:
        artifact = run_validation_dag(
            candidate,
            panel,
            policy,
            ledger,
            event_at=100,
            lease_now=1,
            production=True,
        )
        receipt = ledger.validation_receipt(artifact.trial_id)["oos"]

    assert artifact.verdict == "BLOCK"
    assert receipt["payload"]["reason"] == "oos engine unavailable"


def test_production_fdr_is_derived_from_oos_return_population(tmp_path):
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import ValidationPolicy, run_validation_dag
    from tools.validation_dag_production_check import _Candidate, _valid_panel

    panel, exact_definition_id = _valid_panel()
    panel["oos"].pop("pvalues", None)
    policy = ValidationPolicy()
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)

    with TrialLedger(str(tmp_path / "derived-pvalues.db")) as ledger:
        _seed_peer_trials(ledger, panel, candidate, policy)
        artifact = run_validation_dag(
            candidate,
            panel,
            policy,
            ledger,
            event_at=100,
            lease_now=1,
            production=True,
        )
        evidence = ledger.validation_receipt(artifact.trial_id)["oos"]["payload"]["evidence"]

    assert artifact.verdict == "PASS"
    assert set(evidence["fdr_pvalues"]) == set(panel["oos"]["candidate_ids"])
    assert evidence["fdr_pvalues"]["alpha-0"] < evidence["fdr_pvalues"]["alpha-2"]

def test_constant_positive_oos_returns_produce_finite_durable_fdr_evidence(tmp_path):
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import ValidationPolicy, run_validation_dag
    from tools.validation_dag_production_check import _Candidate, _valid_panel

    panel, exact_definition_id = _valid_panel()
    peer_hash = panel["oos"]["pbo_candidate_definition_hashes"][1]
    constant = tuple(0.01 for _ in panel["oos"]["returns"])
    panel["oos"]["return_series_by_definition"][peer_hash] = constant
    panel["oos"]["pbo_return_series_by_definition"][peer_hash] = tuple(
        (event_time, value) for event_time, value in enumerate(constant)
    )
    policy = ValidationPolicy()
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)

    with TrialLedger(str(tmp_path / "constant-fdr.db")) as ledger:
        _seed_peer_trials(ledger, panel, candidate, policy)
        artifact = run_validation_dag(
            candidate, panel, policy, ledger, event_at=100, lease_now=1, production=True
        )
        evidence = ledger.validation_receipt(artifact.trial_id)["oos"]["payload"]["evidence"]

    statistic = evidence["fdr_statistics"]["alpha-1"]
    assert artifact.verdict in {"PASS", "BLOCK"}
    assert statistic["pvalue"] == 0.0
    assert statistic["statistic"] is None
    assert statistic["zero_variance"] is True

def test_production_blocks_population_outside_frozen_trial_set(tmp_path):
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import ValidationPolicy, run_validation_dag
    from tools.validation_dag_production_check import _Candidate, _valid_panel

    panel, exact_definition_id = _valid_panel()
    policy = ValidationPolicy()
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)

    with TrialLedger(str(tmp_path / "unfrozen-population.db")) as ledger:
        artifact = run_validation_dag(
            candidate, panel, policy, ledger, event_at=100, lease_now=1, production=True
        )
        receipt = ledger.validation_receipt(artifact.trial_id)["oos"]["payload"]

    assert artifact.verdict == "BLOCK"
    assert receipt["reason"] == "oos engine unavailable"


def test_production_blocks_leakage_compute_for_other_definition(tmp_path):
    from quantos.contracts.feature import FeatureValue
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import (
        ValidationPolicy,
        register_leakage_compute,
        run_validation_dag,
    )
    from tools.validation_dag_production_check import _Candidate, _valid_panel

    panel, exact_definition_id = _valid_panel()

    def other_definition_compute(rows):
        return [
            FeatureValue(
                feature_id="past_close",
                instrument_id=row.instrument_id,
                event_time=row.event_time,
                knowledge_time=row.knowledge_time,
                value=row.close,
            )
            for row in rows
        ]

    panel["cheap"]["compute"] = register_leakage_compute(
        "tests.other_definition_compute:1",
        other_definition_compute,
        exact_definition_id="0" * 64,
    )
    policy = ValidationPolicy()
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)

    with TrialLedger(str(tmp_path / "wrong-leakage-definition.db")) as ledger:
        artifact = run_validation_dag(
            candidate, panel, policy, ledger, event_at=100, lease_now=1, production=True
        )
        receipts = ledger.validation_receipt(artifact.trial_id)

    assert artifact.verdict == "BLOCK"
    assert receipts["cheap"]["payload"]["reason"] == "leakage compute definition mismatch"
    assert receipts["oos"]["payload"]["evidence"]["short_circuit"] is True


def test_production_blocks_future_known_leakage_feature(tmp_path):
    from quantos.contracts.feature import FeatureValue
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import (
        ValidationPolicy,
        register_leakage_compute,
        run_validation_dag,
    )
    from tools.validation_dag_production_check import _Candidate, _valid_panel

    panel, exact_definition_id = _valid_panel()

    def future_known_compute(rows):
        return [
            FeatureValue(
                feature_id="past_close",
                instrument_id=row.instrument_id,
                event_time=row.event_time,
                knowledge_time=101,
                value=row.close,
            )
            for row in rows
        ]

    panel["cheap"]["compute"] = register_leakage_compute(
        "tests.future_known_compute:1",
        future_known_compute,
        exact_definition_id=exact_definition_id,
    )
    policy = ValidationPolicy()
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)

    with TrialLedger(str(tmp_path / "future-known-feature.db")) as ledger:
        artifact = run_validation_dag(
            candidate, panel, policy, ledger, event_at=100, lease_now=1, production=True
        )
        receipts = ledger.validation_receipt(artifact.trial_id)

    assert artifact.verdict == "BLOCK"
    assert receipts["cheap"]["payload"]["reason"] == "leakage outputs exceed validation as-of"
    assert receipts["oos"]["payload"]["evidence"]["short_circuit"] is True


def test_production_blocks_future_known_leakage_bar(tmp_path):
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import ValidationPolicy, run_validation_dag
    from tools.validation_dag_production_check import _Candidate, _valid_panel

    panel, exact_definition_id = _valid_panel()
    bars = panel["cheap"]["bars"]
    panel["cheap"]["bars"] = (
        bars[0].model_copy(update={"knowledge_time": 101}),
        *bars[1:],
    )
    policy = ValidationPolicy()
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)

    with TrialLedger(str(tmp_path / "future-known-bar.db")) as ledger:
        artifact = run_validation_dag(
            candidate, panel, policy, ledger, event_at=100, lease_now=1, production=True
        )
        receipt = ledger.validation_receipt(artifact.trial_id)["cheap"]["payload"]

    assert artifact.verdict == "BLOCK"
    assert receipt["reason"] == "leakage inputs exceed validation as-of"


def test_production_blocks_future_oos_observation(tmp_path):
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import ValidationPolicy, run_validation_dag
    from tools.validation_dag_production_check import _Candidate, _valid_panel

    panel, exact_definition_id = _valid_panel()
    definition_hash = panel["oos"]["pbo_candidate_definition_hashes"][0]
    series = list(panel["oos"]["pbo_return_series_by_definition"][definition_hash])
    series[-1] = (101, series[-1][1])
    panel["oos"]["pbo_return_series_by_definition"][definition_hash] = tuple(series)
    policy = ValidationPolicy()
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)

    with TrialLedger(str(tmp_path / "future-oos.db")) as ledger:
        _seed_peer_trials(ledger, panel, candidate, policy)
        artifact = run_validation_dag(
            candidate, panel, policy, ledger, event_at=100, lease_now=1, production=True
        )
        receipt = ledger.validation_receipt(artifact.trial_id)["oos"]["payload"]

    assert artifact.verdict == "BLOCK"
    assert receipt["reason"] == "oos engine unavailable"


def test_production_blocks_pbo_returns_different_from_catalog(tmp_path):
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import ValidationPolicy, run_validation_dag
    from tools.validation_dag_production_check import _Candidate, _valid_panel

    panel, exact_definition_id = _valid_panel()
    definition_hash = panel["oos"]["pbo_candidate_definition_hashes"][1]
    series = list(panel["oos"]["pbo_return_series_by_definition"][definition_hash])
    event_time, value = series[0]
    series[0] = (event_time, value + 0.5)
    panel["oos"]["pbo_return_series_by_definition"][definition_hash] = tuple(series)
    policy = ValidationPolicy()
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)

    with TrialLedger(str(tmp_path / "different-pbo-returns.db")) as ledger:
        _seed_peer_trials(ledger, panel, candidate, policy)
        artifact = run_validation_dag(
            candidate, panel, policy, ledger, event_at=100, lease_now=1, production=True
        )
        receipt = ledger.validation_receipt(artifact.trial_id)["oos"]["payload"]

    assert artifact.verdict == "BLOCK"
    assert receipt["reason"] == "oos engine unavailable"


def test_production_blocks_realism_without_event_time_provenance(tmp_path):
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import ValidationPolicy, run_validation_dag
    from tools.validation_dag_production_check import _Candidate, _valid_panel

    panel, exact_definition_id = _valid_panel()
    panel["realism"].pop("event_times")
    policy = ValidationPolicy()
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)

    with TrialLedger(str(tmp_path / "realism-no-times.db")) as ledger:
        _seed_peer_trials(ledger, panel, candidate, policy)
        artifact = run_validation_dag(
            candidate, panel, policy, ledger, event_at=100, lease_now=1, production=True
        )
        receipt = ledger.validation_receipt(artifact.trial_id)["realism"]["payload"]

    assert artifact.verdict == "BLOCK"
    assert receipt["reason"] == "missing realism inputs"


def test_production_blocks_future_realism_observation(tmp_path):
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import ValidationPolicy, run_validation_dag
    from tools.validation_dag_production_check import _Candidate, _valid_panel

    panel, exact_definition_id = _valid_panel()
    times = list(panel["realism"]["event_times"]); times[-1] = 101
    panel["realism"]["event_times"] = tuple(times)
    policy = ValidationPolicy()
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)

    with TrialLedger(str(tmp_path / "future-realism.db")) as ledger:
        _seed_peer_trials(ledger, panel, candidate, policy)
        artifact = run_validation_dag(
            candidate, panel, policy, ledger, event_at=100, lease_now=1, production=True
        )
        receipt = ledger.validation_receipt(artifact.trial_id)["realism"]["payload"]

    assert artifact.verdict == "BLOCK"
    assert receipt["reason"] == "realism inputs exceed validation as-of"


def test_production_blocks_future_known_oos_observation(tmp_path):
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import ValidationPolicy, run_validation_dag
    from tools.validation_dag_production_check import _Candidate, _valid_panel

    panel, exact_definition_id = _valid_panel()
    definition_hash = panel["oos"]["pbo_candidate_definition_hashes"][0]
    times = list(panel["oos"]["knowledge_times_by_definition"][definition_hash])
    times[-1] = 101
    panel["oos"]["knowledge_times_by_definition"][definition_hash] = tuple(times)
    policy = ValidationPolicy()
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)

    with TrialLedger(str(tmp_path / "future-known-oos.db")) as ledger:
        _seed_peer_trials(ledger, panel, candidate, policy)
        artifact = run_validation_dag(
            candidate, panel, policy, ledger, event_at=100, lease_now=1, production=True
        )
        receipt = ledger.validation_receipt(artifact.trial_id)["oos"]["payload"]

    assert artifact.verdict == "BLOCK"
    assert receipt["reason"] == "oos engine unavailable"


def test_production_blocks_future_known_realism_observation(tmp_path):
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import ValidationPolicy, run_validation_dag
    from tools.validation_dag_production_check import _Candidate, _valid_panel

    panel, exact_definition_id = _valid_panel()
    times = list(panel["realism"]["knowledge_times"]); times[-1] = 101
    panel["realism"]["knowledge_times"] = tuple(times)
    policy = ValidationPolicy()
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)

    with TrialLedger(str(tmp_path / "future-known-realism.db")) as ledger:
        _seed_peer_trials(ledger, panel, candidate, policy)
        artifact = run_validation_dag(
            candidate, panel, policy, ledger, event_at=100, lease_now=1, production=True
        )
        receipt = ledger.validation_receipt(artifact.trial_id)["realism"]["payload"]

    assert artifact.verdict == "BLOCK"
    assert receipt["reason"] == "realism inputs exceed validation as-of"


def test_production_blocks_oos_knowledge_before_event_time(tmp_path):
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import ValidationPolicy, run_validation_dag
    from tools.validation_dag_production_check import _Candidate, _valid_panel

    panel, exact_definition_id = _valid_panel()
    definition_hash = panel["oos"]["pbo_candidate_definition_hashes"][0]
    times = list(panel["oos"]["knowledge_times_by_definition"][definition_hash])
    times[-1] = times[-1] - 1
    panel["oos"]["knowledge_times_by_definition"][definition_hash] = tuple(times)
    policy = ValidationPolicy()
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)

    with TrialLedger(str(tmp_path / "invalid-oos-bitemporal.db")) as ledger:
        _seed_peer_trials(ledger, panel, candidate, policy)
        artifact = run_validation_dag(
            candidate, panel, policy, ledger, event_at=100, lease_now=1, production=True
        )

    assert artifact.verdict == "BLOCK"


@pytest.mark.parametrize(("setting", "threshold"), (
    ("min_capacity_aum", 1e30),
    ("max_cost_fraction", 0.0),
    ("max_cvar", 0.0),
    ("min_hill_tail_index", 1e30),
    ("min_regime_profitable_frac", 1.0),
    ("max_lower_tail_dependence", 0.0),
))
def test_production_realism_policy_thresholds_are_load_bearing(
    tmp_path, setting, threshold
):
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import ValidationPolicy, run_validation_dag
    from tools.validation_dag_production_check import _Candidate, _valid_panel

    panel, exact_definition_id = _valid_panel()
    if setting == "min_regime_profitable_frac":
        panel["realism"]["returns"] = tuple(
            -abs(value) for value in panel["realism"]["returns"]
        )
    policy = ValidationPolicy(settings={"realism": {setting: threshold}})
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)

    with TrialLedger(str(tmp_path / f"realism-{setting}.db")) as ledger:
        _seed_peer_trials(ledger, panel, candidate, policy)
        artifact = run_validation_dag(
            candidate, panel, policy, ledger, event_at=100, lease_now=1, production=True
        )
        receipt = ledger.validation_receipt(artifact.trial_id)["realism"]["payload"]

    assert artifact.verdict == "BLOCK"
    assert receipt["reason"] == "realism gate blocked"
    assert receipt["evidence"]["failed_thresholds"] == [setting]


def test_production_rejects_realism_thresholds_from_panel(tmp_path):
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import ValidationPolicy, run_validation_dag
    from tools.validation_dag_production_check import _Candidate, _valid_panel

    panel, exact_definition_id = _valid_panel()
    panel["realism"]["max_cvar"] = 999.0
    policy = ValidationPolicy()
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)

    with TrialLedger(str(tmp_path / "panel-realism-threshold.db")) as ledger:
        _seed_peer_trials(ledger, panel, candidate, policy)
        artifact = run_validation_dag(
            candidate, panel, policy, ledger, event_at=100, lease_now=1, production=True
        )
        receipt = ledger.validation_receipt(artifact.trial_id)["realism"]["payload"]

    assert artifact.verdict == "BLOCK"
    assert receipt["reason"] == "panel cannot define validation thresholds"


def test_production_blocks_invalid_realism_policy(tmp_path):
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import ValidationPolicy, run_validation_dag
    from tools.validation_dag_production_check import _Candidate, _valid_panel

    panel, exact_definition_id = _valid_panel()
    policy = ValidationPolicy(settings={"realism": {"unknown": 1.0}})
    candidate = _Candidate("a" * 64, "f" * 64, exact_definition_id, policy.digest)

    with TrialLedger(str(tmp_path / "invalid-realism-policy.db")) as ledger:
        _seed_peer_trials(ledger, panel, candidate, policy)
        artifact = run_validation_dag(
            candidate, panel, policy, ledger, event_at=100, lease_now=1, production=True
        )
        receipt = ledger.validation_receipt(artifact.trial_id)["realism"]["payload"]

    assert artifact.verdict == "BLOCK"
    assert receipt["reason"] == "invalid realism policy settings"
