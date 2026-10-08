"""Real issuer reservations/signatures must explain every executor grant."""
from contextlib import closing, contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from live.execution.demo_batch_risk import _digest
from tests.live.test_demo_issuer import fixture, NOW
from tests.live.test_gold_strategy_constraints import case as case
from tests.live.gold_checkpoint_fixture import native_bar_checkpoint as native_bar_checkpoint
from tests.tools.test_demo_gold_namespace_checkpoint import source_attachment as source_attachment


@pytest.fixture
def original_pair(tmp_path, monkeypatch):
    from live.execution import demo_issuer
    private, values, stores, session, handoff, registry, policy, issuer = fixture(tmp_path, monkeypatch)
    grant = demo_issuer.issue_submission(handoff, policy=policy, registry=registry,
        key=private, store=issuer, now_ms=NOW+1)
    stores[0].reserve(json.dumps(grant['envelope']).encode(),
        expected_context=handoff['proposal']['context'], orders=handoff['proposal']['orders'], now_ms=NOW+2)
    arguments = dict(issuer_database=issuer.path,
        executor_database=Path(stores[0]._conn.execute('PRAGMA database_list').fetchone()[2]),
        evidence_directory=tmp_path/'evidence',
        account_ref_sha256=handoff['proposal']['context']['account_ref_sha256'],
        deployment_sha256=registry['deployment_sha256'])
    try:
        yield dict(arguments=arguments, issuer=issuer, stores=stores, handoff=handoff,
            grant=grant, private=private, registry=registry, policy=policy)
    finally:
        issuer.close()
        for store in stores: store.close()


def original_bytes(root):
    return {str(p.relative_to(root)):(p.stat().st_ino, hashlib.sha256(p.read_bytes()).hexdigest())
        for p in root.rglob('*') if p.is_file()}


@contextmanager
def joined(value):
    from tools.demo_gold_issuer_executor_join import open_join
    with open_join(**value['arguments']) as actual:
        yield actual


def test_native_signed_and_reserved_records_join_without_mutating_originals(original_pair, tmp_path):
    value=original_pair; before=original_bytes(tmp_path)
    reservation=value['stores'][0].snapshot()[0]
    with joined(value) as actual:
        report=actual.report()
        assert report['status']=='ISSUER_EXECUTOR_SUBMISSIONS_VERIFIED'
        assert report['issuer_record_count']==2 and report['executor_reservation_count']==1
        assert report['unmatched_reservations']==[]
        pair=actual.original_grant(reservation['record_hash'])
        assert pair['reserved']==value['issuer'].snapshot()[0]
        assert pair['signed']==value['issuer'].snapshot()[1]
        assert pair['registry']==value['registry']
        assert pair['proposal']==value['handoff']['proposal']
        assert report['submission_enabled'] is report['economic_authority'] is False
        assert report['whole_checkpoint_set_verified'] is False
    assert original_bytes(tmp_path)==before
    with pytest.raises(ValueError, match='CLOSED'):
        actual.original_grant(reservation['record_hash'])


def test_valid_signature_does_not_replace_missing_issuer_consumption(original_pair, tmp_path):
    from live.execution.demo_issuer_store import IssuerStore
    value=original_pair
    empty=tmp_path/'empty-issuer.sqlite3'
    IssuerStore(empty,create=True).close()
    value['arguments']['issuer_database']=empty
    with pytest.raises(ValueError, match='SIGNED_ANCESTOR'):
        with joined(value): pass


@pytest.mark.parametrize('damage',['missing-proof','changed-proof','account','deployment'])
def test_original_proposal_scope_cannot_be_replaced(original_pair, damage):
    value=original_pair
    path=value['arguments']['evidence_directory']/(value['handoff']['proposal_sha256']+'.json')
    if damage=='missing-proof':path.unlink()
    elif damage=='changed-proof':
        proof=json.loads(path.read_bytes());proof['orders'][0]['price']='1';path.write_text(json.dumps(proof))
    elif damage=='account':value['arguments']['account_ref_sha256']='f'*64
    else:value['arguments']['deployment_sha256']='f'*64
    with pytest.raises((ValueError,FileNotFoundError)):
        with joined(value):pass


def test_original_claim_index_is_checked_by_native_issuer_reader(original_pair):
    value=original_pair; db=value['issuer'].db
    ddl=db.execute("SELECT sql FROM sqlite_master WHERE name='issuer_order_claims_no_delete'").fetchone()[0]
    db.execute('DROP TRIGGER issuer_order_claims_no_delete')
    db.execute('DELETE FROM issuer_order_claims')
    db.execute(ddl);db.commit()
    with pytest.raises(ValueError,match='ISSUER_ORDER_CLAIM_INDEX'):
        with joined(value):pass


def test_signature_created_after_executor_reservation_is_not_a_causal_ancestor(original_pair):
    value=original_pair; db=value['issuer'].db
    row=deepcopy(value['issuer'].snapshot()[-1]);row['at_ms']=NOW+3
    row['record_hash']=_digest({k:v for k,v in row.items() if k!='record_hash'})
    ddl=db.execute("SELECT sql FROM sqlite_master WHERE name='issuer_events_no_update'").fetchone()[0]
    db.execute('DROP TRIGGER issuer_events_no_update')
    db.execute('UPDATE issuer_events SET at_ms=?,record_hash=? WHERE seq=2',(row['at_ms'],row['record_hash']))
    db.execute(ddl);db.commit()
    # Native validation proves an authentic signature; the cross-role ordering
    # independently proves whether it existed before the executor consumed it.
    assert value['issuer'].snapshot()[-1]['at_ms']==NOW+3
    with pytest.raises(ValueError,match='SIGNING_CAUSALITY'):
        with joined(value):pass


def test_proof_replacement_during_review_invalidates_the_handle(original_pair):
    value=original_pair
    with pytest.raises(ValueError,match='ORIGINAL_CHANGED'):
        with joined(value) as actual:
            path=value['arguments']['evidence_directory']/(value['handoff']['proposal_sha256']+'.json')
            raw=path.read_bytes();replacement=path.with_suffix('.replacement')
            replacement.write_bytes(raw);replacement.chmod(path.stat().st_mode&0o777)
            replacement.replace(path)
            actual.recheck()


def test_write_connections_are_not_exposed_by_the_verified_handle(original_pair):
    with joined(original_pair) as actual:
        report=actual.report();report['issuer_record_count']=0
        assert actual.report()['issuer_record_count']==2
        reservation=original_pair['stores'][0].snapshot()[0]
        grant=actual.original_grant(reservation['record_hash']);grant['registry']['keys'].clear()
        assert actual.original_grant(reservation['record_hash'])['registry']['keys']


def test_native_gold_entry_exit_retains_unsigned_consumed_budget(native_bar_checkpoint):
    from tools.demo_gold_issuer_executor_join import open_join
    f=native_bar_checkpoint
    before=original_bytes(f.root.parent)
    with open_join(issuer_database=f.ledger,
            executor_database=f.stores['dispatch']._conn.execute('PRAGMA database_list').fetchone()[2],
            evidence_directory=f.root/'evidence',account_ref_sha256=f.account,
            deployment_sha256=f.registry['deployment_sha256']) as actual:
        report=actual.report()
        assert report['issuer_record_count']==5 and report['executor_reservation_count']==2
        assert len(report['unmatched_reservations'])==1
        assert report['unmatched_reservations'][0]['status']=='UNSIGNED_RESERVED'
        assert len(report['bindings'])==2
        assert report['issuer_history_sha256']==_digest(f.issuer_rows)
    assert original_bytes(f.root.parent)==before


def test_native_signed_undelivered_grant_is_retained_without_an_executor_row(tmp_path, monkeypatch):
    from live.execution import demo_issuer
    from tools.demo_gold_issuer_executor_join import open_join
    private, _, stores, _, handoff, registry, policy, issuer=fixture(tmp_path,monkeypatch)
    try:
        demo_issuer.issue_submission(handoff,policy=policy,registry=registry,
            key=private,store=issuer,now_ms=NOW+1)
        with open_join(issuer_database=issuer.path,
                executor_database=stores[0]._conn.execute('PRAGMA database_list').fetchone()[2],
                evidence_directory=tmp_path/'evidence',account_ref_sha256=policy['account_ref_sha256'],
                deployment_sha256=registry['deployment_sha256']) as actual:
            report=actual.report()
            assert report['executor_reservation_count']==0
            assert report['unmatched_reservations'][0]['status']=='SIGNED_UNDELIVERED'
            assert report['issuer_record_count']==2
    finally:
        issuer.close()
        for store in stores:store.close()


def test_original_nonempty_wal_is_included_without_mutating_source_files(original_pair, tmp_path):
    value=original_pair;db=value['issuer'].db
    assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
    db.execute('PRAGMA user_version=7');db.commit()
    wal=Path(str(value['arguments']['issuer_database'])+'-wal')
    assert wal.stat().st_size>32
    before=original_bytes(tmp_path)
    with joined(value) as actual:
        assert actual.report()['executor_reservation_count']==1
    assert original_bytes(tmp_path)==before


def test_signed_cancellation_delivery_is_not_inferred_from_submission_rows(original_pair):
    from live.execution import demo_issuer
    from tests.live.test_demo_issuer import cancel_handoff
    value=original_pair
    demo_issuer.issue_cancellation(cancel_handoff(value['handoff']),policy=value['policy'],
        registry=value['registry'],key=value['private'],store=value['issuer'],now_ms=NOW+3)
    with joined(value) as actual:
        report=actual.report()
        assert report['issuer_record_count']==4
        assert report['unmatched_reservations'][0]['purpose']=='cancellation'
        assert report['unmatched_reservations'][0]['status']=='SIGNED_CANCELLATION_UNCHECKED'


@pytest.fixture
def original_cancel(original_pair, tmp_path):
    from live.execution import demo_issuer
    from live.execution.demo_cancel_store import DemoCancelStore
    from live.execution.demo_pre_submit import _retain
    from tests.live.test_demo_issuer import cancel_handoff
    value=original_pair
    handoff=cancel_handoff(value['handoff'])
    handoff['proposal']['context']['reservation_sha256']=value['stores'][0].snapshot()[0]['record_hash']
    handoff['proposal_sha256']=_digest(handoff['proposal'])
    path=_retain(tmp_path/'evidence'/'cancellation-proposals',handoff['proposal'])
    handoff['proposal_path']=str(path)
    grant=demo_issuer.issue_cancellation(handoff,policy=value['policy'],registry=value['registry'],
        key=value['private'],store=value['issuer'],now_ms=NOW+3)
    with DemoCancelStore(tmp_path/'cancellations.sqlite3',create=True) as store:
        reserved=store.reserve(json.dumps(grant['envelope']).encode(),context=handoff['proposal']['context'],
            commands=handoff['proposal']['commands'],now_ms=NOW+4)
        value['arguments']['cancellation_database']=store.path
        yield dict(value=value,store=store,grant=reserved,handoff=handoff,path=path)


@pytest.mark.parametrize('outcome',['unclaimed','interrupted','unknown','observed'])
def test_original_cancel_grant_claim_and_result_remain_distinct(original_cancel, tmp_path, outcome):
    value=original_cancel['value'];store=original_cancel['store'];grant=original_cancel['grant']
    if outcome!='unclaimed':
        claim=store.claim(grant['record_hash'],grant['payload']['commands'][0]['origClientOrderId'],
            request_sha256='a'*64,now_ms=NOW+5)
        if outcome in ('unknown','observed'):
            store.result(claim['record_hash'],outcome=dict(status='UNKNOWN',http_status=None,
                response_sha256=None,venue_status=None,executed_quantity=None) if outcome=='unknown' else
                dict(status='ORDER_STATE_OBSERVED',http_status=200,response_sha256='b'*64,
                    venue_status='CANCELED',executed_quantity='0'),now_ms=NOW+6)
    before=original_bytes(tmp_path)
    with joined(value) as actual:
        report=actual.report()
        assert report['status']=='ISSUER_EXECUTOR_GRANTS_VERIFIED'
        cancellation=report['cancellations']
        assert cancellation['record_count']==len(store.snapshot())
        assert cancellation['history_sha256']==_digest(store.snapshot())
        assert cancellation['grant_count']==1
        assert cancellation['claim_count']==(outcome!='unclaimed')
        assert cancellation['result_count']==(outcome in ('unknown','observed'))
        assert report['unmatched_reservations']==[]
        pair=actual.original_cancellation_grant(grant['record_hash'])
        assert pair['reserved']==value['issuer'].snapshot()[2]
        assert pair['signed']==value['issuer'].snapshot()[3]
        assert pair['proposal']==original_cancel['handoff']['proposal']
        assert pair['submission_reservation_sha256']==value['stores'][0].snapshot()[0]['record_hash']
        assert cancellation['cash_reconciled'] is False
        assert 'CANCELLATION_ORIGINAL_QUERY_REPLAY_NOT_CHECKED' in report['limitations']
    assert original_bytes(tmp_path)==before


def test_cancel_valid_signature_cannot_replace_original_issuer_consumption(original_cancel, tmp_path):
    from live.execution import demo_issuer
    from live.execution.demo_issuer_store import IssuerStore
    value=original_cancel['value']
    with closing(IssuerStore(tmp_path/'only-submission.sqlite3',create=True)) as issuer:
        demo_issuer.issue_submission(value['handoff'],policy=value['policy'],registry=value['registry'],
            key=value['private'],store=issuer,now_ms=NOW+1)
        value['arguments']['issuer_database']=issuer.path
        with pytest.raises(ValueError,match='CANCEL_SIGNED_ANCESTOR'):
            with joined(value):pass


def test_cancel_original_proposal_bytes_are_required(original_cancel):
    original_cancel['path'].write_text('{}')
    with pytest.raises(ValueError,match='CANCEL_ORIGINAL_PROPOSAL'):
        with joined(original_cancel['value']):pass


def test_cancel_signed_time_cannot_follow_executor_consumption(original_cancel):
    value=original_cancel['value'];db=value['issuer'].db
    row=deepcopy(value['issuer'].snapshot()[-1]);row['at_ms']=NOW+5
    row['record_hash']=_digest({k:v for k,v in row.items() if k!='record_hash'})
    ddl=db.execute("SELECT sql FROM sqlite_master WHERE name='issuer_events_no_update'").fetchone()[0]
    db.execute('DROP TRIGGER issuer_events_no_update')
    db.execute('UPDATE issuer_events SET at_ms=?,record_hash=? WHERE seq=4',(row['at_ms'],row['record_hash']))
    db.execute(ddl);db.commit()
    assert value['issuer'].snapshot()[-1]['at_ms']==NOW+5
    with pytest.raises(ValueError,match='CANCEL_SIGNING_CAUSALITY'):
        with joined(value):pass


@pytest.mark.parametrize('alias',[False,True])
def test_cancel_join_cannot_read_protected_tree_through_parent_alias(original_cancel,tmp_path,monkeypatch,alias):
    from tools import demo_namespace_checkpoint as checkpoint
    value=original_cancel['value'];protected=tmp_path/'isolated-protected';protected.mkdir(mode=0o700)
    sibling=tmp_path/'sibling';sibling.mkdir(mode=0o700)
    target=protected/'cancellations.sqlite3'
    shutil.copyfile(original_cancel['store'].path,target);target.chmod(0o600)
    monkeypatch.setattr(checkpoint,'PROTECTED_ROOTS',(protected,))
    value['arguments']['cancellation_database']=(sibling/'..'/'isolated-protected'/'cancellations.sqlite3'
        if alias else target)
    with pytest.raises(ValueError,match='ISOLATED_INPUT_REQUIRED|PATH'):
        with joined(value):pass
