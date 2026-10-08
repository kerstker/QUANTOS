"""Controller approvals consume original executor evidence across custody roots.

Public transport, installed authority and controller custody are isolated test
substitutes. Archive/source verification, forward journals, fresh reconstruction,
retained loading, immutable tail replay and task materialization remain native.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from live.execution.demo_batch_risk import _digest
from tests.live.test_gold_strategy_constraints import case as case, request_inputs
from tests.research.test_gold_bar_dataset import raw_rows, STEP
from tests.tools.test_gold_bar_definition import archive
from tests.tools.test_gold_bar_episode_tail import message
from tests.tools.test_gold_bar_observe import recorded
from tests.tools.test_gold_budget_scope_install import copy_definition_source
from tests.tools.test_gold_strategy_approval_request import inputs
from tools import gold_strategy_approval_request as approval
from tools import gold_strategy_dispatch as dispatch
from tools.gold_strategy_request import constraints_from_draft


def private_tree(root):
    for path in (root, *root.rglob('*')):
        assert not path.is_symlink()
        path.chmod(0o700 if path.is_dir() else 0o600)


def bytes_by_path(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def evidence_snapshot(root):
    """Detect additions, replacement and same-byte rewrites, excluding atime."""
    result = {}
    for path in (root, *root.rglob('*')):
        info = path.lstat()
        result[str(path.relative_to(root))] = (
            info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            path.read_bytes() if path.is_file() else None)
    return result


@pytest.fixture(scope='module')
def original_evidence(tmp_path_factory):
    """Build one genuine archived definition and public observation, offline."""
    from live.exchange import demo_network
    from tools import gold_bar_forward as forward
    from tools.gold_bar_fresh_inputs import FreshEpisode
    from tools.gold_bar_episode_tail import EpisodeTail, restore_snapshot
    from tools.gold_bar_worker_inputs import load

    build = tmp_path_factory.mktemp('gold-original-evidence')
    root = build/'executor'
    root.mkdir(mode=0o700)
    trial = archive(build)
    manifest = json.loads((trial/'manifest.json').read_bytes())
    manifest_hash = hashlib.sha256((trial/'manifest.json').read_bytes()).hexdigest()
    trial_target = root/'strategy-bar-trials'/manifest_hash
    trial_target.parent.mkdir(mode=0o700)
    shutil.move(trial, trial_target)
    source = root/'strategy-bar-sources'/manifest['source']['commit']
    source.parent.mkdir(mode=0o700)
    copy_definition_source(Path(__file__).resolve().parents[2], source)
    public = build/'public-fixture'
    public.mkdir()
    end = raw_rows()[-1][6]+1
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(demo_network, 'proxy_url', lambda: None)
        patch.setattr(demo_network, 'tls_context', lambda: object())
        status, _ = recorded(public, patch, stop_after_close_ms=end+100,
                             protected_network=True)
        assert status['status'] == 'OBSERVED'
        receipt = public/'history/receipt.json'
        receipt.write_text(json.dumps(json.loads(receipt.read_bytes()),
                                      sort_keys=True, indent=2)+'\n')
        history_hash = hashlib.sha256((public/'history/receipt.json').read_bytes()).hexdigest()
        history = root/'strategy-bar-history'/history_hash
        shutil.copytree(public/'history', history)
        fresh = dict(schema='GOLD_BAR_FRESH_EPISODE_INPUT_V1', source_commit='c'*40,
                     trial_manifest_sha256=manifest_hash,
                     archive_source_commit=manifest['source']['commit'],
                     candidate='channel', seconds=20, history_sha256=history_hash)
        observation = root/'strategy-bar-observation-work'/_digest(fresh)
        observation.parent.mkdir(mode=0o700)
        times = iter([end+100, end+STEP+21])
        patch.setattr(forward, 'now_ms', lambda: next(times))
        patch.setattr(forward, 'source_identity', lambda: {'fixture': True})
        observed = asyncio.run(forward.run(trial_target, candidate='channel',
            source_directory=source, history=history, output=observation, seconds=20,
            first_close=True, protected_network=True))
        assert observed['status'] == 'FORWARD_SIGNALS_RECORDED'
    private_tree(root)
    episode = FreshEpisode(root, fresh, source_commit='c'*40, uid=os.getuid(), read_only=True)
    tail = EpisodeTail(root, bundle_sha256=_digest(fresh), engine=episode.engine,
                       entry_decision_ms=episode.entry_decision_ms)
    frozen, prefix, _ = tail.snapshot()
    rebuilt = restore_snapshot(root, bundle_sha256=_digest(fresh), engine=episode.engine,
        entry_decision_ms=episode.entry_decision_ms, snapshot_sha256=prefix)
    assert rebuilt.signal_at(episode.entry_decision_ms) == frozen.signal_at(episode.entry_decision_ms)
    # A later retained event must not alter the exact earlier decision prefix.
    later_raw, later_ms = message(1001*STEP, '90')
    tail.append(later_raw, received_at_ms=later_ms)
    observation_hash = hashlib.sha256((observation/'result.json').read_bytes()).hexdigest()
    retained = dict(schema='GOLD_BAR_RETAINED_EPISODE_INPUT_V1', source_commit='c'*40,
                    trial_manifest_sha256=manifest_hash,
                    archive_source_commit=manifest['source']['commit'],
                    observation_sha256=observation_hash,
                    entry_decision_ms=episode.entry_decision_ms)
    shutil.copytree(observation, root/'strategy-bar-observations'/observation_hash)
    private_tree(root/'strategy-bar-observations')
    engine, checked = load(root, retained, source_commit='c'*40, uid=os.getuid())
    assert checked == episode.recheck()
    definition = engine.verified_definition()['definition_hash']
    return dict(root=root, fresh=fresh, retained=retained, prefix=prefix,
                entry_ms=episode.entry_decision_ms, definition=definition,
                signal_hash=_digest(engine.signal_at(episode.entry_decision_ms)))


@pytest.fixture
def cross_root(case, original_evidence, tmp_path, monkeypatch):
    from tools import demo_operating_bundle as bundle, demo_supervisor as supervisor
    from tools import gold_bar_fresh_inputs as fresh, gold_bar_forward as forward
    from tools import gold_bar_capture as capture, gold_bar_episode_tail as tail

    original = original_evidence
    controller = tmp_path/'controller'
    controller.mkdir(mode=0o700)
    executor = tmp_path/'executor'
    shutil.copytree(original['root'], executor)
    for name in ('strategy-approval-requests', 'strategy-decisions', 'tasks', 'task-claims'):
        (controller/name).mkdir(mode=0o700)
    args = request_inputs(case)
    _, _, policy = inputs(case)
    at = original['entry_ms']
    policy.update(valid_from_ms=at-100, valid_until_ms=at+10000)
    policy['budget_group'].update(strategy_definition_sha256=original['definition'],
        valid_from_ms=policy['valid_from_ms'], valid_until_ms=policy['valid_until_ms'])
    registry = {'executor_uid': os.getuid()}
    monkeypatch.setattr(bundle, 'EXECUTOR', executor)
    monkeypatch.setattr(supervisor, 'STATE', controller)
    monkeypatch.setattr(supervisor, '_root_private_directory', lambda _: None)
    monkeypatch.setattr(supervisor, '_read_root_owned_json', lambda path: path.read_bytes())
    monkeypatch.setattr(supervisor, 'configuration', lambda _: (registry, policy))
    monkeypatch.setattr(approval, 'approval_configuration', lambda _: (registry, policy))
    monkeypatch.setattr(supervisor, 'now_ms', lambda: at+10)
    monkeypatch.setattr(bundle, 'write_new', lambda path, body: path.write_bytes(body))
    profile = controller/'profile.json'
    profile.write_text(json.dumps(args['profile']))
    monkeypatch.setattr(dispatch, 'PROFILE', profile)
    forbidden = []

    def no_capture_or_submission(*a, **kw):
        forbidden.append(True)
        raise AssertionError('original evidence must not capture, append, or submit')

    for module, name in ((fresh, 'observe'), (forward, 'observe'), (capture, '_fetch'),
                         (tail, 'observe'), (dispatch.demo_task, 'run')):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, no_capture_or_submission)

    def install(kind):
        runtime = deepcopy(original[kind])
        draft = dict(schema='GOLD_BAR_DEMO_ENTRY_DRAFT_V1', status='BAR_ENTRY_DRAFT',
            environment='BINANCE_USDM_DEMO', observed_at_ms=at, valid_until_ms=at+100,
            signal_valid_until_ms=at+5000, definition_hash=original['definition'],
            origin_sha256=_digest(args['origin']), orders=deepcopy(args['orders']),
            pilot_control=dict(status='ACTIVE', configuration_sha256='e'*64, submission_enabled=False),
            submission_enabled=False, economic_authority=False, risk_status='NOT_RUN')
        draft['result_hash'] = _digest(draft)
        constraints = constraints_from_draft(draft, now_ms=at+10)
        policy['bindings']['execution_constraints_sha256'] = _digest(constraints)
        decision = dict(schema='GOLD_STRATEGY_CYCLE_DECISION_V1', source_commit='c'*40,
            bundle_sha256=_digest(runtime), account_ref_sha256=policy['account_ref_sha256'],
            observed_at_ms=at, strategy_definition_sha256=original['definition'],
            bar_signal_sha256=original['signal_hash'], status='DECISION_REQUIRES_APPROVAL',
            draft=draft, execution_constraints=constraints, submission_enabled=False, economic_authority=False)
        if kind == 'fresh':
            decision['bar_continuation_sha256'] = original['prefix']
        result = dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1', status='DECISION_RETAINED',
            source_commit='c'*40, bundle_sha256=_digest(runtime), decision_sha256=_digest(decision),
            decision=decision, submission_enabled=False, economic_authority=False)
        request = approval.prepare(result, runtime=runtime, policy=policy, now_ms=at+10)
        reference = dict(schema='GOLD_STRATEGY_TASK_APPROVAL_REFERENCE_V1', source_commit='c'*40,
            result_sha256=_digest(result), issuer_policy_sha256=_digest(policy),
            kill_switch=args['kill_state'].model_dump(mode='json'))
        for path, value in ((controller/'strategy-runtime.json', runtime),
                (controller/'strategy-decisions'/(_digest(result)+'.json'), result),
                (controller/'strategy-approval-requests'/(_digest(request)+'.json'), request),
                (controller/'strategy-task-approval.json', reference)):
            path.write_text(json.dumps(value))
            path.chmod(0o600)
        return request

    def consume(consumer, request):
        if consumer == 'approval':
            return approval.review_retained(source_commit='c'*40, stored_request_sha256=_digest(request))
        return dispatch.run(source_commit='c'*40)

    return dict(controller=controller, executor=executor, original=original,
                registry=registry, forbidden=forbidden, install=install, consume=consume,
                stores=args)


@pytest.mark.parametrize(('consumer', 'kind'), [
    ('approval', 'fresh'), ('dispatch', 'fresh'), ('dispatch', 'retained')])
def test_controller_reads_native_executor_originals_without_evidence_writes(cross_root, consumer, kind):
    f = cross_root
    request = f['install'](kind)
    before = evidence_snapshot(f['executor'])
    controller_before = bytes_by_path(f['controller'])
    result = f['consume'](consumer, request)
    assert result['status'] == ('REQUEST_REVIEWED_NOT_APPROVED' if consumer == 'approval' else 'TASK_READY'), result
    assert evidence_snapshot(f['executor']) == before
    assert all((f['controller']/name).read_bytes() == body for name, body in controller_before.items())
    assert not any((f['controller']/name).exists() for name in (
        'strategy-bar-trials', 'strategy-bar-sources', 'strategy-bar-history',
        'strategy-bar-observation-work', 'strategy-bar-observations', 'strategy-bar-tails'))
    if consumer == 'approval':
        assert result['bar_prefix_review']['snapshot_sha256'] == f['original']['prefix']
        assert not list((f['controller']/'tasks').iterdir())
    else:
        task = json.loads(next((f['controller']/'tasks').iterdir()).read_bytes())
        assert task['execution_constraints']['strategy_definition_sha256'] == f['original']['definition']
    assert not f['forbidden']
    assert f['stores']['dispatch'].snapshot() == [] and f['stores']['oms'].orders() == []


@pytest.mark.parametrize(('consumer', 'kind'), [
    ('approval', 'fresh'), ('dispatch', 'fresh'), ('dispatch', 'retained')])
@pytest.mark.parametrize('damage', ['executor_uid', 'tampered_original', 'missing_original'])
def test_changed_or_missing_executor_originals_fail_closed(cross_root, consumer, kind, damage):
    f = cross_root
    request = f['install'](kind)
    runtime = f['original'][kind]
    if damage == 'executor_uid':
        f['registry']['executor_uid'] += 1
    elif kind == 'fresh':
        path = f['executor']/'strategy-bar-tails'/_digest(runtime)/'snapshots'/(f['original']['prefix']+'.json')
        if damage == 'missing_original':
            path.unlink()
        else:
            path.write_text('{}')
    else:
        path = f['executor']/'strategy-bar-observations'/runtime['observation_sha256']/'result.json'
        if damage == 'missing_original':
            path.unlink()
        else:
            path.write_text('{}')
    before = evidence_snapshot(f['executor'])
    if consumer == 'approval':
        with pytest.raises((ValueError, FileNotFoundError)):
            f['consume'](consumer, request)
    else:
        result = f['consume'](consumer, request)
        assert result['status'] == 'INPUTS_UNRESOLVED', result
    assert evidence_snapshot(f['executor']) == before
    assert not list((f['controller']/'tasks').iterdir()) and not f['forbidden']
    assert f['stores']['dispatch'].snapshot() == [] and f['stores']['oms'].orders() == []
