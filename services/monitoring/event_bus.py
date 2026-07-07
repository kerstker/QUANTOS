"""DurableEventBus — 落盘的 append-only 事件总线(Phase 6 item 1,P2-5)。

与 `DurableAuditLog`(audit_store.py,哈希链、防篡改、`AuditEvent` 契约)**互补**:本总线存
`quantos/contracts/events.py::Event` 的规范事件包 + correlation/causation ID,支持按**因果组回放**
(replay)+ 进程内**同步订阅**。定位不同——审计链答"这条治理决策没被篡改",事件总线答"这一串
事件的因果链是什么、能否重放"。

**关键设计(纯加法)**:correlation_id/causation_id 存**总线列**(publish 参数),**不改** frozen
`Event` 契约——不动 `reproducibility_hash`、不动生成的 schema。镜像 `run_registry`/`DurableAuditLog`
的持久化模式(`CREATE TABLE IF NOT EXISTS` + `threading.Lock` + 父目录自建;时间由 event 自带,
店内无墙钟)。fail-closed:非 `Event` 拒绝(TypeError),重复 event_id 抛错(防重放分叉)。纯 stdlib
(sqlite3/json/threading/os),`services/` 无网络 import 由治理测试机器强制。

诚实边界:本总线只捕获调用方 `publish` 时传入的 ID;把 correlation/causation **穿线进真实 emit 点**
(kill-switch/pretrade/promotion/dry-run)让因果图真的长出来 = 需改多处既有行为 = 大重构,按"不做大
重构"红线留后续(见 docs Phase 6 defer 记录)。总线本体独立可用/可测,不依赖那步。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Callable, List, Optional, Tuple

from quantos.contracts.events import Event


class DurableEventBus:
    """Thread-safe append-only 事件总线。时间由 event 自带(无墙钟),故同输入 -> 同落库(可复现)。"""

    def __init__(self, db_path: str, create: bool = True):
        # db 常在 gitignored 目录(data/cache),新检出/CI 缺父目录;sqlite 不会自建父目录。
        if create and db_path != ":memory:":
            parent = os.path.dirname(db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._c = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._subscribers: List[Callable[[Event], None]] = []
        if create:
            with self._lock:
                self._c.execute(
                    "CREATE TABLE IF NOT EXISTS event_log (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " event_id TEXT UNIQUE, event_type TEXT, event_version TEXT, timestamp INTEGER,"
                    " source TEXT, domain TEXT, criticality TEXT, correlation_id TEXT,"
                    " causation_id TEXT, payload_json TEXT, repro_hash TEXT)")
                self._c.commit()

    def publish(self, event: Event, *, correlation_id: str = "", causation_id: str = "") -> Event:
        """落库一条事件(先持久化,再同步通知订阅者)。非 Event -> TypeError;重复 event_id -> ValueError。"""
        if not isinstance(event, Event):
            raise TypeError("event_bus.publish requires a quantos.contracts.events.Event")
        with self._lock:
            if self._c.execute("SELECT 1 FROM event_log WHERE event_id=?",
                               (event.event_id,)).fetchone():
                raise ValueError(f"duplicate event_id (replay): {event.event_id}")
            self._c.execute(
                "INSERT INTO event_log (event_id, event_type, event_version, timestamp, source, domain,"
                " criticality, correlation_id, causation_id, payload_json, repro_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (event.event_id, event.event_type, event.event_version, event.timestamp, event.source,
                 event.domain, event.criticality, correlation_id, causation_id,
                 json.dumps(event.payload, sort_keys=True), event.reproducibility_hash))
            self._c.commit()
            subs = tuple(self._subscribers)
        # persistence-first:commit 之后、在锁外通知(handler 可能回读,threading.Lock 不可重入)
        for handler in subs:
            handler(event)
        return event

    def _rows(self, *, correlation_id: Optional[str] = None, since_seq: Optional[int] = None):
        clauses, params = [], []
        if correlation_id is not None:
            clauses.append("correlation_id=?")
            params.append(correlation_id)
        if since_seq is not None:
            clauses.append("seq > ?")
            params.append(since_seq)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return self._c.execute(
            "SELECT seq, event_id, event_type, event_version, timestamp, source, domain, criticality,"
            " correlation_id, causation_id, payload_json FROM event_log" + where + " ORDER BY seq",
            params).fetchall()

    @staticmethod
    def _event(row) -> Event:
        return Event(event_id=row[1], event_type=row[2], event_version=row[3], timestamp=row[4],
                     source=row[5], domain=row[6], criticality=row[7], payload=json.loads(row[10]))

    def replay(self, *, correlation_id: Optional[str] = None,
               since_seq: Optional[int] = None) -> Tuple[Event, ...]:
        """按 seq 顺序回放事件(可选按因果组 / since_seq 过滤)。只读、确定。"""
        with self._lock:
            return tuple(self._event(r)
                         for r in self._rows(correlation_id=correlation_id, since_seq=since_seq))

    def records(self, *, correlation_id: Optional[str] = None,
                since_seq: Optional[int] = None) -> Tuple[dict, ...]:
        """同 replay 但带总线元数据 {seq, event, correlation_id, causation_id}——因果图需要这些 ID。"""
        with self._lock:
            return tuple({"seq": r[0], "event": self._event(r),
                          "correlation_id": r[8], "causation_id": r[9]}
                         for r in self._rows(correlation_id=correlation_id, since_seq=since_seq))

    def subscribe(self, handler: Callable[[Event], None]) -> None:
        """注册进程内同步订阅者(publish 落库后按注册顺序调用)。纯:无线程、无网络。"""
        self._subscribers.append(handler)

    def count(self) -> int:
        with self._lock:
            return self._c.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
