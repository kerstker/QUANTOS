"""Offline Kraken spot/xStocks identity and committed-bar validation.

Public observation only; listing is not account eligibility or execution authority.
History is first knowable at receipt, never retrospectively at candle open/close.
"""
from decimal import Decimal, InvalidOperation
import re

INTERVALS = (1, 5, 15, 30, 60, 240, 1440, 10080, 21600)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def decimal(value, *, positive=True):
    require(isinstance(value, str) and bool(re.fullmatch(r'\d{1,24}(\.\d{1,18})?', value)), 'KRAKEN_DECIMAL')
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('KRAKEN_DECIMAL') from exc
    require(number.is_finite() and (number > 0 if positive else number >= 0), 'KRAKEN_DECIMAL')
    return number


def result(payload):
    require(isinstance(payload, dict) and payload.get('error') == [] and isinstance(payload.get('result'), dict), 'KRAKEN_RESPONSE')
    return payload['result']


def instrument(payload, *, pair, asset_class):
    require(asset_class in ('currency', 'tokenized_asset'), 'KRAKEN_ASSET_CLASS')
    require(isinstance(pair, str) and bool(re.fullmatch(r'[A-Za-z0-9.]{1,20}/[A-Za-z0-9.]{1,20}', pair)), 'KRAKEN_PAIR')
    matches = [(key, row) for key, row in result(payload).items() if isinstance(row, dict) and row.get('wsname') == pair]
    require(len(matches) == 1, 'KRAKEN_PAIR_IDENTITY')
    key, row = matches[0]
    require(bool(re.fullmatch(r'[A-Za-z0-9./]{1,48}', key)) and row.get('aclass_base') == asset_class, 'KRAKEN_PAIR_CLASS')
    for name in ('base', 'quote', 'altname'):
        require(isinstance(row.get(name), str) and bool(re.fullmatch(r'[A-Za-z0-9.]{1,48}', row[name])), 'KRAKEN_ASSET_IDENTITY')
    for name in ('pair_decimals', 'lot_decimals'):
        require(type(row.get(name)) is int and 0 <= row[name] <= 18, 'KRAKEN_PRECISION')
    require(type(row.get('lot_multiplier')) is int and row['lot_multiplier'] == 1, 'KRAKEN_LOT_MULTIPLIER')
    for name in ('ordermin', 'costmin', 'tick_size'):
        decimal(row.get(name))
    require(isinstance(row.get('status'), str) and bool(re.fullmatch(r'[a-z_]{1,40}', row['status'])), 'KRAKEN_MARKET_STATUS')
    return {'instrument_id': f'KRAKEN:{asset_class}:{key}', 'venue': 'KRAKEN', 'asset_class': asset_class,
            'pair_key': key, 'pair': pair, **{name: row[name] for name in ('base', 'quote', 'altname',
            'pair_decimals', 'lot_decimals', 'lot_multiplier', 'ordermin', 'costmin', 'tick_size', 'status')},
            'account_eligible': None, 'submission_enabled': False, 'authoritative': False}


def closed_rows(payload, *, spec, interval, received_at_ms):
    require(type(interval) is int and interval in INTERVALS, 'KRAKEN_INTERVAL')
    require(type(received_at_ms) is int and received_at_ms > 0, 'KRAKEN_RECEIPT_TIME')
    values = result(payload)
    require(set(values) == {spec['pair_key'], 'last'}, 'KRAKEN_OHLC_IDENTITY')
    require(type(values['last']) is int and values['last'] >= 0, 'KRAKEN_CURSOR')
    bars = values[spec['pair_key']]
    # Observed API responses contain 720 committed rows plus one current row.
    require(isinstance(bars, list) and 2 <= len(bars) <= 721, 'KRAKEN_OHLC_SIZE')
    rows, previous = [], -1
    for index, bar in enumerate(bars):
        require(isinstance(bar, list) and len(bar) == 8, 'KRAKEN_OHLC_SHAPE')
        start = bar[0]
        require(type(start) is int and 0 < start and previous < start, 'KRAKEN_OHLC_ORDER')
        previous = start
        o, h, low, close = (decimal(v) for v in bar[1:5])
        vwap = decimal(bar[5], positive=False)
        volume = decimal(bar[6], positive=False)
        require(type(bar[7]) is int and bar[7] >= 0, 'KRAKEN_TRADE_COUNT')
        require(low <= min(o, close) <= max(o, close) <= h and
                (low <= vwap <= h or vwap == 0 and volume == 0 and bar[7] == 0), 'KRAKEN_OHLC_PRICES')
        # Kraken always includes a not-yet-committed trailing entry; never ingest it.
        if index == len(bars)-1:
            continue
        require(start*1000+interval*60000 <= received_at_ms, 'KRAKEN_UNCLOSED_BAR')
        rows.append({'instrument_id': spec['instrument_id'], 'event_time': start*1000,
                     'knowledge_time': received_at_ms, 'open': float(o), 'high': float(h),
                     'low': float(low), 'close': float(close), 'volume': float(volume)})
    return rows
