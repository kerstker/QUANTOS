import json
from types import SimpleNamespace

from tools import paper_research_risk_bundle as bundle


class _FakeLedger:
    def __init__(self, _path, *, create):
        assert create is False

    def attested_pending(self, alpha_id):
        return [
            {
                "decided_ts": 100,
                "recorded_at": 110,
                "weights_json": json.dumps({"BTC": 1.0}),
                "signals_json": None,
                "source": "TEST_ATTESTED",
                "alpha_id": alpha_id,
            }
        ]

    def close(self):
        return None


class _FakeRealizedLedger(_FakeLedger):
    def attested_pending(self, _alpha_id):
        return []

    def attested_decisions(self, alpha_id):
        return [
            {
                "decided_ts": 80,
                "recorded_at": 90,
                "weights_json": json.dumps({"ETH": -1.0}),
                "signals_json": None,
                "source": "TEST_ATTESTED",
                "alpha_id": alpha_id,
            },
            {
                "decided_ts": 100,
                "recorded_at": 110,
                "weights_json": json.dumps({"BTC": 1.0}),
                "signals_json": None,
                "source": "TEST_ATTESTED",
                "alpha_id": alpha_id,
            },
        ]


def test_pending_bundle_binds_real_decisions_without_synthetic_inputs(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(json.dumps({"dataset": {"version": "pit-v1"}}), encoding="utf-8")
    monkeypatch.setattr(bundle, "AttestedForwardLedger", _FakeLedger)

    document = bundle.build_pending_bundle(
        forward_db=tmp_path / "paper.db",
        dataset_evidence=dataset,
        alpha_ids=("carry_oi_majors",),
    )

    assert document["status"] == "PENDING"
    assert document["synthetic_defaults"] is False
    row = document["alphas"]["carry_oi_majors"]
    assert len(row["binding"]["decision_payload_sha256"]) == 64
    assert len(row["binding"]["config_hash"]) == 64
    assert set(row["missing_inputs"]) == set(bundle.REQUIRED_INPUT_FIELDS)
    assert "inputs" not in row


def test_decision_adv_inputs_use_pit_row_and_cny_capacity() -> None:
    schedule = SimpleNamespace(
        periods=[80, 100, 120],
        adv_by_period={
            100: {"BTC": 20_000_000.0, "ETH": 10_000_000.0},
        },
    )

    inputs = bundle._decision_adv_inputs(
        schedule=schedule,
        decision_ts=110,
        weights={"BTC": 0.5, "ETH": -0.5},
        cny_per_usd=7.0,
        paper_aum_usd=1_000_000.0,
    )

    assert inputs["capacity_cny"] == {
        "BTC": 14_000_000.0,
        "ETH": 7_000_000.0,
    }
    assert inputs["daily_volume_cny"] == 210_000_000.0
    assert inputs["total_aum"] == 7_000_000.0
    assert inputs["liquidity_provenance"]["schedule_period"] == 100


def test_metadata_is_bound_to_predecision_verified_evaluation() -> None:
    row = {
        "evaluation": {
            "as_of": 100,
            "deflated_sharpe": 0.42,
            "pbo": 0.1,
            "verdict": "FAIL",
            "reasons": ["DEFLATED_SHARPE_BELOW_MIN"],
        },
        "research_alpha_id": "paper_crowd_hash",
        "run_id": "run-1",
    }

    metadata = bundle._metadata_from_evaluation(
        deployed_alpha_id="crowd_contra",
        evaluation_row=row,
        decision_recorded_at=100 + 2 * 86_400_000,
        forward_periods=4,
        maintenance={"strategies": {}},
    )

    assert metadata["age_days"] == 2.0
    assert metadata["validation_confidence"] == 0.42
    assert metadata["paper_confidence"] == 0.5
    assert metadata["decay_flag"] is False
    assert metadata["overfit_flag"] is False
    assert metadata["leakage_flag"] is False


def test_metadata_rejects_evaluation_recorded_after_decision() -> None:
    row = {
        "evaluation": {
            "as_of": 101,
            "deflated_sharpe": 0.5,
            "pbo": 0.1,
            "verdict": "PASS",
            "reasons": [],
        }
    }

    try:
        bundle._metadata_from_evaluation(
            deployed_alpha_id="crowd_contra",
            evaluation_row=row,
            decision_recorded_at=100,
            forward_periods=8,
            maintenance={"strategies": {}},
        )
    except RuntimeError as exc:
        assert "predate" in str(exc)
    else:
        raise AssertionError("post-decision evaluation must be rejected")


def test_postdecision_evaluation_is_available_for_monitoring_but_marked() -> None:
    row = {
        "evaluation": {
            "as_of": 101,
            "deflated_sharpe": 0.5,
            "pbo": 0.1,
            "verdict": "FAIL",
            "reasons": [],
        },
        "run_id": "eval-after-decision",
    }

    metadata = bundle._metadata_from_evaluation(
        deployed_alpha_id="crowd_contra_q20",
        evaluation_row=row,
        decision_recorded_at=100,
        forward_periods=1,
        maintenance={"strategies": {}},
        allow_post_decision_monitoring=True,
    )

    assert metadata["evidence_timing"] == "POST_DECISION_MONITORING_ONLY"
    assert metadata["evaluation_as_of"] == 101
    assert metadata["decision_recorded_at"] == 100
    assert metadata["age_days"] == 0.0


def test_realized_attested_decision_can_feed_monitoring_inputs_only() -> None:
    ledger = _FakeRealizedLedger("unused", create=False)

    decision, basis, blockers, error = bundle._dynamic_evidence_decision(
        ledger, "crowd_contra_14d"
    )

    assert decision is not None
    assert decision["decided_ts"] == 100
    assert basis == "LATEST_REALIZED_ATTESTED_DECISION_MONITORING_ONLY"
    assert blockers == ["CURRENT_ATTESTED_PENDING_DECISION_MISSING"]
    assert error is None


def test_high_trial_review_reason_is_not_an_observed_overfit_flag() -> None:
    row = {
        "evaluation": {
            "as_of": 100,
            "deflated_sharpe": 0.2,
            "pbo": 0.03,
            "verdict": "FAIL",
            "reasons": [
                "HIGH_TRIAL_COUNT (2539); review overfitting risk",
            ],
        }
    }

    metadata = bundle._metadata_from_evaluation(
        deployed_alpha_id="crowd_contra",
        evaluation_row=row,
        decision_recorded_at=100,
        forward_periods=1,
        maintenance={"strategies": {}},
    )

    assert metadata["overfit_flag"] is False


def test_pbo_above_half_is_an_observed_overfit_flag() -> None:
    row = {
        "evaluation": {
            "as_of": 100,
            "deflated_sharpe": 0.8,
            "pbo": 0.51,
            "verdict": "FAIL",
            "reasons": [],
        }
    }

    metadata = bundle._metadata_from_evaluation(
        deployed_alpha_id="crowd_contra",
        evaluation_row=row,
        decision_recorded_at=100,
        forward_periods=8,
        maintenance={"strategies": {}},
    )

    assert metadata["overfit_flag"] is True


def test_dynamic_admission_waits_without_blocking_input_precomputation() -> None:
    assert bundle._dynamic_admission_blockers(
        forward_periods=3,
        evaluation={"verdict": "PASS"},
    ) == ["FORWARD_EVIDENCE_MATURITY_PENDING"]
    assert bundle._dynamic_admission_blockers(
        forward_periods=8,
        evaluation={"verdict": "FAIL"},
    ) == ["INDEPENDENT_EVALUATION_NOT_PASS"]
    assert bundle._dynamic_admission_blockers(
        forward_periods=8,
        evaluation={"verdict": "PASS"},
    ) == []


def test_dynamic_admission_fails_closed_when_both_gates_are_missing() -> None:
    assert bundle._dynamic_admission_blockers(
        forward_periods=0,
        evaluation=None,
    ) == [
        "INDEPENDENT_EVALUATION_MISSING",
        "FORWARD_EVIDENCE_MATURITY_PENDING",
    ]


def test_immature_dynamic_candidate_precomputes_inputs_but_stays_pending(
    monkeypatch,
) -> None:
    from tools import crypto_oi_research, crypto_robust_optimization

    candidate = SimpleNamespace(alpha_id="challenger")
    reference = SimpleNamespace(alpha_id="crowd_contra")
    monkeypatch.setattr(bundle, "AttestedForwardLedger", _FakeLedger)
    monkeypatch.setattr(bundle, "forward_decision_payload_sha256", lambda _row: "a" * 64)
    monkeypatch.setattr(bundle, "_forward_periods", lambda *_args, **_kwargs: 3)
    monkeypatch.setattr(
        bundle,
        "_verified_forward_evaluation_rows",
        lambda *_args, **_kwargs: {
            "challenger": {
                "evaluation": {"verdict": "PASS"},
                "run_id": "eval-1",
                "content_hash": "content-1",
            }
        },
    )
    monkeypatch.setattr(
        bundle,
        "_read_object",
        lambda path: (
            {"strategies": {}}
            if "maintenance" in str(path)
            else {"dataset_version": "forward-v1"}
        ),
    )
    monkeypatch.setattr(
        crypto_robust_optimization,
        "load_alpha_schedules",
        lambda: ({"crowd_contra": reference}, "schedule-v1"),
    )
    monkeypatch.setattr(crypto_robust_optimization, "_build_bundle", lambda _rows: {})
    monkeypatch.setattr(crypto_oi_research, "build_panel", lambda: (object(), {}))
    monkeypatch.setattr(bundle, "_dynamic_candidate_schedule", lambda *_args: candidate)
    monkeypatch.setattr(
        bundle,
        "_candidate_risk_history",
        lambda *_args: (
            {"challenger": (0.01, -0.01), "crowd_contra": (0.0, 0.01)},
            ["CALM", "STRESS"],
            [10, 20],
        ),
    )
    monkeypatch.setattr(
        bundle,
        "_cny_per_usd_as_of",
        lambda *_args, **_kwargs: (7.0, {"source": "TEST_FX"}),
    )
    monkeypatch.setattr(
        bundle,
        "_decision_adv_inputs",
        lambda **_kwargs: {
            "capacity_cny": {"BTC": 1_000_000.0},
            "daily_volume_cny": 10_000_000.0,
            "total_aum": 7_000_000.0,
            "liquidity_provenance": {"source": "TEST_ADV"},
        },
    )
    monkeypatch.setattr(
        bundle,
        "_metadata_from_evaluation",
        lambda **_kwargs: {
            "age_days": 0.0,
            "validation_confidence": 1.0,
            "paper_confidence": 0.375,
            "decay_flag": False,
            "overfit_flag": False,
            "leakage_flag": False,
        },
    )

    document = bundle.build_automatic_research_risk_bundle(
        alpha_ids=("challenger",),
    )

    row = document["alphas"]["challenger"]
    assert row["input_evidence_status"] == "READY"
    assert set(bundle.REQUIRED_INPUT_FIELDS).issubset(row["inputs"])
    assert row["provenance"]["decision_basis"] == "CURRENT_ATTESTED_PENDING_DECISION"
    assert row["admission_blockers"] == ["FORWARD_EVIDENCE_MATURITY_PENDING"]
    assert row["status"] == "PENDING"
    assert document["status"] == "PENDING"


def test_main_does_not_publish_pending_artifact_without_complete_attestation(
    tmp_path, monkeypatch
) -> None:
    from tools import paper_candidate_universe

    output = tmp_path / "risk.json"
    document = {
        "status": "PENDING",
        "alphas": {"challenger": {}},
        "missing_inputs": [],
    }
    monkeypatch.setattr(
        paper_candidate_universe,
        "paper_candidate_ids",
        lambda **_kwargs: ("challenger",),
    )
    monkeypatch.setattr(
        bundle,
        "build_automatic_research_risk_bundle",
        lambda **_kwargs: document,
    )

    assert bundle.main(["--output", str(output)]) == 0
    assert not output.exists()
    attestation = bundle.release_run_attestation()
    assert attestation is not None and attestation["complete"] is False


def test_main_returns_failure_when_bundle_cannot_be_computed(
    tmp_path, monkeypatch
) -> None:
    from tools import paper_candidate_universe

    monkeypatch.setattr(
        paper_candidate_universe,
        "paper_candidate_ids",
        lambda **_kwargs: ("challenger",),
    )

    def fail(**_kwargs):
        raise RuntimeError("broken evidence")

    monkeypatch.setattr(bundle, "build_automatic_research_risk_bundle", fail)

    assert bundle.main(["--output", str(tmp_path / "risk.json")]) == 1


def test_main_attests_published_pending_bundle_and_exact_source_view(
    tmp_path, monkeypatch
) -> None:
    from tools import paper_candidate_universe

    release_commit = "c" * 40
    output = tmp_path / "risk.json"
    document = {
        "schema_version": bundle.BUNDLE_SCHEMA_VERSION,
        "provenance": bundle.BUNDLE_PROVENANCE,
        "status": "PENDING",
        "producer": "tools.paper_research_risk_bundle",
        "producer_contract": "REAL_PIT_INPUTS_NO_SYNTHETIC_DEFAULTS_DYNAMIC_FORWARD_GATE",
        "dataset_version": "d" * 64,
        "config_hash": "e" * 64,
        "alphas": {
            "challenger": {
                "binding": {"alpha_id": "challenger"},
                "missing_inputs": ["alpha_metadata"],
                "status": "PENDING",
            }
        },
        "missing_inputs": ["alpha_metadata"],
        "synthetic_defaults": False,
        "execution_authority_granted": False,
        "order_submission_allowed": False,
    }
    source_view = {
        "forward_ledger": {"exists": True, "sha256": "a" * 64},
        "experiment_registry": {"exists": True, "sha256": "b" * 64},
    }
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", release_commit)
    monkeypatch.setattr(
        paper_candidate_universe,
        "paper_candidate_ids",
        lambda **_kwargs: ("challenger",),
    )
    monkeypatch.setattr(
        bundle,
        "build_automatic_research_risk_bundle",
        lambda **_kwargs: document,
    )
    monkeypatch.setattr(bundle, "_source_authority_view", lambda _paths: source_view)

    assert bundle.main(["--output", str(output)]) == 0

    published = json.loads(output.read_text(encoding="utf-8"))
    assert published["safety"]["paper_only"] is True
    attestation = bundle.release_run_attestation()
    assert attestation is not None and attestation["complete"] is True
    assert attestation["module"] == "paper_research_risk_bundle"
    assert attestation["release_commit"] == release_commit
    assert set(attestation["input_hashes"]) == {
        "candidate_universe",
        "paper_research_risk_contract",
        "risk_input_bundle_view",
        "source_authorities_before",
    }
    assert set(attestation["output_hashes"]) == {
        "paper_research_risk_bundle_file",
        "source_authorities_after",
    }
    assert attestation["safety"] == {
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credentials_accessed": False,
        "broker_connectivity": False,
    }


def test_main_failure_clears_prior_release_attestation(tmp_path, monkeypatch) -> None:
    from tools import paper_candidate_universe

    bundle._LAST_RELEASE_RUN_ATTESTATION = {"complete": True}
    monkeypatch.setattr(
        paper_candidate_universe,
        "paper_candidate_ids",
        lambda **_kwargs: ("challenger",),
    )

    def fail(**_kwargs):
        raise RuntimeError("broken evidence")

    monkeypatch.setattr(bundle, "build_automatic_research_risk_bundle", fail)

    assert bundle.main(["--output", str(tmp_path / "risk.json")]) == 1
    assert bundle.release_run_attestation() is None


def test_main_marks_attestation_incomplete_when_source_changes_mid_run(
    tmp_path, monkeypatch
) -> None:
    from tools import paper_candidate_universe

    output = tmp_path / "risk.json"
    output.write_text("previous-attested-bytes\n", encoding="utf-8")
    document = {
        "schema_version": bundle.BUNDLE_SCHEMA_VERSION,
        "provenance": bundle.BUNDLE_PROVENANCE,
        "status": "PENDING",
        "producer": "tools.paper_research_risk_bundle",
        "producer_contract": "REAL_PIT_INPUTS_NO_SYNTHETIC_DEFAULTS_DYNAMIC_FORWARD_GATE",
        "dataset_version": "d" * 64,
        "config_hash": "e" * 64,
        "alphas": {"challenger": {"status": "PENDING"}},
        "missing_inputs": ["alpha_metadata"],
        "synthetic_defaults": False,
        "execution_authority_granted": False,
        "order_submission_allowed": False,
    }
    views = iter(
        (
            {"forward_ledger": {"exists": True, "sha256": "a" * 64}},
            {"forward_ledger": {"exists": True, "sha256": "b" * 64}},
        )
    )
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", "c" * 40)
    monkeypatch.setattr(
        paper_candidate_universe,
        "paper_candidate_ids",
        lambda **_kwargs: ("challenger",),
    )
    monkeypatch.setattr(
        bundle,
        "build_automatic_research_risk_bundle",
        lambda **_kwargs: document,
    )
    monkeypatch.setattr(bundle, "_source_authority_view", lambda _paths: next(views))

    assert bundle.main(["--output", str(output)]) == 0
    attestation = bundle.release_run_attestation()
    assert attestation is not None
    assert attestation["complete"] is False
    assert (
        attestation["input_hashes"]["source_authorities_before"]
        != attestation["output_hashes"]["source_authorities_after"]
    )
    assert output.read_text(encoding="utf-8") == "previous-attested-bytes\n"
    assert not tuple(tmp_path.glob(".risk.json.staged-*"))
