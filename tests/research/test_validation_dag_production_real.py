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
            event_at=1,
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
            event_at=1,
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
            candidate, panel, policy, ledger, event_at=1, lease_now=1, production=True
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
            candidate, panel, policy, ledger, event_at=1, lease_now=1, production=True
        )
        receipt = ledger.validation_receipt(artifact.trial_id)["oos"]["payload"]

    assert artifact.verdict == "BLOCK"
    assert receipt["reason"] == "oos engine unavailable"
