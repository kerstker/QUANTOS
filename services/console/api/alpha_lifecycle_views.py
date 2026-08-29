"""Read-only Alpha Lifecycle Cockpit console route ("Alpha 生命周期驾驶舱" 后端半).

Reads ``evidence/alpha_lifecycle_cockpit.json`` — produced by the offline driver
``tools/alpha_lifecycle_cockpit.py`` (aggregation of existing local evidence stores
only: dry_run.db / paper_track.db / IC panel / falsification / scenario-tail /
capacity / combo / live-track status / maintenance authority; no network, no new
research math) — and exposes only an invalidated identity projection.  Schema-v1
economic metrics and lifecycle stages are retained on disk for audit, but cannot
cross the console boundary after ``INVALIDATED_PERP_LEDGER_V1``.  When
the artifact has not been produced yet (fresh checkout / CI) the endpoint fails
closed to an honest ``SYNTHETIC_DETERMINISTIC`` + ``NO_SNAPSHOT`` body with empty
alphas, exactly like the other artifact-backed routes (go-live-readiness,
mexc-market/mexc-strategy).

This module never touches the network, never reads credentials, and never grants
any capability.  Every retained alpha is projected as non-authoritative and its
derived stage is replaced by the legacy invalidation code.  L2_DRY_RUN_ONLY.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter

from quantos.runtime_paths import runtime_state_root
from services.console.api.backtest_evidence import (
    DIAGNOSTIC_EVIDENCE_STATUS,
    scrub_backtest_payload,
)
from services.console.api.common import with_snapshot_metadata
from services.console.api.state_artifact import read_json_artifact
from services.execution.backtest_authority import LEGACY_INVALIDATION_CODE


router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_ROOT = runtime_state_root(cwd=_REPO_ROOT)
_LIFECYCLE_FILENAME = "alpha_lifecycle_cockpit.json"
_LIFECYCLE_ARTIFACT = os.path.join(_REPO_ROOT, "evidence", _LIFECYCLE_FILENAME)
_STATE_ARTIFACT = os.path.join(
    _STATE_ROOT, "evidence", _LIFECYCLE_FILENAME
)
_STATE_ROOT_CONFIGURED = bool(os.getenv("QUANTOS_STATE_ROOT", "").strip())

_VALID = "VALID"
_MISSING = "MISSING"
_INVALID = "INVALID"

_ABSENT_HONESTY_NOTE = (
    "alpha 生命周期证据尚未产出(evidence/alpha_lifecycle_cockpit.json 缺失)——先人工运行 "
    "python -m tools.alpha_lifecycle_cockpit(全本地输入:既有证据仓只读聚合,无网络)。"
    "本端点只读,不授予任何能力。"
)

_PRESENT_HONESTY_NOTE = (
    "Alpha 生命周期 schema-v1 artifact 仅为留存审计输入。因永续合约账本 v1 已整体失效,"
    "本端点只暴露 alpha 身份和失效阶段,不返回历史回测、纸面、组合、容量、压力或执行指标;"
    "所有行均为 DIAGNOSTIC_ONLY / INVALIDATED_PERP_LEDGER_V1,不可用于晋级、候选选择或"
    "运行授权。原 artifact 不修改、不删除;L2 边界不变,本端点只读。"
)

_INVALIDATED_STAGE_NOTE = (
    "Retained schema-v1 lifecycle evidence is non-authoritative because the "
    "underlying perpetual ledger was invalidated."
)


def _invalidated_alpha(alpha: Dict[str, Any]) -> Dict[str, Any]:
    """Keep identity while removing every retained lifecycle claim and metric."""

    alpha_id = str(alpha.get("alpha_id") or "UNKNOWN_ALPHA")
    display_name = str(alpha.get("display_name") or alpha_id)
    raw_stages = alpha.get("stages")
    stage_names = (
        sorted(str(name) for name in raw_stages)
        if isinstance(raw_stages, dict)
        else []
    )
    stages = {
        name: {
            "status": LEGACY_INVALIDATION_CODE,
            "authoritative": False,
            "evidence_status": DIAGNOSTIC_EVIDENCE_STATUS,
            "invalidation_code": LEGACY_INVALIDATION_CODE,
        }
        for name in stage_names
    }
    projected: Dict[str, Any] = {
        "alpha_id": alpha_id,
        "display_name": display_name,
        "derived_stage": LEGACY_INVALIDATION_CODE,
        "lifecycle_state": None,
        "stages": stages,
        "warnings": [LEGACY_INVALIDATION_CODE],
        "authoritative": False,
        "evidence_status": DIAGNOSTIC_EVIDENCE_STATUS,
        "invalidation_code": LEGACY_INVALIDATION_CODE,
    }
    namespace = alpha.get("namespace")
    if isinstance(namespace, str) and namespace.strip():
        projected["namespace"] = namespace
    return projected


def _invalidated_projection(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a closed projection without mutating the retained artifact."""

    raw_alphas = payload.get("alphas")
    # Scrub the caller-composed envelope first. Rebuild the identity projection
    # afterwards so stage names such as ``capacity`` or ``portfolio`` cannot be
    # mistaken for an economic metric key by the generic boundary scrubber.
    projected = scrub_backtest_payload(dict(payload))
    projected["alphas"] = (
        [
            _invalidated_alpha(alpha)
            for alpha in raw_alphas
            if isinstance(alpha, dict)
        ]
        if isinstance(raw_alphas, list)
        else []
    )
    projected["stage_legend"] = {
        LEGACY_INVALIDATION_CODE: _INVALIDATED_STAGE_NOTE,
    }
    if projected.get("snapshot_present") is True:
        projected["honesty_note"] = _PRESENT_HONESTY_NOTE
    return projected


def _valid_artifact(data: object) -> bool:
    return (
        isinstance(data, dict)
        and data.get("artifact") == "ALPHA_LIFECYCLE_COCKPIT"
        and type(data.get("schema_version")) is int
        and data["schema_version"] == 1
        and type(data.get("generated_at_ms")) is int
        and data["generated_at_ms"] >= 0
        and data.get("readiness") == "L2_DRY_RUN_ONLY"
        and isinstance(data.get("alphas"), list)
        and all(isinstance(item, dict) for item in data["alphas"])
        and isinstance(data.get("stage_legend"), dict)
        and isinstance(data.get("honesty_note"), str)
        and bool(data["honesty_note"].strip())
    )


def _read_candidate(
    path: str, *, trusted_root: str | os.PathLike[str]
) -> tuple[str, Optional[Dict[str, Any]]]:
    status, data = read_json_artifact(path, trusted_root=trusted_root)
    if status != _VALID:
        return status, None
    return (_VALID, data) if _valid_artifact(data) else (_INVALID, None)


def _selection_from_state(status: str, artifact: Optional[Dict[str, Any]]):
    if status == _VALID:
        return artifact, "QUANTOS_STATE_ROOT", None, False
    if status == _INVALID:
        return None, "QUANTOS_STATE_ROOT", "STATE_SNAPSHOT_INVALID", True
    return None


def _select_artifact():
    """Select one whole artifact; mutable state always outranks release evidence."""
    distinct_state = _STATE_ROOT_CONFIGURED or (
        os.path.normcase(os.path.abspath(_STATE_ARTIFACT))
        != os.path.normcase(os.path.abspath(_LIFECYCLE_ARTIFACT))
    )
    if distinct_state:
        state_status, state_artifact = _read_candidate(
            _STATE_ARTIFACT, trusted_root=_STATE_ROOT
        )
        selected = _selection_from_state(state_status, state_artifact)
        if selected is not None:
            return selected

    release_status, release_artifact = _read_candidate(
        _LIFECYCLE_ARTIFACT, trusted_root=_REPO_ROOT
    )
    if distinct_state:
        # Do not return an immutable fallback if state appeared while it was read.
        state_status, state_artifact = _read_candidate(
            _STATE_ARTIFACT, trusted_root=_STATE_ROOT
        )
        selected = _selection_from_state(state_status, state_artifact)
        if selected is not None:
            return selected
    if release_status == _VALID:
        return release_artifact, "RELEASE_BASELINE", None, False
    if release_status == _INVALID:
        return None, "RELEASE_BASELINE", "RELEASE_SNAPSHOT_INVALID", False
    return None, None, None, False


def _load_artifact(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Compatibility helper: load one strictly validated artifact."""
    if path is None:
        return _select_artifact()[0]
    status, artifact = _read_candidate(path, trusted_root=_REPO_ROOT)
    return artifact if status == _VALID else None


def _alpha_lifecycle_payload() -> Dict[str, Any]:
    artifact, artifact_provenance, error_reason, fallback_suppressed = _select_artifact()
    selection_metadata = {
        "artifact_provenance": artifact_provenance,
        "snapshot_error_reason": error_reason,
        "release_fallback_suppressed": fallback_suppressed,
    }
    if error_reason is not None:
        suppressed_note = (
            " release fallback suppressed because mutable state is present."
            if fallback_suppressed
            else ""
        )
        return _invalidated_projection({
            "source": "BACKEND_COMPUTED_SNAPSHOT",
            "read_only": True,
            "api_version": "0.1.0",
            "data_provenance": "UNAVAILABLE",
            "snapshot_status": "UNAVAILABLE",
            "snapshot_present": False,
            "artifact": None,
            "generated_at_ms": None,
            "readiness": "L2_DRY_RUN_ONLY",
            "alphas": [],
            "stage_legend": {},
            "honesty_note": (
                f"{error_reason}: evidence artifact failed strict schema validation;"
                f"{suppressed_note}"
            ),
            **selection_metadata,
        })
    if artifact is None:
        return _invalidated_projection({
            "source": "BACKEND_COMPUTED_SNAPSHOT",
            "read_only": True,
            "api_version": "0.1.0",
            "data_provenance": "SYNTHETIC_DETERMINISTIC",
            # 显式 NO_SNAPSHOT:artifact 缺席是"驱动器未跑",不是数据被质量门拦
            # (DATA_BLOCKED 是另一语义,见 go_live_views 的同款注释)。
            "snapshot_status": "NO_SNAPSHOT",
            "snapshot_present": False,
            "artifact": None,
            "generated_at_ms": None,
            "readiness": "L2_DRY_RUN_ONLY",
            "alphas": [],
            "stage_legend": {},
            "honesty_note": _ABSENT_HONESTY_NOTE,
            **selection_metadata,
        })
    return _invalidated_projection({
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_ALPHA_LIFECYCLE",
        "snapshot_status": "REAL",
        "snapshot_present": True,
        "artifact": artifact.get("artifact"),
        "generated_at_ms": artifact.get("generated_at_ms"),
        "readiness": artifact.get("readiness", "L2_DRY_RUN_ONLY"),
        "alphas": artifact.get("alphas") or [],
        "stage_legend": artifact.get("stage_legend") or {},
        "honesty_note": _PRESENT_HONESTY_NOTE,
        **selection_metadata,
    })


@router.get("/api/v1/console/alpha-lifecycle")
async def alpha_lifecycle() -> Dict[str, Any]:
    return with_snapshot_metadata(_alpha_lifecycle_payload())


__all__ = ["router"]
