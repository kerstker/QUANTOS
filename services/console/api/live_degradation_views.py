"""Read-only Live-vs-Replay degradation console route (W1 后端半).

Serves ``evidence/live_degradation.json`` — produced by the offline driver
``tools/live_degradation.py`` (read-only over live stores, no network) —
verbatim under the console's provenance/marker conventions.  When the artifact
has not been produced yet (fresh checkout / CI) the endpoint fails closed to an
honest ``SYNTHETIC_DETERMINISTIC`` + ``NO_SNAPSHOT`` body with empty
reports/summary, exactly like the go-live-readiness / alpha-lifecycle routes.

This module never touches the network, never opens a ledger or credential, and
never grants any live capability.  L2_DRY_RUN_ONLY.
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
_FILENAME = "live_degradation.json"
_ARTIFACT = os.path.join(_REPO_ROOT, "evidence", _FILENAME)
_STATE_ARTIFACT = os.path.join(_STATE_ROOT, "evidence", _FILENAME)
_STATE_ROOT_CONFIGURED = bool(os.getenv("QUANTOS_STATE_ROOT", "").strip())

_VALID = "VALID"
_MISSING = "MISSING"
_INVALID = "INVALID"

_ABSENT_HONESTY_NOTE = (
    "live-vs-replay 衰减证据尚未产出(evidence/live_degradation.json 缺失)——先人工运行 "
    "python -m tools.live_degradation(只读 live 账本/前向台账/parity 记录,无网络)。"
    "本端点只读,不授予任何能力。"
)

_PRESENT_HONESTY_NOTE = (
    "testnet/demo 成交无真实市场冲击——本报告度量 运营/模型 漏损(执行延迟、滑点 vs 决策价、"
    "掉腿/漏调仓、成本假设差),不是真实生产冲击。fee_actual/funding_actual 账本不记录、恒 "
    "UNKNOWN;比率为 None 时读 degradation_ratio_reason,绝不把 None 当 0 或 1。"
    "内容为 tools/live_degradation.py 的离线诊断原样 serve。本端点只读,不授予任何能力。"
)


def _valid_artifact(data: object) -> bool:
    return (
        isinstance(data, dict)
        and data.get("artifact") == "LIVE_DEGRADATION"
        and type(data.get("schema_version")) is int
        and data["schema_version"] == 1
        and type(data.get("generated_at_ms")) is int
        and data["generated_at_ms"] >= 0
        and data.get("readiness") == "L2_DRY_RUN_ONLY"
        and isinstance(data.get("cost_model"), dict)
        and isinstance(data.get("replay_construction"), str)
        and bool(data["replay_construction"].strip())
        and isinstance(data.get("honesty_note"), str)
        and bool(data["honesty_note"].strip())
        and isinstance(data.get("summary"), dict)
        and isinstance(data.get("reports"), list)
        and all(isinstance(item, dict) for item in data["reports"])
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
    distinct_state = _STATE_ROOT_CONFIGURED or (
        os.path.normcase(os.path.abspath(_STATE_ARTIFACT))
        != os.path.normcase(os.path.abspath(_ARTIFACT))
    )
    if distinct_state:
        state_status, state_artifact = _read_candidate(
            _STATE_ARTIFACT, trusted_root=_STATE_ROOT
        )
        selected = _selection_from_state(state_status, state_artifact)
        if selected is not None:
            return selected

    release_status, release_artifact = _read_candidate(
        _ARTIFACT, trusted_root=_REPO_ROOT
    )
    if distinct_state:
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
    if path is None:
        return _select_artifact()[0]
    status, artifact = _read_candidate(path, trusted_root=_REPO_ROOT)
    return artifact if status == _VALID else None


def _live_degradation_payload() -> Dict[str, Any]:
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
            "summary": None,
            "reports": [],
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
            # 显式 NO_SNAPSHOT:artifact 缺席是"驱动器未跑",不是数据被质量门拦。
            "snapshot_status": "NO_SNAPSHOT",
            "snapshot_present": False,
            "artifact": None,
            "generated_at_ms": None,
            "readiness": "L2_DRY_RUN_ONLY",
            "summary": None,
            "reports": [],
            "honesty_note": _ABSENT_HONESTY_NOTE,
            **selection_metadata,
        }
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_LIVE_DEGRADATION",
        "snapshot_status": "REAL",
        "snapshot_present": True,
        "artifact": artifact.get("artifact"),
        "generated_at_ms": artifact.get("generated_at_ms"),
        "readiness": artifact.get("readiness", "L2_DRY_RUN_ONLY"),
        "summary": artifact.get("summary"),
        "reports": artifact.get("reports") or [],
        "honesty_note": _PRESENT_HONESTY_NOTE,
        **selection_metadata,
    }


@router.get("/api/v1/console/live-degradation")
def live_degradation() -> Dict[str, Any]:
    return with_snapshot_metadata(_live_degradation_payload())


__all__ = ["router"]
