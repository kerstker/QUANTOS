"""Exact, bounded public filter semantics; not a venue execution certification."""
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from math import gcd
from functools import reduce

from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context

SCHEMA = 'BINANCE_USDM_DECLARED_FILTERS_V1'


def _number(value, *, zero=False):
    if not isinstance(value, Decimal) or not value.is_finite() or (value < 0 if zero else value <= 0):
        raise ValueError('finite nonnegative filter amount required' if zero else 'positive filter amount required')
    parts = value.as_tuple()
    if len(parts.digits) > 24 or parts.exponent < -24 or value.adjusted() > 24:
        raise ValueError('filter amount outside supported exact arithmetic range')
    return value


def _exact(value: Fraction) -> Decimal:
    # Filter constants are bounded; the private ledger context makes rounding
    # independent of the caller. Round-tripping rejects nonrepresentable values.
    with ledger_decimal_context():
        result = Decimal(value.numerator) / Decimal(value.denominator)
    if Fraction(result) != value:
        raise ValueError('filter result exceeds exact decimal context')
    return result


@dataclass(frozen=True)
class PriceFilter:
    minimum: Decimal
    maximum: Decimal
    step: Decimal

    def __post_init__(self):
        for value in (self.minimum, self.maximum, self.step):
            _number(value, zero=True)
        if self.maximum and self.minimum > self.maximum:
            raise ValueError('empty public price interval')

    def reason(self, price: Decimal):
        if (self.minimum and price < self.minimum) or (self.maximum and price > self.maximum):
            return 'PRICE_RANGE'
        if self.step and (Fraction(price) - Fraction(self.minimum)) % Fraction(self.step):
            return 'PRICE_TICK'
        return None

    def adverse_price(self, price: Decimal, *, buy: bool):
        if not self.step:
            return price
        units = (Fraction(price) - Fraction(self.minimum)) / Fraction(self.step)
        whole = -(-units.numerator // units.denominator) if buy else units.numerator // units.denominator
        return _exact(Fraction(self.minimum) + whole * Fraction(self.step))


@dataclass(frozen=True)
class QuantityFilter:
    minimum: Decimal
    maximum: Decimal
    step: Decimal

    def __post_init__(self):
        for value in (self.minimum, self.maximum, self.step):
            _number(value)
        if self.minimum > self.maximum:
            raise ValueError('empty public quantity interval')

    def reason(self, quantity: Decimal):
        amount = quantity.copy_abs()
        if amount > self.maximum:
            return 'MAX_ORDER_QUANTITY'
        if amount < self.minimum:
            return 'MIN_ORDER_QUANTITY'
        if (Fraction(amount) - Fraction(self.minimum)) % Fraction(self.step):
            return 'QUANTITY_STEP'
        return None


@dataclass(frozen=True)
class VenueOrderFilters:
    price: PriceFilter
    limit: QuantityFilter
    market: QuantityFilter

    def __post_init__(self):
        if not isinstance(self.price, PriceFilter) or not all(isinstance(f, QuantityFilter) for f in (self.limit, self.market)):
            raise ValueError('typed public price and quantity filters required')

    @property
    def quantity_atom(self):
        values = tuple(Fraction(v) for f in (self.limit, self.market) for v in (f.minimum, f.step))
        denominator = reduce(lambda a, b: a * b // gcd(a, b), (v.denominator for v in values))
        numerator = reduce(gcd, (v.numerator * (denominator // v.denominator) for v in values))
        return _exact(Fraction(numerator, denominator))

    def quantity_reason(self, quantity: Decimal, *, market: bool):
        # LOT_SIZE is general; MARKET_LOT_SIZE adds a conservative constraint.
        # Actual matching-engine precedence still requires private receipts.
        return ((self.market.reason(quantity) if market else None) or self.limit.reason(quantity))

    def manifest(self):
        def row(f):
            return {name: canonical_decimal(getattr(f, name)) for name in ('minimum', 'maximum', 'step')}
        return {'schema': SCHEMA, 'price': row(self.price), 'lot_size': row(self.limit),
            'market_lot_size': row(self.market), 'quantity_atom': canonical_decimal(self.quantity_atom),
            'grid_origin': 'FILTER_MINIMUM', 'market_application': 'LOT_SIZE_AND_MARKET_LOT_SIZE',
            'zero_price_fields': 'DISABLE_INDIVIDUAL_CONSTRAINT',
            'partial_fills': 'ACCOUNTING_ATOM_NOT_PARENT_ORDER_GRID', 'venue_behavior_verified': False}


def parse_filters(symbol):
    rows = symbol['filters']
    if (not isinstance(rows, list) or not 1 <= len(rows) <= 128
            or any(not isinstance(row, dict) or not isinstance(row.get('filterType'), str) for row in rows)):
        raise ValueError('bounded typed public filters required')
    values = {row['filterType']: row for row in rows}
    if len(values) != len(rows):
        raise ValueError('duplicate order filter')
    def number(row, field):
        value = row[field]
        if not isinstance(value, str) or not 1 <= len(value) <= 64:
            raise ValueError('exact public decimal string required')
        try:
            return Decimal(value)
        except ArithmeticError as exc:
            raise ValueError('invalid public decimal string') from exc
    price = values['PRICE_FILTER']
    def quantity(kind):
        row = values[kind]
        return QuantityFilter(*(number(row, key) for key in ('minQty', 'maxQty', 'stepSize')))
    return VenueOrderFilters(PriceFilter(*(number(price, key) for key in ('minPrice', 'maxPrice', 'tickSize'))),
        quantity('LOT_SIZE'), quantity('MARKET_LOT_SIZE'))
