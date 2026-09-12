from copy import deepcopy
from decimal import localcontext

import pytest

from services.research.demo_cash_window import assess, load_capture, save_capture, validate

NOW = 1789170000000
REF = 'a' * 64


def fixture():
    def read(kind, at, data, sweep=0, page=0):
        return dict(kind=kind, requested_at_ms=at, received_at_ms=at+100,
                    account_ref_sha256=None if kind.startswith('clock') else REF,
                    response_sha256='b'*64, sweep=sweep, page=page, data=data)
    rows = [dict(symbol='BTCUSDT',incomeType=kind,income=value,asset='USDT',
                 time=NOW+30000+i,tranId=str(9007199254740993+i),tradeId='')
            for i,(kind,value) in enumerate([('TRANSFER','100'),('COMMISSION','-0.1'),('FUNDING_FEE','-0.2')])]
    return dict(schema='DEMO_CASH_WINDOW_V1',status='CAPTURED',observed_at_ms=NOW,
                completed_at_ms=NOW+110000,account_ref_sha256=REF,source_sha256='c'*64,
                observe_seconds=60,reads=[
                    read('clock_before',NOW,{'serverTime':NOW+50}),
                    read('wallet_before',NOW+200,[{'asset':'USDT','wallet':'1000','updated_at_ms':NOW-1000}]),
                    read('wallet_after',NOW+60300,[{'asset':'USDT','wallet':'1099.7','updated_at_ms':NOW+30002}]),
                    read('clock_after',NOW+60500,{'serverTime':NOW+60550}),
                    read('income',NOW+81000,deepcopy(rows),1,1),
                    read('income',NOW+101100,deepcopy(rows),2,1),
                ],failures=[])


def test_exact_wallet_movement_counts_income_once_and_separates_transfer(tmp_path):
    raw=fixture()
    with localcontext() as ctx:
        ctx.prec=4
        report=assess(raw)
    assert report['status']=='MATCHED_REPORTED_WINDOW'
    a=report['assets'][0]
    assert (a['wallet_change'],a['reported_flow'],a['residual'])==('99.7','99.7','0')
    assert (a['transfers'],a['commissions'],a['funding'])==('100','-0.1','-0.2')
    assert report['strategy_pnl'] is None and report['ledger_reconciliation']=='NOT_EVALUATED'
    assert report['history_complete'] is False and report['source_verified'] is False
    assert report['recent_flows'][0]['transaction_id']=='9007199254740993'
    save_capture(tmp_path,raw)
    assert load_capture(tmp_path)==raw
    save_capture(tmp_path,raw)
    assert len(list(tmp_path.glob('*.json')))==2


def test_unexplained_wallet_difference_is_visible():
    raw=fixture();raw['reads'][2]['data'][0]['wallet']='1099.69999999'
    report=assess(raw)
    assert report['status']=='DIFFERENCE_FOUND'
    assert report['assets'][0]['residual']=='-0.00000001'


def test_currency_is_not_combined_and_missing_wallet_is_not_assumed_zero():
    raw=fixture()
    for r in raw['reads']:
        if r['kind']=='income':r['data'][0]['asset']='USDC'
    report=assess(raw)
    assert report['status']=='UNRESOLVED'
    a=next(a for a in report['assets'] if a['asset']=='USDC')
    assert a['opening_wallet'] is None and a['residual'] is None
    assert 'WALLET_ASSET_MISSING' in report['issues']


@pytest.mark.parametrize('at',[NOW+200,NOW+60300,NOW-4800,NOW+65400])
def test_events_in_inclusive_endpoint_uncertainty_bands_prevent_matching(at):
    raw=fixture()
    for r in raw['reads']:
        if r['kind']=='income':r['data'][0]['time']=at
    report=assess(raw)
    assert report['status']=='UNRESOLVED'
    assert 'ENDPOINT_EVENT_AMBIGUITY' in report['issues']
    assert report['assets'][0]['residual'] is None


def test_second_income_sweep_must_match_first():
    raw=fixture();raw['reads'][-1]['data'].pop()
    report=assess(raw)
    assert report['status']=='UNRESOLVED'
    assert 'INCOME_SWEEPS_DIFFER' in report['issues']


def test_duplicates_are_retained_but_not_added_and_conflicts_unresolved():
    raw=fixture()
    for r in raw['reads']:
        if r['kind']=='income':r['data'].append(deepcopy(r['data'][0]))
    report=assess(raw)
    assert report['status']=='MATCHED_REPORTED_WINDOW' and report['duplicate_rows']==2
    raw['reads'][-1]['data'][-1]['income']='101'
    assert 'CONFLICTING_FLOW_IDENTITY' in assess(raw)['issues']


def test_full_last_page_is_not_evidence_of_coverage():
    raw=fixture()
    for r in raw['reads']:
        if r['kind']=='income':r['data']=[deepcopy(r['data'][0]) for _ in range(1000)]
    report=assess(raw)
    assert report['status']=='UNRESOLVED'
    assert 'INCOME_PAGE_LIMIT' in report['issues']


@pytest.mark.parametrize('change', ['identity','clock','page','future','float','extra','clock_drift','wallet_future'])
def test_invalid_binding_timing_and_exact_input_are_rejected(change):
    raw=fixture()
    if change=='identity':raw['reads'][2]['account_ref_sha256']='d'*64
    elif change=='clock':raw['reads'][2]['requested_at_ms']=NOW-1
    elif change=='page':raw['reads'][-1]['page']=2
    elif change=='future':raw['reads'][-1]['received_at_ms']=raw['completed_at_ms']+1
    elif change=='float':raw['reads'][2]['data'][0]['wallet']=1099.7
    elif change=='extra':raw['token']='do not retain unknown input'
    elif change=='clock_drift':raw['reads'][3]['data']['serverTime']+=1000
    elif change=='wallet_future':raw['reads'][1]['data'][0]['updated_at_ms']=NOW+10000
    with pytest.raises(ValueError):validate(raw)


def test_future_report_and_pointer_tampering_are_rejected(tmp_path):
    raw=fixture()
    with pytest.raises(ValueError):assess(raw,as_of_ms=NOW)
    save_capture(tmp_path,raw)
    artifact=next(p for p in tmp_path.glob('*.json') if p.name!='latest.json')
    artifact.write_text('{}')
    with pytest.raises(ValueError):load_capture(tmp_path)


def test_failed_capture_preserves_observation_without_claiming_zeros():
    raw=fixture();raw['status']='PARTIAL';raw['reads']=raw['reads'][:2]
    raw['failures']=[dict(kind='wallet_after',sweep=0,page=0,requested_at_ms=NOW+60000,
                         received_at_ms=NOW+61000,reason='REQUEST_FAILED')]
    report=assess(raw)
    assert report['status']=='UNRESOLVED'
    assert report['income_count']==0 and report['assets'][0]['closing_wallet'] is None
    assert report['assets'][0]['reported_flow'] is None
