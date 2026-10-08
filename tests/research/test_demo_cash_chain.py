"""One ledger must retain basis, fees and capacity across observed batch starts."""
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal
import json

import pytest

from services.research.ai_factory_store import digest
from services.research.demo_execution_journal import DemoExecutionJournal, observations, replay_ledger, prepare
from services.research.demo_opening_projection import bind_opening
from services.research.demo_cash_chain import project_cash_chain
from quantos.contracts.binance_demo_account import DemoAccountCapture
from tests.research.test_demo_cash_projection import inputs
from tests.research.test_demo_account_baseline import captured
from tests.research.test_binance_order_receipts import message, receipt
from tests.research.test_demo_cash_window import NOW


def account_at(at, *, quantity='1', wallet='999.9802', basis='99'):
    raw=captured().model_dump(); offset=at-raw['observed_at_ms']
    raw['observed_at_ms']+=offset; raw['completed_at_ms']+=offset
    for r in raw['reads']:
        r['started_at_ms']+=offset; r['received_at_ms']+=offset
        r['data']=json.loads(json.dumps(r['data']).replace('USDT','USDC'))
        if r['kind'].startswith('account'):
            r['data']['positions'][0]['positionAmt']=quantity
            r['data']['assets'][0].update(walletBalance=wallet,crossWalletBalance=wallet,
                availableBalance=wallet,unrealizedProfit='0',marginBalance=wallet)
        if r['kind']=='positions':
            r['data'][0].update(positionAmt=quantity,entryPrice=str(Decimal(basis)/Decimal(quantity)),
                markPrice=str(Decimal(basis)/Decimal(quantity)),unRealizedProfit='0',updateTime=at-1)
    return DemoAccountCapture.model_validate(raw)


def fixture(tmp_path):
    first, messages, cash=inputs()
    first['native']['coverage']['end_ms']=NOW+25000
    # Original ledger prefix supplies exact basis, never the rounded exchange average.
    with DemoExecutionJournal(tmp_path/'prefix.db',manifest=first) as j:
        j.append(messages); e=observations(j.manifest,j.read()[0],j.parsed)['native_observations']
        state=replay_ledger(j.manifest,j.parsed,e,cash_events=[]).state_as_canonical_dict()
    second=deepcopy(first)
    second['native'].update(schema='BINANCE_USDM_ORDER_RECEIPTS_V2',
        coverage={'start_ms':NOW+20101,'end_ms':NOW+70000,'gaps':[]},
        bindings=[dict(order_id='fixed-close',client_order_id='client-close',venue_order_id='124',
                       time_in_force='IOC',reduce_only=True)])
    second['actions']=[dict(timestamp=NOW+44000,kind='ORDER',order_id='fixed-close',
        side='SELL',quantity='0.4',price='110')]
    second['native']['action_hash']=digest(second['actions']); second['expected_end_position']='0.6'
    second=bind_opening(second,account_at(NOW+20000),basis=[dict(symbol='BTCUSDC',entry_notional='99',source_sha256=digest(state))])
    p=message(NOW+45000,'FILLED','0.4','0.4',trade_id=9,fee='0.0088')
    p['o'].update(c='client-close',S='SELL',i=124,p='110',L='110',q='0.4',f='IOC',R=True,m=False)
    closing=[receipt(p,NOW+45100)]
    for r in cash['reads']:
        if r['kind']=='wallet_after':r['data'][0]['wallet']='1104.1714'
        if r['kind']=='income':r['data'] += [dict(symbol='BTCUSDC',incomeType=k,income=v,asset='USDC',
            time=NOW+45000,tranId=i,tradeId='9') for k,v,i in [('COMMISSION','-0.0088','105'),('REALIZED_PNL','4.4','106')]]
    return [(first,messages),(second,closing)],cash


def run(tmp_path,change=lambda batches,cash:None):
    batches,cash=fixture(tmp_path);change(batches,cash)
    with ExitStack() as stack:
        js=[stack.enter_context(DemoExecutionJournal(tmp_path/f'batch-{i}.db',manifest=m)) for i,(m,_) in enumerate(batches)]
        for j,(_,messages) in zip(js,batches):j.append(messages)
        before=[j.read() for j in js];result=project_cash_chain(js,cash)
        assert [j.read() for j in js]==before
    with ExitStack() as stack:
        recovered=[stack.enter_context(DemoExecutionJournal(tmp_path/f'batch-{i}.db',read_only=True)) for i in range(len(js))]
        assert project_cash_chain(recovered,cash)==result
    return result


def test_partial_reduction_carries_original_basis_and_costs_without_restarting_pnl(tmp_path):
    r=run(tmp_path)
    assert r['status']=='MATCHED_REPORTED_INPUTS',r['issues']
    assert r['canonical_wallet']=='1104.1714' and r['wallet_residual']=='0'
    assert r['positions']['BTCUSDC']['quantity']=='0.6'
    assert r['positions']['BTCUSDC']['entry_notional']=='59.4'
    assert r['reconciliation']['initial_equity']=='1000'
    assert r['reconciliation']['fee']=='0.0286'
    assert r['reconciliation']['funding']=='-0.2' and r['reconciliation']['external_cash_flow']=='100'
    assert r['continuations'][0]['status']=='MATCHED_CANONICAL_PREFIX'
    assert r['realized_pnl_comparison']=='MATCHED_REPORTED_TOTAL'
    assert r['strategy_pnl'] is None and r['authoritative'] is False
    assert r['source_verified'] is False and r['coverage_verified'] is False


@pytest.mark.parametrize('bad', ['basis','source','wallet','position','gap','contract','capacity','account','missing_opening','reverse'])
def test_changed_or_incomplete_continuation_cannot_invent_a_closed_chain(tmp_path,bad):
    def change(batches,cash):
        m=batches[1][0]
        if bad=='basis':m['opening']['basis'][0]['entry_notional']='100'
        elif bad=='source':m['opening']['basis'][0]['source_sha256']='f'*64
        elif bad in ('wallet','position'):
            raw=account_at(NOW+20000,wallet='1000' if bad=='wallet' else '999.9802',quantity='2' if bad=='position' else '1')
            m.pop('opening');batches[1]=(bind_opening(m,raw,basis=[dict(symbol='BTCUSDC',entry_notional='99',source_sha256='f'*64)]),batches[1][1])
        elif bad=='gap':m['native']['coverage']['start_ms']=NOW+26000
        elif bad=='contract':m['contract']['margin_brackets'][0]['initial_margin_rate']='0.2'
        elif bad=='capacity':m['adv_notional']='1000001'
        elif bad=='account':m['native']['account_ref_sha256']='f'*64;m['opening']['account_ref_sha256']='f'*64
        elif bad=='missing_opening':m.pop('opening');m['schema']='DEMO_EXECUTION_REPLAY_V1';m['initial_position']='0'
        elif bad=='reverse':batches.reverse()
    r=run(tmp_path,change)
    assert r['status']=='UNRESOLVED' and r['issues'] and r['canonical_wallet'] is None
    assert r['strategy_pnl'] is None


def prefix_source(batches):
    m,messages=batches[0]; parsed=prepare(m)
    e=observations(m,messages,parsed)['native_observations']
    state=replay_ledger(m,parsed,e,cash_events=[]).state_as_canonical_dict()
    batches[1][0]['opening']['basis'][0]['source_sha256']=digest(state)


def test_single_chain_uses_cumulative_capacity_not_a_fresh_budget_per_batch(tmp_path):
    def change(batches,cash):
        # 99+44 exceeds 120, while each isolated batch fits.
        for m,_ in batches:m['adv_notional']='1200'
        prefix_source(batches)
    r=run(tmp_path,change)
    assert r['status']=='UNRESOLVED' and r['canonical_wallet'] is None
    assert r['continuations'][0]['status']=='MATCHED_CANONICAL_PREFIX'
    assert r['issues']==['CHAIN_REPLAY_REPORTEDFILLREPLAYERROR']


def test_overlapping_receipt_windows_do_not_duplicate_reported_income(tmp_path):
    def change(batches,cash):
        for r in cash['reads']:
            if r['kind']=='income':r['data']+=deepcopy(r['data'])
    r=run(tmp_path,change)
    assert r['status']=='MATCHED_REPORTED_INPUTS' and r['reconciliation']['fee']=='0.0286'


def test_cash_during_account_observation_keeps_continuity_unknown(tmp_path):
    def change(batches,cash):
        for r in cash['reads']:
            if r['kind']=='income':r['data'][2]['time']=NOW+20050
    r=run(tmp_path,change)
    assert 'CONTINUATION_EVENT_OVERLAPS_OBSERVATION' in r['issues']
    assert r['status']=='UNRESOLVED' and r['canonical_wallet'] is None


@pytest.mark.parametrize('offset',[-1000,1000])
def test_separate_brackets_preserve_original_basis_and_cash_across_batch_clocks(tmp_path,offset):
    def change(batches,cash):
        for m,messages in batches:
            n=m['native'];n['schema']='BINANCE_USDM_ORDER_RECEIPTS_V3'
            for b in n['bindings']:b.setdefault('time_in_force','GTX');b.setdefault('reduce_only',False)
            start=n['coverage']['start_ms']-1000;end=n['coverage']['end_ms']+100
            n['clock_bracket']={'schema':'BINANCE_USDM_CLOCK_BRACKET_V1','provenance':'SYNTHETIC_TEST_FIXTURE',
                'before':dict(requested_at_ms=start,received_at_ms=start+20,server_time_ms=start+10+offset,response_sha256='a'*64,source_sha256='b'*64),
                'after':dict(requested_at_ms=end,received_at_ms=end+20,server_time_ms=end+10+offset,response_sha256='c'*64,source_sha256='d'*64)}
            for i,r in enumerate(messages):
                p=json.loads(r['raw_utf8']);p['E']+=offset;p['T']+=offset;p['o']['T']+=offset
                messages[i]=receipt(p,r['received_at_ms'])
        for r in cash['reads']:
            if r['kind'].startswith('clock'):r['data']['serverTime']+=offset
            if r['kind']=='income':
                for flow in r['data']:flow['time']+=offset
        prefix_source(batches)
    r=run(tmp_path,change)
    assert r['status']=='MATCHED_REPORTED_INPUTS',r['issues']
    assert r['canonical_wallet']=='1104.1714' and r['positions']['BTCUSDC']['entry_notional']=='59.4'
    assert all(b['clock']['exact_latency_ms'] is None for b in r['batches'])


def test_single_batch_chain_reuses_existing_cash_ledger_identity(tmp_path):
    from services.research.demo_cash_projection import project_cash_window
    m,messages,cash=inputs()
    with DemoExecutionJournal(tmp_path/'single.db',manifest=m) as j:
        j.append(messages);single=project_cash_window(j,cash);chain=project_cash_chain([j],cash)
    assert chain['status']=='MATCHED_REPORTED_INPUTS'
    assert chain['ledger_state_hash']==single['ledger_state_hash']
    assert chain['reconciliation']==single['reconciliation'] and chain['continuations']==[]


def test_reused_original_order_identity_is_not_double_counted_as_another_batch(tmp_path):
    def change(batches,cash):
        m,messages=batches[1];m['native']['bindings'][0]['client_order_id']='client-1'
        p=json.loads(messages[0]['raw_utf8']);p['o']['c']='client-1';messages[0]=receipt(p,messages[0]['received_at_ms'])
    r=run(tmp_path,change)
    assert r['issues']==['ORDER_IDENTITY_REUSED_IN_CHAIN'] and r['canonical_wallet'] is None


def test_three_batches_cover_five_minute_cash_without_resetting_original_book(tmp_path):
    from services.execution.perpetual_ledger_v2 import ReportedCashFlowEvent
    from services.research.demo_execution_journal import apply_reported_events, reported_fill_events
    batches,cash=fixture(tmp_path)
    first,messages=batches[0];second,closing=batches[1]
    first['native']['coverage']['end_ms']=NOW+145000
    cash['observe_seconds']=300;cash['completed_at_ms']=NOW+350000
    for r in cash['reads'][2:]:
        r['requested_at_ms']+=240000;r['received_at_ms']+=240000
        if r['kind']=='clock_after':r['data']['serverTime']+=240000
        if r['kind']=='income':
            for f in r['data']:
                if f['tradeId']=='9':f['time']=NOW+211000
    parsed=prepare(first);e=observations(first,messages,parsed)['native_observations']
    flows=[ReportedCashFlowEvent(timestamp=f['time'],account_ref_sha256=cash['account_ref_sha256'],asset='USDC',
        income_type=f['incomeType'],transaction_id=f['tranId'],amount=Decimal(f['income']),
        instrument_id='BTCUSDC' if f['incomeType']=='FUNDING_FEE' else None,source_sha256=digest(cash))
        for f in cash['reads'][-1]['data'] if f['incomeType'] in ('FUNDING_FEE','TRANSFER')]
    ledger=replay_ledger(first,parsed,e,cash_events=flows)
    second.pop('opening');second['native']['coverage'].update(start_ms=NOW+140101,end_ms=NOW+280000)
    second['actions'][0]['timestamp']=NOW+210000;second['native']['action_hash']=digest(second['actions'])
    second=bind_opening(second,account_at(NOW+140000,wallet='1099.7802'),
        basis=[dict(symbol='BTCUSDC',entry_notional='99',source_sha256=digest(ledger.state_as_canonical_dict()))])
    p=json.loads(closing[0]['raw_utf8']);p['E']=NOW+211001;p['T']=p['o']['T']=NOW+211000
    closing=[receipt(p,NOW+211100)];batches[1]=(second,closing)
    second_parsed=prepare(second);second_e=observations(second,closing,second_parsed)['native_observations']
    events,reduce_only=reported_fill_events(second,second_parsed,second_e)
    apply_reported_events(ledger,events,reduce_only)
    third=deepcopy(second);third.pop('opening')
    third['native']['coverage'].update(start_ms=NOW+270101,end_ms=NOW+310000)
    third['actions'][0].update(timestamp=NOW+285000,order_id='fixed-timeout',quantity='0.6')
    third['native']['action_hash']=digest(third['actions'])
    third['native']['bindings'][0].update(order_id='fixed-timeout',client_order_id='client-timeout',venue_order_id='125')
    third=bind_opening(third,account_at(NOW+270000,quantity='0.6',wallet='1104.1714',basis='59.4'),
        basis=[dict(symbol='BTCUSDC',entry_notional='59.4',source_sha256=digest(ledger.state_as_canonical_dict()))])
    p=message(NOW+285100,'EXPIRED');p['o'].update(c='client-timeout',S='SELL',i=125,p='110',q='0.6',f='IOC',R=True,x='EXPIRED',m=False)
    batches.append((third,[receipt(p,NOW+285200)]))
    with ExitStack() as stack:
        js=[stack.enter_context(DemoExecutionJournal(tmp_path/f'five-{i}.db',manifest=m)) for i,(m,_) in enumerate(batches)]
        for j,(_,messages) in zip(js,batches):j.append(messages)
        r=project_cash_chain(js,cash)
    assert r['status']=='MATCHED_REPORTED_INPUTS',r['issues']
    assert len(r['batches'])==3 and len(r['continuations'])==2
    assert r['reconciliation']['fee']=='0.0286' and r['canonical_wallet']=='1104.1714'
    assert list(r['capacity_consumed'].values())==['143']
