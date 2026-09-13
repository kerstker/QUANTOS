"""Private subprocess handoff has a single attempt and bounded failure paths."""
import json
import os
import subprocess
import sys
import time

import pytest

from tools import demo_supervisor as supervisor
from live.execution.demo_batch_risk import _digest


def child(code):
    return subprocess.Popen([sys.executable,'-I','-B','-u','-c',code],stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)


def test_units_keep_the_issuer_offline_and_both_roles_on_fixed_release_and_state():
    registry={'executor_uid':2001,'issuer_uid':2002}
    for role in ('executor','issuer'):
        command=supervisor.child_command(role,source_commit='a'*40,registry=registry,purpose='submission',unit='quantos-demo-'+role+'-isolated1234')
        assert command[:5]==['systemd-run','--quiet','--collect','--wait','--pipe']
        text=' '.join(command)
        assert f'User={registry[role+"_uid"]}' in text and 'NoNewPrivileges=yes' in text
        assert 'ALLOW_ORDER_SUBMISSION=false' in text and 'ALLOW_AI_DIRECT_TRADING=false' in text
        assert '/opt/quantos/releases/'+'a'*40 in text
        if role=='issuer':
            assert 'PrivateNetwork=yes' in text and 'RestrictAddressFamilies=AF_UNIX' in text
            assert 'InaccessiblePaths=/etc/quantos/demo-submission /var/lib/quantos-demo-executor' in text
        else:assert 'InaccessiblePaths=/etc/quantos/demo-issuer /var/lib/quantos-demo-issuer' in text


def test_actual_two_child_pipes_forward_exact_proposal_and_grant_without_retries():
    proposal={'fixture':'bounded subprocess protocol only'}
    handoff={'schema':'DEMO_EXECUTION_SESSION_PROPOSAL_V1','proposal_sha256':_digest(proposal),
        'proposal_path':'/isolated/example.json','proposal':proposal,'status':'WAITING_INDEPENDENT_GRANT','submission_enabled':False}
    grant={'schema':'DEMO_EXECUTION_SESSION_GRANT_V1','proposal_sha256':handoff['proposal_sha256'],'envelope':{'fixture':True}}
    expected={'schema':'DEMO_EXECUTION_SESSION_RESULT_V1','status':'QUERY_RECOVERY_REQUIRED',
        'cash_reconciled':False,'economic_authority':False,'fixture_only':True}
    executable=child('import sys,json\nassert sys.stdin.buffer.readline()==b"request\\n"\n'
        +f'print({json.dumps(json.dumps(handoff))},flush=True)\n'
        +f'assert json.loads(sys.stdin.buffer.readline())=={grant!r}\n'
        +f'print({json.dumps(json.dumps(expected))},flush=True)\n')
    issuers=[]
    def issuer():
        process=child('import sys,json\n'+f'assert json.loads(sys.stdin.buffer.readline())=={handoff!r}\n'
            +f'print({json.dumps(json.dumps(grant))},flush=True)\n')
        issuers.append(process);return process
    try:
        result=supervisor.exchange(executable,issuer,b'request',purpose='submission',deadline=time.monotonic()+5)
        assert result['result']==expected and result['proposal_sha256']==handoff['proposal_sha256']
        assert len(issuers)==1 and executable.wait(timeout=2)==issuers[0].wait(timeout=2)==0
    finally:
        for process in [executable,*issuers]:
            if process.poll() is None:process.kill();process.wait(timeout=2)
            process.stdin.close();process.stdout.close()


@pytest.mark.parametrize('mode',['eof','timeout','extra_frame'])
def test_failed_executor_proposal_never_launches_issuer(mode):
    code='import sys,time\nsys.stdin.buffer.readline()\n'
    if mode=='timeout':code+='time.sleep(5)\n'
    elif mode=='extra_frame':code+='print("{}\\n{}",flush=True)\n'
    process=child(code);started=time.monotonic()
    try:
        with pytest.raises(ValueError):supervisor.exchange(process,lambda:pytest.fail('issuer started'),
            b'request',purpose='submission',deadline=time.monotonic()+.1)
        assert time.monotonic()-started<2
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=2)
        process.stdin.close();process.stdout.close()


def test_a_request_claim_is_not_recreated_after_process_restart(tmp_path,monkeypatch):
    monkeypatch.setattr(supervisor,'_root_private_directory',lambda p:None)
    root=tmp_path;request_sha='b'*64
    value={'schema':'DEMO_SUPERVISOR_CLAIM_V1','request_sha256':request_sha,'purpose':'submission','source_commit':'a'*40,
        'units':{'executor':'quantos-demo-executor-isolated1234','issuer':'quantos-demo-issuer-isolated1234'},'observed_at_ms':100}
    path=supervisor.claim(root,value)
    before=path.read_bytes()
    with pytest.raises(FileExistsError):supervisor.claim(root,dict(value,observed_at_ms=101))
    assert path.read_bytes()==before
