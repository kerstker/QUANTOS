"""Real setpriv, UID/group/capability drop, sealed IPC, native archives and exit.

The service manager, its cgroup membership and accepted-release paths are
explicit fixtures. This does not exercise host systemd or prove writer exclusion.
All three original fixed-role producers and native consumers run in an offline
container. Empty native histories do not establish a cross-role business join.
"""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.tools.test_demo_gold_role_capture_kernel import role_roster as role_roster
from tests.tools.test_demo_gold_research_capture_peer_kernel import (
    native_roster as native_roster,
    source_attachment as source_attachment,
    registered_case as registered_case,
    native_research as native_research,
    private_files as private_files,
    fixed_public as fixed_public,
    book as book,
    env as env,
)
from tools import demo_gold_role_launcher as launcher
from tools import demo_gold_research_launcher as research
from tools import demo_gold_all_role_capture as coordinator
from tools import demo_gold_file_leases as leases
from tests.tools.test_demo_gold_role_capture_kernel import restricted
from tools import demo_supervisor as supervisor
from tools.gold_pilot_retention_store import canonical


@pytest.fixture
def manager_boundary(role_roster, tmp_path, monkeypatch):
    f = role_roster
    requests, children, snapshots, stopped = {}, {}, {}, []
    cgroup = tmp_path / 'manager-cgroup'
    cgroup.mkdir(mode=0o700)
    monkeypatch.setattr(launcher, 'CGROUP', cgroup)
    monkeypatch.setattr(launcher, 'verify_source', supervisor.verify_source)
    real_command, research_command = launcher.command, research.command
    popen, run, metadata = subprocess.Popen, subprocess.run, launcher._metadata
    fault = [None]
    program = '''
import json, sys
from pathlib import Path
sys.path.insert(0, '/code')
from tools import demo_gold_checkpoint_public as fixed
boundaries = json.loads(sys.argv[1])
fixed.PATHS = {key: Path(path) for key, path in boundaries['paths'].items()}
fixed.RELEASES = Path(boundaries['releases'])
fixed.PARTITION_HISTORY = Path(boundaries['history'])
from tools import demo_gold_namespace_checkpoint as native
native.ROLE_ROOTS = {key: Path(path) for key, path in boundaries['roots'].items()}
if boundaries['namespace'] == 'research':
 from tools import demo_gold_research_capture_worker as worker
else:
 from tools import demo_gold_role_capture_worker as worker
del sys.argv[1]
result = worker.main()
raise SystemExit(2 if result == 0 and boundaries['fault'] == 'bad_exit' else result)
'''
    def command(request):
        selected = research if type(request) is research.handoff.ResearchRequest else launcher
        unit = selected.unit_for(request)
        requests[unit] = request
        original = (research_command if selected is research else real_command)(request)
        start = original.index('/usr/bin/setpriv')
        end = original.index('--', start)
        options = original[start:end + 1]
        if fault[0] == 'default_groups':
            options.remove('--clear-groups')
            options.insert(-1, '--keep-groups')
        elif fault[0] == 'bounding_caps':
            options.remove('--bounding-set=-all')
        boundaries = canonical(dict(paths=f['fixed_paths'], releases=f['releases'],
            history=f['partition_history'], roots={key: str(path) for key, path in f['roots'].items()},
            namespace=request.document().get('namespace', 'research'), fault=fault[0])).decode()
        return options + [sys.executable, '-I', '-B', '-c', program, boundaries, '--control-fd', '0']
    def start(argv, **kwargs):
        if argv[0] != '/usr/bin/setpriv':
            return popen(argv, **kwargs)
        uid = int(next(item.split('=', 1)[1] for item in argv if item.startswith('--reuid=')))
        namespace = coordinator.native.decode(argv[-3])['namespace']
        unit = next(name for name, request in requests.items() if request.document()['uid'] == uid
            and request.document().get('namespace', 'research') == namespace)
        # Inherit a deliberately unrelated group to prove setpriv clears it.
        child = popen(argv, **dict(kwargs, extra_groups=[uid, 9999]))
        children[unit] = child
        directory = cgroup / 'system.slice' / unit
        directory.mkdir(mode=0o700, parents=True)
        (directory / 'cgroup.events').write_text('populated 1\n')
        return child
    def show(unit):
        child = children.get(unit)
        if child is None:
            return dict(LoadState='not-found', ActiveState='inactive', SubState='dead', MainPID='0',
                ExecMainPID='0', ExecMainCode='0', ExecMainStatus='0', ControlGroup='', Description=unit)
        exited = child.poll() is not None
        (cgroup / 'system.slice' / unit / 'cgroup.events').write_text(
            'populated 1\n' if not exited or fault[0] == 'populated' else 'populated 0\n')
        return dict(LoadState='loaded', ActiveState='inactive' if exited else 'active',
            SubState='dead' if exited else 'running', MainPID='0' if exited else str(child.pid),
            ExecMainPID=str(child.pid), ExecMainCode='1' if exited else '0',
            ExecMainStatus=str(child.returncode) if exited else '0', ControlGroup='/system.slice/' + unit,
            Description=(research if type(requests[unit]) is research.handoff.ResearchRequest else launcher)
                .description(requests[unit].request_sha256))
    def read_metadata(path):
        child_unit = next((unit for unit, child in children.items()
            if path.parent == Path('/proc') / str(child.pid)), None)
        if child_unit is not None and path.name == 'cgroup':
            return '0::/system.slice/' + child_unit + '\n'
        value = metadata(path)
        if child_unit is not None and path.name == 'status':
            snapshots[child_unit] = dict(line.split(':', 1) for line in value.splitlines() if ':' in line)
        return value
    def service_run(argv, **kwargs):
        if argv[:2] != ['/usr/bin/systemctl', 'stop']:
            return run(argv, **kwargs)
        unit = argv[2]
        assert unit in children and requests[unit].request_sha256 in show(unit)['Description']
        # launch() already closed the actual socket; the original worker exits
        # on EOF. No additional cross-UID signal capability is given to tests.
        children[unit].wait(timeout=10)
        stopped.append(unit)
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(research, 'command', command)
    monkeypatch.setattr(research, 'show', show)
    monkeypatch.setattr(research, '_metadata', read_metadata)
    monkeypatch.setattr(research, 'CGROUP', cgroup)
    monkeypatch.setattr(research, 'verify_source', supervisor.verify_source)
    monkeypatch.setattr(launcher, 'command', command)
    monkeypatch.setattr(launcher.subprocess, 'Popen', start)
    monkeypatch.setattr(launcher.subprocess, 'run', service_run)
    monkeypatch.setattr(launcher, 'show', show)
    monkeypatch.setattr(launcher, '_metadata', read_metadata)
    monkeypatch.setattr(launcher.shared, '_metadata', read_metadata)
    yield dict(f=f, fault=fault, requests=requests, children=children, snapshots=snapshots, stopped=stopped, actual_popen=popen)
    for child in children.values():
        child.wait(timeout=10)




def sources(f):
    return dict(f['sources'], research={value['layout']['uid']: value['source'] for value in f['values']})


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated root/UID container with CAP_LEASE/SETPCAP')
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
def test_every_fixed_role_and_research_uid_share_native_capture_and_actual_exit(manager_boundary):
    b = manager_boundary
    f = b['f']
    with leases.hold(verified_layout=f['roster'], verified_public=f['public']) as held:
        result = coordinator.capture_all(verified_layout=f['roster'], verified_public=f['public'],
            source_attachments=sources(f), file_leases=held)
        held.recheck()
    assert result['roles'] == ['executor', 'issuer', 'controller', 'research']
    assert result['research_uids'] == [10001, 10002]
    assert set(result['captures']['research']) == set(result['worker_exits']['research']) == {'10001', '10002'}
    assert result['native_four_role_archives_verified'] is result['single_public_cut_bound'] is True
    assert result['worker_exit_verified'] is result['original_file_leases_verified'] is True
    assert result['writer_barrier_verified'] is result['cross_role_business_join_verified'] is False
    assert result['cross_namespace_atomic'] is result['whole_set_verified'] is False
    assert result['economic_authority'] is result['submission_enabled'] is False
    assert len(b['children']) == 5 and not b['stopped']
    for unit, child in b['children'].items():
        doc = b['requests'][unit].document()
        namespace = doc.get('namespace', 'research')
        receipt = result['worker_exits'][namespace]
        if namespace == 'research':
            receipt = receipt[str(doc['uid'])]
        assert child.poll() == receipt['exit_code'] == 0
        assert receipt['worker_identity']['pid'] == child.pid
        assert receipt['worker_identity']['groups'] == []
        status = b['snapshots'][unit]
        assert status['Uid'].split() == status['Gid'].split() == [str(doc['uid'])] * 4
        assert status['Groups'].strip() == '' and status['NoNewPrivs'].strip() == '1'
        for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
            assert int(status[key].strip(), 16) == 0


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated root/UID container with CAP_LEASE/SETPCAP')
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
def test_last_research_original_change_blocks_all_role_close_and_reaps_every_worker(manager_boundary, monkeypatch):
    b = manager_boundary
    f = b['f']
    close_calls = []
    original_verify = research.peer.ResearchPeer.verify_archive
    original_close = research.peer.ResearchPeer.close_capture
    def verify(consumer):
        report = original_verify(consumer)
        if type(consumer) is research.peer.ResearchPeer and consumer.uid == 10002:
            target = str(Path(consumer._request.document()['research_root']) / 'unexpected.dat')
            program = "import os,sys;from pathlib import Path;os.umask(0o077);Path(sys.argv[1]).write_bytes(b'changed')"
            child = b['actual_popen'](restricted(consumer.uid, program, target), cwd='/code',
                env=dict(PATH='/usr/bin:/bin'), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = child.communicate(timeout=10)
            assert child.returncode == 0, stdout + stderr
        return report
    def close(consumer):
        close_calls.append(consumer.uid)
        return original_close(consumer)
    monkeypatch.setattr(research.peer.ResearchPeer, 'verify_archive', verify)
    monkeypatch.setattr(research.peer.ResearchPeer, 'close_capture', close)
    with pytest.raises(ValueError, match='CHANGED'):
        with leases.hold(verified_layout=f['roster'], verified_public=f['public']) as held:
            coordinator.capture_all(verified_layout=f['roster'], verified_public=f['public'],
                source_attachments=sources(f), file_leases=held)
    assert close_calls == []
    assert len(b['children']) == 5
    assert b['stopped'] and all(child.poll() is not None for child in b['children'].values())
