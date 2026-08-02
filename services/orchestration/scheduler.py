"""Persistent deterministic scheduler(727 §16,K3 切片)。

存储 :class:`SqliteSchedulerStore`(与 K1/K2 同款打开语义、可共库)+
编排 :class:`Scheduler`(复用 K1 CommandBus 与 K2 LeaderLeaseStore)。

核心语义(逐条在设计文档 §9 登记):

1. **只有 leader 兑现**:run_once 先 acquire 租约;异 owner 持有中 → 每个 due
   task 记 SKIPPED_NOT_LEADER(审计留痕)后返回。每个 task 兑现前再过一次
   require_owner fencing 闸门。
2. **按计划时刻推进,不漂移**:INTERVAL 完成后 next_due = slot_ms + interval_ms
   (slot 是计划时刻,不是 now)——调度延迟不会累积成周期漂移。
3. **错过补跑合并(coalesce)**:落后超过一个周期时,只为被跳过的旧槽位各追加
   一条 COALESCED run(诚实记录错过了哪些,绝不静默),只兑现最新完整周期槽位。
   ONE_SHOT 不合并。
4. **幂等补跑**:每次兑现经 CommandBus 执行,command_id/idempotency_key =
   ``sched:{task_id}:{slot}:{attempts}``。同一 (task, 槽位, 尝试序) 重跑 = K1
   重放,executor 零重复副作用。**与任务书规格的显式偏差**:key 编入 attempts
   (规格写的是 ``{task_id}:{slot'}``)——K1 规定 FAILED 终态不换 key 不重跑,
   若 key 不含 attempts,失败退避重试会永远撞 CommandStateError,重试机制名存
   实亡。编入后每次重试是逻辑上的新命令,K1「每条命令至多执行一次」不变。
5. **确定性退避 + DLQ**:FAILED → attempts+1,耗尽(max_attempts)→ dead=1 +
   追加 DLQ run,只上面、绝不自动重试;否则 next_due = now_ms + min(
   backoff_base_ms * 2**attempts, backoff_cap_ms)(attempts 为递增后的值,
   base/cap 为 store 构造参数)。
6. **未知结果不记账、大声抛出**:CommandBusError(in-flight 冲突 = 结果未知)
   不从 run_once 吞掉——中止本轮,命令留在 RECEIVED 由 in_flight() 上面,
   人工裁决(镜像 727 §0.4.8)。CommandStateError(FAILED 重放 = 结果已知为
   失败)按任务失败记账,退避语义正常推进。
7. **record_run 单事务原子**:run 追加与 task 状态迁移在同一事务提交——崩溃
   窗口只可能整段缺失(重放后恰好补回一条 run),不存在半截记录。

确定性核心:无墙钟、无随机、**不起线程**(没有后台 tick;兑现节奏由调用方
驱动 run_once)。无网络、不触碰 live/order/凭证路径。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from quantos.contracts.alpha import Actor
from quantos.contracts.hashing import stable_hash

from services.orchestration.command_bus import CommandBus, CommandBusError
from services.orchestration.command_envelope import CommandEnvelope
from services.orchestration.leader_lease import (
    LeaderLeaseBusyError,
    LeaderLeaseFencedError,
    SqliteLeaderLeaseStore,
)


class SchedulerError(RuntimeError):
    """The scheduler store is missing, conflicting, or unverifiable."""


class SchedulerConflictError(SchedulerError):
    """A task_id re-registration carries different registration fields."""


class SchedulerStateError(SchedulerError):
    """An illegal scheduler state transition was attempted (e.g. reviving a live task)."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduler_tasks (
  seq INTEGER NOT NULL UNIQUE,
  task_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('INTERVAL','ONE_SHOT')),
  interval_ms INTEGER,
  run_at_ms INTEGER,
  payload_json TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
  next_due_ms INTEGER NOT NULL CHECK (next_due_ms >= 0),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
  dead INTEGER NOT NULL DEFAULT 0 CHECK (dead IN (0,1)),
  registered_at_ms INTEGER NOT NULL CHECK (registered_at_ms >= 0),
  prev_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scheduler_runs (
  run_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  slot_ms INTEGER NOT NULL CHECK (slot_ms >= 0),
  fencing_token INTEGER NOT NULL CHECK (fencing_token >= 0),
  outcome TEXT NOT NULL CHECK (outcome IN
    ('COMPLETED','FAILED','COALESCED','DLQ','SKIPPED_NOT_LEADER')),
  detail TEXT NOT NULL DEFAULT '',
  recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms >= 0),
  prev_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS scheduler_tasks_no_delete
BEFORE DELETE ON scheduler_tasks
BEGIN SELECT RAISE(ABORT, 'scheduler_tasks rows are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS scheduler_runs_no_update
BEFORE UPDATE ON scheduler_runs
BEGIN SELECT RAISE(ABORT, 'scheduler_runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS scheduler_runs_no_delete
BEFORE DELETE ON scheduler_runs
BEGIN SELECT RAISE(ABORT, 'scheduler_runs is append-only'); END;
"""

_TASK_CONTENT_COLUMNS = (
    "task_id",
    "kind",
    "interval_ms",
    "run_at_ms",
    "payload_json",
    "enabled",
    "next_due_ms",
    "attempts",
    "max_attempts",
    "dead",
    "registered_at_ms",
)

_RUN_CONTENT_COLUMNS = (
    "task_id",
    "slot_ms",
    "fencing_token",
    "outcome",
    "detail",
    "recorded_at_ms",
)

_TASK_SELECT = (
    "SELECT seq,task_id,kind,interval_ms,run_at_ms,payload_json,enabled,next_due_ms,"
    " attempts,max_attempts,dead,registered_at_ms,prev_hash,record_hash"
    " FROM scheduler_tasks"
)

_RUN_SELECT = (
    "SELECT run_seq,task_id,slot_ms,fencing_token,outcome,detail,recorded_at_ms,"
    " prev_hash,record_hash FROM scheduler_runs"
)


def _task_hash(row: dict, *, prev_hash: str) -> str:
    content = {name: row[name] for name in _TASK_CONTENT_COLUMNS}
    content["seq"] = row["seq"]
    return stable_hash({"content": content, "prev_hash": prev_hash})


def _run_hash(row: dict, *, prev_hash: str) -> str:
    content = {name: row[name] for name in _RUN_CONTENT_COLUMNS}
    content["run_seq"] = row["run_seq"]
    return stable_hash({"content": content, "prev_hash": prev_hash})


def _payload_json(payload: Mapping) -> str:
    return json.dumps(
        dict(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


@dataclass(frozen=True)
class ScheduledTask:
    """One registered task row (verified against the tasks hash chain on read)."""

    task_id: str
    kind: str                       # INTERVAL | ONE_SHOT
    interval_ms: Optional[int]
    run_at_ms: Optional[int]
    payload_json: str
    enabled: bool
    next_due_ms: int
    attempts: int
    max_attempts: int
    dead: bool                      # DLQ:attempts 耗尽,只上面,绝不自动重试
    registered_at_ms: int


@dataclass(frozen=True)
class SchedulerRun:
    """One append-only run record (审计:每次兑现/合并/跳过/DLQ 各一行)。"""

    run_seq: int
    task_id: str
    slot_ms: int
    fencing_token: int
    outcome: str                    # COMPLETED|FAILED|COALESCED|DLQ|SKIPPED_NOT_LEADER
    detail: str
    recorded_at_ms: int


class SqliteSchedulerStore:
    """Single-node durable scheduler store: tasks chain (re-chained) + runs chain (append-only)."""

    def __init__(
        self,
        path: str,
        *,
        create: bool = False,
        backoff_base_ms: int = 60_000,
        backoff_cap_ms: int = 3_600_000,
    ) -> None:
        if not isinstance(path, str) or not path:
            raise SchedulerError("scheduler store path must be a non-empty string")
        for name, value in (
            ("backoff_base_ms", backoff_base_ms),
            ("backoff_cap_ms", backoff_cap_ms),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self._backoff_base_ms = backoff_base_ms
        self._backoff_cap_ms = backoff_cap_ms
        if not create and (path == ":memory:" or not os.path.isfile(path)):
            raise SchedulerError(f"scheduler store unavailable: {path}")
        if create and path != ":memory:":
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        try:
            with self._lock:
                self._conn.execute("PRAGMA busy_timeout=5000")
                if create:
                    try:
                        self._conn.execute("PRAGMA journal_mode=WAL")
                    except sqlite3.OperationalError as exc:
                        if "locked" not in str(exc).lower():
                            raise
                        self._conn.execute("BEGIN IMMEDIATE")
                        self._conn.rollback()
                        self._conn.execute("PRAGMA journal_mode=WAL")
                    self._conn.execute("PRAGMA synchronous=FULL")
                    self._conn.executescript(_SCHEMA)
                    self._conn.commit()
                self._verify_locked()  # fail-closed:链不可验证的库绝不打开
        except Exception:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            raise

    # ------------------------------------------------------------------ 校验

    @staticmethod
    def _require_now(value: object, *, name: str = "now_ms") -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
        return value

    # ------------------------------------------------------------------ 链

    def _verify_locked(self) -> tuple[str, str]:
        """双链校验(tasks 重链链 + runs 追加链),返回两个链头。"""
        tasks_head = ""
        try:
            task_rows = self._conn.execute(f"{_TASK_SELECT} ORDER BY seq").fetchall()
            run_rows = self._conn.execute(f"{_RUN_SELECT} ORDER BY run_seq").fetchall()
        except sqlite3.DatabaseError as exc:
            raise SchedulerError(f"scheduler store unreadable: {exc}") from exc
        for row in task_rows:
            data = {name: row[name] for name in row.keys()}
            if row["prev_hash"] != tasks_head:
                raise SchedulerError(
                    f"scheduler task seq={row['seq']} previous hash mismatch"
                )
            expected = _task_hash(data, prev_hash=tasks_head)
            if row["record_hash"] != expected:
                raise SchedulerError(
                    f"scheduler task seq={row['seq']} record hash mismatch"
                )
            tasks_head = expected
        runs_head = ""
        for row in run_rows:
            data = {name: row[name] for name in row.keys()}
            if row["prev_hash"] != runs_head:
                raise SchedulerError(
                    f"scheduler run seq={row['run_seq']} previous hash mismatch"
                )
            expected = _run_hash(data, prev_hash=runs_head)
            if row["record_hash"] != expected:
                raise SchedulerError(
                    f"scheduler run seq={row['run_seq']} record hash mismatch"
                )
            runs_head = expected
        return tasks_head, runs_head

    def _rechain_tasks_locked(self, *, from_seq: int) -> None:
        """task 行迁移后重链(与 command log / leader lease 同一裁定)。"""
        head_row = self._conn.execute(
            "SELECT record_hash FROM scheduler_tasks WHERE seq<? ORDER BY seq DESC LIMIT 1",
            (from_seq,),
        ).fetchone()
        previous = head_row[0] if head_row is not None else ""
        rows = self._conn.execute(
            f"{_TASK_SELECT} WHERE seq>=? ORDER BY seq", (from_seq,)
        ).fetchall()
        for row in rows:
            data = {name: row[name] for name in row.keys()}
            digest = _task_hash(data, prev_hash=previous)
            self._conn.execute(
                "UPDATE scheduler_tasks SET prev_hash=?, record_hash=? WHERE seq=?",
                (previous, digest, row["seq"]),
            )
            previous = digest

    def _append_run_locked(
        self,
        *,
        task_id: str,
        slot_ms: int,
        fencing_token: int,
        outcome: str,
        detail: str,
        recorded_at_ms: int,
    ) -> None:
        head_row = self._conn.execute(
            "SELECT record_hash FROM scheduler_runs ORDER BY run_seq DESC LIMIT 1"
        ).fetchone()
        head = head_row[0] if head_row is not None else ""
        seq_row = self._conn.execute(
            "SELECT COALESCE(MAX(run_seq),0)+1 FROM scheduler_runs"
        ).fetchone()
        content = {
            "run_seq": int(seq_row[0]),
            "task_id": task_id,
            "slot_ms": slot_ms,
            "fencing_token": fencing_token,
            "outcome": outcome,
            "detail": detail,
            "recorded_at_ms": recorded_at_ms,
        }
        digest = _run_hash(content, prev_hash=head)
        self._conn.execute(
            "INSERT INTO scheduler_runs"
            " (task_id,slot_ms,fencing_token,outcome,detail,recorded_at_ms,"
            " prev_hash,record_hash) VALUES (?,?,?,?,?,?,?,?)",
            (task_id, slot_ms, fencing_token, outcome, detail, recorded_at_ms, head, digest),
        )

    def _get_task_row_locked(self, task_id: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            f"{_TASK_SELECT} WHERE task_id=?", (task_id,)
        ).fetchone()

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> ScheduledTask:
        return ScheduledTask(
            task_id=row["task_id"],
            kind=row["kind"],
            interval_ms=row["interval_ms"],
            run_at_ms=row["run_at_ms"],
            payload_json=row["payload_json"],
            enabled=bool(row["enabled"]),
            next_due_ms=int(row["next_due_ms"]),
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            dead=bool(row["dead"]),
            registered_at_ms=int(row["registered_at_ms"]),
        )

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> SchedulerRun:
        return SchedulerRun(
            run_seq=int(row["run_seq"]),
            task_id=row["task_id"],
            slot_ms=int(row["slot_ms"]),
            fencing_token=int(row["fencing_token"]),
            outcome=row["outcome"],
            detail=row["detail"],
            recorded_at_ms=int(row["recorded_at_ms"]),
        )

    # ------------------------------------------------------------------ 任务

    def register(
        self,
        task_id: str,
        *,
        kind: str,
        interval_ms: Optional[int] = None,
        run_at_ms: Optional[int] = None,
        payload: Mapping,
        max_attempts: int = 3,
        now_ms: int,
    ) -> ScheduledTask:
        """注册任务。同 task_id 全字段一致 → 幂等返回;不一致 → SchedulerConflictError。"""
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        if kind not in ("INTERVAL", "ONE_SHOT"):
            raise ValueError("kind must be INTERVAL or ONE_SHOT")
        if kind == "INTERVAL":
            if (
                isinstance(interval_ms, bool)
                or not isinstance(interval_ms, int)
                or interval_ms <= 0
            ):
                raise ValueError("INTERVAL tasks require interval_ms > 0")
            if run_at_ms is not None:
                raise ValueError("INTERVAL tasks must not set run_at_ms")
        else:
            if (
                isinstance(run_at_ms, bool)
                or not isinstance(run_at_ms, int)
                or run_at_ms < 0
            ):
                raise ValueError("ONE_SHOT tasks require run_at_ms >= 0")
            if interval_ms is not None:
                raise ValueError("ONE_SHOT tasks must not set interval_ms")
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a Mapping")
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or max_attempts < 1
        ):
            raise ValueError("max_attempts must be a positive integer")
        now_ms = self._require_now(now_ms)
        payload_json = _payload_json(payload)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                head, _ = self._verify_locked()
                row = self._get_task_row_locked(task_id)
                if row is not None:
                    # 注册字段(不含运行态)全一致 → 幂等;否则冲突。
                    if (
                        row["kind"] == kind
                        and row["interval_ms"] == interval_ms
                        and row["run_at_ms"] == run_at_ms
                        and row["payload_json"] == payload_json
                        and int(row["max_attempts"]) == max_attempts
                    ):
                        self._conn.rollback()
                        return self._task_from_row(row)
                    raise SchedulerConflictError(
                        f"task re-registration carries different fields: {task_id}"
                    )
                seq = int(
                    self._conn.execute(
                        "SELECT COALESCE(MAX(seq),0)+1 FROM scheduler_tasks"
                    ).fetchone()[0]
                )
                next_due = (
                    now_ms + interval_ms if kind == "INTERVAL" else run_at_ms
                )
                content = {
                    "seq": seq,
                    "task_id": task_id,
                    "kind": kind,
                    "interval_ms": interval_ms,
                    "run_at_ms": run_at_ms,
                    "payload_json": payload_json,
                    "enabled": 1,
                    "next_due_ms": next_due,
                    "attempts": 0,
                    "max_attempts": max_attempts,
                    "dead": 0,
                    "registered_at_ms": now_ms,
                }
                digest = _task_hash(content, prev_hash=head)
                self._conn.execute(
                    "INSERT INTO scheduler_tasks"
                    " (seq,task_id,kind,interval_ms,run_at_ms,payload_json,enabled,"
                    " next_due_ms,attempts,max_attempts,dead,registered_at_ms,"
                    " prev_hash,record_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        seq,
                        task_id,
                        kind,
                        interval_ms,
                        run_at_ms,
                        payload_json,
                        1,
                        next_due,
                        0,
                        max_attempts,
                        0,
                        now_ms,
                        head,
                        digest,
                    ),
                )
                self._conn.commit()
                row = self._get_task_row_locked(task_id)
                assert row is not None
                return self._task_from_row(row)
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def set_enabled(self, task_id: str, *, enabled: bool, now_ms: int) -> ScheduledTask:
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self._require_now(now_ms)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._verify_locked()
                row = self._get_task_row_locked(task_id)
                if row is None:
                    raise SchedulerError(f"unknown task: {task_id}")
                self._conn.execute(
                    "UPDATE scheduler_tasks SET enabled=? WHERE task_id=?",
                    (1 if enabled else 0, task_id),
                )
                self._rechain_tasks_locked(from_seq=int(row["seq"]))
                self._conn.commit()
                row = self._get_task_row_locked(task_id)
                assert row is not None
                return self._task_from_row(row)
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def get(self, task_id: str) -> Optional[ScheduledTask]:
        with self._lock:
            self._verify_locked()
            row = self._get_task_row_locked(task_id)
            return None if row is None else self._task_from_row(row)

    def due_tasks(self, now_ms: int) -> tuple[ScheduledTask, ...]:
        """到期任务:enabled ∧ ¬dead ∧ next_due_ms<=now_ms,按 (next_due_ms, task_id)
        排序——确定性快照序。"""
        self._require_now(now_ms)
        with self._lock:
            self._verify_locked()
            rows = self._conn.execute(
                f"{_TASK_SELECT} WHERE enabled=1 AND dead=0 AND next_due_ms<=?"
                " ORDER BY next_due_ms, task_id",
                (now_ms,),
            ).fetchall()
            return tuple(self._task_from_row(row) for row in rows)

    def dead_tasks(self) -> tuple[ScheduledTask, ...]:
        """DLQ 任务(attempts 耗尽)。只上面,绝不自动重试。"""
        with self._lock:
            self._verify_locked()
            rows = self._conn.execute(
                f"{_TASK_SELECT} WHERE dead=1 ORDER BY task_id"
            ).fetchall()
            return tuple(self._task_from_row(row) for row in rows)

    def revive(self, task_id: str, *, now_ms: int) -> ScheduledTask:
        """DLQ 任务显式复活(recovery 层调用;人工驱动的显式动作,绝不自动)。

        dead=0 / attempts=0;INTERVAL → next_due = now_ms + interval_ms;
        ONE_SHOT → enabled=1, next_due = now_ms。非 dead → SchedulerStateError。
        复活不是槽位兑现,**不进** scheduler_runs(outcome 枚举只承载兑现语义);
        防篡改由 tasks 重链承担,复活后的行迁移完整入链。
        """
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        now_ms = self._require_now(now_ms)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._verify_locked()
                row = self._get_task_row_locked(task_id)
                if row is None:
                    raise SchedulerError(f"unknown task: {task_id}")
                if not row["dead"]:
                    raise SchedulerStateError(
                        f"revive requires a dead (DLQ) task: {task_id}"
                    )
                if row["kind"] == "INTERVAL":
                    self._conn.execute(
                        "UPDATE scheduler_tasks SET dead=0, attempts=0, next_due_ms=?"
                        " WHERE task_id=?",
                        (now_ms + int(row["interval_ms"]), task_id),
                    )
                else:
                    self._conn.execute(
                        "UPDATE scheduler_tasks SET dead=0, attempts=0, enabled=1,"
                        " next_due_ms=? WHERE task_id=?",
                        (now_ms, task_id),
                    )
                self._rechain_tasks_locked(from_seq=int(row["seq"]))
                self._conn.commit()
                updated = self._get_task_row_locked(task_id)
                assert updated is not None
                return self._task_from_row(updated)
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    # ------------------------------------------------------------------ 运行记录

    def record_run(
        self,
        task_id: str,
        *,
        slot_ms: int,
        fencing_token: int,
        outcome: str,
        detail: str = "",
        now_ms: int,
    ) -> Optional[ScheduledTask]:
        """追加 run 记录并按 outcome 迁移 task 状态(单事务原子)。

        返回迁移后的 task(COMPLETED/FAILED);纯记录型 outcome(COALESCED/
        SKIPPED_NOT_LEADER/DLQ)返回 None。
        """
        if outcome not in ("COMPLETED", "FAILED", "COALESCED", "DLQ", "SKIPPED_NOT_LEADER"):
            raise ValueError(f"unsupported outcome: {outcome}")
        self._require_now(slot_ms, name="slot_ms")
        now_ms = self._require_now(now_ms)
        if (
            isinstance(fencing_token, bool)
            or not isinstance(fencing_token, int)
            or fencing_token < 0
        ):
            raise ValueError("fencing_token must be a non-negative integer")
        if not isinstance(detail, str):
            raise TypeError("detail must be a string")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._verify_locked()
                row = self._get_task_row_locked(task_id)
                if row is None:
                    raise SchedulerError(f"unknown task: {task_id}")
                self._append_run_locked(
                    task_id=task_id,
                    slot_ms=slot_ms,
                    fencing_token=fencing_token,
                    outcome=outcome,
                    detail=detail,
                    recorded_at_ms=now_ms,
                )
                if outcome == "COMPLETED":
                    if row["kind"] == "INTERVAL":
                        # 按计划时刻推进,不按 now——调度延迟不累积成漂移。
                        self._conn.execute(
                            "UPDATE scheduler_tasks SET attempts=0, next_due_ms=?"
                            " WHERE task_id=?",
                            (slot_ms + int(row["interval_ms"]), task_id),
                        )
                    else:
                        self._conn.execute(
                            "UPDATE scheduler_tasks SET attempts=0, enabled=0"
                            " WHERE task_id=?",
                            (task_id,),
                        )
                    self._rechain_tasks_locked(from_seq=int(row["seq"]))
                elif outcome == "FAILED":
                    attempts = int(row["attempts"]) + 1
                    if attempts >= int(row["max_attempts"]):
                        # DLQ:attempts 耗尽 → dead=1 + 追加 DLQ run,只上面。
                        self._conn.execute(
                            "UPDATE scheduler_tasks SET attempts=?, dead=1"
                            " WHERE task_id=?",
                            (attempts, task_id),
                        )
                        self._rechain_tasks_locked(from_seq=int(row["seq"]))
                        self._append_run_locked(
                            task_id=task_id,
                            slot_ms=slot_ms,
                            fencing_token=fencing_token,
                            outcome="DLQ",
                            detail=(
                                f"attempts exhausted ({attempts}>="
                                f"{int(row['max_attempts'])}); last error: {detail}"
                            ),
                            recorded_at_ms=now_ms,
                        )
                    else:
                        backoff = min(
                            self._backoff_base_ms * (2 ** attempts),
                            self._backoff_cap_ms,
                        )
                        self._conn.execute(
                            "UPDATE scheduler_tasks SET attempts=?, next_due_ms=?"
                            " WHERE task_id=?",
                            (attempts, now_ms + backoff, task_id),
                        )
                        self._rechain_tasks_locked(from_seq=int(row["seq"]))
                self._conn.commit()
                if outcome in ("COMPLETED", "FAILED"):
                    updated = self._get_task_row_locked(task_id)
                    assert updated is not None
                    return self._task_from_row(updated)
                return None
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def runs(self, task_id: Optional[str] = None) -> tuple[SchedulerRun, ...]:
        """追加序运行记录(可按 task_id 过滤)。"""
        with self._lock:
            self._verify_locked()
            if task_id is None:
                rows = self._conn.execute(
                    f"{_RUN_SELECT} ORDER BY run_seq"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    f"{_RUN_SELECT} WHERE task_id=? ORDER BY run_seq",
                    (task_id,),
                ).fetchall()
            return tuple(self._run_from_row(row) for row in rows)

    def chain_valid(self) -> bool:
        with self._lock:
            try:
                self._verify_locked()
                return True
            except (SchedulerError, sqlite3.DatabaseError):
                return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class Scheduler:
    """Leader-gated deterministic scheduler over K1 command bus + K2 leases.

    不起线程:兑现节奏由调用方驱动 :meth:`run_once`(生产上由外层循环/cron
    按固定节拍调用;节奏本身不是本组件的语义)。
    """

    def __init__(
        self,
        *,
        tasks: SqliteSchedulerStore,
        command_bus: CommandBus,
        leases: SqliteLeaderLeaseStore,
    ) -> None:
        if not isinstance(tasks, SqliteSchedulerStore):
            raise TypeError("tasks must be a SqliteSchedulerStore")
        if not isinstance(command_bus, CommandBus):
            raise TypeError("command_bus must be a CommandBus")
        if not isinstance(leases, SqliteLeaderLeaseStore):
            raise TypeError("leases must be a SqliteLeaderLeaseStore")
        self._tasks = tasks
        self._bus = command_bus
        self._leases = leases

    def run_once(
        self,
        *,
        resource: str,
        owner_id: str,
        actor: Actor,
        now_ms: int,
        lease_ms: int,
        executor: Callable[[CommandEnvelope], Any],
    ) -> dict:
        """兑现一轮到期任务。返回计数摘要(见模块 docstring)。"""
        if not isinstance(actor, Actor):
            raise TypeError("actor must be a quantos.contracts.alpha.Actor")
        if not callable(executor):
            raise TypeError("executor must be callable")
        summary = {
            "fired": 0,
            "completed": 0,
            "failed": 0,
            "coalesced": 0,
            "dlq": 0,
            "skipped_not_leader": 0,
            "leader": False,
            "fencing_token": 0,
        }
        try:
            lease = self._leases.acquire(
                resource, owner_id=owner_id, now_ms=now_ms, lease_ms=lease_ms
            )
        except LeaderLeaseBusyError:
            # 单实例语义:只有 leader 兑现;跳过也进审计。
            holder = self._leases.current(resource, now_ms=now_ms)
            detail = f"held_by:{holder.owner_id}" if holder else "not leader"
            token = holder.fencing_token if holder else 0
            for task in self._tasks.due_tasks(now_ms):
                self._tasks.record_run(
                    task.task_id,
                    slot_ms=task.next_due_ms,
                    fencing_token=token,
                    outcome="SKIPPED_NOT_LEADER",
                    detail=detail,
                    now_ms=now_ms,
                )
                summary["skipped_not_leader"] += 1
            return summary

        summary["leader"] = True
        summary["fencing_token"] = lease.fencing_token
        for task in self._tasks.due_tasks(now_ms):
            slot = task.next_due_ms
            # 错过补跑合并:落后超过一个周期 → 旧槽位各记 COALESCED,只兑现最新槽位。
            if (
                task.kind == "INTERVAL"
                and task.interval_ms is not None
                and now_ms - slot > task.interval_ms
            ):
                missed = (now_ms - slot) // task.interval_ms
                fired_slot = slot + missed * task.interval_ms
                for k in range(missed):
                    self._tasks.record_run(
                        task.task_id,
                        slot_ms=slot + k * task.interval_ms,
                        fencing_token=lease.fencing_token,
                        outcome="COALESCED",
                        detail=f"coalesced into slot {fired_slot}",
                        now_ms=now_ms,
                    )
                    summary["coalesced"] += 1
                slot = fired_slot

            # 每个 task 兑现前过一次 fencing 闸门(租约可能轮中丢失)。
            try:
                self._leases.require_owner(
                    resource,
                    owner_id=owner_id,
                    fencing_token=lease.fencing_token,
                    now_ms=now_ms,
                )
            except LeaderLeaseFencedError:
                self._tasks.record_run(
                    task.task_id,
                    slot_ms=slot,
                    fencing_token=lease.fencing_token,
                    outcome="SKIPPED_NOT_LEADER",
                    detail="lease lost mid-round",
                    now_ms=now_ms,
                )
                summary["skipped_not_leader"] += 1
                summary["leader"] = False
                break

            envelope = CommandEnvelope.create(
                command_id=f"sched:{task.task_id}:{slot}:{task.attempts}",
                command_type=task.task_id,
                requested_at=now_ms,
                actor=actor,
                subject=f"scheduler:{task.task_id}",
                idempotency_key=f"{task.task_id}:{slot}:{task.attempts}",
                payload=json.loads(task.payload_json),
            )
            summary["fired"] += 1
            try:
                self._bus.execute(envelope, executor)
            except CommandBusError:
                # 结果未知(in-flight 冲突):不记账、大声抛出,交 in_flight/人工。
                raise
            except Exception as exc:
                # 含 CommandStateError(FAILED 重放 = 结果已知为失败):按失败记账。
                updated = self._tasks.record_run(
                    task.task_id,
                    slot_ms=slot,
                    fencing_token=lease.fencing_token,
                    outcome="FAILED",
                    detail=type(exc).__name__,
                    now_ms=now_ms,
                )
                summary["failed"] += 1
                if updated is not None and updated.dead:
                    summary["dlq"] += 1
                continue  # 单任务失败不拖垮整轮
            self._tasks.record_run(
                task.task_id,
                slot_ms=slot,
                fencing_token=lease.fencing_token,
                outcome="COMPLETED",
                detail="",
                now_ms=now_ms,
            )
            summary["completed"] += 1
        return summary


__all__ = [
    "ScheduledTask",
    "Scheduler",
    "SchedulerConflictError",
    "SchedulerError",
    "SchedulerRun",
    "SchedulerStateError",
    "SqliteSchedulerStore",
]
