"""Bind the existing root installer and user cutover into one operator command."""
import ast
import inspect
import json
from pathlib import Path

from tools import research_service_cutover as cutover
from tools.build_research_install_bundle import DRIVER as INSTALL_DRIVER


def operate(binding, *, apply=False):
    import hashlib
    import os
    from pathlib import Path
    import stat
    import subprocess

    if os.getuid()==0 or os.getuid()!=binding['uid'] or os.geteuid()!=os.getuid():
        raise ValueError('RESEARCH_OPERATOR_USER_REQUIRED')
    contents={}
    for name,row in binding['files'].items():
        path=Path(row['path'])
        if path.resolve()!=path:raise ValueError('OPERATOR_PATH_CHANGED')
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_mode&0o022 or info.st_uid not in (0,os.getuid()):
                raise ValueError('OPERATOR_FILE_CUSTODY')
            raw=stream.read(2_000_001)
        if len(raw)>2_000_000 or hashlib.sha256(raw).hexdigest()!=row['sha256']:
            raise ValueError('OPERATOR_PAYLOAD_CHANGED')
        contents[name]=raw
    if not apply:
        return dict(status='REVIEWED_NOT_APPLIED',artifact_commit=binding['commit'],services_changed=False)
    # Use the already-reviewed root installer unchanged. Its own root-private
    # staging verifies every source/frontend payload before executing it.
    subprocess.run(['sudo','/usr/bin/python3','-I','-'],input=contents['installer'],check=True)
    root='/opt/quantos/releases/'+binding['commit']
    program=('import sys;sys.path.insert(0,'+repr(root)+');'+
        'ns={"__name__":"research_operator_cutover"};exec(compile('+repr(contents['cutover'])+
        ',"reviewed-cutover.py","exec"),ns);raise SystemExit(ns["main"]('+repr([
        '--delivery',binding['delivery'],'--plan-sha256',binding['plan_sha256'],
        '--operation',binding['operation'],'--apply'])+'))')
    subprocess.run(['/opt/quantos/venvs/'+binding['commit']+'/bin/python','-I','-B','-c',program],check=True)
    return dict(status='RESEARCH_DELIVERY_COMPLETE',artifact_commit=binding['commit'],submission_enabled=False)


def prepare(combined, delivery, destination, operation):
    import os
    combined=Path(combined).absolute();delivery=Path(delivery).absolute()
    destination=Path(destination).absolute()
    plan_raw=cutover.read(delivery/'plan.json')
    plan,_,_,_=cutover.review(delivery,cutover.sha(plan_raw))
    install_plan=cutover.decode(cutover.read(combined/'plan.json'))
    installer=cutover.read(combined/'install-source-and-frontend.py')
    cutover.require(install_plan['schema']=='RESEARCH_COMBINED_INSTALL_PLAN_V1'
        and install_plan['status']=='PREPARED_NOT_APPLIED'
        and install_plan['artifact_commit']==plan['artifact_commit']
        and install_plan['source_anchor']==plan['source_anchor']
        and install_plan['installer_sha256']==cutover.sha(installer),'INSTALL_PLAN_BINDING')
    assignments=[n for n in ast.parse(installer).body if isinstance(n,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=='BINDING' for t in n.targets)]
    cutover.require(len(assignments)==1,'INSTALLER_BINDING')
    original=ast.literal_eval(assignments[0].value)
    expected=INSTALL_DRIVER.replace('BINDING = None  # Bound by reviewed package builder.','BINDING = '+repr(original))
    cutover.require(installer==expected.encode()
        and original['source_binding']['artifact_commit']==plan['artifact_commit']
        and original['source_binding']['source_anchor']==plan['source_anchor']
        and original['frontend_binding']['artifact_commit']==plan['artifact_commit'],'INSTALLER_CODE')
    destination.mkdir(mode=0o700)
    payloads={'installer':installer,'cutover':Path(cutover.__file__).read_bytes(),
              'delivery/plan.json':plan_raw}
    payloads.update({'delivery/'+name:cutover.read(delivery/name) for name in plan['files']})
    files={}
    for name,raw in payloads.items():
        relative={'installer':'install-source-and-frontend.py','cutover':'cutover.py'}.get(name,name)
        path=destination/relative;path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        path.write_bytes(raw);path.chmod(0o600)
        files[name]=dict(path=str(path),sha256=cutover.sha(raw))
    binding=dict(uid=os.getuid(),commit=plan['artifact_commit'],files=files,
        delivery=str(destination/'delivery'),plan_sha256=cutover.sha(plan_raw),
        operation=str(Path(operation).absolute()))
    driver='import argparse,json\n'+inspect.getsource(operate)+'\nBINDING = '+repr(binding)+'''
if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    print(json.dumps(operate(BINDING,apply=args.apply)),flush=True)
'''
    (destination/'operate.py').write_text(driver)
    (destination/'operate.py').chmod(0o600)
    result=dict(status='PREPARED_NOT_APPLIED',artifact_commit=plan['artifact_commit'],
        operator_sha256=cutover.sha(driver.encode()),services_changed=False)
    (destination/'operator-plan.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
