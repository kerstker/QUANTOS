import os
import subprocess
import sys

import pytest


def test_unbound_install_driver_cannot_run_or_create_workspace(tmp_path):
    from tools.build_research_install_bundle import DRIVER
    script=tmp_path/'install.py';script.write_text(DRIVER)
    result=subprocess.run([sys.executable,'-I',str(script)],cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode != 0
    assert list(tmp_path.iterdir()) == [script]


def test_staging_does_not_execute_source_payload_and_rejects_changes(tmp_path):
    from tools.build_research_install_bundle import copy_checked
    import hashlib
    source=tmp_path/'input.py'; source.write_text("raise RuntimeError('never execute')")
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    destination=tmp_path/'copy.py'
    copy_checked(source,destination,digest)
    assert destination.read_bytes()==source.read_bytes()
    source.write_text('changed')
    with pytest.raises(ValueError):copy_checked(source,tmp_path/'bad.py',digest)
    link=tmp_path/'link';link.symlink_to(destination)
    with pytest.raises((ValueError,OSError)):copy_checked(link,tmp_path/'linked.py',digest)


@pytest.mark.parametrize('fail', [False, True])
def test_combined_install_preserves_runtime_access_and_private_staging(tmp_path, monkeypatch, fail):
    import runpy
    import stat
    import tempfile
    from tools.build_research_install_bundle import DRIVER

    workspace = tmp_path / 'private'
    runtime = tmp_path / 'runtime'
    binding = {'payloads': {}, 'source_binding': {'artifact_commit': 'a' * 40},
               'frontend_binding': {}}
    def workspace_create(**kwargs):
        workspace.mkdir(mode=0o700)
        return str(workspace)
    def install(*args):
        runtime.mkdir()
        (runtime / 'package.py').write_text('value = 1\n')
        if fail:
            raise RuntimeError('source install failed')
    monkeypatch.setattr(os, 'getresuid', lambda: (0, 0, 0))
    monkeypatch.setattr(tempfile, 'mkdtemp', workspace_create)
    monkeypatch.setattr(runpy, 'run_path', lambda path: {'BINDING': binding['source_binding'], 'install': install})
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: None)
    script = DRIVER.replace('BINDING = None  # Bound by reviewed package builder.', 'BINDING = ' + repr(binding))
    previous = os.umask(0o077)
    try:
        if fail:
            with pytest.raises(RuntimeError, match='source install failed'):
                exec(compile(script, 'install.py', 'exec'), {'__name__': '__main__'})
        else:
            exec(compile(script, 'install.py', 'exec'), {'__name__': '__main__'})
        restored = os.umask(0o077)
        assert restored == 0o077
        assert stat.S_IMODE(workspace.stat().st_mode) == 0o700
        assert stat.S_IMODE(runtime.stat().st_mode) == 0o755
        assert stat.S_IMODE((runtime / 'package.py').stat().st_mode) == 0o644
    finally:
        os.umask(previous)
