"""Read-only projection of the L2 hourly alpha workflow authorities."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import time
from typing import Any

from fastapi import APIRouter
from quantos.runtime_paths import runtime_state_root
from services.research.alpha_workflow_store import AlphaWorkflowError, WorkflowStore
from services.research.paper_trading.forward_store import ForwardLedger
from services.research.ports.persistence import ForwardLedgerError
from services.research.trial_ledger import TrialLedger, TrialLedgerError


router = APIRouter(tags=["hourly-alpha"])
_SCHEMA_VERSION = "quantos.hourly-alpha-console.v1"
_FRESH_SECONDS = 7_200
_INVALIDATION_CODE = "INVALIDATED_PERP_LEDGER_V1"
_AUTHORITY_MODE = "QUARANTINED_LEGACY_AUTHORITY"
_REPO_ROOT = Path(__file__).resolve().parents[3]
def _hourly_alpha_cache_path() -> Path:
    return runtime_state_root(cwd=_REPO_ROOT) / "data" / "cache"


def _read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _unavailable(path: Path, reason: str) -> dict[str, Any]:
    del path
    return {"status": "UNAVAILABLE", "reason": reason}


def _workflow_projection(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    try:
        connection = _read_only(path)
        with connection:
            required = {"alpha_workflow_batches", "alpha_workflow_candidates", "alpha_workflow_events"}
            if not required.issubset(_tables(connection)):
                raise sqlite3.DatabaseError("workflow schema is incomplete")
            batch = connection.execute(
                "SELECT * FROM alpha_workflow_batches ORDER BY event_at DESC,batch_id DESC LIMIT 1"
            ).fetchone()
            if batch is None:
                return {"status": "AVAILABLE"}, None
            batch_id = str(batch["batch_id"])
        with WorkflowStore(str(path), create=False) as store:
            snapshot = store.snapshot(batch_id)
            return {"status": "AVAILABLE"}, {
                "batch_id": snapshot.batch_id,
                "state": snapshot.state,
                "event_at": snapshot.event_at,
                "generated_count": snapshot.generated_count,
                "checkpoint_watermark": snapshot.checkpoint_watermark,
                "candidates": [
                    {
                        "ordinal": item.ordinal,
                        "candidate_id": item.candidate_id,
                        "state": item.state,
                        "trial_id": item.trial_id,
                        "artifact_commitment": item.artifact_commitment,
                    }
                    for item in snapshot.candidates
                ],
            }
    except (FileNotFoundError, OSError, sqlite3.Error, ValueError, AlphaWorkflowError) as exc:
        return _unavailable(path, type(exc).__name__), None
    finally:
        if "connection" in locals():
            connection.close()


def _trial_projection(path: Path) -> dict[str, Any]:
    try:
        with TrialLedger(str(path), create=False, read_only=True) as ledger:
            ledger.verify()
        connection = _read_only(path)
        with connection:
            required = {"trial_events", "validation_receipt_events"}
            if not required.issubset(_tables(connection)):
                raise sqlite3.DatabaseError("trial schema is incomplete")
            count = int(connection.execute("SELECT COUNT(DISTINCT trial_id) FROM trial_events").fetchone()[0])
            return {"status": "AVAILABLE", "trials": count}
    except (FileNotFoundError, OSError, sqlite3.Error, ValueError, TrialLedgerError) as exc:
        return _unavailable(path, type(exc).__name__)
    finally:
        if "connection" in locals():
            connection.close()


def _forward_projection(path: Path, candidate_ids: tuple[str, ...]) -> tuple[dict[str, Any], int, int]:
    try:
        with ForwardLedger(str(path), create=False, read_only=True) as ledger:
            ledger.verify_hourly()
            decisions = sum(len(ledger.hourly_decisions(alpha_id)) for alpha_id in candidate_ids)
            candidate_set = set(candidate_ids)
            realizations = sum(
                row["alpha_id"] in candidate_set for row in ledger.hourly_realizations()
            )
            return {"status": "AVAILABLE"}, decisions, realizations
    except (FileNotFoundError, OSError, sqlite3.Error, ValueError, ForwardLedgerError) as exc:
        return _unavailable(path, type(exc).__name__), 0, 0


def build_hourly_alpha_snapshot(
    *,
    workflow_path: Path,
    trial_path: Path,
    forward_path: Path,
    as_of: int,
) -> dict[str, Any]:
    if type(as_of) is not int or as_of < 0:
        raise ValueError("as_of must be a non-negative integer")
    workflow_status, workflow = _workflow_projection(Path(workflow_path))
    trial_status = _trial_projection(Path(trial_path))
    candidate_ids = () if workflow is None else tuple(item["candidate_id"] for item in workflow["candidates"])
    forward_status, decisions, realizations = _forward_projection(Path(forward_path), candidate_ids)
    stores = {"workflow": workflow_status, "trials": trial_status, "forward": forward_status}
    available = all(item["status"] == "AVAILABLE" for item in stores.values())
    if available and workflow is not None:
        candidates = workflow["candidates"]
        funnel: dict[str, int] | None = {
            "generated": workflow["generated_count"],
            "representatives": len(candidates),
            "validated": sum(item["state"] == "VALIDATED" for item in candidates),
            "blocked": sum(item["state"] == "BLOCKED" for item in candidates),
            "forward_decisions": decisions,
            "forward_realizations": realizations,
        }
        candidate_states = candidates
        watermark = {"event_at": workflow["event_at"], "batch_id": workflow["batch_id"]}
        age = max(0, as_of - workflow["event_at"])
        freshness = {"status": "CURRENT" if age <= _FRESH_SECONDS else "STALE", "age_seconds": age}
    elif available:
        funnel = {
            "generated": 0,
            "representatives": 0,
            "validated": 0,
            "blocked": 0,
            "forward_decisions": 0,
            "forward_realizations": 0,
        }
        candidate_states = []
        watermark = None
        freshness = {"status": "NO_RUN", "age_seconds": None}
    else:
        funnel = None
        candidate_states = []
        watermark = None
        freshness = {"status": "UNAVAILABLE", "age_seconds": None}
    provenance = "REAL_HOURLY_ALPHA_AUTHORITIES"
    snapshot_ids = [] if workflow is None else [workflow["batch_id"]]
    if not available:
        snapshot_status = "UNAVAILABLE"
    elif workflow is None:
        snapshot_status = "NO_SNAPSHOT"
    elif freshness["status"] == "STALE":
        snapshot_status = "STALE"
    else:
        snapshot_status = "REAL"
    return {
        "source": "HOURLY_ALPHA_SQLITE_AUTHORITIES",
        "read_only": True,
        "api_version": "v1",
        "data_provenance": provenance,
        "snapshot_id": snapshot_ids[-1] if snapshot_ids else None,
        "snapshot_ids": snapshot_ids,
        "snapshot_status": snapshot_status,
        "data_quality": {
            "status": snapshot_status,
            "provenance": provenance,
            "snapshot_ids": snapshot_ids,
        },
        "schema_version": _SCHEMA_VERSION,
        "as_of": as_of,
        "status": "AVAILABLE" if available else "BLOCKED",
        "freshness": freshness,
        "watermark": watermark,
        "batch_funnel": funnel,
        "candidate_states": candidate_states,
        "stores": stores,
        "safety": {
            "read_only": True,
            "readiness": "L2_DRY_RUN_ONLY",
            "allow_order_submission": False,
            "allow_ai_direct_trading": False,
            "require_human_approval": True,
        },
    }


@router.get("/api/v1/console/hourly-alpha")
def hourly_alpha_status() -> dict[str, Any]:
    cache = _hourly_alpha_cache_path()
    snapshot = build_hourly_alpha_snapshot(
        workflow_path=cache / "hourly_alpha_workflow.db",
        trial_path=cache / "hourly_alpha_trials.db",
        forward_path=cache / "hourly_alpha_forward.db",
        as_of=int(time.time()),
    )
    # The scheduled writer for these V1 stores is hard-quarantined by the L2
    # release policy.  Even if migrated files reappear, the console must never
    # present them as a current authority.  Preserve store diagnostics while
    # hiding retained candidates/metrics and make the planned state explicit.
    return {
        **snapshot,
        "snapshot_status": "UNAVAILABLE",
        "data_quality": {
            **snapshot["data_quality"],
            "status": "UNAVAILABLE",
            "reason": _INVALIDATION_CODE,
        },
        "status": "BLOCKED",
        "freshness": {"status": "UNAVAILABLE", "age_seconds": None},
        "watermark": None,
        "batch_funnel": None,
        "candidate_states": [],
        "stores": {
            name: {"status": "UNAVAILABLE", "reason": _INVALIDATION_CODE}
            for name in ("workflow", "trials", "forward")
        },
        "authority_mode": _AUTHORITY_MODE,
        "authoritative": False,
        "evidence_status": "DIAGNOSTIC_ONLY",
        "invalidation_code": _INVALIDATION_CODE,
    }


__all__ = ["router", "build_hourly_alpha_snapshot", "hourly_alpha_status"]
