"""生成数据渠道覆盖清单（离线读取本地资产，不发网络请求）。

清单把“代码中有获取器”和“本地已有可审计数据”分开：artifact 缺失时明确
``BLOCKED_MISSING``；只有诊断历史或近期快照时也保留其原始 availability，
避免控制台把数据渠道数量误报成可部署 Alpha 数量。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

from services.data.channel_registry import channel_specs
from tools.cache_io import atomic_write_json

_OUT = os.path.join("data", "cache", "data_channel_inventory.json")
_REPORT = os.path.join("docs", "reports", "data-channel-inventory-2026-07-25.md")


def _resolve(root: str, path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(root, path)


def _json_last_observed(payload) -> int | None:
    if not isinstance(payload, dict):
        return None
    candidates = []
    for key in ("fetched_at_ms", "last_observed_at_ms", "generated_at_ms"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            candidates.append(int(value))
    meta = payload.get("_meta")
    if isinstance(meta, dict):
        for value in meta.values():
            if isinstance(value, dict):
                for key in ("fetched_at_ms", "observed_at_ms", "through"):
                    raw = value.get(key)
                    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                        candidates.append(int(raw))
    for row in payload.get("snapshots", []) if isinstance(payload.get("snapshots"), list) else []:
        if isinstance(row, dict):
            raw = row.get("observed_at_ms")
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                candidates.append(int(raw))
    data = payload.get("data")
    if isinstance(data, dict):
        for records in data.values():
            if isinstance(records, dict):
                for raw in records:
                    try:
                        candidates.append(int(raw))
                    except (TypeError, ValueError):
                        pass
    return max(candidates) if candidates else None


def _sqlite_last_observed(path: str) -> int | None:
    if not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(path)
        try:
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            candidates = []
            if "bars" in tables:
                row = conn.execute("SELECT MAX(event_time) FROM bars").fetchone()
                if row and row[0] is not None:
                    candidates.append(int(row[0]))
            # Deribit/fundamental stores use the same bitemporal column names but
            # may not expose a bars table. Only inspect known column names.
            for table in tables:
                cols = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
                for col in ("knowledge_time", "event_time", "event_time_ms"):
                    if col in cols:
                        row = conn.execute(
                            f'SELECT MAX("{col}") FROM "{table}"').fetchone()
                        if row and row[0] is not None:
                            try:
                                candidates.append(int(row[0]))
                            except (TypeError, ValueError):
                                pass
            return max(candidates) if candidates else None
        finally:
            conn.close()
    except (OSError, sqlite3.DatabaseError):
        return None


def inspect_artifact(root: str, spec) -> dict:
    path = _resolve(root, spec.artifact_path)
    exists = os.path.isfile(path)
    size = os.path.getsize(path) if exists else 0
    last_observed = None
    parse_error = None
    if exists and size:
        try:
            if path.lower().endswith(".json"):
                with open(path, encoding="utf-8") as handle:
                    last_observed = _json_last_observed(json.load(handle))
            elif path.lower().endswith(".db"):
                last_observed = _sqlite_last_observed(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parse_error = type(exc).__name__
    if exists and size > 0:
        status = spec.availability
    elif spec.availability == "PUBLIC_STREAM_REQUIRED":
        status = spec.availability
    else:
        status = "BLOCKED_MISSING"
    if parse_error:
        status = "BLOCKED_INVALID"
    return {
        "path": spec.artifact_path,
        "exists": exists,
        "size_bytes": size,
        "last_observed_ms": last_observed,
        "status": status,
        "parse_error": parse_error,
    }


def build_inventory(root: str = ".", *, now_ms: int | None = None) -> dict:
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    channels = []
    for spec in channel_specs():
        row = spec.as_dict()
        row["artifact"] = inspect_artifact(root, spec)
        channels.append(row)
    by_family = {}
    for row in channels:
        for family in row["alpha_families"]:
            by_family.setdefault(family, []).append({
                "channel_id": row["channel_id"],
                "status": row["artifact"]["status"],
                "availability": row["availability"],
            })
    counts = {}
    for row in channels:
        status = row["artifact"]["status"]
        counts[status] = counts.get(status, 0) + 1
    return {
        "schema_version": 1,
        "generated_at_ms": int(now_ms),
        "purpose": "DATA_CHANNEL_COVERAGE_NOT_ALPHA_PROMOTION",
        "channels": channels,
        "alpha_family_channels": by_family,
        "summary": {"channel_count": len(channels), "status_counts": counts},
        "honesty": [
            "代码获取器存在不等于本地有连续历史数据",
            "REAL_RECENT/REAL_SNAPSHOT/REAL_DIAGNOSTIC 不可直接当作 paper-ready PIT 截面",
            "缺失输入不会用零值、上一值或跨 venue 价格静默补齐",
        ],
    }


def write_inventory(*, root: str = ".", output_path: str = _OUT,
                    report_path: str = _REPORT, now_ms: int | None = None) -> dict:
    inventory = build_inventory(root, now_ms=now_ms)
    atomic_write_json(output_path, inventory, indent=2)
    lines = [
        "# QUANTOS 数据渠道覆盖清单",
        "",
        f"生成时间（epoch ms）：`{inventory['generated_at_ms']}`",
        "",
        "这是数据覆盖审计，不是盈利或实盘授权。缺失、近期窗口和诊断历史均保持原状态。",
        "",
        "| 渠道 | 提供方/场地 | 频率 | 本地资产 | 状态 | 主要字段 |",
        "|---|---|---|---|---|---|",
    ]
    for row in inventory["channels"]:
        artifact = row["artifact"]
        fields = "、".join(row["fields"])
        lines.append(
            f"| `{row['channel_id']}` | {row['provider']} / {row['venue']} | "
            f"{row['cadence']} | `{row['artifact_path']}` | "
            f"{artifact['status']} | {fields} |"
        )
    lines.extend(["", "## 解释", ""])
    lines.extend(f"- {item}" for item in inventory["honesty"])
    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    with open(report_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return inventory


def main() -> None:
    inventory = write_inventory()
    print("data channels:", inventory["summary"])
    print(f"-> {_OUT}")
    print(f"-> {_REPORT}")


if __name__ == "__main__":
    main()
