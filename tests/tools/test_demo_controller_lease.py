"""Maintenance excludes cooperative controllers without changing their records."""
import pytest
from tools import demo_supervisor as supervisor, demo_task, demo_cycle_checkpoint


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, 'STATE', tmp_path)
    monkeypatch.setattr(supervisor, '_root_private_directory', lambda p: None)
    return tmp_path


def test_active_controllers_block_maintenance_and_release_on_failure(state):
    with pytest.raises(RuntimeError):
        with supervisor.controller_lease():
            with supervisor.controller_lease():
                with pytest.raises(ValueError, match='CONTROLLER_MAINTENANCE_BUSY'):
                    with supervisor.controller_lease(maintenance=True):
                        pytest.fail('maintenance entered')
            raise RuntimeError('fixture')
    with supervisor.controller_lease(maintenance=True):
        with pytest.raises(ValueError, match='CONTROLLER_MAINTENANCE_BUSY'):
            with supervisor.controller_lease():
                pytest.fail('new controller entered')
    assert list(state.iterdir()) == []


def test_maintenance_blocks_task_and_supervisor_before_claim_or_child(state, monkeypatch):
    monkeypatch.setattr(supervisor, 'configuration', lambda _: pytest.fail('configuration entered'))
    monkeypatch.setattr(supervisor.subprocess, 'Popen', lambda *a, **kw: pytest.fail('child launched'))
    with supervisor.controller_lease(maintenance=True):
        result = supervisor.run(source_commit='a'*40, request_sha256='b'*64, purpose='submission')
        task = demo_task.run(source_commit='a'*40, task_sha256='b'*64)
    assert result['claim_retained'] is False
    assert task['task_claim_retained'] is False
    assert list(state.iterdir()) == []


def test_maintenance_blocks_existing_cycle_directory_lock(state):
    area = state/'cycle'; area.mkdir()
    with supervisor.controller_lease(maintenance=True):
        with pytest.raises(ValueError, match='CONTROLLER_MAINTENANCE_BUSY'):
            with demo_cycle_checkpoint.cycle_lock(area):
                pytest.fail('cycle entered')
    with demo_cycle_checkpoint.cycle_lock(area):
        pass


def test_exclusive_maintenance_excludes_an_independent_process(state):
    import subprocess
    import sys
    code = """import fcntl,os,sys
fd=os.open(sys.argv[1],os.O_RDONLY|os.O_DIRECTORY)
try:
    fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(7)
finally:
    os.close(fd)
"""
    def attempt():
        return subprocess.run([sys.executable, '-I', '-c', code, str(state)],
                              capture_output=True, timeout=5).returncode
    with supervisor.controller_lease(maintenance=True):
        assert attempt() == 7
    assert attempt() == 0


def test_maintenance_blocks_portfolio_before_configuration_or_publication(state, monkeypatch):
    from tools import demo_portfolio_controller as controller
    monkeypatch.setattr(controller, 'configuration', lambda _: pytest.fail('configuration entered'))
    monkeypatch.setattr(controller, 'publish_status', lambda *a, **kw: pytest.fail('status published'))
    with supervisor.controller_lease(maintenance=True):
        result = controller.run(source_commit='a'*40)
    assert result['status'] == 'INPUTS_UNRESOLVED'
    assert list(state.iterdir()) == []
