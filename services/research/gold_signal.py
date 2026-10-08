"""Long-only gold research prototype, not teacher replication or order authority.

Windows must be constructed from complete, PIT-aligned XAUUSDT inputs upstream.
This layer never creates fills, calculates PnL, sizes orders, or submits orders.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
import re


class SignalError(ValueError):
    pass


def _positive_int(value):
    return type(value) is int and 0 < value <= 2**63-1


def _time(value):
    return type(value) is int and 0 <= value <= 2**63-1


def _decimal(value, zero=False):
    return (type(value) is Decimal and value.is_finite()
            and (value >= 0 if zero else value > 0))


def _hash(value):
    return hashlib.sha256(json.dumps(value,default=str,sort_keys=True,
        separators=(',',':')).encode()).hexdigest()


@dataclass(frozen=True)
class Rule:
    window_ms: int
    max_age_ms: int
    sell_threshold: Decimal
    confirm_windows: int


@dataclass(frozen=True)
class Zone:
    lower: Decimal
    upper: Decimal
    registered_at_ms: int
    expires_at_ms: int


@dataclass(frozen=True)
class Window:
    symbol: str
    start_ms: int
    end_ms: int
    received_at_ms: int
    low: Decimal
    high: Decimal
    close: Decimal
    buy_quantity: Decimal
    sell_quantity: Decimal
    source_hash: str
    complete: bool


class GoldSignal:
    """One pre-registered zone, one signal at most; gaps require a new epoch."""

    def __init__(self, rule: Rule, zone: Zone):
        if not isinstance(rule,Rule) or not isinstance(zone,Zone):
            raise SignalError('INVALID_CONFIG_TYPE')
        if not (all(_positive_int(v) for v in (rule.window_ms,rule.max_age_ms,rule.confirm_windows))
                and _decimal(rule.sell_threshold) and _decimal(zone.lower)
                and _decimal(zone.upper) and zone.lower < zone.upper
                and _time(zone.registered_at_ms) and _time(zone.expires_at_ms)
                and zone.registered_at_ms < zone.expires_at_ms):
            raise SignalError('INVALID_RULE_OR_ZONE')
        self.rule,self.zone=rule,zone
        self.state='WATCHING'
        self._previous_end=None
        self._last_decision=None
        self._confirmation_high=None
        self._remaining=0
        self.rule_id=_hash({'schema':'GOLD_ABSORPTION_LONG_V1','symbol':'XAUUSDT',
                            'rule':asdict(rule),'zone':asdict(zone)})
        self._chain=self.rule_id

    def _result(self, reason, decision_ms, frame=None, intent=None):
        evidence=asdict(frame) if frame is not None else None
        self._chain=_hash({'parent':self._chain,'state':self.state,'reason':reason,
            'decision_ms':decision_ms,'evidence':evidence})
        return {'schema':'GOLD_RESEARCH_SIGNAL_V1','symbol':'XAUUSDT',
            'rule_id':self.rule_id,'decision_id':self._chain,'state':self.state,
            'reason':reason,'decision_ms':decision_ms,'intent':intent,
            'earliest_execution_after_ms':decision_ms if intent else None,
            'entry_price':None,'authoritative':False,'status':'RESEARCH_PROTOTYPE_ONLY'}

    def observe(self, frame: Window, *, decision_ms: int):
        if not _time(decision_ms):
            self.state='DATA_UNKNOWN'
            raise SignalError('INVALID_DECISION_TIME')
        if self.state in ('SIGNALLED','EXPIRED','DATA_UNKNOWN'):
            return self._result('TERMINAL_ZONE',decision_ms)
        valid=isinstance(frame,Window)
        if valid:
            valid=(frame.symbol=='XAUUSDT'
                and all(_time(x) for x in (frame.start_ms,frame.end_ms,frame.received_at_ms))
                and frame.start_ms < frame.end_ms <= frame.received_at_ms <= decision_ms
                and frame.end_ms-frame.start_ms==self.rule.window_ms
                and decision_ms-frame.end_ms<=self.rule.max_age_ms
                and all(_decimal(x) for x in (frame.low,frame.high,frame.close))
                and frame.low<=frame.close<=frame.high
                and _decimal(frame.buy_quantity,zero=True)
                and _decimal(frame.sell_quantity,zero=True)
                and frame.complete is True
                and isinstance(frame.source_hash,str)
                and re.fullmatch('[0-9a-f]{64}',frame.source_hash) is not None)
        if (not valid or (self._previous_end is not None and frame.start_ms!=self._previous_end)
                or (self._last_decision is not None and decision_ms<self._last_decision)
                or (self._previous_end is None and frame.start_ms<self.zone.registered_at_ms)):
            self.state='DATA_UNKNOWN'
            return self._result('INVALID_OR_MISSING_WINDOW',decision_ms)
        self._previous_end=frame.end_ms
        self._last_decision=decision_ms
        if decision_ms>=self.zone.expires_at_ms or frame.low<self.zone.lower:
            self.state='EXPIRED'
            return self._result('ZONE_EXPIRED_OR_BREACHED',decision_ms,frame)
        if self.state=='CONFIRMING':
            self._remaining-=1
            if frame.close>self._confirmation_high:
                self.state='SIGNALLED'
                return self._result('LATER_WINDOW_CONFIRMED',decision_ms,frame,'LONG_RESEARCH_SIGNAL')
            if self._remaining==0:
                self.state='EXPIRED'
            return self._result('CONFIRMATION_WAIT' if self._remaining else 'CONFIRMATION_TIMEOUT',
                                decision_ms,frame)
        if (frame.low<=self.zone.upper and frame.close>self.zone.upper
                and frame.sell_quantity>=self.rule.sell_threshold
                and frame.sell_quantity>frame.buy_quantity):
            self.state='CONFIRMING'
            self._confirmation_high=frame.high
            self._remaining=self.rule.confirm_windows
            return self._result('SELL_PRESSURE_WITH_RECLAIM',decision_ms,frame)
        return self._result('CONDITIONS_NOT_MET',decision_ms,frame)


@dataclass(frozen=True)
class ExitPlan:
    filled_at_ms: int
    stop: Decimal
    target: Decimal
    deadline_ms: int


def exit_reason(plan: ExitPlan, *, bid: Decimal, event_ms: int, received_ms: int,
                decision_ms: int, max_age_ms: int):
    """Research exit trigger on an observed bid, never proof of a closing fill.

Caller supplies an externally established long position and frozen plan. No
OHLC path inference is made; stale data returns unknown, not 'keep holding'.
"""
    if not (isinstance(plan,ExitPlan) and _time(plan.filled_at_ms)
            and _time(plan.deadline_ms) and plan.deadline_ms>plan.filled_at_ms
            and _decimal(plan.stop) and _decimal(plan.target) and plan.stop<plan.target):
        raise SignalError('INVALID_EXIT_PLAN')
    if not (_decimal(bid) and all(_time(x) for x in (event_ms,received_ms,decision_ms))
            and _positive_int(max_age_ms)
            and plan.filled_at_ms<=event_ms<=received_ms<=decision_ms
            and decision_ms-event_ms<=max_age_ms):
        return 'DATA_UNKNOWN'
    return exit_condition(plan, bid=bid, at_ms=decision_ms)


def exit_condition(plan: ExitPlan, *, bid: Decimal, at_ms: int):
    """Shared price/time condition after the caller establishes fresh data.

    All plan and decision times must use the same clock domain. This function
    establishes neither freshness, position ownership nor order permission.
    """
    if not (isinstance(plan, ExitPlan) and _time(plan.filled_at_ms)
            and _time(plan.deadline_ms) and plan.deadline_ms > plan.filled_at_ms
            and _decimal(plan.stop) and _decimal(plan.target) and plan.stop < plan.target
            and _decimal(bid) and _time(at_ms) and plan.filled_at_ms <= at_ms):
        raise SignalError('INVALID_EXIT_CONDITION')
    if bid<=plan.stop:
        return 'STOP'
    if bid>=plan.target:
        return 'TARGET'
    if at_ms>=plan.deadline_ms:
        return 'TIMEOUT'
    return 'NO_EXIT_TRIGGER'
