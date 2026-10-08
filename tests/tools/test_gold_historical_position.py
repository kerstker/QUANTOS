"""Historical context from original execution chains, never current balances."""
from copy import deepcopy
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import stat
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tests.live import gold_checkpoint_fixture as checkpoint_fixtures
from tests.live.gold_checkpoint_fixture import source_attachment as source_attachment
from tests.live.test_gold_strategy_constraints import case as case
from tests.tools.test_demo_gold_namespace_checkpoint import private_files as private_files
from tests.tools.test_demo_gold_checkpoint_components import readonly_stores, component_arguments as raw_component_arguments, snapshot
from live.execution import demo_authority
from tests.live.gold_partition_checkpoint_fixture import (
    cold_scope_local as cold_scope_local,
    first_scope_archive as first_scope_archive,
    installed_first_scope as installed_first_scope,
    native_scope as native_scope,
    installed_partition_checkpoint as installed_partition_checkpoint,
)

_ROOT_READER = demo_authority._read_root_owned_json
original_bar_checkpoint = checkpoint_fixtures.native_bar_checkpoint


@pytest.mark.parametrize('cold_scope_local',[True],indirect=True)
def test_installed_partition_fixture_keeps_old_btc_and_real_gold_flat(installed_partition_checkpoint):
    from live.execution.demo_pre_submit import _state
    from live.execution.demo_market_partition import partition_state
    f=installed_partition_checkpoint
    state=_state(**f.stores)
    split=partition_state(state=state,candidate=f.local['value']['record']['candidate'],as_of_ms=f.clock[0])
    assert split['legacy']==f.installed['history']['executor_state']
    assert len(split['target']['reservations'])==2
    assert f.held_context['quantity']!='0' and f.context['quantity']=='0'


@pytest.mark.parametrize('cold_scope_local',[True],indirect=True)
def test_installed_partition_replays_held_gold_after_flat_without_replacing_btc(installed_partition_checkpoint,tmp_path,monkeypatch):
    from tests.live.gold_partition_checkpoint_fixture import bind_checkpoint_public
    from tools import demo_gold_historical_position as history
    f=bind_checkpoint_public(installed_partition_checkpoint,tmp_path,monkeypatch)
    before=snapshot(f.root)
    with readonly_stores(f) as stores:
        with history.open_history(f.root,public=f.history_public,uid=f.executor_uid,
                retention=stores['retention'],verified_public=f.verified_public) as book:
            actual=book.replay_trigger(trigger_sha256=f.trigger['record_hash'],runtime_sha256=_digest(f.base_runtime))
    assert actual['context']==f.held_context
    assert actual['summary']['status']=='HISTORICAL_CONTEXT_VERIFIED'
    partition=actual['summary']['partition'];binding=f.local['value']['record']['candidate']['binding']
    assert partition['legacy_state_sha256']==binding['legacy_state_sha256']
    assert partition['legacy_context_sha256']==binding['legacy_context_sha256']
    assert partition['event_coverage']=='COMPLETE_NATIVE_EVENT_CHAIN'
    assert snapshot(f.root)==before


@pytest.mark.parametrize('cold_scope_local',[True],indirect=True)
@pytest.mark.parametrize('encrypted',[False,True])
def test_installed_partition_original_cut_restores_after_later_query_at_another_path(installed_partition_checkpoint,tmp_path,monkeypatch,encrypted):
    from tests.live.gold_partition_checkpoint_fixture import bind_checkpoint_public,append_exit_query
    from tests.tools.test_demo_gold_namespace_checkpoint import materialize
    from tools import demo_gold_namespace_checkpoint as inventory
    from live.execution.demo_issuer_store import IssuerStore
    from tests.live.test_gold_first_scope_original_history import issuer_claims
    f=installed_partition_checkpoint
    append_exit_query(f)
    f=bind_checkpoint_public(f,tmp_path,monkeypatch)
    before=snapshot(f.root)
    with closing(IssuerStore(f.ledger,read_only=True)) as issuer:
        rows=issuer.snapshot();claims=issuer_claims(issuer)
    prefix=f.installed['history']['issuer_prefix']
    assert len(prefix)==3 and prefix[-1]['kind']=='RESERVED' and rows[:3]==prefix
    destination=tmp_path/'installed-partition-restored'
    with inventory.capture_inventory(**f.history_arguments) as captured:
        expected=captured.semantic_view()
        trigger,=expected['bar_inputs']['components']['exit_triggers']
        assert trigger['status']=='HISTORICAL_CONTEXT_VERIFIED'
        assert trigger['historical_position']['position_context_sha256']==_digest(f.held_context)
        if encrypted:
            from tools import demo_namespace_checkpoint as transport
            secret='isolated-installed-partition-archive-test'
            saved=transport.create_gold_namespace_checkpoint(captured,tmp_path/'encrypted-archive',passphrase=secret)
        else:manifest,receipt=materialize(captured,destination)
    if encrypted:
        # Restore relies on the actual source bundle and captured fixed public
        # records in the archive, after both original external inputs disappear.
        Path(f.source_attachment['bundle_path']).unlink()
        shutil.rmtree(f.source_attachment['source_root'])
        shutil.rmtree(f.local['cfg'])
        restored=transport.restore_gold_namespace_checkpoint(tmp_path/'encrypted-archive',destination,
            passphrase=secret,expected_sha256=saved['sha256'])
        actual=restored['state']
        assert restored['production_resumable'] is restored['production_restore_performed'] is False
        assert restored['cross_namespace_atomic'] is False
        restored_before=snapshot(destination)
    else:
        restored_before=snapshot(destination)
        actual=inventory.verify_restored_namespace(destination,inventory=manifest,role_receipt=receipt,
            public_bundle=f.history_public,source_attachment=f.source_attachment,cut_request=f.history_arguments['cut_request'])
    assert actual==expected and actual['mutation_permitted'] is False
    assert snapshot(f.root)==before and snapshot(destination)==restored_before
    with closing(IssuerStore(f.ledger,read_only=True)) as issuer:
        assert issuer.snapshot()==rows and issuer_claims(issuer)==claims


@pytest.mark.parametrize('cold_scope_local',[True],indirect=True)
def test_installed_original_v2_inventory_cannot_upgrade_partition_evidence(installed_partition_checkpoint,tmp_path,monkeypatch):
    from tests.live.gold_partition_checkpoint_fixture import bind_checkpoint_public
    from tests.tools.test_demo_gold_namespace_checkpoint import materialize
    from tools import demo_gold_namespace_checkpoint as inventory
    f=bind_checkpoint_public(installed_partition_checkpoint,tmp_path,monkeypatch)
    original=inventory._semantic_view
    def old_view(*args,**kwargs):
        kwargs['semantic_schema']='GOLD_NAMESPACE_SEMANTIC_VIEW_V2'
        return original(*args,**kwargs)
    destination=tmp_path/'v2-installed-restored'
    before=snapshot(f.root)
    with monkeypatch.context() as old:
        old.setattr(inventory,'_semantic_view',old_view)
        with inventory.capture_inventory(**f.history_arguments) as captured:
            expected=captured.semantic_view();manifest,receipt=materialize(captured,destination)
    assert expected['bar_inputs']['components']['exit_triggers'][0]['status']=='HISTORICAL_PARTITION_RECORDS_REQUIRED'
    actual=inventory.verify_restored_namespace(destination,inventory=manifest,role_receipt=receipt,
        public_bundle=f.history_public,source_attachment=f.source_attachment,cut_request=f.history_arguments['cut_request'])
    assert actual==expected and actual['schema']=='GOLD_NAMESPACE_SEMANTIC_VIEW_V2'
    assert snapshot(f.root)==before


@pytest.mark.parametrize('cold_scope_local',[True],indirect=True)
def test_installed_partition_still_requires_original_legacy_completion(installed_partition_checkpoint,tmp_path,monkeypatch):
    from tests.live.gold_partition_checkpoint_fixture import bind_checkpoint_public
    from tools import demo_gold_historical_position as history
    f=bind_checkpoint_public(installed_partition_checkpoint,tmp_path,monkeypatch)
    source,=f.installed['history']['book_tuple'][1]['sources']
    original=f.root/'receipt-reconciliation/plans'/(source['plan_sha256']+'.json')
    original.rename(tmp_path/'missing-original-legacy-plan.json')
    before=snapshot(f.root)
    with readonly_stores(f) as stores:
        with pytest.raises(ValueError,match='POSITION_CONTEXT_COMPLETION_REQUIRED'):
            with history.open_history(f.root,public=f.history_public,uid=f.executor_uid,
                    retention=stores['retention'],verified_public=f.verified_public) as book:
                book.replay_trigger(trigger_sha256=f.trigger['record_hash'],runtime_sha256=_digest(f.base_runtime))
    assert snapshot(f.root)==before


def component_arguments(f, stores):
    value=raw_component_arguments(f, stores)
    if hasattr(f,'history_public'):value['public']=deepcopy(f.history_public)
    return value


@pytest.fixture
def native_bar_checkpoint(original_bar_checkpoint, tmp_path, monkeypatch):
    """Original BAR book plus actually read absent fixed partition records.

    Only fixed paths/root ownership are virtual. Real descriptor traversal,
    exact file bytes, policy/registry and actual Git source identity are checked.
    Root and executor are separate local OS adapters; no role gate is skipped.
    """
    from tools import demo_gold_checkpoint_public as fixed, demo_supervisor as supervisor
    from tools import gold_strategy_controller as controller, gold_bar_definition as definition
    from tests.tools.test_demo_gold_checkpoint_components import inventory_arguments
    f=original_bar_checkpoint; directory=tmp_path/'fixed-public'; directory.mkdir(mode=0o700)
    executor=directory/'executor';issuer=directory/'issuer';control=directory/'controller'
    for path in (executor,issuer,control):path.mkdir(mode=0o700)
    paths=dict(executor_registry=executor/'authority.json',issuer_registry=issuer/'authority.json',
        policy=issuer/'policy.json',executor_partition=executor/'market-partition.json',
        issuer_partition=issuer/'market-partition.json',partition_intent=issuer/'market-partition-intent.json',
        activation=directory/'market-partition-activation.json')
    for name,value in (('executor_registry',f.registry),('issuer_registry',f.registry),('policy',f.base_policy)):
        paths[name].write_text(json.dumps(value));paths[name].chmod(0o640)
    releases=directory/'releases';evidence=releases/f.source_commit/'evidence/l2_release_evidence_manifest.json'
    evidence.parent.mkdir(parents=True,mode=0o700)
    evidence.write_text(json.dumps(dict(schema_version=1,readiness='L2_DRY_RUN_ONLY',source_commit=f.source_commit)))
    evidence.chmod(0o644)
    monkeypatch.setattr(fixed,'PATHS',paths);monkeypatch.setattr(fixed,'RELEASES',releases)
    monkeypatch.setattr(fixed,'PARTITION_HISTORY',control/'market-partition-installations')
    for name,path in (('EXECUTOR_REGISTRY',paths['executor_registry']),('ISSUER_REGISTRY',paths['issuer_registry']),
            ('ISSUER_POLICY',paths['policy']),('STATE',control)):monkeypatch.setattr(supervisor,name,path)
    proxy=SimpleNamespace(**dict(vars(os),geteuid=lambda:0))
    monkeypatch.setattr(fixed,'os',proxy);monkeypatch.setattr(controller,'os',proxy)
    monkeypatch.setattr(supervisor,'now_ms',lambda:f.clock[0])
    attachment=next(row for row in f.source_attachment['attachments'] if row['identity']['commit']==f.source_commit)
    def verify_source(commit):
        assert commit==f.source_commit
        return definition.verify_source(Path(attachment['source_root']),attachment['identity'])
    monkeypatch.setattr(supervisor,'verify_source',verify_source)
    parents={p.stat().st_ino for leaf in (*paths.values(),evidence,control/'market-partition-installations') for p in leaf.parents}
    gids={path.stat().st_ino:(f.registry['executor_uid'] if name.startswith('executor') else f.registry['issuer_uid'])
        for name,path in paths.items() if path.exists()}
    gids[evidence.stat().st_ino]=0
    def virtual(info):
        data={name:getattr(info,name) for name in fixed._STAT}
        data.update(st_uid=0,st_gid=gids.get(info.st_ino,0))
        if info.st_ino in parents:data['st_mode'] &= ~0o022
        return SimpleNamespace(**data)
    native_identity=fixed._identity
    monkeypatch.setattr(fixed,'_identity',lambda info:native_identity(virtual(info)))
    def protected(info,*,directory):
        assert stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        assert not info.st_mode&0o022 or directory and info.st_ino in parents
        if not directory:assert info.st_nlink==1
    for module in (fixed,demo_authority,supervisor):monkeypatch.setattr(module,'_require_protected',protected)
    for module in (fixed,supervisor):monkeypatch.setattr(module,'_read_root_owned_json',_ROOT_READER)
    arguments=inventory_arguments(f,monkeypatch)
    arguments['cut_request']['roles']=['executor','controller','issuer']
    def refresh_public(registry=None):
        if registry is not None:
            # A changed installed public configuration is a new read cut. The
            # original RESERVED records, grants and proofs are never rewritten.
            policy=deepcopy(f.base_policy);policy['registry_sha256']=_digest(registry)
            for name,value in (('executor_registry',registry),('issuer_registry',registry),('policy',policy)):
                paths[name].write_text(json.dumps(value))
            arguments['public_bundle']['registry']=deepcopy(registry)
        snapshot=fixed.capture(source_commit=f.source_commit,operation_sha256='e'*64,
            account_ref_sha256=f.account,guard=lambda:None)
        assert snapshot['partition_status']=='VERIFIED_ABSENT'
        arguments['public_bundle']['records']['fixed_public']=snapshot
        arguments['cut_request']['registry_sha256']=_digest(arguments['public_bundle']['registry'])
        arguments['cut_request']['public_bundle_sha256']=_digest(arguments['public_bundle'])
        f.verified_public=fixed.bind_captured(snapshot,public_bundle=arguments['public_bundle'],
            cut_request=arguments['cut_request'],guard=arguments['guard'])
        f.history_public=deepcopy(arguments['public_bundle'])
        f.history_arguments=dict(arguments,verified_public=f.verified_public)
    refresh_public();f.refresh_public=refresh_public;f.fixed_directory=directory
    return f


def test_missing_fixed_public_cannot_prove_partition_absence(native_bar_checkpoint):
    from tools import demo_gold_historical_position as history
    f=native_bar_checkpoint; trigger=json.loads(next(f.root.glob('bar-exit-trigger-*.json')).read_bytes())
    with readonly_stores(f) as stores:
        public=component_arguments(f,stores)['public']
        with pytest.raises(history.HistoricalContextUnproven,match='HISTORICAL_PARTITION_RECORDS_REQUIRED'):
            with history.open_history(f.root,public=public,uid=f.executor_uid,retention=stores['retention']) as book:
                book.replay_trigger(trigger_sha256=trigger['record_hash'],runtime_sha256=_digest(f.base_runtime))


def test_caller_dictionary_cannot_replace_fixed_public_handle(native_bar_checkpoint):
    from tools import demo_gold_historical_position as history
    f=native_bar_checkpoint
    with readonly_stores(f) as stores:
        with pytest.raises(ValueError,match='GOLD_FIXED_PUBLIC_VERIFIED_HANDLE_REQUIRED'):
            with history.open_history(f.root,public=f.history_public,uid=f.executor_uid,
                    retention=stores['retention'],verified_public={'status':'VERIFIED_ABSENT'}):
                pytest.fail('caller JSON was accepted as fixed evidence')


def test_captured_public_handle_requires_its_original_root_role(native_bar_checkpoint, monkeypatch):
    from tools import demo_gold_checkpoint_public as fixed
    f=native_bar_checkpoint
    monkeypatch.setattr(fixed.os,'geteuid',lambda:123456)
    with pytest.raises(ValueError,match='GOLD_FIXED_PUBLIC_ROOT_GUARD_REQUIRED'):
        f.verified_public.recheck()


@pytest.mark.parametrize('later_query', [False, True])
def test_original_held_trigger_replays_after_actual_flat_and_later_query(native_bar_checkpoint, later_query):
    from live.execution.demo_prepared_execution import _load_proof
    from tests.live.gold_checkpoint_fixture import append_original_query
    f = native_bar_checkpoint
    proof = _load_proof(f.exit_batch)
    expected = deepcopy(proof['state']['position_context'])
    trigger = json.loads(next(f.root.glob('bar-exit-trigger-*.json')).read_bytes())
    assert expected['quantity'] == '0.1' and f.context['quantity'] == '0'
    assert _digest(expected) == trigger['position_context_sha256']
    core = {name:proof['state'][name] for name in ('reservations', 'recovery', 'oms', 'wire')}
    assert _digest(core) == f.exit_result['decision']['execution_state_sha256']
    if later_query:
        append_original_query(f)
    before = snapshot(f.root)
    from tools import demo_gold_historical_position as history
    with readonly_stores(f) as stores:
        arguments = component_arguments(f, stores)
        with history.open_history(f.root, public=arguments['public'], uid=f.executor_uid,
                retention=stores['retention'],verified_public=f.verified_public) as book:
            result = book.replay_trigger(trigger_sha256=trigger['record_hash'], runtime_sha256=_digest(f.base_runtime))
            assert result['context'] == expected
            assert result['summary']['status'] == 'HISTORICAL_CONTEXT_VERIFIED'
            assert result['summary']['execution_state_sha256'] == _digest(core)
            assert result['summary']['mutation_permitted'] is False
            book.recheck()
    assert snapshot(f.root) == before


@pytest.mark.parametrize('schema',['GOLD_NAMESPACE_SEMANTIC_VIEW_V2','GOLD_NAMESPACE_SEMANTIC_VIEW_V3'])
def test_namespace_restores_original_held_context_at_a_different_path(native_bar_checkpoint, monkeypatch, tmp_path,schema):
    from tools import demo_gold_namespace_checkpoint as inventory
    from tests.tools.test_demo_gold_checkpoint_components import inventory_arguments
    from tests.tools.test_demo_gold_namespace_checkpoint import materialize
    from live.execution.demo_prepared_execution import _load_proof
    f=native_bar_checkpoint; expected=_load_proof(f.exit_batch)['state']['position_context']
    arguments=f.history_arguments; before=snapshot(f.root)
    restored=tmp_path/'historical-restore'
    original=inventory._semantic_view
    def capture_version(*args,**kwargs):
        kwargs['semantic_schema']=schema
        return original(*args,**kwargs)
    with monkeypatch.context() as version:
        version.setattr(inventory,'_semantic_view',capture_version)
        with inventory.capture_inventory(**arguments) as captured:
            view=captured.semantic_view()
            value,=view['bar_inputs']['components']['exit_triggers']
            assert view['schema']==schema
            assert value['status']=='HISTORICAL_CONTEXT_VERIFIED'
            assert value['historical_position']['position_context_sha256']==_digest(expected)
            manifest,receipt=materialize(captured,restored)
    shutil.rmtree(f.fixed_directory)
    restored_before=snapshot(restored)
    actual=inventory.verify_restored_namespace(restored,inventory=manifest,role_receipt=receipt,
        public_bundle=arguments['public_bundle'],source_attachment=f.source_attachment,cut_request=arguments['cut_request'])
    assert actual==view
    assert actual['mutation_permitted'] is False
    assert snapshot(f.root)==before and snapshot(restored)==restored_before


def test_unverified_fixed_public_remains_partial_after_restore(native_bar_checkpoint, tmp_path):
    from tools import demo_gold_namespace_checkpoint as inventory
    from tests.tools.test_demo_gold_namespace_checkpoint import materialize
    f=native_bar_checkpoint;arguments=dict(f.history_arguments);arguments.pop('verified_public')
    restored=tmp_path/'unproven-public-restore'
    with inventory.capture_inventory(**arguments) as captured:
        expected=captured.semantic_view()
        assert expected['bar_inputs']['components']['exit_triggers'][0]['status']=='HISTORICAL_PARTITION_RECORDS_REQUIRED'
        manifest,receipt=materialize(captured,restored)
    actual=inventory.verify_restored_namespace(restored,inventory=manifest,role_receipt=receipt,
        public_bundle=arguments['public_bundle'],source_attachment=f.source_attachment,cut_request=arguments['cut_request'])
    assert actual==expected


@pytest.mark.parametrize('damage', ['missing_state_proof','missing_plan','direct_oms','wrong_runtime','wrong_account'])
def test_historical_context_cannot_replace_missing_originals_or_use_current_state(native_bar_checkpoint, damage):
    from tools import demo_gold_historical_position as history
    from live.execution.demo_prepared_execution import _load_proof
    from quantos.contracts.trading import OrderSide
    f=native_bar_checkpoint
    trigger=json.loads(next(f.root.glob('bar-exit-trigger-*.json')).read_bytes())
    with readonly_stores(f) as stores:
        public=component_arguments(f,stores)['public']
    expected=_load_proof(f.exit_batch)['state']['position_context']
    if damage=='missing_state_proof':
        (f.root/'evidence'/(f.exit_batch['proposal_sha256']+'.json')).unlink()
        error='HISTORICAL_STATE_CUT_UNPROVEN'
    elif damage=='missing_plan':
        (f.root/'receipt-reconciliation/plans'/(expected['sources'][0]['plan_sha256']+'.json')).unlink()
        error='HISTORICAL_REFERENCED_PLAN_MISSING'
    elif damage=='direct_oms':
        f.stores['oms'].record_submission('legacy-no-event','XAUUSDT',OrderSide.BUY,.01,
            strategy_id='original-legacy-api',submitted_at=f.clock[0],status='FILLED')
        error='HISTORICAL_EVENT_COVERAGE_REQUIRED'
    elif damage=='wrong_runtime':error='HISTORICAL_RUNTIME_BINDING'
    else:
        public['account_ref_sha256']='f'*64; error='GOLD_FIXED_PUBLIC_VERIFIED_HANDLE_REQUIRED'
    before=snapshot(f.root)
    with readonly_stores(f) as stores:
        with pytest.raises(ValueError,match=error):
            with history.open_history(f.root,public=public,uid=f.executor_uid,retention=stores['retention'],verified_public=f.verified_public) as book:
                book.replay_trigger(trigger_sha256=trigger['record_hash'],
                    runtime_sha256='f'*64 if damage=='wrong_runtime' else _digest(f.base_runtime))
    assert snapshot(f.root)==before


def test_recheck_rejects_identical_bytes_on_a_replaced_original_inode(native_bar_checkpoint):
    from tools import demo_gold_historical_position as history
    f=native_bar_checkpoint; trigger=json.loads(next(f.root.glob('bar-exit-trigger-*.json')).read_bytes())
    with readonly_stores(f) as stores:
        public=component_arguments(f,stores)['public']
        with pytest.raises(ValueError,match='HISTORICAL_SOURCE_CHANGED'):
            with history.open_history(f.root,public=public,uid=f.executor_uid,retention=stores['retention'],verified_public=f.verified_public) as book:
                book.replay_trigger(trigger_sha256=trigger['record_hash'],runtime_sha256=_digest(f.base_runtime))
                path=f.root/'evidence'/(f.exit_batch['proposal_sha256']+'.json')
                raw=path.read_bytes(); path.unlink(); path.write_bytes(raw); path.chmod(0o600)
                book.recheck()


def test_historical_prefix_is_not_selected_by_cross_order_observed_time(native_bar_checkpoint):
    from tools import demo_gold_historical_position as history
    from live.execution.oms import OmsAuthority
    from quantos.contracts.trading import OrderSide
    f=native_bar_checkpoint; trigger=json.loads(next(f.root.glob('bar-exit-trigger-*.json')).read_bytes())
    with readonly_stores(f) as stores:
        public=component_arguments(f,stores)['public']
    # A later appended unrelated command may carry an earlier observed time.
    # Global seq remains later; filtering by time would wrongly include it.
    authority=OmsAuthority(f.events,f.stores['oms'])
    authority.register_submission(client_order_id='later-global-earlier-clock',instrument_id='XAUUSDT',
        side=OrderSide.BUY,order_quantity=.01,strategy_id='separate-original-order',order_type='LIMIT',
        limit_price=90.,observed_at_ms=trigger['observed_at_ms']-100000,
        command_event_id='historical-proof-counterexample')
    before=snapshot(f.root)
    with readonly_stores(f) as stores:
        with history.open_history(f.root,public=public,uid=f.executor_uid,retention=stores['retention'],verified_public=f.verified_public) as book:
            value=book.replay_trigger(trigger_sha256=trigger['record_hash'],runtime_sha256=_digest(f.base_runtime))
            assert value['context']['quantity']=='0.1'
            assert value['summary']['cut']['order_events']['records']==2
            assert len(f.events.events())==5
    assert snapshot(f.root)==before


def test_original_v1_partial_inventory_remains_partial_when_restored(native_bar_checkpoint, monkeypatch, tmp_path):
    from tools import demo_gold_namespace_checkpoint as inventory
    from tests.tools.test_demo_gold_checkpoint_components import inventory_arguments
    from tests.tools.test_demo_gold_namespace_checkpoint import materialize
    f=native_bar_checkpoint; arguments=f.history_arguments
    original=inventory._semantic_view
    def legacy_view(*args,**kwargs):
        # Reproduce the exact parent V1 report: all native components are still
        # verified, while historical triggers remain explicit dependencies.
        kwargs['semantic_schema']='GOLD_NAMESPACE_SEMANTIC_VIEW_V1'
        return original(*args,**kwargs)
    restored=tmp_path/'original-v1-restore'
    with monkeypatch.context() as old:
        old.setattr(inventory,'_semantic_view',legacy_view)
        with inventory.capture_inventory(**arguments) as captured:
            expected=captured.semantic_view(); manifest,receipt=materialize(captured,restored)
    actual=inventory.verify_restored_namespace(restored,inventory=manifest,role_receipt=receipt,
        public_bundle=arguments['public_bundle'],source_attachment=f.source_attachment,cut_request=arguments['cut_request'])
    assert actual==expected and actual['schema']=='GOLD_NAMESPACE_SEMANTIC_VIEW_V1'
    assert actual['bar_inputs']['components']['exit_triggers'][0]['status']=='HISTORICAL_POSITION_CONTEXT_REQUIRED'


def test_rehashed_dispatch_cannot_hide_an_invalid_original_grant_signature(native_bar_checkpoint):
    from tools import demo_gold_historical_position as history
    from live.execution import demo_dispatch_store as dispatch_module
    f=native_bar_checkpoint; trigger=json.loads(next(f.root.glob('bar-exit-trigger-*.json')).read_bytes())
    with readonly_stores(f) as stores:public=component_arguments(f,stores)['public']
    dispatch=f.stores['dispatch']; original=dispatch.evidence_snapshot()
    changed=deepcopy(original['reservations'][-1]); old_hash=changed.pop('record_hash')
    signature=changed['payload']['envelope']['signature_base64url']
    changed['payload']['envelope']['signature_base64url']=('A' if signature[0]!='A' else 'B')+signature[1:]
    new_hash=dispatch_module._hash(changed); mapping={old_hash:new_hash}
    connection=dispatch._conn
    foreign_keys=connection.execute('PRAGMA foreign_keys').fetchone()[0]
    connection.execute('PRAGMA foreign_keys=OFF')
    for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
        connection.execute('DROP TRIGGER '+name)
    connection.execute('UPDATE demo_dispatch_reservations SET payload=?,record_hash=? WHERE seq=?',
        (dispatch_module._canonical(changed['payload']),new_hash,changed['seq']))
    def replace(value):
        if type(value) is dict:return {k:replace(v) for k,v in value.items()}
        if type(value) is list:return [replace(v) for v in value]
        return mapping.get(value,value) if type(value) is str else value
    previous=''
    for old in original['wire']:
        row=replace(deepcopy(old)); row.pop('record_hash'); row['prev_hash']=previous
        digest=dispatch_module._hash(row);mapping[old['record_hash']]=digest;previous=digest
        connection.execute('UPDATE demo_wire_events SET reservation_sha256=?,payload=?,prev_hash=?,record_hash=? WHERE seq=?',
            (row['reservation_sha256'],dispatch_module._canonical(row['payload']),row['prev_hash'],digest,row['seq']))
    connection.commit()
    connection.execute('PRAGMA foreign_keys='+str(foreign_keys))
    assert not connection.execute('PRAGMA foreign_key_check').fetchall()
    assert dispatch.evidence_snapshot()['reservations'][-1]['record_hash']==new_hash
    with readonly_stores(f) as stores:
        with pytest.raises(ValueError,match='DEMO_AUTHORITY_SIGNATURE_INVALID'):
            with history.open_history(f.root,public=public,uid=f.executor_uid,retention=stores['retention'],verified_public=f.verified_public) as book:
                book.replay_trigger(trigger_sha256=trigger['record_hash'],runtime_sha256=_digest(f.base_runtime))


def test_original_receipt_event_body_must_match_plan_even_if_rehashed_and_projected(native_bar_checkpoint):
    from tools import demo_gold_historical_position as history
    from live.execution.order_event_store import create_order_event, _event_hash
    from dataclasses import asdict
    from live.execution.oms import OmsAuthority
    f=native_bar_checkpoint; trigger=json.loads(next(f.root.glob('bar-exit-trigger-*.json')).read_bytes())
    with readonly_stores(f) as stores:public=component_arguments(f,stores)['public']
    records=f.events.events();connection=f.events._conn
    connection.execute('DROP TRIGGER live_order_events_no_update')
    previous=''
    for row in records:
        value=asdict(row.event);value.pop('canonical_state');value.pop('content_hash')
        if row.seq==2:value['avg_fill_price']=101.0
        event=create_order_event(**value); digest=_event_hash(row.seq,event,previous)
        connection.execute('UPDATE live_order_events SET avg_fill_price=?,content_hash=?,prev_hash=?,event_hash=? WHERE seq=?',
            (event.avg_fill_price,event.content_hash,previous,digest,row.seq))
        previous=digest
    connection.commit()
    # The genuine original projector rebuilds its full read-model from the
    # rehashed events. This cannot turn changed venue prices into old receipts.
    oms=f.stores['oms']
    for table in ('live_oms_projections','live_fills','live_orders'):oms._conn.execute('DELETE FROM '+table)
    oms._conn.commit();OmsAuthority(f.events,oms).replay()
    assert f.events.events()[1].event.avg_fill_price==101.0
    with readonly_stores(f) as stores:
        with pytest.raises(ValueError,match='HISTORICAL_RECEIPT_EVENT_BINDING'):
            with history.open_history(f.root,public=public,uid=f.executor_uid,retention=stores['retention'],verified_public=f.verified_public) as book:
                book.replay_trigger(trigger_sha256=trigger['record_hash'],runtime_sha256=_digest(f.base_runtime))


def test_original_grants_use_exact_registry_prefix_and_original_signature_time(native_bar_checkpoint):
    from tools import demo_gold_historical_position as history
    f=native_bar_checkpoint; trigger=json.loads(next(f.root.glob('bar-exit-trigger-*.json')).read_bytes())
    with readonly_stores(f) as stores:
        public=component_arguments(f,stores)['public']
        key=deepcopy(public['registry']['keys'][-1]); key['key_id']='later-fixture-key'
        key['valid_from_ms']=max(row['valid_until_ms'] for row in public['registry']['keys'])+1
        key['valid_until_ms']=key['valid_from_ms']+100000
        public['registry']['keys'].append(key)
        # The actual original key is now naturally expired; its dates/status
        # are unchanged and the archived reserve happened inside its window.
        f.clock[0]=key['valid_from_ms']+1
        f.refresh_public(public['registry']);public=deepcopy(f.history_public)
        with history.open_history(f.root,public=public,uid=f.executor_uid,retention=stores['retention'],verified_public=f.verified_public) as book:
            value=book.replay_trigger(trigger_sha256=trigger['record_hash'],runtime_sha256=_digest(f.base_runtime))
            assert value['context']['quantity']=='0.1'
        public['registry']['keys'][0]['status']='REVOKED'
        f.refresh_public(public['registry']);public=deepcopy(f.history_public)
        with pytest.raises(history.HistoricalContextUnproven,match='HISTORICAL_REGISTRY_PREFIX_REQUIRED'):
            with history.open_history(f.root,public=public,uid=f.executor_uid,retention=stores['retention'],verified_public=f.verified_public) as book:
                book.replay_trigger(trigger_sha256=trigger['record_hash'],runtime_sha256=_digest(f.base_runtime))


def test_historical_reader_requires_original_trigger_filename(native_bar_checkpoint):
    from tools import demo_gold_historical_position as history
    f=native_bar_checkpoint; path=next(f.root.glob('bar-exit-trigger-*.json')); trigger=json.loads(path.read_bytes())
    with readonly_stores(f) as stores:public=component_arguments(f,stores)['public']
    path.rename(path.with_name('bar-exit-trigger-'+'f'*64+'.json'))
    with readonly_stores(f) as stores:
        with pytest.raises(ValueError,match='HISTORICAL_TRIGGER_PATH'):
            with history.open_history(f.root,public=public,uid=f.executor_uid,retention=stores['retention'],verified_public=f.verified_public) as book:
                book.replay_trigger(trigger_sha256=trigger['record_hash'],runtime_sha256=_digest(f.base_runtime))


def test_historical_cached_book_rejects_new_root_members(native_bar_checkpoint):
    from tools import demo_gold_historical_position as history
    f=native_bar_checkpoint; trigger=json.loads(next(f.root.glob('bar-exit-trigger-*.json')).read_bytes())
    with readonly_stores(f) as stores:
        public=component_arguments(f,stores)['public']
        with pytest.raises(ValueError,match='HISTORICAL_SOURCE_CHANGED'):
            with history.open_history(f.root,public=public,uid=f.executor_uid,retention=stores['retention'],verified_public=f.verified_public) as book:
                book.replay_trigger(trigger_sha256=trigger['record_hash'],runtime_sha256=_digest(f.base_runtime))
                (f.root/'bar-exit-trigger-unreviewed.json').write_text('{}')
                book.replay_trigger(trigger_sha256=trigger['record_hash'],runtime_sha256=_digest(f.base_runtime))
