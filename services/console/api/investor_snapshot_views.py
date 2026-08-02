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

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter

from services.console.api.common import with_snapshot_metadata


router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ARTIFACT = os.path.join(_REPO_ROOT, "evidence", "investor_snapshot.json")

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


def _load_artifact(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    target = path or _ARTIFACT
    if not os.path.exists(target):
        return None
    try:
        with open(target, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("artifact") != "INVESTOR_SNAPSHOT":
        return None
    return data


def _investor_snapshot_payload() -> Dict[str, Any]:
    artifact = _load_artifact()
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
    }


@router.get("/api/v1/console/investor-snapshot")
async def investor_snapshot() -> Dict[str, Any]:
    return with_snapshot_metadata(_investor_snapshot_payload())


__all__ = ["router"]
