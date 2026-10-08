"""SHADOW 观测的 append-only 持久化 + 第七(drain 第 8 源)transactional outbox
(727 §12.2,Phase 7 S2,2026-07-27)。设计:
docs/engineering/specs/2026-07-27-shadow-phase7-design.md §3 S2。

结构克隆 `services/data/refresh_history.py`(最新、最完整的 owner store outbox 先例):
观测行与它的 canonical 事件在**同一 SQLite 事务**提交;append-only 触发器禁改;
观测链(payload_sha256 + prev_hash/record_hash)与 outbox/dispatch 双哈希链在打开时
(create=False)与每次 append 前全量校验——篡改即拒开、拒续写。

**包位置裁定(诚实记录)**:本 store 消费 `live/execution/shadow.py::ShadowObservation`,
而 `services/` 永不 import `live/`(治理钉死),故 store 放 `live/execution/`(live 侧
store 先例:position_ledger.py / kill_switch authority 也在各自域内);dispatch 函数
(`services/monitoring/domain_event_runtime.py::dispatch_shadow_event_outbox`)采用与
既有六个**完全相同**的依赖注入签名(store 实例传入)——细读六个 dispatch 后确认它们
本就全部依赖注入、无一 import owner store 模块,故无偏差可登记。

**事件 payload 是脱敏摘要**:只含观测的标量结论(observation_id/run_label/instrument/
side/quantity/decision_price/executable_vwap/slippage_bps_vs_decision/book_age_ms/
as_of_ms/provenance)——**不含盘口簿、不含 walk 明细**(完整观测在 store 行
payload_json 里,事件只是它的指针 + 结论摘要,与 refresh 事件不含源错误详情同款
纪律)。时间戳:新事件类型遵守现行约定,`timestamp` 为 Unix epoch **微秒**
(`event_timestamp_from_milliseconds(recorded_at_ms)`,catalog 钉死 latest 一律 µs)。

幂等语义:`observation_id` 内容派生("shd_"+payload_sha256[:16])——同内容观测重复
append(同 id 同 payload)返回 (id, False) 不产生新行/新事件;同 id 不同 payload
(按构造不可达,除非哈希碰撞或伪造 id)→ `ShadowStoreError` 大声抛出,绝不静默。
`run_label` 是批次元数据,**不进身份哈希**——同一观测在不同批次标签下重放仍是同一
观测(与 PaperRealtimeStore 剔除 generated_at 的同款裁定:发布/归类行为不是观测身份)。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import List, Optional, Tuple

from live.execution.shadow import PROVENANCE_SHADOW, ShadowObservation
from quantos.contracts.event_schemas import CANONICAL_EVENT_SCHEMAS
from quantos.contracts.events import (EVENT_SHADOW_OBSERVED, Event, EventOutboxMessage,
                                      event_timestamp_from_milliseconds)

SCHEMA_VERSION = 1
_GENESIS = ""

_REQUIRED_TABLES = frozenset({
    "shadow_observations",
    "shadow_event_outbox",
    "shadow_event_dispatch",
})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_observations (
  obs_seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  observation_id   TEXT NOT NULL UNIQUE,
  run_label        TEXT NOT NULL,
  payload_json     TEXT NOT NULL,
  payload_sha256   TEXT NOT NULL,
  provenance       TEXT NOT NULL CHECK (provenance = 'SHADOW'),
  as_of_ms         INTEGER,
  recorded_at_ms   INTEGER NOT NULL CHECK (recorded_at_ms >= 0),
  prev_hash        TEXT NOT NULL,
  record_hash      TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS shadow_event_outbox (
  outbox_seq       INTEGER PRIMARY KEY AUTOINCREMENT,
  obs_seq          INTEGER NOT NULL UNIQUE,
  event_id         TEXT NOT NULL UNIQUE,
  event_json       TEXT NOT NULL,
  correlation_id   TEXT NOT NULL,
  causation_id     TEXT NOT NULL,
  event_repro_hash TEXT NOT NULL,
  prev_record_hash TEXT NOT NULL,
  record_hash      TEXT NOT NULL UNIQUE,
  FOREIGN KEY (obs_seq) REFERENCES shadow_observations(obs_seq)
);
CREATE TABLE IF NOT EXISTS shadow_event_dispatch (
  dispatch_seq     INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id         TEXT NOT NULL UNIQUE,
  dispatched_at    INTEGER NOT NULL CHECK (dispatched_at >= 0),
  event_repro_hash TEXT NOT NULL,
  prev_record_hash TEXT NOT NULL,
  record_hash      TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS shadow_observations_no_update
BEFORE UPDATE ON shadow_observations
BEGIN SELECT RAISE(ABORT, 'shadow_observations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS shadow_observations_no_delete
BEFORE DELETE ON shadow_observations
BEGIN SELECT RAISE(ABORT, 'shadow_observations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS shadow_event_outbox_no_update
BEFORE UPDATE ON shadow_event_outbox
BEGIN SELECT RAISE(ABORT, 'shadow_event_outbox is append-only'); END;
CREATE TRIGGER IF NOT EXISTS shadow_event_outbox_no_delete
BEFORE DELETE ON shadow_event_outbox
BEGIN SELECT RAISE(ABORT, 'shadow_event_outbox is append-only'); END;
CREATE TRIGGER IF NOT EXISTS shadow_event_dispatch_no_update
BEFORE UPDATE ON shadow_event_dispatch
BEGIN SELECT RAISE(ABORT, 'shadow_event_dispatch is append-only'); END;
CREATE TRIGGER IF NOT EXISTS shadow_event_dispatch_no_delete
BEFORE DELETE ON shadow_event_dispatch
BEGIN SELECT RAISE(ABORT, 'shadow_event_dispatch is append-only'); END;
"""


class ShadowStoreError(RuntimeError):
    """The shadow observation store is missing, corrupt, or conflicting."""


def _canonical_json(value) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ShadowStoreError("shadow payload is not canonical JSON") from exc


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_existing_shadow_schema(db_path: str) -> None:
    """create=False 的前置只读检查(ro&immutable,绝不产生 -wal/-shm 副作用;
    与 refresh_history 同款)。"""
    if db_path == ":memory:":
        raise ShadowStoreError("in-memory shadow store cannot be reopened")
    try:
        uri = f"{Path(db_path).resolve().as_uri()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        try:
            tables = {str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ShadowStoreError("shadow observation store is unavailable") from exc
    missing = sorted(_REQUIRED_TABLES - tables)
    if missing:
        raise ShadowStoreError(
            f"shadow observation store schema is incomplete; missing tables: {missing}")


def observation_payload(obs: ShadowObservation) -> dict:
    """ShadowObservation 的全字段 canonical 映射(store payload_json 的内容;
    事件 payload 是它的脱敏子集,见 _event_payload)。"""
    if not isinstance(obs, ShadowObservation):
        raise ShadowStoreError(
            f"observation must be a ShadowObservation, got {type(obs).__name__}")
    return {
        "instrument_id": obs.instrument_id,
        "side": obs.side,
        "quantity": obs.quantity,
        "decision_price": obs.decision_price,
        "paper_fill_price": obs.paper_fill_price,
        "book_mid": obs.book_mid,
        "book_spread_bps": obs.book_spread_bps,
        "executable_vwap": obs.executable_vwap,
        "slippage_bps_vs_decision": obs.slippage_bps_vs_decision,
        "walk": {
            "instrument_id": obs.walk.instrument_id,
            "executable": obs.walk.executable,
            "vwap": obs.walk.vwap,
            "filled_quantity": obs.walk.filled_quantity,
            "unfilled_quantity": obs.walk.unfilled_quantity,
            "levels_consumed": obs.walk.levels_consumed,
            "reason": obs.walk.reason,
        },
        "expected_impact_bps": obs.expected_impact_bps,
        "latency_ms": obs.latency_ms,
        "book_age_ms": obs.book_age_ms,
        "as_of_ms": obs.as_of_ms,
        "provenance": obs.provenance,
    }


def _event_payload(*, observation_id: str, run_label: str, payload: dict) -> dict:
    """事件的脱敏摘要(727 §12.2 结论字段;不含盘口簿/walk 明细)。"""
    return {
        "observation_id": observation_id,
        "run_label": run_label,
        "instrument_id": payload["instrument_id"],
        "side": payload["side"],
        "quantity": payload["quantity"],
        "decision_price": payload["decision_price"],
        "executable_vwap": payload["executable_vwap"],
        "slippage_bps_vs_decision": payload["slippage_bps_vs_decision"],
        "book_age_ms": payload["book_age_ms"],
        "as_of_ms": payload["as_of_ms"],
        "provenance": payload["provenance"],
    }


def shadow_observed_event(*, observation_id: str, run_label: str, payload: dict,
                          record_hash: str, recorded_at_ms: int) -> Event:
    """一条落账观测的 canonical 事件(脱敏摘要;timestamp = µs,新类型现行约定)。"""
    digest = hashlib.sha256(
        f"{observation_id}|{record_hash}|{int(recorded_at_ms)}".encode("utf-8")
    ).hexdigest()
    return Event(
        event_id=f"shd-{digest[:32]}",
        event_type=EVENT_SHADOW_OBSERVED,
        event_version="1.0.0",
        timestamp=event_timestamp_from_milliseconds(recorded_at_ms),
        source="live.execution.shadow_store",
        domain="execution",
        criticality="C2",
        payload=_event_payload(observation_id=observation_id, run_label=run_label,
                               payload=payload),
    )


def _record_hash(*, observation_id: str, run_label: str, payload_sha256: str,
                 as_of_ms: Optional[int], recorded_at_ms: int, prev_hash: str) -> str:
    return _sha256_text(_canonical_json({
        "schema_version": SCHEMA_VERSION,
        "observation_id": observation_id,
        "run_label": run_label,
        "payload_sha256": payload_sha256,
        "as_of_ms": as_of_ms,
        "recorded_at_ms": int(recorded_at_ms),
        "prev_hash": prev_hash,
    }))


def _outbox_hash(*, obs_seq: int, message: EventOutboxMessage,
                 prev_record_hash: str) -> str:
    return _sha256_text(_canonical_json({
        "causation_id": message.causation_id,
        "correlation_id": message.correlation_id,
        "event": message.event.model_dump(mode="json"),
        "obs_seq": obs_seq,
        "prev_record_hash": prev_record_hash,
    }))


def _dispatch_hash(*, event_id: str, dispatched_at: int, event_repro_hash: str,
                   prev_record_hash: str) -> str:
    return _sha256_text(_canonical_json({
        "dispatched_at": dispatched_at,
        "event_id": event_id,
        "event_repro_hash": event_repro_hash,
        "prev_record_hash": prev_record_hash,
    }))


class SqliteShadowStore:
    """Append-only、哈希链防篡改的 SHADOW 观测账本(时间全部调用方 int ms 注入)。"""

    def __init__(self, db_path: str, *, create: bool = False) -> None:
        if create and db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        if not create:
            _require_existing_shadow_schema(db_path)
        self.db_path = db_path
        self._connection = sqlite3.connect(db_path, check_same_thread=False)
        try:
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA busy_timeout=5000")
            if db_path != ":memory:":
                self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._lock = threading.Lock()
            if create:
                self._connection.executescript(_SCHEMA)
                self._connection.commit()
            else:
                with self._lock:
                    self._verify_chains_unlocked()
        except BaseException:
            self._connection.close()
            raise

    def close(self) -> None:
        self._connection.close()

    # --- 读取 ---

    def _observation_rows(self):
        return self._connection.execute(
            "SELECT obs_seq,observation_id,run_label,payload_json,payload_sha256,"
            " provenance,as_of_ms,recorded_at_ms,prev_hash,record_hash"
            " FROM shadow_observations ORDER BY obs_seq"
        ).fetchall()

    def _outbox_rows(self):
        return self._connection.execute(
            "SELECT outbox_seq,obs_seq,event_id,event_json,correlation_id,"
            " causation_id,event_repro_hash,prev_record_hash,record_hash"
            " FROM shadow_event_outbox ORDER BY outbox_seq"
        ).fetchall()

    def _dispatch_rows(self):
        return self._connection.execute(
            "SELECT dispatch_seq,event_id,dispatched_at,event_repro_hash,"
            " prev_record_hash,record_hash FROM shadow_event_dispatch"
            " ORDER BY dispatch_seq"
        ).fetchall()

    # --- 链校验 ---

    def _observation_chain_valid_unlocked(self) -> bool:
        prev_hash = _GENESIS
        for row in self._observation_rows():
            (obs_seq, observation_id, run_label, payload_json, payload_sha256,
             provenance, as_of_ms, recorded_at_ms, stored_prev, stored_record) = row
            if stored_prev != prev_hash:
                return False
            if _sha256_text(payload_json) != payload_sha256:
                return False
            if provenance != PROVENANCE_SHADOW:
                return False
            try:
                payload = json.loads(payload_json)
                if _canonical_json(payload) != payload_json:
                    return False
            except (TypeError, ValueError, json.JSONDecodeError):
                return False
            if observation_id != f"shd_{payload_sha256[:16]}":
                return False
            if stored_record != _record_hash(
                    observation_id=observation_id, run_label=run_label,
                    payload_sha256=payload_sha256,
                    as_of_ms=None if as_of_ms is None else int(as_of_ms),
                    recorded_at_ms=int(recorded_at_ms), prev_hash=stored_prev):
                return False
            prev_hash = stored_record
        return True

    @staticmethod
    def _message_from_outbox_row(row) -> EventOutboxMessage:
        try:
            event = Event.model_validate_json(row[3])
        except Exception as exc:
            raise ShadowStoreError(
                f"shadow outbox row {row[0]} cannot be reconstructed") from exc
        if event.reproducibility_hash != row[6]:
            raise ShadowStoreError(
                f"shadow outbox row {row[0]} payload hash mismatch")
        return EventOutboxMessage(event=event, correlation_id=row[4],
                                  causation_id=row[5])

    @staticmethod
    def _expected_message_for_observation_row(row) -> EventOutboxMessage:
        (_obs_seq, observation_id, run_label, payload_json, _payload_sha256,
         _provenance, _as_of_ms, recorded_at_ms, _prev_hash, record_hash) = row
        payload = json.loads(payload_json)
        event = shadow_observed_event(observation_id=observation_id,
                                      run_label=run_label, payload=payload,
                                      record_hash=record_hash,
                                      recorded_at_ms=int(recorded_at_ms))
        return EventOutboxMessage(event=event, correlation_id=run_label)

    def _verify_event_outbox_unlocked(self) -> Tuple[str, dict, str]:
        if not self._observation_chain_valid_unlocked():
            raise ShadowStoreError("shadow observations failed hash-chain verification")
        observations_by_seq = {int(row[0]): row for row in self._observation_rows()}
        outbox_rows = self._outbox_rows()
        if len(outbox_rows) != len(observations_by_seq):
            raise ShadowStoreError(
                "shadow observations and event outbox cardinality mismatch")
        previous = _GENESIS
        messages: dict = {}
        for row in outbox_rows:
            observation_row = observations_by_seq.get(int(row[1]))
            if observation_row is None:
                raise ShadowStoreError(
                    f"shadow outbox row {row[0]} references missing observation")
            message = self._message_from_outbox_row(row)
            expected = self._expected_message_for_observation_row(observation_row)
            try:
                CANONICAL_EVENT_SCHEMAS.validate(message.event)
            except Exception as exc:
                raise ShadowStoreError(
                    f"shadow outbox row {row[0]} schema is invalid") from exc
            if (message != expected or row[2] != message.event.event_id
                    or message.event.event_type != EVENT_SHADOW_OBSERVED):
                raise ShadowStoreError(
                    f"shadow outbox row {row[0]} does not match observation")
            if row[7] != previous:
                raise ShadowStoreError(
                    f"shadow outbox row {row[0]} previous hash mismatch")
            digest = _outbox_hash(obs_seq=int(row[1]), message=message,
                                  prev_record_hash=previous)
            if row[8] != digest:
                raise ShadowStoreError(
                    f"shadow outbox row {row[0]} record hash mismatch")
            if message.outbox_id in messages:
                raise ShadowStoreError(
                    f"duplicate shadow event id: {message.outbox_id}")
            messages[message.outbox_id] = message
            previous = digest

        dispatch_previous = _GENESIS
        for row in self._dispatch_rows():
            message = messages.get(row[1])
            if message is None or message.event.reproducibility_hash != row[3]:
                raise ShadowStoreError(
                    f"shadow dispatch row {row[0]} reference mismatch")
            if row[4] != dispatch_previous:
                raise ShadowStoreError(
                    f"shadow dispatch row {row[0]} previous hash mismatch")
            digest = _dispatch_hash(event_id=row[1], dispatched_at=int(row[2]),
                                    event_repro_hash=row[3],
                                    prev_record_hash=dispatch_previous)
            if row[5] != digest:
                raise ShadowStoreError(
                    f"shadow dispatch row {row[0]} record hash mismatch")
            dispatch_previous = digest
        return previous, messages, dispatch_previous

    def _verify_chains_unlocked(self) -> None:
        if not self._observation_chain_valid_unlocked():
            raise ShadowStoreError("shadow observations failed hash-chain verification")
        self._verify_event_outbox_unlocked()

    def chain_valid(self) -> bool:
        """观测链(payload 哈希 + prev/record 链)是否自洽。"""
        with self._lock:
            try:
                return self._observation_chain_valid_unlocked()
            except (ShadowStoreError, sqlite3.DatabaseError, TypeError, ValueError):
                return False

    def event_outbox_chain_valid(self) -> bool:
        """观测链 + outbox 链 + dispatch 链全量是否自洽(篡改任一环即 False)。"""
        with self._lock:
            try:
                self._verify_chains_unlocked()
                return True
            except (ShadowStoreError, sqlite3.DatabaseError, TypeError, ValueError):
                return False

    # --- 写入 ---

    def _find_payload_hash(self, observation_id: str) -> Optional[str]:
        row = self._connection.execute(
            "SELECT payload_sha256 FROM shadow_observations WHERE observation_id=?",
            (observation_id,),
        ).fetchone()
        return None if row is None else str(row[0])

    def append(self, obs: ShadowObservation, *, run_label: str,
               recorded_at_ms: int) -> Tuple[str, bool]:
        """追加一条观测 + 它的 canonical 事件(同一事务)。同 id 同 payload →
        (observation_id, False) 幂等;同 id 不同 payload → ShadowStoreError。"""
        payload = observation_payload(obs)
        if not isinstance(run_label, str) or not run_label.strip():
            raise ShadowStoreError("run_label must be non-empty")
        if (isinstance(recorded_at_ms, bool) or int(recorded_at_ms) != recorded_at_ms
                or int(recorded_at_ms) < 0):
            raise ShadowStoreError("recorded_at_ms must be a non-negative integer ms")
        recorded_at_ms = int(recorded_at_ms)
        payload_json = _canonical_json(payload)
        payload_sha256 = _sha256_text(payload_json)
        observation_id = f"shd_{payload_sha256[:16]}"

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._verify_chains_unlocked()          # 篡改存量即拒续写
                outbox_head, _, _ = self._verify_event_outbox_unlocked()
                existing = self._find_payload_hash(observation_id)
                if existing is not None:
                    if existing == payload_sha256:
                        self._connection.rollback()
                        return observation_id, False
                    raise ShadowStoreError(
                        "shadow observation identity reused with different content")
                row = self._connection.execute(
                    "SELECT record_hash FROM shadow_observations"
                    " ORDER BY obs_seq DESC LIMIT 1"
                ).fetchone()
                prev_hash = _GENESIS if row is None else str(row[0])
                record_hash = _record_hash(
                    observation_id=observation_id, run_label=run_label,
                    payload_sha256=payload_sha256, as_of_ms=obs.as_of_ms,
                    recorded_at_ms=recorded_at_ms, prev_hash=prev_hash)
                cursor = self._connection.execute(
                    "INSERT INTO shadow_observations"
                    " (observation_id,run_label,payload_json,payload_sha256,provenance,"
                    " as_of_ms,recorded_at_ms,prev_hash,record_hash)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (observation_id, run_label, payload_json, payload_sha256,
                     PROVENANCE_SHADOW, obs.as_of_ms, recorded_at_ms, prev_hash,
                     record_hash),
                )
                if cursor.lastrowid is None:
                    raise ShadowStoreError("shadow observation insert returned no sequence")
                obs_seq = int(cursor.lastrowid)
                message = EventOutboxMessage(
                    event=shadow_observed_event(
                        observation_id=observation_id, run_label=run_label,
                        payload=payload, record_hash=record_hash,
                        recorded_at_ms=recorded_at_ms),
                    correlation_id=run_label,
                )
                digest = _outbox_hash(obs_seq=obs_seq, message=message,
                                      prev_record_hash=outbox_head)
                self._connection.execute(
                    "INSERT INTO shadow_event_outbox"
                    " (obs_seq,event_id,event_json,correlation_id,causation_id,"
                    " event_repro_hash,prev_record_hash,record_hash)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (obs_seq, message.event.event_id,
                     _canonical_json(message.event.model_dump(mode="json")),
                     message.correlation_id, message.causation_id,
                     message.event.reproducibility_hash, outbox_head, digest),
                )
                self._connection.commit()
                return observation_id, True
            except BaseException:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise

    # --- outbox(drain/dispatch 接缝,签名与既有 owner store 逐位一致) ---

    def pending_event_outbox(self, *, limit: int = 1_000) -> tuple:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._lock:
            _, messages, _ = self._verify_event_outbox_unlocked()
            dispatched = {row[1] for row in self._dispatch_rows()}
            pending = []
            for event_id, message in messages.items():
                if event_id in dispatched:
                    continue
                pending.append({
                    "outbox_id": event_id,
                    "event": message.event,
                    "correlation_id": message.correlation_id,
                    "causation_id": message.causation_id,
                })
                if len(pending) == limit:
                    break
            return tuple(pending)

    def mark_event_dispatched(self, event_id: str, *, dispatched_at: int,
                              event_repro_hash: str) -> bool:
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event_id must be non-empty")
        if (isinstance(dispatched_at, bool) or not isinstance(dispatched_at, int)
                or dispatched_at < 0):
            raise ValueError("dispatched_at must be a non-negative integer")
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                _, messages, dispatch_head = self._verify_event_outbox_unlocked()
                message = messages.get(event_id)
                if (message is None
                        or message.event.reproducibility_hash != event_repro_hash):
                    raise ShadowStoreError("shadow dispatch reference mismatch")
                existing = self._connection.execute(
                    "SELECT dispatch_seq,event_id,dispatched_at,event_repro_hash,"
                    " prev_record_hash,record_hash FROM shadow_event_dispatch"
                    " WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                if existing is not None:
                    if (int(existing[2]) == dispatched_at
                            and existing[3] == event_repro_hash):
                        self._connection.rollback()
                        return False
                    raise ShadowStoreError("shadow dispatch acknowledgement conflict")
                digest = _dispatch_hash(event_id=event_id, dispatched_at=dispatched_at,
                                        event_repro_hash=event_repro_hash,
                                        prev_record_hash=dispatch_head)
                self._connection.execute(
                    "INSERT INTO shadow_event_dispatch"
                    " (event_id,dispatched_at,event_repro_hash,prev_record_hash,"
                    " record_hash) VALUES (?,?,?,?,?)",
                    (event_id, dispatched_at, event_repro_hash, dispatch_head, digest),
                )
                self._connection.commit()
                return True
            except BaseException:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise

    # --- 只读查询 ---

    def records(self, *, run_label: Optional[str] = None,
                since_seq: Optional[int] = None) -> List[dict]:
        """按 obs_seq 升序返回观测(payload dict + 身份/链字段)。run_label 过滤 /
        since_seq 增量(只返回 obs_seq > since_seq 的行)。"""
        with self._lock:
            out = []
            for row in self._observation_rows():
                if run_label is not None and row[2] != run_label:
                    continue
                if since_seq is not None and int(row[0]) <= int(since_seq):
                    continue
                out.append({
                    "obs_seq": int(row[0]),
                    "observation_id": row[1],
                    "run_label": row[2],
                    "payload": json.loads(row[3]),
                    "payload_sha256": row[4],
                    "provenance": row[5],
                    "as_of_ms": row[6],
                    "recorded_at_ms": int(row[7]),
                    "prev_hash": row[8],
                    "record_hash": row[9],
                })
            return out

    def count(self) -> int:
        with self._lock:
            return int(self._connection.execute(
                "SELECT COUNT(*) FROM shadow_observations").fetchone()[0])


__all__ = ["SCHEMA_VERSION", "ShadowStoreError", "SqliteShadowStore",
           "observation_payload", "shadow_observed_event"]
