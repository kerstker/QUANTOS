"""Actual role process, original native archive and root control lifecycle.

The root request's source/layout declaration is a fixture boundary, not an
installed fixed-public authority. Native producers, Git bundle, kernel custody,
UID admission, multipart transport and isolated root restore all execute.
"""
import array
import fcntl
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys

import pytest

from tests.tools.test_demo_gold_research_inventory import native_research as native_research
from tests.tools.test_registered_research_run import registered_case as registered_case
from tests.tools.test_demo_gold_namespace_checkpoint import source_attachment as source_attachment, private_files as private_files
from tools import demo_gold_research_handoff as api
from tools import demo_gold_research_capture_worker as worker
from tools import demo_gold_checkpoint_transport as transport
from tools.gold_pilot_retention_store import canonical


@pytest.mark.skipif(os.getuid() == 0 or 'QUANTOS_KERNEL_EXPORT' not in os.environ,
    reason='only the isolated role producer sub-process exports this fixture')
def test_export_original_native_population(native_research):
    f = native_research
    value = {key: f[key] for key in ('root', 'layout', 'public', 'cut', 'source')}
    value['root'] = str(value['root'])
    value['input_path'] = str(f['f']['directories']['one'] / 'input.json')
    Path(os.environ['QUANTOS_KERNEL_EXPORT']).write_bytes(canonical(value))


def status(channel, pid, uid, child):
    raw, ancillary, flags, _ = channel.recvmsg(worker.MAX_STATUS_BYTES,
        socket.CMSG_SPACE(struct.calcsize('3i')), socket.MSG_CMSG_CLOEXEC)
    assert not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
    if not raw:
        output, error = child.communicate(timeout=30)
        pytest.fail(f'worker exited {child.returncode}: {output}{error}')
    assert ancillary == [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, struct.pack('3i', pid, uid, uid))]
    value = json.loads(raw)
    assert canonical(value) == raw
    return value


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated Linux root/UID container')
@pytest.mark.parametrize('damage', ['stable', 'bytes'])
def test_actual_role_native_archive_stays_held_until_root_close(tmp_path, damage):
    uid = 10001
    role_base = tmp_path / 'producer'
    role_base.mkdir(mode=0o700)
    os.chown(role_base, uid, uid)
    for path in (tmp_path, *tmp_path.parents):
        if path == Path('/work') or path.is_relative_to('/work'):
            path.chmod(0o755)
    exported = role_base / 'export.json'
    producer = subprocess.run([sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
        'tests/tools/test_demo_gold_research_capture_worker_kernel.py::test_export_original_native_population',
        '--basetemp=' + str(role_base / 'pytest')], user=uid, group=uid, extra_groups=[],
        env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1', 'QUANTOS_KERNEL_EXPORT': str(exported)},
        cwd='/code', stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=60)
    assert producer.returncode == 0, producer.stdout + producer.stderr
    value = json.loads(exported.read_bytes())
    value['cut']['operation_sha256'] = api.native._digest(dict(kernel_case=str(tmp_path), damage=damage))
    root = Path(value['root'])
    pid, ticks = api.process_identity(Path('/proc') / str(os.getpid()))
    started = api.time.monotonic_ns()
    workspace = api._workspace(value['cut'], uid)
    workspace.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    workspace.parent.chmod(0o755)
    workspace.mkdir(mode=0o700)
    os.chown(workspace, uid, uid)
    document = dict(schema=api.SCHEMA, action='CAPTURE_RESEARCH', uid=uid,
        source_commit=value['layout']['source_commit'], operation_sha256=value['cut']['operation_sha256'],
        public_bundle=value['public'], cut_request=value['cut'], research_root=str(root),
        research_root_identity=api.native._identity(root.stat(), directory=True), research_layout=value['layout'],
        fixed_layout_sha256='a' * 64, source_attachment=value['source'],
        source_attachment_sha256=api.native._digest(api.native.source_attachment_identity(value['source'])),
        workspace=str(workspace), controller_pid=pid, controller_start_ticks=ticks,
        boot_id=api.boot_id(), issued_monotonic_ns=started, expires_monotonic_ns=started+api.MAX_LIFETIME_NS,
        writer_barrier_verified=False, submission_enabled=False, economic_authority=False)
    api._document(document)
    request_sha256 = api.native._digest(document)
    fd = os.memfd_create('isolated-root-request', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    os.fchmod(fd, 0o600)
    os.write(fd, canonical(document))
    fcntl.fcntl(fd, fcntl.F_ADD_SEALS, api.SEALS)
    parent, inherited = api.channel_pair()
    program = "import sys; sys.path.insert(0, '/code'); from tools.demo_gold_research_capture_worker import main; raise SystemExit(main())"
    child = subprocess.Popen([sys.executable, '-I', '-B', '-c', program,
        '--control-fd', str(inherited.fileno())], user=uid, group=uid, extra_groups=[],
        pass_fds=(inherited.fileno(),), cwd='/work',
        env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'},
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    inherited.close()
    try:
        ready, _ = api._receive_packet(parent, api.READY, with_descriptor=False)
        assert ready == (child.pid, uid, uid)
        assert parent.sendmsg([api.CAPTURE], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]))]) == len(api.CAPTURE)
        prepared = status(parent, child.pid, uid, child)
        assert prepared['phase'] == 'PREPARED' and prepared['request_sha256'] == request_sha256
        assert prepared['role_receipt']['worker_identity']['pid'] == child.pid
        # Transfer bytes into a new root-owned isolated target. Original UID in
        # the native manifest/receipt is preserved, never relabelled.
        validation = tmp_path / 'root-validation'
        validation.mkdir(mode=0o700)
        shutil.copytree(workspace / 'archive', validation / 'archive')
        restored = transport.restore_checkpoint(validation / 'archive', validation / 'restored',
            passphrase=None, expected_sha256=prepared['archive_manifest_sha256'])
        assert restored['source_uid'] == uid
        native = restored['state']['research']['components']['registered']['native']
        assert native['attempt_count'] == 6
        if damage == 'bytes':
            target = Path(value['input_path'])
            target.write_bytes(target.read_bytes() + b' ')
        parent.send(canonical(worker.control_document(request_sha256, 'RECHECK')))
        if damage == 'bytes':
            output, error = child.communicate(timeout=30)
            assert child.returncode == 2 and 'FAILED_ValueError' in error, output + error
            assert (workspace / 'archive').is_dir()
            assert parent.recv(1) == b''
        else:
            rechecked = status(parent, child.pid, uid, child)
            assert rechecked['phase'] == 'RECHECKED'
            parent.send(canonical(worker.control_document(request_sha256, 'CLOSE')))
            closed = status(parent, child.pid, uid, child)
            output, error = child.communicate(timeout=30)
            assert child.returncode == 0, output + error
            assert closed['phase'] == 'CLOSED'
            assert closed['inventory_sha256'] == prepared['inventory_sha256'] == rechecked['inventory_sha256']
            assert closed['writer_barrier_verified'] is closed['whole_set_verified'] is False
    finally:
        parent.close()
        os.close(fd)
        child.communicate(timeout=30)
