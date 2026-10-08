"""Fixed causal gold hypotheses. Signals only; no fills, returns or authority."""
from dataclasses import dataclass
from collections.abc import Sequence
from collections import deque
from decimal import Decimal, DecimalException
import hashlib
import json

from services.execution.perpetual_ledger_v2 import ledger_decimal_context

INTERVAL_MS = 900_000
CANDIDATES = ('channel', 'trend', 'reversion')
WARMUP = 400


CATALOG = 'GOLD_BAR_CANDIDATES_V1'


def candidates():
    descriptions = (
        ('Breakout beyond previous 20 bars; exit opposite previous 10-bar boundary',
         'Breakouts fail to persist after execution costs'),
        ('EMA20 crosses EMA80; exit opposite crossover',
         'Whipsaws and costs overwhelm trend persistence'),
        ('Close deviates two population standard deviations from previous 40 closes; exit at mean',
         'Persistent trends overwhelm mean reversion after costs'),
    )
    return [dict(id=index, family=name, catalog=CATALOG, instrument_id='XAUUSDT',
        interval_ms=INTERVAL_MS, warmup_bars=WARMUP, hypothesis=descriptions[index][0],
        falsification=descriptions[index][1], stop='2 times prior 14-bar mean true range',
        max_holding_bars=16 if name == 'reversion' else 96,
        scope='TECHNICAL_RESEARCH_ONLY') for index, name in enumerate(CANDIDATES)]


@dataclass(frozen=True)
class Bar:
    open_ms: int
    close_ms: int
    available_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def validate(self):
        times = (self.open_ms, self.close_ms, self.available_ms)
        prices = (self.open, self.high, self.low, self.close)
        if (any(type(t) is not int or not 0 <= t <= 2**63 - 1 for t in times)
                or self.open_ms % INTERVAL_MS
                or self.close_ms != self.open_ms + INTERVAL_MS
                or self.available_ms < self.close_ms
                or any(type(p) is not Decimal or not p.is_finite() or p <= 0 for p in prices)):
            raise ValueError('INVALID_GOLD_BAR')
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError('INVALID_GOLD_BAR_RANGE')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def _bar_identity(bar):
    # Validated Bar fields are immutable ints/Decimals. Preserve every original
    # JSON field and Decimal spelling without asdict's recursive deep copies.
    return dict(open_ms=bar.open_ms, close_ms=bar.close_ms, available_ms=bar.available_ms,
                open=str(bar.open), high=str(bar.high), low=str(bar.low), close=str(bar.close))


def _ema(values, period):
    alpha = Decimal(2) / (period + 1)
    result = [values[0].close]
    for bar in values[1:]:
        result.append(alpha * bar.close + (1 - alpha) * result[-1])
    return result


@ledger_decimal_context()
def evaluate(bars, *, candidate, as_of_ms):
    """Evaluate latest available closed bar, excluding unknown future observations.

    close_ms is the exclusive interval end, not Binance's inclusive closeTime.
    Data adapters must retain the real availability convention and provenance.
    A gap discards indicator history; late bars are usable only once available.
    """
    if isinstance(bars, CausalGoldBarEvaluator):
        return bars.evaluate(candidate=candidate, as_of_ms=as_of_ms)
    if (candidate not in CANDIDATES or type(as_of_ms) is not int
            or not 0 <= as_of_ms <= 2**63 - 1 or not isinstance(bars, Sequence)):
        raise ValueError('INVALID_GOLD_BAR_REQUEST')
    known = []
    previous = -1
    for bar in bars:
        if type(bar) is not Bar:
            raise ValueError('INVALID_GOLD_BAR_TYPE')
        bar.validate()
        if bar.open_ms <= previous:
            raise ValueError('GOLD_BAR_ORDER_OR_DUPLICATE')
        previous = bar.open_ms
        if bar.available_ms > as_of_ms:
            continue
        if known and bar.open_ms != known[-1].close_ms:
            known = []
        known.append(bar)
    def input_digest():
        return _digest([_bar_identity(bar) for bar in known])
    def trend():
        fast, slow = _ema(known, 20), _ema(known, 80)
        return fast[-2], slow[-2], fast[-1], slow[-1]
    return _evaluate_known(known, total=len(known), candidate=candidate, as_of_ms=as_of_ms,
                           input_digest=input_digest, trend=trend)


def _evaluate_known(known, *, total, candidate, as_of_ms, input_digest, trend):
    """The original signal math, shared by full-history and incremental reads."""
    result = dict(schema='GOLD_BAR_HYPOTHESIS_V1', candidate=candidate,
                  symbol='XAUUSDT', status='WARMUP', decision_ms=as_of_ms,
                  earliest_execution_after_ms=as_of_ms, entry_side=None,
                  exit_long=False, exit_short=False, entry_price=None,
                  stop_distance=None, max_holding_bars=16 if candidate == 'reversion' else 96,
                  authoritative=False, submission_enabled=False,
                  scope='TECHNICAL_RESEARCH_ONLY', bars_available=total)
    if not known or total <= WARMUP:
        return result
    current, prior = known[-1], known[:-1]
    if as_of_ms - current.close_ms > INTERVAL_MS:
        result['status'] = 'STALE'
        return result
    # Include all causally consumed observations (EMA has a recursive history).
    result['input_sha256'] = input_digest()
    ranges = [max(bar.high - bar.low, abs(bar.high - previous_bar.close),
                  abs(bar.low - previous_bar.close))
              for previous_bar, bar in zip(prior[-15:-1], prior[-14:])]
    distance = Decimal(2) * sum(ranges, Decimal(0)) / 14
    result.update(status='READY', stop_distance=str(distance))
    if distance <= 0:
        result['status'] = 'ZERO_VOLATILITY'
        return result
    if candidate == 'channel':
        if current.close > max(bar.high for bar in prior[-20:]):
            result['entry_side'] = 'BUY'
        elif current.close < min(bar.low for bar in prior[-20:]):
            result['entry_side'] = 'SELL'
        result['exit_long'] = current.close < min(bar.low for bar in prior[-10:])
        result['exit_short'] = current.close > max(bar.high for bar in prior[-10:])
    elif candidate == 'trend':
        prior_fast, prior_slow, fast, slow = trend()
        up = prior_fast <= prior_slow and fast > slow
        down = prior_fast >= prior_slow and fast < slow
        result.update(entry_side='BUY' if up else 'SELL' if down else None,
                      exit_long=down, exit_short=up)
    else:
        closes = [bar.close for bar in prior[-40:]]
        mean = sum(closes, Decimal(0)) / 40
        variance = sum(((p - mean)**2 for p in closes), Decimal(0)) / 40
        if variance:
            z = (current.close - mean) / variance.sqrt()
            result.update(entry_side='BUY' if z < -2 else 'SELL' if z > 2 else None,
                          exit_long=z >= 0, exit_short=z <= 0)
    result['decision_id'] = _digest(result)
    return result


def causal_receipts(bars, *, after=-1):
    """Only append-ordered availability permits consuming a prefix once."""
    for bar in bars:
        if type(bar) is not Bar or bar.available_ms < after:
            return False
        after = bar.available_ms
    return True


def causal_evaluator(bars):
    # The original public evaluator remains available for nonmonotone receipts.
    return CausalGoldBarEvaluator(bars) if causal_receipts(bars) else None


class CausalGoldBarEvaluator:
    """Consume each available original bar once without truncating EMA or identity.

    Forty-one bars suffice for the original finite-window formulas. Full-history
    EMA state and an incremental SHA of the exact original JSON array persist.
    Future inputs remain pending, never part of the indicator state or digest.
    This is ephemeral computation; original journals remain the replay evidence.
    """

    def __init__(self, bars):
        self._pending = deque()
        self._last_open = self._last_available = -1
        self._as_of = None
        self._reset()
        self._append(bars)

    def _reset(self):
        self._window = deque(maxlen=41)
        self._count = 0
        self._digest = hashlib.sha256(b'[')
        self._fast = self._slow = self._prior_fast = self._prior_slow = None
        self._ema_error = None

    def _append(self, bars):
        if not isinstance(bars, (list, tuple)):
            raise ValueError('INVALID_GOLD_BAR_REQUEST')
        previous, available = self._last_open, self._last_available
        for bar in bars:
            if type(bar) is not Bar:
                raise ValueError('INVALID_GOLD_BAR_TYPE')
            bar.validate()
            if bar.open_ms <= previous:
                raise ValueError('GOLD_BAR_ORDER_OR_DUPLICATE')
            if bar.available_ms < available:
                raise ValueError('GOLD_BAR_RECEIPT_ORDER')
            if self._as_of is not None and bar.available_ms <= self._as_of:
                raise ValueError('BAR_RECEIPT_ALREADY_PROCESSED')
            previous, available = bar.open_ms, bar.available_ms
        # Validate the entire batch before changing any state.
        self._pending.extend(bars)
        self._last_open, self._last_available = previous, available

    def append_bars(self, bars):
        if not bars:
            raise ValueError('EMPTY_GOLD_BAR_APPEND')
        self._append(bars)

    @ledger_decimal_context()
    def _consume(self, bar):
        if self._window and bar.open_ms != self._window[-1].close_ms:
            self._reset()
        if self._count:
            self._digest.update(b',')
        self._digest.update(json.dumps(_bar_identity(bar),
            sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
        self._prior_fast, self._prior_slow = self._fast, self._slow
        if not self._count:
            self._fast = self._slow = bar.close
        elif self._ema_error is None:
            try:
                alpha = Decimal(2) / 21
                self._fast = alpha * bar.close + (1 - alpha) * self._fast
                alpha = Decimal(2) / 81
                self._slow = alpha * bar.close + (1 - alpha) * self._slow
            except DecimalException as exc:
                # The original channel/reversion paths do not compute EMA.
                # Raise only if a READY trend actually requires that history.
                self._ema_error = exc
        self._window.append(bar)
        self._count += 1

    def _input_digest(self):
        digest = self._digest.copy()
        digest.update(b']')
        return digest.hexdigest()

    def _trend(self):
        if self._ema_error is not None:
            raise self._ema_error
        return self._prior_fast, self._prior_slow, self._fast, self._slow

    @ledger_decimal_context()
    def evaluate(self, *, candidate, as_of_ms):
        if (candidate not in CANDIDATES or type(as_of_ms) is not int
                or not 0 <= as_of_ms <= 2**63 - 1):
            raise ValueError('INVALID_GOLD_BAR_REQUEST')
        if self._as_of is not None and as_of_ms < self._as_of:
            raise ValueError('GOLD_BAR_CLOCK_REWINDS')
        while self._pending and self._pending[0].available_ms <= as_of_ms:
            self._consume(self._pending.popleft())
        self._as_of = as_of_ms
        return _evaluate_known(list(self._window), total=self._count, candidate=candidate,
            as_of_ms=as_of_ms, input_digest=self._input_digest, trend=self._trend)
