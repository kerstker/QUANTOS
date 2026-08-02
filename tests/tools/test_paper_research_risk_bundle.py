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
