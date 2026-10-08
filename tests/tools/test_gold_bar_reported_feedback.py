from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tools.gold_bar_demo_entry import BarDemoEntry
from tests.research.test_gold_reported_short import short_inputs
from tests.research.test_binance_order_receipts import receipt


@pytest.mark.parametrize('holding_ms', [0, 14400000, 86400000])
@pytest.mark.parametrize('damage', [None, 'definition', 'opening', 'quantity', 'future', 'cash'])
def test_original_bar_short_and_reported_account_are_bound(tmp_path, monkeypatch, damage, holding_ms):
    from tools import gold_bar_reported_feedback as tool
    inputs = short_inputs(tmp_path)
    if holding_ms:
        from tests.tools.test_binance_demo_execution_origin import NOW
        def later(item):
            if type(item) is int and item >= NOW+30000:
                return item+holding_ms
            if isinstance(item, dict):
                return {k: later(v) for k, v in item.items()}
            if isinstance(item, list):
                return [later(v) for v in item]
            return item
        inputs = later(inputs)
        for statement in inputs['statements']:
            for trade in statement['trades']:
                if trade['id'] == '2':
                    trade['time'] += holding_ms
            for flow in statement['income']:
                if flow['tradeId'] == '2':
                    flow['time'] += holding_ms
            statement['income'].append(dict(symbol='XAUUSDT', incomeType='FUNDING_FEE',
                income='-0.05', asset='USDT', time=NOW+holding_ms//2, tranId='99', tradeId=''))
            statement['income'].sort(key=lambda r: (r['time'], (r['incomeType'], r['tranId'])))
            for read in statement['reads']:
                if read['kind'] == 'trades':
                    read['data'] = deepcopy(statement['trades'])
                if read['kind'] == 'income':
                    read['data'] = deepcopy(statement['income'])
        for read in inputs['closing']['reads']:
            if read['kind'].startswith('account'):
                for asset in read['data']['assets']:
                    if asset['asset'] == 'USDT':
                        for k in ('walletBalance', 'crossWalletBalance', 'availableBalance', 'marginBalance'):
                            asset[k] = '5000.0301'
        window = inputs['windows'][1]
        window['start_ms'] += holding_ms
        window['end_ms'] += holding_ms
        row = window['messages'][0]
        event = json.loads(row['raw_utf8'])
        event['E'] += holding_ms
        event['T'] += holding_ms
        event['o']['T'] += holding_ms
        window['messages'] = [receipt(event, row['received_at_ms']+holding_ms)]
        for gap in window['gaps']:
            gap['at_ms'] += holding_ms
    account = inputs['opening']['account_ref_sha256']
    decision = inputs['opening']['observed_at_ms']-1
    definition = dict(definition_hash='d'*64, size_and_timing={'quantity': '0.002'})
    signal = dict(decision_id='s', entry_side='SELL')
    client = 'QD'+_digest(dict(account_ref_sha256=account, definition_hash='d'*64,
        decision_id='s', action='OPEN_SELL'))[:32]
    inputs['system_orders'][0]['newClientOrderId'] = client
    row = inputs['windows'][0]['messages'][0]
    event = json.loads(row['raw_utf8'])
    event['o']['c'] = client
    inputs['windows'][0]['messages'] = [receipt(event, row['received_at_ms'])]
    proofs = [dict(strategy_id='same', origin=deepcopy(inputs['opening']),
        risk={'input_snapshot': {'market': {'daily_volume_base': '10000', 'reference_price': '100'},
            'native_limits': {'max_participation_rate': '0.001'}}},
        execution_constraints={'strategy_definition_sha256': 'd'*64, 'not_before_ms': decision},
        context={'account_ref_sha256': account}, orders=[deepcopy(order)]) for order in inputs['system_orders']]
    prepared = [dict(reservation_sha256=str(i+1)*64, index=i) for i in range(2)]
    before = dict(reservations=[dict(record_hash=p['reservation_sha256'],
        payload=dict(orders=proof['orders'], context=proof['context'])) for p, proof in zip(prepared, proofs)])
    engine = object.__new__(BarDemoEntry)
    monkeypatch.setattr(engine, 'verified_definition', lambda: deepcopy(definition))
    monkeypatch.setattr(engine, 'entry_signal', lambda _: deepcopy(signal))
    monkeypatch.setattr(tool, '_load_proof', lambda p: deepcopy(proofs[p['index']]))
    monkeypatch.setattr(tool, 'check_proposal', lambda *a, **k: None)
    now = inputs['statements'][-1]['completed_at_ms']+1
    if damage == 'definition':
        proofs[1]['execution_constraints']['strategy_definition_sha256'] = 'e'*64
    elif damage == 'opening':
        proofs[0]['origin']['observed_at_ms'] -= 1
    elif damage == 'quantity':
        definition['size_and_timing']['quantity'] = '0.003'
    elif damage == 'future':
        now -= 2
    elif damage == 'cash':
        for read in inputs['closing']['reads']:
            if read['kind'].startswith('account'):
                for asset in read['data']['assets']:
                    if asset['asset'] == 'USDT':
                        asset['walletBalance'] = '5000'
    def build():
        # The stub stands in for the native reader, which already requires this
        # complete prepared/proof binding. Keep its original financial damage
        # cases behind that unchanged protocol boundary.
        for item, proof in zip(prepared, proofs, strict=True):
            item.update(schema='DEMO_PREPARED_BATCH_V1', status='INTENTS_PERSISTED',
                proposal_sha256=_digest(proof), orders=deepcopy(proof['orders']), context=deepcopy(proof['context']),
                submission_enabled=False, economic_authority=False)
        return tool.build(engine=engine, entry_decision_ms=decision, prepared=prepared,
            dispatch=SimpleNamespace(evidence_snapshot=lambda: deepcopy(before)),
            inputs=inputs, source_commit='c'*40, as_of_ms=now)
    if damage:
        with pytest.raises(ValueError):
            build()
    else:
        result = build()
        assert result['account_report']['status'] == 'REPORTED_ACCOUNT_MATCHED'
        assert result['account_report']['wallet_residual'] == '0'
        if holding_ms == 86400000:
            report = result['account_report']
            assert report['schema'] == 'DEMO_REPORTED_ACCOUNT_EPISODE_V1'
            assert report['daily_valuation_status'] == 'NOT_RECONSTRUCTED'
            assert report['risk_budget_reset'] is False
            from services.research.demo_account_recovery import reconcile_days
            daily, ledger = reconcile_days(**inputs)
            assert ledger is None
            assert daily['issues'] == ['ACCOUNT_DAYS_MIDNIGHT_POSITION_UNVALUED']
        assert result['account_report']['canonical_wallet'] == ('5000.0301' if holding_ms else '5000.0801')
        assert len(result['account_report']['stream_gaps']) == 2
        assert result['stream_coverage_repaired'] is False
        assert result['economic_eligible'] is False and result['submission_enabled'] is False
        assert result['result_hash'] == _digest({k:v for k,v in result.items() if k != 'result_hash'})
