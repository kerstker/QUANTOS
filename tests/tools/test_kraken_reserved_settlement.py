from copy import deepcopy
import json
import pytest

from tools import kraken_funding_reservations as book
from tools import kraken_account_snapshot as account_tool
from tests.tools.test_kraken_cash_discovery import setup, NOW
from tests.tools.test_kraken_funding_reservations import request
from tests.tools.test_kraken_public_capture import capture


def fixture(root, *, zero_fill=False, order_status='canceled', reservation_delay=0):
    reader,cash,opening,closing=setup(root)
    _,requirements,drafts=request()
    drafts[0].update(volume='1',limit_price='100')
    requirements['orders'][0]['draft_sha256']=account_tool.digest(drafts[0])
    reservation_account=deepcopy(opening)
    if reservation_delay:
        reservation_account['as_of_ms']+=reservation_delay;reservation_account['observed_at_ms']+=reservation_delay
        for r in reservation_account['reads']:
            r['requested_at_ms']+=reservation_delay;r['received_at_ms']+=reservation_delay
        reservation_account['result_hash']=account_tool.digest({k:v for k,v in reservation_account.items() if k!='result_hash'})
    reserved=book.reserve(root,reservation_account,requirements,drafts,batch_id='1'*32,as_of_ms=NOW+600+reservation_delay)
    original=reader.read
    def read(endpoint,params):
        if zero_fill and endpoint=='Ledgers':return b'{"error":[],"result":{"count":0,"ledger":{}}}'
        value=original(endpoint,params)
        if endpoint=='QueryOrders':
            body=json.loads(value);row=body['result']['ORDER-1']
            row.update(cl_ord_id=drafts[0]['client_order_id'],opentm=str(NOW//1000+1+reservation_delay//1000),status=order_status)
            if zero_fill:row.update(vol_exec='0',cost='0',fee='0',trades=[])
            row['descr']['price']='100';value=json.dumps(body).encode()
        return value
    reader.read=read
    if zero_fill:
        closing['balances']=deepcopy(opening['balances'])
        closing['result_hash']=account_tool.digest({k:v for k,v in closing.items() if k!='result_hash'})
        cash=account_tool.collect_cash_bridge(reader,root,opening,closing,clock_ms=lambda:NOW+63000)
    recovery=account_tool.track_orders(reader,root,[{'order_id':'ORDER-1'}],cash=cash,clock_ms=lambda:NOW+63000)
    return reserved,dict(batch_id='1'*32,cash=cash,recovery=recovery,
                         market_captures=[capture()[0]],as_of_ms=NOW+64000),closing


def test_settlement_releases_once_and_next_reservation_uses_closing_balance(tmp_path):
    reserved,args,closing=fixture(tmp_path)
    settled=book.settle(tmp_path,**args)
    assert settled['status']=='LOCAL_RESERVATION_SETTLED'
    assert settled['actual_debits']=={'ZUSD':'40.04'}
    assert book.settle(tmp_path,**args)==settled
    state=book.status(tmp_path,'a'*64)
    assert state['reserved_debits']=={} and state['generation']==2
    assert state['settled_account_floor']['result_hash']==closing['result_hash']
    _,requirements,drafts=request(2)
    with pytest.raises(ValueError):
        book.reserve(tmp_path,reserved['account'],requirements,drafts,batch_id='2'*32,as_of_ms=NOW+65000)
    result=book.reserve(tmp_path,closing,requirements,drafts,batch_id='2'*32,as_of_ms=NOW+65000)
    assert result['generation']==3 and result['budget_review']['assets']['ZUSD']['owned_available']=='709.96000001'


@pytest.mark.parametrize('change',['cash','identity','opening','cost_bound'])
def test_unresolved_proof_cannot_release(tmp_path,change):
    reserved,args,_=fixture(tmp_path)
    if change=='cash':args['cash']['closing_account_hash']='f'*64
    if change=='identity':
        args['batch_id']='2'*32
    if change=='opening':
        args['cash']['opening_account_hash']='f'*64
    if change=='cost_bound':
        # A second independent store with a smaller explicit claim must not
        # treat the same observed fill as within its debit bound.
        other=tmp_path/'other';other.mkdir(mode=0o700)
        from shutil import copytree
        for name in ('cash-windows','order-queries','trade-queries'):
            copytree(tmp_path/name,other/name)
        requirements=deepcopy(reserved['requirements'])
        requirements['orders'][0].update(principal_debits={'ZUSD':'1'},fee_debits={'ZUSD':'0'})
        book.reserve(other,reserved['account'],requirements,reserved['drafts'],batch_id='1'*32,as_of_ms=NOW+600)
        tmp_path=other
    with pytest.raises(ValueError):book.settle(tmp_path,**args)
    assert book.status(tmp_path,'a'*64)['reserved_debits']


def test_committed_release_recovers_marker_and_missing_proof_is_not_ignored(tmp_path,monkeypatch):
    _,args,_=fixture(tmp_path)
    original=book._write_marker
    def fail(*_):raise OSError('simulated publication failure')
    monkeypatch.setattr(book,'_write_marker',fail)
    with pytest.raises(OSError):book.settle(tmp_path,**args)
    monkeypatch.setattr(book,'_write_marker',original)
    assert book.status(tmp_path,'a'*64)['reserved_debits']=={}
    assert book.settle(tmp_path,**args)['generation']==2
    (tmp_path/'cash-windows'/(args['cash']['evidence_hash']+'.json')).unlink()
    with pytest.raises((ValueError,OSError)):book.status(tmp_path,'a'*64)


def test_account_checkpoint_refresh_automatically_settles_local_claim(tmp_path):
    from tools import kraken_settlement_checkpoint as checkpoints
    from tests.tools.test_kraken_portfolio_cycle import setup as cycle_setup
    reserved,args,closing=fixture(tmp_path)
    market=tmp_path/'market';observer_args=cycle_setup(tmp_path)
    observer_args['observe'](market,observer_args['config'])
    account_tool.atomic_json(tmp_path/'cash-baseline.json',reserved['account'])
    account_tool.save(tmp_path,closing)
    checkpoints.initialise(tmp_path,reserved['account'],market_root=market)
    result=checkpoints.refresh(tmp_path,args['cash'],args['recovery'],market_root=market,as_of_ms=NOW+64000)
    assert result is not None
    assert book.status(tmp_path,'a'*64)['reserved_debits']=={}


def test_portfolio_cycle_rejects_old_snapshot_even_after_claim_released(tmp_path):
    from tools import kraken_portfolio_cycle as cycle
    from tests.tools.test_kraken_portfolio_cycle import setup as cycle_setup,write_inputs
    reserved,args,closing=fixture(tmp_path)
    book.settle(tmp_path,**args)
    cycle_args=cycle_setup(tmp_path);write_inputs(cycle_args)
    cycle_args['clock_ms']=lambda:NOW+65000
    result=cycle.run_once(**cycle_args)
    assert result['status']=='INPUT_REJECTED' and result['inventory'] is None
    account_tool.atomic_json(cycle_args['account_path'],closing)
    assert cycle.run_once(**cycle_args)['status']=='REVIEW_ONLY'


def test_unfilled_cancellation_can_settle_but_open_partial_order_cannot(tmp_path):
    zero=tmp_path/'zero';zero.mkdir(mode=0o700)
    _,args,_=fixture(zero,zero_fill=True)
    result=book.settle(zero,**args)
    assert result['actual_debits']=={} and book.status(zero,'a'*64)['reserved_debits']=={}
    pending=tmp_path/'pending';pending.mkdir(mode=0o700)
    _,args,_=fixture(pending,order_status='open')
    with pytest.raises(ValueError):book.settle(pending,**args)
    assert book.status(pending,'a'*64)['reserved_debits']=={'ZUSD':'401'}


def test_interrupted_release_transaction_keeps_reservation(tmp_path,monkeypatch):
    _,args,_=fixture(tmp_path)
    original=book._append
    def interrupted(conn,record):
        original(conn,record)
        raise KeyboardInterrupt
    monkeypatch.setattr(book,'_append',interrupted)
    with pytest.raises(KeyboardInterrupt):book.settle(tmp_path,**args)
    assert book.status(tmp_path,'a'*64)['reserved_debits']=={'ZUSD':'401'}
    monkeypatch.setattr(book,'_append',original)
    assert book.settle(tmp_path,**args)['generation']==2


def test_changed_or_deleted_settlement_event_rejected(tmp_path):
    import sqlite3
    _,args,_=fixture(tmp_path);book.settle(tmp_path,**args)
    with sqlite3.connect(tmp_path/'funding-reservations.sqlite') as conn:
        conn.execute('DELETE FROM reservations WHERE generation=2')
    with pytest.raises(ValueError):book.status(tmp_path,'a'*64)


def test_reservation_snapshot_inside_cash_window_is_reconciled(tmp_path):
    _,args,_=fixture(tmp_path,reservation_delay=1000)
    assert book.settle(tmp_path,**args)['status']=='LOCAL_RESERVATION_SETTLED'


@pytest.mark.parametrize('case',['balance','activity'])
def test_intermediate_reservation_snapshot_needs_exact_balance_and_quiet_sweep(tmp_path,case):
    from tools.kraken_reserved_settlement import _check_reservation_snapshot
    from tools.kraken_settlement_review import archive,replay_cash
    saved,args,_=fixture(tmp_path,reservation_delay=1000)
    account=deepcopy(saved['account'])
    if case=='balance':account['balances']['ZUSD']['balance']='999'
    else:
        account['reads'][0]['requested_at_ms']=NOW+30000
        account['observed_at_ms']=NOW+30350
    window=archive(tmp_path,'cash-windows',args['cash']['evidence_hash'])
    bridge=replay_cash(tmp_path,args['cash'])
    with pytest.raises(ValueError):_check_reservation_snapshot(window,bridge,account)
