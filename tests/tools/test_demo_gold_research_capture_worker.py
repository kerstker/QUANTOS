"""Original registered population and bundle through the held worker lifecycle.

Root admission/control and spool installation are fixture boundaries here;
actual UID, SCM_RIGHTS and pidfd admission have separate kernel checks.
"""
from copy import deepcopy
import fcntl
import importlib
import os
from pathlib import Path

import pytest

from tests.tools.test_demo_gold_research_inventory import native_research as native_research
from tests.tools.test_registered_research_run import registered_case as registered_case
from tests.tools.test_demo_gold_namespace_checkpoint import source_attachment as source_attachment, private_files as private_files
from tests.tools.test_demo_gold_research_handoff import virtual_root_fd
from tools import demo_gold_research_handoff as handoff
from tools import demo_gold_checkpoint_transport as transport
from tools.gold_pilot_retention_store import canonical


@pytest.fixture
def worker_case(native_research, monkeypatch):
    f = native_research
    module = importlib.import_module('tools.demo_gold_research_capture_worker')
    root = f['root']
    info = root.stat()
    source = deepcopy(f['source'])
    pid, ticks = handoff.process_identity(Path('/proc') / str(os.getpid()))
    started = handoff.time.monotonic_ns()
    document = dict(schema=handoff.SCHEMA, action='CAPTURE_RESEARCH', uid=f['layout']['uid'],
        source_commit=f['layout']['source_commit'], operation_sha256=f['cut']['operation_sha256'],
        public_bundle=f['public'], cut_request=f['cut'], research_root=str(root),
        research_root_identity=handoff.native._identity(info, directory=True),
        research_layout=f['layout'], fixed_layout_sha256='a' * 64, source_attachment=source,
        source_attachment_sha256=handoff.native._digest(handoff.native.source_attachment_identity(source)),
        workspace=str(f['output']), controller_pid=pid, controller_start_ticks=ticks,
        boot_id=handoff.boot_id(), issued_monotonic_ns=started, expires_monotonic_ns=started+handoff.MAX_LIFETIME_NS,
        writer_barrier_verified=False, submission_enabled=False, economic_authority=False)
    descriptor = os.memfd_create('native-worker-unit', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    os.fchmod(descriptor, 0o600)
    os.write(descriptor, canonical(document))
    fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, handoff.SEALS)
    parent = os.pidfd_open(pid)
    virtual_root_fd(handoff, monkeypatch, descriptor)
    lease = handoff.ResearchLease(fd=descriptor, value=document, raw=canonical(document), controller_pidfd=parent)
    monkeypatch.setattr(module, '_workspace', lambda value: f['output'])
    monkeypatch.setattr(module, '_control', lambda channel, held, action: held.check())
    events = []
    monkeypatch.setattr(module, '_send_status', lambda channel, value: events.append(value))
    try:
        yield dict(f=f, api=module, lease=lease, events=events)
    finally:
        os.close(parent)
        os.close(descriptor)


def test_worker_keeps_original_read_set_through_final_root_ack(worker_case):
    f = worker_case
    result = f['api']._capture(f['lease'], None)
    assert [row['phase'] for row in f['events']] == ['PREPARED', 'RECHECKED', 'CLOSED']
    assert result == f['events'][-1]
    assert len({row['inventory_sha256'] for row in f['events']}) == 1
    assert all(row['request_sha256'] == f['lease'].request_sha256 for row in f['events'])
    assert all(row['role_receipt']['stable'] is True for row in f['events'])
    assert all(row['writer_barrier_verified'] is row['whole_set_verified']
        is row['submission_enabled'] is row['economic_authority'] is False for row in f['events'])
    restored = f['f']['output'] / 'independent-restore'
    checked = transport.restore_checkpoint(f['f']['output'] / 'archive', restored,
        passphrase=None, expected_sha256=result['archive_manifest_sha256'])
    native = checked['state']['research']['components']['registered']['native']
    assert native['attempt_count'] == 6
    assert set(native['runs']) == {row['invocation_id'] for row in f['f']['plans']}


@pytest.mark.parametrize('damage', ['bytes', 'inode', 'new_member'])
def test_prepared_archive_cannot_hide_changes_while_waiting_for_root(worker_case, monkeypatch, damage):
    f = worker_case
    target = f['f']['f']['directories']['one'] / 'input.json'

    def prepared(channel, value):
        f['events'].append(value)
        if value['phase'] == 'PREPARED':
            if damage == 'bytes':
                target.write_bytes(target.read_bytes() + b' ')
            elif damage == 'inode':
                replacement = target.with_name('replacement.tmp')
                replacement.write_bytes(target.read_bytes())
                os.replace(replacement, target)
            else:
                (target.parent / 'later.json').write_bytes(b'{}\n')

    monkeypatch.setattr(f['api'], '_send_status', prepared)
    with pytest.raises(ValueError):
        f['api']._capture(f['lease'], None)
    assert [row['phase'] for row in f['events']] == ['PREPARED']
    assert (f['f']['output'] / 'archive').is_dir()  # Keep the failed attempt's bytes.
