"""Explain a complete canonical replay; never a second ledger or economic gate."""
from decimal import Decimal, InvalidOperation

from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context


def _amount(value):
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError('decimal receipt required')
    result = Decimal(value)
    if not result.is_finite() or abs(result) > Decimal('1e30'):
        raise ValueError('invalid decimal receipt')
    return result


@ledger_decimal_context()
def attribute_replay(result):
    """Re-express embedded fill slippage, checking complete fills against the ledger.

    Gross is a reference-price explanation of the SAME realized quantities, not
    a zero-cost rerun with different sizing. No deduction is applied to the ledger.
    Liquidations/ADL require extra position receipts and remain unknown here.
    """
    empty = dict(schema='QUANTOS_PROFITABILITY_ATTRIBUTION_V1', status='UNKNOWN',
                 evidence_type='TECHNICAL_REPLAY', economic_eligible=False,
                 net_pnl=None, gross_reference_pnl=None, embedded_slippage=None,
                 fees=None, funding=None, turnover_notional=None, turnover_initial_equity=None,
                 position_contribution=None, actual_venue_fees=None, spread_component=None,
                 diagnosis='UNKNOWN', prediction_edge='UNKNOWN', reason='INCOMPLETE_REPLAY_RECEIPTS',
                 effective_independent_samples=None,
                 limitations=['REFERENCE_GROSS_USES_SAME_FILLED_QUANTITIES',
                              'SLIPPAGE_INCLUDES_TICK_ROUNDING_NOT_SEPARATE_SPREAD',
                              'NO_CAUSAL_PREDICTION_EDGE_OR_ECONOMIC_AUTHORITY'])
    try:
        if result['cash_model'] not in ('CANONICAL_LINEAR_USDC_PERPETUAL','CANONICAL_LINEAR_USDT_PERPETUAL'):
            return {**empty, 'reason': 'UNSUPPORTED_LEDGER'}
        r = {k: _amount(result['reconciliation'][k]) for k in
             ('initial_equity', 'ending_equity', 'price_pnl', 'fee', 'spread', 'impact',
              'funding', 'liquidation_fee', 'insurance_adjustment', 'adl_adjustment', 'residual')}
        if r['residual'] != 0 or r['initial_equity'] <= 0:
            return {**empty, 'reason': 'LEDGER_RECONCILIATION_FAILED'}
        if any(r[k] != 0 for k in ('liquidation_fee', 'insurance_adjustment', 'adl_adjustment')):
            return {**empty, 'reason': 'NON_FILL_POSITION_ADJUSTMENT_UNATTRIBUTED'}
        fills = result['fills']; daily = result['daily']
        if not isinstance(fills, list) or not daily:
            raise ValueError('complete fills and closing mark required')
        quantity = fee = slippage = turnover = Decimal(0)
        contribution = {'long': Decimal(0), 'short': Decimal(0)}
        previous_reference = None; previous_time = -1
        for fill in fills:
            at = fill['time']
            if type(at) is not int or at < previous_time or at < daily[0]['time'] or at > daily[-1]['time']:
                raise ValueError('unordered fill receipt')
            delta, price, reference, paid = (_amount(fill[k]) for k in ('quantity', 'price', 'reference', 'fee'))
            if delta == 0 or min(price, reference) <= 0 or paid < 0:
                raise ValueError('invalid fill')
            if quantity and previous_reference is not None:
                contribution['long' if quantity > 0 else 'short'] += quantity * (reference - previous_reference)
            quantity += delta; fee += paid
            slippage += delta * (price - reference)
            turnover += abs(delta) * reference
            previous_reference = reference; previous_time = at
        end_quantity = _amount(daily[-1]['quantity']); end_mark = _amount(daily[-1]['mark'])
        if end_mark <= 0 or quantity != end_quantity or fee != r['fee']:
            return {**empty, 'reason': 'FILL_LEDGER_MISMATCH'}
        if quantity and previous_reference is not None:
            contribution['long' if quantity > 0 else 'short'] += quantity * (end_mark - previous_reference)
        gross = r['price_pnl'] + slippage
        net = r['ending_equity'] - r['initial_equity']
        explained = gross - slippage - fee - r['spread'] - r['impact'] + r['funding']
        if explained != net or sum(contribution.values()) != gross:
            return {**empty, 'reason': 'ATTRIBUTION_RECONCILIATION_FAILED'}
        diagnosis = ('NO_TRADING_EVIDENCE' if not fills else 'NO_POSITIVE_GROSS_EDGE' if gross <= 0 else
                     'POSITIVE_GROSS_CONSUMED_BY_COSTS_OR_FUNDING' if net <= 0 else
                     'POSITIVE_NET_TECHNICAL_RESULT')
        return {**empty, 'status': 'RECONCILED_TECHNICAL_ONLY', 'reason': None,
                'net_pnl': canonical_decimal(net), 'gross_reference_pnl': canonical_decimal(gross),
                'embedded_slippage': canonical_decimal(slippage), 'fees': canonical_decimal(fee),
                'funding': canonical_decimal(r['funding']), 'explicit_spread': canonical_decimal(r['spread']),
                'explicit_impact': canonical_decimal(r['impact']), 'diagnosis': diagnosis,
                'turnover_notional': canonical_decimal(turnover),
                'turnover_initial_equity': canonical_decimal(turnover / r['initial_equity']),
                'position_contribution': {k: canonical_decimal(v) for k, v in contribution.items()},
                'fill_count': len(fills), 'reconciliation_residual': '0'}
    except (KeyError, TypeError, ValueError, InvalidOperation, IndexError):
        return empty
