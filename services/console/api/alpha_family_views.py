"""Read-only view of the automatic Alpha-family discovery ledger."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter

from quantos.runtime_paths import runtime_state_root
from services.console.api.common import with_snapshot_metadata
from services.console.api.sqlite_snapshot import (
    SqliteSnapshotError,
    stable_sqlite_snapshot,
)
from services.research.alpha_family_catalog import family_specs
from services.research.alpha_family_registry import AlphaFamilyRegistry


router = APIRouter()
_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_ROOT = runtime_state_root(cwd=_REPO_ROOT)
_DB = os.path.join(_STATE_ROOT, "data", "cache", "alpha_family_registry.db")


def _payload() -> Dict[str, Any]:
    if not os.path.exists(_DB):
        return {
            "source": "BACKEND_COMPUTED_SNAPSHOT",
            "read_only": True,
            "api_version": "0.1.0",
            "data_provenance": "REAL_ALPHA_FAMILY_LEDGER",
            "snapshot_status": "NO_SNAPSHOT",
            "registry_present": False,
            "chain_valid": False,
            "families": [],
            "honesty_note": "alpha family discovery ledger not produced; no candidate status is inferred",
        }
    try:
        with stable_sqlite_snapshot(Path(_DB)) as snapshot_path:
            with AlphaFamilyRegistry(
                str(snapshot_path), create=False, read_only=True
            ) as store:
                candidates = store.candidates()
                current_specs = {spec.candidate_id: spec for spec in family_specs()}
                families = []
                for row in candidates:
                    evaluations = row.pop("evaluations", [])
                    paper_runs = row.pop("paper_runs", [])
                    # The ledger keeps immutable research identity.  Refresh
                    # human-readable metadata from the current catalogue so a
                    # wording/encoding correction is visible without rewriting
                    # historical evidence rows.
                    spec = current_specs.get(row.get("candidate_id"))
                    if spec is not None:
                        row.update({
                            "family_id": spec.family_id,
                            "name_zh": spec.name_zh,
                            "complementarity": spec.complementarity,
                            "intended_frequency": spec.intended_frequency,
                            "evaluation_frequency": spec.evaluation_frequency,
                            "required_fields": list(spec.required_fields),
                            "paper_eligible": bool(spec.paper_eligible),
                            "notes": spec.notes,
                        })
                    families.append({
                        **row,
                        "latest_evaluation": evaluations[-1] if evaluations else None,
                        "latest_paper_run": paper_runs[-1] if paper_runs else None,
                        "evaluation_count": len(evaluations),
                        "paper_run_count": len(paper_runs),
                    })
                return {
                    "source": "BACKEND_COMPUTED_SNAPSHOT",
                    "read_only": True,
                    "api_version": "0.1.0",
                    "data_provenance": "REAL_ALPHA_FAMILY_LEDGER",
                    "snapshot_status": "REAL",
                    "registry_present": True,
                    "chain_valid": store.verify_chain(),
                    "event_count": store.event_count(),
                    "families": families,
                    "live_submission": False,
                    "readiness": "L2_DRY_RUN_ONLY",
                    "honesty_note": "paper-only evidence; APPROVED/live authority is never created by this view",
                }
    except SqliteSnapshotError as exc:
        return {
            "source": "BACKEND_COMPUTED_SNAPSHOT",
            "read_only": True,
            "api_version": "0.1.0",
            "data_provenance": "REAL_ALPHA_FAMILY_LEDGER",
            "snapshot_status": "UNAVAILABLE",
            "registry_present": True,
            "chain_valid": False,
            "families": [],
            "error_type": type(exc).__name__,
            "honesty_note": "family ledger could not be captured as one stable snapshot",
        }
    except Exception as exc:
        return {
            "source": "BACKEND_COMPUTED_SNAPSHOT",
            "read_only": True,
            "api_version": "0.1.0",
            "data_provenance": "REAL_ALPHA_FAMILY_LEDGER",
            "snapshot_status": "CORRUPT_OR_UNREADABLE",
            "registry_present": True,
            "chain_valid": False,
            "families": [],
            "error_type": type(exc).__name__,
            "honesty_note": "family ledger read failed closed",
        }


@router.get("/api/v1/console/alpha-families")
async def alpha_families() -> Dict[str, Any]:
    return with_snapshot_metadata(_payload())


__all__ = ["router"]
