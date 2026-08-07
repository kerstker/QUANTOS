from __future__ import annotations

from pathlib import Path
import sqlite3

from quantos.contracts.hashing import stable_hash
from services.console.api.hourly_alpha_views import (
    build_hourly_alpha_snapshot,
    router,
)
from services.research.alpha_workflow_store import WorkflowStore
from services.research.paper_trading.forward_store import ForwardLedger
from services.research.trial_ledger import TrialLedger


HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
HEX_D = "d" * 64


def _initialized_stores(tmp_path: Path) -> tuple[Path, Path, Path]:
    workflow_path = tmp_path / "workflow.db"
    trial_path = tmp_path / "trials.db"
    forward_path = tmp_path / "forward.db"
    with WorkflowStore(str(workflow_path)) as workflow:
        workflow.register_batch(
            stable_hash({"batch": "console"}),
            candidate_ids=(HEX_A, HEX_B),
            panel_digest=HEX_A,
            registry_digest=HEX_B,
            policy_digest=HEX_C,
            budget_digest=HEX_D,
            event_at=100,
            generated_count=3,
        )
    with TrialLedger(str(trial_path)):
        pass
    ForwardLedger(str(forward_path), create=True)
    return workflow_path, trial_path, forward_path


def test_projection_is_read_only_and_reports_funnel_states_and_safety(tmp_path):
    workflow, trials, forward = _initialized_stores(tmp_path)
    before = {path: path.stat().st_mtime_ns for path in (workflow, trials, forward)}

    snapshot = build_hourly_alpha_snapshot(
        workflow_path=workflow,
        trial_path=trials,
        forward_path=forward,
        as_of=120,
    )

    after = {path: path.stat().st_mtime_ns for path in (workflow, trials, forward)}
    assert after == before
    assert snapshot["schema_version"] == "quantos.hourly-alpha-console.v1"
    assert snapshot["status"] == "AVAILABLE"
    assert snapshot["watermark"] == {"event_at": 100, "batch_id": stable_hash({"batch": "console"})}
    assert snapshot["batch_funnel"] == {
        "generated": 3,
        "representatives": 2,
        "validated": 0,
        "blocked": 0,
        "forward_decisions": 0,
        "forward_realizations": 0,
    }
    assert [item["state"] for item in snapshot["candidate_states"]] == ["QUEUED", "QUEUED"]
    assert snapshot["safety"] == {
        "read_only": True,
        "readiness": "L2_DRY_RUN_ONLY",
        "allow_order_submission": False,
        "allow_ai_direct_trading": False,
        "require_human_approval": True,
    }


def test_missing_authorities_are_explicitly_unavailable_and_blocked(tmp_path):
    snapshot = build_hourly_alpha_snapshot(
        workflow_path=tmp_path / "missing-workflow.db",
        trial_path=tmp_path / "missing-trials.db",
        forward_path=tmp_path / "missing-forward.db",
        as_of=120,
    )

    assert snapshot["status"] == "BLOCKED"
    assert snapshot["batch_funnel"] is None
    assert snapshot["candidate_states"] == []
    assert {value["status"] for value in snapshot["stores"].values()} == {"UNAVAILABLE"}


def test_corrupt_workflow_projection_blocks_instead_of_showing_unverified_state(tmp_path):
    workflow, trials, forward = _initialized_stores(tmp_path)
    connection = sqlite3.connect(workflow)
    connection.execute("UPDATE alpha_workflow_candidates SET state='VALIDATED' WHERE ordinal=0")
    connection.commit()
    connection.close()

    snapshot = build_hourly_alpha_snapshot(
        workflow_path=workflow,
        trial_path=trials,
        forward_path=forward,
        as_of=120,
    )

    assert snapshot["status"] == "BLOCKED"
    assert snapshot["stores"]["workflow"]["status"] == "UNAVAILABLE"
    assert snapshot["batch_funnel"] is None


def test_console_surface_is_get_only():
    route = next(item for item in router.routes if item.path == "/api/v1/console/hourly-alpha")
    assert route.methods == {"GET"}
