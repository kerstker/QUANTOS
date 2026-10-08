"""Real closure originals and private worker pipes, with isolated venue/roles.

The completed AUTO/legacy economic fixtures, original BTC partition enrollment,
capture algorithms, claims, ledgers, STOP and controller mirrors remain native.
Only systemd/OS/source acceptance and venue transport use existing test seams.
"""
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _state
from tools import demo_reported_recovery as _reported  # bind before custody fixtures
from tools.demo_executor_origin import run as original_origin_run
from tools.gold_pilot_retention_store import canonical
from tests.live.test_gold_pilot_closure_integration import ForkWorker
from tests.tools.gold_controller_fixture import map_host_custody
from tests.tools.test_gold_pilot_closure import configure_closure


def native_snapshot(f):
    from tools.gold_pilot_retention import executor_retention
    with executor_retention(f.root, f.current_runtime, source_commit=f.source_commit,
            account=f.account, uid=f.executor_uid) as store:
        assert store is not None
        return dict(metadata=store.metadata(), frames=store.conn.execute(
            'SELECT * FROM frames ORDER BY ordinal').fetchall(), records=store.conn.execute(
            'SELECT * FROM records ORDER BY sequence').fetchall())


def retained_original(f):
    """Read the actual first incomplete closure, never manufacture its claim."""
    intent_raw=(f.root/'gold-pilot/closure-intent.json').read_bytes()
    intent=json.loads(intent_raw)
    if f.legacy:
        claim=None
        raw=(f.root/'gold-pilot/closure-origin.json').read_bytes()
        snapshot=None
    else:
        from tools.gold_pilot_retention import executor_retention
        with executor_retention(f.root, f.current_runtime, source_commit=f.source_commit,
                account=f.account, uid=f.executor_uid) as store:
            claim=intent['observation_claim']
            raw=store.read_claim(claim,'origin')
            assert raw is not None
            assert store.read_claim(claim,'decision') is store.read_claim(claim,'result') is None
        snapshot=native_snapshot(f)
    origin=json.loads(raw)
    assert origin['status']=='CAPTURED'
    assert not (f.root/'gold-pilot/stopped.json').exists()
    assert not (f.root/'gold-pilot/closure-result.json').exists()
    return dict(intent=intent, intent_raw=intent_raw, claim=claim, origin=origin,
        origin_raw=raw, snapshot=snapshot)


def assert_preserved(f,old):
    assert _state(**f.stores)==f.before_state
    if not f.idle:
        assert f.issuer_snapshot()==f.original_issuer
    assert all(path.read_bytes()==raw for path,raw in f.original_files.items())
    assert (f.root/'gold-pilot/closure-intent.json').read_bytes()==old['intent_raw']
    if f.legacy:
        assert (f.root/'gold-pilot/closure-origin.json').read_bytes()==old['origin_raw']
    else:
        from tools.gold_pilot_retention import executor_retention
        current=native_snapshot(f)
        assert current['frames'][:len(old['snapshot']['frames'])]==old['snapshot']['frames']
        assert current['records'][:len(old['snapshot']['records'])]==old['snapshot']['records']
        with executor_retention(f.root, f.current_runtime, source_commit=f.source_commit,
                account=f.account, uid=f.executor_uid) as store:
            assert store.read_claim(old['claim'],'origin')==old['origin_raw']


def interrupt_origin(f,monkeypatch):
    """Hard-exit the real worker immediately after its complete origin commits."""
    from tools.gold_pilot_retention_store import Retention
    from live.execution import demo_gold_pilot as pilot
    with monkeypatch.context() as patch:
        if f.legacy:
            native=pilot.retain_once
            def stop(path,*args,**kwargs):
                value=native(path,*args,**kwargs)
                if path.name=='closure-origin.json':os._exit(71)
                return value
            patch.setattr(pilot,'retain_once',stop)
        else:
            native=Retention.put
            def stop(self,claim,kind,value):
                result=native(self,claim,kind,value)
                if kind=='origin':os._exit(71)
                return result
            patch.setattr(Retention,'put',stop)
        result=f.close_tick()
    assert result['status'] not in ('PILOT_CLOSED_WITH_FEEDBACK','PILOT_CLOSED_WITHOUT_EXECUTION')
    assert f.children and f.children[-1].returncode==71,(result,f.diagnostics)
    old=retained_original(f)
    # Advance the actual fixture wall clock, after the original origin was
    # durably captured. There is deliberately no historical STOP witness.
    f.clock[0]=old['origin']['completed_at_ms']+31001
    assert f.clock[0]<f.window['ends_at_ms']+900000
    return old


@pytest.fixture
def reobservation_case(request,tmp_path,monkeypatch):
    from tools import gold_strategy_controller as controller, gold_strategy_worker as worker
    from tools import gold_strategy_tick as tick, gold_bar_rotation as rotation
    from tools import demo_operating_bundle as installer, gold_pilot_closure_controller as terminal
    from tools import demo_executor_origin as origin
    from tools.gold_feedback_recovery import FeedbackOwner
    from tools.gold_pilot_install import _read_target
    kind=request.param
    assert kind in ('bar','historical','btc_idle','legacy','legacy_idle')
    fixture='idle_empty_case' if kind=='legacy_idle' else 'idle_btc_case' if kind=='btc_idle' else (
        'completed_legacy_feedback' if kind=='legacy' else 'completed_bar_feedback')
    f=request.getfixturevalue(fixture)
    configure_closure(f,monkeypatch)
    f.kind=kind;f.idle=kind in ('btc_idle','legacy_idle');f.legacy=kind in ('legacy','legacy_idle')
    if kind=='legacy_idle':
        # This original empty enrollment has no strategy observations or native
        # marker. Select its non-BAR runtime before the first closure request;
        # never remove a marker or manufacture prior strategy decisions.
        f.base_runtime=dict(schema='GOLD_STRATEGY_WORKER_INPUT_V1',source_commit=f.source_commit,
            trial_manifest_sha256=f.base_runtime['trial_manifest_sha256'],observation_sha256='e'*64)
        monkeypatch.setattr(origin,'run',original_origin_run)
        assert not (f.root/'strategy-decisions').exists()
        assert not (f.root/'gold-pilot-retention').exists()
    if kind in ('legacy','btc_idle','legacy_idle'):
        f.controller_root=tmp_path/'reobservation-controller';f.controller_root.mkdir(mode=0o700)
    monkeypatch.setattr(os,'geteuid',os.getuid)
    monkeypatch.setattr(os,'getegid',os.getuid)
    monkeypatch.setattr(os,'getgroups',lambda:[os.getuid()])
    map_host_custody(tmp_path,monkeypatch)
    monkeypatch.setattr(installer,'EXECUTOR',f.root)
    monkeypatch.setattr(controller.supervisor,'STATE',f.controller_root)
    monkeypatch.setattr(controller.supervisor,'now_ms',lambda:f.clock[0])
    monkeypatch.setattr(worker,'now_ms',lambda:f.clock[0])
    f.registry=dict(executor_uid=f.executor_uid)
    monkeypatch.setattr(controller,'observation_configuration',lambda source:
        (f.registry,dict(account_ref_sha256=f.account)))
    monkeypatch.setattr(worker,'demo_authority',SimpleNamespace(load_registry_for_executor=lambda:f.registry))
    monkeypatch.setattr(worker,'verify_source',lambda source:
        None if source==f.source_commit else pytest.fail('unexpected source'))
    monkeypatch.setattr(worker,'fixed_account',lambda:(f.root,f.account))
    monkeypatch.setattr(worker,'require_custody',lambda:f.root)
    def private(fd):
        info=os.fstat(fd)
        assert stat.S_ISFIFO(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077
    monkeypatch.setattr(worker,'private_pipe',private)
    monkeypatch.setattr(controller.supervisor,'_read_root_owned_json',lambda path:_read_target(path,uid=os.getuid()))
    path=f.controller_root/'strategy-runtime.json';path.write_bytes(canonical(f.base_runtime));path.chmod(0o600)
    f.before_state=deepcopy(_state(**f.stores))
    f.current_runtime=f.base_runtime;f.receipt=None
    if not f.idle:
        f.window=f.result['original_pilot_window']
        f.receipt=dict(status='RESEARCH_FEEDBACK_DELIVERED',cycle_sha256=_digest(f.base_runtime),
            feedback_sha256=f.exported['feedback_sha256'],source_verified=False,
            submission_enabled=False,economic_authority=False)
        with FeedbackOwner() as owner:
            owner.bind(root=f.controller_root,base_runtime=f.base_runtime,source_commit=f.source_commit,
                account=f.account,registry=f.registry)
            if kind=='legacy':owner.retention.retain(f.result)
            delivered=owner.recover(current_runtime=f.base_runtime,deliver=lambda **kw:f.receipt)
            if kind=='historical':
                from tests.tools.test_gold_feedback_retention_readers import _native_no_entry_after_settlement
                current=rotation.advance(root=f.controller_root,base=f.base_runtime,account=f.account,
                    current=f.base_runtime,result=delivered)
                no_entry=_native_no_entry_after_settlement(f,current)
                owner.retention.retain(no_entry);owner.release()
                f.current_runtime=rotation.advance(root=f.controller_root,base=f.base_runtime,
                    account=f.account,current=current,result=no_entry)
    f.clock[0]=f.window['ends_at_ms']+1000
    f.children=[];f.diagnostics=[]
    def launch(command,**kwargs):
        assert command[0]=='systemd-run' and 'RuntimeMaxSec=450s' in command
        assert command[command.index('-m')+1]=='tools.gold_strategy_worker'
        child=ForkWorker(f.source_commit);f.children.append(child);return child
    monkeypatch.setattr(terminal.subprocess,'Popen',launch)
    monkeypatch.setattr(terminal.subprocess,'run',lambda command,**kw:
        subprocess.CompletedProcess(command,0) if command[:2]==['systemctl','stop'] else pytest.fail('unexpected process'))
    monkeypatch.setattr(controller,'deliver_feedback',lambda **kw:pytest.fail('republication'))
    monkeypatch.setattr(controller,'_run_observation',lambda **kw:pytest.fail('new strategy observation'))
    read_frame=controller.supervisor.read_frame
    def read(*args,**kwargs):
        raw=read_frame(*args,**kwargs);f.diagnostics.append(json.loads(raw));return raw
    monkeypatch.setattr(controller.supervisor,'read_frame',read)
    def close_tick():
        with FeedbackOwner() as owner:
            return tick.run_loop(source_commit=f.source_commit,require_pilot=True,feedback_owner=owner)
    f.close_tick=close_tick
    f.original_files={p:p.read_bytes() for p in (f.root/'gold-pilot').rglob('*') if p.is_file()}
    f.original_files[f.root/'gold-pilot-request.json']=(f.root/'gold-pilot-request.json').read_bytes()
    if not f.idle:
        if kind=='legacy':
            f.issuer_snapshot=request.getfixturevalue('legacy_case')[5].snapshot
        else:
            from live.execution.demo_issuer_store import IssuerStore
            def issuer_snapshot():
                with closing(IssuerStore(f.ledger,read_only=True)) as store:
                    return store.snapshot()
            f.issuer_snapshot=issuer_snapshot
        f.original_issuer=deepcopy(f.issuer_snapshot())
        assert f.original_issuer==f.issuer_rows
        f.original_files[Path(f.exported['path'])]=f.artifact_bytes
        for directory in (f.root/'receipt-reconciliation',f.root/'evidence'):
            f.original_files.update({p:p.read_bytes() for p in directory.rglob('*.json') if p.is_file()})
        if kind=='legacy':
            for directory in (f.root/'strategy-decisions',f.controller_root/'strategy-decisions',
                    f.controller_root/'strategy-feedback-deliveries'):
                f.original_files.update({p:p.read_bytes() for p in directory.glob('*.json')})
    # Each real _report launch is witnessed outside protected fixture roots;
    # parent tests can count child attempts after hard exits without shared lists.
    f.capture_events=tmp_path/'reobservation-capture-events.jsonl'
    native_report=origin._report
    def report(**kwargs):
        fd=os.open(f.capture_events,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        with os.fdopen(fd,'ab') as stream:
            stream.write(canonical(dict(pid=os.getpid(),at_ms=f.clock[0],symbol=kwargs['symbol'])))
            stream.flush();os.fsync(stream.fileno())
        return native_report(**kwargs)
    monkeypatch.setattr(origin,'_report',report)
    f.capture_count=lambda:len(f.capture_events.read_bytes().splitlines()) if f.capture_events.exists() else 0
    return f
