"""Durable imported Demo observations and rebuildable V2 technical projection.

This is an input journal, not another cash ledger. Raw facts survive a failed
model projection. No network, credentials, order submission or promotion.
"""
from copy import deepcopy
from decimal import Decimal
import json
import hashlib
from itertools import groupby
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace

from quantos.contracts.binance_demo_orders import fixed_order_bindings, query_binding_hash

from services.execution.passive_queue import Action
from services.execution.perpetual_ledger_v2 import (
    BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID, CapacityPolicy, CapacityAction,
    ContractSpec, MarginMode, LinearPerpetualLedger,
    ReportedFillReplayEvent, ReportedCashReplayPolicy, canonical_decimal, ledger_decimal_context, QuantityRuleViolation,
)
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.ai_factory_store import digest
from services.research.passive_receipt_calibration import validate_calibration,SCHEMA as TRACE_SCHEMA
from services.research.passive_receipt_calibration import validate_observed_trace
from services.research.binance_order_receipts import normalize_order_receipts,CLOCK_SCHEMAS,OPTION_SCHEMAS,GOLD_SCHEMAS
from services.research.demo_opening_projection import contract_spec, prepare_opening, OPENING_SCHEMAS,GOLD_OPENING_MANIFEST_SCHEMA
from quantos.contracts.binance_demo_clock import bracket_bounds

SCHEMA = "DEMO_EXECUTION_REPLAY_V1"
GOLD_FLAT_SCHEMA = "DEMO_GOLD_EXECUTION_REPLAY_V1"
ZERO_HASH = "0" * 64
MAX_BYTES = 4_000_000
_SERVICES = Path(__file__).resolve().parents[1]
REPLAY_SOURCE_SHA256 = hashlib.sha256(b"\n".join(
    str(name).encode() + b":" + hashlib.sha256((_SERVICES / name).read_bytes()).hexdigest().encode()
    for name in ("execution/perpetual_ledger_v2.py", "execution/passive_queue.py",
                 "governance/json_integrity.py", "research/ai_factory_store.py",
                 "research/binance_order_receipts.py", "research/passive_receipt_calibration.py",
                 "../quantos/contracts/binance_demo_clock.py",
                 "../quantos/contracts/binance_demo_orders.py",
                 "research/demo_execution_journal.py", "research/demo_opening_projection.py",
                 "research/demo_account_baseline.py", "../quantos/contracts/binance_demo_account.py"))).hexdigest()


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
    _fields(manifest, "schema environment source_commit initial_collateral initial_position contract adv_notional participation_rate actions native expected_end_position"
            + (" opening" if isinstance(manifest, dict) and manifest.get("schema") in OPENING_SCHEMAS else ""))
    if (manifest["schema"] not in (SCHEMA, GOLD_FLAT_SCHEMA, *OPENING_SCHEMAS) or manifest["environment"] != "BINANCE_USDM_DEMO"
        or not isinstance(manifest["source_commit"], str) or not re.fullmatch("[0-9a-f]{40}", manifest["source_commit"])
        or manifest["schema"] in (SCHEMA,GOLD_FLAT_SCHEMA) and manifest["initial_position"] != "0"):
        raise ValueError("FROZEN_FLAT_DEMO_REPLAY_REQUIRED")
    if manifest['schema'] in OPENING_SCHEMAS and manifest['contract'] is None:
        symbol = manifest['native']['symbol']
        if symbol not in ('BTCUSDT', 'BTCUSDC'):
            raise ValueError('UNSUPPORTED_DEMO_SYMBOL')
        spec = SimpleNamespace(instrument_id=symbol, margin_asset=symbol[3:])
    else:
        spec = contract_spec(manifest['contract'])
    gold=manifest['schema'] in (GOLD_FLAT_SCHEMA,GOLD_OPENING_MANIFEST_SCHEMA)
    if (spec.instrument_id not in (('XAUUSDT',) if gold else ('BTCUSDT','BTCUSDC')) or
        spec.instrument_id != ('XAU' if gold else 'BTC') + spec.margin_asset or
        gold != (manifest['native']['schema'] in GOLD_SCHEMAS)):
        raise ValueError('UNSUPPORTED_DEMO_SYMBOL')
    collateral = _decimal(manifest["initial_collateral"])
    if isinstance(spec, ContractSpec):
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
        actions.append(action)
    if len({a.order_id for a in actions}) != len(actions):
        raise ValueError("DUPLICATE_FIXED_ORDER")
    native = manifest["native"]
    clocked=isinstance(native,dict) and native.get('schema') in CLOCK_SCHEMAS
    _fields(native, "schema symbol account_ref_sha256 action_hash provenance coverage bindings"+(' clock_bracket' if clocked else ''))
    if native["symbol"] != spec.instrument_id or native["action_hash"] != digest(manifest["actions"]):
        raise ValueError("FROZEN_ACTION_IDENTITY_MISMATCH")
    window = native["coverage"]
    _fields(window, "start_ms end_ms gaps")
    start, end = window["start_ms"], window["end_ms"]
    if (type(start) is not int or type(end) is not int or not 0 <= start < end
        or not 2 <= end - start <= 150_000 or start // 86_400_000 != end // 86_400_000
        or any(not start <= a.timestamp <= end for a in actions)):
        raise ValueError("BOUNDED_SAME_DAY_WINDOW_REQUIRED")
    period=start//86_400_000
    if clocked:
        clock=bracket_bounds(native['clock_bracket'],start_ms=start,end_ms=end)
        lower,upper=start+clock['offset_lower_ms'],end+clock['offset_upper_ms']
        if lower<0 or lower//86_400_000!=upper//86_400_000:
            raise ValueError('CAPACITY_CLOCK_DAY_UNRESOLVED')
        period=lower//86_400_000
    capacity = CapacityPolicy(venue_id="BINANCE_USDM", window_id=digest(manifest),
        period_rule_id=BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID, period_index=period,
        adv_notional=_decimal(manifest["adv_notional"]), max_participation_rate=_decimal(manifest["participation_rate"]),
        action=CapacityAction.REJECT)
    prepare_opening(manifest, spec)
    parsed = (spec, collateral, tuple(actions), capacity)
    observations(manifest, [], parsed)
    # Only validated native options can exempt a reducing order from the
    # minimum notional. Lot size and step still apply to the submitted action;
    # actual reported fills separately enforce direction and remaining holding.
    reductions = {b['order_id'] for b in native['bindings'] if b.get('reduce_only') is True}
    if isinstance(spec, ContractSpec):
        for action in actions:
            if action.quantity < spec.min_quantity:
                raise QuantityRuleViolation('order quantity is below min_quantity')
            spec.validate_quantity(action.quantity, action.price, reducing=action.order_id in reductions)
    return parsed


def observations(manifest, messages, parsed):
    spec, _, actions, _ = parsed
    native = deepcopy(manifest["native"])
    native["messages"] = messages
    start, end = native["coverage"]["start_ms"], native["coverage"]["end_ms"]
    if native['schema'] in OPTION_SCHEMAS:
        reduce_only=frozenset(b['order_id'] for b in native['bindings'] if b.get('reduce_only') is True)
        observed,evidence=normalize_order_receipts(native,actions,spec.instrument_id,native['action_hash'],reduce_only_ids=reduce_only)
        if native['schema'] in (*CLOCK_SCHEMAS,*GOLD_SCHEMAS):
            validate_observed_trace(observed,actions,spec.instrument_id,native['action_hash'])
            return {'observed':observed,'native_observations':evidence}
        checked=validate_calibration({'schema':TRACE_SCHEMA,'cutoff_ms':start+1,'observed':observed},
            SimpleNamespace(symbol=spec.instrument_id,start_ms=start,end_ms=end),actions,native['action_hash'])
        return {**checked,'native_observations':evidence}
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
def replay_ledger(manifest, parsed, evidence, *, cash_events=None):
    """One fill projection for receipt-only and explicitly reported cash replay.

    Callers validate their input/window coverage before using this projection.
    It mutates no input store and returns no authority or economic admission.
    """
    spec, collateral, actions, capacity = parsed
    specs, seed, opening = prepare_opening(manifest, spec)
    if opening is not None and opening['status'] != 'DECLARED_INPUTS_READY':
        raise ValueError('OPENING_INPUTS_INCOMPLETE')
    policy = None if cash_events is None else ReportedCashReplayPolicy(
        account_ref_sha256=manifest['native']['account_ref_sha256'], asset=spec.margin_asset)
    ledger = LinearPerpetualLedger(specs, initial_collateral=collateral, opening_state=seed,
                                  reported_cash_policy=policy)
    events, reduce_only = reported_fill_events(manifest, parsed, evidence)
    events.extend(cash_events or ())
    ordered = events if cash_events is None else sorted(events, key=lambda e: e.timestamp)
    apply_reported_events(ledger, ordered, reduce_only)
    return ledger


@ledger_decimal_context()
def reported_fill_events(manifest, parsed, evidence):
    """Convert validated receipts; no new account or cash calculation here."""
    spec, _, actions, capacity = parsed
    by_id = {a.order_id: a for a in actions}
    options = {b['order_id']: b for b in manifest['native']['bindings']}
    events = []
    for trade in evidence['trade_rows']:
        side = 1 if by_id[trade['order_id']].side == 'BUY' else -1
        events.append(ReportedFillReplayEvent(timestamp=trade['venue_trade_ms'], instrument_id=spec.instrument_id,
            quantity_delta=side*Decimal(trade['quantity']), execution_price=Decimal(trade['price']),
            margin_mode=MarginMode.CROSS, capacity=capacity, fee=Decimal(trade['reported_commission']),
            order_id=trade['order_id'], receipt_sha256=trade['response_sha256']))
    return events, frozenset(b['order_id'] for b in options.values() if b.get('reduce_only'))


@ledger_decimal_context()
def apply_reported_events(ledger, ordered, reduce_only):
    """Continue the same ledger, including its cash identities and capacity use.

    The caller supplies validated chronological observations. This is a pure
    replay primitive, not a persistence or execution permission boundary.
    """
    # Stable fill order is the validated stream order; cash batches retain the
    # ledger's atomic group checks and reject ambiguous same-ms funding/fills.
    for _, group in groupby(ordered, key=lambda e: e.timestamp):
        cash = []
        for event in group:
            if not isinstance(event, ReportedFillReplayEvent):
                cash.append(event)
                continue
            if event.order_id in reduce_only:
                held = ledger.position(event.instrument_id)
                if held is None or held.quantity*event.quantity_delta >= 0 or abs(event.quantity_delta) > abs(held.quantity):
                    raise ValueError('reported reduce-only fill conflicts with preceding position')
            ledger.apply_event(event)
        if cash:
            ledger.process_events(cash)


@ledger_decimal_context()
def replay_report(journal):
    records, tail, rejected = journal.read()
    manifest = journal.manifest
    spec, collateral, actions, capacity = journal.parsed
    validated = observations(manifest, records, journal.parsed)
    evidence = validated["native_observations"]
    orders = validated["observed"]["orders"]
    specs, seed, opening = prepare_opening(manifest, spec)
    ledger = None
    reason = None
    if opening is not None and opening["status"] != "DECLARED_INPUTS_READY":
        reason = "OPENING_INPUTS_INCOMPLETE"
    elif not records:
        reason = "NO_RECEIPTS"
    elif evidence["coverage"]["gaps"]:
        reason = "DECLARED_STREAM_GAPS"
    elif any(t["reported_commission"] is None or t["commission_asset"] != spec.margin_asset
             or Decimal(t["reported_commission"]) < 0 for t in evidence["trade_rows"]):
        reason = "COMMISSION_NOT_SUPPORTED"
    elif opening is not None and 'clock_boundary' in opening and any(
            t['venue_trade_ms']<=opening['clock_boundary']['venue_not_before_ms'] for t in evidence['trade_rows']):
        reason = 'OPENING_CLOCK_BOUNDARY_UNRESOLVED'
    if reason is None:
        try:
            ledger = replay_ledger(manifest, journal.parsed, evidence)
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
        **({'clock':evidence['clock']} if 'clock' in evidence else {}),
        "manifest_hash": journal.manifest_hash, "declared_source_commit": manifest["source_commit"],
        "opening": opening,
        "query_binding_hash": query_binding_hash(fixed_order_bindings(manifest['actions'], manifest['native']['bindings']), manifest['native']['account_ref_sha256']),
        "replay_source_sha256": REPLAY_SOURCE_SHA256,
        "symbol": spec.instrument_id, "quote_asset": spec.margin_asset, "provenance": manifest["native"]["provenance"],
        "journal_tail": tail, "journal_records": len(records), "rejected_batches": rejected,
        "observed_at_ms": max((r["received_at_ms"] for r in records), default=opening["opening_at_ms"] if opening is not None else None),
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
                    "last_event_at_ms": o["events"][-1]["venue_ms"] if o["events"] else None,
                    "filled_quantity": o["events"][-1]["cumulative_filled"] if o["events"] else "0"} for o in orders],
        "recent_trades": evidence["trade_rows"][-50:],
        "limitations": ["OPENING_ACCOUNT_INPUTS_DECLARED_ONLY" if opening is not None else "INITIAL_FLAT_ACCOUNT_DECLARED_ONLY",
                        "OTHER_POSITIONS_MARKS_FROZEN_NO_OTHER_ORDER_CASHFLOWS", "NO_MARK_FUNDING_OR_EXTERNAL_CASHFLOW_RECONCILIATION",
                        "FIXED_LIMIT_GTX_IOC_ONE_WAY_CROSS_ONLY" if manifest['native']['schema'] in OPTION_SCHEMAS else
                        "FIXED_LIMIT_GTX_ONE_WAY_CROSS_ONLY", "IMPORTED_SOURCE_UNVERIFIED"]}
