"""干跑流水线运行记录的 append-only sqlite 持久层(沿用 paper_track/experiment_registry 模式)。

一次 DryRunRun = 一次端到端干跑的可复现摘要(意图/批准/拒绝原因/PnL/审计根)。纯 stdlib、确定性、fail-closed。
不碰真钱/真券商/真凭证——只记录干跑结果。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field

from quantos.contracts.events import (
    EVENT_DRY_RUN_COMPLETED,
    EVENT_RESEARCH_RISK_EVALUATED,
    Event,
    event_timestamp_from_milliseconds,
)
from services.execution.simulated_tca import verify_route_tca_record


class DryRunStoreError(RuntimeError):
    """库不可读 / schema 缺失时抛出(fail-closed,绝不静默)。"""


@dataclass(frozen=True)
class DryRunOutboxEvent:
    run_id: str
    event: Event
    correlation_id: str = ""
    causation_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("outbox run_id must be non-empty")
        if not isinstance(self.event, Event):
            raise TypeError("outbox event must be a canonical Event")
        if not isinstance(self.correlation_id, str) or not isinstance(
            self.causation_id, str
        ):
            raise TypeError("outbox causal identifiers must be strings")

    @property
    def outbox_id(self) -> str:
        return self.event.event_id


@dataclass(frozen=True)
class DryRunRun:
    run_id: str
    strategy_id: str
    timestamp: int
    n_intents: int
    n_approved: int
    n_rejected: int
    rejected_reasons: list
    pnl: dict
    audit_root: str
    detail: dict = field(default_factory=dict)

    def as_record(self) -> dict:
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "timestamp": int(self.timestamp),
            "n_intents": self.n_intents,
            "n_approved": self.n_approved,
            "n_rejected": self.n_rejected,
            "rejected_reasons": self.rejected_reasons,
            "pnl": self.pnl,
            "audit_root": self.audit_root,
            "detail": self.detail,
        }

    @property
    def reproducibility_hash(self) -> str:
        canonical = json.dumps(
            self.as_record(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS dry_run (
  run_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, timestamp INTEGER NOT NULL,
  n_intents INTEGER, n_approved INTEGER, n_rejected INTEGER,
  rejected_reasons TEXT, pnl TEXT, audit_root TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS dry_run_outbox (
  outbox_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  outbox_id TEXT UNIQUE NOT NULL,
  run_id TEXT NOT NULL,
  event_json TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  causation_id TEXT NOT NULL,
  repro_hash TEXT NOT NULL,
  prev_record_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dry_run_outbox_dispatch (
  dispatch_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  outbox_id TEXT UNIQUE NOT NULL,
  dispatched_at INTEGER NOT NULL,
  event_repro_hash TEXT NOT NULL,
  prev_record_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS dry_run_no_update
BEFORE UPDATE ON dry_run
BEGIN SELECT RAISE(ABORT, 'dry_run is append-only'); END;
CREATE TRIGGER IF NOT EXISTS dry_run_no_delete
BEFORE DELETE ON dry_run
BEGIN SELECT RAISE(ABORT, 'dry_run is append-only'); END;
CREATE TRIGGER IF NOT EXISTS dry_run_outbox_no_update
BEFORE UPDATE ON dry_run_outbox
BEGIN SELECT RAISE(ABORT, 'dry_run_outbox is append-only'); END;
CREATE TRIGGER IF NOT EXISTS dry_run_outbox_no_delete
BEFORE DELETE ON dry_run_outbox
BEGIN SELECT RAISE(ABORT, 'dry_run_outbox is append-only'); END;
CREATE TRIGGER IF NOT EXISTS dry_run_outbox_dispatch_no_update
BEFORE UPDATE ON dry_run_outbox_dispatch
BEGIN SELECT RAISE(ABORT, 'dry_run_outbox_dispatch is append-only'); END;
CREATE TRIGGER IF NOT EXISTS dry_run_outbox_dispatch_no_delete
BEFORE DELETE ON dry_run_outbox_dispatch
BEGIN SELECT RAISE(ABORT, 'dry_run_outbox_dispatch is append-only'); END;
"""


def _event_json(event: Event) -> str:
    return json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _outbox_hash(message: DryRunOutboxEvent, prev_record_hash: str) -> str:
    canonical = json.dumps(
        {
            "causation_id": message.causation_id,
            "correlation_id": message.correlation_id,
            "event": message.event.model_dump(mode="json"),
            "outbox_id": message.outbox_id,
            "prev_record_hash": prev_record_hash,
            "run_id": message.run_id,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _dispatch_hash(
    *,
    outbox_id: str,
    dispatched_at: int,
    event_repro_hash: str,
    prev_record_hash: str,
) -> str:
    canonical = json.dumps(
        {
            "dispatched_at": dispatched_at,
            "event_repro_hash": event_repro_hash,
            "outbox_id": outbox_id,
            "prev_record_hash": prev_record_hash,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class DryRunStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._outbox_head_hash = ""
        self._dispatch_head_hash = ""
        if create:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        try:
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        except sqlite3.DatabaseError as exc:  # corrupt / not a db
            raise DryRunStoreError(f"dry-run store unreadable: {exc}") from exc
        tables = {r["name"] for r in rows}
        if "dry_run" not in tables:
            raise DryRunStoreError("dry-run store missing table 'dry_run'")
        outbox_tables = {"dry_run_outbox", "dry_run_outbox_dispatch"}
        if tables & outbox_tables and not outbox_tables <= tables:
            raise DryRunStoreError("dry-run outbox schema is partial")
        self._has_outbox = outbox_tables <= tables
        self._validate_retained_route_tca()
        if self._has_outbox:
            self._refresh_outbox_heads()

    @staticmethod
    def _validate_run_route_tca(run: DryRunRun) -> None:
        detail = run.detail
        if not isinstance(detail, Mapping):
            raise DryRunStoreError("dry-run detail must be an object")
        schema_version = detail.get("route_tca_schema_version")
        if schema_version is None:
            return
        if schema_version != 1 or not detail.get("routing_snapshot_id"):
            raise DryRunStoreError("route TCA schema or snapshot binding is invalid")
        routing = detail.get("routing")
        records = detail.get("route_tca")
        if not isinstance(routing, list) or not isinstance(records, list):
            raise DryRunStoreError("route TCA collections are invalid")
        multi_period = detail.get("mode") == "multi_period"
        verified: list[tuple[int | None, Mapping[str, object]]] = []
        for retained in records:
            period = None
            tca = retained
            if multi_period:
                if (
                    not isinstance(retained, Mapping)
                    or set(retained) != {"period", "tca"}
                    or type(retained["period"]) is not int
                ):
                    raise DryRunStoreError("multi-period route TCA envelope is invalid")
                period = retained["period"]
                tca = retained["tca"]
            if not verify_route_tca_record(tca):
                raise DryRunStoreError("route TCA record is invalid")
            verified.append((period, tca))
        expected = [
            (
                item.get("period") if multi_period else None,
                item.get("instrument_id"),
                item.get("status"),
            )
            for item in routing
            if isinstance(item, Mapping) and item.get("status") != "REJECTED"
        ]
        actual = [
            (period, tca.get("instrument_id"), tca.get("route_status"))
            for period, tca in verified
        ]
        if sorted(expected, key=str) != sorted(actual, key=str):
            raise DryRunStoreError("route TCA does not bind the retained route plans")

    def _validate_retained_route_tca(self) -> None:
        try:
            rows = self._conn.execute("SELECT * FROM dry_run ORDER BY run_id").fetchall()
            for row in rows:
                self._validate_run_route_tca(DryRunRun(**self._row(row)))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise DryRunStoreError("retained dry-run snapshot is invalid") from exc

    def _require_outbox(self) -> None:
        if not self._has_outbox:
            raise DryRunStoreError(
                "dry-run outbox unavailable; open the writer with create=True"
            )

    @staticmethod
    def _event_binds_run(event: Event, run: DryRunRun) -> bool:
        if (
            event.event_type == EVENT_DRY_RUN_COMPLETED
            and event.event_version == "1.1.0"
        ):
            # Historical test/tool events used the generic envelope and only
            # carried the run hash.  Canonical completion events are emitted by
            # the dry-run pipeline and must bind every summary field, while the
            # fallback keeps old non-canonical records replayable.
            if event.source != "services.execution.dry_run_pipeline":
                return (
                    event.payload.get("run_id") == run.run_id
                    and event.payload.get("run_repro_hash")
                    == run.reproducibility_hash
                )
            try:
                n_fills = run.detail.get("n_fills", 0)
                if isinstance(n_fills, bool):
                    return False
                n_fills = int(n_fills)
                if n_fills < 0:
                    return False
            except (AttributeError, TypeError, ValueError):
                return False
            expected_payload = {
                "audit_root": run.audit_root,
                "n_approved": int(run.n_approved),
                "n_fills": n_fills,
                "n_intents": int(run.n_intents),
                "n_rejected": int(run.n_rejected),
                "run_id": run.run_id,
                "run_repro_hash": run.reproducibility_hash,
                "strategy_id": run.strategy_id,
            }
            return (
                event.timestamp == event_timestamp_from_milliseconds(run.timestamp)
                and event.source == "services.execution.dry_run_pipeline"
                and event.domain == "execution"
                and event.criticality == "C2"
                and event.payload == expected_payload
            )
        if (
            event.event_type == EVENT_RESEARCH_RISK_EVALUATED
            and event.event_version == "1.0.0"
        ):
            risk = run.detail.get("research_risk")
            if not isinstance(risk, Mapping):
                return False
            try:
                engines_run_raw = risk["engines_run"]
                engines_skipped_raw = risk["engines_skipped"]
                if not isinstance(engines_run_raw, Mapping) or not isinstance(
                    engines_skipped_raw, Mapping
                ):
                    return False
                decision = str(risk["decision"])
                expected_payload = {
                    "config_provenance": str(risk["config_provenance"]),
                    "decision": decision,
                    "engines_run": {
                        str(name): value
                        for name, value in sorted(engines_run_raw.items())
                    },
                    "engines_skipped": sorted(
                        str(name) for name in engines_skipped_raw
                    ),
                    "gates_passed": int(risk["gates_passed"]),
                    "gates_run": int(risk["gates_run"]),
                    "provenance": str(risk["provenance"]),
                    "reasons": [str(reason) for reason in risk["reasons"]],
                    "run_id": run.run_id,
                    "run_repro_hash": run.reproducibility_hash,
                    "series": str(risk["series"]),
                    "strategy_id": run.strategy_id,
                }
            except (KeyError, TypeError, ValueError):
                return False
            return (
                event.timestamp == event_timestamp_from_milliseconds(run.timestamp)
                and event.source
                == "services.risk.research_risk.dry_run_guard"
                and event.domain == "risk"
                and event.criticality
                == ("C2" if decision == "RISK_APPROVED" else "C1")
                and event.payload == expected_payload
            )
        return True

    def _outbox_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT outbox_seq,outbox_id,run_id,event_json,correlation_id,"
            " causation_id,repro_hash,prev_record_hash,record_hash"
            " FROM dry_run_outbox ORDER BY outbox_seq"
        ).fetchall()

    def _dispatch_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT dispatch_seq,outbox_id,dispatched_at,event_repro_hash,"
            " prev_record_hash,record_hash"
            " FROM dry_run_outbox_dispatch ORDER BY dispatch_seq"
        ).fetchall()

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> DryRunOutboxEvent:
        try:
            event = Event.model_validate(json.loads(row["event_json"]))
        except Exception as exc:
            raise DryRunStoreError(
                f"outbox event {row['outbox_id']} cannot be reconstructed"
            ) from exc
        if event.reproducibility_hash != row["repro_hash"]:
            raise DryRunStoreError(
                f"outbox event {row['outbox_id']} payload hash mismatch"
            )
        return DryRunOutboxEvent(
            run_id=row["run_id"],
            event=event,
            correlation_id=row["correlation_id"],
            causation_id=row["causation_id"],
        )

    @classmethod
    def _verify_outbox_rows(cls, rows: list[sqlite3.Row]) -> str:
        previous = ""
        for row in rows:
            message = cls._message_from_row(row)
            if row["outbox_id"] != message.outbox_id:
                raise DryRunStoreError(
                    f"outbox row {row['outbox_seq']} id mismatch"
                )
            if row["prev_record_hash"] != previous:
                raise DryRunStoreError(
                    f"outbox row {row['outbox_seq']} previous hash mismatch"
                )
            expected = _outbox_hash(message, previous)
            if row["record_hash"] != expected:
                raise DryRunStoreError(
                    f"outbox row {row['outbox_seq']} record hash mismatch"
                )
            previous = expected
        return previous

    @staticmethod
    def _verify_dispatch_rows(
        rows: list[sqlite3.Row], outbox_by_id: dict[str, DryRunOutboxEvent]
    ) -> str:
        previous = ""
        for row in rows:
            message = outbox_by_id.get(row["outbox_id"])
            if message is None or message.event.reproducibility_hash != row[
                "event_repro_hash"
            ]:
                raise DryRunStoreError(
                    f"outbox dispatch row {row['dispatch_seq']} reference mismatch"
                )
            if row["prev_record_hash"] != previous:
                raise DryRunStoreError(
                    f"outbox dispatch row {row['dispatch_seq']} previous hash mismatch"
                )
            expected = _dispatch_hash(
                outbox_id=row["outbox_id"],
                dispatched_at=row["dispatched_at"],
                event_repro_hash=row["event_repro_hash"],
                prev_record_hash=previous,
            )
            if row["record_hash"] != expected:
                raise DryRunStoreError(
                    f"outbox dispatch row {row['dispatch_seq']} record hash mismatch"
                )
            previous = expected
        return previous

    def _refresh_outbox_heads(self) -> None:
        self._require_outbox()
        outbox_rows = self._outbox_rows()
        self._outbox_head_hash = self._verify_outbox_rows(outbox_rows)
        outbox_by_id = {
            row["outbox_id"]: self._message_from_row(row) for row in outbox_rows
        }
        for message in outbox_by_id.values():
            event = message.event
            if (
                (
                    event.event_type == EVENT_DRY_RUN_COMPLETED
                    and event.event_version == "1.1.0"
                )
                or (
                    event.event_type == EVENT_RESEARCH_RISK_EVALUATED
                    and event.event_version == "1.0.0"
                )
            ):
                run_row = self._conn.execute(
                    "SELECT * FROM dry_run WHERE run_id=?", (message.run_id,)
                ).fetchone()
                if run_row is None:
                    raise DryRunStoreError(
                        f"outbox event {message.outbox_id} references a missing run"
                    )
                retained = DryRunRun(**self._row(run_row))
                if not self._event_binds_run(event, retained):
                    raise DryRunStoreError(
                        f"outbox event {message.outbox_id} run snapshot hash mismatch"
                    )
        self._dispatch_head_hash = self._verify_dispatch_rows(
            self._dispatch_rows(), outbox_by_id
        )

    @staticmethod
    def _run_record(run: DryRunRun) -> dict:
        return run.as_record()

    def _write_run_locked(self, run: DryRunRun) -> bool:
        self._validate_run_route_tca(run)
        existing = self._conn.execute(
            "SELECT * FROM dry_run WHERE run_id=?", (run.run_id,)
        ).fetchone()
        if existing is not None:
            if self._row(existing) != self._run_record(run):
                raise DryRunStoreError(
                    f"dry-run run conflict: {run.run_id} already has different content"
                )
            return False
        self._conn.execute(
            "INSERT INTO dry_run(run_id, strategy_id, timestamp, n_intents, n_approved, "
            "n_rejected, rejected_reasons, pnl, audit_root, detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                run.run_id,
                run.strategy_id,
                int(run.timestamp),
                run.n_intents,
                run.n_approved,
                run.n_rejected,
                json.dumps(run.rejected_reasons),
                json.dumps(run.pnl),
                run.audit_root,
                json.dumps(run.detail),
            ),
        )
        return True

    def save_run(self, run: DryRunRun) -> None:
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._write_run_locked(run)
            self._conn.commit()
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def save_run_with_outbox(
        self,
        run: DryRunRun,
        messages: list[DryRunOutboxEvent],
        *,
        require_completion: bool = False,
    ) -> None:
        self._require_outbox()
        messages = list(messages)
        if any(not isinstance(message, DryRunOutboxEvent) for message in messages):
            raise TypeError("messages must contain DryRunOutboxEvent values")
        if any(message.run_id != run.run_id for message in messages):
            raise ValueError("outbox event run_id must match DryRunRun.run_id")
        if not isinstance(require_completion, bool):
            raise TypeError("require_completion must be a bool")

        # A completion transaction is the authoritative aggregate boundary:
        # the immutable run snapshot, its research-risk decision (when one is
        # present), and the canonical completion event must commit together.
        # Existing rows are included so exact retries remain idempotent.
        research_risk = run.detail.get("research_risk")
        # An evaluated risk snapshot must be paired with its canonical event.
        # A strict FULL run with missing evidence is instead a PENDING record;
        # the event schema intentionally has no fake "evaluated" representation
        # for that state, so it remains durable without an event.
        risk_requires_event = (
            isinstance(research_risk, Mapping)
            and bool(research_risk.get("engines_run"))
        )
        existing_for_run: list[DryRunOutboxEvent] = []
        if require_completion or risk_requires_event:
            existing_rows = self._conn.execute(
                "SELECT outbox_seq,outbox_id,run_id,event_json,correlation_id,"
                " causation_id,repro_hash,prev_record_hash,record_hash"
                " FROM dry_run_outbox WHERE run_id=? ORDER BY outbox_seq",
                (run.run_id,),
            ).fetchall()
            existing_for_run = [self._message_from_row(row) for row in existing_rows]
        all_messages = existing_for_run + messages
        canonical_completed = [
            message
            for message in all_messages
            if message.event.event_type == EVENT_DRY_RUN_COMPLETED
            and message.event.event_version == "1.1.0"
            and message.event.source == "services.execution.dry_run_pipeline"
        ]
        canonical_risk = [
            message
            for message in all_messages
            if message.event.event_type == EVENT_RESEARCH_RISK_EVALUATED
            and message.event.event_version == "1.0.0"
        ]
        if require_completion and not canonical_completed:
            raise ValueError(
                "completion transaction requires a canonical dry-run completion event"
            )
        if len({message.outbox_id for message in canonical_completed}) > 1:
            raise ValueError("dry-run run cannot bind multiple completion events")
        if risk_requires_event:
            if not canonical_risk:
                raise ValueError(
                    "research-risk snapshot requires a bound risk evaluation event"
                )
            if len({message.outbox_id for message in canonical_risk}) > 1:
                raise ValueError("dry-run run cannot bind multiple risk evaluation events")
        for message in messages:
            event = message.event
            if (
                (
                    event.event_type == EVENT_DRY_RUN_COMPLETED
                    and event.event_version == "1.1.0"
                )
                or (
                    event.event_type == EVENT_RESEARCH_RISK_EVALUATED
                    and event.event_version == "1.0.0"
                )
            ) and not self._event_binds_run(event, run):
                raise ValueError(
                    "dry-run event must bind the exact run snapshot"
                )

        previous_outbox_head = self._outbox_head_hash
        previous_dispatch_head = self._dispatch_head_hash
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._refresh_outbox_heads()
            previous_outbox_head = self._outbox_head_hash
            previous_dispatch_head = self._dispatch_head_hash
            self._write_run_locked(run)
            for message in messages:
                existing = self._conn.execute(
                    "SELECT outbox_seq,outbox_id,run_id,event_json,correlation_id,"
                    " causation_id,repro_hash,prev_record_hash,record_hash"
                    " FROM dry_run_outbox WHERE outbox_id=?",
                    (message.outbox_id,),
                ).fetchone()
                if existing is not None:
                    retained = self._message_from_row(existing)
                    if retained != message:
                        raise DryRunStoreError(
                            f"outbox event conflict: {message.outbox_id}"
                        )
                    continue
                digest = _outbox_hash(message, self._outbox_head_hash)
                self._conn.execute(
                    "INSERT INTO dry_run_outbox"
                    " (outbox_id,run_id,event_json,correlation_id,causation_id,"
                    " repro_hash,prev_record_hash,record_hash)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (
                        message.outbox_id,
                        message.run_id,
                        _event_json(message.event),
                        message.correlation_id,
                        message.causation_id,
                        message.event.reproducibility_hash,
                        self._outbox_head_hash,
                        digest,
                    ),
                )
                self._outbox_head_hash = digest
            self._conn.commit()
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            self._outbox_head_hash = previous_outbox_head
            self._dispatch_head_hash = previous_dispatch_head
            raise

    def pending_outbox(self, *, limit: int = 1_000) -> tuple[dict, ...]:
        self._require_outbox()
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        if limit < 1:
            raise ValueError("limit must be positive")
        self._refresh_outbox_heads()
        rows = self._conn.execute(
            "SELECT o.outbox_seq,o.outbox_id,o.run_id,o.event_json,o.correlation_id,"
            " o.causation_id,o.repro_hash,o.prev_record_hash,o.record_hash"
            " FROM dry_run_outbox o LEFT JOIN dry_run_outbox_dispatch d"
            " ON d.outbox_id=o.outbox_id WHERE d.outbox_id IS NULL"
            " ORDER BY o.outbox_seq LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple(
            {
                "outbox_id": row["outbox_id"],
                "run_id": row["run_id"],
                "event": self._message_from_row(row).event,
                "correlation_id": row["correlation_id"],
                "causation_id": row["causation_id"],
            }
            for row in rows
        )

    def mark_outbox_dispatched(
        self,
        outbox_id: str,
        *,
        dispatched_at: int,
        event_repro_hash: str,
    ) -> bool:
        self._require_outbox()
        if not isinstance(outbox_id, str) or not outbox_id:
            raise ValueError("outbox_id must be non-empty")
        if (
            isinstance(dispatched_at, bool)
            or not isinstance(dispatched_at, int)
            or dispatched_at < 0
        ):
            raise ValueError("dispatched_at must be a non-negative integer")
        if not isinstance(event_repro_hash, str) or not event_repro_hash:
            raise ValueError("event_repro_hash must be non-empty")

        previous_outbox_head = self._outbox_head_hash
        previous_dispatch_head = self._dispatch_head_hash
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._refresh_outbox_heads()
            previous_outbox_head = self._outbox_head_hash
            previous_dispatch_head = self._dispatch_head_hash
            outbox = self._conn.execute(
                "SELECT outbox_seq,outbox_id,run_id,event_json,correlation_id,"
                " causation_id,repro_hash,prev_record_hash,record_hash"
                " FROM dry_run_outbox WHERE outbox_id=?",
                (outbox_id,),
            ).fetchone()
            if outbox is None or outbox["repro_hash"] != event_repro_hash:
                raise DryRunStoreError("outbox dispatch reference mismatch")
            existing = self._conn.execute(
                "SELECT dispatch_seq,outbox_id,dispatched_at,event_repro_hash,"
                " prev_record_hash,record_hash FROM dry_run_outbox_dispatch"
                " WHERE outbox_id=?",
                (outbox_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["dispatched_at"] == dispatched_at
                    and existing["event_repro_hash"] == event_repro_hash
                ):
                    self._conn.rollback()
                    return False
                raise DryRunStoreError("outbox dispatch acknowledgement conflict")
            digest = _dispatch_hash(
                outbox_id=outbox_id,
                dispatched_at=dispatched_at,
                event_repro_hash=event_repro_hash,
                prev_record_hash=self._dispatch_head_hash,
            )
            self._conn.execute(
                "INSERT INTO dry_run_outbox_dispatch"
                " (outbox_id,dispatched_at,event_repro_hash,prev_record_hash,record_hash)"
                " VALUES (?,?,?,?,?)",
                (
                    outbox_id,
                    dispatched_at,
                    event_repro_hash,
                    self._dispatch_head_hash,
                    digest,
                ),
            )
            self._conn.commit()
            self._dispatch_head_hash = digest
            return True
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            self._outbox_head_hash = previous_outbox_head
            self._dispatch_head_hash = previous_dispatch_head
            raise

    def outbox_chain_valid(self) -> bool:
        if not self._has_outbox:
            return False
        try:
            self._refresh_outbox_heads()
            return True
        except (DryRunStoreError, sqlite3.DatabaseError):
            return False

    def _row(self, r) -> dict:
        d = dict(r)
        for k in ("rejected_reasons", "pnl", "detail"):
            d[k] = json.loads(d[k]) if d[k] else None
        return d

    def get_run(self, run_id: str):
        r = self._conn.execute("SELECT * FROM dry_run WHERE run_id=?", (run_id,)).fetchone()
        return self._row(r) if r else None

    def all_runs(self) -> list:
        return [self._row(r) for r in self._conn.execute("SELECT * FROM dry_run ORDER BY timestamp")]

    def close(self) -> None:
        self._conn.close()


__all__ = [
    "DryRunOutboxEvent",
    "DryRunRun",
    "DryRunStore",
    "DryRunStoreError",
]
