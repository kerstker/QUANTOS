"""多方批准的 append-only sqlite 持久化(战线①,2026-07-06)—— L2_DRY_RUN_ONLY 一寸不让。

把 `approval.py` 的 ApprovalRequest 聚合落进 `data/cache/approvals.db`,使三方批准跨进程/跨会话
存活。语义与不变量:

- **write-only-adds**(遵守 memory「Concurrent state write races」教训):save() 先读已存步骤,
  传入聚合的步骤必须以已存序列为**严格前缀**(逐条 entry_hash 相等),只 INSERT 新增的尾部;
  任何分叉/缩短/换 subject → `ApprovalStoreConflict`,绝不覆写既有历史。
- **同事务链头承诺**:每次追加在同一事务里更新 `approval_requests.n_steps/head_hash`。裸哈希链
  抓不到"整体截断"(删掉尾部 REJECTED 步即可复活一个已否决请求)——承诺列抓它,与
  `DurableAuditLog.seal()` 同思路。
- **fail-closed 重载**:load() 逐条用 pydantic 重建 ApprovalStep 并重算 entry_hash 与存值比对
  (抓字段篡改),校验 step_index 从 0 连续无洞、prev_hash 链自洽(抓重排/跨请求拼接)、
  链头 == head_hash ∧ 条数 == n_steps(抓截断);任一不满足 raise `ApprovalStoreError`。
- **并发**:进程内 threading.Lock;跨进程靠 (request_id, step_index) 主键兜底——两个写者都通过
  前缀检查时,后到者的 INSERT 撞主键,sqlite IntegrityError 显式映射为 `ApprovalStoreConflict`
  (评审 #5),事务回滚、库不半写。

诚实边界(与 DurableAuditLog.seal 注脚一字对齐):能直接写 sqlite 文件的攻击者可以同时改
head_hash/n_steps 承诺列——单机文件写权限 = 单进程受信代码的信任级别,真防御是 Phase 14 出进程
签名。纵深:promotion_api 把每个 mutating 步骤同时落进 governance_audit.db 的哈希链,单库篡改
会与审计链对不上。

纯 stdlib(sqlite3/threading/os),店内无墙钟/无随机——时间由调用方注入。绝不 import 券商/网络 SDK。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Optional

from services.governance.approval import ApprovalRequest, ApprovalState, ApprovalStep, _hash_chain_ok


class ApprovalStoreError(RuntimeError):
    """存储缺失、损坏或被篡改 —— fail-closed,绝不读成"空的合法历史"。"""


class ApprovalStoreConflict(ApprovalStoreError):
    """并发写者 / 与已存历史分叉 —— 绝不覆写(write-only-adds)。"""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_requests (
  request_id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  kind       TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  n_steps    INTEGER NOT NULL DEFAULT 0,
  head_hash  TEXT    NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS approval_steps (
  request_id TEXT NOT NULL,
  step_index INTEGER NOT NULL,
  state      TEXT NOT NULL,
  actor_id   TEXT NOT NULL,
  timestamp  INTEGER NOT NULL,
  note       TEXT NOT NULL DEFAULT '',
  prev_hash  TEXT NOT NULL,
  entry_hash TEXT NOT NULL,
  PRIMARY KEY (request_id, step_index)
);
"""


class ApprovalStore:
    def __init__(self, db_path: str, create: bool = True):
        if create and db_path != ":memory:":
            parent = os.path.dirname(db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._c = sqlite3.connect(db_path, check_same_thread=False)
        self._c.isolation_level = None          # 显式事务(BEGIN IMMEDIATE / COMMIT / ROLLBACK)
        self._lock = threading.Lock()
        if create:
            with self._lock:
                self._c.executescript(_SCHEMA)

    # --- 内部读(save 的前缀检查用;单独方法便于测试注入陈旧读模拟跨进程竞态) ---

    def _existing_steps(self, request_id: str) -> list[tuple]:
        return self._c.execute(
            "SELECT step_index, state, actor_id, timestamp, note, prev_hash, entry_hash"
            " FROM approval_steps WHERE request_id=? ORDER BY step_index",
            (request_id,)).fetchall()

    def _header(self, request_id: str) -> Optional[tuple]:
        return self._c.execute(
            "SELECT subject_id, kind, created_at, n_steps, head_hash"
            " FROM approval_requests WHERE request_id=?", (request_id,)).fetchone()

    # --- 写:write-only-adds ---

    def save(self, req: ApprovalRequest, *, now: int, kind: str = "") -> None:
        """追加式落库。req.steps 必须以库内已存步骤为严格前缀(逐条 entry_hash 相等),
        否则 ApprovalStoreConflict;只插新增尾部,同一事务更新链头承诺。"""
        if not req.steps:
            raise ApprovalStoreError("refusing to save a request with no steps")
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE")
                existing = self._existing_steps(req.request_id)
                header = self._header(req.request_id)
                if header is not None and header[0] != req.subject_id:
                    raise ApprovalStoreConflict(
                        f"subject mismatch for {req.request_id!r}: stored {header[0]!r}")
                if len(req.steps) < len(existing):
                    raise ApprovalStoreConflict(
                        f"{req.request_id!r}: incoming history shorter than stored"
                        f" ({len(req.steps)} < {len(existing)})")
                for i, row in enumerate(existing):
                    if req.steps[i].entry_hash != row[6]:
                        raise ApprovalStoreConflict(
                            f"{req.request_id!r}: step {i} diverges from stored history")
                for i in range(len(existing), len(req.steps)):
                    s = req.steps[i]
                    self._c.execute(
                        "INSERT INTO approval_steps (request_id, step_index, state, actor_id,"
                        " timestamp, note, prev_hash, entry_hash) VALUES (?,?,?,?,?,?,?,?)",
                        (req.request_id, i, s.state.value, s.actor_id, s.timestamp, s.note,
                         s.prev_hash, s.entry_hash))
                if header is None:
                    self._c.execute(
                        "INSERT INTO approval_requests (request_id, subject_id, kind, created_at,"
                        " n_steps, head_hash) VALUES (?,?,?,?,?,?)",
                        (req.request_id, req.subject_id, kind, now,
                         len(req.steps), req.head_hash))
                else:
                    self._c.execute(
                        "UPDATE approval_requests SET n_steps=?, head_hash=? WHERE request_id=?",
                        (len(req.steps), req.head_hash, req.request_id))
                self._c.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                self._c.execute("ROLLBACK")
                raise ApprovalStoreConflict(
                    f"{req.request_id!r}: concurrent writer won the append race: {exc}") from exc
            except ApprovalStoreError:
                self._c.execute("ROLLBACK")
                raise
            except Exception:
                self._c.execute("ROLLBACK")
                raise

    # --- 读:fail-closed 重载校验 ---

    def load(self, request_id: str) -> ApprovalRequest:
        with self._lock:
            header = self._header(request_id)
            rows = self._existing_steps(request_id)
        if header is None:
            raise ApprovalStoreError(f"unknown approval request: {request_id!r}")
        subject_id, _kind, _created, n_steps, head_hash = header
        steps: list[ApprovalStep] = []
        prev = ""
        for i, row in enumerate(rows):
            step_index, state, actor_id, timestamp, note, prev_hash, entry_hash = row
            if step_index != i:
                raise ApprovalStoreError(
                    f"{request_id!r}: step indexes not contiguous at position {i} (got {step_index})")
            try:
                step = ApprovalStep(state=ApprovalState(state), actor_id=actor_id,
                                    timestamp=timestamp, note=note, prev_hash=prev_hash)
            except Exception as exc:
                raise ApprovalStoreError(f"{request_id!r}: corrupt step {i}: {exc}") from exc
            if step.entry_hash != entry_hash:
                raise ApprovalStoreError(f"{request_id!r}: step {i} field tamper (hash mismatch)")
            if prev_hash != prev:
                raise ApprovalStoreError(f"{request_id!r}: step {i} breaks the prev_hash chain")
            prev = step.entry_hash
            steps.append(step)
        if len(steps) != n_steps:
            raise ApprovalStoreError(
                f"{request_id!r}: step count {len(steps)} != committed n_steps {n_steps} (truncation?)")
        if (steps[-1].entry_hash if steps else "") != head_hash:
            raise ApprovalStoreError(f"{request_id!r}: head hash does not match commitment")
        try:
            req = ApprovalRequest(request_id=request_id, subject_id=subject_id, steps=tuple(steps))
        except Exception as exc:
            raise ApprovalStoreError(f"{request_id!r}: corrupt aggregate: {exc}") from exc
        if not _hash_chain_ok(req):        # 双保险:再过一遍 approval.py 的链语义
            raise ApprovalStoreError(f"{request_id!r}: hash chain invalid after reload")
        return req

    # --- 摘要查询(给 API 列表用;不做链校验,详情/消费路径必须走 load) ---

    def _summary_row(self, request_id: str, header: tuple) -> dict:
        subject_id, kind, created_at, n_steps, _head = header
        last = self._c.execute(
            "SELECT state, actor_id, timestamp FROM approval_steps WHERE request_id=?"
            " ORDER BY step_index DESC LIMIT 1", (request_id,)).fetchone()
        actors = [r[0] for r in self._c.execute(
            "SELECT actor_id FROM approval_steps WHERE request_id=? ORDER BY step_index",
            (request_id,)).fetchall()]
        return {
            "request_id": request_id, "subject_id": subject_id, "kind": kind,
            "created_at": created_at, "n_steps": n_steps,
            "state": last[0] if last else "REQUESTED",
            "last_actor": last[1] if last else "", "last_ts": last[2] if last else 0,
            "actors": actors,
        }

    def list_requests(self, *, kind: Optional[str] = None,
                      states: Optional[tuple] = None) -> list[dict]:
        with self._lock:
            if kind is None:
                rows = self._c.execute(
                    "SELECT request_id, subject_id, kind, created_at, n_steps, head_hash"
                    " FROM approval_requests ORDER BY created_at, request_id").fetchall()
            else:
                rows = self._c.execute(
                    "SELECT request_id, subject_id, kind, created_at, n_steps, head_hash"
                    " FROM approval_requests WHERE kind=? ORDER BY created_at, request_id",
                    (kind,)).fetchall()
            out = [self._summary_row(r[0], r[1:]) for r in rows]
        if states is not None:
            allowed = set(states)
            out = [r for r in out if r["state"] in allowed]
        return out

    def get_summary(self, request_id: str) -> Optional[dict]:
        with self._lock:
            header = self._header(request_id)
            if header is None:
                return None
            return self._summary_row(request_id, header)

    def close(self) -> None:
        with self._lock:
            self._c.close()
