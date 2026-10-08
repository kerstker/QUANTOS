"""Root peer protocol binds packets to the actual original roster request.

The packet-only fixtures carry no native/archive verification authority.
"""
from copy import deepcopy
import importlib
import array
import os
import socket
from pathlib import Path

import pytest

from tests.tools.test_demo_gold_research_handoff import request_case as request_case, issue
from tests.tools.test_demo_gold_research_layout import configured as configured
from tests.tools.test_demo_gold_checkpoint_public import fixed_public as fixed_public
from tests.tools.test_demo_market_partition_install import book as book, env as env


def packet(request, identity, *, phase='PREPARED'):
    value = request.document()
    receipt = dict(schema='GOLD_CHECKPOINT_ROLE_RECEIPT_V1', namespace='research',
        operation_sha256=value['operation_sha256'], cut_request_sha256=request_case_digest(value['cut_request']),
        source_commit=value['source_commit'], registry_sha256=request_case_digest(value['public_bundle']['registry']),
        inventory_sha256='1' * 64, semantic_view_sha256='2' * 64,
        source_attachment_sha256=value['source_attachment_sha256'], before_sha256='3' * 64, after_sha256='3' * 64,
        started_at_ms=value['cut_request']['opening_at_ms'], completed_at_ms=value['cut_request']['opening_at_ms']+1,
        stable=True, worker_identity=deepcopy(identity), submission_enabled=False, economic_authority=False)
    return dict(schema='GOLD_RESEARCH_CAPTURE_STATUS_V1', phase=phase, request_sha256=request.request_sha256,
        namespace='research', uid=value['uid'], archive='archive', archive_manifest_sha256='4' * 64,
        inventory_sha256='1' * 64, role_receipt=receipt, writer_barrier_verified=False,
        whole_set_verified=False, submission_enabled=False, economic_authority=False)


def request_case_digest(value):
    from tools.demo_gold_namespace_checkpoint import _digest
    return _digest(value)


@pytest.mark.parametrize('damage', ['phase', 'request', 'worker', 'uid', 'cut', 'changed_archive', 'authority', 'extra'])
def test_root_peer_rejects_foreign_or_reordered_capture_status(request_case, damage):
    api = importlib.import_module('tools.demo_gold_research_capture_peer')
    f = request_case
    pid, ticks = f['api'].process_identity(Path('/proc') / str(os.getpid()))
    worker = dict(uid=f['uid'], gid=f['uid'], groups=[], pid=pid, start_ticks=ticks)
    with issue(f) as request:
        prepared = packet(request, worker)
        value = packet(request, worker, phase='RECHECKED')
        if damage == 'phase':
            value['phase'] = 'CLOSED'
        elif damage == 'request':
            value['request_sha256'] = 'f' * 64
        elif damage == 'worker':
            value['role_receipt']['worker_identity']['start_ticks'] += 1
        elif damage == 'uid':
            value['uid'] += 1
        elif damage == 'cut':
            value['role_receipt']['cut_request_sha256'] = 'f' * 64
        elif damage == 'changed_archive':
            value['archive_manifest_sha256'] = 'f' * 64
        elif damage == 'authority':
            value['writer_barrier_verified'] = True
        else:
            value['caller_verified'] = True
        with pytest.raises(ValueError):
            api._status(value, request=request, worker=worker, expected_phase='RECHECKED', previous=prepared)


def test_root_peer_preserves_original_uid_cut_and_archive_through_all_phases(request_case):
    api = importlib.import_module('tools.demo_gold_research_capture_peer')
    f = request_case
    pid, ticks = f['api'].process_identity(Path('/proc') / str(os.getpid()))
    worker = dict(uid=f['uid'], gid=f['uid'], groups=[], pid=pid, start_ticks=ticks)
    with issue(f) as request:
        previous = None
        for phase in ('PREPARED', 'RECHECKED', 'CLOSED'):
            value = packet(request, worker, phase=phase)
            assert api._status(value, request=request, worker=worker, expected_phase=phase, previous=previous) == value
            previous = value


@pytest.mark.parametrize('damage', ['overflow', 'descriptor', 'empty'])
def test_bounded_status_channel_rejects_bad_packets_without_leaking_fds(damage):
    from tools import demo_gold_research_handoff as api
    before = set(os.listdir('/proc/self/fd'))
    left, right = api.channel_pair()
    try:
        body = b'12345' if damage == 'overflow' else (b'' if damage == 'empty' else b'1234')
        ancillary = []
        if damage == 'descriptor':
            fd = os.memfd_create('unwanted-status-fd', os.MFD_CLOEXEC)
            ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]))]
        try:
            left.sendmsg([body], ancillary)
            with pytest.raises(ValueError):
                api.receive_body(right, maximum=4, timeout=1)
        finally:
            if damage == 'descriptor':
                os.close(fd)
    finally:
        left.close()
        right.close()
    assert set(os.listdir('/proc/self/fd')) == before


def test_bounded_status_channel_preserves_kernel_credentials_and_explicit_wait():
    from tools import demo_gold_research_handoff as api
    left, right = api.channel_pair()
    try:
        left.send(b'1234')
        raw, credentials = api.receive_body(right, maximum=4, timeout=60)
        assert raw == b'1234'
        assert credentials == (os.getpid(), os.getuid(), os.getgid())
        assert right.gettimeout() == 60
        for timeout in (0, -1, True, float('inf'), 1801):
            with pytest.raises(ValueError):
                api.receive_body(right, maximum=4, timeout=timeout)
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize('configured', ['multiple'], indirect=True)
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
def test_research_set_cannot_replace_complete_roster_with_empty_or_caller_selected_uids(request_case, monkeypatch):
    api = importlib.import_module('tools.demo_gold_research_capture_peer')
    f = request_case
    capture = api.ResearchCaptureSet(verified_layout=f['handle'], verified_public=f['configured']['handle'])
    assert capture.uids == tuple(sorted(f['configured']['public']['registry']['research_uids']))
    assert len(capture.uids) == 2
    monkeypatch.setattr(api.transport, 'restore_checkpoint', lambda *a, **k: pytest.fail('incomplete roster before IO'))
    with pytest.raises(ValueError, match='COMPLETE_RESEARCH_ROSTER'):
        capture.verify_archives()
    with pytest.raises(ValueError):
        api.ResearchCaptureSet(verified_layout=f['handle'].snapshot(), verified_public=f['configured']['handle'])
