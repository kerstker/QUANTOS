"""Offline USD-M public order constraints from already bound exchange metadata."""
from decimal import Decimal

from services.execution.perpetual_ledger_v2 import canonical_decimal
from services.execution.perpetual_simulation import VenueOrderConstraints


def parse_constraints(symbol):
    """A legacy absent group stays unknown; a partial group is never ignored."""
    filters = {row['filterType']:row for row in symbol['filters']}
    if len(filters) != len(symbol['filters']):
        raise ValueError('duplicate order filter')
    presence = ('PERCENT_PRICE' in filters, 'MAX_NUM_ORDERS' in filters, 'marketTakeBound' in symbol)
    if not any(presence):
        return None
    if not all(presence):
        raise ValueError('incomplete public dynamic order constraints')
    percent = filters['PERCENT_PRICE']
    values = (percent['multiplierUp'], percent['multiplierDown'], symbol['marketTakeBound'])
    if any(not isinstance(value, str) for value in values):
        raise ValueError('public order bounds require exact decimal strings')
    return VenueOrderConstraints(Decimal(values[0]), Decimal(values[1]),
        filters['MAX_NUM_ORDERS']['limit'], Decimal(values[2]))


def constraint_manifest(constraints):
    if constraints is None:
        return None
    return {
        'scope':'ISOLATED_DECLARED_ORDERS_ONLY',
        'percent_price_up':canonical_decimal(constraints.percent_price_up),
        'percent_price_down':canonical_decimal(constraints.percent_price_down),
        'max_open_orders':constraints.max_open_orders,
        'market_take_bound':canonical_decimal(constraints.market_take_bound),
        'limit_band_reference':'CURRENT_MARK_AT_ADMISSION',
        'market_notional_reference':'CURRENT_MARK_AT_ADMISSION',
        'market_protection':'CURRENT_MARK_AT_EACH_FILL_REJECT_REMAINDER_OUTSIDE_BOUND',
        'venue_behavior_verified':False,
    }
