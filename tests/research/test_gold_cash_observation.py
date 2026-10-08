import copy
from decimal import localcontext
import pytest
from services.research.gold_cash_observation import assess, reconcile
from tests.research.test_gold_trade_observation import document as fill_document


def document():
    d=fill_document();d.pop('fills');d['schema']='GOLD_EXTERNAL_CASH_V1'
    d['events']=[dict(record_id='1',time_ms=1500,kind='COMMISSION',asset='USDT',amount='-0.0200005',symbol='XAUUSDT'),
                 dict(record_id='2',time_ms=1600,kind='FUNDING',asset='USDT',amount='-0.1',symbol='XAUUSDT'),
                 dict(record_id='3',time_ms=1700,kind='TRANSFER',asset='USDT',amount='100',symbol=None)]
    return d


def test_transfer_and_funding_are_separate_and_never_account_profit():
    r=assess(document())
    assert r['amounts_by_kind_asset']=={'COMMISSION':{'USDT':'-0.0200005'},'FUNDING':{'USDT':'-0.1'},'TRANSFER':{'USDT':'100'}}
    assert r['net_pnl'] is None and r['history_complete'] is False
    assert r['source_authenticated'] is False


def test_dedup_normalizes_amounts_but_conflicting_ids_reject():
    d=document();d['events'].append(dict(d['events'][0],amount='-0.020000500'))
    assert assess(d)['unique_events']==3 and assess(d)['duplicate_events']==1
    d['events'][-1]['amount']='-2'
    with pytest.raises(ValueError):assess(d)


@pytest.mark.parametrize('change',[{'amount':'NaN'},{'amount':0.1},{'time_ms':2000},{'symbol':'BTCUSDT'},{'kind':'MYSTERY'},{'asset':'usdt'}])
def test_invalid_event_rejected(change):
    d=document();d['events'][0].update(change)
    with pytest.raises(ValueError):assess(d)


def test_report_identity_order_and_precision_are_stable():
    d=document();r=assess(d)
    d['events'].reverse()
    with localcontext() as ctx:
        ctx.prec=2
        assert assess(d)==r


def test_matching_commissions_are_not_added_twice_and_missing_is_unknown():
    r=reconcile(fill_document(),document())
    assert r['commission_checks']=={'USDT':{'status':'MATCHING_DECLARED_TOTALS','fill_cost':'0.0200005','cash_change':'-0.0200005','difference':'0'}}
    assert r['realized_pnl_check']['status']=='MISSING_CASH_CATEGORY'
    assert r['funding_by_asset']=={'USDT':'-0.1'}
    assert r['net_pnl'] is None and r['authoritative'] is False
    d=document();d['events'][0]['amount']='-0.03'
    assert reconcile(fill_document(),d)['commission_checks']['USDT']['status']=='MISMATCH'


@pytest.mark.parametrize('change',[{'account_ref':'b'*64},{'start_ms':999},{'end_ms':2001}])
def test_cross_account_or_window_comparison_rejected(change):
    d=document();d.update(change)
    with pytest.raises(ValueError):reconcile(fill_document(),d)


def test_cash_rebate_sign_and_cross_asset_do_not_get_converted():
    f=fill_document();f['fills'][0].update(commission='-0.02',commission_asset='BNB')
    d=document();d['events'][0].update(amount='0.02',asset='BNB')
    r=reconcile(f,d)
    assert r['commission_checks']['BNB']['status']=='MATCHING_DECLARED_TOTALS'
    assert 'USDT' not in r['commission_checks']
