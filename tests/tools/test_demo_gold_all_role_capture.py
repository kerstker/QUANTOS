"""Original sealed requests; virtual worker/lease boundaries, no host launch."""
from contextlib import contextmanager
import importlib
from types import SimpleNamespace

import pytest

from tests.tools.test_demo_gold_role_handoff import role_case as role_case
from tests.tools.test_demo_gold_research_handoff import (
    request_case as request_case, configured as configured, fixed_public as fixed_public,
    book as book, env as env,
)
from tools import demo_gold_file_leases as leases


def module():
    return importlib.import_module('tools.demo_gold_all_role_capture')


@pytest.fixture
def coordinated(role_case, monkeypatch):
    api = module()
    f = role_case
    events, requests = [], []
    faults = {'key': None, 'stage': None}
    held = object.__new__(leases.OriginalFileReadLeases)
    monkeypatch.setattr(leases.OriginalFileReadLeases, 'guarded_layout', lambda self, **kw: f['handle'])
    monkeypatch.setattr(leases.OriginalFileReadLeases, 'recheck', lambda self: events.append(('lease', None)))
    monkeypatch.setattr(leases.OriginalFileReadLeases, 'report', lambda self: dict(scope_sha256='a' * 64))
    for launcher in (api.fixed, api.research):
        issue = launcher.handoff.issue
        @contextmanager
        def recorded_issue(issue=issue, **kw):
            key = kw.get('namespace', 'research:' + str(kw.get('uid')))
            with issue(**kw) as request:
                requests.append(request)
                events.append(('issue', key))
                yield request
        monkeypatch.setattr(launcher.handoff, 'issue', recorded_issue)

    @contextmanager
    def launch(request):
        doc = request.document()
        key = doc.get('namespace', 'research:' + str(doc['uid']))
        research = key.startswith('research:')
        peer_type = api.research.peer.ResearchPeer if research else api.fixed.peer.RolePeer
        consumer = object.__new__(peer_type)
        consumer._request, consumer._status = request, None
        consumer._verified, consumer._closed = None, False
        consumer._live = lambda: request.recheck()
        def stage(name):
            events.append((name, key))
            if faults == {'key': key, 'stage': name}:
                raise ValueError('INJECTED_' + name.upper())
        def prepare():
            stage('prepare')
            consumer._status = dict(phase='PREPARED', request_sha256=request.request_sha256)
            return dict(consumer._status)
        def verify():
            stage('verify')
            consumer._verified = dict(namespace='research' if research else key,
                source_uid=doc['uid'], source_commit=doc['source_commit'])
            return dict(consumer._verified)
        def recheck():
            stage('recheck')
            consumer._status['phase'] = 'RECHECKED'
        def close():
            stage('close')
            consumer._status['phase'] = 'CLOSED'
            consumer._closed = True
            return dict(consumer._status)
        def finish():
            stage('finish')
            return dict(worker_exit_verified=True, uid=doc['uid'])
        def exit_recheck():
            stage('exit_recheck')
            return dict(worker_exit_verified=True)
        consumer.prepare, consumer.verify_archive = prepare, verify
        consumer.recheck, consumer.close_capture = recheck, close
        events.append(('launch', key))
        try:
            yield SimpleNamespace(request=request, peer=consumer, finish=finish, recheck_exit=exit_recheck)
        finally:
            events.append(('cleanup', key))
    monkeypatch.setattr(api.fixed, 'launch', launch)
    monkeypatch.setattr(api.research, 'launch', launch)
    @contextmanager
    def joined(**kw):
        assert set(kw['peers']) == {'executor', 'issuer', 'controller'}
        assert all(consumer._verified is not None for consumer in kw['peers'].values())
        assert set(kw['research_peers']) == set(f['handle']._public['registry']['research_uids'])
        assert all(consumer._verified is not None for consumer in kw['research_peers'].values())
        events.append(('join', None))
        if faults['stage'] == 'join':
            raise ValueError('INJECTED_JOIN')
        def recheck():
            events.append(('join_recheck', None))
            if faults['stage'] == 'join_recheck':
                raise ValueError('INJECTED_JOIN_RECHECK')
        handle = SimpleNamespace(recheck=recheck, report=lambda: dict(schema='TEST_JOIN_VIEW'))
        try:
            yield handle
        finally:
            events.append(('join_cleanup', None))
    # Only this test's worker/archive boundary is virtual. Kernel tests consume
    # original native restorations and the production join implementation.
    monkeypatch.setattr(api, 'joins', SimpleNamespace(open_join=joined), raising=False)
    uid = f['uid']
    sources = {name: f['source'] for name in ('executor', 'issuer', 'controller')}
    sources['research'] = {uid: f['source']}
    return dict(api=api, f=f, events=events, held=held, sources=sources, faults=faults, requests=requests)


def capture(f, **changes):
    args = dict(verified_layout=f['f']['handle'], verified_public=f['f']['configured']['handle'],
        source_attachments=f['sources'], file_leases=f['held'])
    args.update(changes)
    return f['api'].capture_all(**args)


def test_every_original_request_and_archive_precede_first_close(coordinated):
    f = coordinated
    result = capture(f)
    events = f['events']
    first_launch = next(i for i, row in enumerate(events) if row[0] == 'launch')
    assert sum(row[0] == 'issue' for row in events[:first_launch]) == 4
    first_close = next(i for i, row in enumerate(events) if row[0] == 'close')
    assert sum(row[0] == 'verify' for row in events[:first_close]) == 4
    assert sum(row[0] == 'recheck' for row in events[:first_close]) == 4
    assert sum(row[0] == 'join' for row in events[:first_close]) == 1
    assert any(row[0] == 'join_recheck' for row in events[:first_close])
    assert result['restored_role_joins'] == dict(schema='TEST_JOIN_VIEW')
    assert result['roles'] == ['executor', 'issuer', 'controller', 'research']
    assert result['research_uids'] == [f['f']['uid']]
    assert result['native_four_role_archives_verified'] is result['worker_exit_verified'] is True
    assert result['single_public_cut_bound'] is result['original_file_leases_verified'] is True
    assert result['writer_barrier_verified'] is result['whole_set_verified'] is False
    assert result['cross_role_business_join_verified'] is result['cross_namespace_atomic'] is False
    assert result['economic_authority'] is result['submission_enabled'] is False
    assert sum(row[0] == 'cleanup' for row in events) == 4
    assert all(request._closed for request in f['requests'])


@pytest.mark.parametrize('stage', ['join', 'join_recheck'])
def test_cross_role_contradiction_prevents_every_close_and_cleans_all_workers(coordinated, stage):
    f = coordinated
    f['faults']['stage'] = stage
    with pytest.raises(ValueError, match='INJECTED_JOIN'):
        capture(f)
    assert not any(kind == 'close' for kind, _ in f['events'])
    assert sum(kind == 'cleanup' for kind, _ in f['events']) == 4


@pytest.mark.parametrize('missing', ['executor', 'issuer', 'controller', 'research', 'research_uid'])
def test_missing_role_or_uid_fails_before_any_request_or_worker(coordinated, missing):
    f = coordinated
    if missing == 'research_uid':
        f['sources']['research'] = {}
    else:
        del f['sources'][missing]
    with pytest.raises(ValueError, match='COMPLETE'):
        capture(f)
    assert not any(kind in ('issue', 'launch') for kind, _ in f['events'])


@pytest.mark.parametrize('replacement', [True, {}, None])
def test_claims_cannot_replace_held_original_file_leases(coordinated, replacement):
    f = coordinated
    with pytest.raises(ValueError, match='LIVE_FILE_LEASES_REQUIRED'):
        capture(f, file_leases=replacement)
    assert not any(kind in ('issue', 'launch') for kind, _ in f['events'])


@pytest.mark.parametrize('uid', [True, 999999])
def test_unknown_or_boolean_research_uid_rejected_before_issue(coordinated, uid):
    f = coordinated
    f['sources']['research'][uid] = f['f']['source']
    with pytest.raises(ValueError, match='COMPLETE_RESEARCH_ROSTER'):
        capture(f)
    assert not any(kind in ('issue', 'launch') for kind, _ in f['events'])


@pytest.mark.parametrize('stage', ['verify', 'recheck'])
def test_last_research_failure_prevents_every_role_close(coordinated, stage):
    f = coordinated
    f['faults'].update(key='research:' + str(f['f']['uid']), stage=stage)
    with pytest.raises(ValueError, match='INJECTED'):
        capture(f)
    assert not any(kind == 'close' for kind, _ in f['events'])
    assert sum(kind == 'cleanup' for kind, _ in f['events']) == 4


@pytest.mark.parametrize('stage', ['close', 'finish', 'exit_recheck'])
def test_late_failure_never_returns_completed_set_and_cleans_all_workers(coordinated, stage):
    f = coordinated
    f['faults'].update(key='research:' + str(f['f']['uid']), stage=stage)
    with pytest.raises(ValueError, match='INJECTED'):
        capture(f)
    assert sum(kind == 'cleanup' for kind, _ in f['events']) == 4
