"""Compile a preselected relative price recipe from one prior public quote."""
from copy import deepcopy
from decimal import Decimal, localcontext

PRICE_BOUNDS = {'zone_lower_bps': (-500, -1), 'zone_upper_bps': (0, 100),
                'stop_bps': (-1000, -2), 'target_bps': (1, 2000)}


def validate_price_design(value):
    bounds = {'window_ms': (1000, 15000), 'max_age_ms': (100, 5000),
              'window_count': (2, 6), **PRICE_BOUNDS}
    if (type(value) is not dict or set(value) != set(bounds)
            or any(type(value[k]) is not int or not low <= value[k] <= high
                   for k, (low, high) in bounds.items())
            or not value['stop_bps'] < value['zone_lower_bps'] < value['zone_upper_bps'] < value['target_bps']):
        raise ValueError('INVALID_GOLD_PRICE_DESIGN')
    return dict(value)


def compile_prices(design, *, document, plan, anchor, designed_at_ms):
    """Caller supplies the first capture frame; never search later prices for fit."""
    from services.execution.perpetual_simulation import SimulationFrame
    design = validate_price_design(design)
    if (not isinstance(anchor, SimulationFrame) or len(anchor.quotes) != 1
            or type(designed_at_ms) is not int or designed_at_ms < 0
            or not designed_at_ms < anchor.timestamp <= document['start_ms']):
        raise ValueError('INVALID_GOLD_PRICE_ANCHOR_TIME')
    quote = anchor.quotes[0]
    if quote.instrument_id != 'XAUUSDT' or quote.available_time > anchor.timestamp:
        raise ValueError('INVALID_GOLD_PRICE_ANCHOR')
    document, plan = deepcopy((document, plan))
    with localcontext() as context:
        context.prec = 50
        mid = (quote.bid_price + quote.ask_price) / 2
        prices = {k: mid*(Decimal(10000)+design[k])/10000 for k in PRICE_BOUNDS}
    document['zone'] = {'lower': str(prices['zone_lower_bps']),
        'upper': str(prices['zone_upper_bps']), 'registered_at_ms': anchor.timestamp,
        'expires_at_ms': document['start_ms'] + design['window_ms']*design['window_count'] + design['max_age_ms'] + 1}
    plan.update(stop=prices['stop_bps'], target=prices['target_bps'])
    return document, plan
