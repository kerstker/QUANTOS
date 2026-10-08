"""Native cash episode whose two source identities are real local bundles.

Only public venue I/O, synthetic authority/custody and the historical fixture's
database filenames are isolated. Every identity is selected before enrollment,
reservation and signing. No saved grant, receipt, result or state is rewritten.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.live.gold_feedback_recovery_fixture import completed_feedback
from tests.tools.test_demo_gold_namespace_checkpoint import source_attachment as source_attachment
from tests.live.test_gold_strategy_constraints import case as case


def git(*args, cwd):
    return subprocess.check_output(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null',
        '-c', 'user.name=Isolated Fixture', '-c', 'user.email=fixture@example.invalid',
        *args], cwd=cwd, text=True, stderr=subprocess.PIPE).strip()


def archived_inputs(tmp_path, attachment, monkeypatch):
    from tools import gold_bar_experiment as experiment, gold_bar_definition as definition
    from tools.gold_bar_review import review_request
    from tests.tools.test_gold_bar_cost_replay import inputs
    from tests.tools.test_gold_runtime_evidence import private_tree
    from tests.research.test_gold_bar_dataset import raw_rows, STEP
    source = tmp_path/'archive-source'
    git('clone', '--quiet', attachment['bundle_path'], str(source), cwd=tmp_path)
    (source/'release-note.txt').write_text('Independent archived source fixture.\n')
    git('add', 'release-note.txt', cwd=source)
    git('commit', '--quiet', '-m', 'Archive release fixture', cwd=source)
    commit = git('rev-parse', 'HEAD', cwd=source)
    identity = dict(attachment['identity'], commit=commit)
    definition.verify_source(source, identity)
    bundle = tmp_path/'archive-source.bundle'
    git('bundle', 'create', str(bundle), 'HEAD', cwd=source)
    archived = dict(schema='GOLD_CHECKPOINT_VERIFIED_SOURCE_V1', source_root=str(source),
        bundle_path=str(bundle), bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(), identity=identity)
    args = inputs(); args['release_identity'] = identity
    trial = tmp_path/'archive'
    with monkeypatch.context() as local:
        local.setattr(experiment, 'source_identity', lambda: deepcopy(identity))
        experiment.archive(args, trial)
        review_request(trial)
    manifest = hashlib.sha256((trial/'manifest.json').read_bytes()).hexdigest()
    root = tmp_path/'inputs'; root.mkdir(mode=0o700)
    (root/'strategy-bar-trials').mkdir(mode=0o700)
    shutil.move(trial, root/'strategy-bar-trials'/manifest)
    (root/'strategy-bar-sources').mkdir(mode=0o700)
    git('clone', '--quiet', str(bundle), str(root/'strategy-bar-sources'/commit), cwd=tmp_path)
    private_tree(root)
    runtime = dict(schema='GOLD_BAR_AUTO_EPISODE_INPUT_V1', source_commit=attachment['identity']['commit'],
        trial_manifest_sha256=manifest, archive_source_commit=commit, candidate='channel', seconds=20,
        episode_id='e'*64)
    # This is the next genuine first-close observation time used by the original
    # recorded public transport, not a fabricated decision or retained result.
    entry_ms = raw_rows()[-1][6]+1+STEP+20
    return dict(root=root, retained=runtime, entry_ms=entry_ms), archived


def fixed_observation_inputs(tmp_path, attachment, monkeypatch, *, retained, start_observation=True):
    """Original FRESH/RETAINED producer formats with genuine bundled sources."""
    import asyncio
    from live.exchange import demo_network
    from live.execution.demo_batch_risk import _digest
    from tools import gold_bar_forward as forward, gold_bar_observe as observer
    from tools.gold_bar_fresh_inputs import FreshEpisode
    from tools.gold_bar_worker_inputs import load
    from tests.tools.test_gold_runtime_evidence import private_tree
    from tests.tools.test_gold_bar_observe import recorded
    from tests.research.test_gold_bar_dataset import raw_rows, STEP
    args, archived = archived_inputs(tmp_path, attachment, monkeypatch)
    root = args['root']; base = args['retained']
    trial = root/'strategy-bar-trials'/base['trial_manifest_sha256']
    source = root/'strategy-bar-sources'/base['archive_source_commit']
    public = tmp_path/'recorded-public'; public.mkdir(mode=0o700)
    end = raw_rows()[-1][6]+1
    with monkeypatch.context() as patch:
        patch.setattr(demo_network, 'proxy_url', lambda:None)
        patch.setattr(demo_network, 'tls_context', lambda:object())
        status, _ = recorded(public, patch, stop_after_close_ms=end+100, protected_network=True)
        assert status['status'] == 'OBSERVED'
        receipt_path = public/'history/receipt.json'
        receipt_path.write_text(json.dumps(json.loads(receipt_path.read_bytes()), sort_keys=True, indent=2)+'\n')
        history_hash = hashlib.sha256((public/'history/receipt.json').read_bytes()).hexdigest()
        history = public/'history'
        if not retained:
            history = root/'strategy-bar-history'/history_hash
            shutil.copytree(public/'history', history)
        runtime = dict(schema='GOLD_BAR_FRESH_EPISODE_INPUT_V1', source_commit=base['source_commit'],
            trial_manifest_sha256=base['trial_manifest_sha256'], archive_source_commit=base['archive_source_commit'],
            candidate='channel', seconds=20, history_sha256=history_hash)
        if not start_observation:
            assert not retained
            private_tree(root)
            return root, runtime, archived
        observation = (public/'observation' if retained else root/'strategy-bar-observation-work'/_digest(runtime))
        observation.parent.mkdir(mode=0o700, exist_ok=True)
        times = iter([end+100, end+STEP+21])
        patch.setattr(forward, 'now_ms', lambda:next(times))
        for module in (forward, observer):
            patch.setattr(module, 'source_identity', lambda:deepcopy(attachment['identity']))
        result = asyncio.run(forward.run(trial, candidate='channel', source_directory=source,
            history=history, output=observation, seconds=20, first_close=True, protected_network=True))
        assert result['status'] == 'FORWARD_SIGNALS_RECORDED'
    private_tree(root)
    if retained:
        identity = hashlib.sha256((observation/'result.json').read_bytes()).hexdigest()
        runtime = dict(schema='GOLD_BAR_RETAINED_EPISODE_INPUT_V1', source_commit=base['source_commit'],
            trial_manifest_sha256=base['trial_manifest_sha256'], archive_source_commit=base['archive_source_commit'],
            observation_sha256=identity, entry_decision_ms=end+STEP+20)
        shutil.copytree(observation, root/'strategy-bar-observations'/identity)
        private_tree(root)
        load(root, runtime, source_commit=base['source_commit'], uid=os.getuid())
    else:
        FreshEpisode(root, runtime, source_commit=base['source_commit'], uid=os.getuid(), read_only=True)
    return root, runtime, archived


@pytest.fixture
def native_bar_checkpoint(case, source_attachment, tmp_path, monkeypatch):
    from live.execution.demo_dispatch_store import DemoDispatchStore
    from live.execution.order_recovery import SqliteOrderRecoveryStore
    from live.execution.position_ledger import LiveLedger
    from live.execution.order_event_store import SqliteOrderEventStore
    from services.research.demo_user_stream import UserStreamJournal
    inputs, archived = archived_inputs(tmp_path, source_attachment, monkeypatch)
    work = tmp_path/'run'; work.mkdir(mode=0o700)
    names = {'dispatch.db':'dispatch.sqlite3', 'recovery.db':'recovery.sqlite3',
        'oms.db':'oms.sqlite3', 'gold-events.db':'order-events.sqlite3', 'gold-stream.db':'user-stream.sqlite3'}
    for cls in (DemoDispatchStore, SqliteOrderRecoveryStore, LiveLedger, SqliteOrderEventStore, UserStreamJournal):
        original = cls.__init__
        def mapped(self, path, *args, _original=original, **kwargs):
            path = Path(path)
            if work in path.parents and path.name in names:
                path = path.with_name(names[path.name])
            return _original(self, str(path), *args, **kwargs)
        monkeypatch.setattr(cls, '__init__', mapped)
    previous = os.umask(0o077)
    try:
        with completed_feedback(case, inputs, work, monkeypatch,
                source_commit=source_attachment['identity']['commit']) as f:
            f.source_attachment = dict(schema='GOLD_CHECKPOINT_SOURCE_SET_V1', current_commit=f.source_commit,
                attachments=sorted([source_attachment, archived], key=lambda row:row['identity']['commit']))
            yield f
    finally:
        os.umask(previous)


def append_original_query(f):
    """One real original-ID observation and idempotent native OMS projection."""
    from tools import gold_bar_feedback
    from live.execution import demo_oms_reconciliation as reconciliation
    from live.execution import demo_position_context as position
    from live.execution.demo_pre_submit import _retain, _state
    _, batches, _ = gold_bar_feedback._retained_episode(f.root, engine=f.engine,
        entry_decision_ms=f.entry_decision_ms, account=f.account,
        stores=dict(f.stores, stream=f.stream), as_of_ms=f.clock[0])
    original = f.stores['dispatch'].evidence_snapshot()
    query = next(row for row in reversed(original['wire']) if row['kind'] == 'QUERY_RESULT')
    f.stores['dispatch'].record_query_result(reservation_sha256=query['reservation_sha256'],
        client_order_id=query['client_order_id'], requested_at_ms=f.clock[0]+1,
        outcome=deepcopy(query['payload']['outcome']), now_ms=f.clock[0]+2)
    f.clock[0] += 3
    plan = reconciliation.prepare_resolution(f.exit_batch, dispatch=f.stores['dispatch'], stream=f.stream,
        start_ms=batches[-1]['start_ms'], end_ms=batches[-1]['end_ms'], source_commit=f.source_commit,
        output_directory=f.root/'receipt-reconciliation', as_of_ms=f.clock[0])
    assert plan['status'] == 'READY_FOR_OMS_PROJECTION'
    outcome = reconciliation._project_resolution(plan, prepared=f.exit_batch,
        **f.stores, event_store=f.events, now_ms=f.clock[0])
    _retain(f.root/'receipt-reconciliation/outcomes', outcome)
    f.context = position.load_context(_state(**f.stores), symbol='XAUUSDT', account_ref=f.account, as_of_ms=f.clock[0])
    return original


def observe_following_episode(f, tmp_path, monkeypatch, *, before_observation=None):
    """Original delivery/rotation then an actual next first-close observation.

    The second period has no breakout. It makes no second trading claim, does
    not reset the wallet, and preserves the issuer's first-round reservations.
    """
    from live.execution.demo_batch_risk import _digest
    from tools import gold_bar_rotation as rotation
    from tools.gold_bar_demo_cycle import prepare
    from tools.gold_bar_episode_tail import EpisodeTail
    from tools.gold_bar_pilot_capacity import original_context
    from tools.gold_pilot_retention_store import Retention
    from tools.gold_strategy_worker import original_pilot_window
    from tools.demo_executor_origin import capture_for_pilot
    from tests.tools.test_gold_feedback_retention_readers import _native_feedback_controller, receipt
    from tests.research.test_gold_bar_dataset import raw_rows, STEP
    from tests.live.gold_feedback_recovery_fixture import _auto_episode
    with _native_feedback_controller(f, tmp_path, monkeypatch) as controller:
        controller.enable_feedback_discovery()
        found = controller.discover_feedback(current_runtime=f.base_runtime)
        assert found['result_bytes'] == f.result_bytes
        controller.attach(f.result, 'delivery', receipt(f.result))
    delivered = dict(f.result, research_feedback=receipt(f.result))
    current = rotation.advance(root=f.controller_root, base=f.base_runtime, account=f.account,
        current=f.base_runtime, result=delivered)
    assert current != f.base_runtime
    f.current_runtime = current
    f.rotation_records = rotation._load(f.controller_root, f.base_runtime, f.account, read_only=True)[2]
    if before_observation is not None:
        before_observation(f)
    # The previous observed candle becomes part of genuine next warmup bytes.
    rows = raw_rows(1001)[1:]
    rows[-1][1:5] = ['100', '110', '99', '109']
    rows[-1][7] = '545'
    at = rows[-1][6]+1+STEP+21
    observed = {}
    collect = _auto_episode({}, monkeypatch, observed, bundle=current, rows=rows, context=f.context)
    engine, entry_ms, actual = collect(root=f.root,
        stores=tuple(f.stores[k] for k in ('dispatch', 'recovery', 'oms')), runtime=current, now_ms=at)
    assert actual == current and entry_ms > f.entry_decision_ms
    f.clock = observed['capture'].clock
    f.clock[0] = at
    native = original_context(f.root, f.base_runtime, uid=f.executor_uid, account=f.account)
    tail = EpisodeTail(f.root, bundle_sha256=_digest(current), engine=engine, entry_decision_ms=entry_ms)
    engine, continuation, _ = tail.snapshot()
    with Retention(f.root, original=native, base_runtime=f.base_runtime, uid=f.executor_uid, role='executor') as store:
        claim = store.claim(bundle=current, now_ms=at)
        origin = capture_for_pilot(symbol='XAUUSDT', source_commit=f.source_commit, retention=store, claim=claim)
        decision = prepare(engine=engine, entry_decision_ms=entry_ms, origin=origin, **f.stores, now_ms=at)
        assert decision['status'] == 'NO_ENTRY_SIGNAL', decision
        decision.update(source_commit=f.source_commit, bundle_sha256=_digest(current), account_ref_sha256=f.account,
            origin_sha256=_digest(origin), bar_continuation_sha256=continuation)
        store.put(claim, 'decision', decision)
        result = dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1', status='DECISION_RETAINED',
            source_commit=f.source_commit, bundle_sha256=_digest(current), decision_sha256=_digest(decision),
            decision=decision, submission_enabled=False, economic_authority=False,
            original_pilot_window=original_pilot_window(f.root, decision=decision,
                source_commit=f.source_commit, account=f.account))
        store.put(claim, 'result', result)
    with _native_feedback_controller(f, tmp_path, monkeypatch, map_custody=False) as controller:
        controller.retain(result)
    f.current_runtime = current
    f.rotation_records = rotation._load(f.controller_root, f.base_runtime, f.account, read_only=True)[2]
    f.second_episode = observed['episode']
    return result
