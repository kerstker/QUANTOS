import json
from decimal import Decimal

import pytest

from services.research.gold_bar_dataset import load_bars, split_plan
from services.research.gold_bar_candidates import INTERVAL_MS as STEP


def raw_rows(count=1000):
    return [[i * STEP, '100', '102', '99', '101', '5', (i + 1) * STEP - 1,
             '505', 2, '3', '303', '0'] for i in range(count)]


def test_historical_bars_keep_actual_receipt_time_and_exclusive_close():
    raw = json.dumps(raw_rows()).encode()
    values = load_bars(raw, requested_at_ms=1000 * STEP, received_at_ms=1000 * STEP + 50)
    assert len(values) == 1000
    assert values[0].close_ms == STEP
    assert values[0].available_ms == values[-1].available_ms == 1000 * STEP + 50
    assert values[0].close == Decimal('101')


def test_split_excludes_warmup_and_is_bound_to_original_data():
    raw = json.dumps(raw_rows()).encode()
    values = load_bars(raw, requested_at_ms=1000 * STEP, received_at_ms=1000 * STEP + 50)
    plan = split_plan(values, source_raw=raw)
    assert [s['count'] for s in plan['segments']] == [360, 120, 120]
    assert plan['segments'][0]['start_ms'] == 400 * STEP
    assert plan['historical_pit_proven'] is False
    assert plan['status'] == 'SPLIT_DEFINED_NOT_EVALUATED'


@pytest.mark.parametrize('mutation', ['duplicate', 'gap', 'unfinished', 'badclose', 'nan'])
def test_invalid_history_cannot_be_silently_filled_or_truncated(mutation):
    rows = raw_rows()
    if mutation == 'duplicate':
        rows.append(rows[-1])
    elif mutation == 'gap':
        del rows[10]
    elif mutation == 'unfinished':
        rows[-1][6] = 1001 * STEP - 1
    elif mutation == 'badclose':
        rows[-1][6] -= 1
    else:
        rows[-1][4] = 'NaN'
    with pytest.raises(ValueError):
        load_bars(json.dumps(rows).encode(), requested_at_ms=1000 * STEP,
                  received_at_ms=1000 * STEP + 50)
