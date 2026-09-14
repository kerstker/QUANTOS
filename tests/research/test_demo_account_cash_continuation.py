"""A later cash window compares its increment while preserving the original book."""
from copy import deepcopy
from decimal import Decimal
import pytest

from services.research.ai_factory_store import digest
from services.research.demo_cash_chain import project_cash_chain
from services.research.demo_execution_journal import DemoExecutionJournal
from services.research.demo_origin_contract import contract_from_origin
from tests.research.test_demo_account_recovery import inputs as account_inputs
from tests.research.test_demo_cash_window import fixture as cash_input, NOW as CASH_NOW
from tests.research.test_demo_execution_journal import manifest
from tests.research.test_binance_order_receipts import message, receipt
from tests.tools.test_binance_demo_execution_origin import NOW


def fixture(tmp_path):
    prefix=account_inputs(tmp_path); at=NOW+100000; cash=cash_input(); offset=at-CASH_NOW
    cash['observed_at_ms']+=offset;cash['completed_at_ms']+=offset
    cash['account_ref_sha256']=prefix['opening']['account_ref_sha256']
    for r in cash['reads']:
        r['requested_at_ms']+=offset;r['received_at_ms']+=offset
        if r['account_ref_sha256']:r['account_ref_sha256']=cash['account_ref_sha256']
        if r['kind'].startswith('clock'):r['data']['serverTime']+=offset
        elif r['kind'].startswith('wallet'):
            r['data']=[dict(asset='USDC',wallet='4999.6801' if r['kind']=='wallet_before' else '4999.5801',updated_at_ms=at-1000)]
        else:r['data']=[dict(symbol='BTCUSDC',incomeType='COMMISSION',income='-0.1',asset='USDC',time=at+11000,tranId='50',tradeId='3')]
    m=manifest();m.update(initial_collateral='4999.6801',contract=contract_from_origin(prefix['opening'])[0],
        adv_notional=prefix['adv_notional'],participation_rate=prefix['participation_rate'],expected_end_position='2')
    m['actions']=[dict(timestamp=at+10000,kind='ORDER',order_id='new-buy',side='BUY',quantity='2',price='100')]
    m['native'].update(schema='BINANCE_USDM_ORDER_RECEIPTS_V2', account_ref_sha256=cash['account_ref_sha256'],
        action_hash=digest(m['actions']),coverage=dict(start_ms=at-5000,end_ms=at+70000,gaps=[]),
        bindings=[dict(order_id='new-buy',client_order_id='QDnewbuy',venue_order_id='30',time_in_force='IOC',reduce_only=False)])
    p=message(at+11000,'FILLED','2','2',trade_id=3,fee='0.1')
    p['o'].update(c='QDnewbuy',i=30,q='2',p='100',L='100',f='IOC',m=False,rp='0')
    return prefix,m,[receipt(p,at+11005)],cash


def test_cash_window_does_not_recharge_the_historical_manual_incident(tmp_path):
    prefix,m,messages,cash=fixture(tmp_path)
    with DemoExecutionJournal(tmp_path/'new.db',manifest=m) as j:
        j.append(messages)
        r=project_cash_chain([j],cash,account_prefix=prefix)
        assert r['status']=='MATCHED_REPORTED_INPUTS',r['issues']
        assert r['canonical_wallet']=='4999.5801' and r['wallet_residual']=='0'
        assert r['reconciliation']['initial_equity']=='5000' and r['reconciliation']['fee']=='0.2199'
        assert r['window_fee']=='0.1' and r['window_realized_pnl']=='0'
        assert sum(map(Decimal,r['capacity_consumed'].values()))==Decimal('439.8')
        assert r['account_prefix_sha256']==digest(prefix)


@pytest.mark.parametrize('damage',['wallet','account','future','capacity','contract'])
def test_later_window_cannot_replace_or_rebudget_reported_prefix(tmp_path,damage):
    prefix,m,messages,cash=fixture(tmp_path)
    if damage=='wallet':m['initial_collateral']='5000'
    elif damage=='account':m['native']['account_ref_sha256']='a'*64
    elif damage=='future':prefix['statements'][1]['completed_at_ms']=NOW+300000
    elif damage=='capacity':m['adv_notional']='9999999'
    else:m['contract']=deepcopy(m['contract']);m['contract']['min_notional']='101'
    with DemoExecutionJournal(tmp_path/'new.db',manifest=m) as j:
        j.append(messages)
        r=project_cash_chain([j],cash,account_prefix=prefix)
        assert r['status']=='UNRESOLVED' and r['canonical_wallet'] is None
