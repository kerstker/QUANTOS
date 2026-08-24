"""Audit literature-derived Alpha methods against local data availability.

This tool performs no network fetch and no return evaluation.  It produces a
ranked experiment-supply queue whose rank reflects evidence/data readiness,
never profitability or promotion eligibility.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import json
import os
from pathlib import Path
import time
from typing import Mapping

if __package__ in {None, ""}:  # pragma: no cover - CLI path compatibility
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantos.contracts.hashing import stable_hash
from services.data.channel_registry import channel_specs
from services.research.alpha_methodology import (
    ALPHA_METHODS,
    DISCOVERY_CONTROLS,
    SOURCES,
    audit_methodology,
)
from tools.cache_io import atomic_write_json


DEFAULT_OUTPUT = os.path.join("data", "cache", "alpha_methodology_audit.json")
DEFAULT_REPORT = os.path.join(
    "docs", "reports", "alpha-methodology-audit-2026-08-25.md"
)


def local_channel_availability(root: str = ".") -> dict[str, str]:
    """Map registry declarations to honest local artifact availability."""
    result: dict[str, str] = {}
    for spec in channel_specs():
        path = spec.artifact_path
        if not os.path.isabs(path):
            path = os.path.join(root, path)
        present = os.path.isfile(path) and os.path.getsize(path) > 0
        if present:
            result[spec.channel_id] = spec.availability
        else:
            result[spec.channel_id] = "BLOCKED_MISSING"
    return result


def _registry_payload() -> dict[str, object]:
    return {
        "sources": [asdict(item) for item in SOURCES],
        "alpha_methods": [asdict(item) for item in ALPHA_METHODS],
        "discovery_controls": [asdict(item) for item in DISCOVERY_CONTROLS],
    }


def build_methodology_audit(
    *,
    availability: Mapping[str, str] | None = None,
    root: str = ".",
    generated_at_ms: int | None = None,
) -> dict[str, object]:
    if generated_at_ms is None:
        generated_at_ms = int(time.time() * 1000)
    if type(generated_at_ms) is not int or generated_at_ms < 0:
        raise ValueError("generated_at_ms must be a non-negative integer")
    channel_availability = dict(
        local_channel_availability(root) if availability is None else availability
    )
    ranked = audit_methodology(channel_availability)
    registry = _registry_payload()
    rows = []
    method_by_id = {item.method_id: item for item in ALPHA_METHODS}
    for row in ranked:
        method = method_by_id[row.method_id]
        item = asdict(row)
        item["readiness"] = row.readiness.value
        item["name"] = method.name
        item["portability"] = method.portability
        item["required_channel_ids"] = list(method.required_channel_ids)
        item["testable_claim"] = method.testable_claim
        item["falsification_tests"] = list(method.falsification_tests)
        item["system_modules"] = list(method.system_modules)
        rows.append(item)
    readiness_counts = Counter(item["readiness"] for item in rows)
    return {
        "schema_version": 1,
        "generated_at_ms": generated_at_ms,
        "purpose": "ALPHA_RESEARCH_SUPPLY_NOT_PROMOTION",
        "methodology_hash": stable_hash(registry),
        "source_count": len(SOURCES),
        "method_count": len(ALPHA_METHODS),
        "discovery_control_count": len(DISCOVERY_CONTROLS),
        "alpha_eligible_count": 0,
        "channel_availability": dict(sorted(channel_availability.items())),
        "summary": {"readiness_counts": dict(sorted(readiness_counts.items()))},
        "ranked_methods": rows,
        "registry": registry,
        "honesty": (
            "priority ranks evidence and data readiness, not expected or realized profit",
            "historical diagnostic readiness is not formal OOS, paper, Testnet, or live eligibility",
            "practitioner translations remain explicitly non-academic until independently evidenced",
            "every evaluated configuration must enter the global trial ledger before outcome access",
        ),
    }


def _markdown(report: Mapping[str, object]) -> str:
    lines = [
        "# Alpha 方法论与实验供应审计",
        "",
        f"方法论哈希：`{report['methodology_hash']}`",
        "",
        "本报告按来源证据和本地数据成熟度排序，不按回测收益排序；所有正式 Alpha 准入仍为 0。",
        "",
        "| Rank | 方法 | 证据等级 | 数据状态 | 系统处置 | 缺失渠道 |",
        "|---:|---|---|---|---|---|",
    ]
    for item in report["ranked_methods"]:  # type: ignore[index]
        missing = "、".join(item["missing_channels"]) or "-"
        tiers = "、".join(item["evidence_tiers"])
        lines.append(
            f"| {item['priority_rank']} | `{item['method_id']}` {item['name']} | "
            f"{tiers} | {item['readiness']} | {item['system_disposition']} | {missing} |"
        )
    lines.extend(
        [
            "",
            "## 研究纪律",
            "",
            *[f"- {item}" for item in report["honesty"]],  # type: ignore[index]
            "",
        ]
    )
    return "\n".join(lines)


def write_methodology_audit(
    *,
    root: str = ".",
    output_path: str = DEFAULT_OUTPUT,
    report_path: str = DEFAULT_REPORT,
    generated_at_ms: int | None = None,
) -> dict[str, object]:
    report = build_methodology_audit(root=root, generated_at_ms=generated_at_ms)
    atomic_write_json(output_path, report, indent=2)
    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    Path(report_path).write_text(_markdown(report), encoding="utf-8", newline="\n")
    return report


def main() -> None:
    report = write_methodology_audit()
    print(json.dumps({
        "methodology_hash": report["methodology_hash"],
        "summary": report["summary"],
        "alpha_eligible_count": report["alpha_eligible_count"],
    }, ensure_ascii=False, sort_keys=True))
    print(f"-> {DEFAULT_OUTPUT}")
    print(f"-> {DEFAULT_REPORT}")


if __name__ == "__main__":
    main()

