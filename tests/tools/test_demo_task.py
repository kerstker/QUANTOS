"""Task preparation keeps exact input IDs and the original execution handoff."""
from copy import deepcopy
import json
import time

import pytest

from tools import demo_task as task_tool
from tools import demo_request_prepare as preparer
from live.execution import demo_request_inputs as builder
from live.execution.demo_batch_risk import _digest
from tests.live.test_demo_request_inputs import inputs,NOW
from tests.tools.test_demo_supervisor import child


def material(kwargs):
    return {'schema':'DEMO_REQUEST_TASK_V1','source_commit':'c'*40,
        'valid_from_ms':NOW-1,'valid_until_ms':NOW+10000,
        'issuer_policy_sha256':_digest(kwargs['issuer_policy']),
        'profile':deepcopy(kwargs['profile']),'orders':deepcopy(kwargs['orders']),
        'kill_switch':kwargs['kill_state'].model_dump(mode='json')}


@pytest.mark.parametrize('change',[None,'expired','future','source','policy','scope','approval','extra','kill'])
def test_task_is_finite_and_preserves_installed_declarations(tmp_path,monkeypatch,change):
    kwargs,opened,log=inputs(tmp_path,monkeypatch);task=material(kwargs)
    if change=='expired':task['valid_until_ms']=NOW
    elif change=='future':task['valid_from_ms']=NOW+100
    elif change=='source':task['source_commit']='d'*40
    elif change=='policy':task['issuer_policy_sha256']='f'*64
    elif change=='scope':task['orders'][0]['symbol']='BTCUSDT'
    elif change=='approval':task['profile']['mandate']['approved_by']=['AUTO']
    elif change=='extra':task['retry_on_failure']=True
    elif change=='kill':task['kill_switch']={'armed':False}
    try:
        if change:
            with pytest.raises(ValueError):builder.task_check(task,policy=kwargs['issuer_policy'],source_commit='c'*40,now_ms=NOW+10)
        else:
            original=deepcopy(task)
            assert builder.task_check(task,policy=kwargs['issuer_policy'],source_commit='c'*40,now_ms=NOW+10)==task
            assert task==original
        assert opened[0].snapshot()==[] and opened[2].orders()==[]
    finally:
        log.close()
        for store in opened:store.close()


def test_preparer_unit_has_fixed_executor_identity_and_finite_get_budget():
    command=task_tool.preparation_command(source_commit='a'*40,registry={'executor_uid':2001,'issuer_uid':2002},unit='quantos-demo-executor-isolated1234')
    text=' '.join(command)
    assert 'User=2001' in text and 'RuntimeMaxSec=155s' in text
    assert 'tools.demo_request_prepare' in text and 'tools.demo_execution_session' not in text
    assert 'ALLOW_ORDER_SUBMISSION=false' in text and '--purpose' not in command
    assert 'InaccessiblePaths=/etc/quantos/demo-issuer /var/lib/quantos-demo-issuer' in text


def test_actual_preparation_pipe_requires_the_same_task_and_exact_request_digest():
    task={'fixture':'private pipe protocol'};bundle={'task':task,'policy':{}}
    request={'schema':'DEMO_EXECUTION_SESSION_INPUT_V1','fixture':'isolated'}
    response={'schema':'DEMO_REQUEST_PREPARATION_V1','status':'PREPARED_INPUT_ONLY',
        'task_sha256':_digest(task),'request':request,'request_sha256':_digest(request),
        'submission_enabled':False,'economic_authority':False}
    for changed in (False,True):
        result=dict(response,task_sha256='f'*64) if changed else response
        process=child('import json,sys\n'+f'assert json.loads(sys.stdin.buffer.readline())=={bundle!r}\n'+
            f'print({json.dumps(json.dumps(result))},flush=True)\n')
        try:
            if changed:
                with pytest.raises(ValueError):task_tool.prepare_exchange(process,bundle,deadline=time.monotonic()+5)
            else:assert task_tool.prepare_exchange(process,bundle,deadline=time.monotonic()+5)==response
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=2)
            process.stdin.close();process.stdout.close()


def test_missing_installed_configuration_stops_before_market_reads_or_execution(monkeypatch):
    from tools import demo_supervisor as supervisor
    monkeypatch.setattr(supervisor,'configuration',lambda _:(_ for _ in ()).throw(ValueError('missing fixture configuration')))
    monkeypatch.setattr(task_tool,'prepare_exchange',lambda *a,**k:pytest.fail('capture started'))
    monkeypatch.setattr(supervisor,'run',lambda **k:pytest.fail('submission started'))
    result=task_tool.run(source_commit='c'*40,task_sha256='a'*64)
    assert result['status']=='TASK_NOT_COMPLETED' and result['phase']=='CONFIGURATION'
    assert result['task_claim_retained'] is False and not result['retry_submission']


def test_preparer_missing_custody_does_not_collect_or_construct_a_request(monkeypatch):
    monkeypatch.setattr(preparer,'verify_source',lambda _:(_ for _ in ()).throw(ValueError('uninstalled')))
    monkeypatch.setattr(preparer,'capture_inputs',lambda **k:pytest.fail('network started'))
    import io
    output=io.StringIO()
    assert preparer.run(source_commit='c'*40,input_fd=0,output=output)==3
    result=json.loads(output.getvalue())
    assert result['status']=='INPUTS_UNRESOLVED' and result['phase']=='CONFIGURATION'
    assert not result['submission_enabled'] and 'request' not in result


def test_preparer_uses_its_exact_origin_when_a_periodic_writer_moves_latest(tmp_path,monkeypatch):
    from services.research import demo_execution_origin as origin_model,demo_market_inputs
    kwargs,opened,log=inputs(tmp_path,monkeypatch)
    directory=tmp_path/'execution-origins/BTCUSDC'
    path=origin_model.save(directory,kwargs['origin'])
    changed=deepcopy(kwargs['origin']);changed['source_sha256']='f'*64
    origin_model.save(directory,changed)
    monkeypatch.setattr(preparer.demo_executor_origin,'run',lambda **k:{'status':'CAPTURED','path':str(path)})
    monkeypatch.setattr(preparer.binance_demo_market_inputs,'collect_bounded',lambda **k:kwargs['market_inputs'])
    try:
        origin,market=preparer.capture_inputs(root=tmp_path,symbol='BTCUSDC',source_commit='c'*40)
        assert origin==kwargs['origin'] and origin!=origin_model.load_latest(directory)
        assert demo_market_inputs.load_latest(tmp_path/'market-inputs-BTCUSDC')==market
    finally:
        log.close()
        for store in opened:store.close()


def test_a_retained_task_is_not_prepared_or_submitted_again_after_restart(tmp_path,monkeypatch):
    from tools import demo_supervisor as supervisor
    kwargs,opened,log=inputs(tmp_path,monkeypatch);task=material(kwargs);identity=_digest(task)
    for name in ('tasks','task-claims','task-results','requests'):(tmp_path/name).mkdir()
    monkeypatch.setattr(supervisor,'STATE',tmp_path)
    monkeypatch.setattr(supervisor,'configuration',lambda _:({'executor_uid':2001,'issuer_uid':2002},kwargs['issuer_policy']))
    monkeypatch.setattr(supervisor,'_root_private_directory',lambda _:None)
    monkeypatch.setattr(supervisor,'read_document',lambda *a:json.dumps(task).encode())
    monkeypatch.setattr(supervisor,'now_ms',lambda:NOW+10)
    monkeypatch.setattr(task_tool,'task_directories',lambda:None)
    launches=[]
    def failed_child(*a,**k):launches.append(1);raise OSError('isolated launch failure')
    monkeypatch.setattr(task_tool.subprocess,'Popen',failed_child)
    monkeypatch.setattr(supervisor,'run',lambda **k:pytest.fail('original submission reached'))
    try:
        first=task_tool.run(source_commit='c'*40,task_sha256=identity)
        claim=(tmp_path/'task-claims'/f'{identity}.json').read_bytes()
        second=task_tool.run(source_commit='c'*40,task_sha256=identity)
        assert first['task_claim_retained'] and first['phase']=='OBSERVED_PREPARATION'
        assert second['phase']=='TASK_CLAIM' and launches==[1]
        assert (tmp_path/'task-claims'/f'{identity}.json').read_bytes()==claim
        assert len(list((tmp_path/'task-results').iterdir()))==1
    finally:
        log.close()
        for store in opened:store.close()


def test_preparer_emits_original_risk_request_over_one_pipe_without_order_writes(tmp_path,monkeypatch):
    import os
    kwargs,opened,log=inputs(tmp_path,monkeypatch);task=material(kwargs)
    monkeypatch.setattr(preparer,'verify_source',lambda _:None)
    monkeypatch.setattr(preparer,'fixed_account',lambda:(tmp_path,kwargs['issuer_policy']['account_ref_sha256']))
    monkeypatch.setattr(preparer.os,'getegid',lambda:2001);monkeypatch.setattr(preparer.os,'getgroups',lambda:[2001])
    monkeypatch.setattr(preparer,'private_pipe',lambda _:None)  # isolated user-owned pipe; custody tested separately
    monkeypatch.setattr(preparer,'now_ms',lambda:NOW+10)
    monkeypatch.setattr(preparer,'_open',lambda *a:{k:v for k,v in zip(('dispatch','recovery','oms'),opened)})
    monkeypatch.setattr(preparer,'capture_inputs',lambda **k:(kwargs['origin'],kwargs['market_inputs']))
    read,write=os.pipe();out_read,out_write=os.pipe()
    # Large request output is drained by a concurrent reader, exactly as root
    # does in production; these are synthetic fixtures without venue requests.
    from concurrent.futures import ThreadPoolExecutor
    bundle=json.dumps({'task':task,'policy':kwargs['issuer_policy']},separators=(',',':')).encode()+b'\n'
    def read_output():
        with os.fdopen(out_read,'rb') as channel:return channel.read()
    try:
        os.write(write,bundle);os.close(write)
        with ThreadPoolExecutor(max_workers=1) as pool:
            reading=pool.submit(read_output)
            with os.fdopen(out_write,'w') as output:code=preparer.run(source_commit='c'*40,input_fd=read,output=output)
            raw=reading.result(timeout=3)
        value=json.loads(raw)
        assert code==0 and value['task_sha256']==_digest(task) and value['status']=='PREPARED_INPUT_ONLY'
        assert value['precheck_status']=='RISK_CHECKS_PASSED' and value['request']['orders']==task['orders']
        assert len(list((tmp_path/'request-preparation-results').iterdir()))==1
        assert opened[0].snapshot()==[] and opened[2].orders()==[] and log.snapshot()==[]
    finally:
        os.close(read);log.close()
        for store in opened:store.close()


@pytest.mark.parametrize('expires',[False,True])
def test_task_handoff_retains_exact_request_before_original_supervisor_and_rechecks_expiry(tmp_path,monkeypatch,expires):
    from tools import demo_supervisor as supervisor
    kwargs,opened,log=inputs(tmp_path,monkeypatch);task=material(kwargs);identity=_digest(task)
    prepared=builder.build(**kwargs);prepared.update(schema='DEMO_REQUEST_PREPARATION_V1',task_sha256=identity)
    for name in ('tasks','task-claims','task-results','requests'):(tmp_path/name).mkdir()
    monkeypatch.setattr(supervisor,'STATE',tmp_path)
    monkeypatch.setattr(supervisor,'configuration',lambda _:({'executor_uid':2001,'issuer_uid':2002},kwargs['issuer_policy']))
    monkeypatch.setattr(supervisor,'_root_private_directory',lambda _:None)
    monkeypatch.setattr(supervisor,'read_document',lambda *a:json.dumps(task).encode())
    monkeypatch.setattr(supervisor,'read_request',lambda sha:(tmp_path/'requests'/f'{sha}.json').read_bytes())
    monkeypatch.setattr(supervisor,'now_ms',lambda:NOW+10)
    monkeypatch.setattr(task_tool,'task_directories',lambda:None)
    class Completed:
        stdin=stdout=None
        def poll(self):return 0
    launches=[]
    monkeypatch.setattr(task_tool.subprocess,'Popen',lambda *a,**k:(launches.append(1) or Completed()))
    monkeypatch.setattr(task_tool.subprocess,'run',lambda *a,**k:None)
    def prepare(*a,**k):
        assert (tmp_path/'task-claims'/f'{identity}.json').exists()
        if expires:monkeypatch.setattr(supervisor,'now_ms',lambda:task['valid_until_ms'])
        return prepared
    monkeypatch.setattr(task_tool,'prepare_exchange',prepare)
    called=[]
    def original(**args):
        raw=(tmp_path/'requests'/f'{args["request_sha256"]}.json').read_bytes()
        import hashlib
        assert hashlib.sha256(raw).hexdigest()==args['request_sha256'] and not raw.endswith(b'\n')
        assert json.loads(raw)==prepared['request'] and args['purpose']=='submission'
        assert list((tmp_path/'task-results').iterdir())
        called.append(args);return {'status':'EXECUTOR_RESULT_RECORDED','cash_reconciled':False}
    monkeypatch.setattr(supervisor,'run',original)
    try:
        value=task_tool.run(source_commit='c'*40,task_sha256=identity)
        assert len(called)==(0 if expires else 1) and launches==[1]
        assert value['status']==('TASK_NOT_COMPLETED' if expires else 'EXECUTOR_RESULT_RECORDED')
        assert value['task_claim_retained'] and not value['cash_reconciled']
        task_tool.run(source_commit='c'*40,task_sha256=identity)
        assert launches==[1] and len(called)==(0 if expires else 1)
    finally:
        log.close()
        for store in opened:store.close()
