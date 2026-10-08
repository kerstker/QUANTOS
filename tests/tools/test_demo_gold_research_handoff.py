"""Root selection from real fixed-roster reads; virtual OS root in unit cases.

Actual kernel root/role handoff is exercised separately in the offline container.
These source declarations describe a capture attempt, not a verified bundle.
"""
from copy import deepcopy
import array
import fcntl
import importlib
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.tools.test_demo_gold_research_layout import configured as configured, capture
from tests.tools.test_demo_gold_checkpoint_public import fixed_public as fixed_public
from tests.tools.test_demo_market_partition_install import book as book, env as env
from tools.gold_pilot_retention_store import canonical


def module():
    return importlib.import_module('tools.demo_gold_research_handoff')


@pytest.fixture
def request_case(configured, tmp_path, monkeypatch):
    f = configured
    api = module()
    monkeypatch.setattr(os, 'getresuid', lambda: (0, 0, 0))
    monkeypatch.setattr(os, 'getresgid', lambda: (0, 0, 0))
    monkeypatch.setattr(api, '_root_uids', lambda path: (0, 0, 0, 0))
    handle = capture(f)
    source = dict(schema='GOLD_CHECKPOINT_VERIFIED_SOURCE_V1',
        source_root=str(tmp_path / 'declared-source'), bundle_path=str(tmp_path / 'declared.bundle'),
        bundle_sha256='b' * 64, identity=dict(commit=f['public']['source_commit'],
            python_source_sha256='c' * 64, dependency_lock_sha256='d' * 64,
            python_version='3.12.3', architecture='aarch64'))
    monkeypatch.setattr(api, 'OUTPUT_ROOT', tmp_path / 'role-spool')
    return dict(api=api, configured=f, handle=handle, source=source, uid=f['roots'][0]['layout']['uid'])


def issue(f, **changes):
    arguments = dict(verified_layout=f['handle'], verified_public=f['configured']['handle'],
        uid=f['uid'], source_attachment=f['source'])
    arguments.update(changes)
    return f['api'].issue(**arguments)


def virtual_root_fd(api, monkeypatch, fd):
    original = os.fstat
    def metadata(number):
        info = original(number)
        if number != fd:
            return info
        fields = {key: getattr(info, key) for key in api.native.IDENTITY}
        fields.update(st_uid=0, st_gid=0)
        return SimpleNamespace(**fields)
    monkeypatch.setattr(api.os, 'fstat', metadata)


def test_root_request_is_sealed_and_selects_captured_originals(request_case, monkeypatch):
    f = request_case
    with issue(f) as request:
        value = request.document()
        assert value['schema'] == 'GOLD_RESEARCH_CAPTURE_REQUEST_V1'
        assert value['action'] == 'CAPTURE_RESEARCH'
        assert value['uid'] == f['uid']
        assert value['research_root'] == str(f['handle'].root_for_uid(f['uid']))
        assert value['research_layout'] == f['handle'].layout_for_uid(f['uid'])
        assert value['fixed_layout_sha256'] == f['api'].native._digest(f['handle'].snapshot())
        assert value['source_attachment_sha256'] == f['api'].native._digest(
            f['api'].native.source_attachment_identity(f['source']))
        assert value['writer_barrier_verified'] is value['submission_enabled'] is value['economic_authority'] is False
        assert os.pread(request.fd, f['api'].MAX_REQUEST_BYTES, 0) == canonical(value)
        assert fcntl.fcntl(request.fd, fcntl.F_GET_SEALS) & f['api'].SEALS == f['api'].SEALS
        with monkeypatch.context() as role:
            virtual_root_fd(f['api'], role, request.fd)
            assert f['api'].read_descriptor(request.fd)[0] == value
        value['uid'] = 999999
        assert request.document()['uid'] == f['uid']
    with pytest.raises(ValueError, match='CLOSED'):
        request.recheck()


@pytest.mark.parametrize('damage', ['dictionary', 'unknown_uid', 'boolean_uid', 'source', 'secret_source'])
def test_root_request_rejects_unbound_selection_before_sealing(request_case, monkeypatch, damage):
    f = request_case
    arguments = {}
    if damage == 'dictionary':
        arguments['verified_layout'] = f['handle'].snapshot()
    elif damage == 'unknown_uid':
        arguments['uid'] = 999999
    elif damage == 'boolean_uid':
        arguments['uid'] = True
    else:
        source = deepcopy(f['source'])
        if damage == 'source':
            source['identity']['commit'] = 'f' * 40
        else:
            source['bundle_path'] = str(Path(source['bundle_path']).parent / 'key' / 'secret.bundle')
        arguments['source_attachment'] = source
    monkeypatch.setattr(f['api'].os, 'memfd_create', lambda *a, **k: pytest.fail('must reject before sealing'))
    with pytest.raises(ValueError):
        with issue(f, **arguments):
            pytest.fail('invalid root selection was accepted')


def test_root_cannot_reuse_roster_after_same_byte_config_replacement(request_case):
    f = request_case
    with pytest.raises(ValueError, match='CHANGED'):
        with issue(f) as request:
            config = f['configured']['config']
            body = config.read_bytes()
            replacement = config.with_name('changed-inode.json')
            replacement.write_bytes(body)
            replacement.chmod(0o644)
            os.replace(replacement, config)
            request.recheck()


@pytest.mark.parametrize('configured', ['multiple'], indirect=True)
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
def test_root_selects_every_original_uid_without_first_uid_fallback(request_case):
    f = request_case
    uids = [row['layout']['uid'] for row in f['configured']['roots']]
    assert len(uids) == 2
    for uid in uids:
        with issue(f, uid=uid) as request:
            assert request.document()['uid'] == uid
            assert request.document()['research_root'] == str(f['handle'].root_for_uid(uid))


def test_research_owned_sealed_descriptor_cannot_claim_root(request_case):
    f = request_case
    with issue(f) as request:
        assert os.fstat(request.fd).st_uid == os.getuid() != 0
        with pytest.raises(ValueError, match='CUSTODY'):
            f['api'].read_descriptor(request.fd)


def test_unsealed_root_descriptor_is_rejected_before_body(request_case, monkeypatch):
    f = request_case
    fd = os.memfd_create('unsealed-test', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, b'{}')
        virtual_root_fd(f['api'], monkeypatch, fd)
        with pytest.raises(ValueError, match='UNSEALED'):
            f['api'].read_descriptor(fd)
    finally:
        os.close(fd)


def test_selected_role_must_match_actual_uid_gid_and_groups(request_case, monkeypatch):
    f = request_case
    with issue(f) as request:
        with monkeypatch.context() as role:
            virtual_root_fd(f['api'], role, request.fd)
            role.setattr(os, 'getresuid', lambda: (f['uid'] + 1,) * 3)
            role.setattr(f['api'], '_controller_pidfd', lambda *a: pytest.fail('wrong role must reject first'))
            with pytest.raises(ValueError, match='ROLE_IDENTITY'):
                with f['api'].admitted(request.fd):
                    pytest.fail('foreign role was admitted')


@pytest.mark.parametrize('damage', ['extra_group', 'mixed_gid', 'expired'])
def test_role_privileges_and_request_lifetime_fail_closed(request_case, monkeypatch, damage):
    f = request_case
    with issue(f) as request:
        with monkeypatch.context() as role:
            virtual_root_fd(f['api'], role, request.fd)
            role.setattr(os, 'getresuid', lambda: (f['uid'],) * 3)
            role.setattr(os, 'getresgid', lambda: (f['uid'],) * 3)
            role.setattr(os, 'getgroups', lambda: [f['uid']])
            if damage == 'extra_group':
                role.setattr(os, 'getgroups', lambda: [f['uid'], f['uid'] + 1])
            elif damage == 'mixed_gid':
                role.setattr(os, 'getresgid', lambda: (f['uid'], 0, f['uid']))
            else:
                role.setattr(f['api'].time, 'monotonic_ns', lambda: request.document()['expires_monotonic_ns'])
            role.setattr(f['api'], '_controller_pidfd', lambda *a: pytest.fail('must reject before controller lookup'))
            with pytest.raises(ValueError, match='ROLE_IDENTITY|EXPIRED'):
                with f['api'].admitted(request.fd):
                    pytest.fail('invalid role or lifetime was admitted')


def test_lease_rejects_reused_descriptor_even_with_identical_sealed_bytes(request_case, monkeypatch):
    f = request_case
    with issue(f) as request:
        original = os.dup(request.fd)
        replacement = os.memfd_create('replacement', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        try:
            os.fchmod(replacement, 0o600)
            os.write(replacement, canonical(request.document()))
            fcntl.fcntl(replacement, fcntl.F_ADD_SEALS, f['api'].SEALS)
            with monkeypatch.context() as role:
                virtual_root_fd(f['api'], role, request.fd)
                role.setattr(os, 'getresuid', lambda: (f['uid'],) * 3)
                role.setattr(os, 'getresgid', lambda: (f['uid'],) * 3)
                role.setattr(os, 'getgroups', lambda: [f['uid']])
                role.setattr(f['api'], '_controller_pidfd', lambda document: os.pidfd_open(os.getpid()))
                role.setattr(f['api'].role, '_node', lambda *a, **kw: request.document()['research_root_identity'])
                with pytest.raises(ValueError, match='CHANGED'):
                    with f['api'].admitted(request.fd) as lease:
                        os.dup2(replacement, request.fd)
                        lease.check()
        finally:
            os.dup2(original, request.fd)
            os.close(original)
            os.close(replacement)


@pytest.mark.parametrize('damage', ['extra_descriptor', 'too_many_descriptors', 'truncated_packet', 'unexpected_descriptor'])
def test_malformed_socket_packet_closes_every_received_descriptor(request_case, damage):
    api = request_case['api']
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        right.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        with issue(request_case) as request:
            body = b'CAPTURE'
            descriptors = [request.fd, request.fd] if damage == 'extra_descriptor' else [request.fd]
            if damage == 'too_many_descriptors':
                descriptors *= 32
            if damage == 'truncated_packet':
                body += b'x' * 1024
            left.sendmsg([body], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', descriptors))])
            before = len(os.listdir('/proc/self/fd'))
            with pytest.raises(ValueError):
                api._receive_packet(right, b'CAPTURE', with_descriptor=damage != 'unexpected_descriptor')
            assert len(os.listdir('/proc/self/fd')) == before
    finally:
        left.close()
        right.close()


def test_stream_socket_cannot_carry_capture_permission(request_case):
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with pytest.raises(ValueError, match='CHANNEL'):
            request_case['api'].receive_request(right)
    finally:
        left.close()
        right.close()
