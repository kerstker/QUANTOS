from copy import deepcopy

import pytest

from tools import binance_demo_cash_window as tool
from tests.tools.test_binance_demo_cash_window import Clock, transport


def test_long_window_keeps_original_opening_after_four_hours():
    clock = Clock()
    calls = []
    arguments = dict(credential_file=None, operator_read=True, observe_seconds=30,
        credential_reader=lambda _: ('synthetic-key', 'synthetic-secret'),
        get=transport(clock, calls), clock_ms=clock.now, sleep=clock.sleep, long_window=True)
    opening = tool.collect(**arguments, opening_only=True)
    original = deepcopy(opening)
    clock.sleep(4*3600)
    result = tool.collect(**arguments, opening_prefix=opening)
    assert result['schema'] == 'DEMO_CASH_WINDOW_LONG_V1'
    assert result['status'] == 'CAPTURED' and len(calls) == 6
    assert result['reads'][:2] == opening['reads'] and opening == original
    assert result['attempts'][:2] == opening['attempts']
    assert result['observe_seconds'] >= 4*3600+30
    report = tool.assess(result)
    assert report['status'] == 'MATCHED_REPORTED_WINDOW'
    assert report['attempts'] == result['attempts']
    wrong_schema = deepcopy(result)
    wrong_schema['schema'] = 'DEMO_CASH_WINDOW_V2'
    with pytest.raises(ValueError, match='CLOCK'):
        tool.validate(wrong_schema)


@pytest.mark.parametrize('damage', ['old_prefix', 'expired'])
def test_long_window_does_not_upgrade_old_prefix_or_resume_after_budget(damage):
    clock = Clock()
    prefix = tool.collect(credential_file=None, operator_read=True, observe_seconds=30,
        credential_reader=lambda _: ('synthetic-key', 'synthetic-secret'),
        get=transport(clock, []), clock_ms=clock.now, sleep=clock.sleep,
        opening_only=True, **({'long_window':True} if damage != 'old_prefix' else {}))
    if damage == 'expired':
        clock.sleep(6*3600)
    with pytest.raises(ValueError, match='RESUME_PREFIX'):
        tool.collect(credential_file=None, operator_read=True, observe_seconds=30,
            opening_prefix=prefix, long_window=True, clock_ms=clock.now,
            credential_reader=lambda _: pytest.fail('credentials read after invalid prefix'),
            get=lambda *a, **kw: pytest.fail('network after invalid prefix'))


@pytest.mark.parametrize('changed_sweep', [False, True])
def test_four_hour_fees_and_funding_remain_two_sweep_reconciled(changed_sweep):
    import json
    clock = Clock()
    start = clock.ms
    wallets = []
    incomes = []
    def get(path, **kwargs):
        at = clock.ms
        clock.ms += 100
        if path == '/fapi/v1/time':
            data = {'serverTime':at+50}
        elif path == '/fapi/v3/account':
            wallets.append(1)
            data = {'assets':[{'asset':'USDT','walletBalance':'1000' if len(wallets) == 1 else '998.7',
                'updateTime':at-1000}]}
        else:
            assert path == '/fapi/v1/income'
            incomes.append(1)
            data = [dict(symbol='XAUUSDT', incomeType=kind, income=amount, asset='USDT',
                time=start+(index+1)*3600000, tranId=index+1, tradeId=str(index+1), info='')
                for index, (kind, amount) in enumerate([('COMMISSION','-0.2'), ('FUNDING_FEE','-0.1'), ('REALIZED_PNL','-1')])]
            if changed_sweep and len(incomes) == 2:
                data[1]['income'] = '-0.11'
        return json.dumps(data).encode()
    args = dict(credential_file=None, operator_read=True, observe_seconds=30, long_window=True,
        credential_reader=lambda _: ('synthetic-key', 'synthetic-secret'),
        get=get, clock_ms=clock.now, sleep=clock.sleep)
    prefix = tool.collect(**args, opening_only=True)
    clock.sleep(14400)
    result = tool.collect(**args, opening_prefix=prefix)
    report = tool.assess(result)
    if changed_sweep:
        assert report['status'] == 'UNRESOLVED' and 'INCOME_SWEEPS_DIFFER' in report['issues']
        assert report['assets'][0]['residual'] is None
    else:
        assert report['status'] == 'MATCHED_REPORTED_WINDOW'
        asset, = report['assets']
        assert (asset['commissions'], asset['funding'], asset['realized_pnl'], asset['residual']) == ('-0.2', '-0.1', '-1', '0')
    assert report['ledger_reconciliation'] == 'NOT_EVALUATED'
