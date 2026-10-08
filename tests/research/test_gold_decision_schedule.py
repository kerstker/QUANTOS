from copy import deepcopy

import pytest

from services.data.gold_execution_tape import new_gold_trade_cursor
from tests.tools.test_gold_signal_replay import archive


def test_first_watermark_decisions_use_receipt_time_without_changing_windows():
    from services.research.gold_decision_schedule import watermark_decisions
    document=archive()
    for packet in document['packets']:
        packet['received_at_ms']+=20
    original=deepcopy(document)
    times=watermark_decisions(document,cursor_factory=new_gold_trade_cursor)
    assert times==[2030,3030]
    assert document==original
    document['decision_times_ms']=times
    from tools.gold_signal_replay import replay
    assert replay(document)['status']=='RESEARCH_SIGNALS_ONLY'
    # Price/quantity cannot change when the policy decides; there is no search
    # for a favourable signal, and records beyond the last decision are unused.
    for packet in document['packets']:
        packet['payload']['p']='5000'
        packet['payload']['q']='0.01'
    document['packets'].append({'received_at_ms':99999,'payload':{}})
    assert watermark_decisions(document,cursor_factory=new_gold_trade_cursor)==times


@pytest.mark.parametrize('change',['late','missing','gap'])
def test_watermark_policy_does_not_skip_failed_windows(change):
    from services.research.gold_decision_schedule import watermark_decisions
    document=archive()
    if change=='late':
        for packet in document['packets']:packet['received_at_ms']+=101
    elif change=='missing':document['packets']=document['packets'][:3]
    else:document['packets'].pop(1)
    with pytest.raises(ValueError):
        watermark_decisions(document,cursor_factory=new_gold_trade_cursor)
