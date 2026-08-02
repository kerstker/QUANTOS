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

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter

from services.console.api.common import with_snapshot_metadata


router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GO_LIVE_ARTIFACT = os.path.join(_REPO_ROOT, "evidence", "go_live_readiness.json")

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


def _load_artifact(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    target = path or _GO_LIVE_ARTIFACT
    if not os.path.exists(target):
        return None
    try:
        with open(target, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("artifact") != "GO_LIVE_READINESS":
        return None
    return data


def _go_live_readiness_payload() -> Dict[str, Any]:
    artifact = _load_artifact()
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
    }


@router.get("/api/v1/console/go-live-readiness")
async def go_live_readiness() -> Dict[str, Any]:
    return with_snapshot_metadata(_go_live_readiness_payload())


__all__ = ["router"]
