import json
from copy import deepcopy
import pytest
from services.data import kraken_cash_reconciliation as cash
from services.data.kraken_account import assess
from tests.data.test_kraken_account import observations, NOW


def samples():
    opening=assess(observations(),as_of_ms=NOW+500)
    reads=observations()
    for r in reads:
        r['requested_at_ms']+=60000;r['received_at_ms']+=60000
        if r['endpoint']=='BalanceEx':
            p=json.loads(r['body']);p['result']['ZUSD']['balance']='959.96000001'
            p['result']['AAPLx.T']['balance']='2.523456789012345678';r['body']=json.dumps(p)
    closing=assess(reads,as_of_ms=NOW+60500)
    pages=[]
    for cls,asset,amount,fee,balance in [('currency','ZUSD','-40','0.04','959.96000001'),
        ('tokenized_asset','AAPLx.T','0.4','0','2.523456789012345678')]:
        pages.append({'endpoint':'Ledgers','params':{'asset':'all','aclass':cls,'type':'all',
            'start':NOW//1000,'end':NOW//1000+61,'ofs':0,'rebase_multiplier':'base'},
            'account_ref_sha256':'a'*64,'requested_at_ms':NOW+62000,'received_at_ms':NOW+62000,
            'body':json.dumps({'error':[],'result':{'count':1,'ledger':{'L-'+cls:{'refid':'TRADE-1',
                'time':str(NOW//1000+30),'type':'trade','subtype':'','aclass':cls,'asset':asset,
                'amount':amount,'fee':fee,'balance':balance}}}})})
    return opening,closing,pages


def test_cash_and_token_changes_include_fee_in_its_actual_asset():
    before,after,pages=samples()
    report=cash.reconcile(before,after,pages,as_of_ms=NOW+63000)
    assert report['status']=='BALANCE_MOVEMENTS_MATCHED'
    assert report['assets']['ZUSD']['movement']=='-40.04'
    assert report['assets']['ZUSD']['fees']=='0.04'
    assert report['assets']['AAPLx.T']['movement']=='0.4'
    assert report['next_cycle_allowed'] is False


@pytest.mark.parametrize('case',['missing_class','missing_page','duplicate','wrong_account','boundary','drift','post_balance'])
def test_incomplete_coverage_or_unexplained_balance_cannot_pass(case):
    before,after,pages=samples()
    if case=='missing_class':pages.pop()
    if case=='duplicate':pages.append(deepcopy(pages[0]))
    if case=='wrong_account':pages[0]['account_ref_sha256']='b'*64
    if case=='drift':after['balances']['ZUSD']['balance']='960'
    if case in ('missing_page','boundary','post_balance'):
        p=json.loads(pages[0]['body'])
        if case=='missing_page':p['result']['count']=51
        if case=='boundary':p['result']['ledger']['L-currency']['time']=str(NOW//1000)+'.2'
        if case=='post_balance':p['result']['ledger']['L-currency']['balance']='999'
        pages[0]['body']=json.dumps(p)
    try:report=cash.reconcile(before,after,pages,as_of_ms=NOW+63000)
    except ValueError:return
    assert report['status']=='UNRESOLVED'


def test_fee_in_base_asset_is_not_silently_charged_to_quote_cash():
    before,after,pages=samples()
    after['balances']['ZUSD']['balance']='960.00000001'
    after['balances']['AAPLx.T']['balance']='2.519456789012345678'
    for page in pages:
        p=json.loads(page['body']);row=next(iter(p['result']['ledger'].values()))
        row['fee']='0' if row['asset']=='ZUSD' else '0.004'
        row['balance']=after['balances'][row['asset']]['balance'];page['body']=json.dumps(p)
    report=cash.reconcile(before,after,pages,as_of_ms=NOW+63000)
    assert report['status']=='BALANCE_MOVEMENTS_MATCHED'
    assert report['assets']['ZUSD']['fees']=='0' and report['assets']['AAPLx.T']['fees']=='0.004'


def test_multiple_pages_reconstruct_chronology_independent_of_response_order():
    before,after,pages=samples()
    p=json.loads(pages[0]['body']);entries=p['result']['ledger']
    for i in range(50):
        entries['L-ZERO-'+str(i)]={'refid':'','type':'adjustment','aclass':'currency','asset':'ZUSD',
            'time':f'{NOW//1000+1}.{i:02d}','amount':'0','fee':'0','balance':before['balances']['ZUSD']['balance']}
    pairs=list(entries.items())
    first=deepcopy(pages[0]);first['body']=json.dumps({'error':[],'result':{'count':51,'ledger':dict(pairs[:50])}})
    second=deepcopy(pages[0]);second['params']['ofs']=50
    second['body']=json.dumps({'error':[],'result':{'count':51,'ledger':dict(pairs[50:])}})
    result=cash.reconcile(before,after,[first,second,pages[1]],as_of_ms=NOW+63000)
    assert result['status']=='BALANCE_MOVEMENTS_MATCHED' and len(result['ledger_entries'])==52
