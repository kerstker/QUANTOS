"""Stdlib-sqlite3 persistence for the AlphaAssetRegistry (Alpha Factory upgrade / Phase 2 P3-4).

Mirrors services/research/experiment_registry/store.py: owns the schema and ALL SQL, no domain logic.
Append-only / immutable: an (alpha_id, version) is write-once (same content = idempotent no-op, different
content = ImmutableAlphaAssetError); fail-closed: reading an uninitialized/corrupt store raises
AlphaAssetStoreError rather than reading as empty. load_registry RECOMPUTES each asset's content hash and
rejects tampered rows, then rebuilds through registry.restore() so the containment guard (non-human cannot
back-door APPROVED) is re-applied on the persisted path too. The registry stays pure (no sqlite import)."""
from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from quantos.contracts.alpha_asset import AlphaAsset
from services.alpha.registry import AlphaAssetRegistry


class AlphaAssetStoreError(Exception):
    """The store is missing, corrupt, uninitialized, or holds a tampered asset — fail closed."""


class ImmutableAlphaAssetError(Exception):
    """Attempt to overwrite a recorded (alpha_id, version) with different content."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS alpha_assets (
    alpha_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    asset_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    add_ts INTEGER NOT NULL,
    PRIMARY KEY (alpha_id, version)
);
CREATE TABLE IF NOT EXISTS alpha_asset_audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    alpha_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    event_json TEXT NOT NULL,
    UNIQUE (alpha_id, version)
);
"""

_TABLES = ("alpha_assets", "alpha_asset_audit")


class SqliteAlphaAssetStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _require_schema(self) -> None:
        try:
            rows = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise AlphaAssetStoreError(f"store unreadable: {exc}") from exc
        missing = [t for t in _TABLES if t not in {r["name"] for r in rows}]
        if missing:
            raise AlphaAssetStoreError(f"store uninitialized; missing tables: {missing}")

    def insert_asset(self, alpha_id: str, version: int, asset_json: str, content_hash: str,
                     add_ts: int) -> bool:
        """Write-once. Same content_hash on the same (alpha_id, version) = idempotent no-op (False);
        different content = ImmutableAlphaAssetError. New = insert (True)."""
        self._require_schema()
        existing = self._conn.execute(
            "SELECT content_hash FROM alpha_assets WHERE alpha_id=? AND version=?",
            (alpha_id, int(version))).fetchone()
        if existing is not None:
            if existing["content_hash"] == content_hash:
                return False
            raise ImmutableAlphaAssetError(
                f"alpha {alpha_id} v{version}: content differs from the recorded immutable snapshot")
        self._conn.execute(
            "INSERT INTO alpha_assets(alpha_id, version, asset_json, content_hash, add_ts) VALUES (?,?,?,?,?)",
            (alpha_id, int(version), asset_json, content_hash, int(add_ts)))
        self._conn.commit()
        return True

    def all_assets(self) -> list:
        self._require_schema()
        return list(self._conn.execute(
            "SELECT alpha_id, version, asset_json, content_hash, add_ts FROM alpha_assets "
            "ORDER BY alpha_id, version"))

    def insert_audit(self, alpha_id: str, version: int, event_json: str) -> None:
        self._require_schema()
        self._conn.execute(
            "INSERT OR IGNORE INTO alpha_asset_audit(alpha_id, version, event_json) VALUES (?,?,?)",
            (alpha_id, int(version), event_json))
        self._conn.commit()

    def all_audit(self) -> list:
        self._require_schema()
        return list(self._conn.execute(
            "SELECT alpha_id, version, event_json FROM alpha_asset_audit ORDER BY seq"))


# --- bridges (SQL stays in the store; registry stays pure) ---

def save_registry(registry: AlphaAssetRegistry, store: SqliteAlphaAssetStore) -> None:
    """把内存 registry 幂等累积落盘(镜像 ExperimentRegistry.save:永不删/覆盖,跨会话单调)。每条 add 审计
    (=每 version 一行)对应存一个 asset 快照 + 该审计行。同 (alpha_id,version) 同内容=幂等 no-op。"""
    for entry in registry.audit_log():
        aid, ver = entry["alpha_id"], entry["version"]
        asset = registry.get(aid, ver)
        if asset is None:
            continue
        store.insert_asset(aid, ver, asset.model_dump_json(), asset.asset_hash, entry.get("ts", 0))
        store.insert_audit(aid, ver, json.dumps(entry, sort_keys=True))


def load_registry(store: SqliteAlphaAssetStore) -> AlphaAssetRegistry:
    """从盘重建 registry。**加载即校验完整性**:重算每个 asset 的 content hash,与存的不符=篡改即 raise
    AlphaAssetStoreError;再经 registry.restore() 过 containment 守卫(伪造 APPROVED / 单调违反即 raise)。"""
    assets = []
    for row in store.all_assets():
        asset = AlphaAsset.model_validate_json(row["asset_json"])
        if asset.asset_hash != row["content_hash"]:
            raise AlphaAssetStoreError(
                f"alpha {row['alpha_id']} v{row['version']}: asset_json tampered (content_hash mismatch)")
        assets.append(asset)
    audit = [json.loads(row["event_json"]) for row in store.all_audit()]
    reg = AlphaAssetRegistry()
    reg.restore(assets, audit)
    return reg
