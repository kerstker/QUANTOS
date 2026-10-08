"""Gold setup documents remain reviewable and explicitly operator gated."""
import json
import pytest
from tools import demo_operator_setup as tool
from tests.tools.test_gold_demo_execution_origin import capture,NOW
from tests.live.test_demo_submission_verification import material
from tests.tools.test_demo_operator_setup import stop_bytes


def test_gold_explicit_spec_and_isolated_bundle_keep_exact_market_scope(tmp_path):
    origin,_=capture();private,registry,*_=material();registry['keys']=[]
    with pytest.raises(ValueError):tool.propose(origin=origin,registry=registry,source_commit='c'*40,now_ms=NOW)
    spec=tool.propose(origin=origin,registry=registry,source_commit='c'*40,now_ms=NOW,symbol='XAUUSDT')
    assert spec['schema']=='DEMO_GOLD_FUNCTIONAL_SETUP_SPEC_V1' and spec['symbol']=='XAUUSDT'
    assert not spec['orders_submitted'] and not registry['keys']
    order=tool.opening_order(origin,spec,client_order_id='QDgoldsetup000001',now_ms=NOW)
    assert order['symbol']=='XAUUSDT' and order['timeInForce']=='IOC' and not order['reduceOnly']
    approval={'schema':'DEMO_OPERATOR_ENROLLMENT_V1','spec_sha256':tool.digest(spec),
        'operator_id':'isolated-human-operator','approved_at_ms':NOW,'purpose':'DEMO_GOLD_FUNCTIONAL_TEST_ONLY'}
    stop=stop_bytes(tmp_path)
    manifest,files=tool.assemble(spec=spec,approval=approval,origin=origin,registry=registry,
        private_key=private,stop_image=stop,now_ms=NOW)
    policy=json.loads(files['policy.json']);profile=json.loads(files['profile.json'])
    assert policy['schema']=='DEMO_GOLD_ISSUER_POLICY_V1' and policy['scope']['symbol']=='XAUUSDT'
    assert profile['native_limits']['allowed_instruments']==['XAUUSDT']
    assert profile['mandate']['allowed_instruments']==['XAUUSDT']
    assert files['kill-switch.sqlite3']==stop and len(manifest['tasks'])==1
    assert json.loads(files[manifest['tasks'][0]+'.json'])['orders'][0]['symbol']=='XAUUSDT'
    with pytest.raises(ValueError):
        tool.assemble(spec=spec,approval=dict(approval,purpose='DEMO_FUNCTIONAL_TEST_ONLY'),origin=origin,
            registry=registry,private_key=private,stop_image=stop,now_ms=NOW)
    assert not registry['keys']


def test_gold_cli_proposes_without_enrollment_or_installation(tmp_path,monkeypatch,capsys):
    origin,_=capture();_,registry,*_=material();registry['keys']=[]
    for name in ('CONFIG','ISSUER','CONTROLLER','EXECUTOR'):
        monkeypatch.setattr(tool.installer,name,tmp_path/name)
    monkeypatch.setattr(tool.installer,'now_ms',lambda:NOW)
    calls=[]
    def inputs(source_commit,*,symbol):
        assert source_commit=='c'*40 and symbol=='XAUUSDT';calls.append(symbol)
        return registry,origin
    monkeypatch.setattr(tool,'_runtime_inputs',inputs)
    monkeypatch.setattr(tool,'operator_confirmation',lambda *a:pytest.fail('unsigned proposal cannot enroll'))
    assert tool.main(['--source-commit','c'*40,'--symbol','XAUUSDT'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='UNSIGNED_PROPOSAL' and result['spec']['schema']==tool.GOLD_SCHEMA
    assert calls==['XAUUSDT'] and not result['orders_submitted'] and not result['services_started']
    assert list(tmp_path.iterdir())==[]


def test_gold_operator_confirmation_preserves_reviewed_currency_and_purpose(monkeypatch,capsys):
    import os,pwd,sys
    from types import SimpleNamespace
    origin,_=capture();_,registry,*_=material();registry['keys']=[]
    spec=tool.propose(origin=origin,registry=registry,source_commit='c'*40,now_ms=NOW,symbol='XAUUSDT')
    # Only simulated identity/TTY; never calls the installed operator entrypoint.
    monkeypatch.setattr(os,'geteuid',lambda:0)
    monkeypatch.setattr(pwd,'getpwuid',lambda _:SimpleNamespace(pw_name='isolated-operator'))
    monkeypatch.setenv('SUDO_UID','12345');monkeypatch.setenv('SUDO_USER','isolated-operator')
    monkeypatch.setattr(sys.stdin,'isatty',lambda:True);monkeypatch.setattr(sys.stdout,'isatty',lambda:True)
    monkeypatch.setattr(tool.installer,'now_ms',lambda:NOW)
    monkeypatch.setattr('builtins.input',lambda prompt:'DEMO '+tool.digest(spec))
    approval=tool.operator_confirmation(spec)
    assert approval['purpose']=='DEMO_GOLD_FUNCTIONAL_TEST_ONLY'
    assert approval['spec_sha256']==tool.digest(spec)
    text=capsys.readouterr().out
    assert '250 USDT' in text and '750 USDT' in text and '250 USDC' not in text


@pytest.mark.parametrize('wrong_path',[False,True])
def test_gold_runtime_capture_keeps_executor_uid_and_market_directory(tmp_path,monkeypatch,wrong_path):
    import os,subprocess
    from types import SimpleNamespace
    from services.research.demo_execution_origin import save
    from live.execution import demo_authority
    from tools import demo_issuer,demo_resident_recovery
    origin,_=capture();_,registry,*_=material();registry['keys']=[]
    monkeypatch.setattr(os,'geteuid',lambda:0)
    monkeypatch.setattr(tool.installer,'EXECUTOR',tmp_path)
    monkeypatch.setattr(demo_issuer,'verify_source',lambda value:None)
    monkeypatch.setattr(demo_authority,'_read_root_owned_json',lambda path:json.dumps(registry).encode())
    monkeypatch.setattr(demo_resident_recovery,'_check_path',lambda *a,**kw:None)
    path=save(tmp_path/'execution-origins'/('BTCUSDC' if wrong_path else 'XAUUSDT'),origin)
    calls=[]
    def child(command,**kwargs):
        calls.append(command)
        assert command[command.index('--symbol')+1]=='XAUUSDT'
        assert kwargs['user']==kwargs['group']==registry['executor_uid']
        assert kwargs['stdin']==subprocess.DEVNULL and 'API_KEY' not in kwargs['env']
        return SimpleNamespace(returncode=0,stdout=json.dumps({'status':'CAPTURED','source_commit':'c'*40,'path':str(path)}).encode())
    monkeypatch.setattr(subprocess,'run',child)
    if wrong_path:
        with pytest.raises(ValueError,match='SETUP_ORIGIN_PATH'):
            tool._runtime_inputs('c'*40,symbol='XAUUSDT')
    else:
        actual,observed=tool._runtime_inputs('c'*40,symbol='XAUUSDT')
        assert actual==registry and observed==origin
    assert len(calls)==1


def test_gold_research_identity_is_bound_before_operator_approval(tmp_path):
    from copy import deepcopy
    from tests.research.test_gold_feedback_trigger import feedback
    research=feedback()['research_identity']
    origin,_=capture();private,registry,*_=material();registry['keys']=[]
    kwargs=dict(origin=origin,registry=registry,source_commit='c'*40,now_ms=NOW,symbol='XAUUSDT')
    spec=tool.propose(**kwargs,research_identity=research)
    assert spec['research_identity']==research
    approval={'schema':'DEMO_OPERATOR_ENROLLMENT_V1','spec_sha256':tool.digest(spec),
        'operator_id':'isolated-human-operator','approved_at_ms':NOW,'purpose':'DEMO_GOLD_FUNCTIONAL_TEST_ONLY'}
    stop=stop_bytes(tmp_path)
    manifest,files=tool.assemble(spec=spec,approval=approval,origin=origin,registry=registry,
        private_key=private,stop_image=stop,now_ms=NOW)
    sid=research['strategy_id'];profile=json.loads(files['profile.json']);policy=json.loads(files['policy.json'])
    assert profile['strategy']['strategy_id']==policy['bindings']['strategy_id']==sid
    assert profile['attestations'][sid]['strategy_id']==sid
    assert json.loads(files[manifest['tasks'][0]+'.json'])['profile']['strategy']['strategy_id']==sid
    changed=deepcopy(spec);changed.pop('research_identity')
    with pytest.raises(ValueError):
        tool.assemble(spec=changed,approval=approval,origin=origin,registry=registry,
            private_key=private,stop_image=stop,now_ms=NOW)
    changed=deepcopy(spec);changed['research_identity']['trial_id']='f'*64
    with pytest.raises(ValueError):tool.spec_check(changed)
    from services.research.gold_execution_identity import identify
    future=identify({k:v for k,v in research.items() if k not in ('strategy_id','result_hash')}|{'available_at_ms':NOW+1})
    with pytest.raises(ValueError):tool.propose(**kwargs,research_identity=future)
    assert not registry['keys']


def test_reviewed_research_identity_uses_only_fixed_hash_named_protected_file(monkeypatch):
    from live.execution import demo_authority
    from tests.research.test_gold_feedback_trigger import feedback
    identity=feedback()['research_identity'];sha=tool.digest(identity);paths=[]
    monkeypatch.setattr(demo_authority,'_read_root_owned_json',lambda p:paths.append(str(p)) or tool.canonical(identity))
    assert tool.reviewed_research_identity(sha,now_ms=NOW)==identity
    assert paths==['/etc/quantos/demo-observation/research-identities/'+sha+'.json']
    with pytest.raises(ValueError):tool.reviewed_research_identity('../outside',now_ms=NOW)
    assert len(paths)==1
    with pytest.raises(ValueError):tool.reviewed_research_identity('f'*64,now_ms=NOW)
