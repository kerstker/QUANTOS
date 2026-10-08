"""Bound offline maintenance installer. Unbound development template cannot run.

Installs a separate release and environment. Does not launch an operator session.
Never replaces resident units, signing policy, credentials or existing releases.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import tempfile

BINDING = None  # Replaced with exact accepted artifact/package hashes at packaging.
PACKAGE = '/home/kerstker/quantos-dev-output/maintenance-unit-query-20260928/delivery'


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def stage_package(source, destination, files):
    require(isinstance(files,dict) and 1<=len(files)<=100,'PACKAGE_MANIFEST')
    for name,digest in files.items():
        require(isinstance(name,str) and (name in {'release.bundle','requirements-offline.lock','maintenance_launcher.py'}
            or re.fullmatch(r'wheelhouse/[A-Za-z0-9_.+-]+\.whl',name))
            and isinstance(digest,str) and re.fullmatch('[a-f0-9]{64}',digest),'PACKAGE_MANIFEST')
    source=Path(source).absolute();destination=Path(destination)
    require(source.resolve()==source,'PACKAGE_PATH')
    destination.mkdir(mode=0o700)
    for name,digest in files.items():
        path=source/name
        require(path.parent.resolve()==path.parent,'PACKAGE_PATH')
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            before=os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=512*1024**2,'PACKAGE_FILE')
            raw=stream.read(512*1024**2+1);after=os.fstat(stream.fileno())
        require(len(raw)==before.st_size and all(getattr(before,k)==getattr(after,k)
            for k in ('st_size','st_mtime_ns','st_ctime_ns')),'PACKAGE_CHANGED')
        require(hashlib.sha256(raw).hexdigest()==digest,'PACKAGE_DIGEST')
        target=destination/name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())


def protected(path):
    for item in (path,*path.parents):
        info=item.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022,'INSTALL_PARENT')


def saved_payloads(venv, files, *, owner=0):
    saved=venv/'quantos-maintenance'
    info=saved.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid==owner and not info.st_mode&0o022,'INSTALLED_PAYLOAD_CUSTODY')
    for name in ('requirements-offline.lock','maintenance_launcher.py'):
        path=saved/name;info=path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid==owner and info.st_nlink==1
            and not info.st_mode&0o022,'INSTALLED_PAYLOAD_CUSTODY')
        require(hashlib.sha256(path.read_bytes()).hexdigest()==files[name],'INSTALLED_PAYLOAD_DIGEST')
    return saved


def install(binding, package):
    require(os.getresuid()==(0,0,0),'INSTALL_ROOT_REQUIRED')
    require(isinstance(binding,dict) and set(binding)=={'artifact_commit','source_anchor','files'},'INSTALL_BINDING')
    commit=binding['artifact_commit'];anchor=binding['source_anchor']
    require(isinstance(commit,str) and re.fullmatch('[a-f0-9]{40}',commit),'INSTALL_RELEASE')
    require(isinstance(anchor,str) and re.fullmatch('[a-f0-9]{40}',anchor),'INSTALL_ANCHOR')
    require(platform.machine()=='aarch64','INSTALL_ARCHITECTURE')
    files=binding['files']
    require(isinstance(files,dict) and {'release.bundle','requirements-offline.lock','maintenance_launcher.py'}<=files.keys(),'INSTALL_FILES')
    for parent in (Path('/opt/quantos/releases'),Path('/opt/quantos/venvs')):protected(parent)
    workspace=None
    release=Path('/opt/quantos/releases')/commit;venv=Path('/opt/quantos/venvs')/commit
    stamp=hashlib.sha256(json.dumps(binding,sort_keys=True).encode()).hexdigest()
    receipt=venv/'quantos-maintenance-install.json'
    fresh=not release.exists() and not venv.exists()
    if not fresh:
        protected(release);protected(venv)
        info=receipt.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022
            and info.st_nlink==1 and json.loads(receipt.read_bytes())=={'binding_sha256':stamp},'INSTALL_INCOMPLETE_OR_DIFFERENT')
        stage=saved_payloads(venv,files)
    else:
        workspace=Path(tempfile.mkdtemp(prefix='quantos-maintenance-install-',dir='/var/tmp'))
        stage=workspace/'payload';stage_package(package,stage,files)
    env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1',
         'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null'}
    def run(args,**kwargs):return subprocess.run(args,env=env,check=True,**kwargs)
    if fresh:
        require(not release.is_symlink() and not venv.is_symlink(),'INSTALL_TARGET_LINK')
        run(['/usr/bin/git','clone','--quiet','--no-checkout',str(stage/'release.bundle'),str(release)])
        run(['/usr/bin/git','-C',str(release),'checkout','--quiet','--detach',commit])
        for path in [release,*release.rglob('*')]:
            info=path.lstat()
            require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode),'INSTALL_SOURCE_TYPE')
            path.chmod(0o755 if path.is_dir() or info.st_mode&0o111 else 0o644)
        run(['/usr/bin/python3.12','-m','venv',str(venv)])
        run([str(venv/'bin/python'),'-I','-m','pip','--isolated','install','--no-index','--no-deps',
             '--require-hashes','--find-links',str(stage/'wheelhouse'),'-r',str(stage/'requirements-offline.lock')])
    env.update(PYTHONPATH=str(release),PYTHONNOUSERSITE='1')
    verify="""import sys,importlib.metadata
from pathlib import Path
from tools.demo_issuer import verify_source
from scripts.evidence_binding import resolve_canonical_source_anchor
verify_source(sys.argv[1])
assert resolve_canonical_source_anchor(Path(sys.argv[2]))==sys.argv[3]
for line in Path(sys.argv[4]).read_text().splitlines():
 name,version=line.split(' --hash=',1)[0].split('==')
 assert importlib.metadata.version(name)==version,(name,'VERSION_MISMATCH')
"""
    run([str(venv/'bin/python'),'-P','-c',verify,commit,str(release),anchor,str(stage/'requirements-offline.lock')])
    run([str(venv/'bin/python'),'-I','-m','pip','check'])
    if fresh:
        saved=venv/'quantos-maintenance'
        stage_package(stage,saved,{name:files[name] for name in ('requirements-offline.lock','maintenance_launcher.py')})
        saved_payloads(venv,files)
        with receipt.open('x') as stream:
            json.dump({'binding_sha256':stamp},stream);stream.flush();os.fsync(stream.fileno())
        receipt.chmod(0o644)
        stage=saved
    print(json.dumps({'status':'INSTALL_VERIFIED','artifact_commit':commit,'workspace':str(workspace)}),flush=True)
    return stage/'maintenance_launcher.py'


def main():
    parser=argparse.ArgumentParser(description='Install one verified release without activating services or launching maintenance.')
    parser.add_argument('--install-only',action='store_true',required=True)
    parser.parse_args()
    require(BINDING is not None,'UNBOUND_INSTALLER')
    os.umask(0o022)
    install(BINDING,Path(PACKAGE))
    print(json.dumps({'status':'RELEASE_INSTALLED_NOT_ACTIVATED','services_changed':False,'submission_enabled':False}),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
