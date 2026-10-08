"""Versioned long grids retain every receipt and legacy capacity boundaries."""
import pytest

from quantos.contracts.gold_long_statistics_budget import declaration
from quantos.contracts.research_statistics import fixed_frequency_equity

START = 1_728_000_000_000
SPAN = 30 * 86_400_000 + 10_000


def observations():
    return [dict(timestamp=START, sequence=0, equity='1000'),
            dict(timestamp=START + 15 * 86_400_000, sequence=1, equity='999'),
            dict(timestamp=START + SPAN, sequence=2, equity='998'),
            dict(timestamp=START + SPAN + 1, sequence=3, equity='994')]


def test_complete_month_grid_keeps_received_cost_changes_and_validates_the_tail():
    rows = observations()
    seen = []

    def source():
        for row in rows:
            seen.append(row['sequence'])
            yield row

    result = fixed_frequency_equity(source(), start_ms=START, end_ms=START + SPAN,
                                    frequency_ms=10_000, budget=declaration())
    assert seen == [0, 1, 2, 3]
    assert len(result['points']) == 259202
    assert result['points'][0]['equity'] == '1000'
    assert result['points'][129600]['equity'] == '999'
    assert result['points'][-1]['equity'] == '998'
    assert result['points'][-1]['timestamp'] == START + SPAN
    assert result['statistics_status'] == 'DIAGNOSTIC_ONLY'
    assert result['frequency_ms'] == 10_000


def test_long_profile_does_not_change_short_grid_bytes_or_legacy_capacity():
    arguments = dict(start_ms=START, end_ms=START + 100_000, frequency_ms=10_000)
    assert fixed_frequency_equity(observations(), **arguments, budget=declaration()) == (
        fixed_frequency_equity(observations(), **arguments))
    with pytest.raises(ValueError):
        fixed_frequency_equity(observations(), start_ms=START, end_ms=START + SPAN, frequency_ms=10_000)


def test_long_grid_rejects_corrupt_outside_sample_tail_and_capacity_before_consumption():
    rows = observations()
    rows[-1]['equity'] = 'NaN'
    with pytest.raises(ValueError, match='finite'):
        fixed_frequency_equity(rows, start_ms=START, end_ms=START + SPAN,
                               frequency_ms=10_000, budget=declaration())
    def unread():
        pytest.fail('invalid declared capacity must not consume receipts')
        yield
    with pytest.raises(ValueError):
        fixed_frequency_equity(unread(), start_ms=START,
                               end_ms=START + 32 * 86_400_000 + 20_000,
                               frequency_ms=10_000, budget=declaration())
