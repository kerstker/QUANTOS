"""Durable imported Demo observations and rebuildable V2 technical projection.

This is an input journal, not another cash ledger. Raw facts survive a failed
model projection. No network, credentials, order submission or promotion.
"""
from copy import deepcopy
from decimal import Decimal
import json
import hashlib
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace

from services.execution.passive_queue import Action
from services.execution.perpetual_ledger_v2 import (
    BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID, CapacityPolicy, CapacityAction,
    ContractSpec, MarginBracket, MarginMode, LinearPerpetualLedger,
    ReportedFillReplayEvent, canonical_decimal, ledger_decimal_context,
)
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.ai_factory_store import digest
from services.research.passive_receipt_calibration import validate_calibration

SCHEMA = "DEMO_EXECUTION_REPLAY_V1"
ZERO_HASH = "0" * 64
MAX_BYTES = 4_000_000
_SERVICES = Path(__file__).resolve().parents[1]
REPLAY_SOURCE_SHA256 = hashlib.sha256(b"\n".join(
    str(name).encode() + b":" + hashlib.sha256((_SERVICES / name).read_bytes()).hexdigest().encode()
    for name in ("execution/perpetual_ledger_v2.py", "execution/passive_queue.py",
                 "governance/json_integrity.py", "research/ai_factory_store.py",
                 "research/binance_order_receipts.py", "research/passive_receipt_calibration.py",
                 "research/demo_execution_journal.py"))).hexdigest()


def _fields(body, keys):
    if not isinstance(body, dict) or set(body) != set(keys.split()):
        raise ValueError("DEMO_REPLAY_FIELDS_INVALID")


def _decimal(value):
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(r"-?\d+(\.\d+)?", value):
        raise ValueError("EXACT_DECIMAL_REQUIRED")
    result = Decimal(value)
    if abs(result) > Decimal("1e15") or result.as_tuple().exponent < -24:
        raise ValueError("DECIMAL_OUT_OF_RANGE")
    return result


@ledger_decimal_context()
def prepare(manifest):
    _fields(manifest, "schema environment source_commit initial_collateral initial_position contract adv_notional participation_rate actions native expected_end_position")
    if (manifest["schema"] != SCHEMA or manifest["environment"] != "BINANCE_USDM_DEMO"
        or not isinstance(manifest["source_commit"], str) or not re.fullmatch("[0-9a-f]{40}", manifest["source_commit"])
        or manifest["initial_position"] != "0"):
        raise ValueError("FROZEN_FLAT_DEMO_REPLAY_REQUIRED")
    raw = manifest["contract"]
    _fields(raw, "decimal_context margin_admission_policy instrument_id contract_type margin_asset contract_multiplier quantity_step min_quantity min_notional liquidation_fee_rate settlement_quantum margin_brackets")
    if raw["instrument_id"] not in ("BTCUSDT", "BTCUSDC"):
        raise ValueError("UNSUPPORTED_DEMO_SYMBOL")
    brackets = []
    if not isinstance(raw["margin_brackets"], list) or not 1 <= len(raw["margin_brackets"]) <= 20:
        raise ValueError("BOUNDED_MARGIN_BRACKETS_REQUIRED")
    for row in raw["margin_brackets"]:
        _fields(row, "notional_floor notional_cap initial_margin_rate maintenance_margin_rate maintenance_amount")
        brackets.append(MarginBracket(**{k: None if v is None else _decimal(v) for k, v in row.items()}))
    spec = ContractSpec(**{k: raw[k] for k in ("instrument_id", "contract_type", "margin_asset")},
        **{k: _decimal(raw[k]) for k in ("contract_multiplier", "quantity_step", "min_quantity", "min_notional", "liquidation_fee_rate", "settlement_quantum")},
        margin_brackets=tuple(brackets))
    if spec.as_canonical_dict() != raw or spec.instrument_id != "BTC" + spec.margin_asset:
        raise ValueError("CONTRACT_VERSION_OR_QUOTE_MISMATCH")
    collateral = _decimal(manifest["initial_collateral"])
    LinearPerpetualLedger([spec], initial_collateral=collateral)
    if manifest["expected_end_position"] is not None:
        _decimal(manifest["expected_end_position"])
    actions = []
    if not isinstance(manifest["actions"], list) or not 1 <= len(manifest["actions"]) <= 200:
        raise ValueError("BOUNDED_FIXED_ORDERS_REQUIRED")
    for row in manifest["actions"]:
        _fields(row, "timestamp kind order_id side quantity price")
        if row["kind"] != "ORDER" or not isinstance(row["order_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", row["order_id"]):
            raise ValueError("FIXED_ORDINARY_ORDER_REQUIRED")
        action = Action(**(row | {"quantity": _decimal(row["quantity"]), "price": _decimal(row["price"])}))
        spec.validate_quantity(action.quantity, action.price)
        actions.append(action)
    if len({a.order_id for a in actions}) != len(actions):
        raise ValueError("DUPLICATE_FIXED_ORDER")
    native = manifest["native"]
    _fields(native, "schema symbol account_ref_sha256 action_hash provenance coverage bindings")
    if native["symbol"] != spec.instrument_id or native["action_hash"] != digest(manifest["actions"]):
        raise ValueError("FROZEN_ACTION_IDENTITY_MISMATCH")
    window = native["coverage"]
    _fields(window, "start_ms end_ms gaps")
    start, end = window["start_ms"], window["end_ms"]
    if (type(start) is not int or type(end) is not int or not 0 <= start < end
        or not 2 <= end - start <= 150_000 or start // 86_400_000 != end // 86_400_000
        or any(not start <= a.timestamp <= end for a in actions)):
        raise ValueError("BOUNDED_SAME_DAY_WINDOW_REQUIRED")
    capacity = CapacityPolicy(venue_id="BINANCE_USDM", window_id=digest(manifest),
        period_rule_id=BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID, period_index=start // 86_400_000,
        adv_notional=_decimal(manifest["adv_notional"]), max_participation_rate=_decimal(manifest["participation_rate"]),
        action=CapacityAction.REJECT)
    parsed = (spec, collateral, tuple(actions), capacity)
    observations(manifest, [], parsed)
    return parsed


def observations(manifest, messages, parsed):
    spec, _, actions, _ = parsed
    native = deepcopy(manifest["native"])
    native["messages"] = messages
    start, end = native["coverage"]["start_ms"], native["coverage"]["end_ms"]
    return validate_calibration({"schema": "PASSIVE_BINANCE_ORDER_RECEIPTS_V1", "cutoff_ms": start + 1,
                                "raw_observed": native},
        SimpleNamespace(symbol=spec.instrument_id, start_ms=start, end_ms=end), actions, native["action_hash"])


class DemoExecutionJournal:
    def __init__(self, path, *, manifest=None, read_only=False):
        self.path = Path(path).resolve()
        self.read_only = read_only
        existing = self.path.exists()
        if manifest is not None:
            prepare(manifest)
        if not existing and (read_only or manifest is None):
            raise ValueError("DEMO_JOURNAL_MISSING")
        if not existing:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path.as_uri() + ("?mode=ro" if read_only else "?mode=rwc"), uri=True)
        try:
            if not existing:
                self.db.executescript("""
                  CREATE TABLE demo_manifest(singleton INTEGER PRIMARY KEY CHECK(singleton=1), body TEXT NOT NULL, hash TEXT NOT NULL);
                  CREATE TABLE demo_receipts(sequence INTEGER PRIMARY KEY, body TEXT NOT NULL, record_hash TEXT UNIQUE NOT NULL, prev_hash TEXT NOT NULL, event_hash TEXT UNIQUE NOT NULL);
                  CREATE TABLE demo_rejections(sequence INTEGER PRIMARY KEY, batch_hash TEXT NOT NULL, reason TEXT NOT NULL);
                """)
                self.db.execute("INSERT INTO demo_manifest VALUES(1,?,?)", (json.dumps(manifest), digest(manifest)))
                for table in ("demo_manifest", "demo_receipts", "demo_rejections"):
                    for op in ("UPDATE", "DELETE"):
                        self.db.execute(f"CREATE TRIGGER {table}_no_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'append only'); END")
                self.db.commit()
            row = self.db.execute("SELECT body,hash FROM demo_manifest WHERE singleton=1").fetchone()
            if row is None:
                raise ValueError("DEMO_MANIFEST_MISSING")
            self.manifest = load_json_without_duplicate_keys(row[0])
            self.manifest_hash = digest(self.manifest)
            if row[1] != self.manifest_hash or manifest is not None and digest(manifest) != self.manifest_hash:
                raise ValueError("DEMO_MANIFEST_CONFLICT")
            self.parsed = prepare(self.manifest)
            self.read()
        except Exception:
            self.db.close()
            raise

    def _read_locked(self):
        meta = self.db.execute("SELECT body,hash FROM demo_manifest WHERE singleton=1").fetchone()
        if (meta is None or meta[1] != self.manifest_hash or digest(self.manifest) != self.manifest_hash
            or digest(load_json_without_duplicate_keys(meta[0])) != self.manifest_hash):
            raise ValueError("DEMO_MANIFEST_CORRUPT")
        rows = self.db.execute("SELECT sequence,body,record_hash,prev_hash,event_hash FROM demo_receipts ORDER BY sequence LIMIT 10001").fetchall()
        if len(rows) > 10000:
            raise ValueError("DEMO_JOURNAL_LIMIT")
        records, previous, size = [], ZERO_HASH, 0
        for expected, (sequence, body, record_hash, prev_hash, event_hash) in enumerate(rows, 1):
            size += len(body.encode())
            if size > MAX_BYTES:
                raise ValueError("DEMO_JOURNAL_LIMIT")
            value = load_json_without_duplicate_keys(body)
            if (sequence != expected or digest(value) != record_hash or prev_hash != previous
                or event_hash != digest([self.manifest_hash, sequence, record_hash, previous])):
                raise ValueError("DEMO_JOURNAL_CHAIN_INVALID")
            records.append(value)
            previous = event_hash
        rejected = self.db.execute("SELECT COUNT(*) FROM demo_rejections").fetchone()[0]
        return records, previous, rejected

    def read(self):
        self.db.execute("BEGIN")
        try:
            return self._read_locked()
        finally:
            self.db.rollback()

    def append(self, messages):
        if self.read_only:
            raise ValueError("DEMO_JOURNAL_READ_ONLY")
        if not isinstance(messages, list) or len(messages) > 10000:
            raise ValueError("BOUNDED_MESSAGE_BATCH_REQUIRED")
        batch_hash = digest(messages)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            records, previous, _ = self._read_locked()
            known = {digest(r) for r in records}
            added = []
            for record in messages:
                identity = digest(record)
                if identity not in known:
                    known.add(identity)
                    added.append(record)
            if len(records) + len(added) > 10000 or sum(len(json.dumps(r).encode()) for r in records + added) > MAX_BYTES:
                raise ValueError("DEMO_JOURNAL_LIMIT")
            observations(self.manifest, records + added, self.parsed)
            for sequence, record in enumerate(added, len(records) + 1):
                identity = digest(record)
                event_hash = digest([self.manifest_hash, sequence, identity, previous])
                self.db.execute("INSERT INTO demo_receipts VALUES(?,?,?,?,?)", (sequence, json.dumps(record), identity, previous, event_hash))
                previous = event_hash
            self.db.commit()
            return len(added)
        except Exception:
            self.db.rollback()
            self.db.execute("INSERT INTO demo_rejections(batch_hash,reason) VALUES(?,?)", (batch_hash, "BATCH_REJECTED"))
            self.db.commit()
            raise

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@ledger_decimal_context()
def replay_report(journal):
    records, tail, rejected = journal.read()
    manifest = journal.manifest
    spec, collateral, actions, capacity = journal.parsed
    validated = observations(manifest, records, journal.parsed)
    evidence = validated["native_observations"]
    orders = validated["observed"]["orders"]
    ledger = LinearPerpetualLedger([spec], initial_collateral=collateral)
    reason = None
    if not records:
        reason = "NO_RECEIPTS"
    elif evidence["coverage"]["gaps"]:
        reason = "DECLARED_STREAM_GAPS"
    elif any(t["reported_commission"] is None or t["commission_asset"] != spec.margin_asset
             or Decimal(t["reported_commission"]) < 0 for t in evidence["trade_rows"]):
        reason = "COMMISSION_NOT_SUPPORTED"
    by_id = {a.order_id: a for a in actions}
    if reason is None:
        try:
            for trade in evidence["trade_rows"]:
                side = 1 if by_id[trade["order_id"]].side == "BUY" else -1
                ledger.apply_event(ReportedFillReplayEvent(timestamp=trade["venue_trade_ms"], instrument_id=spec.instrument_id,
                    quantity_delta=side * Decimal(trade["quantity"]), execution_price=Decimal(trade["price"]),
                    margin_mode=MarginMode.CROSS, capacity=capacity, fee=Decimal(trade["reported_commission"]),
                    order_id=trade["order_id"], receipt_sha256=trade["response_sha256"]))
        except Exception as exc:
            reason = "MODEL_REPLAY_" + type(exc).__name__.upper()
    resolved = reason is None
    position = ledger.position(spec.instrument_id) if resolved else None
    quantity = canonical_decimal(position.quantity if position else Decimal(0)) if resolved else None
    all_terminal = all(o["events"] and o["events"][-1]["state"] in ("FILLED", "CANCELLED", "EXPIRED") for o in orders) and len(orders) == len(actions)
    comparison = "UNKNOWN"
    if resolved and not all_terminal:
        comparison = "WAITING_TERMINAL_ORDERS"
    elif resolved and manifest["expected_end_position"] is not None:
        comparison = "DECLARED_POSITION_MATCH" if Decimal(quantity) == Decimal(manifest["expected_end_position"]) else "DECLARED_POSITION_MISMATCH"
    return {"schema": SCHEMA, "environment": "BINANCE_USDM_DEMO", "scope": "TECHNICAL_VALIDATION_ONLY",
        "source_verified": False, "authoritative": False, "submission_enabled": False,
        "manifest_hash": journal.manifest_hash, "declared_source_commit": manifest["source_commit"],
        "replay_source_sha256": REPLAY_SOURCE_SHA256,
        "symbol": spec.instrument_id, "quote_asset": spec.margin_asset, "provenance": manifest["native"]["provenance"],
        "journal_tail": tail, "journal_records": len(records), "rejected_batches": rejected,
        "observed_at_ms": max((r["received_at_ms"] for r in records), default=None),
        "projection_status": "REPLAYED_TECHNICAL_ONLY" if resolved else "UNRESOLVED", "projection_reason": reason,
        "filled_quantity": evidence["reported_filled_quantity"], "position_quantity": quantity,
        "expected_end_position": manifest["expected_end_position"], "position_comparison": comparison,
        "venue_cash_reconciliation": "UNKNOWN", "actual_paid_fees": None,
        "reconciliation": ledger.reconciliation_snapshot().as_canonical_dict() if resolved else None,
        "ledger_state_hash": digest(ledger.state_as_canonical_dict()) if resolved else None,
        "trades_count": evidence["trades_count"], "duplicate_messages": evidence["duplicate_messages"],
        "reported_commission_by_asset": evidence["reported_commission_by_asset"],
        "orders": [{"order_id": o["order_id"], "side": o["side"], "quantity": o["quantity"],
                    "state": o["events"][-1]["state"] if o["events"] else "UNKNOWN",
                    "filled_quantity": o["events"][-1]["cumulative_filled"] if o["events"] else "0"} for o in orders],
        "recent_trades": evidence["trade_rows"][-50:],
        "limitations": ["INITIAL_FLAT_ACCOUNT_DECLARED_ONLY", "NO_MARK_FUNDING_OR_EXTERNAL_CASHFLOW_RECONCILIATION",
                        "FIXED_LIMIT_GTX_ONE_WAY_CROSS_ONLY", "IMPORTED_SOURCE_UNVERIFIED"]}
