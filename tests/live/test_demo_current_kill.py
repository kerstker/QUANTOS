"""A fresh durable stop must override an unchanged in-memory risk object."""
import json
from pathlib import Path

import pytest

from live.execution import demo_current_kill as current
from live.execution import demo_prepared_execution as executor
from live.exchange import binance_demo_exact as transport
from quantos.contracts.alpha import Actor,ActorKind
from quantos.contracts.killswitch import KillSwitchState
from services.risk.pre_trade.kill_switch_store import KillSwitchStateStore
from services.risk.pre_trade.kill_switch_runtime import arm_and_record
from tests.live.test_demo_prepared_execution import ready
from tests.live.test_demo_pre_submit import NOW


def stop(path):
    writer=KillSwitchStateStore(str(path))
    try:arm_and_record(writer,Actor(actor_id='isolated-sentinel',kind=ActorKind.SYSTEM),'test stop',at=NOW+1,incident_id='persistent-stop')
    finally:writer.close()


def test_durable_trip_after_preparation_blocks_before_credentials_without_changing_memory(tmp_path,monkeypatch):
    batch,args=ready(tmp_path,monkeypatch);calls=[]
    stop(tmp_path/'kill-switch.sqlite3')
    assert args['risk_inputs']['kill_switch'].state.tripped is False
    monkeypatch.setattr(transport,'_credentials',lambda *a:calls.append('credentials'))
    monkeypatch.setattr(transport,'_http_once',lambda *a:calls.append('http'))
    try:
        with pytest.raises(executor.DemoExecutionStopped):executor.execute(batch,**args)
        assert calls==[] and args['dispatch'].wire_snapshot()==[]
    finally:
        for s in ('dispatch','recovery','oms'):args[s].close()


def test_trip_after_claim_before_transport_records_unknown_original_id_and_never_posts(tmp_path,monkeypatch):
    batch,args=ready(tmp_path,monkeypatch);calls=[]
    def credentials(ref):
        stop(tmp_path/'kill-switch.sqlite3');return 'isolated-fixture-key','isolated-fixture-secret'
    monkeypatch.setattr(transport,'_credentials',credentials)
    monkeypatch.setattr(transport,'_http_once',lambda *a:calls.append('http'))
    try:
        r=executor.execute(batch,**args)
        assert r['status']=='QUERY_RECOVERY_REQUIRED' and calls==[]
        assert len(args['dispatch'].wire_snapshot())==2 and len(args['recovery'].pending())==1
        with pytest.raises(executor.DemoExecutionStopped):executor.execute(batch,**args)
    finally:
        for s in ('dispatch','recovery','oms'):args[s].close()


@pytest.mark.parametrize('problem',['missing','empty','corrupt','future'])
def test_missing_or_invalid_durable_state_never_bootstraps_an_untripped_switch(tmp_path,monkeypatch,problem):
    path=tmp_path/'kill-switch.sqlite3'
    monkeypatch.setattr(current,'_PATH',path)
    monkeypatch.setattr(current,'_custody',lambda:None)
    if problem=='corrupt':path.write_text('not a database')
    elif problem!='missing':
        store=KillSwitchStateStore(str(path),create=True)
        if problem=='future':
            store.bootstrap(KillSwitchState(),recorded_at=0)
            arm_and_record(store,Actor(actor_id='isolated-sentinel',kind=ActorKind.SYSTEM),'future fixture',at=NOW+1000,incident_id='future-test')
        store.close()
    original=path.read_bytes() if path.exists() else None
    with pytest.raises(current.DemoKillStateRejected):current.require_expected(KillSwitchState(),now_ms=NOW)
    assert (path.read_bytes() if path.exists() else None)==original


@pytest.mark.parametrize('problem',['valid','directory','file','symlink','hardlink','wal_symlink','large','ancestor'])
def test_fixed_store_custody_checks_original_inode_sidecars_and_parent_permissions(tmp_path,monkeypatch,problem):
    import os
    root=tmp_path/'executor';root.mkdir(mode=0o700);path=root/'kill-switch.sqlite3'
    store=KillSwitchStateStore(str(path),create=True)
    store.bootstrap(KillSwitchState(),recorded_at=0);store.close();path.chmod(0o600)
    monkeypatch.setattr(current,'_PATH',path)
    monkeypatch.setattr(current.demo_authority,'load_registry_for_executor',lambda:{'executor_uid':os.getuid()})
    # The real private test directory is user-owned; only outer root ancestors
    # are substituted here. Production traversal retains its original checks.
    monkeypatch.setattr(current.demo_authority,'_require_protected',lambda *a,**k:None)
    if problem=='directory':root.chmod(0o777)
    elif problem=='file':path.chmod(0o666)
    elif problem=='symlink':path.rename(root/'actual.db');path.symlink_to(root/'actual.db')
    elif problem=='hardlink':os.link(path,root/'other-name.db')
    elif problem=='wal_symlink':Path(str(path)+'-wal').symlink_to(root/'missing')
    elif problem=='large':
        with path.open('r+b') as f:f.truncate(64*1024*1024+1)
    elif problem=='ancestor':
        monkeypatch.setattr(current.demo_authority,'_require_protected',lambda *a,**k:(_ for _ in ()).throw(ValueError('unprotected ancestor')))
    if problem=='valid':
        before=path.read_bytes();r=current.require_expected(KillSwitchState(),now_ms=NOW)
        assert r['transition_count']==1 and len(r['record_sha256'])==64 and path.read_bytes()==before
    else:
        with pytest.raises(current.DemoKillStateRejected):current.require_expected(KillSwitchState(),now_ms=NOW)
