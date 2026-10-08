"""RF3 published daily targets -> the unique V2 perpetual ledger, offline only.

Registered account adapter for
docs/engineering/plans/2026-09-20-rf3-daily-forward.md (part two).  Pure and
offline: the frozen RF3 contract, the canonical V2 replay and its attribution,
nothing else - no filesystem read, no network, no second ledger, no broker,
risk-gate or credential import and no module-level mutable state.

The caller passes the registered plan, the fourteen published slot bundles
exactly as the observer persisted them (saved signal, attempt record and
publication receipt) and one pre-captured public market.  Nothing is trusted:
every target is recomputed from the attempt's conserved raw response through
``rf3.issue_target`` and compared against the saved signal, so a fabricated
target, a substituted receipt or a reordered slot is a rejection instead of a
result.  All fourteen slots must be complete and in order before any replay
starts, and no slot may be supplied twice.

The window is fixed by the plan: ``start = slots[0] + 00:05Z`` and ``end =
start + 14 days`` at the canonical UTC-day decision interval.  Three accounts -
cash, a passive 25% long and the frozen daily trend - are replayed at 2/5/10 bp
slippage with 4 bp taker fees, the loss-budget stop and the canonical 1500
initial collateral in one continuous account that is never reset daily.  Fee,
stop, decision and risk parameters are module constants: this API exposes no
argument that could override them.

Authority.  Every case is a simulated technical replay of intended targets, and
attribution that does not reconcile is a rejection rather than a profit.  A
positive number here is not forward profit, not economic eligibility and not a
gate: the result stays ``TECHNICAL_VALIDATION_ONLY`` with ``economic_eligible``,
``authority`` and ``order_submission`` all false.
"""
from __future__ import annotations

from decimal import Decimal

from services.research import rf3_daily_forward as rf3
from services.research.perpetual_alpha import replay
from services.research.profitability_attribution import attribute_replay

SCHEMA = "QUANTOS_RF3_FORWARD_ACCOUNT_V1"
STATUS = "TECHNICAL_VALIDATION_ONLY"
RECONCILED = "RECONCILED_TECHNICAL_ONLY"
PUBLISHED = "PUBLISHED"
ACCOUNTS = ("CASH", "PASSIVE_25", "FROZEN_TREND")
FEE_BPS = Decimal("4")
SLIPPAGE_BPS = (2, 5, 10)
ATTEMPT_LIMIT = 3
MAXIMUM_QUANTITY = Decimal("50")
# The terminal valuation uses the minute at ``end``; that minute closes later.
MATURE_OFFSET_MS = 60_000
MARKET_KEYS = frozenset({"times", "prices", "marks", "funding", "start", "end",
                         "daily_volume", "maximum_quantity"})
BUNDLE_KEYS = frozenset({"signal", "attempt", "publication"})
PUBLICATION_KEYS = frozenset({"status", "completed_ms", "signal_hash"})
LIMITATIONS = [
    "FINAL_EQUITY_IS_MARKED_TO_MARKET_AND_MAY_INCLUDE_AN_OPEN_POSITION",
    "PUBLICATION_CLOCKS_ARE_UNSIGNED_LOCAL_TIMES_WITHOUT_TIME_PROOF",
    "SIMULATED_EXECUTION_OF_PREDECLARED_TARGET_WEIGHTS",
    "UNCALIBRATED_CAPACITY_SLIPPAGE_AND_VENUE_RULES",
    "NOT_VENUE_ACCOUNT_PNL_AND_NOT_FORWARD_PROFIT",
    "TECHNICAL_REPLAY_CONFERS_NO_ECONOMIC_OR_TRADING_AUTHORITY",
]


def _reject(code, detail):
    raise rf3.Rf3Rejection(code, detail)


def _exactly(value, expected):
    """Type-strict binding check; ``0 == False`` and ``1.0 == 1`` must not pass."""
    return type(value) is type(expected) and value == expected


def account_window(plan):
    """The plan's fixed rebalance window: 00:05Z of slot 0 to 14 days later."""
    rf3.verify_plan(plan)
    start = plan["slots"][0] + rf3.DEADLINE_MS
    return start, start + rf3.SLOT_COUNT * rf3.DAY_MS


def _verify_market(market, start, end):
    """Bound the captured market surface; the canonical engine validates arrays."""
    if not isinstance(market, dict):
        _reject("MARKET_INVALID", "market must be the captured public market dictionary")
    keys = set(market)
    if keys not in (MARKET_KEYS, MARKET_KEYS | {"quote"}):
        _reject("MARKET_INVALID", "market must carry only the standard capture keys")
    if "quote" in market and market["quote"] != "USDC":
        _reject("MARKET_INVALID", "only the BTCUSDC quote market is registered")
    for key in ("start", "end"):
        if type(market[key]) is not int:
            _reject("MARKET_INVALID", f"market[{key!r}] must be an integer ms clock")
    if market["start"] != start or market["end"] != end:
        _reject("MARKET_BOUNDS", "market start/end must equal the plan's rebalance window")
    maximum = market["maximum_quantity"]
    if type(maximum) is not Decimal or maximum != MAXIMUM_QUANTITY:
        _reject("MARKET_INVALID", "market maximum_quantity must be the canonical Decimal 50")


def _verify_bundle(plan, bundle, index):
    """Recompute one slot's target from its conserved raw response; trust nothing."""
    slot_ms = plan["slots"][index]
    if not isinstance(bundle, dict) or set(bundle) != BUNDLE_KEYS:
        _reject("BUNDLE_INVALID",
                f"slot {index} must carry its original signal, attempt and publication artifacts")
    signal, record, publication = bundle["signal"], bundle["attempt"], bundle["publication"]
    if not isinstance(signal, dict) or not isinstance(record, dict) or not isinstance(publication, dict):
        _reject("BUNDLE_INVALID", f"slot {index} artifacts must be JSON objects")
    if set(publication) != PUBLICATION_KEYS:
        _reject("BUNDLE_INVALID", f"slot {index} publication must be the observer's own receipt")
    attempt = signal.get("attempt")
    if type(attempt) is not int or not 1 <= attempt <= ATTEMPT_LIMIT:
        _reject("ATTEMPT_INVALID", f"slot {index} attempt must be an integer in 1..{ATTEMPT_LIMIT}")
    if record.get("status") != PUBLISHED:
        _reject("ATTEMPT_STATUS", f"slot {index} attempt is not a published success")
    if not _exactly(record.get("slot_index"), index) or not _exactly(record.get("slot_ms"), slot_ms):
        _reject("SLOT_MISMATCH", f"slot {index} attempt record is bound to another slot")
    if not _exactly(record.get("attempt"), attempt):
        _reject("ATTEMPT_INVALID", f"slot {index} attempt record disagrees with its signal's attempt")
    raw = record.get("raw_utf8")
    if not isinstance(raw, str):
        _reject("ATTEMPT_INVALID", f"slot {index} attempt must conserve the raw public response text")
    issued_ms = signal.get("issued_ms")
    for label, value in (("issued_ms", issued_ms), ("requested_ms", record.get("requested_ms")),
                         ("received_ms", record.get("received_ms"))):
        if type(value) is not int:
            _reject("CLOCK_INVALID", f"slot {index} {label} must be an integer ms clock")
    receipt = {"ok": True, "url": record.get("url"), "requested_ms": record["requested_ms"],
               "received_ms": record["received_ms"], "raw_utf8": raw}
    try:
        expected = rf3.issue_target(plan, receipt, issued_ms)
    except rf3.Rf3Rejection as exc:
        # LATE, WRONG_WINDOW, BAR_ALIGNMENT, MALFORMED_BAR, CLOCK_ORDER, ... all carry forward.
        _reject(exc.code, f"slot {index}: {exc.detail}")
    if not _exactly(expected["slot_index"], index):
        _reject("SLOT_REORDERED", f"slot {index} artifacts belong to another slot window")
    if record.get("receipt_sha256") != expected["receipt_sha256"]:
        _reject("RECEIPT_MISMATCH", f"slot {index} attempt receipt hash does not seal its raw response")
    recorded_ms = signal.get("recorded_ms")
    if type(recorded_ms) is not int or not issued_ms <= recorded_ms < expected["entry_ms"]:
        _reject("RECORDED_CLOCK",
                f"slot {index} recorded_ms must be an integer inside its publication window")
    if not _exactly(record.get("recorded_ms"), recorded_ms):
        _reject("RECORDED_CLOCK", f"slot {index} attempt record and signal disagree on recorded_ms")
    # Canonical-JSON comparison, so a numeric flag or a float clock cannot pass
    # for the boolean/int the observer actually persisted.
    if rf3.canonical_json(signal) != rf3.canonical_json(
            {**expected, "attempt": attempt, "recorded_ms": recorded_ms}):
        _reject("SIGNAL_MISMATCH", f"slot {index} saved signal differs from its recomputed target")
    completed_ms = publication["completed_ms"]
    if publication["status"] != PUBLISHED:
        _reject("PUBLICATION_STATUS", f"slot {index} publication is not a completed success")
    if type(completed_ms) is not int or not recorded_ms <= completed_ms < expected["entry_ms"]:
        _reject("PUBLICATION_CLOCK",
                f"slot {index} publication must complete at or after its signal and before entry")
    if publication["signal_hash"] != rf3.digest(signal):
        _reject("PUBLICATION_HASH", f"slot {index} publication does not seal the saved signal")
    return signal


def _verify_bundles(plan, bundles):
    """Require all fourteen ordered slots before any replay is attempted."""
    if not isinstance(bundles, list) or len(bundles) != rf3.SLOT_COUNT:
        _reject("BUNDLES_INCOMPLETE",
                f"all {rf3.SLOT_COUNT} published slot bundles are required before replay")
    return [_verify_bundle(plan, bundle, index) for index, bundle in enumerate(bundles)]


def _decision(account, frozen):
    """Fixed float target callbacks; the account stays continuous, no daily reset."""
    if account == "CASH":
        return lambda at, held: 0.0
    if account == "PASSIVE_25":
        return lambda at, held: 0.25
    return lambda at, held: frozen[at]


def evaluate(plan, bundles, market, *, evaluated_ms, case_observer=None):
    """Replay the fourteen published daily targets on the canonical ledger.

    ``bundles`` is the ordered list of exactly fourteen ``{signal, attempt,
    publication}`` artifacts; ``market`` is one pre-captured completed public
    window.  Fee, slippage, stop, decision and risk parameters are fixed here.
    ``case_observer``, when supplied, is called with one lifecycle event per
    case: ``{"state": "START", "account", "slippage_bps"}`` immediately before
    that case's replay and ``{"state": "COMPLETED", "account", "slippage_bps",
    "case"}`` with the full reconciled case right after its attribution.  A
    callback error propagates unchanged and aborts the remaining cases.
    """
    rf3.verify_plan(plan)
    start = plan["slots"][0] + rf3.DEADLINE_MS
    end = start + rf3.SLOT_COUNT * rf3.DAY_MS
    if type(evaluated_ms) is not int:
        _reject("CLOCK_INVALID", "evaluated_ms must be an integer ms clock")
    if case_observer is not None and not callable(case_observer):
        _reject("OBSERVER_INVALID", "case_observer must be callable when supplied")
    if evaluated_ms <= end + MATURE_OFFSET_MS:
        _reject("IMMATURE", "the window and its closing minute must be complete before evaluation")
    _verify_market(market, start, end)
    signals = _verify_bundles(plan, bundles)
    frozen = {signal["entry_ms"]: float(signal["target_weight"]) for signal in signals}
    cases = []
    for account in ACCOUNTS:
        decision = _decision(account, frozen)
        for slippage in SLIPPAGE_BPS:
            if case_observer is not None:
                case_observer({"state": "START", "account": account, "slippage_bps": slippage})
            # One canonical ledger per case; no parallel PnL implementation here.
            receipt = replay(**market, decision=decision, decision_interval_ms=rf3.DAY_MS,
                             slip_bps=slippage, taker_fee_bps=FEE_BPS, stop=True)
            attribution = attribute_replay(receipt)
            if attribution["status"] != RECONCILED:
                _reject("ATTRIBUTION_UNKNOWN",
                        f"{account} at {slippage}bp: {attribution['reason']}")
            case = {"account": account, "fee_bps": int(FEE_BPS), "slippage_bps": slippage,
                    "receipt": receipt, "attribution": attribution}
            if case_observer is not None:
                case_observer({"state": "COMPLETED", "account": account,
                               "slippage_bps": slippage, "case": case})
            cases.append(case)
    return {"schema": SCHEMA, "status": STATUS, "plan_hash": plan["plan_hash"],
            "artifacts_digest": rf3.digest({"plan_hash": plan["plan_hash"], "artifacts": bundles}),
            "start_ms": start, "end_ms": end, "mature_at_ms": end + MATURE_OFFSET_MS,
            "evaluated_ms": evaluated_ms, "slot_count": len(signals), "cases": cases,
            "economic_eligible": False, "authority": False, "order_submission": False,
            "pnl": None, "limitations": list(LIMITATIONS)}
