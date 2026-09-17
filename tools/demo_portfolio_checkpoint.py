"""Root-private portfolio decision retention and original task materialization.

Called only by a trusted controller after source verification and target build.
No CLI, execution, signing or implicit retry is provided. Existing task claims
remain the send barrier; a decision checkpoint never proves a fill.
"""
import os
import stat

from live.execution.demo_issuer_policy import require, validate_policy
from live.execution.demo_portfolio_task import strategy_id
from live.execution.demo_request_inputs import task_check
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.ai_factory_store import digest as research_digest
from tools import demo_supervisor as supervisor, demo_operating_bundle as bundle
from tools.demo_cycle_checkpoint import cycle_lock
from tools.demo_operator_setup import canonical, digest


def _directory(path):
    path.mkdir(mode=0o700,exist_ok=True)
    supervisor._root_private_directory(path)


def _budget(directory):
    count=size=0
    for path in directory.iterdir():
        info=path.lstat()
        require(stat.S_ISREG(info.st_mode) and not path.is_symlink(),'PORTFOLIO_CHECKPOINT_FILE')
        count+=1;size+=info.st_size
        require(count<=16000 and size<=256*1024*1024,'PORTFOLIO_CHECKPOINT_BUDGET')


def retain(*,allocation,forecast,built,policy,now_ms):
    """Keep the first result per account/model/hour, then install that exact task.

    Reentry cannot regenerate an expired task or reinterpret an existing claim.
    A caller must still authenticate source files and hold the controller lease
    through dispatch; this function neither authenticates a model nor dispatches.
    """
    require(os.geteuid()==0,'PORTFOLIO_CHECKPOINT_ROOT_REQUIRED')
    validate_policy(policy)
    sid=strategy_id(allocation)
    require(policy['bindings']['strategy_id']==sid and
        policy['account_ref_sha256']==allocation['account_ref_sha256'] and
        policy['executor_release_commit']==allocation['source_commit'],'PORTFOLIO_CHECKPOINT_POLICY')
    require(forecast.get('result_hash')==research_digest({k:v for k,v in forecast.items() if k!='result_hash'}) and
        forecast['model_hash']==allocation['model_hash'],'PORTFOLIO_CHECKPOINT_FORECAST')
    identity=digest({'account':allocation['account_ref_sha256'],'model':allocation['model_hash'],
        'slot_ms':forecast['slot_ms']})
    supervisor._root_private_directory(supervisor.STATE)
    area=supervisor.STATE/'portfolio-decisions';_directory(area)
    with cycle_lock(area):
        _budget(area)
        path=area/(identity+'.json')
        if not path.exists():
            require(built.get('schema')=='DEMO_PORTFOLIO_TASK_BUILD_V1' and
                built.get('allocation_sha256')==digest(allocation) and
                built.get('submission_enabled') is False and built.get('economic_authority') is False,
                'PORTFOLIO_CHECKPOINT_BUILD')
            intent=built['intent']
            require(intent.get('result_hash')==research_digest({k:v for k,v in intent.items() if k!='result_hash'}) and
                intent['forecast_sha256']==forecast['result_hash'] and intent['model_hash']==allocation['model_hash'] and
                intent['observed_at_ms']<=now_ms<intent['valid_until_ms'],'PORTFOLIO_CHECKPOINT_INTENT')
            task=built['task']
            if task is not None:
                task_check(task,policy=policy,source_commit=allocation['source_commit'],now_ms=now_ms)
                require(built['task_sha256']==digest(task) and
                    built['request_build']['precheck_status']=='RISK_CHECKS_PASSED','PORTFOLIO_CHECKPOINT_TASK')
            else:
                require(built['status']=='NO_CHANGE','PORTFOLIO_CHECKPOINT_NO_TASK')
            value={'schema':'DEMO_PORTFOLIO_DECISION_CHECKPOINT_V1','identity':identity,
                'allocation_sha256':digest(allocation),'forecast_sha256':forecast['result_hash'],
                'build_sha256':digest(built),'task':task,'observed_at_ms':now_ms}
            value['record_sha256']=digest(value)
            body=canonical(value)
            require(len(body)<=65536,'PORTFOLIO_CHECKPOINT_SIZE')
            bundle.write_new(path,body)
        value=load_json_without_duplicate_keys(supervisor._read_root_owned_json(path))
        require(value.get('record_sha256')==digest({k:v for k,v in value.items() if k!='record_sha256'}) and
            value.get('schema')=='DEMO_PORTFOLIO_DECISION_CHECKPOINT_V1' and value['identity']==identity and
            value['allocation_sha256']==digest(allocation) and value['forecast_sha256']==forecast['result_hash'],
            'PORTFOLIO_CHECKPOINT_CONFLICT')
        task=value['task']
        result={'schema':'DEMO_PORTFOLIO_TASK_RETENTION_V1','decision_sha256':identity,
            'checkpoint_sha256':value['record_sha256'],'task_sha256':None if task is None else digest(task),
            'status':'NO_CHANGE','submission_enabled':False,'economic_authority':False}
        if task is None:return result
        task_hash=digest(task)
        claim=supervisor.STATE/'task-claims'/(task_hash+'.json')
        if os.path.lexists(claim):
            # Partial, malformed or lost-response claims also prohibit resending.
            return {**result,'status':'QUERY_ONLY'}
        if now_ms>=task['valid_until_ms']:
            return {**result,'status':'EXPIRED_QUERY_ONLY'}
        task_check(task,policy=policy,source_commit=allocation['source_commit'],now_ms=now_ms)
        directory=supervisor.STATE/'tasks';_directory(directory);_budget(directory)
        target=directory/(task_hash+'.json');body=canonical(task)
        if target.exists():
            require(supervisor._read_root_owned_json(target)==body,'PORTFOLIO_CHECKPOINT_TASK_CONFLICT')
        else:bundle.write_new(target,body)
        return {**result,'status':'TASK_READY'}
