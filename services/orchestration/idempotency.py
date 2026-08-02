"""Durable idempotent command log (727 §2.5/§4.2, K1 切片).

每条命令一行,生命周期是 **CAS 状态机**而非纯追加制品::

    RECEIVED --complete--> COMPLETED
             --fail-----> FAILED        (两个终态均不可变)

为什么允许 UPDATE(与 kill_switch_store 的纯追加表不同):命令行承载的是
「这条命令当前处在生命周期哪一步」的**可变性状态**,不是事件历史。防篡改由
哈希链承担——每次状态迁移都**重链**(re-chain):重算该行 record_hash,并按
seq 顺序级联重算其后所有行,使链条在迁移后依然自洽;tamper(绕过本类直接改
库)会被 :meth:`SqliteCommandLog.chain_valid` / 打开时校验立刻抓到。DELETE 由
数据库触发器结构性禁止。

与 727 §4.2 列清单的两点**显式偏差**(设计文档 §4 已登记):
1. 额外 ``seq`` 列(单调插入序号)——列清单中没有可排序字段,链序必须稳定;
2. ``recorded_at`` 语义 = 「本行当前版本被记录的时间」:begin 时为受理时间,
   complete/fail 时更新为迁移时间(列清单没有 completed_at/failed_at,
   迁移时间必须落库才能进链、可验证)。

幂等语义(对齐 research_runner JobQueue invocation_id 先例):
- 同 (command_type, idempotency_key) + 同 payload_hash → 返回既有行(重试安全);
- 同 (command_type, idempotency_key) + 不同 payload_hash → CommandConflictError;
- 同 command_id + 不同 idempotency_key → CommandConflictError。

确定性核心:无墙钟、无随机;所有时间戳均为调用方传入的非负整数。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import Optional

from quantos.contracts.hashing import stable_hash

from services.orchestration.command_envelope import CommandEnvelope


class CommandLogError(RuntimeError):
    """The command log is missing, conflicting, or unverifiable."""


class CommandConflictError(CommandLogError):
    """An idempotency-key or command-id replay carries different content."""


class CommandStateError(CommandLogError):
    """An illegal lifecycle transition was attempted (terminal states are immutable)."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS command_log (
  seq INTEGER NOT NULL UNIQUE,
  command_id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL,
  command_type TEXT NOT NULL,
  subject TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  actor_kind TEXT NOT NULL,
  requested_at INTEGER NOT NULL CHECK (requested_at >= 0),
  expected_state_version INTEGER,
  authorization_ref TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('RECEIVED','COMPLETED','FAILED')),
  result_json TEXT,
  recorded_at INTEGER NOT NULL CHECK (recorded_at >= 0),
  prev_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL,
  UNIQUE (command_type, idempotency_key)
);
CREATE TRIGGER IF NOT EXISTS command_log_no_delete
BEFORE DELETE ON command_log
BEGIN SELECT RAISE(ABORT, 'command_log rows are never deleted'); END;
"""

# 链内容:除 seq/prev_hash/record_hash 外的全部承重字段(prev_hash 单独入哈希)。
_CONTENT_COLUMNS = (
    "command_id",
    "idempotency_key",
    "command_type",
    "subject",
    "actor_id",
    "actor_kind",
    "requested_at",
    "expected_state_version",
    "authorization_ref",
    "payload_hash",
    "payload_json",
    "status",
    "result_json",
    "recorded_at",
)

_SELECT = (
    "SELECT seq,command_id,idempotency_key,command_type,subject,actor_id,actor_kind,"
    " requested_at,expected_state_version,authorization_ref,payload_hash,payload_json,"
    " status,result_json,recorded_at,prev_hash,record_hash FROM command_log"
)


def _payload_json(payload: object) -> str:
    return json.dumps(
        dict(payload),  # type: ignore[arg-type]
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _record_hash(row: dict, *, prev_hash: str) -> str:
    content = {name: row[name] for name in _CONTENT_COLUMNS}
    content["seq"] = row["seq"]
    return stable_hash({"content": content, "prev_hash": prev_hash})


@dataclass(frozen=True)
class CommandRecord:
    """One durable command row (verified against the hash chain on read)."""

    seq: int
    command_id: str
    idempotency_key: str
    command_type: str
    subject: str
    actor_id: str
    actor_kind: str
    requested_at: int
    expected_state_version: Optional[int]
    authorization_ref: str
    payload_hash: str
    payload_json: str
    status: str                       # RECEIVED | COMPLETED | FAILED
    result_json: Optional[str]
    recorded_at: int
    prev_hash: str
    record_hash: str


class SqliteCommandLog:
    """Single-node durable command log with CAS transitions and a re-chained hash chain."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        if not isinstance(path, str) or not path:
            raise CommandLogError("command log path must be a non-empty string")
        if not create and (path == ":memory:" or not os.path.isfile(path)):
            raise CommandLogError(f"command log unavailable: {path}")
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

    # ------------------------------------------------------------------ 内部

    def _rows_locked(self) -> list[sqlite3.Row]:
        try:
            return self._conn.execute(f"{_SELECT} ORDER BY seq").fetchall()
        except sqlite3.DatabaseError as exc:
            raise CommandLogError(f"command log unreadable: {exc}") from exc

    @staticmethod
    def _row_dict(row: sqlite3.Row) -> dict:
        return {name: row[name] for name in row.keys()}

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> CommandRecord:
        return CommandRecord(
            seq=int(row["seq"]),
            command_id=row["command_id"],
            idempotency_key=row["idempotency_key"],
            command_type=row["command_type"],
            subject=row["subject"],
            actor_id=row["actor_id"],
            actor_kind=row["actor_kind"],
            requested_at=int(row["requested_at"]),
            expected_state_version=row["expected_state_version"],
            authorization_ref=row["authorization_ref"],
            payload_hash=row["payload_hash"],
            payload_json=row["payload_json"],
            status=row["status"],
            result_json=row["result_json"],
            recorded_at=int(row["recorded_at"]),
            prev_hash=row["prev_hash"],
            record_hash=row["record_hash"],
        )

    def _verify_locked(self) -> str:
        """全链校验,返回链头;任何一环不符即 CommandLogError(fail-closed)。"""
        previous = ""
        for row in self._rows_locked():
            data = self._row_dict(row)
            if row["prev_hash"] != previous:
                raise CommandLogError(
                    f"command log row seq={row['seq']} previous hash mismatch"
                )
            expected = _record_hash(data, prev_hash=previous)
            if row["record_hash"] != expected:
                raise CommandLogError(
                    f"command log row seq={row['seq']} record hash mismatch"
                )
            previous = expected
        return previous

    def _rechain_locked(self, *, from_seq: int) -> None:
        """状态迁移后重链:从 from_seq 起按序级联重算 prev_hash/record_hash。

        这是 UPDATE 被允许的前提下仍保持篡改可见的关键:任何一行的内容变化都会
        改变其后所有行的链值。O(n) 级联是刻意的诚实开销——命令量远低于事件量,
        单节点研究编排下可忽略;若未来成为瓶颈,正确解法是迁到「追加式迁移表 +
        状态视图」,而不是放弃重链。
        """
        previous = ""
        head_row = self._conn.execute(
            "SELECT record_hash FROM command_log WHERE seq<? ORDER BY seq DESC LIMIT 1",
            (from_seq,),
        ).fetchone()
        if head_row is not None:
            previous = head_row[0]
        rows = self._conn.execute(
            f"{_SELECT} WHERE seq>=? ORDER BY seq", (from_seq,)
        ).fetchall()
        for row in rows:
            data = self._row_dict(row)
            digest = _record_hash(data, prev_hash=previous)
            self._conn.execute(
                "UPDATE command_log SET prev_hash=?, record_hash=? WHERE seq=?",
                (previous, digest, row["seq"]),
            )
            previous = digest

    def _get_row_locked(self, command_id: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            f"{_SELECT} WHERE command_id=?", (command_id,)
        ).fetchone()

    # ------------------------------------------------------------------ 写入

    def begin(self, envelope: CommandEnvelope, *, recorded_at: int) -> CommandRecord:
        """受理命令(幂等)。重试安全语义见模块 docstring。"""
        if not isinstance(envelope, CommandEnvelope):
            raise TypeError("envelope must be a CommandEnvelope")
        if (
            isinstance(recorded_at, bool)
            or not isinstance(recorded_at, int)
            or recorded_at < 0
        ):
            raise ValueError("recorded_at must be a non-negative integer")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                head = self._verify_locked()

                by_id = self._get_row_locked(envelope.command_id)
                if by_id is not None:
                    # 同 command_id 必须完全同源,否则是重放冲突。
                    if (
                        by_id["idempotency_key"] != envelope.idempotency_key
                        or by_id["command_type"] != envelope.command_type
                        or by_id["payload_hash"] != envelope.payload_hash
                    ):
                        raise CommandConflictError(
                            "command_id replay carries different content: "
                            f"{envelope.command_id}"
                        )
                    self._conn.rollback()
                    return self._record_from_row(by_id)

                by_key = self._conn.execute(
                    f"{_SELECT} WHERE command_type=? AND idempotency_key=?",
                    (envelope.command_type, envelope.idempotency_key),
                ).fetchone()
                if by_key is not None:
                    if by_key["payload_hash"] != envelope.payload_hash:
                        raise CommandConflictError(
                            "idempotency key reused with a different payload: "
                            f"{envelope.command_type}/{envelope.idempotency_key}"
                        )
                    self._conn.rollback()
                    return self._record_from_row(by_key)

                seq_row = self._conn.execute(
                    "SELECT COALESCE(MAX(seq),0)+1 FROM command_log"
                ).fetchone()
                seq = int(seq_row[0])
                payload_json = _payload_json(envelope.payload)
                content = {
                    "seq": seq,
                    "command_id": envelope.command_id,
                    "idempotency_key": envelope.idempotency_key,
                    "command_type": envelope.command_type,
                    "subject": envelope.subject,
                    "actor_id": envelope.actor.actor_id,
                    "actor_kind": envelope.actor.kind.value,
                    "requested_at": envelope.requested_at,
                    "expected_state_version": envelope.expected_state_version,
                    "authorization_ref": envelope.authorization_ref,
                    "payload_hash": envelope.payload_hash,
                    "payload_json": payload_json,
                    "status": "RECEIVED",
                    "result_json": None,
                    "recorded_at": recorded_at,
                }
                digest = _record_hash(content, prev_hash=head)
                self._conn.execute(
                    "INSERT INTO command_log"
                    " (seq,command_id,idempotency_key,command_type,subject,actor_id,"
                    " actor_kind,requested_at,expected_state_version,authorization_ref,"
                    " payload_hash,payload_json,status,result_json,recorded_at,"
                    " prev_hash,record_hash)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        seq,
                        envelope.command_id,
                        envelope.idempotency_key,
                        envelope.command_type,
                        envelope.subject,
                        envelope.actor.actor_id,
                        envelope.actor.kind.value,
                        envelope.requested_at,
                        envelope.expected_state_version,
                        envelope.authorization_ref,
                        envelope.payload_hash,
                        payload_json,
                        "RECEIVED",
                        None,
                        recorded_at,
                        head,
                        digest,
                    ),
                )
                self._conn.commit()
                row = self._get_row_locked(envelope.command_id)
                assert row is not None  # 刚插入,必存在
                return self._record_from_row(row)
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def _transition_locked(
        self,
        command_id: str,
        *,
        to_status: str,
        result_json: str,
        at: int,
    ) -> CommandRecord:
        if (
            isinstance(at, bool) or not isinstance(at, int) or at < 0
        ):
            raise ValueError("at must be a non-negative integer")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._verify_locked()
                row = self._get_row_locked(command_id)
                if row is None:
                    raise CommandStateError(f"unknown command: {command_id}")
                if row["status"] != "RECEIVED":
                    # 终态不可变:重复 complete/fail 一律拒绝,绝不静默改写。
                    raise CommandStateError(
                        f"command {command_id} is already terminal: {row['status']}"
                    )
                if at < row["requested_at"]:
                    raise CommandStateError(
                        f"transition time precedes requested_at for {command_id}"
                    )
                self._conn.execute(
                    "UPDATE command_log SET status=?, result_json=?, recorded_at=?"
                    " WHERE command_id=?",
                    (to_status, result_json, at, command_id),
                )
                self._rechain_locked(from_seq=int(row["seq"]))
                self._conn.commit()
                updated = self._get_row_locked(command_id)
                assert updated is not None
                return self._record_from_row(updated)
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def complete(self, command_id: str, *, result_json: str, at: int) -> CommandRecord:
        """CAS 迁移 RECEIVED→COMPLETED,落结果(JSON 字符串)。"""
        if not isinstance(command_id, str) or not command_id:
            raise ValueError("command_id must be non-empty")
        if not isinstance(result_json, str):
            raise TypeError("result_json must be a JSON string")
        return self._transition_locked(
            command_id, to_status="COMPLETED", result_json=result_json, at=at
        )

    def fail(self, command_id: str, *, error_class: str, at: int) -> CommandRecord:
        """CAS 迁移 RECEIVED→FAILED。error_class 以 canonical JSON 落入 result_json
        (列清单无独立错误列——迁移信息必须落库才能进链可验证)。"""
        if not isinstance(command_id, str) or not command_id:
            raise ValueError("command_id must be non-empty")
        if not isinstance(error_class, str) or not error_class:
            raise ValueError("error_class must be non-empty")
        result_json = json.dumps(
            {"error_class": error_class},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return self._transition_locked(
            command_id, to_status="FAILED", result_json=result_json, at=at
        )

    # ------------------------------------------------------------------ 读取

    def get(self, command_id: str) -> Optional[CommandRecord]:
        with self._lock:
            self._verify_locked()
            row = self._get_row_locked(command_id)
            return None if row is None else self._record_from_row(row)

    def find(
        self, command_type: str, idempotency_key: str
    ) -> Optional[CommandRecord]:
        with self._lock:
            self._verify_locked()
            row = self._conn.execute(
                f"{_SELECT} WHERE command_type=? AND idempotency_key=?",
                (command_type, idempotency_key),
            ).fetchone()
            return None if row is None else self._record_from_row(row)

    def in_flight(self) -> tuple[CommandRecord, ...]:
        """状态仍为 RECEIVED 的命令 = 崩溃在飞行中途者。

        镜像 727 §0.4.8 未知订单规则:**只上面,绝不自动重试**——恢复决策属于
        人工/恢复层(K 系列后续切片),本日志不提供 retry API。
        """
        with self._lock:
            self._verify_locked()
            rows = self._conn.execute(
                f"{_SELECT} WHERE status='RECEIVED' ORDER BY seq"
            ).fetchall()
            return tuple(self._record_from_row(row) for row in rows)

    def count(self) -> int:
        with self._lock:
            self._verify_locked()
            return int(
                self._conn.execute("SELECT COUNT(*) FROM command_log").fetchone()[0]
            )

    def chain_valid(self) -> bool:
        with self._lock:
            try:
                self._verify_locked()
                return True
            except (CommandLogError, sqlite3.DatabaseError):
                return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = [
    "CommandConflictError",
    "CommandLogError",
    "CommandRecord",
    "CommandStateError",
    "SqliteCommandLog",
]
