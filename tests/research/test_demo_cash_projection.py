"""Real-shaped reported inputs exercise the single canonical cash projection."""
from copy import deepcopy
import json

import pytest

from services.research.demo_execution_journal import DemoExecutionJournal
from services.research.demo_cash_projection import project_cash_window
from services.research.ai_factory_store import digest
from tests.research.test_demo_execution_journal import manifest
from tests.research.test_binance_order_receipts import native_spec, edit_message
from tests.research.test_binance_order_receipts import receipt, message
from tests.research.test_demo_cash_window import fixture, NOW, REF


def inputs():
    m = manifest(); source = native_spec(); cash = fixture()
    # Explicit flat technical origin, independent of the venue wallet's history.
    for read in cash['reads']:
        if read['kind'].startswith('wallet'):
            read['data'][0]['asset'] = 'USDC'
    cash['reads'][2]['data'][0]['wallet'] = '1099.7802'
    m['actions'][0]['timestamp'] = NOW + 10000
    native = source['raw_observed']
    native['account_ref_sha256'] = REF
    native['coverage'] = {'start_ms': NOW-5000, 'end_ms': NOW+70000, 'gaps': []}
    for i, record in enumerate(native['messages']):
        record['received_at_ms'] = NOW + 11000 + i*1000
        def move(p, i=i):
            p['E'] = p['T'] = p['o']['T'] = NOW+10900+i*1000
        edit_message(source, i, move)
    messages = native.pop('messages'); native['action_hash'] = digest(m['actions'])
    m['native'] = native
    fees = [('101', '-0.00792'), ('102', '-0.01188')]
    trades = [json.loads(r['raw_utf8'])['o'] for r in messages[1:]]
    rows = [dict(symbol='BTCUSDC',incomeType='COMMISSION',income=fee,asset='USDC',
                 time=t['T'],tranId=tid,tradeId=str(t['t'])) for (tid,fee),t in zip(fees,trades)]
    rows += [dict(symbol='BTCUSDC',incomeType='FUNDING_FEE',income='-0.2',asset='USDC',
                  time=NOW+30000,tranId='103',tradeId=''),
             dict(symbol='',incomeType='TRANSFER',income='100',asset='USDC',
                  time=NOW+40000,tranId='104',tradeId='')]
    for read in cash['reads']:
        if read['kind'] == 'income': read['data'] = deepcopy(rows)
    return m, messages, cash


def run(tmp_path, mutate=lambda m, messages, cash: None):
    m, messages, cash = inputs(); mutate(m, messages, cash)
    with DemoExecutionJournal(tmp_path/'input.db', manifest=m) as journal:
        journal.append(messages)
        before = journal.read()
        report = project_cash_window(journal, cash)
        assert journal.read() == before
    with DemoExecutionJournal(tmp_path/'input.db', read_only=True) as recovered:
        assert project_cash_window(recovered, cash) == report
    return report


def test_exact_cash_projection_deduplicates_commissions_and_separates_external_money(tmp_path):
    r = run(tmp_path)
    assert r['status'] == 'MATCHED_REPORTED_INPUTS'
    assert r['canonical_wallet'] == '1099.7802' and r['wallet_residual'] == '0'
    assert r['reconciliation']['fee'] == '0.0198'
    assert r['reconciliation']['funding'] == '-0.2'
    assert r['reconciliation']['external_cash_flow'] == '100'
    assert r['fee_comparison'] == 'MATCHED_REPORTED_TRADES'
    assert r['strategy_pnl'] is None and r['authoritative'] is False
    assert r['source_verified'] is False and r['coverage_verified'] is False


@pytest.mark.parametrize('failure', ['namespace','wallet','fee','trade_id','extra_trade','funding_symbol',
    'income_type','window','missing_receipt','gap','endpoint','cash_sweep','initial_wallet'])
def test_missing_or_conflicting_inputs_do_not_invent_a_cash_match(tmp_path, failure):
    def change(m, messages, cash):
        if failure=='namespace': cash['account_ref_sha256']='f'*64; [r.update(account_ref_sha256='f'*64) for r in cash['reads'] if not r['kind'].startswith('clock')]
        elif failure=='wallet': cash['reads'][2]['data'][0]['wallet']='1099.7801'
        elif failure=='initial_wallet': cash['reads'][1]['data'][0]['wallet']='1001'
        elif failure=='window': m['native']['coverage']['end_ms']=NOW+50000
        elif failure=='missing_receipt': messages.pop()
        elif failure=='gap': m['native']['coverage']['gaps']=[{'start_ms':NOW+20000,'end_ms':NOW+21000}]
        elif failure=='cash_sweep': cash['reads'][-1]['data'].pop()
        else:
            for r in cash['reads']:
                if r['kind']!='income':continue
                if failure=='fee':r['data'][0]['income']='-0.008'
                elif failure=='trade_id':r['data'][0]['tradeId']='999'
                elif failure=='extra_trade':r['data'].append(dict(r['data'][0],tranId='999',tradeId='999'))
                elif failure=='funding_symbol':r['data'][2]['symbol']='ETHUSDC'
                elif failure=='income_type':r['data'][3]['incomeType']='INSURANCE_CLEAR'
                elif failure=='endpoint':r['data'][3]['time']=NOW+60300
    r = run(tmp_path, change)
    assert r['status'] in ('UNRESOLVED','DIFFERENCE_FOUND')
    assert r['issues'] and r['strategy_pnl'] is None


def test_same_reported_flow_in_both_sweeps_is_never_posted_twice(tmp_path):
    def duplicate(m, messages, cash):
        for r in cash['reads']:
            if r['kind']=='income':r['data'] += deepcopy(r['data'])
    r = run(tmp_path, duplicate)
    assert r['status']=='MATCHED_REPORTED_INPUTS' and r['canonical_wallet']=='1099.7802'
    assert r['cash_input_duplicates']==8


@pytest.mark.parametrize('pnl_difference', [False, True])
def test_closing_trade_realized_cash_is_compared_without_second_credit(tmp_path, pnl_difference):
    def close(m, messages, cash):
        m['actions'].append(dict(m['actions'][0], timestamp=NOW+44000, order_id='fixed-sell', side='SELL', price='110'))
        m['native']['bindings'].append({'order_id':'fixed-sell','venue_order_id':'124','client_order_id':'client-2'})
        m['native']['action_hash']=digest(m['actions']); m['expected_end_position']='0'
        p=message(NOW+45000,'FILLED','1','1',trade_id=9,fee='0.022')
        p['o'].update(c='client-2',S='SELL',i=124,p='110',L='110')
        messages.append(receipt(p,NOW+45100))
        for r in cash['reads']:
            if r['kind']=='wallet_after':r['data'][0]['wallet']='1110.7582'
            if r['kind']=='income':
                r['data'] += [dict(symbol='BTCUSDC',incomeType=k,income=v,asset='USDC',time=NOW+45000,tranId=i,tradeId='9')
                    for k,v,i in [('COMMISSION','-0.022','105'),('REALIZED_PNL','12' if pnl_difference else '11','106')]]
    r=run(tmp_path, close)
    assert r['canonical_wallet']=='1110.7582'
    assert r['reconciliation']['fee']=='0.0418'
    assert r['status']==('DIFFERENCE_FOUND' if pnl_difference else 'MATCHED_REPORTED_INPUTS')
    assert r['realized_pnl_comparison']==('DIFFERENCE_FOUND' if pnl_difference else 'MATCHED_REPORTED_TOTAL')


def test_missing_reported_fee_remains_unknown_instead_of_crashing_projection(tmp_path):
    def missing(m, messages, cash):
        p=json.loads(messages[1]['raw_utf8']);p['o'].pop('n');p['o'].pop('N')
        messages[1]=receipt(p,messages[1]['received_at_ms'])
    r=run(tmp_path, missing)
    assert r['status']=='UNRESOLVED' and r['canonical_wallet'] is None
    assert 'RECEIPT_PROJECTION_UNRESOLVED' in r['issues']
