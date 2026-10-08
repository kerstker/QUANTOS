from dataclasses import replace
from decimal import Decimal as D

import pytest

from services.research.gold_bar_candidates import Bar, INTERVAL_MS, evaluate


def bars(count=401):
    return [Bar(i * INTERVAL_MS, (i + 1) * INTERVAL_MS,
                (i + 1) * INTERVAL_MS + 10, D('100'), D('101'),
                D('99'), D('100')) for i in range(count)]


def test_breakout_excludes_current_bar_from_channel_and_atr():
    values = bars()
    values[-1] = replace(values[-1], high=D('110'), close=D('109'))
    result = evaluate(values, candidate='channel', as_of_ms=values[-1].available_ms)
    assert result['entry_side'] == 'BUY'
    assert result['stop_distance'] == '4'
    assert result['exit_short'] is True
    assert result['earliest_execution_after_ms'] == values[-1].available_ms
    assert result['entry_price'] is None and result['authoritative'] is False


def test_unavailable_future_bar_cannot_change_decision():
    values = bars(402)
    values[-1] = replace(values[-1], high=D('1000'), close=D('999'))
    now = values[-2].available_ms
    for candidate in ('channel', 'trend', 'reversion'):
        assert evaluate(values, candidate=candidate, as_of_ms=now) == evaluate(
            values[:-1], candidate=candidate, as_of_ms=now)


def test_gap_requires_new_warmup_and_stale_data_does_not_trade():
    values = bars()
    assert evaluate(values[:400], candidate='trend', as_of_ms=values[399].available_ms)['status'] == 'WARMUP'
    assert evaluate(values[:200] + values[201:], candidate='channel',
                    as_of_ms=values[-1].available_ms)['status'] == 'WARMUP'
    assert evaluate(values, candidate='channel',
                    as_of_ms=values[-1].available_ms + INTERVAL_MS + 1)['status'] == 'STALE'


def test_trend_only_emits_on_cross_and_reversion_zero_variance_is_no_signal():
    values = bars()
    assert evaluate(values, candidate='reversion', as_of_ms=values[-1].available_ms)['entry_side'] is None
    values[-1] = replace(values[-1], high=D('103'), close=D('102'))
    assert evaluate(values, candidate='trend', as_of_ms=values[-1].available_ms)['entry_side'] == 'BUY'
    next_bar = replace(values[-1], open_ms=401 * INTERVAL_MS,
                       close_ms=402 * INTERVAL_MS, available_ms=402 * INTERVAL_MS + 10)
    assert evaluate(values + [next_bar], candidate='trend', as_of_ms=next_bar.available_ms)['entry_side'] is None


def test_reversion_uses_previous_population_and_short_breakout_is_symmetric():
    values = bars()
    for i in range(360, 400):
        values[i] = replace(values[i], close=D('99') if i % 2 else D('101'))
    values[-1] = replace(values[-1], high=D('104'), close=D('103'))
    result = evaluate(values, candidate='reversion', as_of_ms=values[-1].available_ms)
    assert result['entry_side'] == 'SELL' and result['exit_long'] is True
    values[-1] = replace(values[-1], low=D('94'), close=D('95'))
    assert evaluate(values, candidate='channel', as_of_ms=values[-1].available_ms)['entry_side'] == 'SELL'


@pytest.mark.parametrize('change', [dict(available_ms=0), dict(high=D('98')),
                                   dict(close=D('NaN')), dict(open_ms=True)])
def test_invalid_bar_rejected(change):
    values = bars()
    values[-1] = replace(values[-1], **change)
    with pytest.raises(ValueError):
        evaluate(values, candidate='channel', as_of_ms=401 * INTERVAL_MS + 10)


def test_duplicate_bar_and_unknown_candidate_rejected():
    values = bars()
    with pytest.raises(ValueError):
        evaluate(values + [values[-1]], candidate='channel', as_of_ms=values[-1].available_ms)
    with pytest.raises(ValueError):
        evaluate(values, candidate='optimised', as_of_ms=values[-1].available_ms)
