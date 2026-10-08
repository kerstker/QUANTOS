"""Receipt-time xStocks display units; never historical valuation or order sizing."""
from decimal import localcontext

from services.data.kraken_public import decimal, require, result


def canonical(value):
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def asset_units(payload, *, spec, received_at_ms, as_of_ms):
    require(type(received_at_ms) is int and type(as_of_ms) is int and
            0 < received_at_ms <= as_of_ms <= received_at_ms + 120000, 'KRAKEN_UNITS_TIME')
    asset_class = spec['asset_class']
    require(asset_class in ('currency', 'tokenized_asset'), 'KRAKEN_UNITS_CLASS')
    rows = result(payload)
    require(set(rows) == {spec['base']}, 'KRAKEN_UNITS_IDENTITY')
    row = rows[spec['base']]
    require(isinstance(row, dict) and row.get('aclass') == asset_class, 'KRAKEN_UNITS_CLASS')
    for key in ('decimals', 'display_decimals'):
        require(type(row.get(key)) is int and 0 <= row[key] <= 18, 'KRAKEN_UNITS_PRECISION')
    require(row.get('status') == 'enabled', 'KRAKEN_UNITS_DISABLED')
    multiplier = decimal(row.get('token_multiplier')) if asset_class == 'tokenized_asset' else decimal('1')
    return {'instrument_id': spec['instrument_id'], 'base': spec['base'],
            'asset_class': asset_class, 'token_multiplier': canonical(multiplier),
            'decimals': row['decimals'], 'display_decimals': row['display_decimals'],
            'display_basis': 'UNDERLYING_EQUITY' if asset_class == 'tokenized_asset' else 'NATIVE_ASSET',
            'received_at_ms': received_at_ms, 'as_of_ms': as_of_ms,
            'scope': 'CURRENT_DISPLAY_UNITS_ONLY', 'submission_enabled': False, 'authoritative': False}


def convert_quantity(quantity, payload, *, spec, received_at_ms, as_of_ms):
    metadata = asset_units(payload, spec=spec, received_at_ms=received_at_ms, as_of_ms=as_of_ms)
    base = decimal(quantity, positive=False)
    with localcontext() as context:
        context.prec = 100
        display = base * decimal(metadata['token_multiplier'])
    return dict(metadata, base_quantity=canonical(base), display_quantity=canonical(display))
