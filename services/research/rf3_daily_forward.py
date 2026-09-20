"""RF3 fixed 90-close daily trend: pure plan and intended-target contract.

Registered observation for docs/engineering/plans/2026-09-20-rf3-daily-forward.md.
Pure, offline, deterministic: standard library plus the fixed RF2 benchmark signal
only - no network, no data read, no ledger, no broker/risk/credential import and no
module-level mutable state.

The plan freezes the rule (BTCUSDC linear perpetual, 90 consecutive closed UTC days,
first-to-last close sign -> +0.25 / 0 / -0.25), the collector identity and the RF2
ancestry digest, and schedules 14 daily slots from the first UTC midnight after
registration; each slot opens at 00:00Z and must be published before 00:05Z.

Availability comes from the actually received response, never from a historical
decision-time proxy: the request must fall inside the slot window, all 90 bars must
be closed, and the publication instant must still precede the deadline.  A window
that closed before publication is LATE and is never backfilled.  A published target
is an intended weight only: entry_ms/exit_ms are simulated rebalance instants and
executed_position is always null.  No PnL is computed anywhere in this module.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from services.research.rf2_bounded_strategy import benchmark_target_weight

SCHEMA = "QUANTOS_RF3_DAILY_FORWARD_PLAN_V1"
SIGNAL_SCHEMA = "QUANTOS_RF3_DAILY_TARGET_V1"
SIGNAL_STATUS = "SIGNAL_ONLY_NO_ORDER_NO_PNL"
SYMBOL, INTERVAL, MARKET = "BTCUSDC", "1d", "BTCUSDC_LINEAR_PERPETUAL"
TARGET_BASIS = "FIRST_TO_LAST_CLOSE_SIGN_SAME_WINDOW"
WINDOW_DAYS, SLOT_COUNT = 90, 14
DAY_MS, DEADLINE_MS = 86_400_000, 300_000
URL_PREFIX = "https://fapi.binance.com/fapi/v1/klines?"
TARGETS = ["0.25", "0", "-0.25"]
PLAN_FIELDS = ("schema", "created_ms", "collector_identity", "ancestry_hash", "market", "symbol",
               "interval", "window_days", "slot_count", "slots", "deadline_offset_ms", "targets",
               "authority", "order_submission", "pnl")
FIXED_FIELDS = {"schema": SCHEMA, "market": MARKET, "symbol": SYMBOL, "interval": INTERVAL,
                "window_days": WINDOW_DAYS, "slot_count": SLOT_COUNT, "targets": TARGETS,
                "deadline_offset_ms": DEADLINE_MS, "authority": False, "order_submission": False,
                "pnl": False}


class Rf3Rejection(ValueError):
    """A rejected plan, receipt or publication instant (code carries the reason)."""

    def __init__(self, code, detail):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _reject(code, detail):
    raise Rf3Rejection(code, detail)


def _no_duplicate_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            _reject("DUPLICATE_KEY", f"duplicate JSON key {key!r} is not a defined response")
        seen[key] = value
    return seen


def _reject_non_finite(literal):
    _reject("NONFINITE_JSON", f"non-finite JSON literal {literal} is not a defined response")


def strict_loads(text):
    """Parse JSON with duplicate keys and NaN/Infinity literals rejected."""
    if not isinstance(text, str):
        _reject("MALFORMED_INPUT", "raw payload must be text")
    try:
        return json.loads(text, object_pairs_hook=_no_duplicate_keys,
                          parse_constant=_reject_non_finite)
    except Rf3Rejection:
        raise
    except ValueError as exc:
        _reject("RAW_NOT_JSON", str(exc))


def canonical_json(payload):
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        _reject("MALFORMED_INPUT", f"value is not canonical-JSON encodable: {exc}")


def digest(payload):
    """Canonical sha256 over a payload that must contain no non-finite number."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _as_int(value, name):
    if type(value) is not int:
        _reject("MALFORMED_INPUT", f"{name} must be an integer, got {value!r}")
    return value


def _as_digest(value, name):
    if not isinstance(value, str) or len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value):
        _reject("MALFORMED_INPUT", f"{name} must be a lowercase sha256 hex digest")
    return value


def next_slot_ms(created_ms):
    """First UTC midnight strictly after the registration instant."""
    day = datetime.fromtimestamp(_as_int(created_ms, "created_ms") / 1000, timezone.utc).date()
    start = datetime.combine(day + timedelta(days=1), datetime.min.time(), timezone.utc)
    return int(start.timestamp() * 1000)


def fixed_url(slot_ms):
    """The only public URL this observer may construct, for one 00:00Z slot."""
    slot_ms = _as_int(slot_ms, "slot_ms")
    start = slot_ms - WINDOW_DAYS * DAY_MS
    return (f"{URL_PREFIX}symbol={SYMBOL}&interval={INTERVAL}"
            f"&startTime={start}&endTime={slot_ms - 1}&limit={WINDOW_DAYS}")


def make_plan(created_ms, collector_identity, ancestry_hash):
    """Freeze the observation plan; ``created_ms`` is the registration instant."""
    created_ms = _as_int(created_ms, "created_ms")
    if not isinstance(collector_identity, str) or not collector_identity:
        _reject("MALFORMED_INPUT", "collector_identity must be a non-empty string")
    ancestry_hash = _as_digest(ancestry_hash, "ancestry_hash")
    start = next_slot_ms(created_ms)
    plan = {"schema": SCHEMA, "created_ms": created_ms, "collector_identity": collector_identity,
            "ancestry_hash": ancestry_hash, "market": MARKET, "symbol": SYMBOL,
            "interval": INTERVAL, "window_days": WINDOW_DAYS, "slot_count": SLOT_COUNT,
            "slots": [start + index * DAY_MS for index in range(SLOT_COUNT)],
            "deadline_offset_ms": DEADLINE_MS, "targets": list(TARGETS), "authority": False,
            "order_submission": False, "pnl": False}
    plan["plan_hash"] = digest(plan)
    return plan


def verify_plan(plan):
    """Reject any plan whose sealed digest or fixed field values have drifted."""
    if not isinstance(plan, dict) or set(plan) != set(PLAN_FIELDS) | {"plan_hash"}:
        _reject("PLAN_INVALID", "plan keys do not match the frozen RF3 field set")
    if not isinstance(plan["plan_hash"], str) or digest(
            {key: value for key, value in plan.items() if key != "plan_hash"}) != plan["plan_hash"]:
        _reject("PLAN_HASH_MISMATCH", "plan digest does not match the frozen field values")
    for key, expected in FIXED_FIELDS.items():
        if plan[key] != expected or type(plan[key]) is not type(expected):
            _reject("PLAN_INVALID", f"plan[{key!r}] must be exactly {expected!r}")
    created_ms = _as_int(plan["created_ms"], "created_ms")
    slots = plan["slots"]
    if not isinstance(slots, list) or any(type(slot) is not int for slot in slots):
        _reject("PLAN_INVALID", "plan slots must be integer ms clocks")
    if slots != [next_slot_ms(created_ms) + index * DAY_MS for index in range(SLOT_COUNT)]:
        _reject("PLAN_INVALID", "plan slots must be the 14 consecutive UTC days after registration")
    if created_ms >= slots[0]:
        _reject("PLAN_INVALID", "registration must precede the first slot")
    if not isinstance(plan["collector_identity"], str) or not plan["collector_identity"]:
        _reject("PLAN_INVALID", "collector_identity must be a non-empty string")
    _as_digest(plan["ancestry_hash"], "ancestry_hash")
    return plan


def _positive_close(value, index):
    if isinstance(value, bool):
        _reject("MALFORMED_BAR", f"kline close[{index}] must not be boolean")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, (int, float, str)):
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            _reject("MALFORMED_BAR", f"kline close[{index}] is not a decimal number")
    else:
        _reject("MALFORMED_BAR", f"kline close[{index}] is not a decimal number")
    if not number.is_finite() or number <= 0:
        _reject("MALFORMED_BAR", f"kline close[{index}] must be finite and positive")
    return number


def _closed_bars(rows, slot_ms):
    """Validate the exact 90 consecutive closed UTC bars of one slot window."""
    if not isinstance(rows, list) or len(rows) != WINDOW_DAYS:
        _reject("MISSING_BAR", f"window requires exactly {WINDOW_DAYS} closed daily bars")
    first_open = slot_ms - WINDOW_DAYS * DAY_MS
    closes = []
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) < 7:
            _reject("MALFORMED_BAR", f"kline row {index} is not a full public kline")
        open_ms = row[0]
        if type(open_ms) is not int:
            _reject("MALFORMED_BAR", f"kline row {index} open clock must be an integer ms")
        if open_ms != first_open + index * DAY_MS:
            _reject("BAR_ALIGNMENT",
                    f"kline row {index} is not the expected unique consecutive UTC day")
        close_ms = row[6]
        if type(close_ms) is not int or close_ms != open_ms + DAY_MS - 1:
            _reject("BAR_ALIGNMENT", f"kline row {index} close must be open + one day - 1 ms")
        closes.append(_positive_close(row[4], index))
    return first_open, closes


def issue_target(plan, receipt, issued_ms):
    """Publish the fixed intended target for a plan slot from one raw receipt.

    ``issued_ms`` is the publication instant read from the real clock after the
    response was received and validated.
    """
    verify_plan(plan)
    issued_ms = _as_int(issued_ms, "issued_ms")
    if not isinstance(receipt, dict) or receipt.get("ok") is not True:
        _reject("UNSUPPORTED_RECEIPT", "receipt does not describe a successful public response")
    url = receipt.get("url")
    slot_index = next((index for index, slot in enumerate(plan["slots"])
                       if fixed_url(slot) == url), None)
    if slot_index is None:
        _reject("WRONG_WINDOW", "receipt URL is not this plan's fixed BTCUSDC daily window")
    slot_ms = plan["slots"][slot_index]
    requested_ms = _as_int(receipt.get("requested_ms"), "requested_ms")
    received_ms = _as_int(receipt.get("received_ms"), "received_ms")
    if not slot_ms <= requested_ms <= received_ms <= issued_ms:
        _reject("CLOCK_ORDER", "clock order must be slot <= requested <= received <= issued")
    if issued_ms >= slot_ms + DEADLINE_MS:
        _reject("LATE", "target must be published before the 00:05Z deadline")
    raw = receipt.get("raw_utf8")
    if not isinstance(raw, str):
        _reject("UNSUPPORTED_RECEIPT", "receipt must carry the conserved raw response text")
    first_open, closes = _closed_bars(strict_loads(raw), slot_ms)
    return {"schema": SIGNAL_SCHEMA, "status": SIGNAL_STATUS, "slot_index": slot_index,
            "slot_ms": slot_ms, "market": MARKET, "symbol": SYMBOL, "interval": INTERVAL,
            "window_days": WINDOW_DAYS, "bar_count": len(closes), "window_open_ms": first_open,
            "window_close_ms": slot_ms - 1, "target_weight": str(benchmark_target_weight(closes)),
            "target_basis": TARGET_BASIS, "intended_only": True, "executed_position": None,
            "entry_ms": slot_ms + DEADLINE_MS, "exit_ms": slot_ms + DEADLINE_MS + DAY_MS,
            "issued_ms": issued_ms, "requested_ms": requested_ms, "received_ms": received_ms,
            "receipt_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "plan_hash": plan["plan_hash"], "collector_identity": plan["collector_identity"],
            "ancestry_hash": plan["ancestry_hash"], "authority": False,
            "order_submission": False, "pnl": None}
