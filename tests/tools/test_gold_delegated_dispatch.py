"""Actual dispatcher/materializer with native proof and original retained records.

Only deployment identities, the fixed public-file boundary and executor process
launch are isolated. Archive replay, ENABLE content checks, readonly stores,
retention, controller lease, admission and task materialization remain native.
Frozen fixture clocks establish correctness, never five-second wall latency.
"""
from contextlib import ExitStack, closing
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import time
from types import SimpleNamespace

import pytest

from live.execution import demo_authority, demo_gold_delegation_enable as enabling
from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import budget_group_binding
from tests.live.test_demo_gold_delegated_issuer import native_issuer_case
from tests.live.test_gold_delegated_pipeline import task_material
from tests.live.test_gold_strategy_constraints import case as case
from tests.tools.test_gold_runtime_evidence import original_evidence as original_evidence
from tools import gold_strategy_dispatch as dispatch
from tools import gold_strategy_approval_request as approval
from tools import demo_operating_bundle as bundle
from tools import gold_pilot_retention as retention
from tools.demo_operator_setup import canonical
from tools.demo_resident_recovery import _check_path


def native_runtime(f, kind, original, tmp_path, monkeypatch):
    """New synthetic enrollment copy with actual fresh/AUTO storage allocation.

    Original signed declarations are generated for this test runtime before any
    dispatcher use. No existing retention/capacity history is reset or migrated.
    """
    if kind == 'retained':
        return
    from live.execution import demo_position_context as position
    from live.execution import demo_gold_execution_delegation as contract
    from tests.live.test_gold_execution_delegation import sign
    from tools import gold_bar_pilot_capacity as capacity
    from tools.gold_bar_fresh_inputs import FreshEpisode
    from tools.gold_bar_episode_tail import EpisodeTail
    from tools.gold_pilot_retention_store import Retention
    from tools.gold_delegated_proof import freeze_prefix

    root = tmp_path/('executor-'+kind)
    root.mkdir(mode=0o700)
    for name in ('strategy-bar-trials', 'strategy-bar-sources', 'strategy-bar-history'):
        shutil.copytree(original['root']/name, root/name)
    for name in ('gold-pilot',):
        shutil.copytree(f.root/name, root/name)
    for name in ('gold-pilot-request.json', 'dispatch.db', 'recovery.db', 'oms.db'):
        shutil.copy2(f.root/name, root/name)
    runtime = deepcopy(original['fresh'])
    if kind == 'auto':
        runtime['schema'] = 'GOLD_BAR_AUTO_EPISODE_INPUT_V1'
        runtime['episode_id'] = 'e'*64
        runtime.pop('history_sha256')
    original_context = capacity.original_context(root, runtime, uid=os.getuid(),
        account=f.base_policy['account_ref_sha256'])
    history = original['root']/'strategy-bar-history'/original['fresh']['history_sha256']
    if kind == 'auto':
        area, binding = capacity.storage(root, 'history', original_context, runtime, uid=os.getuid())
        capacity.allocate_capture(area, binding, 'history', os.getuid(), runtime)
        from tools import gold_bar_capture as capture, gold_bar_forward as forward
        from tests.tools.test_gold_bar_observe import recorded
        from tests.research.test_gold_bar_dataset import STEP
        from live.exchange import demo_network
        import asyncio
        import hashlib
        raw = (history/'response.json').read_bytes()
        receipt = json.loads((history/'receipt.json').read_bytes())
        history = area/_digest(runtime)

        def fetch(output, endpoint, requested, *, protected_network):
            assert protected_network is True and requested == receipt['requested_at_ms']
            for name in ('response.json', 'response.wire'):
                (output/name).write_bytes(raw)
            capture._write(output/'receipt.json', dict(receipt, endpoint=endpoint,
                content_encoding='identity', wire_sha256=hashlib.sha256(raw).hexdigest(),
                network_mode='PROTECTED_DEMO_NETWORK_V1'))
            return raw, receipt['received_at_ms']

        with monkeypatch.context() as transport:
            transport.setattr(capture, '_fetch', fetch)
            transport.setattr(capture.time, 'time_ns', lambda: receipt['requested_at_ms']*1_000_000)
            capture.capture(history, protected_network=True)
        area, binding = capacity.storage(root, 'observations', original_context, runtime, uid=os.getuid())
        capacity.allocate_capture(area, binding, 'observations', os.getuid(), runtime)
        with monkeypatch.context() as transport:
            transport.setattr(demo_network, 'proxy_url', lambda: None)
            transport.setattr(demo_network, 'tls_context', lambda: object())
            fixture = tmp_path/'auto-public-fixture'
            fixture.mkdir()
            end = receipt['requested_at_ms']
            recorded(fixture, transport, stop_after_close_ms=end+100, protected_network=True)
            times = iter([end+100, end+STEP+21])
            transport.setattr(forward, 'now_ms', lambda: next(times))
            transport.setattr(forward, 'source_identity', lambda: {'fixture':True})
            result = asyncio.run(forward.run(root/'strategy-bar-trials'/runtime['trial_manifest_sha256'],
                candidate='channel',source_directory=root/'strategy-bar-sources'/runtime['archive_source_commit'],
                history=history,output=area/_digest(runtime),seconds=runtime['seconds'],
                first_close=True,protected_network=True))
            assert result['status'] == 'FORWARD_SIGNALS_RECORDED'
        from tests.tools.test_gold_runtime_evidence import private_tree
        private_tree(root)
    else:
        area = root/'strategy-bar-observation-work'
        area.mkdir(mode=0o700)
        observation = original['root']/'strategy-bar-observation-work'/_digest(original['fresh'])
        shutil.copytree(observation, area/_digest(runtime))
    episode = FreshEpisode(root, runtime, source_commit='c'*40, uid=os.getuid(), read_only=True)
    tail = EpisodeTail(root, bundle_sha256=_digest(runtime), engine=episode.engine,
        entry_decision_ms=episode.entry_decision_ms)
    _, prefix, _ = tail.snapshot()
    decision = deepcopy(f.proof['result']['decision'])
    decision.update(bundle_sha256=_digest(runtime), bar_continuation_sha256=prefix)
    result = deepcopy(f.proof['result'])
    result.update(bundle_sha256=_digest(runtime), decision_sha256=_digest(decision), decision=decision)
    with Retention(root, original=original_context, base_runtime=runtime, uid=os.getuid(), role='executor') as saved:
        claim = saved.claim(bundle=runtime, now_ms=f.now_ms)
        saved.put(claim, 'origin', f.proof['origin'])
        saved.put(claim, 'decision', decision)
        saved.put(claim, 'result', result)
        f.proof['capacity'] = deepcopy(saved.binding)
    f.delegation['terms']['runtime_sha256'] = _digest(runtime)
    sign(contract, f.private, f.delegation)
    f.enable['runtime'] = runtime
    f.enable['original_binding']['runtime_sha256'] = _digest(runtime)
    f.enable['operator_confirmation']['candidate_sha256'] = _digest(enabling.candidate(f.enable))
    f.proof.update(runtime=runtime, result=result, claim=claim,
        delegation_sha256=_digest(f.delegation), enable_sha256=_digest(f.enable),
        prefix=freeze_prefix(root=root,runtime=runtime,decision=decision,source_commit='c'*40,uid=os.getuid()))
    f.root = root
    monkeypatch.setattr(position, '_ROOT', root)

    def custody(path, *, directory):
        path.relative_to(root)
        _check_path(path, os.getuid(), directory=directory)

    monkeypatch.setattr(position, '_custody', custody)


def controller_custody(state, monkeypatch):
    """Virtualize root ownership only, preserving real descriptor/path checks."""
    supervisor = dispatch.supervisor
    uid, gid = os.getuid(), os.getgid()

    def private(path):
        path.relative_to(state)
        _check_path(path, uid, directory=True)

    def root_stat(info, *, directory=False):
        assert info.st_uid in (0, uid) if directory else info.st_uid == uid
        values = {name: getattr(info, name) for name in (
            'st_mode', 'st_uid', 'st_gid', 'st_dev', 'st_ino', 'st_nlink',
            'st_size', 'st_mtime_ns', 'st_ctime_ns')}
        values.update(st_uid=0, st_gid=0)
        if directory:
            values['st_mode'] &= ~0o022
        return SimpleNamespace(**values)

    def controller_fstat(fd):
        Path(os.readlink('/proc/self/fd/'+str(fd))).relative_to(state)
        return root_stat(os.fstat(fd))

    original_reader = supervisor._read_root_owned_json
    protected = demo_authority._require_protected

    def reader(path):
        path.relative_to(state)
        with monkeypatch.context() as mapping:
            mapping.setattr(demo_authority, '_require_protected',
                lambda info, *, directory: protected(root_stat(info, directory=directory), directory=directory))
            return original_reader(path)

    writer = bundle.write_new

    def write(path, raw):
        path.relative_to(state)
        return writer(path, raw, uid=uid, gid=gid)

    monkeypatch.setattr(supervisor, 'STATE', state)
    monkeypatch.setattr(supervisor, '_root_private_directory', private)
    monkeypatch.setattr(supervisor, '_read_root_owned_json', reader)
    monkeypatch.setattr(supervisor, 'os', SimpleNamespace(**{**vars(os), 'fstat': controller_fstat}))
    monkeypatch.setattr(bundle, 'write_new', write)


@pytest.fixture
def installed(case, original_evidence, tmp_path, monkeypatch, request):
    from live.execution.demo_dispatch_store import DemoDispatchStore
    from live.execution.order_recovery import SqliteOrderRecoveryStore
    from live.execution.position_ledger import LiveLedger
    from tools.gold_delegated_proof import export

    f = native_issuer_case(case, original_evidence, tmp_path, monkeypatch,
        configure_registry=lambda registry: registry.update(executor_uid=os.getuid(), research_uids=[3001]))
    native_runtime(f, getattr(request, 'param', 'retained'), original_evidence, tmp_path, monkeypatch)
    task, _, _ = task_material(f)
    state = tmp_path/'controller'
    state.mkdir(mode=0o700)
    (state/'task-claims').mkdir(mode=0o700)
    monkeypatch.setattr(os, 'geteuid', lambda: os.getuid())
    controller_custody(state, monkeypatch)
    monkeypatch.setattr(bundle, 'EXECUTOR', f.root)
    monkeypatch.setattr(approval, 'approval_configuration', lambda _: (f.registry, f.base_policy))
    monkeypatch.setattr(dispatch.supervisor, 'configuration', lambda _: (f.registry, f.base_policy))
    from tools import gold_strategy_controller
    monkeypatch.setattr(gold_strategy_controller, 'observation_configuration', lambda _: (f.registry, f.base_policy))
    f.clock = [f.now_ms]
    monkeypatch.setattr(dispatch.supervisor, 'now_ms', lambda: f.clock[0])
    config = dict(schema='GOLD_STRATEGY_APPROVED_POLICY_DISPATCH_V1', source_commit='c'*40,
        account_ref_sha256=f.base_policy['account_ref_sha256'],
        budget_group_binding_sha256=budget_group_binding(f.base_policy), kill_switch=task['kill_switch'])
    for name, value in (('strategy-runtime.json', f.proof['runtime']),
            ('strategy-dispatch.json', config), ('profile.json', task['profile'])):
        path = state/name
        path.write_bytes(canonical(value))
        path.chmod(0o600)
    monkeypatch.setattr(dispatch, 'PROFILE', state/'profile.json')
    f.enable['original_binding']['dispatcher_sha256'] = _digest(config)
    f.enable['operator_confirmation']['candidate_sha256'] = _digest(enabling.candidate(f.enable))
    f.proof['enable_sha256'] = _digest(f.enable)
    f.current_enable = deepcopy(f.enable)
    f.enable_reads = []

    def load_enabled(**kwargs):
        assert kwargs.pop('role') == 'controller'
        f.enable_reads.append(f.clock[0])
        return deepcopy(enabling.validate(f.current_enable, **kwargs))

    monkeypatch.setattr(enabling, 'load_enabled', load_enabled)
    retention.controller_retain(state, f.proof['result'], base_runtime=f.proof['runtime'],
        source_commit='c'*40, account=f.base_policy['account_ref_sha256'], executor_uid=os.getuid())
    f.exports = []
    f.export_ms = []
    f.after_export = lambda proof: proof

    def native_export(**kwargs):
        started = time.monotonic()
        assert kwargs == dict(source_commit='c'*40, registry=f.registry, runtime=f.proof['runtime'],
            identity=_digest(f.proof['result']), enable_sha256=_digest(f.enable),
            valid_until_ms=f.proof['result']['decision']['execution_constraints']['valid_until_ms'])
        with ExitStack() as stack:
            stores = dict(dispatch=stack.enter_context(closing(DemoDispatchStore(f.root/'dispatch.db',read_only=True))),
                recovery=stack.enter_context(closing(SqliteOrderRecoveryStore(str(f.root/'recovery.db'),read_only=True))),
                oms=stack.enter_context(closing(LiveLedger(str(f.root/'oms.db'),read_only=True))))
            value = export(root=f.root, runtime=kwargs['runtime'], result_sha256=kwargs['identity'],
                enable=f.enable, registry=f.registry, source_commit='c'*40, uid=os.getuid(),
                now_ms=f.clock[0], stores=stores)
        f.exports.append(value)
        f.export_ms.append((time.monotonic()-started)*1000)
        return f.after_export(value)

    monkeypatch.setattr(dispatch, 'export_delegated_proof', native_export)
    monkeypatch.setattr(dispatch.demo_task, 'run', lambda **kw: pytest.fail('read-only materialization submitted'))
    f.state = state
    f.run = lambda: dispatch.run(source_commit='c'*40, use_approved_policy=True,
        result_sha256=_digest(f.proof['result']))
    return f


@pytest.mark.parametrize('installed', ['retained', 'fresh', 'auto'], indirect=True)
def test_native_delegate_run_materializes_without_legacy_cold_review(installed, monkeypatch, record_property):
    f = installed
    selected = dispatch.approved_reference('c'*40, f.base_policy, result_sha256=_digest(f.proof['result']))
    assert selected[2]['result_sha256'] == _digest(f.proof['result'])
    calls = []

    def obsolete(**kwargs):
        calls.append(kwargs)
        raise ValueError('DELEGATED_LEGACY_COLD_REVIEW_FORBIDDEN')

    monkeypatch.setattr(dispatch, 'review_bar_runtime', obsolete)
    started = time.monotonic()
    status = f.run()
    record_property('dispatch_ms', (time.monotonic()-started)*1000)
    record_property('export_ms', f.export_ms)
    assert status['status'] == 'TASK_READY', (status, calls)
    assert calls == [] and len(f.exports) == 1 and len(f.enable_reads) == 2
    task_path = f.state/'tasks'/(status['task_sha256']+'.json')
    task = json.loads(task_path.read_bytes())
    assert task['schema'] == 'DEMO_GOLD_DELEGATED_REQUEST_TASK_V1'
    assert task['delegated_admission'] == f.exports[0] == f.proof
    assert task['execution_constraints'] == f.proof['result']['decision']['execution_constraints']
    assert task['issuer_policy_sha256'] == _digest(f.base_policy)
    assert not list((f.state/'task-claims').iterdir())


@pytest.mark.parametrize('damage', [
    'export_failure', 'prefix_bytes', 'proof_result', 'proof_runtime', 'enable',
    'policy', 'dispatcher', 'profile', 'runtime', 'retained_result', 'deadline',
])
def test_native_delegate_rejects_cross_boundary_drift_before_task(installed, monkeypatch, damage):
    f = installed

    def changed(proof):
        if damage == 'export_failure':
            raise OSError('isolated executor transport loss')
        if damage == 'prefix_bytes':
            import base64
            prefix = proof['prefix']['files']
            name = 'journal/messages.jsonl'
            prefix[name] = base64.b64encode(base64.b64decode(prefix[name])+b'{}\n').decode()
        elif damage == 'proof_result':
            # Still valid result structure, but not the original selected bytes.
            proof['result']['recovery_observation'] = {'new_order_submission_enabled':False}
        elif damage == 'proof_runtime':
            proof['runtime']['observation_sha256'] = 'f'*64
        elif damage == 'enable':
            f.current_enable['registration_sha256'] = 'f'*64
            f.current_enable['operator_confirmation']['candidate_sha256'] = _digest(enabling.candidate(f.current_enable))
        elif damage == 'policy':
            policy = deepcopy(f.base_policy)
            policy['policy_id'] = 'changed-independent-installed-policy'
            monkeypatch.setattr(dispatch.supervisor, 'configuration', lambda _: (f.registry, policy))
        elif damage in ('dispatcher', 'profile', 'runtime'):
            name = {'dispatcher':'strategy-dispatch.json','profile':'profile.json','runtime':'strategy-runtime.json'}[damage]
            path = f.state/name
            path.write_bytes(path.read_bytes()+b' ')
        elif damage == 'retained_result':
            events = f.state/'gold-pilot-retention-audit'/'events'
            paths = [path for path in events.iterdir() if json.loads(path.read_bytes()).get('kind') == 'result']
            assert len(paths) == 1
            paths[0].write_bytes(b'{}\n')
        elif damage == 'deadline':
            f.clock[0] = proof['result']['decision']['execution_constraints']['valid_until_ms']
        return proof

    f.after_export = changed
    status = f.run()
    assert status['status'] == 'INPUTS_UNRESOLVED', status
    assert status['task_sha256'] is None and status['retry_submission'] is False
    assert status['submission_enabled'] is status['economic_authority'] is False
    assert len(f.exports) == 1
    assert not (f.state/'tasks').exists() and not list((f.state/'task-claims').iterdir())
    expected = ('ORIGINAL_TASK_MATERIALIZATION' if damage in
        ('policy','dispatcher','profile','runtime','retained_result') else 'EXECUTOR_DELEGATED_PROOF')
    assert status['phase'] == expected


def test_deadline_crossed_after_proof_review_never_materializes(installed, monkeypatch):
    f = installed
    configuration = dispatch.supervisor.configuration

    def elapsed(source):
        f.clock[0] = f.proof['result']['decision']['execution_constraints']['valid_until_ms']
        return configuration(source)

    monkeypatch.setattr(dispatch.supervisor, 'configuration', elapsed)
    status = f.run()
    assert status['status'] == 'INPUTS_UNRESOLVED' and status['phase'] == 'ORIGINAL_TASK_MATERIALIZATION'
    assert len(f.exports) == 1 and not (f.state/'tasks').exists()
    assert not list((f.state/'task-claims').iterdir())


@pytest.mark.parametrize('installed', ['retained', 'fresh', 'auto'], indirect=True)
def test_exact_installed_bar_policy_keeps_original_cold_review(installed, monkeypatch):
    f = installed
    f.base_policy['bindings']['execution_constraints_sha256'] = _digest(f.proof['result']['decision']['execution_constraints'])
    original = dispatch.review_bar_runtime
    calls = []

    def reviewed(**kwargs):
        value = original(**kwargs)
        calls.append(value)
        return value

    monkeypatch.setattr(dispatch, 'review_bar_runtime', reviewed)
    monkeypatch.setattr(dispatch, 'export_delegated_proof', lambda **kw: pytest.fail('exact approval used delegation'))
    status = f.run()
    assert status['status'] == 'TASK_READY', status
    assert len(calls) == 2 and calls[0] == calls[1]
    task = json.loads((f.state/'tasks'/(status['task_sha256']+'.json')).read_bytes())
    assert task['schema'] == 'DEMO_GOLD_STRATEGY_REQUEST_TASK_V1'
    assert 'delegated_admission' not in task and not f.enable_reads


def test_native_delegate_existing_task_and_interrupted_claim_do_not_resend(installed):
    f = installed
    first = f.run()
    assert first['status'] == 'TASK_READY', first
    task = f.state/'tasks'/(first['task_sha256']+'.json')
    before = task.stat()
    raw = task.read_bytes()
    assert f.run() == first
    after = task.stat()
    assert raw == task.read_bytes()
    assert (before.st_ino,before.st_mtime_ns,before.st_ctime_ns) == (after.st_ino,after.st_mtime_ns,after.st_ctime_ns)
    claim = f.state/'task-claims'/(first['task_sha256']+'.json')
    claim.write_bytes(b'{')
    status = f.run()
    assert status['status'] == 'QUERY_ONLY' and status['task_sha256'] == first['task_sha256']
    assert claim.read_bytes() == b'{' and list((f.state/'tasks').iterdir()) == [task]


@pytest.mark.parametrize('clock_change', ['expired', 'rollback'])
def test_native_materialization_terminal_clock_prevents_execution(installed, monkeypatch, clock_change):
    f = installed
    original = dispatch.checkpoint.materialize
    written = []
    executions = []

    def materialized(task, result, **kwargs):
        status = original(task, result, **kwargs)
        assert status['status'] == 'TASK_READY'
        path = f.state/'tasks'/(status['task_sha256']+'.json')
        written.append((path, path.read_bytes(), path.stat()))
        # Time changes only after actual native validation and durable write.
        f.clock[0] = task['valid_until_ms'] if clock_change == 'expired' else kwargs['now_ms']-1
        return status

    def execute(**kwargs):
        executions.append(kwargs)
        return {'status':'ISOLATED_UNEXPECTED_EXECUTION'}

    monkeypatch.setattr(dispatch.checkpoint, 'materialize', materialized)
    monkeypatch.setattr(dispatch.demo_task, 'run', execute)
    status = dispatch.run(source_commit='c'*40,execute=True,use_approved_policy=True,
        result_sha256=_digest(f.proof['result']))
    assert executions == [], status
    assert status['status'] == ('EXPIRED_QUERY_ONLY' if clock_change == 'expired' else 'INPUTS_UNRESOLVED')
    assert len(written) == 1
    path, raw, before = written[0]
    after = path.stat()
    assert path.read_bytes() == raw
    assert (before.st_ino,before.st_mtime_ns,before.st_ctime_ns) == (after.st_ino,after.st_mtime_ns,after.st_ctime_ns)
    assert json.loads(raw)['valid_until_ms'] == f.proof['result']['decision']['execution_constraints']['valid_until_ms']
    assert not list((f.state/'task-claims').iterdir())
