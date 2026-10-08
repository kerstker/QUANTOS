"""Immutable pre-signal origin: native stores and original capture contract."""
from contextlib import ExitStack
from copy import deepcopy
import json
import os
import sqlite3
from types import SimpleNamespace
import pytest
from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_strategy_entry import origin_at
from tests.live.test_demo_pre_submit import stores
from tools import gold_bar_cash as cash


@pytest.fixture
def opening_case(tmp_path, monkeypatch):
    from tools import demo_resident_recovery as custody, demo_executor_stream as stream, demo_executor_origin as origin
    from live.execution import demo_market_partition_runtime as partition, demo_gold_pilot as pilot
    root=tmp_path/'executor';root.mkdir(mode=0o700)
    native=stores(root)
    account=origin_at(10000000)['account_ref_sha256'];clock=[10000000]
    bundle=dict(schema='GOLD_BAR_FRESH_EPISODE_INPUT_V1',source_commit='c'*40,
        trial_manifest_sha256='a'*64,archive_source_commit='b'*40,candidate='channel',seconds=10,history_sha256='d'*64)
    area=root/'strategy-bar-observation-work';area.mkdir(mode=0o700)
    monkeypatch.setattr(custody,'require_custody',lambda:root)
    monkeypatch.setattr(stream,'fixed_account',lambda:(root,account))
    monkeypatch.setattr(origin,'fixed_account',lambda:(root,account))
    monkeypatch.setattr(partition,'EXECUTOR_RECORD',tmp_path/'partition')
    monkeypatch.setattr(partition,'ACTIVATION',tmp_path/'activation')
    from live.execution import demo_position_context as position, demo_authority
    monkeypatch.setattr(position,'_ROOT',root)
    monkeypatch.setattr(position,'_custody',lambda path,**kw:custody._check_path(path,os.getuid(),**kw))
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:{'executor_uid':os.getuid()})
    monkeypatch.setattr(cash.time,'time_ns',lambda:clock[0]*1000000)
    monkeypatch.setattr(cash.time,'sleep',lambda secs:clock.__setitem__(0,clock[0]+round(secs*1000)))
    # Exact fixed store constructors are checked in integration. Here use the
    # original native stores (legacy test names .db) without copying a ledger.
    def opened(stack,actual):
        assert actual==root
        return dict(zip(('dispatch','recovery','oms'),native))
    monkeypatch.setattr(cash,'_opening_stores',opened,raising=False)
    calls=[]
    def capture(**kwargs):
        assert kwargs==dict(symbol='XAUUSDT',source_commit='c'*40)
        calls.append(clock[0]);value=origin_at(clock[0]);value['source_sha256']=origin.source_hash()
        directory=root/'execution-origins'/'XAUUSDT';directory.parent.mkdir(mode=0o700,exist_ok=True);directory.mkdir(mode=0o700,exist_ok=True)
        path=origin.original.save(directory,value)
        return dict(status='CAPTURED',path=str(path))
    monkeypatch.setattr(origin,'run',capture)
    f=SimpleNamespace(root=root,bundle=bundle,account=account,clock=clock,calls=calls,native=native,area=area)
    f.args=dict(bundle=bundle,account=account,source_commit='c'*40,observation_exists=False,
        observation_area=area,capacity=None)
    yield f
    for s in native:s.close()


def test_opening_before_signal_and_restart_use_same_original(opening_case):
    f=opening_case
    value=cash.open_account_origin(f.root,**f.args)
    assert len(f.calls)==1 and value['origin']['completed_at_ms']+5000<f.clock[0]
    (f.area/_digest(f.bundle)).mkdir(mode=0o700)
    retained=cash.open_account_origin(f.root,**dict(f.args,observation_exists=True))
    assert retained==value and len(f.calls)==1


def test_absent_opening_after_observation_never_recaptures(opening_case):
    f=opening_case
    with pytest.raises(ValueError,match='OPENING'):
        cash.open_account_origin(f.root,**dict(f.args,observation_exists=True))
    assert f.calls==[]


def test_partial_capture_is_not_retried(opening_case,monkeypatch):
    from tools import demo_executor_origin as origin
    f=opening_case
    monkeypatch.setattr(origin,'run',lambda **kw:(_ for _ in ()).throw(RuntimeError('capture stopped')))
    with pytest.raises(RuntimeError,match='capture stopped'):cash.open_account_origin(f.root,**f.args)
    with pytest.raises(ValueError,match='OPENING'):cash.open_account_origin(f.root,**f.args)


def test_signed_first_proof_history_must_equal_presignal_history(opening_case):
    from live.execution.demo_pre_submit import _state
    f=opening_case;value=cash.open_account_origin(f.root,**f.args)
    proof=dict(origin=origin_at(f.clock[0]),state=_state(*f.native),context={'account_ref_sha256':f.account})
    cash.check_account_opening(value,proof=proof,bundle_sha256=_digest(f.bundle),source_commit='c'*40,
        entry_decision_ms=f.clock[0])
    changed=deepcopy(proof);changed['state']['wire'].append({'unknown':'history'})
    with pytest.raises(ValueError,match='HISTORY'):
        cash.check_account_opening(value,proof=changed,bundle_sha256=_digest(f.bundle),source_commit='c'*40,
            entry_decision_ms=f.clock[0])


def test_origin_reference_removal_does_not_recapture(opening_case):
    f=opening_case;value=cash.open_account_origin(f.root,**f.args)
    (f.area/_digest(f.bundle)).mkdir(mode=0o700)
    (f.root/'execution-origins'/'XAUUSDT'/(value['origin_file_sha256']+'.json')).unlink()
    with pytest.raises((ValueError,FileNotFoundError)):
        cash.open_account_origin(f.root,**dict(f.args,observation_exists=True))
    assert len(f.calls)==1


def test_account_origin_and_observation_share_existing_byte_budget(opening_case):
    f=opening_case;cash.open_account_origin(f.root,**f.args)
    directory=f.area/_digest(f.bundle);directory.mkdir(mode=0o700)
    # A sparse native fixture exercises metadata byte accounting without large
    # reads/allocations: the observation alone fits, with its origin it does not.
    path=directory/'messages.jsonl'
    with path.open('wb') as stream:stream.truncate(64*1024*1024-8192)
    path.chmod(0o600)
    with pytest.raises(ValueError,match='ACCOUNT_OPENING_STORAGE_BUDGET'):
        cash.account_opening_usage(f.root,observation_area=f.area,capacity=None,bundle=f.bundle,account=f.account)


def test_loaded_opening_revalidates_real_pilot_configuration(opening_case):
    from live.execution.demo_gold_pilot_enrollment import enroll_requested
    from tests.tools.test_gold_pilot_capacity import write
    f=opening_case
    request=dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1',source_commit='c'*40,
        account_ref_sha256=f.account,duration_seconds=86400,max_loss_fraction='0.05',submission_enabled=False)
    write(f.root/'gold-pilot-request.json',request)
    enroll_requested(root=f.root,source_commit='c'*40,origin=origin_at(f.clock[0]-100),now_ms=f.clock[0]-100,
        **dict(zip(('dispatch','recovery','oms'),f.native)))
    cash.open_account_origin(f.root,**f.args)
    path=f.root/'gold-pilot/configuration.json';value=json.loads(path.read_bytes());value['ends_at_ms']+=1
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        cash.load_account_opening(f.root,bundle=f.bundle,account=f.account,source_commit='c'*40)
    assert len(f.calls)==1


def test_presignal_clock_rollback_retains_original_without_recapture(opening_case,monkeypatch):
    f=opening_case
    monkeypatch.setattr(cash.time,'sleep',lambda _:None)
    with pytest.raises(ValueError,match='ACCOUNT_OPENING_CLOCK'):
        cash.open_account_origin(f.root,**f.args)
    f.clock[0]+=6000
    value=cash.open_account_origin(f.root,**f.args)
    assert len(f.calls)==1 and value['origin']['completed_at_ms']==10000000


def test_new_record_cannot_relabel_old_opening_time_or_runtime(opening_case):
    from live.execution.demo_pre_submit import _state
    f=opening_case;value=cash.open_account_origin(f.root,**f.args)
    proof=dict(origin=origin_at(f.clock[0]),state=_state(*f.native),context={'account_ref_sha256':f.account})
    for runtime,entry in ((_digest(f.bundle),10004000),('f'*64,f.clock[0])):
        with pytest.raises(ValueError):
            cash.check_account_opening(value,proof=proof,bundle_sha256=runtime,source_commit='c'*40,entry_decision_ms=entry)


def test_account_capture_failure_remains_a_consumed_attempt(opening_case,monkeypatch):
    from tools import demo_executor_origin as origin
    f=opening_case
    monkeypatch.setattr(origin,'run',lambda **kw:dict(status='FAILED'))
    with pytest.raises(ValueError,match='ACCOUNT_OPENING_CAPTURE_FAILED'):
        cash.open_account_origin(f.root,**f.args)
    monkeypatch.setattr(origin,'run',lambda **kw:pytest.fail('failed attempt recaptured'))
    with pytest.raises(ValueError,match='ACCOUNT_OPENING_INCOMPLETE'):
        cash.open_account_origin(f.root,**f.args)


def test_fixed_opening_store_readers_never_create_or_migrate(tmp_path):
    from live.execution.demo_dispatch_store import DemoDispatchStore
    from live.execution.order_recovery import SqliteOrderRecoveryStore
    from live.execution.position_ledger import LiveLedger
    from live.execution.demo_pre_submit import _state,_preparation_lease
    factories=(('dispatch',DemoDispatchStore),('recovery',SqliteOrderRecoveryStore),('oms',LiveLedger))
    for name,factory in factories:
        instance=factory(str(tmp_path/(name+'.sqlite3')),create=True);instance.close()
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    with ExitStack() as stack:
        opened=cash._opening_stores(stack,tmp_path)
        with _preparation_lease(opened['dispatch']):assert _state(**opened)==dict(reservations=[],recovery=[],oms=[],wire=[])
    assert {p.name:p.read_bytes() for p in tmp_path.iterdir()}==before
    missing=tmp_path/'missing';missing.mkdir(mode=0o700)
    with ExitStack() as stack,pytest.raises((ValueError,FileNotFoundError,sqlite3.OperationalError)):
        cash._opening_stores(stack,missing)
    assert list(missing.iterdir())==[]
