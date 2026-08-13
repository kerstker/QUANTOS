"""DurableAuditLog — 落盘的 append-only 哈希链审计存储(Phase 13 / 路线图 Rank #20)。

`GlobalAuditLog`(audit_log.py)是**内存**链,进程退出即失,且从未接进任何治理决策路径——
kill-switch 搬闸/复位、部署门放行/拒绝这类**治理决策事后无痕**。本存储把同一 `AuditEvent`
哈希链**落进 sqlite**,镜像 `run_registry.store` 的持久化模式(`CREATE TABLE IF NOT EXISTS` +
`threading.Lock` + 父目录自建;时间由调用方注入,店内无墙钟),但语义是 **append-only**:

- 每条存 `entry_hash`;`chain_valid()` 读时**重算** entry_hash 与存值比对(抓字段篡改),并核对
  `prev_hash→entry_hash` 链(抓重排);`event_id` 唯一(防重放分叉)。
- `seal()` 出带一个带外承诺(长度 + 链头),`verify_against()` 核对它——抓截断/整体替换/全擦除
  (裸哈希链自身抓不到这三种,review P0)。

与 `GlobalAuditLog` 共享 `AuditEvent` 契约与链语义,故二者都是合法的 duck-typed audit sink:
任何提供 `append(event_id, actor, action, timestamp, target, metadata)` 的对象都能被 KillSwitch /
evaluate_deployment 的可选 `audit_sink=` 接收。纯 stdlib(sqlite3/json/threading/os)——services/
无网络 import 由 `tests/governance/test_pit_read_boundary` 同套的 `test_services_tree_has_no_network_imports` 机器强制。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Optional

from quantos.contracts.governance import AuditEvent
from .audit_log import AuditCommitment


class DurableAuditLog:
    """Thread-safe append-only hash-chained audit store. Time is caller-injected (no wall-clock)."""

    def __init__(self, db_path: str, create: bool = True, *, read_only: bool = False):
        if read_only and create:
            raise ValueError("read_only audit stores cannot create an authority")
        if read_only and db_path == ":memory:":
            raise ValueError("read_only audit stores require a file authority")
        # The db often lives under a gitignored dir (data/cache) absent on fresh checkout / CI;
        # sqlite cannot create the file if its parent is missing (mirrors run_registry.store).
        if create and db_path != ":memory:":
            parent = os.path.dirname(db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        connection = None
        try:
            if read_only:
                uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
                connection = sqlite3.connect(
                    uri, uri=True, check_same_thread=False
                )
                connection.execute("PRAGMA query_only=ON")
            else:
                connection = sqlite3.connect(db_path, check_same_thread=False)
            self._c = connection
            if create:
                with self._lock:
                    self._c.execute(
                        "CREATE TABLE IF NOT EXISTS audit_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                        " event_id TEXT UNIQUE, actor TEXT, action TEXT, target TEXT, timestamp INTEGER,"
                        " metadata_json TEXT, prev_hash TEXT, entry_hash TEXT)")
                    self._c.commit()
        except Exception:
            if connection is not None:
                connection.close()
            raise

    def _rows(self):
        return self._c.execute(
            "SELECT event_id, actor, action, target, timestamp, metadata_json, prev_hash, entry_hash"
            " FROM audit_events ORDER BY seq").fetchall()

    @staticmethod
    def _event(row) -> AuditEvent:
        return AuditEvent(event_id=row[0], actor=row[1], action=row[2], target=row[3],
                          timestamp=row[4], metadata=json.loads(row[5]), prev_hash=row[6])

    def append(self, *, event_id: str, actor: str, action: str, timestamp: int,
               target: str = "", metadata: Optional[dict] = None) -> AuditEvent:
        with self._lock:
            if self._c.execute("SELECT 1 FROM audit_events WHERE event_id=?", (event_id,)).fetchone():
                raise ValueError(f"duplicate audit event_id (replay): {event_id}")
            last = self._c.execute(
                "SELECT entry_hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
            prev = last[0] if last else ""
            ev = AuditEvent(event_id=event_id, actor=actor, action=action, target=target,
                            timestamp=timestamp, metadata=metadata or {}, prev_hash=prev)
            self._c.execute(
                "INSERT INTO audit_events (event_id, actor, action, target, timestamp, metadata_json,"
                " prev_hash, entry_hash) VALUES (?,?,?,?,?,?,?,?)",
                (event_id, actor, action, target, timestamp,
                 json.dumps(ev.metadata, sort_keys=True), prev, ev.entry_hash))
            self._c.commit()
        return ev

    @property
    def events(self) -> tuple:
        # property to match GlobalAuditLog's read interface, so either store is an interchangeable sink
        with self._lock:
            return tuple(self._event(r) for r in self._rows())

    def close(self) -> None:
        self._c.close()

    def chain_valid(self) -> bool:
        with self._lock:
            prev = ""
            for row in self._rows():
                if row[6] != prev:                      # stored prev_hash must match the walk
                    return False
                ev = self._event(row)                   # recompute entry_hash from stored fields
                if ev.entry_hash != row[7]:             # field tamper: recomputed != stored
                    return False
                prev = ev.entry_hash
            return True

    @property
    def head_hash(self) -> str:
        with self._lock:
            last = self._c.execute(
                "SELECT entry_hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
            return last[0] if last else ""

    def _count(self) -> int:
        with self._lock:
            return self._c.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

    def seal(self) -> AuditCommitment:
        """Out-of-band commitment (length + head hash). Hold externally; verify_against() later
        to catch truncation / deletion / wholesale replacement / full erasure."""
        return AuditCommitment(length=self._count(), head_hash=self.head_hash)

    def verify_against(self, commitment: AuditCommitment) -> bool:
        return (self.chain_valid()
                and self._count() == commitment.length
                and self.head_hash == commitment.head_hash)
