"""Resume a reviewed three-service user-manager research cutover.

Default CLI is read-only. Apply never installs releases, reads credential files,
enables trading, clears research state, force-kills workers, or downgrades data.
After new startup, failures retain configuration for forward recovery.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import tempfile
import time
import urllib.request
from services.governance.json_integrity import load_json_without_duplicate_keys as decode

RUNNER='quantos-next-runner.service'
UNITS=(RUNNER,'quantos-next-console.service','quantos-next-web.service')
GUARD_NAME='zz-runner-drain.conf'
GUARD_LINES=('TimeoutStopSec=infinity','SendSIGKILL=no','KillMode=mixed')
GUARD='[Service]\n'+'\n'.join(GUARD_LINES)+'\n'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def require(value,reason):
    if not value: raise ValueError('RESEARCH_CUTOVER_'+reason)


def sha(raw):return hashlib.sha256(raw).hexdigest()
def user_home():return Path(pwd.getpwuid(os.getuid()).pw_dir)


def read(path, *, workspace_sha256=None):
    path=Path(path).absolute()
    require(path.resolve()==path,'UNSAFE_PATH')
    info=path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid in (0,os.getuid())
            and not info.st_mode&0o002
            and (not info.st_mode&0o020 or workspace_sha256 is not None)
            and info.st_size<=2_000_000,'FILE_CUSTODY')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno());raw=stream.read(2_000_001);after=os.fstat(stream.fileno())
    require((info.st_dev,info.st_ino)==(before.st_dev,before.st_ino)
        and (path.lstat().st_dev,path.lstat().st_ino)==(before.st_dev,before.st_ino)
        and len(raw)==before.st_size and (before.st_dev,before.st_ino,before.st_mtime_ns,before.st_ctime_ns)==
        (after.st_dev,after.st_ino,after.st_mtime_ns,after.st_ctime_ns),'FILE_CHANGED')
    if workspace_sha256 is not None:require(sha(raw)==workspace_sha256,'WORKSPACE_CHANGED')
    return raw


def replace_known(path,body,before):
    path=Path(path);parent=path.parent
    require(parent.resolve()==parent.absolute(),'UNSAFE_PATH')
    parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    require(parent.stat().st_uid==os.getuid() and not parent.stat().st_mode&0o022,'DIRECTORY_CUSTODY')
    current=read(path) if path.exists() or path.is_symlink() else None
    require(current==body or (sha(current) if current is not None else None)==before,'TARGET_CHANGED')
    if current==body:return
    fd,name=tempfile.mkstemp(prefix='.quantos-cutover-',dir=parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(body);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
        directory=os.open(parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(name):os.unlink(name)


def render(unit,plan):
    commit=plan['artifact_commit']
    return _render(unit,commit,'/opt/quantos/frontends/'+commit,plan['state_root'],plan['workspace_config_path'])


def review(delivery,plan_sha256):
    home=user_home();units=home/'.config/systemd/user'
    require(home.resolve()==home and units.resolve()==units,'USER_PATH')
    require(units.stat().st_uid==os.getuid() and not units.stat().st_mode&0o022,'UNIT_DIRECTORY_CUSTODY')
    raw=read(Path(delivery)/'plan.json');require(sha(raw)==plan_sha256,'PLAN_CHANGED')
    plan=decode(raw)
    require(plan['schema']=='NEXT_RESEARCH_SERVICE_DELIVERY_V1' and plan['status']=='PREPARED_NOT_APPLIED'
        and plan['service_manager']=='user' and re.fullmatch('[a-f0-9]{40}',plan['artifact_commit']), 'PLAN_SCOPE')
    require(set(plan['runner_drain_guard'])==set(GUARD_LINES),'DRAIN_GUARD')
    for name in ('state_root','workspace_config_path'):
        path=Path(plan[name]);require(path.is_absolute() and path.is_relative_to(home/'quantos-dev-output')
            and path.resolve()==path,'STATE_PATH')
    read(plan['workspace_config_path'],workspace_sha256=plan['workspace_config_sha256'])
    require(set(plan['files'])=={u+'/zz-factory-release.conf' for u in UNITS},'FILE_SET')
    targets={};originals={}
    rows=plan['prior_units'];require(len(rows)==3 and {r['unit'] for r in rows}==set(UNITS),'UNIT_SET')
    for row in rows:
        unit=row['unit'];pre=row['preimage']
        require(len(pre['FragmentPath'])==1 and pre['FragmentPath'][0]['path']==str(units/unit),'FRAGMENT_PATH')
        for item in pre['FragmentPath']+pre['DropInPaths']:
            path=Path(item['path'])
            require(path==units/unit or path.parent==units/(unit+'.d') and path.suffix=='.conf','DROPIN_PATH')
            require(str(path) not in originals,'DUPLICATE_PREIMAGE')
            originals[str(path)]=item['sha256']
        relative=unit+'/zz-factory-release.conf';body=read(Path(delivery)/relative)
        require(sha(body)==plan['files'][relative] and body==render(unit,plan).encode(),'PAYLOAD_CHANGED')
        targets[str(units/(unit+'.d')/'zz-factory-release.conf')]=body
    targets[str(units/(RUNNER+'.d')/GUARD_NAME)]=GUARD.encode()
    # Only original or this operation's exact bytes are admissible on resume.
    for name in set(originals)|set(targets):
        path=Path(name);body=read(path) if path.exists() or path.is_symlink() else None
        require((sha(body) if body is not None else None)==originals.get(name)
                or name in targets and body==targets[name],'PREIMAGE_CHANGED')
    for unit in UNITS:
        area=units/(unit+'.d')
        if area.exists():
            require(area.resolve()==area,'DROPIN_PATH')
            require(all(str(p) in originals or str(p) in targets for p in area.glob('*.conf')),'UNREVIEWED_DROPIN')
    return plan,units,originals,targets


def verify_installation(plan):
    commit=plan['artifact_commit'];root='/opt/quantos/releases/'+commit
    code=('from tools.demo_issuer import verify_source; '
          f'verify_source({commit!r}); '
          'from pathlib import Path; import json; '
          'from tools.build_arm64_release_package import _verify_packaged_frontend_contract; '
          f'p=Path("/opt/quantos/frontends/{commit}"); '
          'm=json.loads((p/"manifest.json").read_text()); '
          f'assert m["release"]["commit"]=={commit!r}; '
          f'assert m["frontend"]["tree_sha256"]=={plan["frontend_tree_sha256"]!r}; '
          '_verify_packaged_frontend_contract(p,m)')
    subprocess.run(['/opt/quantos/venvs/'+commit+'/bin/python','-P','-B','-c',code],cwd=root,
        env={'PATH':'/usr/bin:/bin','PYTHONPATH':root,'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1',
             'GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'safe.directory','GIT_CONFIG_VALUE_0':root},
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,check=True)


def runner_lock_free(plan):
    path=Path(plan['state_root'])/'data/cache/research-runner.lock'
    # The September 14 runner has no lock; PID/cgroup drain remains mandatory.
    if not path.exists() and not path.is_symlink():return True
    require(path.resolve()==path,'RUNNER_LOCK_PATH')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd);require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid(),'RUNNER_LOCK_CUSTODY')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return False
        return True
    finally:os.close(fd)


class Systemd:
    def bind(self,observed):self.observed=observed
    @staticmethod
    def pid_token(pid):
        try:return Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[19]
        except FileNotFoundError:return None
    def capture(self):
        result={}
        for unit in UNITS:
            p=self.properties(unit)
            result[unit]=dict(cgroup=p['ControlGroup'],processes={
                pid:self.pid_token(pid) for pid in (p['MainPID'],p['ControlPID']) if pid!='0'})
        return result
    def call(self,*args):
        return subprocess.check_output(['systemctl','--user',*args],text=True,timeout=15,
                                       stderr=subprocess.DEVNULL)
    def properties(self,unit):
        names=('ActiveState','SubState','MainPID','ControlPID','ControlGroup','WorkingDirectory',
               'TimeoutStopUSec','SendSIGKILL','KillMode','NeedDaemonReload','FragmentPath','DropInPaths')
        return dict(line.split('=',1) for line in self.call('show',unit,*['--property='+n for n in names]).splitlines())
    def reload(self):self.call('daemon-reload')
    def layout(self,plan,targets,*,fresh):
        for row in plan['prior_units']:
            p=self.properties(row['unit']);pre=row['preimage']
            require(p['FragmentPath']==pre['FragmentPath'][0]['path'],'LOADED_FRAGMENT_CHANGED')
            expected={item['path'] for item in pre['DropInPaths']}
            loaded=set(p['DropInPaths'].split())
            if fresh:
                require(loaded==expected and p['NeedDaemonReload']=='no','LOADED_DROPINS_CHANGED')
            else:
                allowed=expected|{name for name in targets if Path(name).parent.name==row['unit']+'.d'}
                require(loaded<=allowed,'LOADED_DROPINS_CHANGED')
    def guard_verified(self):
        p=self.properties(RUNNER)
        require(p['TimeoutStopUSec']=='infinity' and p['SendSIGKILL']=='no' and p['KillMode']=='mixed'
                and p['NeedDaemonReload']=='no','GUARD_NOT_LOADED')
    def stop(self,unit):
        p=self.properties(unit)
        if p['ActiveState'] not in ('inactive','failed','deactivating'):self.call('stop','--no-block',unit)
    def drained_unit(self,unit):
        p=self.properties(unit)
        if p['ActiveState'] not in ('inactive','failed') or p['MainPID']!='0' or p['ControlPID']!='0':return False
        old=self.observed[unit]
        if any(token is not None and self.pid_token(pid)==token for pid,token in old['processes'].items()):return False
        if any(self.populated(group) for group in {p['ControlGroup'],old['cgroup']} if group):return False
        return True
    @staticmethod
    def populated(group):
        require(group.startswith('/user.slice/') and '..' not in Path(group).parts,'CGROUP_PATH')
        path=Path('/sys/fs/cgroup')/group.lstrip('/')/'cgroup.events'
        return path.exists() and dict(line.split() for line in path.read_text().splitlines()).get('populated')!='0'
    def start(self,unit):
        if self.properties(unit)['ActiveState'] not in ('active','activating'):self.call('start','--no-block',unit)
    def healthy(self,unit,commit):
        p=self.properties(unit);root='/opt/quantos/releases/'+commit
        if p['ActiveState']!='active' or p['SubState']!='running' or p['MainPID']=='0':return False
        require(p['WorkingDirectory']==root and p['NeedDaemonReload']=='no'
                and os.readlink('/proc/'+p['MainPID']+'/cwd')==root,'RUNNING_VERSION_CHANGED')
        address={'quantos-next-web.service':'http://192.168.5.68:15911/studio',
                 'quantos-next-console.service':'http://127.0.0.1:15840/health',
                 RUNNER:'http://127.0.0.1:15842/health'}[unit]
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(address,timeout=2) as response:
                return response.status==200
        except OSError:return False


def advance(*,delivery,plan_sha256,operation,manager=None):
    plan,units,originals,targets=review(delivery,plan_sha256)
    verify_installation(plan)
    manager=Systemd() if manager is None else manager
    operation=Path(operation).absolute();require(operation.resolve()==operation,'OPERATION_PATH')
    operation.mkdir(mode=0o700,parents=True,exist_ok=True)
    require(operation.stat().st_uid==os.getuid() and not operation.stat().st_mode&0o077,'OPERATION_CUSTODY')
    lock=units/'.quantos-cutover.lock'
    fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_uid==os.getuid(),'LOCK_CUSTODY')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        path=operation/'state.json'
        fresh=not path.exists()
        manager.layout(plan,targets,fresh=fresh)
        current=decode(read(path)) if not fresh else dict(plan_sha256=plan_sha256,phase='GUARD',observed_services=manager.capture())
        require(current['plan_sha256']==plan_sha256,'OPERATION_BINDING')
        manager.bind(current['observed_services'])
        def save(phase):
            current['phase']=phase
            before=sha(read(path)) if path.exists() else None
            replace_known(path,(json.dumps(current,sort_keys=True)+'\n').encode(),before)
        phase=current['phase']
        if phase=='GUARD':
            save('GUARD')
            guard=units/(RUNNER+'.d')/GUARD_NAME
            replace_known(guard,GUARD.encode(),originals.get(str(guard)))
            manager.reload();manager.guard_verified();save('DRAIN_RUNNER');manager.stop(RUNNER)
            status='WAITING_OLD_RUNNER'
        elif phase=='DRAIN_RUNNER':
            manager.guard_verified()
            if not manager.drained_unit(RUNNER):
                manager.stop(RUNNER);status='WAITING_OLD_RUNNER'
            elif not runner_lock_free(plan):status='WAITING_OLD_RUNNER'
            else:
                save('STOP_READERS')
                for unit in UNITS[1:]:manager.stop(unit)
                status='STOPPING_READERS'
        elif phase=='STOP_READERS':
            for unit in UNITS[1:]:manager.stop(unit)
            if all(manager.drained_unit(u) for u in UNITS) and runner_lock_free(plan):
                save('INSTALL');status='INSTALL_PENDING'
            else:status='WAITING_OLD_SERVICES'
        elif phase=='INSTALL':
            require(all(manager.drained_unit(u) for u in UNITS) and runner_lock_free(plan),'OLD_SERVICES_NOT_DRAINED')
            for name,body in targets.items():replace_known(Path(name),body,originals.get(name))
            save('START');status='START_PENDING'
        elif phase=='START':
            require(all(read(Path(name))==body for name,body in targets.items()),'INSTALLED_FILES_CHANGED')
            manager.reload();manager.guard_verified()
            for unit in (UNITS[1],RUNNER,UNITS[2]):manager.start(unit)
            if all(manager.healthy(u,plan['artifact_commit']) for u in UNITS):
                save('COMPLETE');status='CUTOVER_COMPLETE'
            else:status='WAITING_NEW_SERVICES'
        elif phase=='COMPLETE':
            require(all(manager.healthy(u,plan['artifact_commit']) for u in UNITS),'SERVICE_HEALTH_CHANGED')
            status='CUTOVER_COMPLETE'
        else:raise ValueError('RESEARCH_CUTOVER_PHASE')
        return dict(status=status,phase=current['phase'],artifact_commit=plan['artifact_commit'],
                    operation_path=str(path),trading_enabled=False)
    finally:os.close(fd)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delivery',type=Path,required=True)
    parser.add_argument('--plan-sha256',required=True)
    parser.add_argument('--operation',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args(argv)
    require(os.getuid()!=0 and os.getuid()==os.geteuid(),'USER_MANAGER_REQUIRED')
    plan,*_=review(args.delivery,args.plan_sha256)
    verify_installation(plan)
    if not args.apply:
        print(json.dumps(dict(status='REVIEWED_ONLY',artifact_commit=plan['artifact_commit'],services_changed=False)))
        return 0
    previous=None
    while True:
        result=advance(delivery=args.delivery,plan_sha256=args.plan_sha256,operation=args.operation)
        if result['status']!=previous:print(json.dumps(result),flush=True);previous=result['status']
        if result['status']=='CUTOVER_COMPLETE':return 0
        time.sleep(2)


def quoted(value):
    if any(c in value for c in ('\n', '\r', '%', '$')):
        raise ValueError('unexpected systemd argument expansion')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'

def _render(unit, commit, frontend_root, STATE, WORKSPACES):
    release = '/opt/quantos/releases/' + commit
    python = '/opt/quantos/venvs/' + commit + '/bin/python'
    environment = dict(PYTHONPATH=release, QUANTOS_RELEASE_ROOT=release,
                       QUANTOS_RELEASE_COMMIT=commit, QUANTOS_STATE_ROOT=STATE)
    checks = dict(LIVE_TRADING_ENABLED='false', REAL_BROKER_ENABLED='false',
                  ALLOW_ORDER_SUBMISSION='false', ALLOW_AI_DIRECT_TRADING='false',
                  REQUIRE_HUMAN_APPROVAL='true', QUANTOS_STATE_ROOT=STATE)
    if unit != 'quantos-next-web.service':
        checks.update(QUANTOS_FACTORY_CAMPAIGNS_ENABLED='true', QUANTOS_FACTORY_TRANSPORT='LOCAL_HTTP',
                      QUANTOS_FACTORY_ENDPOINT='http://127.0.0.1:8001/v1/chat/completions',
                      QUANTOS_FACTORY_MODEL='qwen3.8-27b')
    if unit == 'quantos-next-console.service':
        checks['QUANTOS_RESEARCH_WORKSPACES_PATH'] = WORKSPACES
    check = ('import os; from pathlib import Path; '
             'from scripts.evidence_binding import assert_release_evidence_manifest; '
             f'assert_release_evidence_manifest(Path({release!r}), expected_release_commit={commit!r}); '
             f'assert all(os.environ.get(k)==v for k,v in {checks!r}.items()), "RESEARCH_RUNTIME_CONFIGURATION_CHANGED"')
    if unit == 'quantos-next-web.service':
        environment['QUANTOS_FRONTEND_DIST'] = frontend_root + '/frontend'
        check += ('; import json; from tools.build_arm64_release_package import _verify_packaged_frontend_contract; '
                  f'_verify_packaged_frontend_contract(Path({frontend_root!r}), '
                  f'json.loads(Path({(frontend_root + "/manifest.json")!r}).read_text()))')
        arguments = ['-P', '-m', 'tools.local_frontend_server', '--host', '192.168.5.68',
                     '--allow-network', '192.168.5.0/24', '--port', '15911',
                     '--console-port', '15840', '--runner-port', '15842', '--gold-console-port', '15844']
    else:
        module, port = ('services.console.main:app', '15840') if unit == 'quantos-next-console.service' else ('services.research_runner.main:app', '15842')
        arguments = ['-P', '-m', 'uvicorn', module, '--host', '127.0.0.1', '--port', port]
    return '\n'.join(['[Service]', *(['TimeoutStopSec=infinity', 'SendSIGKILL=no', 'KillMode=mixed'] if unit == 'quantos-next-runner.service' else []), 'WorkingDirectory=' + release,
        *['Environment=' + quoted(k + '=' + v) for k, v in environment.items()],
        'ExecStartPre=', 'ExecStartPre=' + ' '.join(map(quoted, [python, '-P', '-c', check])),
        'ExecStart=', 'ExecStart=' + ' '.join(map(quoted, [python, *arguments])), ''])

if __name__=='__main__':
    raise SystemExit(main())
