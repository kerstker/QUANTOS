"""Leader Lease(727 §2.5/§16,K2 切片)— 单实例 Leader + fencing token + 任务租约。

用途:自治内核后续切片(K3 scheduler、K4 recovery 等)的任何「同一时刻只能有
一个执行者」的工作,都必须先持有对应 resource 的 lease,并在每次副作用前经
:meth:`SqliteLeaderLeaseStore.require_owner` 校验 fencing token——**stale leader
写不进**,这是 fencing 的核心语义。

Honest boundary(诚实边界):
- **单节点** SQLite CAS 实现,防的是「本机多实例/崩溃重启后的僵尸执行者」,
  **不是**跨主机共识(无 quorum、无网络);跨主机选主是 post-L2(Phase 14)的
  显式非目标。墙钟跳变会让「过期」判定失真——本机单调性由部署环境保证,
  本组件只承诺确定性语义(所有时间均为调用方传入的 int ms)。
- fencing token 只是单调序号;下游 store 是否把 token 写进自己的 CAS 条件,
  是各消费方自己的事(K3 起接入)。

哈希链与 command log 同款:每个 resource 一行(生命周期是可变性 CAS 状态,
非追加制品),获得/续期/释放/易主都重写该行并**重链**(按 seq 级联重算其后
所有行);DELETE 触发器结构性禁止。token **释放后不归零**(行不删,标
released=1)——token 归零会让「旧 token 又被发出」的 fencing 证明失效。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import Optional

from quantos.contracts.hashing import stable_hash


class LeaderLeaseError(RuntimeError):
    """The leader-lease store is missing, conflicting, or unverifiable."""


class LeaderLeaseBusyError(LeaderLeaseError):
    """The resource is held by a different live owner."""


class LeaderLeaseFencedError(LeaderLeaseError):
    """The caller is not the current fenced owner (stale/unknown/expired/released)."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS leader_leases (
  seq INTEGER NOT NULL UNIQUE,
  resource TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  fencing_token INTEGER NOT NULL CHECK (fencing_token >= 1),
  acquired_at_ms INTEGER NOT NULL CHECK (acquired_at_ms >= 0),
  expires_at_ms INTEGER NOT NULL CHECK (expires_at_ms >= 0),
  renewed_at_ms INTEGER NOT NULL CHECK (renewed_at_ms >= 0),
  released INTEGER NOT NULL DEFAULT 0 CHECK (released IN (0,1)),
  prev_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS leader_leases_no_delete
BEFORE DELETE ON leader_leases
BEGIN SELECT RAISE(ABORT, 'leader_leases rows are never deleted'); END;
"""

# 链内容:除 prev_hash/record_hash 外的全部承重字段。
_CONTENT_COLUMNS = (
    "resource",
    "owner_id",
    "fencing_token",
    "acquired_at_ms",
    "expires_at_ms",
    "renewed_at_ms",
    "released",
)

_SELECT = (
    "SELECT seq,resource,owner_id,fencing_token,acquired_at_ms,expires_at_ms,"
    " renewed_at_ms,released,prev_hash,record_hash FROM leader_leases"
)


def _record_hash(row: dict, *, prev_hash: str) -> str:
    content = {name: row[name] for name in _CONTENT_COLUMNS}
    content["seq"] = row["seq"]
    return stable_hash({"content": content, "prev_hash": prev_hash})


@dataclass(frozen=True)
class LeaderLease:
    """A fenced ownership claim over a resource (proof = owner_id + fencing_token)."""

    resource: str
    owner_id: str
    fencing_token: int
    acquired_at_ms: int
    expires_at_ms: int


class SqliteLeaderLeaseStore:
    """Single-node durable leader-lease store with fencing and a re-chained hash chain."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        if not isinstance(path, str) or not path:
            raise LeaderLeaseError("leader-lease store path must be a non-empty string")
        if not create and (path == ":memory:" or not os.path.isfile(path)):
            raise LeaderLeaseError(f"leader-lease store unavailable: {path}")
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
    def _require_identity(value: object, *, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value

    @staticmethod
    def _require_now(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("now_ms must be a non-negative integer")
        return value

    @staticmethod
    def _require_lease_ms(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("lease_ms must be a positive integer")
        return value

    # ------------------------------------------------------------------ 内部

    def _rows_locked(self) -> list[sqlite3.Row]:
        try:
            return self._conn.execute(f"{_SELECT} ORDER BY seq").fetchall()
        except sqlite3.DatabaseError as exc:
            raise LeaderLeaseError(f"leader-lease store unreadable: {exc}") from exc

    @staticmethod
    def _row_dict(row: sqlite3.Row) -> dict:
        return {name: row[name] for name in row.keys()}

    @staticmethod
    def _lease_from_row(row: sqlite3.Row) -> LeaderLease:
        return LeaderLease(
            resource=row["resource"],
            owner_id=row["owner_id"],
            fencing_token=int(row["fencing_token"]),
            acquired_at_ms=int(row["acquired_at_ms"]),
            expires_at_ms=int(row["expires_at_ms"]),
        )

    def _verify_locked(self) -> str:
        """全链校验,返回链头;任何一环不符即 LeaderLeaseError(fail-closed)。"""
        previous = ""
        for row in self._rows_locked():
            data = self._row_dict(row)
            if row["prev_hash"] != previous:
                raise LeaderLeaseError(
                    f"leader-lease row seq={row['seq']} previous hash mismatch"
                )
            expected = _record_hash(data, prev_hash=previous)
            if row["record_hash"] != expected:
                raise LeaderLeaseError(
                    f"leader-lease row seq={row['seq']} record hash mismatch"
                )
            previous = expected
        return previous

    def _rechain_locked(self, *, from_seq: int) -> None:
        """行迁移后重链:从 from_seq 起按序级联重算 prev_hash/record_hash。

        与 command log 同一裁定:UPDATE 被允许(租约是可变性 CAS 状态),篡改
        可见性由重链保证;O(n) 级联是刻意开销,租约数量极小。
        """
        head_row = self._conn.execute(
            "SELECT record_hash FROM leader_leases WHERE seq<? ORDER BY seq DESC LIMIT 1",
            (from_seq,),
        ).fetchone()
        previous = head_row[0] if head_row is not None else ""
        rows = self._conn.execute(
            f"{_SELECT} WHERE seq>=? ORDER BY seq", (from_seq,)
        ).fetchall()
        for row in rows:
            data = self._row_dict(row)
            digest = _record_hash(data, prev_hash=previous)
            self._conn.execute(
                "UPDATE leader_leases SET prev_hash=?, record_hash=? WHERE seq=?",
                (previous, digest, row["seq"]),
            )
            previous = digest

    def _get_row_locked(self, resource: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            f"{_SELECT} WHERE resource=?", (resource,)
        ).fetchone()

    def _rewrite_row_locked(
        self,
        row: sqlite3.Row,
        *,
        owner_id: str,
        fencing_token: int,
        acquired_at_ms: int,
        expires_at_ms: int,
        renewed_at_ms: int,
        released: int,
    ) -> None:
        self._conn.execute(
            "UPDATE leader_leases SET owner_id=?, fencing_token=?, acquired_at_ms=?,"
            " expires_at_ms=?, renewed_at_ms=?, released=? WHERE resource=?",
            (
                owner_id,
                fencing_token,
                acquired_at_ms,
                expires_at_ms,
                renewed_at_ms,
                released,
                row["resource"],
            ),
        )
        self._rechain_locked(from_seq=int(row["seq"]))

    # ------------------------------------------------------------------ API

    def acquire(
        self, resource: str, *, owner_id: str, now_ms: int, lease_ms: int
    ) -> LeaderLease:
        """获得租约。空闲(无行/已释放/已过期)→ token+1 易主;同 owner 持有中 →
        幂等返回当前 lease(**不延期**,延期必须走 renew);异 owner 持有中 → Busy。"""
        resource = self._require_identity(resource, name="resource")
        owner_id = self._require_identity(owner_id, name="owner_id")
        now_ms = self._require_now(now_ms)
        lease_ms = self._require_lease_ms(lease_ms)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                head = self._verify_locked()
                row = self._get_row_locked(resource)
                if row is None:
                    seq = int(
                        self._conn.execute(
                            "SELECT COALESCE(MAX(seq),0)+1 FROM leader_leases"
                        ).fetchone()[0]
                    )
                    content = {
                        "seq": seq,
                        "resource": resource,
                        "owner_id": owner_id,
                        "fencing_token": 1,
                        "acquired_at_ms": now_ms,
                        "expires_at_ms": now_ms + lease_ms,
                        "renewed_at_ms": now_ms,
                        "released": 0,
                    }
                    digest = _record_hash(content, prev_hash=head)
                    self._conn.execute(
                        "INSERT INTO leader_leases"
                        " (seq,resource,owner_id,fencing_token,acquired_at_ms,"
                        " expires_at_ms,renewed_at_ms,released,prev_hash,record_hash)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            seq,
                            resource,
                            owner_id,
                            1,
                            now_ms,
                            now_ms + lease_ms,
                            now_ms,
                            0,
                            head,
                            digest,
                        ),
                    )
                elif row["released"] or now_ms >= row["expires_at_ms"]:
                    # 空闲易主:token 单调递增,绝不归零。
                    self._rewrite_row_locked(
                        row,
                        owner_id=owner_id,
                        fencing_token=int(row["fencing_token"]) + 1,
                        acquired_at_ms=now_ms,
                        expires_at_ms=now_ms + lease_ms,
                        renewed_at_ms=now_ms,
                        released=0,
                    )
                else:
                    if row["owner_id"] != owner_id:
                        raise LeaderLeaseBusyError(
                            f"resource {resource} is held by {row['owner_id']} "
                            f"until {row['expires_at_ms']}"
                        )
                    # 同 owner 持有中:幂等返回,不延期、token 不变。
                    self._conn.rollback()
                    return self._lease_from_row(row)
                self._conn.commit()
                return self._lease_from_row(self._get_row_locked(resource))  # type: ignore[arg-type]
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def renew(
        self, lease: LeaderLease, *, now_ms: int, lease_ms: int
    ) -> LeaderLease:
        """续期:仅当前 owner + token 匹配 + 未过期;否则 LeaderLeaseFencedError。"""
        if not isinstance(lease, LeaderLease):
            raise TypeError("lease must be a LeaderLease")
        now_ms = self._require_now(now_ms)
        lease_ms = self._require_lease_ms(lease_ms)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._verify_locked()
                row = self._require_fenced_row_locked(lease, now_ms=now_ms)
                self._rewrite_row_locked(
                    row,
                    owner_id=lease.owner_id,
                    fencing_token=lease.fencing_token,
                    acquired_at_ms=int(row["acquired_at_ms"]),
                    expires_at_ms=now_ms + lease_ms,
                    renewed_at_ms=now_ms,
                    released=0,
                )
                self._conn.commit()
                return self._lease_from_row(self._get_row_locked(lease.resource))  # type: ignore[arg-type]
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def release(self, lease: LeaderLease, *, now_ms: int) -> None:
        """释放:仅当前 owner + token 匹配;同 owner 同 token 重复释放幂等 no-op。
        行不删,released=1,token 保留(不归零)。"""
        if not isinstance(lease, LeaderLease):
            raise TypeError("lease must be a LeaderLease")
        self._require_now(now_ms)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._verify_locked()
                row = self._get_row_locked(lease.resource)
                if (
                    row is None
                    or row["owner_id"] != lease.owner_id
                    or int(row["fencing_token"]) != lease.fencing_token
                ):
                    raise LeaderLeaseFencedError(
                        f"release rejected: not the fenced owner of {lease.resource}"
                    )
                if row["released"]:
                    # 幂等 no-op:同 owner 同 token 重复释放。
                    self._conn.rollback()
                    return
                self._rewrite_row_locked(
                    row,
                    owner_id=lease.owner_id,
                    fencing_token=lease.fencing_token,
                    acquired_at_ms=int(row["acquired_at_ms"]),
                    expires_at_ms=int(row["expires_at_ms"]),
                    renewed_at_ms=int(row["renewed_at_ms"]),
                    released=1,
                )
                self._conn.commit()
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def require_owner(
        self, resource: str, *, owner_id: str, fencing_token: int, now_ms: int
    ) -> None:
        """副作用前的 fencing 闸门:任何失配(无 lease/易主/token 旧/已过期/已释放)
        一律 LeaderLeaseFencedError——stale leader 写不进。"""
        resource = self._require_identity(resource, name="resource")
        owner_id = self._require_identity(owner_id, name="owner_id")
        if (
            isinstance(fencing_token, bool)
            or not isinstance(fencing_token, int)
            or fencing_token < 1
        ):
            raise ValueError("fencing_token must be a positive integer")
        now_ms = self._require_now(now_ms)
        with self._lock:
            self._verify_locked()
            row = self._get_row_locked(resource)
            if (
                row is None
                or row["released"]
                or row["owner_id"] != owner_id
                or int(row["fencing_token"]) != fencing_token
                or now_ms >= row["expires_at_ms"]
            ):
                raise LeaderLeaseFencedError(
                    f"fencing check failed for {resource}: "
                    f"owner={owner_id} token={fencing_token}"
                )

    def current(self, resource: str, *, now_ms: int) -> Optional[LeaderLease]:
        """当前有效租约(未过期且未释放),否则 None。"""
        resource = self._require_identity(resource, name="resource")
        now_ms = self._require_now(now_ms)
        with self._lock:
            self._verify_locked()
            row = self._get_row_locked(resource)
            if row is None or row["released"] or now_ms >= row["expires_at_ms"]:
                return None
            return self._lease_from_row(row)

    def active_leases(self, *, now_ms: int) -> tuple[LeaderLease, ...]:
        """所有未释放且未过期的租约,按 resource 字典序(recovery 层审计面)。"""
        now_ms = self._require_now(now_ms)
        with self._lock:
            self._verify_locked()
            rows = self._conn.execute(
                f"{_SELECT} WHERE released=0 AND expires_at_ms>? ORDER BY resource",
                (now_ms,),
            ).fetchall()
            return tuple(self._lease_from_row(row) for row in rows)

    def _require_fenced_row_locked(
        self, lease: LeaderLease, *, now_ms: int
    ) -> sqlite3.Row:
        row = self._get_row_locked(lease.resource)
        if (
            row is None
            or row["released"]
            or row["owner_id"] != lease.owner_id
            or int(row["fencing_token"]) != lease.fencing_token
            or now_ms >= row["expires_at_ms"]
        ):
            raise LeaderLeaseFencedError(
                f"operation rejected: not the live fenced owner of {lease.resource}"
            )
        return row

    def chain_valid(self) -> bool:
        with self._lock:
            try:
                self._verify_locked()
                return True
            except (LeaderLeaseError, sqlite3.DatabaseError):
                return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = [
    "LeaderLease",
    "LeaderLeaseBusyError",
    "LeaderLeaseError",
    "LeaderLeaseFencedError",
    "SqliteLeaderLeaseStore",
]
