import pytest

from tools.paper_shadow_auto import SHADOW_CLASS, admit_shadow_preview


def _preview():
    return {
        "alpha_id": "crowd_contra",
        "decision_ts": 100,
        "run_id": "run-1",
        "n_intents": 2,
        "n_approved": 2,
        "n_rejected": 0,
        "n_paper_fills": 2,
        "reconciliation_residual": 0.0,
        "paper_execution": "PAPER_BLOCKED",
        "live_allowed": False,
        "order_submission_allowed": False,
        "broker_connectivity": False,
        "broker_order_submission": False,
        "credentials_accessed": False,
    }


def test_shadow_admission_is_filled_but_nonpromotable():
    row = admit_shadow_preview(_preview())

    assert row["paper_execution"] == "PAPER_FILLED"
    assert row["execution_class"] == SHADOW_CLASS
    assert row["official_paper_execution"] == "PAPER_BLOCKED"
    assert row["promotion_eligible"] is False
    assert row["forward_evidence_eligible"] is False
    assert row["deployment_eligible"] is False
    assert row["live_allowed"] is False
    assert row["order_submission_allowed"] is False


@pytest.mark.parametrize(
    "field",
    [
        "live_allowed",
        "order_submission_allowed",
        "broker_connectivity",
        "broker_order_submission",
        "credentials_accessed",
    ],
)
def test_shadow_admission_rejects_any_authority_surface(field):
    preview = _preview()
    preview[field] = True

    with pytest.raises(RuntimeError, match="safety boundary"):
        admit_shadow_preview(preview)


def test_shadow_admission_requires_exact_reconciliation_and_fills():
    preview = _preview()
    preview["n_paper_fills"] = 1
    with pytest.raises(RuntimeError, match="pre-trade and reconciliation"):
        admit_shadow_preview(preview)
