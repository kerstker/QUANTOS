"""Successor proposals retain the original market and pinned research identity."""
from contextlib import nullcontext
import json
import pytest
from tools import demo_successor_setup as tool
from tools import demo_operator_setup as setup
from tests.tools.test_gold_demo_execution_origin import capture,NOW
from tests.live.test_demo_submission_verification import material
from tests.research.test_gold_feedback_trigger import feedback


def environment(tmp_path,monkeypatch):
    origin,_=capture();_,registry,*_=material();registry['keys']=[]
    identity=feedback()['research_identity']
    spec=setup.propose(origin=origin,registry=registry,source_commit='b'*40,now_ms=NOW,
        symbol='XAUUSDT',research_identity=identity)
    installed={'spec_sha256':setup.digest(spec),'source_commit':'b'*40}
    stage=tmp_path/'setup';stage.mkdir()
    monkeypatch.setattr(tool,'SETUP',stage)
    monkeypatch.setattr(tool,'active_setup',lambda:stage)
    monkeypatch.setattr(tool,'read',lambda path:spec if path.name=='spec.json' else installed)
    monkeypatch.setattr(tool,'verify_source',lambda _:None)
    monkeypatch.setattr(tool.os,'geteuid',lambda:0)
    monkeypatch.setattr(tool,'cycle_lock',lambda _:nullcontext())
    monkeypatch.setattr(tool.installer,'now_ms',lambda:NOW)
    monkeypatch.setattr(tool.installer,'CONTROLLER',tmp_path/'controller')
    monkeypatch.setattr(tool,'context_for',lambda *a:{'initial_equity':'5000','canonical_wallet':'5000'})
    monkeypatch.setattr(setup,'operator_confirmation',lambda *a:pytest.fail('unsigned proposal enrolled'))
    calls=[]
    def inputs(commit,*,continuation,symbol='BTCUSDC'):
        assert continuation and symbol=='XAUUSDT';calls.append(symbol)
        return registry,origin
    monkeypatch.setattr(setup,'_runtime_inputs',inputs)
    def propose(**kw):
        assert kw['symbol']=='XAUUSDT' and kw['research_identity']==identity
        return dict(spec,source_commit=kw['source_commit'])
    monkeypatch.setattr(setup,'propose',propose)
    return calls,spec,installed,identity


@pytest.mark.parametrize('explicit',[False,True])
def test_unsigned_gold_successor_keeps_market_and_research_identity(tmp_path,monkeypatch,capsys,explicit):
    calls,spec,installed,identity=environment(tmp_path,monkeypatch)
    args=['--source-commit','c'*40]+(['--symbol','XAUUSDT'] if explicit else [])
    assert tool.main(args)==0
    value=json.loads(capsys.readouterr().out)
    assert value['status']=='UNSIGNED_SUCCESSOR_PROPOSAL'
    assert value['spec']['research_identity']==identity and not value['orders_submitted']
    assert calls==['XAUUSDT','XAUUSDT']


@pytest.mark.parametrize('damage',['market','spec_hash'])
def test_wrong_original_scope_stops_before_capture(tmp_path,monkeypatch,damage):
    calls,spec,installed,_=environment(tmp_path,monkeypatch)
    args=['--source-commit','c'*40]
    if damage=='market':args+=['--symbol','BTCUSDC']
    else:installed['spec_sha256']='f'*64
    assert tool.main(args)==3
    assert calls==[]


def test_next_candidate_requires_reviewed_identity_before_proposal(tmp_path,monkeypatch,capsys):
    calls,spec,installed,identity=environment(tmp_path,monkeypatch)
    next_identity=dict(identity,trial_id='next-reviewed-trial')
    reviewed=[]
    def load(value,*,now_ms):
        reviewed.append(value);assert now_ms==NOW
        return next_identity
    monkeypatch.setattr(setup,'reviewed_research_identity',load)
    def propose(**kwargs):
        assert kwargs['symbol']=='XAUUSDT' and kwargs['research_identity']==next_identity
        return dict(spec,source_commit=kwargs['source_commit'],research_identity=next_identity)
    monkeypatch.setattr(setup,'propose',propose)
    assert tool.main(['--source-commit','c'*40,'--research-identity-sha256','d'*64])==0
    assert reviewed==['d'*64] and calls==['XAUUSDT','XAUUSDT']
    value=json.loads(capsys.readouterr().out)
    assert value['spec']['research_identity']==next_identity and not value['orders_submitted']


def test_gold_successor_cannot_silently_become_portfolio(tmp_path,monkeypatch):
    calls,*_=environment(tmp_path,monkeypatch)
    assert tool.main(['--source-commit','c'*40,'--portfolio-config',str(tmp_path/'unused')])==3
    assert calls==[]


def gold_continuation(tmp_path):
    from services.research.demo_origin_contract import contract_from_origin
    from tests.tools.test_demo_successor_setup import fixture
    _,registry,private,context=fixture(tmp_path)
    origin,_=capture()
    context.update(symbol='XAUUSDT',account_ref_sha256=origin['account_ref_sha256'],
        canonical_wallet='5000',initial_equity='5001',peak_equity_floor='5002',
        available_at_ms=NOW-1,contract=contract_from_origin(origin)[0])
    return origin,registry,private,context


def test_gold_continuation_bundle_retains_risk_history_and_trial_identity(tmp_path):
    from copy import deepcopy
    from decimal import Decimal
    from tests.tools.test_demo_operator_setup import stop_bytes
    origin,registry,private,context=gold_continuation(tmp_path)
    before=deepcopy(registry);identity=feedback()['research_identity']
    spec=setup.propose(origin=origin,registry=registry,source_commit='c'*40,now_ms=NOW,
        position_context=context,symbol='XAUUSDT',research_identity=identity)
    approval={'schema':'DEMO_OPERATOR_ENROLLMENT_V1','spec_sha256':setup.digest(spec),
        'operator_id':'isolated-test-operator','approved_at_ms':NOW,'purpose':'DEMO_GOLD_FUNCTIONAL_TEST_ONLY'}
    stop=stop_bytes(tmp_path)
    manifest,files=setup.assemble(spec=spec,approval=approval,origin=origin,registry=registry,
        private_key=private,stop_image=stop,now_ms=NOW,position_context=context)
    profile=json.loads(files['profile.json']);policy=json.loads(files['policy.json'])
    task=json.loads(files[manifest['tasks'][0]+'.json'])
    assert json.loads(files['registry.json'])['keys'][:-1]==before['keys'] and registry==before
    assert profile['initial_day_pnl']=='-1' and profile['initial_peak_equity']=='5002'
    assert Decimal(str(profile['minimum_plan_turnover']))>=Decimal('239.8')/Decimal('5000')
    assert profile['strategy']['strategy_id']==policy['bindings']['strategy_id']==identity['strategy_id']
    assert task['orders'][0]['symbol']=='XAUUSDT' and policy['scope']['symbol']=='XAUUSDT'
    assert files['kill-switch.sqlite3']==stop


@pytest.mark.parametrize('damage',['wallet','held','active_key','market'])
def test_gold_continuation_cannot_reset_account_or_reuse_active_approval(tmp_path,damage):
    origin,registry,_,context=gold_continuation(tmp_path)
    if damage=='wallet':context['canonical_wallet']='5001'
    elif damage=='held':context['quantity']='0.001'
    elif damage=='active_key':registry['keys'][0]['valid_until_ms']=NOW+1
    else:context['symbol']='BTCUSDC'
    with pytest.raises(ValueError):
        setup.propose(origin=origin,registry=registry,source_commit='c'*40,now_ms=NOW,
            position_context=context,symbol='XAUUSDT',research_identity=feedback()['research_identity'])
