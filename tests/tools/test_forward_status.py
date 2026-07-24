from tools.forward_status import (
    alpha_forward_status,
    forward_evidence_is_current,
    refresh_evidence_available,
)


def test_attested_release_fields_are_normalized_without_counting_legacy_rows():
    status = {
        "refresh_evidence_available": True,
        "alphas": {
            "crowd_contra": {
                "n_forward_periods": 0,
                "attested_pending_decisions": 0,
                "legacy_unattested_decisions": 2,
                "evidence_state": "ADMISSION_READY_PENDING_DECISION",
            }
        },
    }
    row = alpha_forward_status(status, "crowd_contra")
    assert row["n_forward_periods"] == 0
    assert row["pending_decisions"] == 0
    assert row["attested_pending_decisions"] == 0
    assert row["legacy_unattested_decisions"] == 2
    assert row["evidence_state"] == "ADMISSION_READY_PENDING_DECISION"
    assert refresh_evidence_available(status) is True


def test_legacy_status_remains_readable_without_false_attestation():
    row = alpha_forward_status(
        {"alphas": {"top_contra": {"n_forward_periods": 1, "pending_decisions": 1}}},
        "top_contra",
    )
    assert row["pending_decisions"] == 1
    assert row["attested_pending_decisions"] is None
    assert row["legacy_unattested_decisions"] is None


def test_go_no_go_snapshot_must_match_current_forward_run():
    status = {"ran_at": 200, "alphas": {}}
    assert forward_evidence_is_current(
        status, {"forward_evidence": {"present": True, "ran_at": 199}}
    ) is False
    assert forward_evidence_is_current(
        status, {"forward_evidence": {"present": True, "ran_at": 200}}
    ) is True
