"""Prepare one bound source+frontend install command without activating services."""
import ast
import hashlib
import inspect
import json
import os
import re
from pathlib import Path
import stat

from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from scripts.evidence_binding import assert_release_evidence_manifest, resolve_canonical_source_anchor
from tools import install_research_frontend as frontend
from tools.build_arm64_release_package import sha256_file


def copy_checked(source, destination, expected):
    source=Path(source);destination=Path(destination)
    if source.parent.resolve()!=source.parent.absolute(): raise ValueError('INSTALL_PAYLOAD_PARENT')
    fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>512*1024*1024:
            raise ValueError('INSTALL_PAYLOAD_FILE')
        digest=hashlib.sha256()
        destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        with destination.open('xb') as target:
            total=0
            while part:=stream.read(1024*1024):
                total+=len(part)
                if total>512*1024*1024: raise ValueError('INSTALL_PAYLOAD_SIZE')
                digest.update(part);target.write(part)
            target.flush();os.fsync(target.fileno())
        after=os.fstat(stream.fileno())
    if (total!=before.st_size or digest.hexdigest()!=expected or
        any(getattr(before,k)!=getattr(after,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns'))):
        raise ValueError('INSTALL_PAYLOAD_CHANGED')


DRIVER = '''import hashlib,json,os,stat,subprocess,tempfile,runpy
from pathlib import Path
BINDING = None  # Bound by reviewed package builder.
'''+inspect.getsource(copy_checked)+'''
if __name__ == '__main__':
    if BINDING is None: raise ValueError('UNBOUND_RESEARCH_INSTALLER')
    if os.getresuid() != (0,0,0): raise ValueError('INSTALL_ROOT_REQUIRED')
    os.umask(0o077)
    workspace=Path(tempfile.mkdtemp(prefix='quantos-research-install-',dir='/var/tmp'))
    for relative,row in BINDING['payloads'].items():
        name=Path(relative)
        if name.is_absolute() or '..' in name.parts: raise ValueError('INSTALL_RELATIVE_PATH')
        copy_checked(Path(row['source']),workspace/name,row['sha256'])
    # Only digest-checked code in the root-created workspace is executed.
    source=runpy.run_path(str(workspace/'source/install-release-only.py'))
    if source['BINDING'] != BINDING['source_binding']: raise ValueError('INSTALL_SOURCE_BINDING')
    # The imported installer does not run its main() umask setup. Runtime
    # files must be readable by the unprivileged research service user.
    previous_umask=os.umask(0o022)
    try:
        source['install'](BINDING['source_binding'],workspace/'source')
    finally:
        os.umask(previous_umask)
    commit=BINDING['source_binding']['artifact_commit']
    release='/opt/quantos/releases/'+commit
    python='/opt/quantos/venvs/'+commit+'/bin/python'
    program='import sys,json,runpy;sys.path.insert(0,'+repr(release)+');m=runpy.run_path('+repr(str(workspace/'frontend-helper.py'))+');print(json.dumps(m["install"]('+repr(str(workspace/'frontend'))+','+repr(BINDING['frontend_binding'])+')))' 
    subprocess.run([python,'-I','-c',program],check=True,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'})
    print(json.dumps({'status':'RESEARCH_RELEASE_INSTALLED_NOT_ACTIVATED','artifact_commit':commit,'services_changed':False,'submission_enabled':False,'workspace':str(workspace)}))
'''


def prepare(source_delivery, frontend_delivery, destination):
    """Bind accepted source, its matching frontend, and reviewed helper bytes."""
    source=Path(source_delivery).absolute();web=Path(frontend_delivery).absolute();out=Path(destination).absolute()
    report=decode((source/'bundle-verification.json').read_bytes())
    if report.get('status')!='SOURCE_ONLY_DELIVERY_VERIFIED': raise ValueError('SOURCE_DELIVERY_NOT_VERIFIED')
    source_binding={k:report[k] for k in ('artifact_commit','source_anchor','files')}
    clone=source/'verification-clone'
    assert_release_evidence_manifest(clone,expected_release_commit=report['artifact_commit'])
    if resolve_canonical_source_anchor(clone)!=report['source_anchor']: raise ValueError('SOURCE_ANCHOR_CHANGED')
    script=source/'install-release-only.py';raw=script.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=report['installer_sha256']: raise ValueError('SOURCE_INSTALLER_CHANGED')
    assignments=[node for node in ast.parse(raw).body if isinstance(node,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=='BINDING' for t in node.targets)]
    if len(assignments)!=1 or ast.literal_eval(assignments[0].value)!=source_binding:
        raise ValueError('SOURCE_INSTALLER_BINDING')
    template=Path(__file__).with_name('research_source_install_template.py').read_bytes()
    if hashlib.sha256(template).hexdigest()!='c6246bac627c6962b3cd748d2b9f8817cbfed416ba03a3dc468dabd49d2bcd46':
        raise ValueError('SOURCE_INSTALLER_TEMPLATE_CHANGED')
    expected=template.decode().replace('BINDING = None  # Replaced with exact accepted artifact/package hashes at packaging.',
        'BINDING = '+repr(source_binding)).replace("PACKAGE = '/home/kerstker/quantos-dev-output/maintenance-unit-query-20260928/delivery'",
        'PACKAGE = '+repr(str(source)))
    if raw!=expected.encode(): raise ValueError('SOURCE_INSTALLER_CODE_CHANGED')
    for name in report['files']:
        if name not in {'release.bundle','requirements-offline.lock','maintenance_launcher.py'} and not re.fullmatch(r'wheelhouse/[A-Za-z0-9_.+-]+\.whl',name):
            raise ValueError('SOURCE_PAYLOAD_SCOPE')
    manifest_raw=(web/'manifest.json').read_bytes()
    web_binding=dict(artifact_commit=report['artifact_commit'],source_anchor=report['source_anchor'],
                     manifest_sha256=hashlib.sha256(manifest_raw).hexdigest())
    _,rows=frontend.verify(web,web_binding)
    helper=Path(frontend.__file__).resolve();helper_raw=helper.read_bytes()
    payloads={'source/'+name:dict(source=str(source/name),sha256=digest) for name,digest in report['files'].items()}
    payloads['source/install-release-only.py']=dict(source=str(script),sha256=report['installer_sha256'])
    payloads.update({'frontend/frontend/'+name:dict(source=str(web/'frontend'/name),sha256=digest) for name,digest in rows.items()})
    payloads['frontend/manifest.json']=dict(source=str(web/'manifest.json'),sha256=web_binding['manifest_sha256'])
    payloads['frontend-helper.py']=dict(source=str(helper),sha256=hashlib.sha256(helper_raw).hexdigest())
    if len(payloads)>5000: raise ValueError('INSTALL_PAYLOAD_COUNT')
    for row in payloads.values():
        path=Path(row['source']);info=path.lstat()
        if (path.parent.resolve()!=path.parent or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1
            or info.st_size>512*1024*1024 or sha256_file(path)!=row['sha256']):
            raise ValueError('INSTALL_PAYLOAD_CHANGED')
    out.mkdir(mode=0o700)
    # Save the reviewed helper alongside the driver, instead of referring to
    # a mutable development file when the operator eventually runs it.
    (out/'frontend-helper.py').write_bytes(helper_raw)
    payloads['frontend-helper.py']['source']=str(out/'frontend-helper.py')
    binding=dict(schema='RESEARCH_COMBINED_INSTALL_V1',source_binding=source_binding,
                 frontend_binding=web_binding,payloads=payloads)
    driver=DRIVER.replace('BINDING = None  # Bound by reviewed package builder.','BINDING = '+repr(binding))
    ast.parse(driver);(out/'install-source-and-frontend.py').write_text(driver)
    plan=dict(schema='RESEARCH_COMBINED_INSTALL_PLAN_V1',status='PREPARED_NOT_APPLIED',
        artifact_commit=report['artifact_commit'],source_anchor=report['source_anchor'],
        installer_sha256=hashlib.sha256(driver.encode()).hexdigest(),frontend_files=len(rows),
        payload_files=len(payloads),services_changed=False,installed=False,root_install_verified=False)
    (out/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    return plan
