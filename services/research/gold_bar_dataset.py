"""Historical REST candles as receipt-time warmup, never invented PIT evidence."""
import hashlib
import json

from quantos.contracts.decimal_strings import nonnegative_decimal_string as _amount
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.gold_bar_candidates import Bar, INTERVAL_MS, WARMUP


def load_bars(raw, *, requested_at_ms, received_at_ms):
    if (type(raw) is not bytes or len(raw) > 2_000_000
            or any(type(t) is not int or not 0 <= t <= 2**63 - 1
                   for t in (requested_at_ms, received_at_ms))
            or received_at_ms < requested_at_ms):
        raise ValueError('INVALID_BAR_RECEIPT')
    rows = load_json_without_duplicate_keys(raw)
    if type(rows) is not list or not 1 <= len(rows) <= 1500:
        raise ValueError('INVALID_BAR_RESPONSE')
    values = []
    for row in rows:
        if (type(row) is not list or len(row) != 12
                or type(row[0]) is not int or type(row[6]) is not int
                or row[6] + 1 != row[0] + INTERVAL_MS
                or row[6] + 1 > requested_at_ms
                or type(row[8]) is not int or row[8] < 0):
            raise ValueError('INVALID_OR_UNFINISHED_BAR')
        for index in (5, 7, 9, 10):
            _amount(row[index])
        bar = Bar(row[0], row[6] + 1, received_at_ms,
                  *(_amount(row[i], positive=True) for i in (1, 2, 3, 4)))
        bar.validate()
        if values and values[-1].close_ms != bar.open_ms:
            raise ValueError('BAR_GAP_DUPLICATE_OR_ORDER')
        values.append(bar)
    return tuple(values)


def split_plan(bars, *, source_raw):
    """Freeze chronological ranges before any return/selection computation.

    This defines ranges only; it does not restrict who can read the source file,
    prove historical publication clocks, or grant final-OOS evaluation authority.
    """
    if not bars or len(bars) <= WARMUP:
        raise ValueError('INSUFFICIENT_BAR_HISTORY')
    reproduced = load_bars(source_raw, requested_at_ms=bars[-1].close_ms,
                           received_at_ms=bars[-1].available_ms)
    if tuple(bars) != reproduced:
        raise ValueError('BAR_SOURCE_BINDING')
    usable = len(bars) - WARMUP
    sizes = (usable * 6 // 10, usable * 2 // 10)
    sizes = (*sizes, usable - sum(sizes))
    if min(sizes) < 1:
        raise ValueError('INSUFFICIENT_SPLIT')
    segments = []
    cursor = WARMUP
    for name, count in zip(('train', 'validation', 'final_oos'), sizes):
        segments.append(dict(name=name, start_ms=bars[cursor].open_ms,
                             end_exclusive_ms=bars[cursor + count - 1].close_ms, count=count))
        cursor += count
    result = dict(schema='GOLD_BAR_SPLIT_PLAN_V1', status='SPLIT_DEFINED_NOT_EVALUATED',
                  source_sha256=hashlib.sha256(source_raw).hexdigest(),
                  warmup_count=WARMUP, segments=segments,
                  historical_pit_proven=False, authoritative=False,
                  submission_enabled=False, final_oos_evaluated=False)
    result['plan_sha256'] = hashlib.sha256(json.dumps(
        result, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return result
