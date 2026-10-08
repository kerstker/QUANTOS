"""Latched five-percent initial-equity stop on the original research ledger.

This controls one simulated account, not a Demo mandate or a cross-window
portfolio. All cancellations/reductions still pass through the V2 order engine.
"""
from decimal import Decimal

from quantos.contracts import gold_loss_observation as observation
from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context
from services.execution.perpetual_simulation import CancelOrder, OrderIntent, RiskVerdict, SimulationContext


class GoldResearchLossControl:
    @ledger_decimal_context()
    def __init__(self, strategy, *, initial_equity, max_quote_age_ms=30000):
        if (not callable(strategy) or type(initial_equity) is not Decimal
                or not initial_equity.is_finite() or initial_equity <= 0
                or type(max_quote_age_ms) is not int or not 1 <= max_quote_age_ms <= 30000):
            raise ValueError('INVALID_GOLD_RESEARCH_LOSS_CONTROL')
        self.strategy = strategy
        self.initial = initial_equity
        self.threshold = initial_equity * Decimal('0.95')
        self.max_age = max_quote_age_ms
        self.identity = dict(version='GOLD_RESEARCH_INITIAL_EQUITY_LOSS_V1',
            initial_equity=canonical_decimal(initial_equity), max_loss_fraction='0.05',
            scope='ONE_SIMULATED_ACCOUNT_PER_TAPE', trigger_basis='MARKED_EQUITY_AFTER_ACTUAL_LEDGER_EVENTS',
            max_quote_age_ms=max_quote_age_ms)
        self.trigger = None
        self._seen = 0
        self._observed_digest = observation.new_digest()
        self._exit_quote = None
        self._exit_attempts = 0

    def _observe(self, ctx):
        if (not isinstance(ctx, SimulationContext) or not ctx.history
                or len(ctx.history) < self._seen or ctx.history[0].equity != self.initial
                or any(name != 'XAUUSDT' for name in ctx.positions)):
            raise ValueError('GOLD_LOSS_CONTROL_ACCOUNT_CHANGED')
        for i in range(self._seen, len(ctx.history)):
            point = ctx.history[i]
            if point.sequence != i:
                raise ValueError('GOLD_LOSS_CONTROL_HISTORY_SEQUENCE')
            observation.update_digest(self._observed_digest, timestamp=point.timestamp,
                sequence=point.sequence, phase=point.phase, equity=point.equity)
            if self.trigger is None and point.equity <= self.threshold:
                self.trigger = dict(timestamp=point.timestamp, sequence=point.sequence,
                    equity=canonical_decimal(point.equity), reason='CUMULATIVE_LOSS_LIMIT')
        self._seen = len(ctx.history)

    @ledger_decimal_context()
    def risk(self, ctx, intent):
        # Called by the original engine at admission and before committing fills;
        # this also stops previously pending opening orders after a funding/mark loss.
        self._observe(ctx)
        if self.trigger is None:
            return RiskVerdict(True)
        position = ctx.positions.get(intent.instrument_id)
        reducing = (intent.reduce_only and position is not None
            and (position.quantity > 0) != (intent.quantity_delta > 0)
            and abs(intent.quantity_delta) <= abs(position.quantity))
        return RiskVerdict(bool(reducing), None if reducing else 'GOLD_CUMULATIVE_LOSS_LIMIT')

    @ledger_decimal_context()
    def __call__(self, ctx):
        self._observe(ctx)
        if self.trigger is None:
            return self.strategy(ctx)
        position = ctx.positions.get('XAUUSDT')
        actions = []
        pending_reduction = False
        for order_id, state in ctx.open_orders.items():
            valid_exit = (position is not None and state.intent.instrument_id == 'XAUUSDT'
                and state.intent.reduce_only and (state.remaining_quantity > 0) != (position.quantity > 0)
                and abs(state.remaining_quantity) <= abs(position.quantity))
            if valid_exit:
                pending_reduction = True
            else:
                actions.append(CancelOrder(order_id))
        if position is None or pending_reduction:
            return tuple(actions)
        quote = ctx.quotes.get('XAUUSDT')
        if quote is None or not 0 <= ctx.timestamp - quote.event_time <= self.max_age:
            return tuple(actions)
        identity = (quote.event_time, quote.available_time)
        if identity == self._exit_quote:
            return tuple(actions)
        self._exit_quote = identity
        self._exit_attempts += 1
        actions.append(OrderIntent('gold-loss-stop-' + str(self._exit_attempts), 'XAUUSDT',
                                   -position.quantity, reduce_only=True))
        return tuple(actions)

    def report(self, simulation):
        pending = any(row['status'] in ('OPEN', 'PARTIALLY_FILLED') for row in simulation['orders'])
        flat = not simulation['ending_state']['positions'] and not pending
        return dict(schema='GOLD_RESEARCH_LOSS_CONTROL_V2', policy=dict(self.identity),
            status='ACTIVE' if self.trigger is None else 'STOPPED_FLAT' if flat else 'STOPPING_UNRESOLVED',
            trigger=None if self.trigger is None else dict(self.trigger), exit_attempts=self._exit_attempts,
            history_observation=dict(schema=observation.SCHEMA, observed_points=self._seen,
                                     observed_prefix_sha256=self._observed_digest.hexdigest()),
            economic_eligible=False, submission_enabled=False)
