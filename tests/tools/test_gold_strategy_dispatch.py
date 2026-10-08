import json
from copy import deepcopy

import pytest

from live.execution.demo_batch_risk import _digest
from tests.live.test_gold_strategy_constraints import case, request_inputs, NOW
from tools.gold_strategy_request import constraints_from_draft


@pytest.fixture(autouse=True)
def _isolated_legacy_executor(tmp_path, monkeypatch):
    from tools import demo_operating_bundle as installer
    executor = tmp_path/'retention-legacy-executor'
    executor.mkdir(mode=0o700)
    monkeypatch.setattr(installer, 'EXECUTOR', executor)


@pytest.mark.parametrize("use_approved_policy,targeted,cli", [(False,False,False), (True,False,False), (True,True,False), (True,True,True)])
def test_retained_approved_decision_uses_original_task_claim_on_lost_response(case, tmp_path, monkeypatch, capsys, use_approved_policy, targeted, cli):
    from tools import gold_strategy_dispatch as tool
    args = request_inputs(case)
    draft = dict(schema='GOLD_DEMO_STRATEGY_ENTRY_DRAFT_V1', status='STRATEGY_ENTRY_DRAFT',
        environment='BINANCE_USDM_DEMO', observed_at_ms=NOW, valid_until_ms=NOW+100,
        strategy_definition_sha256='a'*64, origin_sha256=_digest(args['origin']),
        orders=args['orders'], submission_enabled=False, economic_authority=False, risk_status='NOT_RUN')
    draft['result_hash'] = _digest(draft)
    constraints = constraints_from_draft(draft, now_ms=NOW+10)
    policy = args['issuer_policy']; policy['schema'] = 'DEMO_GOLD_STRATEGY_ISSUER_POLICY_V1'
    policy['bindings']['execution_constraints_sha256'] = _digest(constraints)
    if use_approved_policy:
        from live.execution.demo_issuer_policy import budget_group_binding
        policy['schema'] = 'DEMO_GOLD_STRATEGY_BUDGET_POLICY_V1'
        policy['budget_group'] = dict(group_id='synthetic-dispatch-budget',
            strategy_definition_sha256='a'*64, valid_from_ms=policy['valid_from_ms'],
            valid_until_ms=policy['valid_until_ms'], max_grants=2, max_orders=2,
            max_total_notional='30', max_cancel_grants=2, max_cancel_orders=2)
    runtime = dict(schema='GOLD_STRATEGY_WORKER_INPUT_V1', source_commit='c'*40,
                   observation_sha256='a'*64, trial_manifest_sha256='b'*64)
    decision = dict(schema='GOLD_STRATEGY_CYCLE_DECISION_V1', source_commit='c'*40,
        bundle_sha256=_digest(runtime), account_ref_sha256=policy['account_ref_sha256'],
        status='DECISION_REQUIRES_APPROVAL', draft=draft, execution_constraints=constraints,
        submission_enabled=False, economic_authority=False)
    result = dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1', status='DECISION_RETAINED',
        source_commit='c'*40, bundle_sha256=_digest(runtime), decision_sha256=_digest(decision),
        decision=decision, submission_enabled=False, economic_authority=False)
    result_id = _digest(result)
    approval = dict(schema='GOLD_STRATEGY_TASK_APPROVAL_REFERENCE_V1', source_commit='c'*40,
        result_sha256=result_id, issuer_policy_sha256=_digest(policy),
        kill_switch=args['kill_state'].model_dump(mode='json'))
    for name in ('strategy-decisions', 'tasks', 'task-claims', 'task-results'):
        (tmp_path/name).mkdir()
    (tmp_path/'strategy-decisions'/(result_id+'.json')).write_text(json.dumps(result))
    (tmp_path/'strategy-task-approval.json').write_text(json.dumps(approval))
    if use_approved_policy:
        # One independently installed scope, not a per-decision reference.
        (tmp_path/'strategy-task-approval.json').unlink()
        dispatch_config = dict(schema='GOLD_STRATEGY_APPROVED_POLICY_DISPATCH_V1', source_commit='c'*40,
            account_ref_sha256=policy['account_ref_sha256'],
            budget_group_binding_sha256=budget_group_binding(policy),
            kill_switch=args['kill_state'].model_dump(mode='json'))
        (tmp_path/'strategy-dispatch.json').write_text(json.dumps(dispatch_config))
    (tmp_path/'strategy-runtime.json').write_text(json.dumps(runtime))
    profile_path = tmp_path/'profile.json'; profile_path.write_text(json.dumps(args['profile']))
    monkeypatch.setattr(tool, 'PROFILE', profile_path)
    monkeypatch.setattr(tool.supervisor, 'STATE', tmp_path)
    monkeypatch.setattr(tool.supervisor, 'configuration', lambda _: ({}, policy))
    monkeypatch.setattr(tool.supervisor, '_root_private_directory', lambda _: None)
    monkeypatch.setattr(tool.supervisor, '_read_root_owned_json', lambda path: path.read_bytes())
    monkeypatch.setattr(tool.supervisor, 'now_ms', lambda: NOW+10)
    monkeypatch.setattr(tool.checkpoint.bundle, 'write_new', lambda path, body: path.write_bytes(body))
    selection = {'use_approved_policy': True} if use_approved_policy else {}
    if targeted:
        selection['result_sha256'] = result_id
        path_type = type(tmp_path)
        original_iterdir = path_type.iterdir
        def no_historical_scan(path):
            assert path != tmp_path/'strategy-decisions', 'targeted handoff scanned unrelated decisions'
            return original_iterdir(path)
        monkeypatch.setattr(path_type, 'iterdir', no_historical_scan)
    sends = []
    def interrupted(**kwargs):
        sends.append(kwargs)
        tool.supervisor.claim(tmp_path/'task-claims', {'task_sha256': kwargs['task_sha256']}, identity_field='task_sha256')
        raise OSError('synthetic response loss after original claim')
    monkeypatch.setattr(tool.demo_task, 'run', interrupted)
    def dispatch():
        if not cli:return tool.run(source_commit='c'*40, execute=True, **selection)
        code=tool.main(['--source-commit','c'*40,'--execute','--use-approved-policy',
                        '--result-sha256',selection['result_sha256']])
        value=json.loads(capsys.readouterr().out)
        assert code==(0 if value['status'] in ('TASK_READY','QUERY_ONLY','EXECUTOR_RESULT_RECORDED') else 3)
        return value
    first = dispatch()
    assert first['status'] == 'INPUTS_UNRESOLVED' and first['phase'] == 'ORIGINAL_TASK'
    again = dispatch()
    assert again['status'] == 'QUERY_ONLY' and len(sends) == 1
    task = json.loads(next((tmp_path/'tasks').iterdir()).read_bytes())
    assert task['execution_constraints'] == constraints and task['orders'] == draft['orders']
    assert args['dispatch'].snapshot() == []
    # Even validly rehashed but unapproved decision bytes cannot be consumed.
    policy['bindings']['execution_constraints_sha256'] = 'e'*64
    approval['issuer_policy_sha256'] = _digest(policy)
    (tmp_path/'strategy-task-approval.json').write_text(json.dumps(approval))
    refused = dispatch()
    assert refused['status'] == ('WAITING_INDEPENDENT_APPROVAL' if targeted else 'INPUTS_UNRESOLVED') and len(sends) == 1

    if use_approved_policy:
        before = (tmp_path/'strategy-dispatch.json').read_bytes()
        # A distinct independently approved decision consumes through the same
        # installed dispatcher scope; no new per-decision reference is written.
        (tmp_path/'strategy-task-approval.json').unlink()
        second = deepcopy(result)
        second_draft = second['decision']['draft']
        second_draft['observed_at_ms'] += 1
        second_draft['result_hash'] = _digest({k:v for k,v in second_draft.items() if k != 'result_hash'})
        second['decision']['execution_constraints'] = constraints_from_draft(second_draft, now_ms=NOW+10)
        second['decision_sha256'] = _digest(second['decision'])
        second_id = _digest(second)
        (tmp_path/'strategy-decisions'/(second_id+'.json')).write_text(json.dumps(second))
        if targeted:
            policy['bindings']['execution_constraints_sha256'] = _digest(constraints)
            selection['result_sha256'] = second_id
            pending = dispatch()
            assert pending['status'] == 'WAITING_INDEPENDENT_APPROVAL' and len(sends) == 1
            assert len(list((tmp_path/'tasks').iterdir())) == 1
        policy['bindings']['execution_constraints_sha256'] = _digest(second['decision']['execution_constraints'])
        next_result = dispatch()
        assert next_result['phase'] == 'ORIGINAL_TASK' and len(sends) == 2
        assert len(list((tmp_path/'tasks').iterdir())) == 2
        assert (tmp_path/'strategy-dispatch.json').read_bytes() == before
        assert not (tmp_path/'strategy-task-approval.json').exists()
        again = dispatch()
        assert again['status'] == 'QUERY_ONLY' and len(sends) == 2
        duplicate = {**second, 'extra_fixture_metadata': True}
        duplicate_path = tmp_path/'strategy-decisions'/(_digest(duplicate)+'.json')
        duplicate_path.write_text(json.dumps(duplicate))
        ambiguous = dispatch()
        assert ambiguous['status'] == ('QUERY_ONLY' if targeted else 'INPUTS_UNRESOLVED') and len(sends) == 2
        duplicate_path.unlink()
        policy['budget_group']['max_total_notional'] = '31'
        changed = dispatch()
        assert changed['status'] == 'INPUTS_UNRESOLVED' and len(sends) == 2
