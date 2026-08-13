"""Read-only snapshot and delta views for the MFT Testnet execution loop."""
from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from fastapi import APIRouter, Query

from quantos.runtime_paths import runtime_state_root
from services.monitoring.mft_read_model import (
    MftFreshnessPolicy,
    MftReadModelError,
    MftReadModelStore,
    resnapshot_required_delta,
    unavailable_mft_snapshot,
)


router = APIRouter(tags=["mft-execution"])
_REPO_ROOT = Path(__file__).resolve().parents[3]
_FRESHNESS_POLICY = MftFreshnessPolicy(
    policy_id="mft-console-freshness",
    version="1",
    max_age_ms=7_200_000,
)


def _mft_read_model_path() -> Path:
    return runtime_state_root(cwd=_REPO_ROOT) / "data" / "cache" / "mft_read_model.db"


def build_mft_execution_snapshot(*, path: Path, as_of_ms: int) -> dict[str, Any]:
    try:
        with MftReadModelStore(str(path), read_only=True) as store:
            return store.snapshot(
                as_of_ms=as_of_ms,
                freshness_policy=_FRESHNESS_POLICY,
            )
    except MftReadModelError as exc:
        return unavailable_mft_snapshot(as_of_ms=as_of_ms, reason=exc.code)
    except (OSError, ValueError, TypeError):
        return unavailable_mft_snapshot(as_of_ms=as_of_ms, reason="STORE_UNAVAILABLE")


def build_mft_execution_delta(
    *,
    path: Path,
    cursor: str,
    as_of_ms: int,
    limit: int = 500,
) -> dict[str, Any]:
    try:
        with MftReadModelStore(str(path), read_only=True) as store:
            return store.delta(
                cursor,
                as_of_ms=as_of_ms,
                freshness_policy=_FRESHNESS_POLICY,
                limit=limit,
            )
    except MftReadModelError as exc:
        return resnapshot_required_delta(exc.code)
    except (OSError, ValueError, TypeError):
        return resnapshot_required_delta("STORE_UNAVAILABLE")


@router.get("/api/v1/console/mft-execution")
def mft_execution_snapshot() -> dict[str, Any]:
    path = _mft_read_model_path()
    return build_mft_execution_snapshot(path=path, as_of_ms=time.time_ns() // 1_000_000)


@router.get("/api/v1/console/mft-execution/delta")
def mft_execution_delta(
    cursor: str = Query(..., min_length=66, max_length=96),
    limit: int = Query(500, ge=1, le=1_000),
) -> dict[str, Any]:
    path = _mft_read_model_path()
    return build_mft_execution_delta(
        path=path,
        cursor=cursor,
        as_of_ms=time.time_ns() // 1_000_000,
        limit=limit,
    )


__all__ = [
    "router",
    "build_mft_execution_delta",
    "build_mft_execution_snapshot",
    "_mft_read_model_path",
    "mft_execution_delta",
    "mft_execution_snapshot",
]
