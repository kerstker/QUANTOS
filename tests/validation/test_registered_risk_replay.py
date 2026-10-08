"""Pure lifecycle checks over original native output; no execution imports in API."""
import copy
import json

import pytest

from tests.tools.test_registered_research_run import registered_case as registered_case
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments
from tests.tools.test_registered_risk_replay import prepare,run


@pytest.fixture
def retained_risk(registered_case,technical_arguments):
    f=registered_case;plan=prepare(f,technical_arguments);result=run(f,technical_arguments,plan)
    directory=f['run'].run_directory(f['root']/'project',plan)
    return (json.loads((directory/'archive/risk-replay.json').read_bytes()),
        dict(plan=plan,original_result=result,invocation=json.loads((directory/'invocation.json').read_bytes())))


@pytest.mark.parametrize('damage',['plan','report','time','bool_time','authority','input','replay','run_id','manifest','extra'])
def test_actual_artifact_cannot_be_rebound_or_promoted(retained_risk,damage):
    from quantos.contracts.registered_risk_replay import validate_artifact
    original,arguments=retained_risk;value=copy.deepcopy(original)
    if damage=='plan':value['plan_sha256']='0'*64
    if damage=='report':value['original_report_sha256']='0'*64
    if damage=='time':value['completed_at_ms']=value['started_at_ms']-1
    if damage=='bool_time':value['started_at_ms']=True
    if damage=='authority':value['economic_authority']=0
    if damage=='input':value['input_sha256']='0'*64
    if damage=='replay':value['replay']['status']='PASS'
    if damage in ('run_id','manifest'):value['replay']['run_id' if damage=='run_id' else 'manifest_sha256']='0'*64
    if damage=='extra':value['approved']=True
    with pytest.raises(ValueError):validate_artifact(value,**arguments)


def test_native_report_and_false_attestations_are_not_rewritten(retained_risk):
    from quantos.contracts.registered_risk_replay import validate_artifact
    value,args=retained_risk;before=copy.deepcopy(args)
    actual=validate_artifact(value,**args)
    assert args==before
    assert actual['replay']['reproducibility']['original_attestations']['risk_history_ledger_replayed'] is False
    assert 'REGISTERED_RISK_PLAN_UNAVAILABLE' in actual['replay']['limitations']
