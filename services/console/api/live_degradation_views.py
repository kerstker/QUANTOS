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

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter

from services.console.api.common import with_snapshot_metadata


router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ARTIFACT = os.path.join(_REPO_ROOT, "evidence", "live_degradation.json")

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


def _load_artifact(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    target = path or _ARTIFACT
    if not os.path.exists(target):
        return None
    try:
        with open(target, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("artifact") != "LIVE_DEGRADATION":
        return None
    return data


def _live_degradation_payload() -> Dict[str, Any]:
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
            "summary": None,
            "reports": [],
            "honesty_note": _ABSENT_HONESTY_NOTE,
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
    }


@router.get("/api/v1/console/live-degradation")
async def live_degradation() -> Dict[str, Any]:
    return with_snapshot_metadata(_live_degradation_payload())


__all__ = ["router"]
