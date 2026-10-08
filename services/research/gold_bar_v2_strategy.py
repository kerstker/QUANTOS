"""Gold bar hypotheses routed through original V2 simulation orders only."""
from decimal import Decimal

from services.execution.perpetual_simulation import CancelOrder, OrderIntent, SimulationContext
from services.research.gold_bar_candidates import (
    CANDIDATES, INTERVAL_MS, causal_evaluator, causal_receipts, evaluate,
)


class GoldBarV2Strategy:
    def __init__(self, bars, *, candidate, quantity, entry_timeout_ms, max_quote_age_ms,
                 entry_not_before_ms=None):
        if (candidate not in CANDIDATES or type(quantity) is not Decimal
                or not quantity.is_finite() or quantity <= 0
                or any(type(v) is not int or v <= 0
                       for v in (entry_timeout_ms, max_quote_age_ms))):
            raise ValueError('INVALID_GOLD_BAR_PLAN')
        if entry_not_before_ms is not None and (type(entry_not_before_ms) is not int
                                               or not 0 <= entry_not_before_ms <= 2**63 - 1):
            raise ValueError('INVALID_GOLD_ENTRY_START')
        self.entry_not_before_ms = entry_not_before_ms
        self.bars = tuple(bars)
        evaluate(self.bars, candidate=candidate, as_of_ms=0)
        self._signal_engine = causal_evaluator(self.bars)
        self.candidate, self.quantity = candidate, quantity
        self.entry_timeout, self.max_age = entry_timeout_ms, max_quote_age_ms
        self.last_time = None
        self.consumed = set()
        self.entry_id = self.exit_id = None
        self.exit_attempts = 0
        self.exit_quote_time = None
        self.distance = self.stop = self.opened_at = None
        self.expected_side = None
        self.events = []
        self.observations = []

    def append_bars(self, bars):
        """Append received closed bars without replacing strategy/position state."""
        incoming = tuple(bars)
        if not incoming:
            raise ValueError('EMPTY_GOLD_BAR_APPEND')
        combined = self.bars + incoming
        incremental = self._signal_engine is not None and causal_receipts(
            incoming, after=self._signal_engine._last_available)
        if not incremental:
            evaluate(tuple(combined), candidate=self.candidate, as_of_ms=0)
        if self.last_time is not None and any(b.available_ms <= self.last_time for b in incoming):
            raise ValueError('BAR_RECEIPT_ALREADY_PROCESSED')
        if incremental:
            self._signal_engine.append_bars(incoming)
        else:
            self._signal_engine = None
        if isinstance(self.bars, tuple):
            self.bars = combined
        else:
            self.bars.append_validated(incoming)

    def __call__(self, ctx):
        if not isinstance(ctx, SimulationContext):
            raise ValueError('SIMULATION_CONTEXT_REQUIRED')
        now = ctx.timestamp
        if self.last_time is not None and now <= self.last_time:
            raise ValueError('STRATEGY_TIME_NOT_INCREASING')
        self.last_time = now
        if any(symbol != 'XAUUSDT' for symbol in ctx.positions):
            raise ValueError('UNEXPECTED_POSITION')
        owned = {self.entry_id, self.exit_id} - {None}
        if set(ctx.open_orders) - owned:
            raise ValueError('UNEXPECTED_OPEN_ORDER')
        position = ctx.positions.get('XAUUSDT')
        amount = position.quantity if position else Decimal(0)
        signal = evaluate(self._signal_engine if self._signal_engine is not None else tuple(self.bars),
                          candidate=self.candidate, as_of_ms=now)
        self.observations.append(dict(timestamp=now, status=signal['status'],
                                      entry_side=signal['entry_side']))
        # One attempt per bar, including rejection, expiry and same-bar exits.
        if self._signal_engine is not None:
            window = self._signal_engine._window
            bar_key = window[-1].close_ms if window else None
        else:
            available = [b for b in self.bars if b.available_ms <= now]
            bar_key = available[-1].close_ms if available else None
        fresh_signal = signal['status'] == 'READY' and bar_key not in self.consumed
        if fresh_signal:
            self.consumed.add(bar_key)
        q = ctx.quotes.get('XAUUSDT')
        fresh_quote = q is not None and 0 <= now - q.event_time <= self.max_age
        if amount:
            if self.entry_id is None or (amount > 0) != (self.expected_side == 'BUY'):
                raise ValueError('UNOWNED_GOLD_POSITION')
            if self.opened_at is None:
                self.opened_at = now
                self.stop = (position.average_entry_price - self.distance if amount > 0
                             else position.average_entry_price + self.distance)
            if self.exit_id in ctx.open_orders:
                return ()
            if not fresh_quote:
                return ()
            retry = self.exit_id is not None
            if retry and q.event_time <= self.exit_quote_time:
                return ()
            stop = q.bid_price <= self.stop if amount > 0 else q.ask_price >= self.stop
            timed = now >= self.opened_at + signal['max_holding_bars'] * INTERVAL_MS
            reverse = fresh_signal and signal['exit_long' if amount > 0 else 'exit_short']
            if not (retry or stop or timed or reverse):
                return ()
            # A terminated reduction does not clear the exit decision. Retry on
            # a new quote using actual remaining inventory through normal gates.
            self.exit_attempts += 1
            suffix = '' if self.exit_attempts == 1 else '-' + str(self.exit_attempts)
            self.exit_id = self.entry_id + '-exit' + suffix
            self.exit_quote_time = q.event_time
            actions = [CancelOrder(self.entry_id)] if self.entry_id in ctx.open_orders else []
            actions.append(OrderIntent(self.exit_id, 'XAUUSDT', -amount, reduce_only=True))
            self.events.append(dict(timestamp=now, reason='EXIT_RETRY' if retry else
                'STOP' if stop else 'TIMEOUT' if timed else 'SIGNAL_EXIT'))
            return tuple(actions)
        if ctx.open_orders:
            return ()
        if self.entry_id is not None:
            # Do not reverse in the same callback that observes an exit or rejection.
            self.entry_id = self.exit_id = self.opened_at = self.stop = None
            self.exit_attempts = 0
            self.exit_quote_time = None
            return ()
        if (not fresh_signal or not fresh_quote or signal['entry_side'] is None
                or self.entry_not_before_ms is not None and now < self.entry_not_before_ms):
            return ()
        self.distance = Decimal(signal['stop_distance'])
        self.expected_side = signal['entry_side']
        entry_quote = q.ask_price if self.expected_side == 'BUY' else q.bid_price
        if self.distance >= entry_quote:
            return ()
        self.entry_id = 'gold-bar-' + signal['decision_id']
        self.events.append(dict(timestamp=now, reason='ENTRY_INTENT', decision=signal))
        return (OrderIntent(self.entry_id, 'XAUUSDT', self.quantity if self.expected_side == 'BUY'
                            else -self.quantity, expires_at=now + self.entry_timeout),)
