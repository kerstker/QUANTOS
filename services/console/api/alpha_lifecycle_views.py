"""Read-only Alpha Lifecycle Cockpit console route ("Alpha 生命周期驾驶舱" 后端半).

Serves ``evidence/alpha_lifecycle_cockpit.json`` — produced by the offline driver
``tools/alpha_lifecycle_cockpit.py`` (aggregation of existing local evidence stores
only: dry_run.db / paper_track.db / IC panel / falsification / scenario-tail /
capacity / combo / live-track status / maintenance authority; no network, no new
research math) — verbatim under the console's provenance/marker conventions.  When
the artifact has not been produced yet (fresh checkout / CI) the endpoint fails
closed to an honest ``SYNTHETIC_DETERMINISTIC`` + ``NO_SNAPSHOT`` body with empty
alphas, exactly like the other artifact-backed routes (go-live-readiness,
mexc-market/mexc-strategy).

This module never touches the network, never reads credentials, and never grants
any capability: ``derived_stage`` is a mechanical evidence-chain derivation, not a
promotion endorsement, and ``lifecycle_state`` stays null because no per-alpha
lifecycle-SM state is persisted on disk.  L2_DRY_RUN_ONLY.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter

from quantos.runtime_paths import runtime_state_root
from services.console.api.common import with_snapshot_metadata
from services.console.api.state_artifact import read_json_artifact


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
    "Alpha 生命周期驾驶舱:对既有证据仓的只读聚合,没有新研究数学;缺证据阶段即 ABSENT,"
    "绝不编造。derived_stage 是机械推导(见 stage_legend),非晋级背书;lifecycle_state 恒为 "
    "null(状态机无落盘)。capacity 为 UNCALIBRATED_PRINCIPLED 区间带(诊断非门禁);"
    "attestation/mandate 就绪归 /api/v1/console/go-live-readiness。内容为 "
    "tools/alpha_lifecycle_cockpit.py 的离线诊断原样 serve;L2 边界不变,本端点只读,"
    "不授予任何能力。"
)


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
        return {
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
        }
    if artifact is None:
        return {
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
        }
    return {
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
    }


@router.get("/api/v1/console/alpha-lifecycle")
async def alpha_lifecycle() -> Dict[str, Any]:
    return with_snapshot_metadata(_alpha_lifecycle_payload())


__all__ = ["router"]
