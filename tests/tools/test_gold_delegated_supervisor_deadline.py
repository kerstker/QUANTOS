"""Real proof replay cannot cross its earlier deadline before a send claim."""
from contextlib import nullcontext
from copy import deepcopy
import json

import pytest

from live.execution import demo_delegated_inputs as inputs, demo_gold_execution_delegation as contract
from live.execution import demo_gold_delegation_enable as enabling, demo_request_inputs as builder
from live.execution.demo_batch_risk import _digest
from tests.live.test_gold_delegated_pipeline import pipeline as pipeline, task_material
from tests.live.test_gold_strategy_constraints import case as case
from tests.tools.test_gold_runtime_evidence import original_evidence as original_evidence
from tests.live.test_gold_execution_delegation import sign
from tools import demo_supervisor as supervisor


@pytest.mark.parametrize('clock_change',['delegation_deadline','rollback','claim_persistence','constraints_rollback'])
def test_native_replay_deadline_or_clock_failure_never_launches(pipeline,monkeypatch,clock_change):
    f=pipeline
    # Change and independently sign synthetic authority before constructing
    # this test request. The actual archive, replay, signature and risk gates
    # remain native; this is not an installed authority mutation.
    f.delegation['terms']['opening_until_ms']=f.now_ms+1000
    sign(contract,f.private,f.delegation)
    f.enable['delegation']=deepcopy(f.delegation)
    f.enable['operator_confirmation']['candidate_sha256']=_digest(enabling.candidate(f.enable))
    f.proof['delegation_sha256']=_digest(f.delegation)
    f.proof['enable_sha256']=_digest(f.enable)
    task,market,context=task_material(f)
    built=builder.build(orders=task['orders'],origin=f.proof['origin'],market_inputs=market,
        profile=task['profile'],issuer_policy=f.base_policy,
        kill_state=f.proposal_values['risk_inputs']['kill_switch'].state,now_ms=f.now_ms,
        execution_constraints=task['execution_constraints'],delegated_admission=task['delegated_admission'],
        delegation_context=context,**f.stores)
    clock=[f.now_ms+2]
    claims=[]; launches=[]; resolved=[]
    native=inputs.resolve
    native_constraints=supervisor.strategy_constraints_check

    def enabled(**kwargs):
        assert kwargs.pop('role')=='controller'
        return deepcopy(enabling.validate(f.current_enable,**kwargs))

    def elapsed(*args,**kwargs):
        admitted=native(*args,**kwargs)
        resolved.append(admitted)
        clock[0]=(admitted['valid_until_ms'] if clock_change=='delegation_deadline' else
                  kwargs['now_ms']-1 if clock_change=='rollback' else kwargs['now_ms']+1)
        # The earlier delegation clock, rather than the ordinary five-second
        # constraints, is what must prevent this request from claiming.
        assert task['execution_constraints']['not_before_ms']<=clock[0]<task['execution_constraints']['valid_until_ms']
        return admitted

    def no_launch(*args,**kwargs):
        launches.append(kwargs)
        raise ValueError('isolated launch boundary')

    def record_claim(*args):
        claims.append(args)
        if clock_change=='claim_persistence':clock[0]=resolved[-1]['valid_until_ms']

    def checked_constraints(*args,**kwargs):
        value=native_constraints(*args,**kwargs)
        if clock_change=='constraints_rollback':clock[0]=kwargs['now_ms']-1
        return value

    monkeypatch.setattr(enabling,'load_enabled',enabled)
    monkeypatch.setattr(inputs,'resolve',elapsed)
    monkeypatch.setattr(supervisor,'strategy_constraints_check',checked_constraints)
    monkeypatch.setattr(supervisor,'now_ms',lambda:clock[0])
    monkeypatch.setattr(supervisor,'controller_lease',lambda:nullcontext())
    monkeypatch.setattr(supervisor,'configuration',lambda source:(f.registry,f.base_policy))
    monkeypatch.setattr(supervisor,'read_request',lambda identity:json.dumps(built['request']).encode())
    monkeypatch.setattr(supervisor,'claim',record_claim)
    monkeypatch.setattr(supervisor,'child_command',no_launch)
    monkeypatch.setattr(supervisor,'_root_private_directory',lambda path:None)
    monkeypatch.setattr(supervisor,'_retain',lambda *args:None)
    result=supervisor.run(source_commit='c'*40,request_sha256='a'*64,purpose='submission')
    assert len(resolved)==1
    assert len(claims)==int(clock_change=='claim_persistence') and launches==[]
    assert result['claim_retained'] is (clock_change=='claim_persistence')
