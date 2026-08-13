"""Read-only Go-Live readiness console route ("Go-Live 上线向导" 后端半).

Serves ``evidence/go_live_readiness.json`` — produced by the offline driver
``tools/go_live_readiness.py`` (local files + offline signal computation, no
network) — verbatim under the console's provenance/marker conventions.  When
the artifact has not been produced yet (fresh checkout / CI) the endpoint
fails closed to an honest ``SYNTHETIC_DETERMINISTIC`` + ``NO_SNAPSHOT`` body
with empty venues/strategies/guidance, exactly like the other artifact-backed
routes (mexc-market/mexc-strategy).

This module never touches the network, never reads credentials or mandate
contents itself (the driver already redacted/summarized them), and never
grants any live capability.  L2_DRY_RUN_ONLY.
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
_FILENAME = "go_live_readiness.json"
_GO_LIVE_ARTIFACT = os.path.join(_REPO_ROOT, "evidence", _FILENAME)
_STATE_ARTIFACT = os.path.join(_STATE_ROOT, "evidence", _FILENAME)
_STATE_ROOT_CONFIGURED = bool(os.getenv("QUANTOS_STATE_ROOT", "").strip())

_VALID = "VALID"
_MISSING = "MISSING"
_INVALID = "INVALID"

_ABSENT_HONESTY_NOTE = (
    "go-live 就绪证据尚未产出(evidence/go_live_readiness.json 缺失)——先人工运行 "
    "python -m tools.go_live_readiness(全本地输入,无网络);key 权限证据需先人工运行 "
    "python -m tools.verify_key_permissions --venue <exchange> --write-evidence "
    "evidence/key_permissions_<venue>.json。本端点只读,不授予任何能力。"
)

_PRESENT_HONESTY_NOTE = (
    "Go-live 就绪清单:全部项绿也只是'具备提交人工评审的材料',绝不代表可以实盘——L2 边界 "
    "(SafetyConfig 五旗标 + readiness pin)不变,live 仍需多方人工 mandate + 人工 --arm-live。"
    "内容为 tools/go_live_readiness.py 的离线诊断原样serve;guidance.suggested_caps 是给真人"
    "重签 mandate 的建议数字,代码不代签。本端点只读,不授予任何能力。"
)


def _valid_artifact(data: object) -> bool:
    # The historical producer predates schema_version. Keep its real contract
    # strict without inventing a field that tracked release artifacts do not have.
    return (
        isinstance(data, dict)
        and data.get("artifact") == "GO_LIVE_READINESS"
        and type(data.get("generated_at_ms")) is int
        and data["generated_at_ms"] >= 0
        and data.get("readiness") == "L2_DRY_RUN_ONLY"
        and isinstance(data.get("venues"), list)
        and all(isinstance(item, dict) for item in data["venues"])
        and isinstance(data.get("strategies"), dict)
        and isinstance(data.get("guidance"), dict)
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
    distinct_state = _STATE_ROOT_CONFIGURED or (
        os.path.normcase(os.path.abspath(_STATE_ARTIFACT))
        != os.path.normcase(os.path.abspath(_GO_LIVE_ARTIFACT))
    )
    if distinct_state:
        state_status, state_artifact = _read_candidate(
            _STATE_ARTIFACT, trusted_root=_STATE_ROOT
        )
        selected = _selection_from_state(state_status, state_artifact)
        if selected is not None:
            return selected

    release_status, release_artifact = _read_candidate(
        _GO_LIVE_ARTIFACT, trusted_root=_REPO_ROOT
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


def _go_live_readiness_payload() -> Dict[str, Any]:
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
            "venues": [],
            "strategies": {},
            "guidance": None,
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
            # (DATA_BLOCKED 是另一语义,见 mexc_views 的同款注释)。
            "snapshot_status": "NO_SNAPSHOT",
            "snapshot_present": False,
            "artifact": None,
            "generated_at_ms": None,
            "readiness": "L2_DRY_RUN_ONLY",
            "venues": [],
            "strategies": {},
            "guidance": None,
            "honesty_note": _ABSENT_HONESTY_NOTE,
            **selection_metadata,
        }
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_GO_LIVE_READINESS",
        "snapshot_status": "REAL",
        "snapshot_present": True,
        "artifact": artifact.get("artifact"),
        "generated_at_ms": artifact.get("generated_at_ms"),
        "readiness": artifact.get("readiness", "L2_DRY_RUN_ONLY"),
        "venues": artifact.get("venues") or [],
        "strategies": artifact.get("strategies") or {},
        "guidance": artifact.get("guidance"),
        "honesty_note": _PRESENT_HONESTY_NOTE,
        **selection_metadata,
    }


@router.get("/api/v1/console/go-live-readiness")
async def go_live_readiness() -> Dict[str, Any]:
    return with_snapshot_metadata(_go_live_readiness_payload())


__all__ = ["router"]
