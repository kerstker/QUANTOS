"""Real setpriv, UID/group/capability drop, sealed IPC, native archives and exit.

The service manager, its cgroup membership and accepted-release paths are
explicit fixtures. This does not exercise host systemd or prove writer exclusion.
Original producers and native consumers remain unchanged in an offline container.
"""
import os
from pathlib import Path
import subprocess
import sys

import pytest

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
from tools import demo_gold_research_launcher as launcher
from tools import demo_supervisor as supervisor
from tools.gold_pilot_retention_store import canonical


@pytest.fixture
def manager_boundary(native_roster, tmp_path, monkeypatch):
    f = native_roster
    requests, children, snapshots, stopped = {}, {}, {}, []
    cgroup = tmp_path / 'manager-cgroup'
    cgroup.mkdir(mode=0o700)
    monkeypatch.setattr(launcher, 'CGROUP', cgroup)
    monkeypatch.setattr(launcher, 'verify_source', supervisor.verify_source)
    real_command, popen, run, metadata = launcher.command, subprocess.Popen, subprocess.run, launcher._metadata
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
from tools import demo_gold_research_capture_worker as worker
del sys.argv[1]
result = worker.main()
raise SystemExit(2 if result == 0 and boundaries['fault'] == 'bad_exit' else result)
'''
    def command(request):
        unit = launcher.unit_for(request)
        requests[unit] = request
        original = real_command(request)
        start = original.index('/usr/bin/setpriv')
        end = original.index('--', start)
        options = original[start:end + 1]
        if fault[0] == 'default_groups':
            options.remove('--clear-groups')
            options.insert(-1, '--keep-groups')
        elif fault[0] == 'bounding_caps':
            options.remove('--bounding-set=-all')
        boundaries = canonical(dict(paths=f['fixed_paths'], releases=f['releases'],
            history=f['partition_history'], fault=fault[0])).decode()
        return options + [sys.executable, '-I', '-B', '-c', program, boundaries, '--control-fd', '0']
    def start(argv, **kwargs):
        if argv[0] != '/usr/bin/setpriv':
            return popen(argv, **kwargs)
        uid = int(next(item.split('=', 1)[1] for item in argv if item.startswith('--reuid=')))
        unit = next(name for name, request in requests.items() if request.document()['uid'] == uid)
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
            Description=launcher.description(requests[unit].request_sha256))
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
    monkeypatch.setattr(launcher, 'command', command)
    monkeypatch.setattr(launcher.subprocess, 'Popen', start)
    monkeypatch.setattr(launcher.subprocess, 'run', service_run)
    monkeypatch.setattr(launcher, 'show', show)
    monkeypatch.setattr(launcher, '_metadata', read_metadata)
    yield dict(f=f, fault=fault, requests=requests, children=children, snapshots=snapshots, stopped=stopped)
    for child in children.values():
        child.wait(timeout=10)


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated root/UID container with CAP_SETPCAP')
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
def test_controlled_launcher_captures_every_uid_after_actual_privilege_drop(manager_boundary):
    b = manager_boundary
    f = b['f']
    result = launcher.capture_all(verified_layout=f['roster'], verified_public=f['public'],
        source_attachments={value['layout']['uid']: value['source'] for value in f['values']})
    assert result['research_uids'] == [10001, 10002]
    assert result['native_research_archives_verified'] is result['worker_exit_verified'] is True
    assert result['writer_barrier_verified'] is result['whole_set_verified'] is False
    assert result['submission_enabled'] is result['economic_authority'] is False
    assert set(result['worker_exits']) == {'10001', '10002'}
    assert not b['stopped']
    for unit, child in b['children'].items():
        request = b['requests'][unit]
        uid = request.document()['uid']
        exit_receipt = result['worker_exits'][str(uid)]
        assert exit_receipt['worker_identity']['pid'] == child.pid
        assert exit_receipt['worker_identity']['groups'] == []
        assert child.poll() == exit_receipt['exit_code'] == 0
        status = b['snapshots'][unit]
        assert status['Uid'].split() == status['Gid'].split() == [str(uid)] * 4
        assert status['Groups'].strip() == ''
        assert status['NoNewPrivs'].strip() == '1'
        for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
            assert int(status[key].strip(), 16) == 0


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated root/UID container with CAP_SETPCAP')
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
@pytest.mark.parametrize('fault,error', [('bad_exit', 'WORKER_EXIT_STATUS'),
    ('populated', 'WORKER_NOT_DRAINED'), ('default_groups', 'WORKER_IDENTITY'),
    ('bounding_caps', 'WORKER_PRIVILEGES')])
def test_controlled_launcher_rejects_bad_exit_or_incomplete_privilege_drop(manager_boundary, fault, error):
    b = manager_boundary
    b['fault'][0] = fault
    f = b['f']
    with pytest.raises(ValueError, match=error):
        launcher.capture_all(verified_layout=f['roster'], verified_public=f['public'],
            source_attachments={value['layout']['uid']: value['source'] for value in f['values']})
    assert b['stopped']
    assert all(child.poll() is not None for child in b['children'].values())
