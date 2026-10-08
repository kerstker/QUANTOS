"""Read-only 投资者/审计证据快照 console route(W9 后端半,规格 §13)。

Serves ``evidence/investor_snapshot.json`` — produced by the offline driver
``tools/investor_snapshot.py`` (read-only over live ledgers / equity series /
published evidence artifacts, no network) — verbatim under the console's
provenance/marker conventions.  When the artifact has not been produced yet
(fresh checkout / CI) the endpoint fails closed to an honest
``SYNTHETIC_DETERMINISTIC`` + ``NO_SNAPSHOT`` body, exactly like the
live-degradation / go-live-readiness routes.

This module never touches the network, never opens a ledger or credential, and
never grants any live capability.  L2_DRY_RUN_ONLY.
"""

from __future__ import annotations

import os
from pathlib import Path
import string
from typing import Any, Dict, Optional

from fastapi import APIRouter

from quantos.runtime_paths import runtime_state_root
from services.console.api.common import with_snapshot_metadata
from services.console.api.state_artifact import read_json_artifact


router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_ROOT = runtime_state_root(cwd=_REPO_ROOT)
_FILENAME = "investor_snapshot.json"
_ARTIFACT = os.path.join(_REPO_ROOT, "evidence", _FILENAME)
_STATE_ARTIFACT = os.path.join(_STATE_ROOT, "evidence", _FILENAME)
_STATE_ROOT_CONFIGURED = bool(os.getenv("QUANTOS_STATE_ROOT", "").strip())

_VALID = "VALID"
_MISSING = "MISSING"
_INVALID = "INVALID"

_ABSENT_HONESTY_NOTE = (
    "投资者/审计证据快照尚未产出(evidence/investor_snapshot.json 缺失)——先人工运行 "
    "python -m tools.investor_snapshot(只读 live 账本/权益序列/已发布证据 artifact,无网络)。"
    "本端点只读,不授予任何能力。"
)

_PRESENT_HONESTY_NOTE = (
    "快照由 tools/investor_snapshot.py 从账本与已发布 artifact 机械生成(绝不手工编辑;"
    "report_snapshot_hash=SHA512(data_cutoff+ledger_root+strategy_versions),同一数据截止点"
    "重跑必得同一哈希)。fee_actual/funding_actual 账本不记录、恒 UNKNOWN;策略版本/代码哈希"
    "无 artifact 钉、恒 UNKNOWN;NAV 在 raw 权益序列上(live 账本不记录 venue 出入金,"
    "RAW_FLOWS_UNRECORDED);paper 线 W1 artifact 不承载、ABSENT;§13.3 双组合视图"
    "(Showcase/Growth)今天只有一条 live 轨道、尚未实例化、ABSENT。任何 None 读 "
    "unknown_fields,绝不把 None 当 0。本端点只读,不授予任何能力。"
)


def _non_negative_int_or_none(value: object) -> bool:
    return value is None or (type(value) is int and value >= 0)


def _valid_artifact(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    report_hash = data.get("report_snapshot_hash")
    return (
        data.get("artifact") == "INVESTOR_SNAPSHOT"
        and type(data.get("schema_version")) is int
        and data["schema_version"] == 1
        and type(data.get("generated_at_ms")) is int
        and data["generated_at_ms"] >= 0
        and data.get("readiness") == "L2_DRY_RUN_ONLY"
        and _non_negative_int_or_none(data.get("data_cutoff_ms"))
        and isinstance(report_hash, str)
        and len(report_hash) == 128
        and all(character in string.hexdigits for character in report_hash)
        and isinstance(data.get("hash_scheme"), dict)
        and isinstance(data.get("portfolio_views"), dict)
        and isinstance(data.get("summary"), dict)
        and isinstance(data.get("venues"), list)
        and all(isinstance(item, dict) for item in data["venues"])
        and isinstance(data.get("amendments"), list)
        and all(isinstance(item, dict) for item in data["amendments"])
        and isinstance(data.get("unknown_fields"), list)
        and all(isinstance(item, str) and bool(item.strip()) for item in data["unknown_fields"])
        and isinstance(data.get("amendment_policy"), str)
        and bool(data["amendment_policy"].strip())
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


def _investor_snapshot_payload() -> Dict[str, Any]:
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
            "data_cutoff_ms": None,
            "report_snapshot_hash": None,
            "hash_scheme": None,
            "portfolio_views": None,
            "summary": None,
            "venues": [],
            "amendments": [],
            "unknown_fields": [],
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
            "data_cutoff_ms": None,
            "report_snapshot_hash": None,
            "hash_scheme": None,
            "portfolio_views": None,
            "summary": None,
            "venues": [],
            "amendments": [],
            "unknown_fields": [],
            "honesty_note": _ABSENT_HONESTY_NOTE,
            **selection_metadata,
        }
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_INVESTOR_SNAPSHOT",
        "snapshot_status": "REAL",
        "snapshot_present": True,
        "artifact": artifact.get("artifact"),
        "generated_at_ms": artifact.get("generated_at_ms"),
        "readiness": artifact.get("readiness", "L2_DRY_RUN_ONLY"),
        "data_cutoff_ms": artifact.get("data_cutoff_ms"),
        "report_snapshot_hash": artifact.get("report_snapshot_hash"),
        "hash_scheme": artifact.get("hash_scheme"),
        "portfolio_views": artifact.get("portfolio_views"),
        "summary": artifact.get("summary"),
        "venues": artifact.get("venues") or [],
        "amendments": artifact.get("amendments") or [],
        "unknown_fields": artifact.get("unknown_fields") or [],
        "honesty_note": _PRESENT_HONESTY_NOTE,
        **selection_metadata,
    }


@router.get("/api/v1/console/investor-snapshot")
def investor_snapshot() -> Dict[str, Any]:
    return with_snapshot_metadata(_investor_snapshot_payload())


__all__ = ["router"]
