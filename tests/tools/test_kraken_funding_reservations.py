from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from tests.tools.test_kraken_funding_review import fixture, NOW
from tools import kraken_funding_reservations as book
from tools.kraken_public_capture import digest


def request(number=1):
    account, requirements, drafts = fixture()
    oid = f'{number:032x}'
    drafts[0]['client_order_id'] = oid
    requirements['orders'][0].update(client_order_id=oid, draft_sha256=digest(drafts[0]))
    return account, requirements, drafts


def reserve(root, number=1, values=None):
    account, requirements, drafts = values or request(number)
    return book.reserve(root, account, requirements, drafts, batch_id=f'{number:032x}', as_of_ms=NOW+600)


def test_restart_and_retry_keep_exact_reservation_without_double_counting(tmp_path):
    first = reserve(tmp_path)
    assert first['status'] == 'LOCAL_BUDGET_RESERVED'
    assert first['reservation_created'] is True and first['submission_enabled'] is False
    assert reserve(tmp_path) == first
    state = book.status(tmp_path, 'a'*64)
    assert state['reserved_debits'] == {'ZUSD': '401'} and state['generation'] == 1
    assert reserve(tmp_path, 2)['status'] == 'BLOCKED'
    assert book.status(tmp_path, 'a'*64)['generation'] == 1


def test_two_concurrent_batches_cannot_reserve_the_same_cash(tmp_path):
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve, tmp_path, n) for n in (1, 2)]
        results = [future.result() for future in futures]
    assert sorted(r['status'] for r in results) == ['BLOCKED', 'LOCAL_BUDGET_RESERVED']
    assert book.status(tmp_path, 'a'*64)['reserved_debits'] == {'ZUSD': '401'}


@pytest.mark.parametrize('change', ['draft', 'fee', 'account', 'reserves', 'order_reused'])
def test_identity_and_policy_cannot_be_rebound(tmp_path, change):
    reserve(tmp_path)
    account, requirements, drafts = request()
    batch = '1'.zfill(32)
    if change == 'draft': drafts[0]['volume'] = '99'
    if change == 'fee': requirements['orders'][0]['fee_debits']['ZUSD'] = '0'
    if change == 'account':
        account['account_ref_sha256'] = requirements['account_ref_sha256'] = 'b'*64
        account['result_hash'] = digest({k:v for k,v in account.items() if k != 'result_hash'})
    if change == 'reserves': requirements['cash_reserves'] = {'ZUSD': '0'}
    if change == 'order_reused': batch = '2'.zfill(32)
    with pytest.raises(ValueError):
        book.reserve(tmp_path, account, requirements, drafts, batch_id=batch, as_of_ms=NOW+600)
    assert book.status(tmp_path, 'a'*64)['reserved_debits'] == {'ZUSD': '401'}


def test_commit_before_marker_interruption_recovers_without_repeating(tmp_path, monkeypatch):
    reserve(tmp_path)
    account, requirements, drafts = request(2)
    requirements['orders'][0]['principal_debits'] = {'ZUSD': '10'}
    original = book._write_marker
    def interrupted(*args, **kwargs):
        raise OSError('simulated interrupted publication')
    monkeypatch.setattr(book, '_write_marker', interrupted)
    with pytest.raises(OSError): reserve(tmp_path, 2, (account, requirements, drafts))
    monkeypatch.setattr(book, '_write_marker', original)
    assert book.status(tmp_path, 'a'*64)['reserved_debits'] == {'ZUSD': '412'}
    assert reserve(tmp_path, 2, (account, requirements, drafts))['generation'] == 2


@pytest.mark.parametrize('change', ['database_deleted', 'tail_deleted', 'record_changed', 'marker_deleted'])
def test_missing_or_changed_history_never_resets_budget(tmp_path, change):
    reserve(tmp_path)
    db = tmp_path/'funding-reservations.sqlite'
    if change == 'database_deleted': db.unlink()
    if change == 'marker_deleted': (tmp_path/'funding-reservations-head.json').unlink()
    if change in ('tail_deleted', 'record_changed'):
        with sqlite3.connect(db) as conn:
            if change == 'tail_deleted': conn.execute('DELETE FROM reservations')
            else:
                row = json.loads(conn.execute('SELECT document FROM reservations').fetchone()[0])
                row['requirements']['orders'][0]['principal_debits']['ZUSD'] = '1'
                conn.execute('UPDATE reservations SET document=?', (json.dumps(row),))
    with pytest.raises(ValueError): reserve(tmp_path, 2)


def test_unresolved_costs_and_external_pending_are_not_lost(tmp_path):
    account, requirements, drafts = request()
    requirements['pending_debits'] = {'ZUSD': '300'}
    assert reserve(tmp_path, values=(account, requirements, drafts))['status'] == 'BLOCKED'
    requirements['pending_debits'] = {}
    requirements['orders'][0]['fee_debits'] = None
    assert reserve(tmp_path, values=(account, requirements, drafts))['status'] == 'BLOCKED'
    assert book.status(tmp_path, 'a'*64)['generation'] == 0


def test_killed_transaction_rolls_back_before_retry(tmp_path):
    import multiprocessing
    import os
    reserve(tmp_path)
    account, requirements, drafts = request(2)
    requirements['orders'][0]['principal_debits'] = {'ZUSD': '10'}
    def child():
        original = book._append
        def crash(conn, record):
            original(conn, record)
            os._exit(7)
        book._append = crash
        reserve(tmp_path, 2, (account, requirements, drafts))
    process = multiprocessing.get_context('fork').Process(target=child)
    process.start()
    try:
        process.join(10)
        assert process.exitcode == 7
    finally:
        if process.is_alive(): process.kill(); process.join(5)
    assert book.status(tmp_path, 'a'*64)['reserved_debits'] == {'ZUSD': '401'}
    assert reserve(tmp_path, 2, (account, requirements, drafts))['generation'] == 2


def test_elapsed_time_or_failed_refresh_never_releases_existing_claim(tmp_path):
    first = reserve(tmp_path)
    account, requirements, drafts = request()
    assert book.reserve(tmp_path, account, requirements, drafts, batch_id='1'.zfill(32), as_of_ms=NOW+200000) == first
    account, requirements, drafts = request(2)
    assert book.reserve(tmp_path, account, requirements, drafts, batch_id='2'.zfill(32),
                        as_of_ms=NOW+200000)['status'] == 'BLOCKED'
    assert book.status(tmp_path, 'a'*64)['reserved_debits'] == {'ZUSD': '401'}


def test_symlink_database_rejected(tmp_path):
    reserve(tmp_path)
    db = tmp_path/'funding-reservations.sqlite'
    target = tmp_path/'original.sqlite'; db.rename(target); db.symlink_to(target)
    with pytest.raises(ValueError, match='SYMLINK'): reserve(tmp_path, 2)


def test_rehashing_derived_budget_cannot_replace_recomputation(tmp_path):
    reserve(tmp_path)
    with sqlite3.connect(tmp_path/'funding-reservations.sqlite') as conn:
        row=json.loads(conn.execute('SELECT document FROM reservations').fetchone()[0])
        budget=row['budget_review'];budget['assets']['ZUSD']['new_debits']='1'
        budget['result_hash']=digest({k:v for k,v in budget.items() if k!='result_hash'})
        row['result_hash']=digest({k:v for k,v in row.items() if k!='result_hash'})
        conn.execute('UPDATE reservations SET document=?',(json.dumps(row),))
    marker=tmp_path/'funding-reservations-head.json'
    binding=json.loads(marker.read_text());binding['head_hash']=row['result_hash']
    marker.write_text(json.dumps(binding))
    with pytest.raises(ValueError,match='REPLAY'): book.status(tmp_path,'a'*64)


def test_newer_observation_does_not_forget_prior_debits(tmp_path):
    reserve(tmp_path)
    account,requirements,drafts=request(2)
    account['observed_at_ms']+=100
    account['result_hash']=digest({k:v for k,v in account.items() if k!='result_hash'})
    assert reserve(tmp_path,2,(account,requirements,drafts))['status']=='BLOCKED'
    assert book.status(tmp_path,'a'*64)['reserved_debits']=={'ZUSD':'401'}
