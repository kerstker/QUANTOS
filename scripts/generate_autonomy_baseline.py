#!/usr/bin/env python
"""Build the current Phase 0/1 autonomy baseline without changing runtime state.

The baseline is deliberately evidence-first: it hashes the inputs it reads,
records stale-document conflicts instead of choosing the optimistic value, and
keeps the L2 safety flags and order path fail-closed.  It does not fetch data,
create paper fills, or grant deployment authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "evidence" / "autonomy_baseline.json"
DEFAULT_GAP_REPORT = ROOT / "docs" / "reports" / "autonomy_gap_analysis.md"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS

REQUIRED_INPUTS = (
    "AGENTS.md",
    "QUANTOS_MASTER_BLUEPRINT.md",
    "docs/PROJECT_STATE.md",
    "docs/ARCHITECTURE.md",
    "docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md",
    "evidence/top_tier_assessment.json",
    "evidence/data_quality_admission.json",
    "evidence/alpha_paper_cycle.json",
    "evidence/three_alpha_paper_flow.json",
    "data/cache/paper_health_10k.json",
    "data/cache/binance_usdt_continuous_paper.json",
    "evidence/raw_data_lineage_backfill.json",
)


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(root: Path, relative_path: str) -> dict[str, Any]:
    path = root / relative_path
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _artifact(root: Path, relative_path: str) -> dict[str, Any]:
    path = root / relative_path
    digest = _sha256(path)
    result: dict[str, Any] = {
        "path": relative_path.replace("\\", "/"),
        "exists": digest is not None,
        "size_bytes": path.stat().st_size if digest is not None else None,
        "sha256": digest,
    }
    if digest is None:
        return result
    payload = _load_json(root, relative_path)
    for key in (
        "verdict",
        "readiness",
        "paper_ready",
        "paper_mark_ready",
        "paper_preview_ready",
        "live_allowed",
        "order_submission_allowed",
        "research_risk_bundle_complete",
    ):
        if key in payload:
            result[key] = payload[key]
    if isinstance(payload.get("summary"), dict):
        result["summary"] = payload["summary"]
    return result


def _git_commit(root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else "UNKNOWN"


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safety_flags() -> dict[str, bool]:
    values = RESEARCH_SAFE_DEFAULTS.to_dict()
    return {
        "LIVE_TRADING_ENABLED": bool(values["live_trading_enabled"]),
        "REAL_BROKER_ENABLED": bool(values["real_broker_enabled"]),
        "ALLOW_ORDER_SUBMISSION": bool(values["allow_order_submission"]),
        "ALLOW_AI_DIRECT_TRADING": bool(values["allow_ai_direct_trading"]),
        "REQUIRE_HUMAN_APPROVAL": bool(values["require_human_approval"]),
    }


def _phase_status(
    *,
    safety_flags: dict[str, bool],
    inputs: dict[str, dict[str, Any]],
    admission: dict[str, Any],
    lineage: dict[str, Any],
    validation: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    safety_ok = (
        safety_flags["LIVE_TRADING_ENABLED"] is False
        and safety_flags["REAL_BROKER_ENABLED"] is False
        and safety_flags["ALLOW_ORDER_SUBMISSION"] is False
        and safety_flags["ALLOW_AI_DIRECT_TRADING"] is False
        and safety_flags["REQUIRE_HUMAN_APPROVAL"] is True
    )
    missing_inputs = sorted(
        path for path, artifact in inputs.items() if artifact["exists"] is not True
    )
    full_validation_pass = (
        validation.get("selection_scope") == "ALL"
        and validation.get("overall_status") == "PASS"
    )
    phase0_reasons = []
    if not safety_ok:
        phase0_reasons.append("SAFETY_FLAGS_NOT_RESEARCH_SAFE")
    if missing_inputs:
        phase0_reasons.append(f"REQUIRED_INPUTS_MISSING:{missing_inputs}")
    if not full_validation_pass:
        phase0_reasons.append("FULL_VALIDATION_REPORT_NOT_PASS")

    admission_pass = (
        admission.get("verdict") == "PASS"
        and admission.get("quality_verdict") == "PASS"
        and admission.get("lineage_verdict") == "PASS"
    )
    lineage_pass = lineage.get("verdict") == "PASS"
    phase1_reasons = []
    if not admission_pass:
        phase1_reasons.append("DATA_QUALITY_ADMISSION_NOT_PASS")
    if not lineage_pass:
        phase1_reasons.append("RAW_LINEAGE_BACKFILL_NOT_PASS")

    return {
        "phase_0": {
            "status": "PASS" if not phase0_reasons else "PARTIAL",
            "reasons": phase0_reasons,
            "acceptance": "safety freeze, evidence inventory, and full gate report",
        },
        "phase_1": {
            "status": "PASS" if not phase1_reasons else "BLOCKED",
            "reasons": phase1_reasons,
            "acceptance": "rebuildable quality and raw-lineage admission",
        },
    }


def build_baseline(*, root: Path = ROOT, now_ms: int | None = None) -> dict[str, Any]:
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    root = root.resolve()
    inputs = {
        relative_path: _artifact(root, relative_path)
        for relative_path in REQUIRED_INPUTS
    }
    admission = _load_json(root, "evidence/data_quality_admission.json")
    lineage = _load_json(root, "evidence/raw_data_lineage_backfill.json")
    top_tier = _load_json(root, "evidence/top_tier_assessment.json")
    paper_health = _load_json(root, "data/cache/paper_health_10k.json")
    validation = _load_json(root, "evidence/validation_report.json")
    project_state_path = root / "docs" / "PROJECT_STATE.md"
    try:
        project_state = project_state_path.read_text(encoding="utf-8")
    except OSError:
        project_state = ""

    safety_flags = _safety_flags()
    conflicts = []
    if (
        admission.get("verdict") == "PASS"
        and ("quality=BLOCK" in project_state or "lineage=BLOCK" in project_state)
    ):
        conflicts.append(
            {
                "id": "EVIDENCE_CONFLICT.PROJECT_STATE_VS_CURRENT_ADMISSION",
                "status": "RECORDED",
                "authoritative_paths": [
                    "evidence/data_quality_admission.json",
                    "evidence/raw_data_lineage_backfill.json",
                ],
                "historical_path": "docs/PROJECT_STATE.md",
                "authoritative_verdict": {
                    "admission": admission.get("verdict"),
                    "quality": admission.get("quality_verdict"),
                    "lineage": admission.get("lineage_verdict"),
                },
                "historical_marker": "quality=BLOCK or lineage=BLOCK",
                "resolution": (
                    "Use current executable evidence as authority; retain the old BLOCK "
                    "as historical context and update the live-document annotation."
                ),
                "fail_closed": True,
            }
        )

    input_hash = _canonical_hash(inputs)
    generator_path = root / "scripts" / "generate_autonomy_baseline.py"
    paper_verdict = (
        paper_health.get("readiness_class")
        or paper_health.get("readiness")
        or "MISSING"
    )
    baseline = {
        "schema_version": 1,
        "report_kind": "AUTONOMY_BASELINE",
        "generated_at_ms": int(now_ms),
        "commit": _git_commit(root),
        "readiness": "L2_DRY_RUN_ONLY",
        "evidence_priority": [
            "current_executable_source",
            "current_tests_and_governance",
            "current_hashed_json_sqlite_event_artifacts",
            "current_live_documents",
            "historical_reports",
        ],
        "safety_flags": safety_flags,
        "safety_verdict": "PASS"
        if safety_flags["LIVE_TRADING_ENABLED"] is False
        and safety_flags["REAL_BROKER_ENABLED"] is False
        and safety_flags["ALLOW_ORDER_SUBMISSION"] is False
        and safety_flags["ALLOW_AI_DIRECT_TRADING"] is False
        and safety_flags["REQUIRE_HUMAN_APPROVAL"] is True
        else "BLOCK",
        "order_path": {
            "live_allowed": False,
            "order_submission_allowed": False,
            "live_trading_armed": bool(RESEARCH_SAFE_DEFAULTS.live_trading_armed),
            "real_broker": "STRUCTURAL_REJECTING_SEAM_ONLY",
            "credentials_loaded": False,
        },
        "inputs": inputs,
        "input_hash": input_hash,
        "config_hash": _sha256(root / "quantos" / "contracts" / "config.py"),
        "generator_sha256": _sha256(generator_path),
        "current_evidence": {
            "data_admission": {
                "verdict": admission.get("verdict", "MISSING"),
                "quality_verdict": admission.get("quality_verdict", "MISSING"),
                "lineage_verdict": admission.get("lineage_verdict", "MISSING"),
                "block_reasons": admission.get("block_reasons", []),
            },
            "raw_lineage_backfill": {
                "verdict": lineage.get("verdict", "MISSING"),
                "block_reasons": lineage.get("block_reasons", []),
            },
            "paper_health_10k": {
                "verdict": paper_verdict,
                "readiness": paper_health.get("readiness"),
                "blockers": paper_health.get("blockers", []),
                "paper_ready": (paper_health.get("verdict") or {}).get("paper_ready")
                if isinstance(paper_health.get("verdict"), dict)
                else paper_health.get("paper_ready"),
                "paper_mark_ready": (paper_health.get("verdict") or {}).get("paper_mark_ready")
                if isinstance(paper_health.get("verdict"), dict)
                else paper_health.get("paper_mark_ready"),
                "paper_preview_ready": (paper_health.get("verdict") or {}).get("paper_preview_ready")
                if isinstance(paper_health.get("verdict"), dict)
                else paper_health.get("paper_preview_ready"),
                "research_risk_bundle_complete": (
                    (paper_health.get("verdict") or {}).get("paper_research_risk_bundle_complete")
                    if isinstance(paper_health.get("verdict"), dict)
                    else paper_health.get("research_risk_bundle_complete")
                ),
            },
            "top_tier": {
                "verdict": top_tier.get("verdict", "MISSING"),
                "summary": top_tier.get("summary", {}),
            },
            "validation_report": {
                "selection_scope": validation.get("selection_scope", "MISSING"),
                "overall_status": validation.get("overall_status", "MISSING"),
                "summary": validation.get("summary", {}),
            },
        },
        "evidence_conflicts": conflicts,
    }
    baseline["phase_status"] = _phase_status(
        safety_flags=safety_flags,
        inputs=inputs,
        admission=admission,
        lineage=lineage,
        validation=validation,
    )
    baseline["remaining_blockers"] = [
        "PAPER_RESEARCH_RISK_BUNDLE_PENDING",
        "FORWARD_PAPER_EVIDENCE_0_OF_8_OR_CURRENT_ARTIFACT_REQUIRED",
        "TOP_TIER_RED_LINES_OPEN",
        "EXTERNAL_IDENTITY_DATA_EXECUTION_AND_ARCHIVAL_EVIDENCE",
    ]
    return baseline


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def render_gap_report(baseline: dict[str, Any]) -> str:
    admission = baseline["current_evidence"]["data_admission"]
    lineage = baseline["current_evidence"]["raw_lineage_backfill"]
    paper = baseline["current_evidence"]["paper_health_10k"]
    top_tier = baseline["current_evidence"]["top_tier"]
    validation = baseline["current_evidence"]["validation_report"]
    conflicts = baseline["evidence_conflicts"]
    lines = [
        "# QUANTOS 自治能力基线与差距分析",
        "",
        "> 本报告由 `scripts/generate_autonomy_baseline.py` 生成。它是当前快照，",
        "> 不覆盖历史证据，也不授予任何真实资金或订单权限。",
        "",
        f"- 生成时间（Unix ms）：`{baseline['generated_at_ms']}`",
        f"- commit：`{baseline['commit']}`",
        f"- 当前等级：`{baseline['readiness']}`",
        f"- 输入哈希：`{baseline['input_hash']}`",
        f"- 配置哈希：`{baseline['config_hash']}`",
        "",
        "## Phase 0/1 当前状态",
        "",
        "| 阶段 | 状态 | 依据/阻断 |\n|---|---|---|",
        f"| Phase 0 基线与安全冻结 | `{baseline['phase_status']['phase_0']['status']}` | "
        f"{'; '.join(baseline['phase_status']['phase_0']['reasons']) or '安全旗标与输入清单满足；完整门禁报告已纳入判定'} |",
        f"| Phase 1 数据血缘与统一门禁 | `{baseline['phase_status']['phase_1']['status']}` | "
        f"admission={admission['verdict']}, quality={admission['quality_verdict']}, "
        f"lineage={admission['lineage_verdict']}, backfill={lineage['verdict']} |",
        "",
        "## 当前机器证据",
        "",
        f"- Data Admission：`{admission['verdict']}`（quality=`{admission['quality_verdict']}`, lineage=`{admission['lineage_verdict']}`）。",
        f"- Raw lineage/backfill：`{lineage['verdict']}`。",
        f"- Paper 10k：`{paper['verdict']}`；paper_ready=`{paper['paper_ready']}`，"
        f"paper_mark_ready=`{paper['paper_mark_ready']}`，paper_preview_ready=`{paper['paper_preview_ready']}`。",
        f"- Top-tier：`{top_tier['verdict']}`；摘要=`{json.dumps(top_tier['summary'], ensure_ascii=False, sort_keys=True)}`。",
        f"- Full validation report：scope=`{validation['selection_scope']}`，status=`{validation['overall_status']}`。",
        "",
        "## 安全边界",
        "",
        "| 旗标/能力 | 当前值 |\n|---|---|",
        *[
            f"| `{name}` | `{str(value).lower()}` |"
            for name, value in baseline["safety_flags"].items()
        ],
        "| live_allowed | `false` |",
        "| order_submission_allowed | `false` |",
        "| real broker | `STRUCTURAL_REJECTING_SEAM_ONLY` |",
        "",
        "## 证据冲突",
        "",
    ]
    if conflicts:
        lines.extend(
            [
                "发现并记录以下冲突；按优先级使用当前可执行证据，未把冲突伪装成全绿：",
                "",
                *[
                    f"- `{conflict['id']}`：当前 admission 为 `PASS`，"
                    f"`docs/PROJECT_STATE.md` 仍含历史 `{conflict['historical_marker']}`；"
                    "该冲突已 fail-closed 记录，活文档已明确标注其为历史状态。"
                    for conflict in conflicts
                ],
            ]
        )
    else:
        lines.append("- 未发现当前输入集合中的文档/机器证据冲突。")
    lines.extend(
        [
            "",
            "## 尚未完成的真实缺口",
            "",
            "- 完整九引擎 Paper 风险输入包与至少 8 个有效前向周期仍未满足；不能以默认值、旧快照或合成数据替代。",
            "- 新研究入口已接入 TrialRecord 与 OOS access 哈希链；2533 条历史 bare trial 的缺失字段不可事后伪造。独立模型风险审查身份、外部 OIDC/mTLS、授权数据、跨场所执行和外部封存仍需真实证据。",
            "- 主网 broker、真实凭证和真实下单路径不属于当前 L2 开发范围，且必须继续保持结构性拒绝。",
            "",
            "## 下一项唯一最高优先级",
            "",
            "在当前 admission 已 PASS 的前提下，生成并绑定三条 Alpha 的完整九引擎 Paper 风险输入包；"
            "缺失项必须继续输出 `PAPER_BLOCKED`，并为每条 Alpha 保留拒单、对账和审计证据。",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(
    *,
    output_path: Path = DEFAULT_OUTPUT,
    gap_report_path: Path = DEFAULT_GAP_REPORT,
    now_ms: int | None = None,
) -> dict[str, Any]:
    baseline = build_baseline(now_ms=now_ms)
    _write_json(output_path, baseline)
    gap_report_path = gap_report_path.resolve()
    gap_report_path.parent.mkdir(parents=True, exist_ok=True)
    gap_report_path.write_text(render_gap_report(baseline), encoding="utf-8", newline="\n")
    return baseline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--gap-report", default=str(DEFAULT_GAP_REPORT))
    parser.add_argument("--now-ms", type=int)
    args = parser.parse_args(argv)
    baseline = write_outputs(
        output_path=Path(args.output),
        gap_report_path=Path(args.gap_report),
        now_ms=args.now_ms,
    )
    print(
        f"autonomy baseline: {baseline['phase_status']['phase_0']['status']} / "
        f"{baseline['phase_status']['phase_1']['status']}"
    )
    print(f"-> {args.output}")
    print(f"-> {args.gap_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
