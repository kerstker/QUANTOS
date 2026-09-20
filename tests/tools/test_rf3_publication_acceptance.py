"""Independent publication-boundary regressions; all data and clocks synthetic."""
import json

import pytest

from tools import rf3_daily_forward as tool
from services.research import rf3_daily_forward as contract


@pytest.fixture
def observation(tmp_path):
    ancestry = tmp_path / 'ancestry-source.json'
    ancestry.write_text(json.dumps({
        'schema': tool.RF2_CASE_SCHEMA, 'candidate': tool.RF2_CANDIDATE,
        'signal': tool.RF2_SIGNAL, 'status': tool.TECHNICAL,
        'evidence_class': tool.TECHNICAL,
        **{key: False for key in tool.AUTHORITY_FIELDS},
    }))
    root = tmp_path / 'state'
    tool.register(root, ancestry, clock=lambda: 1_787_000_000_000)
    plan = json.loads((root / 'plan.json').read_text())
    slot = plan['slots'][0]
    rows = [[slot - (90-i)*contract.DAY_MS, '1', '1', '1', str(100+i), '1',
             slot - (89-i)*contract.DAY_MS - 1] for i in range(90)]
    receipt = {'ok': True, 'url': contract.fixed_url(slot), 'raw_utf8': json.dumps(rows),
               'requested_ms': slot+1, 'received_ms': slot+2}
    return root, plan, slot, receipt


def test_success_retains_raw_response_for_independent_recompute(observation):
    root, plan, slot, receipt = observation
    result = tool.run_once(root, clock=lambda: slot+1000, fetch=lambda *_: receipt,
                           identity=lambda: plan['collector_identity'])
    assert result['outcome'] == 'PUBLISHED'
    saved = json.loads((root / 'attempt-0-1.json').read_text())
    assert saved['raw_utf8'] == receipt['raw_utf8']


def test_deadline_crossed_during_final_publication_rejects_signal(observation):
    root, plan, slot, receipt = observation
    clocks = iter([slot+1000, slot+2000])
    clock = lambda: next(clocks, slot+contract.DEADLINE_MS)
    result = tool.run_once(root, clock=clock, fetch=lambda *_: receipt,
                           identity=lambda: plan['collector_identity'])
    assert result['outcome'] == 'LATE'
    assert not (root / 'signal-0.json').exists()


def test_interruption_reserves_attempt_before_network(observation):
    root, plan, slot, _ = observation

    def interrupt(*_):
        raise KeyboardInterrupt('synthetic interruption')

    with pytest.raises(KeyboardInterrupt):
        tool.run_once(root, clock=lambda: slot+1000, fetch=interrupt,
                      identity=lambda: plan['collector_identity'])
    status = tool.status(root, clock=lambda: slot+1000)
    assert status['slots'][0]['remaining_attempts'] == 2


def test_corrupt_saved_signal_is_not_counted_as_published(observation):
    root, plan, slot, receipt = observation
    tool.run_once(root, clock=lambda: slot+1000, fetch=lambda *_: receipt,
                  identity=lambda: plan['collector_identity'])
    path = root / 'signal-0.json'
    signal = json.loads(path.read_text())
    signal['target_weight'] = '999'
    path.write_text(json.dumps(signal))
    with pytest.raises((ValueError, RuntimeError)):
        tool.status(root, clock=lambda: slot+1000)


def test_slow_signal_write_cannot_create_a_valid_late_publication(observation, monkeypatch):
    root, plan, slot, receipt = observation
    current = [slot+1000]
    write = tool._write_new

    def slow_write(path, payload):
        write(path, payload)
        if path.name == 'signal-0.json':
            current[0] = slot+contract.DEADLINE_MS

    monkeypatch.setattr(tool, '_write_new', slow_write)
    result = tool.run_once(root, clock=lambda: current[0], fetch=lambda *_: receipt,
                           identity=lambda: plan['collector_identity'])
    assert result['outcome'] == 'LATE'
    assert tool.status(root, clock=lambda: current[0])['published'] == []
