"""Collector-to-account acceptance with synthetic public receipts; no network."""
import json
from decimal import Decimal

import numpy as np
import pytest

from services.research import rf3_daily_forward as contract
from services.research import rf3_forward_account as account
from tools import rf3_daily_forward as collector


@pytest.fixture
def collected_window(tmp_path):
    ancestry = tmp_path / 'ancestry.json'
    ancestry.write_text(json.dumps({
        'schema': collector.RF2_CASE_SCHEMA, 'candidate': collector.RF2_CANDIDATE,
        'signal': collector.RF2_SIGNAL, 'status': collector.TECHNICAL,
        'evidence_class': collector.TECHNICAL,
        **{key: False for key in collector.AUTHORITY_FIELDS},
    }))
    root = tmp_path / 'state'
    collector.register(root, ancestry, clock=lambda: 1_787_000_000_000)
    plan = json.loads((root / 'plan.json').read_text())
    bundles = []
    for index, slot in enumerate(plan['slots']):
        rows = [[slot - (90-i)*contract.DAY_MS, '1', '1', '1', str(100+i), '1',
                 slot - (89-i)*contract.DAY_MS - 1] for i in range(90)]
        receipt = {'ok': True, 'url': contract.fixed_url(slot),
                   'raw_utf8': json.dumps(rows), 'requested_ms': slot+1,
                   'received_ms': slot+2}
        result = collector.run_once(
            root, clock=lambda: slot+1000, fetch=lambda *_: receipt,
            identity=lambda: plan['collector_identity'])
        assert result['outcome'] == 'PUBLISHED'
        bundles.append({
            'signal': json.loads((root / f'signal-{index}.json').read_text()),
            'attempt': json.loads((root / f'attempt-{index}-1.json').read_text()),
            'publication': json.loads((root / f'publication-{index}.json').read_text()),
        })
    start = plan['slots'][0] + contract.DEADLINE_MS
    end = start + 14*contract.DAY_MS
    times = np.arange(start, end+60000, 60000, dtype=np.int64)
    market = dict(times=times, prices=np.full(len(times), 100.),
                  marks=np.full(len(times), 100.), funding=[], start=start, end=end,
                  daily_volume={int(t//contract.DAY_MS): Decimal('10000000') for t in times},
                  maximum_quantity=Decimal('50'))
    return plan, bundles, market, end+60001


def test_actual_collector_artifacts_feed_nine_case_account(collected_window):
    plan, bundles, market, mature = collected_window
    result = account.evaluate(plan, bundles, market, evaluated_ms=mature)
    assert len(result['cases']) == 9
    assert result['plan_hash'] == plan['plan_hash']
    assert result['economic_eligible'] is False


@pytest.mark.parametrize('fault', ['late_publication', 'wrong_attempt', 'missing_slot',
                                   'boolean_attempt', 'float_record_clock', 'numeric_signal_flag'])
def test_collector_evidence_faults_reject_before_replay(collected_window, monkeypatch, fault):
    plan, bundles, market, mature = collected_window
    if fault == 'late_publication':
        bundles[0]['publication']['completed_ms'] = bundles[0]['signal']['entry_ms']
    elif fault == 'wrong_attempt':
        bundles[0]['attempt']['attempt'] = 2
    elif fault == 'boolean_attempt':
        bundles[0]['attempt']['attempt'] = True
    elif fault == 'float_record_clock':
        bundles[0]['attempt']['recorded_ms'] = float(bundles[0]['signal']['recorded_ms'])
    elif fault == 'numeric_signal_flag':
        bundles[0]['signal']['authority'] = 0
        bundles[0]['publication']['signal_hash'] = contract.digest(bundles[0]['signal'])
    else:
        bundles.pop()
    monkeypatch.setattr(account, 'replay', lambda **_: pytest.fail('invalid evidence reached replay'))
    with pytest.raises(ValueError):
        account.evaluate(plan, bundles, market, evaluated_ms=mature)


@pytest.mark.parametrize('override', [{'stop': False}, {'taker_fee_bps': Decimal('0')},
                                     {'max_position_weight': Decimal('100')}])
def test_market_dictionary_cannot_override_execution(collected_window, monkeypatch, override):
    plan, bundles, market, mature = collected_window
    monkeypatch.setattr(account, 'replay', lambda **_: pytest.fail('override reached replay'))
    with pytest.raises(ValueError):
        account.evaluate(plan, bundles, {**market, **override}, evaluated_ms=mature)
