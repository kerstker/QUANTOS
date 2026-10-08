"""Real isolated files; service-manager and installed-release checks substituted."""
import json

import pytest


def test_workspace_metadata_requires_exact_reviewed_bytes(tmp_path):
    from tools import research_service_cutover as tool
    path=tmp_path/'workspaces.json';path.write_bytes(b'{}');path.chmod(0o664)
    digest=tool.sha(b'{}')
    assert tool.read(path,workspace_sha256=digest)==b'{}'
    with pytest.raises(ValueError,match='FILE_CUSTODY'):tool.read(path)
    path.write_bytes(b'{"changed":true}')
    with pytest.raises(ValueError,match='WORKSPACE_CHANGED'):tool.read(path,workspace_sha256=digest)


def test_cutover_waits_for_old_work_and_resumes_partial_files(tmp_path, monkeypatch):
    from tools import research_service_cutover as tool
    home=tmp_path/'home';units=home/'.config/systemd/user';units.mkdir(parents=True)
    delivery=tmp_path/'delivery';delivery.mkdir();operation=tmp_path/'operation'
    output=home/'quantos-dev-output';output.mkdir()
    workspace=output/'workspaces.json';workspace.write_text('{}')
    state=output/'state';state.mkdir()
    plan=dict(schema='NEXT_RESEARCH_SERVICE_DELIVERY_V1',status='PREPARED_NOT_APPLIED',
        artifact_commit='a'*40,state_root=str(state),workspace_config_path=str(workspace),
        workspace_config_sha256=tool.sha(workspace.read_bytes()),service_manager='user',
        runner_drain_guard=list(tool.GUARD_LINES),files={},prior_units=[])
    for unit in tool.UNITS:
        original=units/unit;original.write_text('[Service]\nExecStart=/usr/bin/true\n')
        plan['prior_units'].append(dict(unit=unit,preimage=dict(
            FragmentPath=[dict(path=str(original),sha256=tool.sha(original.read_bytes()))],DropInPaths=[])))
        relative=unit+'/zz-factory-release.conf';path=delivery/relative;path.parent.mkdir()
        path.write_text(tool.render(unit,plan));plan['files'][relative]=tool.sha(path.read_bytes())
    raw=json.dumps(plan).encode();(delivery/'plan.json').write_bytes(raw)
    for path in (home,delivery,*home.rglob('*'),*delivery.rglob('*')):
        path.chmod(0o700 if path.is_dir() else 0o600)
    # Existing workspace metadata uses the DGX user's normal group-write umask.
    # It remains hash-bound data and is never executed by the operator.
    workspace.chmod(0o664)
    monkeypatch.setattr(tool,'user_home',lambda:home)
    monkeypatch.setattr(tool,'verify_installation',lambda _:None)
    monkeypatch.setattr(tool,'runner_lock_free',lambda _:True)
    class Manager:
        def __init__(self):
            self.live={u:True for u in tool.UNITS};self.stopped=[];self.started=[];self.drained=False
        def reload(self):pass
        def layout(self,*a,**kw):pass
        def capture(self):return {}
        def bind(self,value):assert value=={}
        def guard_verified(self):
            assert (units/(tool.RUNNER+'.d')/tool.GUARD_NAME).read_text()==tool.GUARD
        def stop(self,unit):
            self.guard_verified();self.stopped.append(unit)
            if unit!=tool.RUNNER:self.live[unit]=False
        def drained_unit(self,unit):return not self.live[unit]
        def start(self,unit):self.started.append(unit);self.live[unit]=True
        def healthy(self,unit,commit):return unit in self.started
    manager=Manager()
    args=dict(delivery=delivery,plan_sha256=tool.sha(raw),operation=operation,manager=manager)
    tool.advance(**args)
    assert manager.stopped==[tool.RUNNER] and manager.started==[]
    assert tool.advance(**args)['status']=='WAITING_OLD_RUNNER'
    assert not any((units/(u+'.d')/'zz-factory-release.conf').exists() for u in tool.UNITS)
    manager.live[tool.RUNNER]=False
    tool.advance(**args)
    assert set(manager.stopped)==set(tool.UNITS) and manager.started==[]
    tool.advance(**args)
    write=tool.replace_known;interrupt=[True]
    def interrupted(path,body,before):
        write(path,body,before)
        if path.name=='zz-factory-release.conf' and interrupt[0]:
            interrupt[0]=False;raise OSError('synthetic crash after first installed file')
    monkeypatch.setattr(tool,'replace_known',interrupted)
    with pytest.raises(OSError):tool.advance(**args)
    assert manager.started==[]
    tool.advance(**args)
    result=tool.advance(**args)
    assert result['status']=='CUTOVER_COMPLETE' and set(manager.started)==set(tool.UNITS)
    assert tool.advance(**args)['status']=='CUTOVER_COMPLETE'
    assert len(manager.started)==3
    assert manager.started==[tool.UNITS[1],tool.RUNNER,tool.UNITS[2]]
    assert list(state.iterdir())==[] and workspace.read_text()=='{}'
    # Unknown edits are never silently overwritten during recovery.
    (units/(tool.RUNNER+'.d')/'zz-factory-release.conf').write_text('unexpected')
    with pytest.raises(ValueError,match='CHANGED'):tool.advance(**args)


def test_drain_checks_captured_cgroup_and_original_pid_after_manager_clears_them(monkeypatch):
    from tools import research_service_cutover as tool
    manager=tool.Systemd()
    manager.bind({tool.RUNNER:dict(cgroup='/user.slice/old.service',processes={'123':'old-start'})})
    monkeypatch.setattr(manager,'properties',lambda _:dict(ActiveState='inactive',MainPID='0',ControlPID='0',ControlGroup=''))
    monkeypatch.setattr(manager,'pid_token',lambda _: 'old-start')
    monkeypatch.setattr(manager,'populated',lambda _:False)
    assert not manager.drained_unit(tool.RUNNER)
    monkeypatch.setattr(manager,'pid_token',lambda _:None)
    monkeypatch.setattr(manager,'populated',lambda group:group=='/user.slice/old.service')
    assert not manager.drained_unit(tool.RUNNER)
    monkeypatch.setattr(manager,'populated',lambda _:False)
    assert manager.drained_unit(tool.RUNNER)


def test_health_rejects_redirect_without_contacting_redirect_target(monkeypatch):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    from tools import research_service_cutover as tool
    hits=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(302)
            self.send_header('Location','/unexpected')
            self.end_headers()
        def log_message(self,*args):pass
    server=HTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    original=tool.urllib.request.build_opener
    def local_opener(*handlers):
        opener=original(*handlers)
        class Local:
            def open(self,address,timeout):
                return opener.open(f'http://127.0.0.1:{server.server_port}/health',timeout=timeout)
        return Local()
    monkeypatch.setattr(tool.urllib.request,'build_opener',local_opener)
    manager=tool.Systemd();commit='a'*40;root='/opt/quantos/releases/'+commit
    monkeypatch.setattr(manager,'properties',lambda _:dict(ActiveState='active',SubState='running',
        MainPID='123',WorkingDirectory=root,NeedDaemonReload='no'))
    monkeypatch.setattr(tool.os,'readlink',lambda _:root)
    try:
        assert not manager.healthy(tool.RUNNER,commit)
        assert hits==['/health']
    finally:
        server.shutdown();thread.join();server.server_close()


def test_installation_verifier_trusts_only_exact_release_in_isolated_git_env(monkeypatch):
    from tools import research_service_cutover as tool
    seen = []
    monkeypatch.setattr(tool.subprocess, 'run', lambda *args, **kwargs: seen.append(kwargs))
    commit = 'a' * 40
    tool.verify_installation({'artifact_commit': commit, 'frontend_tree_sha256': 'b' * 64})
    env = seen[0]['env']
    assert env['GIT_CONFIG_COUNT'] == '1'
    assert env['GIT_CONFIG_KEY_0'] == 'safe.directory'
    assert env['GIT_CONFIG_VALUE_0'] == '/opt/quantos/releases/' + commit
