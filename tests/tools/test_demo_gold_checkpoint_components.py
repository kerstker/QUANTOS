"""Native BAR component inventory; only venue/UID fixture boundaries are isolated."""
import hashlib
import json
import os

import pytest
from contextlib import contextmanager, ExitStack, closing

from live.execution.demo_batch_risk import _digest
from tests.live.test_gold_feedback_recovery_fixture import completed_bar_feedback as completed_bar_feedback
from tests.live.test_gold_strategy_constraints import case as case
from tests.tools.test_gold_runtime_evidence import original_evidence as original_evidence
from tests.live.gold_checkpoint_fixture import native_bar_checkpoint as native_bar_checkpoint
from tests.live.gold_checkpoint_fixture import source_attachment as source_attachment
from tests.tools.test_demo_gold_namespace_checkpoint import private_files as private_files
from tests.tools.test_demo_gold_namespace_checkpoint import gold_namespace as gold_namespace


@contextmanager
def readonly_stores(f):
    from live.execution.demo_dispatch_store import DemoDispatchStore
    from live.execution.order_recovery import SqliteOrderRecoveryStore
    from live.execution.position_ledger import LiveLedger
    from services.research.demo_user_stream import UserStreamJournal
    with ExitStack() as stack:
        paths = {key: value._conn.execute('PRAGMA database_list').fetchone()[2]
            for key, value in f.stores.items() if key in ('dispatch', 'oms')}
        paths['recovery'] = f.stores['recovery'].db_path
        values = {}
        for key, cls in (('dispatch', DemoDispatchStore), ('recovery', SqliteOrderRecoveryStore), ('oms', LiveLedger)):
            values[key] = cls(paths[key], read_only=True)
            stack.callback(values[key].close)
        values['stream'] = stack.enter_context(UserStreamJournal(f.stream.path, read_only=True))
        from tools.gold_bar_pilot_capacity import original_context
        from tools.gold_pilot_retention_store import Retention
        original = original_context(f.root, f.base_runtime, uid=f.executor_uid, account=f.account)
        values['retention'] = stack.enter_context(Retention(f.root, original=original,
            base_runtime=f.base_runtime, uid=f.executor_uid, role='executor', read_only=True, verify_history=True))
        yield values


def execution_records(f, stores):
    from pathlib import Path
    from live.execution.demo_pre_submit import _state
    from live.execution.demo_position_context import load_context
    from live.execution.demo_market_partition_runtime import context_reservations
    from services.research.demo_execution_journal import DemoExecutionJournal
    from tools.demo_reported_recovery import _read
    state = _state(stores['dispatch'], stores['recovery'], stores['oms'])
    context = load_context(state, symbol='XAUUSDT', account_ref=f.account, as_of_ms=f.clock[0])
    sources = context_reservations(state=state, context=context, as_of_ms=f.clock[0])
    records = {}
    for reservation, source in sources:
        plan = _read(f.root/'receipt-reconciliation'/'plans'/(source['plan_sha256']+'.json'), f.root, 16_000_000)
        proof = _read(f.root/'evidence'/(plan['proposal_sha256']+'.json'), f.root, 16_000_000)
        with DemoExecutionJournal(Path(plan['journal_path']), read_only=True) as journal:
            _, tail, _ = journal.read()
            records[source['plan_sha256']] = dict(plan=plan, proof=proof,
                journal=dict(manifest=journal.manifest, manifest_sha256=journal.manifest_hash, tail=tail))
    return dict(state=state, context=context, reservation_sources=sources, records=records)


def component_arguments(f, stores):
    from tools.gold_bar_pilot_capacity import original_context, _capacity
    original = original_context(f.root, f.base_runtime, uid=f.executor_uid, account=f.account)
    public = dict(schema='GOLD_CHECKPOINT_PUBLIC_CONTEXT_V1', source_commit=f.source_commit,
        registry=f.registry, account_ref_sha256=f.account, base_runtime=f.base_runtime,
        current_runtime=f.current_runtime, records=dict(rotation=dict(base=f.base_runtime,
            current=f.current_runtime, records=getattr(f, 'rotation_records', []), capacity=_capacity(original, f.base_runtime))))
    return dict(namespace='executor', public=public, original=original, uid=f.executor_uid,
        readonly_stores=stores, execution=execution_records(f, stores), market_partition=None, retention=stores['retention'])


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_ino, p.stat().st_size,
        hashlib.sha256(p.read_bytes()).hexdigest()) for p in root.rglob('*') if p.is_file()}


def assert_only_trigger_context_pending(view):
    assert len(view['exit_triggers']) == 1
    assert view['partial'] == [dict(component='exit_trigger', record_hash=view['exit_triggers'][0]['record_hash'],
        status='HISTORICAL_POSITION_CONTEXT_REQUIRED')]


@pytest.mark.parametrize('retained', [False, True], ids=['fresh', 'retained'])
def test_fixed_native_observation_format_has_no_automatic_rotation(source_attachment, tmp_path, monkeypatch, retained):
    from copy import deepcopy
    from tests.live.gold_checkpoint_fixture import fixed_observation_inputs
    from tools import demo_gold_checkpoint_components as components
    root, runtime, _ = fixed_observation_inputs(tmp_path, source_attachment, monkeypatch, retained=retained)
    public = dict(source_commit=runtime['source_commit'], account_ref_sha256='a'*64,
        base_runtime=runtime, current_runtime=runtime,
        records=dict(rotation=dict(base=runtime, current=runtime, records=[], capacity=None)))
    before = snapshot(root)
    view = components.verify(root, namespace='executor', public=public, original=None, uid=os.getuid())
    assert view['capacity'] is None and view['rotation']['records'] == 0
    assert view['episodes'][0]['observation']['status'] == 'COMPLETE'
    assert view['partial'] == [] and snapshot(root) == before
    wrong = deepcopy(public)
    key = 'observation_sha256' if retained else 'history_sha256'
    wrong['current_runtime'] = dict(runtime, **{key:'f'*64})
    wrong['records']['rotation']['current'] = wrong['current_runtime']
    with pytest.raises(ValueError, match='FIXED_RUNTIME_ROTATION'):
        components.verify(root, namespace='executor', public=wrong, original=None, uid=os.getuid())


@pytest.mark.parametrize('damage', ['receipt', 'response'])
def test_fresh_history_before_observation_replays_original_binding(source_attachment, tmp_path, monkeypatch, damage):
    from tests.live.gold_checkpoint_fixture import fixed_observation_inputs
    from tools import demo_gold_checkpoint_components as components
    root, runtime, _ = fixed_observation_inputs(tmp_path, source_attachment, monkeypatch,
        retained=False, start_observation=False)
    public = dict(source_commit=runtime['source_commit'], account_ref_sha256='a'*64,
        base_runtime=runtime, current_runtime=runtime,
        records=dict(rotation=dict(base=runtime, current=runtime, records=[], capacity=None)))
    before = snapshot(root)
    view = components.verify(root, namespace='executor', public=public, original=None, uid=os.getuid())
    assert view['episodes'][0]['history']['status'] == 'COMPLETE'
    assert view['episodes'][0]['observation']['status'] == 'NOT_STARTED'
    assert snapshot(root) == before
    history = root/'strategy-bar-history'/runtime['history_sha256']
    if damage == 'receipt':
        path = history/'receipt.json'
        path.write_bytes(path.read_bytes()+b' ')
    else:
        path = history/'response.json'
        rows = json.loads(path.read_bytes())
        rows[0][4] = '1'
        path.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match='FRESH_HISTORY'):
        components.verify(root, namespace='executor', public=public, original=None, uid=os.getuid())


@pytest.mark.parametrize('retained', [False, True], ids=['fresh', 'retained'])
def test_fixed_format_namespace_uses_original_transport_bucket_and_restores(gold_namespace, tmp_path, monkeypatch, retained):
    import shutil
    from tests.live.gold_checkpoint_fixture import fixed_observation_inputs
    from tests.tools.test_demo_gold_namespace_checkpoint import capture, materialize
    from tools import demo_gold_namespace_checkpoint as inventory
    f = gold_namespace
    build = tmp_path/'bar-build'; build.mkdir(mode=0o700)
    root, runtime, archived = fixed_observation_inputs(build, f.attachment, monkeypatch, retained=retained)
    # This fixture adds only original definition/history/observation artifacts
    # to its already enrolled real empty book. It never rewrites enrollment.
    for directory in root.iterdir():
        assert directory.is_dir()
        shutil.copytree(directory, f.root/directory.name)
    f.attachment = dict(schema='GOLD_CHECKPOINT_SOURCE_SET_V1', current_commit=f.commit,
        attachments=sorted([f.attachment, archived], key=lambda row:row['identity']['commit']))
    f.public.update(base_runtime=runtime, current_runtime=runtime,
        records=dict(rotation=dict(base=runtime, current=runtime, records=[], capacity=None)))
    f.cut['public_bundle_sha256'] = _digest(f.public)
    before = snapshot(f.root)
    destination = tmp_path/'fixed-restored'
    with capture(f, monkeypatch) as captured:
        manifest, receipt = materialize(captured, destination)
        expected = captured.semantic_view()
        assert captured.manifest()['component_bounds']['ordinary']['max_files'] == 100000
        assert 'bar_history' not in captured.manifest()['component_bounds']
    restored_before = snapshot(destination)
    view = inventory.verify_restored_namespace(destination, inventory=manifest, role_receipt=receipt,
        public_bundle=f.public, source_attachment=f.attachment)
    assert view == expected and view['verified_partial'] == []
    assert view['bar_inputs']['components']['episodes'][0]['observation']['status'] == 'COMPLETE'
    assert snapshot(f.root) == before and snapshot(destination) == restored_before


def test_mixed_unbound_prior_fixture_is_not_a_complete_inventory(completed_bar_feedback):
    from tools import demo_gold_checkpoint_components as components
    f = completed_bar_feedback; before = snapshot(f.root)
    with readonly_stores(f) as stores:
        # The historical shared fixture copied other candidate observations and
        # tails before this AUTO enrollment. Never discard or silently relabel
        # those as episodes in the active pilot's rotation history.
        with pytest.raises(ValueError, match='(EPISODE_OUTSIDE_ROTATION|MEMBER_BUDGET)'):
            components.verify(f.root, **component_arguments(f, stores))
    assert snapshot(f.root) == before


def test_retained_episode_pure_records_share_original_native_checks(completed_bar_feedback):
    from tools import gold_bar_feedback as feedback
    f = completed_bar_feedback
    with readonly_stores(f) as stores:
        evidence = execution_records(f, stores)
        pure = feedback.validate_retained_episode_records(**evidence, engine=f.engine,
            entry_decision_ms=f.entry_decision_ms, account=f.account)
        _, original, _ = feedback._retained_episode(f.root, engine=f.engine,
            entry_decision_ms=f.entry_decision_ms, account=f.account, stores=stores, as_of_ms=f.clock[0])
        assert len(pure) == len(original) == 2
        for row, batch in zip(pure, original, strict=True):
            assert row['prepared'] == {k:v for k,v in batch['prepared'].items() if k != 'proposal_path'}
            assert (row['start_ms'], row['end_ms']) == (batch['start_ms'], batch['end_ms'])
        key = next(iter(evidence['records']))
        evidence['records'][key]['journal']['tail'] = 'f'*64
        with pytest.raises(ValueError, match='NATIVE_JOURNAL_BINDING'):
            feedback.validate_retained_episode_records(**evidence, engine=f.engine,
                entry_decision_ms=f.entry_decision_ms, account=f.account)


def test_complete_cash_component_uses_actual_signed_source_before_first_reservation(native_bar_checkpoint):
    from tools import demo_gold_checkpoint_components as components
    from live.execution.demo_issuer_store import IssuerStore
    from tools.gold_bar_definition import verify_source
    f = native_bar_checkpoint
    assert f.source_commit != 'c'*40
    for attachment in f.source_attachment['attachments']:
        verify_source(attachment['source_root'], attachment['identity'])
    with closing(IssuerStore(f.ledger, read_only=True)) as issuer:
        assert issuer.snapshot() == f.issuer_rows
        assert all(row['payload']['policy']['executor_release_commit'] == f.source_commit
            for row in issuer.snapshot() if row['kind'] == 'RESERVED')
    assert f.base_runtime['archive_source_commit'] != f.source_commit
    with readonly_stores(f) as stores:
        view = components.verify(f.root, **component_arguments(f, stores))
    episode, = view['episodes']
    assert episode['bundle_sha256'] == _digest(f.base_runtime)
    assert episode['history']['status'] == episode['observation']['status'] == 'COMPLETE'
    assert episode['opening']['origin_sha256'] == f.opening['origin_sha256']
    assert episode['feedback']['artifact_sha256'] == f.exported['artifact_sha256']
    assert episode['feedback']['account_report_sha256'] == f.artifact['account_report_sha256']
    assert_only_trigger_context_pending(view)


def inventory_arguments(f, monkeypatch):
    from tools import demo_gold_namespace_checkpoint as inventory
    with readonly_stores(f) as stores:
        public = component_arguments(f, stores)['public']
    public['records']['pilot'] = dict(request=json.loads((f.root/'gold-pilot-request.json').read_bytes()),
        configuration=json.loads((f.root/'gold-pilot/configuration.json').read_bytes()),
        intent=json.loads((f.root/'gold-pilot/enrollment-intent.json').read_bytes()),
        receipt=json.loads((f.root/'gold-pilot/enrollment.json').read_bytes()), stop=None)
    cut = dict(schema='GOLD_CHECKPOINT_CUT_REQUEST_V1', operation_sha256='e'*64,
        source_commit=f.source_commit, registry_sha256=_digest(f.registry), public_bundle_sha256=_digest(public),
        roles=['executor', 'controller', 'issuer', 'research'], writer_barrier_sha256='f'*64,
        opening_at_ms=f.clock[0], submission_enabled=False, economic_authority=False)
    monkeypatch.setitem(inventory.ROLE_ROOTS, 'executor', f.root)
    monkeypatch.setattr(inventory, '_role_uid', lambda registry, role:os.getuid())
    monkeypatch.setattr(inventory.os, 'geteuid', os.getuid)
    monkeypatch.setattr(inventory.os, 'getegid', os.getgid)
    monkeypatch.setattr(inventory.os, 'getgroups', lambda:[os.getuid()])
    return dict(namespace='executor', source_commit=f.source_commit, operation_sha256='e'*64,
        cut_request=cut, public_bundle=public, source_attachment=f.source_attachment, guard=lambda:None)


def test_actual_complete_bar_namespace_inventory_preserves_all_original_files(native_bar_checkpoint, monkeypatch):
    from tools import demo_gold_namespace_checkpoint as inventory
    f = native_bar_checkpoint
    before = snapshot(f.root)
    with inventory.capture_inventory(**inventory_arguments(f, monkeypatch)) as captured:
        rows = list(captured.iter_entries())
        assert {row['relative'] for row in rows if row['kind'] == 'FILE'} == {
            relative for relative in before if '.git' not in relative.split('/')}
        assert captured.semantic_view()['bar_inputs']['components']['schema'] == 'GOLD_CHECKPOINT_BAR_COMPONENTS_V1'
        assert captured.manifest()['component_bounds']['ordinary']['max_files'] == 100000
    assert snapshot(f.root) == before


def test_actual_bar_component_isolated_restore_replays_original_bytes(native_bar_checkpoint, monkeypatch, tmp_path):
    from tools import demo_gold_namespace_checkpoint as inventory
    from tests.tools.test_demo_gold_namespace_checkpoint import materialize
    f = native_bar_checkpoint
    arguments = inventory_arguments(f, monkeypatch)
    before = snapshot(f.root)
    restored = tmp_path/'isolated-bar-restore'
    with inventory.capture_inventory(**arguments) as captured:
        original = captured.semantic_view()
        manifest, receipt = materialize(captured, restored)
    restored_before = snapshot(restored)
    view = inventory.verify_restored_namespace(restored, inventory=manifest, role_receipt=receipt,
        public_bundle=arguments['public_bundle'], source_attachment=f.source_attachment)
    assert view == original
    assert view['bar_inputs']['components']['episodes'][0]['feedback']['artifact_sha256'] == f.exported['artifact_sha256']
    trigger, = view['bar_inputs']['components']['exit_triggers']
    from live.execution.demo_prepared_execution import _load_proof
    original_context = _load_proof(f.exit_batch)['state']['position_context']
    assert original_context['quantity'] == '0.1' and f.context['quantity'] == '0'
    # This original fixture supplies no root-verified fixed public handle.
    # New history tests exercise the actual provider before claiming replay.
    assert trigger['status'] == 'HISTORICAL_PARTITION_RECORDS_REQUIRED'
    assert 'historical_position' not in trigger
    assert 'HISTORICAL_PARTITION_RECORDS_REQUIRED' in view['missing_dependencies']
    assert view['mutation_permitted'] is False
    assert snapshot(f.root) == before and snapshot(restored) == restored_before


@pytest.mark.parametrize('extra', ['foreign_episode', 'cash_extra', 'capture_extra'])
def test_component_members_cannot_hide_unknown_originals(native_bar_checkpoint, extra):
    from tools import demo_gold_checkpoint_components as components
    f = native_bar_checkpoint
    if extra == 'foreign_episode':
        target = f.root/'strategy-cash-work'/('f'*64)
        target.mkdir(mode=0o700)
    elif extra == 'cash_extra':
        target = f.root/'strategy-cash-work'/_digest(f.base_runtime)/'unexpected.json'
        target.write_text('{}\n'); target.chmod(0o600)
    else:
        target = f.root/'strategy-bar-reported-captures'/_digest(f.base_runtime)/'unexpected.json'
        target.write_text('{}\n'); target.chmod(0o600)
    with readonly_stores(f) as stores:
        with pytest.raises(ValueError, match='(EPISODE_OUTSIDE_ROTATION|MEMBER_SET|MEMBER_BUDGET)'):
            components.verify(f.root, **component_arguments(f, stores))


def test_native_trigger_cannot_substitute_another_retained_origin(native_bar_checkpoint):
    from tools import demo_gold_checkpoint_components as components
    f = native_bar_checkpoint
    path, = f.root.glob('bar-exit-trigger-*.json')
    value = json.loads(path.read_bytes())
    value['origin_sha256'] = _digest(f.settled_origin)
    value['record_hash'] = _digest({k:v for k,v in value.items() if k != 'record_hash'})
    path.write_text(json.dumps(value))
    with readonly_stores(f) as stores:
        with pytest.raises(ValueError, match='EXIT_TRIGGER_FRAME'):
            components.verify(f.root, **component_arguments(f, stores))


def test_original_cash_replay_requires_exact_historical_dispatch_prefix(native_bar_checkpoint):
    from copy import deepcopy
    from tools import gold_bar_feedback as feedback, gold_bar_reported_feedback as reported
    from live.execution.demo_prepared_execution import _load_proof
    f = native_bar_checkpoint
    _, batches, _ = feedback._retained_episode(f.root, engine=f.engine, entry_decision_ms=f.entry_decision_ms,
        account=f.account, stores=dict(f.stores, stream=f.stream), as_of_ms=f.clock[0])
    prepared = [b['prepared'] for b in batches]
    inputs = json.loads(next((f.root/'strategy-bar-reported-captures'/_digest(f.base_runtime)/'inputs').glob('*.json')).read_bytes())['inputs']
    arguments = dict(engine=f.engine, entry_decision_ms=f.entry_decision_ms, prepared=prepared,
        inputs=inputs, source_commit=f.source_commit, as_of_ms=f.clock[0], opening_record=f.opening,
        bundle_sha256=_digest(f.base_runtime))
    original = f.stores['dispatch'].evidence_snapshot()
    assert reported.build(**arguments, dispatch=f.stores['dispatch']) == f.artifact
    query = next(row for row in reversed(original['wire']) if row['kind'] == 'QUERY_RESULT')
    f.stores['dispatch'].record_query_result(reservation_sha256=query['reservation_sha256'],
        client_order_id=query['client_order_id'], requested_at_ms=f.clock[0]+1,
        outcome=deepcopy(query['payload']['outcome']), now_ms=f.clock[0]+2)
    current = f.stores['dispatch'].evidence_snapshot()
    assert current['reservations'] == original['reservations'] and current['wire'][:-1] == original['wire']
    # The original producer can only bind today's full snapshot. Reusing it
    # for an older archive must not silently replace the original commitment.
    assert reported.build(**arguments, dispatch=f.stores['dispatch']) != f.artifact
    assert reported.build_from_records(**arguments, proofs=[_load_proof(p) for p in prepared],
        dispatch_snapshot=original) == f.artifact
    # Reconcile the newly observed original-ID query through the actual native
    # path. Leaving it unprojected would deliberately invalidate current context.
    from live.execution import demo_oms_reconciliation as reconciliation
    from live.execution.demo_pre_submit import _retain
    f.clock[0] += 3
    plan = reconciliation.prepare_resolution(f.exit_batch, dispatch=f.stores['dispatch'], stream=f.stream,
        start_ms=batches[-1]['start_ms'], end_ms=batches[-1]['end_ms'], source_commit=f.source_commit,
        output_directory=f.root/'receipt-reconciliation', as_of_ms=f.clock[0])
    assert plan['status'] == 'READY_FOR_OMS_PROJECTION'
    outcome = reconciliation._project_resolution(plan, prepared=f.exit_batch,
        **f.stores, event_store=f.events, now_ms=f.clock[0])
    _retain(f.root/'receipt-reconciliation/outcomes', outcome)
    from tools import demo_gold_checkpoint_components as components
    with readonly_stores(f) as stores:
        args = component_arguments(f, stores)
        missing = components.verify(f.root, **args)
        assert missing['episodes'][0]['feedback']['status'] == 'REPLAY_DEPENDENCY_REQUIRED'
        assert any(item['status'] == 'DISPATCH_PREFIX_REQUIRED' for item in missing['partial'])
        args['execution']['dispatch_cuts'] = {_digest(original):{
            name:len(original[name]) for name in ('reservations', 'wire')}}
        replayed = components.verify(f.root, **args)
        assert_only_trigger_context_pending(replayed)
        assert replayed['episodes'][0]['feedback']['artifact_sha256'] == f.exported['artifact_sha256']
        args['execution']['dispatch_cuts'][_digest(original)]['wire'] += 1
        with pytest.raises(ValueError, match='DISPATCH_PREFIX'):
            components.verify(f.root, **args)


def test_original_cash_and_following_native_observation_survive_rotation(native_bar_checkpoint, tmp_path, monkeypatch):
    from tests.live.gold_checkpoint_fixture import observe_following_episode, append_original_query
    from tools import demo_gold_checkpoint_components as components
    from live.execution.demo_issuer_store import IssuerStore
    f = native_bar_checkpoint
    original = append_original_query(f)
    def pending_capture(value):
        with readonly_stores(value) as stores:
            args = component_arguments(value, stores)
            args['execution']['dispatch_cuts'] = {_digest(original):{name:len(original[name]) for name in ('reservations', 'wire')}}
            pending = components.verify(value.root, **args)
            assert pending['episodes'][1]['history']['status'] == pending['episodes'][1]['observation']['status'] == 'NOT_STARTED'
    result = observe_following_episode(f, tmp_path, monkeypatch, before_observation=pending_capture)
    assert result['decision']['status'] == 'NO_ENTRY_SIGNAL'
    assert f.second_episode.account_opening['origin']['reads'] != f.opening['origin']['reads']
    before = snapshot(f.root)
    with readonly_stores(f) as stores:
        args = component_arguments(f, stores)
        args['execution']['dispatch_cuts'] = {_digest(original):{name:len(original[name]) for name in ('reservations', 'wire')}}
        view = components.verify(f.root, **args)
        controller_args = dict(args, namespace='controller')
        controller = components.verify(f.controller_root, **controller_args)
    first, second = view['episodes']
    assert first['feedback']['artifact_sha256'] == f.exported['artifact_sha256']
    assert second['history']['status'] == second['observation']['status'] == 'COMPLETE'
    assert second['cash']['status'] == 'OPENING_RETAINED'
    assert second['feedback'] is None
    assert_only_trigger_context_pending(view)
    assert view['rotation']['records'] == 1
    assert controller['rotation'] == view['rotation'] and controller['episodes'] == []
    assert snapshot(f.root) == before
    with closing(IssuerStore(f.ledger, read_only=True)) as issuer:
        assert issuer.snapshot() == f.issuer_rows


def test_actual_failed_cash_capture_remains_bounded_original_evidence(native_bar_checkpoint, tmp_path, monkeypatch):
    from tests.live.gold_checkpoint_fixture import observe_following_episode
    from tools import gold_bar_cash as cash, gold_strategy_cash as wallet
    from tools import demo_gold_checkpoint_components as components
    f = native_bar_checkpoint
    observe_following_episode(f, tmp_path, monkeypatch)
    def failed_capture(account, **kwargs):
        assert account == f.account
        def broken_transport(*args, **kwargs):
            raise TimeoutError('isolated unavailable GET')
        return wallet.collector.collect(credential_file=None, operator_read=True, observe_seconds=30,
            credential_reader=lambda _:('synthetic-key', 'synthetic-secret'), get=broken_transport,
            clock_ms=lambda:f.clock[0], sleep=lambda secs:f.clock.__setitem__(0,f.clock[0]+round(secs*1000)), **kwargs)
    monkeypatch.setattr(wallet, '_collect', failed_capture)
    with pytest.raises(ValueError, match='CLOSING_INCOMPLETE'):
        cash.close_window(f.root, bundle=f.current_runtime, account=f.account, source_commit=f.source_commit)
    before = snapshot(f.root)
    with readonly_stores(f) as stores:
        view = components.verify(f.root, **component_arguments(f, stores))
    assert view['episodes'][0]['feedback']['artifact_sha256'] == f.exported['artifact_sha256']
    pending = view['episodes'][1]['cash']
    assert pending['status'] == 'OPENING_RETAINED' and pending['completed_sha256'] is None
    assert len(pending['failures']) == 1
    assert snapshot(f.root) == before
