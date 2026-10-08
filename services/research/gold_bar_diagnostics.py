"""Cost and risk projections of the canonical simulation, never qualification."""
from decimal import Decimal

from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context


def _number(value):
    if type(value) is not str or not 1 <= len(value) <= 256:
        raise ValueError('INVALID_GOLD_ACCOUNT_DIAGNOSTIC')
    result = Decimal(value)
    if not result.is_finite() or result and not -96 <= result.adjusted() <= 96:
        raise ValueError('INVALID_GOLD_ACCOUNT_DIAGNOSTIC')
    return result


@ledger_decimal_context()
def account_diagnostics(simulation):
    state = simulation['ending_state']
    identity = state['cashflow_identity']
    initial, ending, change, residual = (_number(identity[k]) for k in
        ('initial_equity', 'ending_equity', 'attributed_change', 'residual'))
    history = simulation['equity_history']
    if (residual != 0 or ending - initial != change or not history
            or _number(state['account_equity']) != ending
            or _number(history[0]['equity']) != initial
            or _number(history[-1]['equity']) != ending):
        raise ValueError('GOLD_ACCOUNT_DIAGNOSTIC_NOT_RECONCILED')
    drawdowns = [_number(row['drawdown']) for row in history]
    if min(drawdowns) < 0:
        raise ValueError('INVALID_GOLD_ACCOUNT_DRAWDOWN')
    _number(identity['fee'])
    _number(identity['funding'])
    return dict(currency='USDT', initial_equity=identity['initial_equity'],
        ending_equity=identity['ending_equity'], net_equity_change=identity['attributed_change'],
        fees=identity['fee'], funding=identity['funding'],
        max_observed_drawdown=canonical_decimal(max(drawdowns)),
        reconciliation_residual=identity['residual'],
        valuation='MARK_TO_MARKET_INCLUDES_OPEN_POSITIONS')


@ledger_decimal_context()
def compact_diagnostic(value):
    """Bounded model summary; exact decimals remain in the hash-bound report."""
    return None if value is None else format(_number(value), '.5g')


_TARGET_COUNTS = ('all_zero_count', 'opposed_zero_count',
                  'opposed_nonzero_count', 'unopposed_nonzero_count')


def validate_target_diagnostics(value):
    """Counts describe strategy observations, not trades or elapsed time."""
    if (type(value) is not dict
            or set(value) != {'schema', 'measure', 'observation_count', 'economic_eligible', *_TARGET_COUNTS}
            or value['schema'] != 'GOLD_TARGET_NETTING_DIAGNOSTIC_V1'
            or value['measure'] != 'STRATEGY_TARGET_OBSERVATIONS_NOT_TRADES'
            or value['economic_eligible'] is not False
            or any(type(value[k]) is not int or not 0 <= value[k] <= 1000000
                   for k in ('observation_count', *_TARGET_COUNTS))
            or sum(value[k] for k in _TARGET_COUNTS) != value['observation_count']):
        raise ValueError('INVALID_GOLD_TARGET_DIAGNOSTIC')
    return dict(value)


@ledger_decimal_context()
def target_diagnostics(trace):
    """Project original target trace without inferring fills or component P&L."""
    if type(trace) is not list or len(trace) > 1000000:
        raise ValueError('INVALID_GOLD_TARGET_TRACE')
    counts = dict.fromkeys(_TARGET_COUNTS, 0)
    previous = -1
    for row in trace:
        if (type(row) is not dict
                or set(row) != {'timestamp', 'desired', 'net_target', 'actual_quantity'}
                or type(row['timestamp']) is not int or row['timestamp'] <= previous
                or type(row['desired']) is not dict
                or set(row['desired']) != {'channel', 'trend', 'reversion'}):
            raise ValueError('INVALID_GOLD_TARGET_TRACE')
        previous = row['timestamp']
        values = [_number(v) for v in row['desired'].values()]
        net = _number(row['net_target'])
        _number(row['actual_quantity'])
        if sum(values, Decimal(0)) != net:
            raise ValueError('GOLD_TARGET_NET_MISMATCH')
        opposing = any(v > 0 for v in values) and any(v < 0 for v in values)
        key = ('opposed_nonzero_count' if net else 'opposed_zero_count') if opposing else (
            'unopposed_nonzero_count' if net else 'all_zero_count')
        counts[key] += 1
    return validate_target_diagnostics(dict(schema='GOLD_TARGET_NETTING_DIAGNOSTIC_V1',
        measure='STRATEGY_TARGET_OBSERVATIONS_NOT_TRADES', observation_count=len(trace),
        economic_eligible=False, **counts))
