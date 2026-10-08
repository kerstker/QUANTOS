"""Hold an original research inventory until the root coordinator closes its cut.

The kernel request selects the original role/root/layout. Native research,
bundle and multipart archive checks stay in their original implementations.
The private archive is a component spool, never whole-set or trading authority.
"""
import argparse
import os
from pathlib import Path
import select
import socket
import stat
import sys

from tools import demo_gold_research_handoff as handoff
from tools import demo_gold_research_inventory as research
from tools import demo_gold_checkpoint_transport as transport
from tools.gold_pilot_retention_store import canonical

STATUS_SCHEMA = 'GOLD_RESEARCH_CAPTURE_STATUS_V1'
CONTROL_SCHEMA = 'GOLD_RESEARCH_CAPTURE_CONTROL_V1'
MAX_STATUS_BYTES = 65536
require = handoff.require


def _workspace(value):
    path = Path(value['workspace'])
    require(path == handoff._workspace(value['cut_request'], value['uid']), 'WORKSPACE')
    handoff.roster._parents(path, (value['uid'], value['uid']))
    for parent in (handoff.OUTPUT_ROOT, path.parent):
        research._node(parent, 0, directory=True)
    identity = research._node(path, value['uid'], directory=True)
    require(stat.S_IMODE(identity['mode']) == 0o700, 'WORKSPACE_CUSTODY')
    return path


def control_document(request_sha256, action):
    handoff.native._hash(request_sha256)
    require(action in ('RECHECK', 'CLOSE'), 'CONTROL_ACTION')
    return dict(schema=CONTROL_SCHEMA, request_sha256=request_sha256, action=action)


def _control(channel, lease, action):
    lease.check()
    remaining = lease.document()['expires_monotonic_ns'] - handoff.time.monotonic_ns()
    require(remaining > 0, 'EXPIRED')
    poller = select.poll()
    poller.register(channel.fileno(), select.POLLIN | select.POLLHUP | select.POLLERR)
    poller.register(lease.controller_pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
    while True:
        lease.check()
        remaining = lease.document()['expires_monotonic_ns'] - handoff.time.monotonic_ns()
        events = poller.poll(max(1, min(1000, remaining // 10**6)))
        lease.check()
        if any(fd == channel.fileno() for fd, _ in events):
            break
    expected = canonical(control_document(lease.request_sha256, action))
    credentials, _ = handoff._receive_packet(channel, expected, with_descriptor=False)
    require(credentials == (lease.document()['controller_pid'], 0, 0), 'CONTROLLER_CREDENTIALS')
    lease.check()


def _send_status(channel, value):
    raw = canonical(value)
    require(len(raw) <= MAX_STATUS_BYTES, 'STATUS_BOUND')
    require(channel.send(raw) == len(raw), 'SEND')


def _capture(lease, channel):
    require(type(lease) is handoff.ResearchLease, 'ADMITTED_LEASE_REQUIRED')
    lease.check()
    value = lease.document()
    workspace = _workspace(value)
    identity = research._node(workspace, value['uid'], directory=True)

    def guard():
        lease.check()
        current = research._node(workspace, value['uid'], directory=True)
        # Creating archive/ changes nlink; every custody and path-identity field
        # remains fixed while the original transport creates its new children.
        require(all(current[key] == identity[key] for key in ('device', 'inode', 'uid', 'gid', 'mode')),
            'WORKSPACE_CHANGED')

    def status(phase, inventory, saved):
        receipt = inventory.recheck()
        return dict(schema=STATUS_SCHEMA, phase=phase, request_sha256=lease.request_sha256,
            namespace='research', uid=value['uid'], archive='archive',
            archive_manifest_sha256=saved['sha256'], inventory_sha256=handoff.native._digest(inventory.manifest()),
            role_receipt=receipt, writer_barrier_verified=False, whole_set_verified=False,
            submission_enabled=False, economic_authority=False)

    with research.capture_inventory(root=value['research_root'], layout=value['research_layout'],
            public_bundle=value['public_bundle'], cut_request=value['cut_request'],
            source_attachment=value['source_attachment'], guard=guard) as inventory:
        require(inventory.root_identity == value['research_root_identity'], 'ROOT_CHANGED')
        saved = transport.create_checkpoint(inventory, workspace / 'archive', passphrase=None, capture_only=True)
        _send_status(channel, status('PREPARED', inventory, saved))
        _control(channel, lease, 'RECHECK')
        _send_status(channel, status('RECHECKED', inventory, saved))
        _control(channel, lease, 'CLOSE')
        final = status('CLOSED', inventory, saved)
    guard()
    _send_status(channel, final)
    return final


def run(channel):
    fd = handoff.receive_request(channel)
    try:
        with handoff.admitted(fd) as lease:
            return _capture(lease, channel)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control-fd', type=int, required=True)
    arguments = parser.parse_args()
    # systemd-run --pipe forwards the control socket as stdin. Descriptor zero
    # still passes the original AF_UNIX/SEQPACKET, SCM credential and sealed
    # root-request checks; stdout/stderr are never control channels.
    require(arguments.control_fd == 0 or arguments.control_fd >= 3, 'CHANNEL_DESCRIPTOR')
    with socket.socket(fileno=arguments.control_fd) as channel:
        try:
            run(channel)
        except Exception as error:
            print('GOLD_RESEARCH_CAPTURE_FAILED_' + type(error).__name__, file=sys.stderr)
            return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
