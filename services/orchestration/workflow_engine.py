"""Workflow Engine(727 §2.5,K4b 切片)— 持久化多步工作流,崩溃可恢复,转换有 guard。

每条工作流定义 = 有序步骤序列;每个实例 = 一行持久化状态(current_step_idx +
status)。步骤经 K1 CommandBus 执行 → **崩溃可恢复、重放零副作用**。

转换表(机器钉死,违规 → :class:`WorkflowStateError`)::

    PENDING  → RUNNING                 (首次 run_next)
    RUNNING  → COMPLETED | FAILED | HALTED
    FAILED   → RUNNING                 (resume,显式重试)
    HALTED   → RUNNING                 (resume,显式恢复)
    COMPLETED → (终态,无出边)

崩溃恢复语义(诚实边界):

- 进程死在 CommandBus 执行中途 → 实例停在 RUNNING,命令卡在 command log 的
  RECEIVED(in_flight 上面)。重启后直接再调 run_next:同
  ``wf:{instance}:{step}:{attempt}`` key 走 K1 幂等——命令已 COMPLETED 则
  **零副作用重放推进**;卡 RECEIVED 则 CommandBusError 原样上抛,实例停在
  RUNNING 等 recovery 层裁决(§0.4.8:绝不盲目重跑)。裁决路径:
  ``recovery.resolve_in_flight(MARK_FAILED)`` → run_next(撞 FAILED 终态,
  实例记 FAILED)→ resume → run_next(attempt+1 新 key,executor 重跑)。
  每一步都是显式动作,没有一步是自动的。
- 步骤失败 → step_run FAILED + 实例 FAILED + error=异常类型名,**不外抛**
  (实例级隔离:一个实例失败不拖垮同轮其他实例)。
- attempt = 该 (instance, step_idx) 已失败次数,编进 command_id/
  idempotency_key(与 K3 同法:K1 FAILED 终态不换 key 不重跑,attempt 编 key
  后每次重试是逻辑新命令,K1「每条命令至多执行一次」不变)。
- HALTED 本切片保留给未来的人工暂停(K4b 无 halt API);转换边已钉死,
  resume 路径已可用。

确定性核心:无墙钟、无随机、无网络;时间全为调用方 int ms。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Tuple

from quantos.contracts.alpha import Actor
from quantos.contracts.hashing import stable_hash

from services.orchestration.command_bus import CommandBus, CommandBusError
from services.orchestration.command_envelope import CommandEnvelope


class WorkflowError(RuntimeError):
    """The workflow store is missing, conflicting, or unverifiable."""


class WorkflowConflictError(WorkflowError):
    """A definition/instance re-registration carries different content."""


class WorkflowNotFoundError(WorkflowError):
    """The workflow type or instance is unknown (fail-closed)."""


class WorkflowStateError(WorkflowError):
    """An illegal instance status transition was attempted."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_definitions (
  seq INTEGER NOT NULL UNIQUE,
  workflow_type TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  steps_json TEXT NOT NULL,
  registered_at_ms INTEGER NOT NULL CHECK (registered_at_ms >= 0),
  prev_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_instances (
  seq INTEGER NOT NULL UNIQUE,
  instance_id TEXT PRIMARY KEY,
  workflow_type TEXT NOT NULL,
  version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN
    ('PENDING','RUNNING','COMPLETED','FAILED','HALTED')),
  current_step_idx INTEGER NOT NULL DEFAULT 0 CHECK (current_step_idx >= 0),
  context_json TEXT NOT NULL,
  error TEXT NOT NULL DEFAULT '',
  created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
  updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0),
  prev_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_step_runs (
  run_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  instance_id TEXT NOT NULL,
  step_idx INTEGER NOT NULL CHECK (step_idx >= 0),
  step_id TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('COMPLETED','FAILED')),
  detail TEXT NOT NULL DEFAULT '',
  recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms >= 0),
  prev_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS workflow_definitions_no_update
BEFORE UPDATE ON workflow_definitions
BEGIN SELECT RAISE(ABORT, 'workflow_definitions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS workflow_definitions_no_delete
BEFORE DELETE ON workflow_definitions
BEGIN SELECT RAISE(ABORT, 'workflow_definitions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS workflow_instances_no_delete
BEFORE DELETE ON workflow_instances
BEGIN SELECT RAISE(ABORT, 'workflow_instances rows are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS workflow_step_runs_no_update
BEFORE UPDATE ON workflow_step_runs
BEGIN SELECT RAISE(ABORT, 'workflow_step_runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS workflow_step_runs_no_delete
BEFORE DELETE ON workflow_step_runs
BEGIN SELECT RAISE(ABORT, 'workflow_step_runs is append-only'); END;
"""

# 状态转换表(机器钉死;见模块 docstring 图)。
_ALLOWED_TRANSITIONS = {
    "PENDING": frozenset({"RUNNING"}),
    "RUNNING": frozenset({"COMPLETED", "FAILED", "HALTED"}),
    "FAILED": frozenset({"RUNNING"}),
    "HALTED": frozenset({"RUNNING"}),
    "COMPLETED": frozenset(),
}

_DEF_CONTENT_COLUMNS = ("workflow_type", "version", "steps_json", "registered_at_ms")
_INST_CONTENT_COLUMNS = (
    "instance_id",
    "workflow_type",
    "version",
    "status",
    "current_step_idx",
    "context_json",
    "error",
    "created_at_ms",
    "updated_at_ms",
)
_RUN_CONTENT_COLUMNS = (
    "instance_id",
    "step_idx",
    "step_id",
    "outcome",
    "detail",
    "recorded_at_ms",
)

_DEF_SELECT = (
    "SELECT seq,workflow_type,version,steps_json,registered_at_ms,prev_hash,"
    " record_hash FROM workflow_definitions"
)
_INST_SELECT = (
    "SELECT seq,instance_id,workflow_type,version,status,current_step_idx,"
    " context_json,error,created_at_ms,updated_at_ms,prev_hash,record_hash"
    " FROM workflow_instances"
)
_RUN_SELECT = (
    "SELECT run_seq,instance_id,step_idx,step_id,outcome,detail,recorded_at_ms,"
    " prev_hash,record_hash FROM workflow_step_runs"
)


def _def_hash(row: dict, *, prev_hash: str) -> str:
    content = {name: row[name] for name in _DEF_CONTENT_COLUMNS}
    content["seq"] = row["seq"]
    return stable_hash({"content": content, "prev_hash": prev_hash})


def _inst_hash(row: dict, *, prev_hash: str) -> str:
    content = {name: row[name] for name in _INST_CONTENT_COLUMNS}
    content["seq"] = row["seq"]
    return stable_hash({"content": content, "prev_hash": prev_hash})


def _run_hash(row: dict, *, prev_hash: str) -> str:
    content = {name: row[name] for name in _RUN_CONTENT_COLUMNS}
    content["run_seq"] = row["run_seq"]
    return stable_hash({"content": content, "prev_hash": prev_hash})


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class WorkflowStep:
    step_id: str
    command_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.step_id, str) or not self.step_id.strip():
            raise ValueError("step_id must be a non-empty string")
        if not isinstance(self.command_type, str) or not self.command_type.strip():
            raise ValueError("command_type must be a non-empty string")


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_type: str
    version: str
    steps: Tuple[WorkflowStep, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.workflow_type, str) or not self.workflow_type.strip():
            raise ValueError("workflow_type must be a non-empty string")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be a non-empty string")
        if not isinstance(self.steps, tuple) or len(self.steps) < 1:
            raise ValueError("steps must be a non-empty tuple")
        if any(not isinstance(step, WorkflowStep) for step in self.steps):
            raise ValueError("steps must be WorkflowStep values")
        step_ids = [step.step_id for step in self.steps]
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("step_id values must be unique within a workflow")

    def steps_json(self) -> str:
        return _canonical_json(
            [
                {"command_type": step.command_type, "step_id": step.step_id}
                for step in self.steps
            ]
        )


@dataclass(frozen=True)
class WorkflowInstance:
    instance_id: str
    workflow_type: str
    version: str
    status: str                     # PENDING|RUNNING|COMPLETED|FAILED|HALTED
    current_step_idx: int
    context_json: str
    error: str
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True)
class WorkflowStepRun:
    run_seq: int
    instance_id: str
    step_idx: int
    step_id: str
    outcome: str                    # COMPLETED | FAILED
    detail: str
    recorded_at_ms: int


class SqliteWorkflowStore:
    """Single-node durable workflow store:三链(definitions 追加链 + instances
    重链链 + step_runs 追加链),同款打开语义,可与其他内核 store 共库。"""

    def __init__(self, path: str, *, create: bool = False) -> None:
        if not isinstance(path, str) or not path:
            raise WorkflowError("workflow store path must be a non-empty string")
        if not create and (path == ":memory:" or not os.path.isfile(path)):
            raise WorkflowError(f"workflow store unavailable: {path}")
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

    # ------------------------------------------------------------------ 链

    def _verify_locked(self) -> None:
        try:
            def_rows = self._conn.execute(f"{_DEF_SELECT} ORDER BY seq").fetchall()
            inst_rows = self._conn.execute(f"{_INST_SELECT} ORDER BY seq").fetchall()
            run_rows = self._conn.execute(f"{_RUN_SELECT} ORDER BY run_seq").fetchall()
        except sqlite3.DatabaseError as exc:
            raise WorkflowError(f"workflow store unreadable: {exc}") from exc
        for label, rows, hasher, seq_name in (
            ("definition", def_rows, _def_hash, "seq"),
            ("instance", inst_rows, _inst_hash, "seq"),
            ("step_run", run_rows, _run_hash, "run_seq"),
        ):
            previous = ""
            for row in rows:
                data = {name: row[name] for name in row.keys()}
                if row["prev_hash"] != previous:
                    raise WorkflowError(
                        f"workflow {label} seq={row[seq_name]} previous hash mismatch"
                    )
                expected = hasher(data, prev_hash=previous)
                if row["record_hash"] != expected:
                    raise WorkflowError(
                        f"workflow {label} seq={row[seq_name]} record hash mismatch"
                    )
                previous = expected

    def _rechain_instances_locked(self, *, from_seq: int) -> None:
        head_row = self._conn.execute(
            "SELECT record_hash FROM workflow_instances WHERE seq<?"
            " ORDER BY seq DESC LIMIT 1",
            (from_seq,),
        ).fetchone()
        previous = head_row[0] if head_row is not None else ""
        rows = self._conn.execute(
            f"{_INST_SELECT} WHERE seq>=? ORDER BY seq", (from_seq,)
        ).fetchall()
        for row in rows:
            data = {name: row[name] for name in row.keys()}
            digest = _inst_hash(data, prev_hash=previous)
            self._conn.execute(
                "UPDATE workflow_instances SET prev_hash=?, record_hash=? WHERE seq=?",
                (previous, digest, row["seq"]),
            )
            previous = digest

    def _append_step_run_locked(
        self,
        *,
        instance_id: str,
        step_idx: int,
        step_id: str,
        outcome: str,
        detail: str,
        recorded_at_ms: int,
    ) -> None:
        head_row = self._conn.execute(
            "SELECT record_hash FROM workflow_step_runs ORDER BY run_seq DESC LIMIT 1"
        ).fetchone()
        head = head_row[0] if head_row is not None else ""
        seq_row = self._conn.execute(
            "SELECT COALESCE(MAX(run_seq),0)+1 FROM workflow_step_runs"
        ).fetchone()
        content = {
            "run_seq": int(seq_row[0]),
            "instance_id": instance_id,
            "step_idx": step_idx,
            "step_id": step_id,
            "outcome": outcome,
            "detail": detail,
            "recorded_at_ms": recorded_at_ms,
        }
        digest = _run_hash(content, prev_hash=head)
        self._conn.execute(
            "INSERT INTO workflow_step_runs"
            " (instance_id,step_idx,step_id,outcome,detail,recorded_at_ms,"
            " prev_hash,record_hash) VALUES (?,?,?,?,?,?,?,?)",
            (instance_id, step_idx, step_id, outcome, detail, recorded_at_ms, head, digest),
        )

    # ------------------------------------------------------------------ 行映射

    def _get_def_row_locked(self, workflow_type: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            f"{_DEF_SELECT} WHERE workflow_type=?", (workflow_type,)
        ).fetchone()

    def _get_inst_row_locked(self, instance_id: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            f"{_INST_SELECT} WHERE instance_id=?", (instance_id,)
        ).fetchone()

    @staticmethod
    def _definition_from_row(row: sqlite3.Row) -> WorkflowDefinition:
        raw = json.loads(row["steps_json"])
        return WorkflowDefinition(
            workflow_type=row["workflow_type"],
            version=row["version"],
            steps=tuple(
                WorkflowStep(step_id=item["step_id"], command_type=item["command_type"])
                for item in raw
            ),
        )

    @staticmethod
    def _instance_from_row(row: sqlite3.Row) -> WorkflowInstance:
        return WorkflowInstance(
            instance_id=row["instance_id"],
            workflow_type=row["workflow_type"],
            version=row["version"],
            status=row["status"],
            current_step_idx=int(row["current_step_idx"]),
            context_json=row["context_json"],
            error=row["error"],
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=int(row["updated_at_ms"]),
        )

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> WorkflowStepRun:
        return WorkflowStepRun(
            run_seq=int(row["run_seq"]),
            instance_id=row["instance_id"],
            step_idx=int(row["step_idx"]),
            step_id=row["step_id"],
            outcome=row["outcome"],
            detail=row["detail"],
            recorded_at_ms=int(row["recorded_at_ms"]),
        )

    def _set_status_locked(
        self, row: sqlite3.Row, to_status: str, *, now_ms: int, error: str = ""
    ) -> None:
        current = row["status"]
        if to_status not in _ALLOWED_TRANSITIONS[current]:
            raise WorkflowStateError(
                f"illegal workflow transition {current} -> {to_status} "
                f"for {row['instance_id']}"
            )
        self._conn.execute(
            "UPDATE workflow_instances SET status=?, error=?, updated_at_ms=?"
            " WHERE instance_id=?",
            (to_status, error, now_ms, row["instance_id"]),
        )
        self._rechain_instances_locked(from_seq=int(row["seq"]))

    # ------------------------------------------------------------------ 定义

    def register_definition(
        self, defn: WorkflowDefinition, *, now_ms: int
    ) -> WorkflowDefinition:
        """注册定义。全字段一致幂等;同 type 异 version/异 steps →
        WorkflowConflictError(定义不可静默改——运行中实例绑旧 version)。"""
        if not isinstance(defn, WorkflowDefinition):
            raise TypeError("defn must be a WorkflowDefinition")
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise ValueError("now_ms must be a non-negative integer")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._verify_locked()
                row = self._get_def_row_locked(defn.workflow_type)
                if row is not None:
                    if (
                        row["version"] == defn.version
                        and row["steps_json"] == defn.steps_json()
                    ):
                        self._conn.rollback()
                        return self._definition_from_row(row)
                    raise WorkflowConflictError(
                        f"workflow definition {defn.workflow_type} already "
                        f"registered at version {row['version']}; definitions "
                        "are never silently replaced"
                    )
                head_row = self._conn.execute(
                    "SELECT record_hash FROM workflow_definitions"
                    " ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                head = head_row[0] if head_row is not None else ""
                seq = int(
                    self._conn.execute(
                        "SELECT COALESCE(MAX(seq),0)+1 FROM workflow_definitions"
                    ).fetchone()[0]
                )
                content = {
                    "seq": seq,
                    "workflow_type": defn.workflow_type,
                    "version": defn.version,
                    "steps_json": defn.steps_json(),
                    "registered_at_ms": now_ms,
                }
                digest = _def_hash(content, prev_hash=head)
                self._conn.execute(
                    "INSERT INTO workflow_definitions"
                    " (seq,workflow_type,version,steps_json,registered_at_ms,"
                    " prev_hash,record_hash) VALUES (?,?,?,?,?,?,?)",
                    (seq, defn.workflow_type, defn.version, defn.steps_json(),
                     now_ms, head, digest),
                )
                self._conn.commit()
                row = self._get_def_row_locked(defn.workflow_type)
                assert row is not None
                return self._definition_from_row(row)
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def get_definition(self, workflow_type: str) -> Optional[WorkflowDefinition]:
        with self._lock:
            self._verify_locked()
            row = self._get_def_row_locked(workflow_type)
            return None if row is None else self._definition_from_row(row)

    # ------------------------------------------------------------------ 实例

    def create_instance(
        self,
        instance_id: str,
        workflow_type: str,
        context_json: str,
        *,
        now_ms: int,
    ) -> WorkflowInstance:
        """创建实例(PENDING)。同 instance_id 同 type/context 幂等;不一致 →
        WorkflowConflictError;未知 type → WorkflowNotFoundError。"""
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise ValueError("instance_id must be a non-empty string")
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise ValueError("now_ms must be a non-negative integer")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._verify_locked()
                def_row = self._get_def_row_locked(workflow_type)
                if def_row is None:
                    raise WorkflowNotFoundError(
                        f"unknown workflow type: {workflow_type}"
                    )
                row = self._get_inst_row_locked(instance_id)
                if row is not None:
                    if (
                        row["workflow_type"] == workflow_type
                        and row["context_json"] == context_json
                    ):
                        self._conn.rollback()
                        return self._instance_from_row(row)
                    raise WorkflowConflictError(
                        f"workflow instance {instance_id} already exists with "
                        "different type/context"
                    )
                head_row = self._conn.execute(
                    "SELECT record_hash FROM workflow_instances"
                    " ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                head = head_row[0] if head_row is not None else ""
                seq = int(
                    self._conn.execute(
                        "SELECT COALESCE(MAX(seq),0)+1 FROM workflow_instances"
                    ).fetchone()[0]
                )
                content = {
                    "seq": seq,
                    "instance_id": instance_id,
                    "workflow_type": workflow_type,
                    "version": def_row["version"],  # 实例绑定义时的 version
                    "status": "PENDING",
                    "current_step_idx": 0,
                    "context_json": context_json,
                    "error": "",
                    "created_at_ms": now_ms,
                    "updated_at_ms": now_ms,
                }
                digest = _inst_hash(content, prev_hash=head)
                self._conn.execute(
                    "INSERT INTO workflow_instances"
                    " (seq,instance_id,workflow_type,version,status,current_step_idx,"
                    " context_json,error,created_at_ms,updated_at_ms,prev_hash,"
                    " record_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (seq, instance_id, workflow_type, def_row["version"], "PENDING",
                     0, context_json, "", now_ms, now_ms, head, digest),
                )
                self._conn.commit()
                row = self._get_inst_row_locked(instance_id)
                assert row is not None
                return self._instance_from_row(row)
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def get_instance(self, instance_id: str) -> Optional[WorkflowInstance]:
        with self._lock:
            self._verify_locked()
            row = self._get_inst_row_locked(instance_id)
            return None if row is None else self._instance_from_row(row)

    def instances(self, status: Optional[str] = None) -> tuple[WorkflowInstance, ...]:
        """实例快照(instance_id 字典序,确定性);可按 status 过滤。"""
        with self._lock:
            self._verify_locked()
            if status is None:
                rows = self._conn.execute(
                    f"{_INST_SELECT} ORDER BY instance_id"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    f"{_INST_SELECT} WHERE status=? ORDER BY instance_id", (status,)
                ).fetchall()
            return tuple(self._instance_from_row(row) for row in rows)

    def mark_running(self, instance_id: str, *, now_ms: int) -> WorkflowInstance:
        """PENDING→RUNNING(首跑)或 FAILED/HALTED→RUNNING(显式 resume);
        其他现状 → WorkflowStateError。重进 RUNNING 清 error(历史在 step_runs)。"""
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise ValueError("now_ms must be a non-negative integer")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._verify_locked()
                row = self._get_inst_row_locked(instance_id)
                if row is None:
                    raise WorkflowNotFoundError(f"unknown instance: {instance_id}")
                self._set_status_locked(row, "RUNNING", now_ms=now_ms, error="")
                self._conn.commit()
                updated = self._get_inst_row_locked(instance_id)
                assert updated is not None
                return self._instance_from_row(updated)
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def advance_step(
        self,
        instance_id: str,
        *,
        step_idx: int,
        step_id: str,
        outcome: str,
        detail: str,
        now_ms: int,
    ) -> WorkflowInstance:
        """追加 step_run 并迁移实例(单事务原子)。COMPLETED:idx+1,越界 →
        COMPLETED;FAILED:status=FAILED + error=detail。仅 RUNNING 可调。"""
        if outcome not in ("COMPLETED", "FAILED"):
            raise ValueError("outcome must be COMPLETED or FAILED")
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise ValueError("now_ms must be a non-negative integer")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._verify_locked()
                row = self._get_inst_row_locked(instance_id)
                if row is None:
                    raise WorkflowNotFoundError(f"unknown instance: {instance_id}")
                if row["status"] != "RUNNING":
                    raise WorkflowStateError(
                        f"advance_step requires RUNNING, got {row['status']} "
                        f"for {instance_id}"
                    )
                if int(row["current_step_idx"]) != step_idx:
                    raise WorkflowStateError(
                        f"step index mismatch for {instance_id}: instance at "
                        f"{row['current_step_idx']}, got {step_idx}"
                    )
                self._append_step_run_locked(
                    instance_id=instance_id,
                    step_idx=step_idx,
                    step_id=step_id,
                    outcome=outcome,
                    detail=detail,
                    recorded_at_ms=now_ms,
                )
                if outcome == "COMPLETED":
                    def_row = self._get_def_row_locked(row["workflow_type"])
                    assert def_row is not None
                    step_count = len(json.loads(def_row["steps_json"]))
                    next_idx = step_idx + 1
                    self._conn.execute(
                        "UPDATE workflow_instances SET current_step_idx=?,"
                        " updated_at_ms=? WHERE instance_id=?",
                        (next_idx, now_ms, instance_id),
                    )
                    self._rechain_instances_locked(from_seq=int(row["seq"]))
                    if next_idx >= step_count:
                        fresh = self._get_inst_row_locked(instance_id)
                        assert fresh is not None
                        self._set_status_locked(
                            fresh, "COMPLETED", now_ms=now_ms, error=""
                        )
                else:
                    self._set_status_locked(row, "FAILED", now_ms=now_ms, error=detail)
                self._conn.commit()
                updated = self._get_inst_row_locked(instance_id)
                assert updated is not None
                return self._instance_from_row(updated)
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def count_failed_attempts(self, instance_id: str, step_idx: int) -> int:
        """该 (instance, step_idx) 的已失败次数 = 下次重试的 attempt 序号。"""
        with self._lock:
            self._verify_locked()
            return int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM workflow_step_runs"
                    " WHERE instance_id=? AND step_idx=? AND outcome='FAILED'",
                    (instance_id, step_idx),
                ).fetchone()[0]
            )

    def step_runs(self, instance_id: str) -> tuple[WorkflowStepRun, ...]:
        with self._lock:
            self._verify_locked()
            rows = self._conn.execute(
                f"{_RUN_SELECT} WHERE instance_id=? ORDER BY run_seq", (instance_id,)
            ).fetchall()
            return tuple(self._run_from_row(row) for row in rows)

    def chain_valid(self) -> bool:
        with self._lock:
            try:
                self._verify_locked()
                return True
            except (WorkflowError, sqlite3.DatabaseError):
                return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class WorkflowEngine:
    """持久化多步工作流引擎(步骤经 K1 CommandBus 执行 → 崩溃可恢复)。"""

    def __init__(self, store: SqliteWorkflowStore, command_bus: CommandBus) -> None:
        if not isinstance(store, SqliteWorkflowStore):
            raise TypeError("store must be a SqliteWorkflowStore")
        if not isinstance(command_bus, CommandBus):
            raise TypeError("command_bus must be a CommandBus")
        self._store = store
        self._bus = command_bus

    def register_workflow(
        self, defn: WorkflowDefinition, *, now_ms: int
    ) -> WorkflowDefinition:
        return self._store.register_definition(defn, now_ms=now_ms)

    def start(
        self,
        workflow_type: str,
        *,
        instance_id: str,
        context: Mapping,
        actor: Actor,
        now_ms: int,
    ) -> WorkflowInstance:
        """启动实例(PENDING)。actor 校验类型但不落库——实例表无 actor 列;
        步骤执行时的 actor 经 CommandEnvelope 进命令日志(那里才是承重审计)。"""
        if not isinstance(actor, Actor):
            raise TypeError("actor must be a quantos.contracts.alpha.Actor")
        if not isinstance(context, Mapping):
            raise TypeError("context must be a Mapping")
        context_json = _canonical_json(dict(context))
        return self._store.create_instance(
            instance_id, workflow_type, context_json, now_ms=now_ms
        )

    def run_next(
        self,
        instance_id: str,
        *,
        actor: Actor,
        executor: Callable[[CommandEnvelope], Any],
        now_ms: int,
    ) -> dict:
        """执行当前步骤并推进。返回 {instance_id, status, step_id, advanced}。"""
        if not isinstance(actor, Actor):
            raise TypeError("actor must be a quantos.contracts.alpha.Actor")
        if not callable(executor):
            raise TypeError("executor must be callable")
        instance = self._store.get_instance(instance_id)
        if instance is None:
            raise WorkflowNotFoundError(f"unknown instance: {instance_id}")
        if instance.status in ("COMPLETED", "FAILED", "HALTED"):
            # COMPLETED 终态;FAILED/HALTED 必须显式 resume,绝不静默重跑。
            raise WorkflowStateError(
                f"run_next requires PENDING/RUNNING, got {instance.status} "
                f"for {instance_id}"
            )
        if instance.status == "PENDING":
            instance = self._store.mark_running(instance_id, now_ms=now_ms)

        defn = self._store.get_definition(instance.workflow_type)
        assert defn is not None
        step = defn.steps[instance.current_step_idx]
        attempt = self._store.count_failed_attempts(
            instance_id, instance.current_step_idx
        )
        envelope = CommandEnvelope.create(
            command_id=f"wf:{instance_id}:{step.step_id}:{attempt}",
            command_type=step.command_type,
            requested_at=now_ms,
            actor=actor,
            subject=f"workflow:{instance_id}",
            idempotency_key=f"wf:{instance_id}:{step.step_id}:{attempt}",
            payload=json.loads(instance.context_json),
        )
        try:
            self._bus.execute(envelope, executor)
        except CommandBusError:
            # 结果未知(in-flight 冲突):实例停 RUNNING,原样上抛等 recovery 裁决。
            raise
        except Exception as exc:
            # 步骤失败:记 FAILED,实例级隔离,不外抛。
            updated = self._store.advance_step(
                instance_id,
                step_idx=instance.current_step_idx,
                step_id=step.step_id,
                outcome="FAILED",
                detail=type(exc).__name__,
                now_ms=now_ms,
            )
            return {
                "instance_id": instance_id,
                "status": updated.status,
                "step_id": step.step_id,
                "advanced": False,
            }
        updated = self._store.advance_step(
            instance_id,
            step_idx=instance.current_step_idx,
            step_id=step.step_id,
            outcome="COMPLETED",
            detail="",
            now_ms=now_ms,
        )
        return {
            "instance_id": instance_id,
            "status": updated.status,
            "step_id": step.step_id,
            "advanced": True,
        }

    def resume(self, instance_id: str, *, now_ms: int) -> WorkflowInstance:
        """FAILED/HALTED → RUNNING(显式恢复动作,审计)。COMPLETED/PENDING/
        RUNNING → WorkflowStateError(恢复必须是显式且只针对可恢复态)。"""
        instance = self._store.get_instance(instance_id)
        if instance is None:
            raise WorkflowNotFoundError(f"unknown instance: {instance_id}")
        if instance.status not in ("FAILED", "HALTED"):
            raise WorkflowStateError(
                f"resume requires FAILED/HALTED, got {instance.status} "
                f"for {instance_id}"
            )
        return self._store.mark_running(instance_id, now_ms=now_ms)

    def get(self, instance_id: str) -> Optional[WorkflowInstance]:
        return self._store.get_instance(instance_id)

    def instances(self, status: Optional[str] = None) -> tuple[WorkflowInstance, ...]:
        return self._store.instances(status=status)

    def chain_valid(self) -> bool:
        return self._store.chain_valid()


__all__ = [
    "SqliteWorkflowStore",
    "WorkflowConflictError",
    "WorkflowDefinition",
    "WorkflowEngine",
    "WorkflowError",
    "WorkflowInstance",
    "WorkflowNotFoundError",
    "WorkflowStateError",
    "WorkflowStep",
    "WorkflowStepRun",
]
