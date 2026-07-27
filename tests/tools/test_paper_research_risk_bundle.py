import json

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

