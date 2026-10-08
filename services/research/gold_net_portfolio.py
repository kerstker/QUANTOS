"""Fixed gold target ensemble on one original cash/margin/funding ledger.

Targets are research preferences, never synthetic component fills or component
P&L. Opposing preferences cancel before execution. Stop references are observed
quotes at preference creation, explicitly distinct from actual fill prices.
"""
from decimal import Decimal
from dataclasses import asdict
from collections.abc import Sequence
import copy

from quantos.contracts.gold_net_allocation import POLICY as NET_ALLOCATION_POLICY
from services.execution.backtest_authority import exact_identity_hash
from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context
from services.execution.perpetual_simulation import (
    CancelOrder, OrderIntent, RiskVerdict, SimulationCostModel, run_linear_perpetual_simulation,
)
from services.research.gold_bar_candidates import (
    CANDIDATES, INTERVAL_MS, causal_evaluator, causal_receipts, evaluate,
)
from services.research.gold_loss_control import GoldResearchLossControl
from services.research.gold_tape_execution import execute

ZERO = Decimal(0)


class _BarIdentity(Sequence):
    def __init__(self, bars):
        self.bars = bars

    def __len__(self):
        return len(self.bars)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return _BarIdentity(self.bars[index])
        return {k: str(v) if isinstance(v, Decimal) else v
                for k, v in asdict(self.bars[index]).items()}

    def __iter__(self):
        for bar in self.bars:
            yield {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(bar).items()}

    def __deepcopy__(self, memo):
        return _BarIdentity(copy.deepcopy(self.bars, memo))


class GoldNetTargetPortfolio:
    def __init__(self, bars, *, quantities, max_notional, entry_not_before_ms=None):
        if (type(quantities) is not dict or set(quantities) != set(CANDIDATES)
                or any(type(v) is not Decimal or not v.is_finite() or v <= 0 for v in quantities.values())
                or type(max_notional) is not Decimal or not max_notional.is_finite() or max_notional <= 0
                or entry_not_before_ms is not None and (type(entry_not_before_ms) is not int or entry_not_before_ms < 0)):
            raise ValueError('INVALID_GOLD_PORTFOLIO_BUDGET')
        self.bars = tuple(bars)
        self._signal_engine = causal_evaluator(self.bars)
        self.quantities = dict(quantities)
        self.max_notional = max_notional
        self.entry_not_before_ms = entry_not_before_ms
        self.targets = {name: ZERO for name in CANDIDATES}
        self.references = {}
        self.consumed = {}
        self.orders = set()
        self.last_time = None
        self.last_order_quote = None
        self.trace = []
        self.allocation_intents = []
        self.identity = dict(version='GOLD_FIXED_NET_TARGET_PORTFOLIO_V2', net_allocation_policy=copy.deepcopy(NET_ALLOCATION_POLICY),
            quantities={n: canonical_decimal(quantities[n]) for n in CANDIDATES},
            max_notional=canonical_decimal(max_notional), entry_not_before_ms=entry_not_before_ms,
            max_quote_age_ms=30000, order_expiry_ms=5000,
            stop_reference='OBSERVED_QUOTE_AT_TARGET_CREATION_NOT_FILL',
            opposing_targets='NET_BEFORE_EXECUTION', reversal='REDUCE_TO_FLAT_BEFORE_OPENING',
            separate_strategy_pnl_computed=False)

    def append_bars(self, bars):
        """Append newly received bars while retaining targets and order ownership."""
        incoming = tuple(bars)
        if not incoming:
            raise ValueError('EMPTY_GOLD_BAR_APPEND')
        combined = self.bars + incoming
        incremental = self._signal_engine is not None and causal_receipts(
            incoming, after=self._signal_engine._last_available)
        if not incremental:
            evaluate(tuple(combined), candidate=CANDIDATES[0], as_of_ms=0)
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

    @ledger_decimal_context()
    def risk(self, ctx, intent):
        if intent.instrument_id != 'XAUUSDT':
            return RiskVerdict(False, 'PORTFOLIO_MARKET_SCOPE')
        position = ctx.positions.get('XAUUSDT')
        current = position.quantity if position else ZERO
        if intent.reduce_only and current * intent.quantity_delta < 0 and abs(intent.quantity_delta) <= abs(current):
            return RiskVerdict(True)
        quote = ctx.quotes.get('XAUUSDT')
        if quote is None or not 0 <= ctx.timestamp - quote.event_time <= 30000:
            return RiskVerdict(False, 'PORTFOLIO_QUOTE_UNAVAILABLE')
        target = current + intent.quantity_delta
        price = max(quote.ask_price, quote.bid_price)
        if abs(target) > sum(self.quantities.values(), ZERO) or abs(target) * price > self.max_notional:
            return RiskVerdict(False, 'PORTFOLIO_SHARED_EXPOSURE_LIMIT')
        return RiskVerdict(True)

    @ledger_decimal_context()
    def __call__(self, ctx):
        if (self.last_time is not None and ctx.timestamp <= self.last_time
                or any(name != 'XAUUSDT' for name in ctx.positions)
                or set(ctx.open_orders) - self.orders):
            raise ValueError('PORTFOLIO_ACCOUNT_OR_CLOCK_CHANGED')
        self.last_time = ctx.timestamp
        quote = ctx.quotes.get('XAUUSDT')
        if quote is None or not 0 <= ctx.timestamp - quote.event_time <= 30000:
            return ()
        for name in CANDIDATES:
            signal = evaluate(self._signal_engine if self._signal_engine is not None else tuple(self.bars),
                              candidate=name, as_of_ms=ctx.timestamp)
            key = signal.get('input_sha256', signal.get('decision_id'))
            fresh = signal['status'] == 'READY' and key is not None and self.consumed.get(name) != key
            if fresh:
                self.consumed[name] = key
            target = self.targets[name]
            if target:
                reference, stop, opened, holding = self.references[name]
                price = quote.bid_price if target > 0 else quote.ask_price
                stopped = price <= reference - stop if target > 0 else price >= reference + stop
                if (stopped or ctx.timestamp >= opened + holding * INTERVAL_MS
                        or fresh and signal['exit_long' if target > 0 else 'exit_short']):
                    self.targets[name] = ZERO
                continue
            if (fresh and signal['entry_side'] is not None
                    and (self.entry_not_before_ms is None or ctx.timestamp >= self.entry_not_before_ms)):
                distance = Decimal(signal['stop_distance'])
                price = quote.ask_price if signal['entry_side'] == 'BUY' else quote.bid_price
                if not distance.is_finite() or not ZERO < distance < price:
                    continue
                self.targets[name] = self.quantities[name] * (1 if signal['entry_side'] == 'BUY' else -1)
                self.references[name] = (price, distance, ctx.timestamp, signal['max_holding_bars'])
        target = sum(self.targets.values(), ZERO)
        position = ctx.positions.get('XAUUSDT')
        current = position.quantity if position else ZERO
        self.trace.append(dict(timestamp=ctx.timestamp,
            desired={n: canonical_decimal(self.targets[n]) for n in CANDIDATES},
            net_target=canonical_decimal(target), actual_quantity=canonical_decimal(current)))
        actions = []
        for order_id, order in ctx.open_orders.items():
            if current + order.remaining_quantity == target:
                return ()
            actions.append(CancelOrder(order_id))
        if actions:
            return tuple(actions)
        quote_id = (quote.event_time, quote.available_time)
        if current == target or self.last_order_quote == quote_id:
            return ()
        delta = target - current
        reducing = current != 0 and (current * target <= 0 or abs(target) < abs(current))
        if current * target < 0:
            delta = -current
        self.last_order_quote = quote_id
        order_id = 'gold-portfolio-' + str(len(self.orders) + 1)
        self.orders.add(order_id)
        self.allocation_intents.append(dict(timestamp=ctx.timestamp, order_id=order_id,
            desired={n:canonical_decimal(self.targets[n]) for n in CANDIDATES},
            net_target=canonical_decimal(target), actual_quantity=canonical_decimal(current)))
        return (OrderIntent(order_id, 'XAUUSDT', delta, reduce_only=reducing,
                            expires_at=ctx.timestamp + 5000),)


def compare_portfolio(*, bars, prepared, release_identity, quantities, initial_collateral,
                      fee_rate, max_notional, entry_not_before_ms=None, observation_binding=None,
                      incremental_bars=False, history=None):
    if type(fee_rate) is not Decimal or not fee_rate.is_finite() or fee_rate <= 0:
        raise ValueError('EXPLICIT_PORTFOLIO_COST_REQUIRED')
    if history is None:
        bars = tuple(bars)
    initial_bars = bars
    if incremental_bars:
        if any(a.available_ms > b.available_ms for a, b in zip(bars, bars[1:])):
            raise ValueError('BAR_RECEIPT_ORDER_REQUIRED_FOR_INCREMENTAL_REPLAY')
        initial_bars = tuple(b for b in bars if b.available_ms <= prepared['frames'][0].timestamp)
    strategy = GoldNetTargetPortfolio(initial_bars, quantities=quantities, max_notional=max_notional,
                                      entry_not_before_ms=entry_not_before_ms)
    control = GoldResearchLossControl(strategy, initial_equity=initial_collateral)
    def risk(ctx, intent):
        verdict = control.risk(ctx, intent)
        return strategy.risk(ctx, intent) if verdict.approved else verdict
    configuration = dict(
        **{k: prepared[k] for k in ('frames','contract_rules','funding_policies','order_rule_profiles')},
        release_identity=release_identity, dataset_identity=dict(quote_manifest=prepared['manifest'], observation_binding=observation_binding,
            bars=([{k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(b).items()} for b in bars]
                  if history is None else _BarIdentity(bars))),
        strategy_identity=strategy.identity, strategy=control, initial_collateral=initial_collateral,
        costs=SimulationCostModel(fee_rate=fee_rate), risk_policy=risk,
        risk_policy_identity=dict(loss=control.identity, exposure=strategy.identity))
    for key in ('environment_updates', 'replay_budget'):
        if key in prepared:
            configuration[key] = prepared[key]
    if incremental_bars:
        simulation = execute(configuration, strategy, bars, initial_bars, history=history)
    else:
        simulation = (run_linear_perpetual_simulation(**configuration) if history is None
                      else history.run(configuration, strategy=strategy))
    result = dict(schema='GOLD_NET_PORTFOLIO_COMPARISON_V1', construction=strategy.identity,
        simulation=simulation, target_trace=strategy.trace, allocation_intents=strategy.allocation_intents, loss_control=control.report(simulation),
        economic_eligible=False, authoritative=False, submission_enabled=False,
        limitations=['FIXED_RESEARCH_TARGET_ENSEMBLE_NOT_SELECTED_STRATEGY',
            'NET_ALLOCATION_CONVENTION_NOT_INDEPENDENT_STRATEGY_PNL',
            'TARGET_STOP_REFERENCES_ARE_NOT_EXECUTION_PRICES', *prepared['manifest']['limitations']])
    from quantos.streaming_identity import exact_stream_identity_hash
    hash_result = exact_identity_hash if history is None else exact_stream_identity_hash
    result['result_hash'] = hash_result(result, name='gold_net_portfolio')
    return result
