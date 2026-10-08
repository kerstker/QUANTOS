from copy import deepcopy

import pytest

from tests.tools.test_gold_bar_cost_replay import inputs
from tools.gold_bar_continuation import compare_prefixes


def test_prefix_extension_replays_same_account_and_preserves_open_position():
    request = inputs()
    result = compare_prefixes(**request, frame_counts=(2, 4))
    first, final = result['prefixes']
    assert first['trials'][0]['outcome'] == final['trials'][0]['outcome'] == 'OPEN_AT_END'
    assert len(first['trials'][0]['simulation']['orders']) == len(final['trials'][0]['simulation']['orders']) == 1
    assert result['prefix_consistency_verified'] is True
    assert result['independent_windows_summed'] is False


def test_repeated_or_decreasing_prefix_cannot_be_used_as_continuation():
    for counts in ((2, 2), (4, 2), (2, 5)):
        with pytest.raises(ValueError):
            compare_prefixes(**inputs(), frame_counts=counts)


def test_changed_past_decision_is_detected(monkeypatch):
    from tools import gold_bar_continuation as module
    original = module.compare_public
    def corrupt(**kwargs):
        report = deepcopy(original(**kwargs))
        if kwargs['frame_limit'] == 4:
            report['trials'][0]['simulation']['decision_events'][0]['timestamp'] += 1
        return report
    monkeypatch.setattr(module, 'compare_public', corrupt)
    with pytest.raises(ValueError, match='PREFIX_CHANGED'):
        compare_prefixes(**inputs(), frame_counts=(2, 4))
