"""Exact decision handoff to an independent approver; never grants permission."""

from copy import deepcopy
import re
from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import (
    GOLD_STRATEGY_BUDGET_POLICY_SCHEMA,
    GOLD_STRATEGY_BUDGET_POLICY_SCHEMA_V2,
    GOLD_BUDGET_POLICY_SCHEMAS,
    budget_group_binding,
    require,
    validate_policy,
)
from tools.gold_strategy_request import constraints_from_draft
from live.execution import demo_gold_budget_scope as scope_contract


def is_scope(policy):
    return scope_contract.is_scope(policy)


def approval_configuration(source_commit):
    """Read current Scope only for independent review, never issuer execution."""
    from tools import demo_supervisor as supervisor
    try:return supervisor.configuration(source_commit)
    except ValueError:
        from tools.gold_strategy_controller import observation_configuration
        registry,policy=observation_configuration(source_commit)
        require(is_scope(policy),'APPROVAL_REQUEST_EXECUTION_CONFIGURATION_REQUIRED')
        scope_contract.current_scope_key(policy,registry,supervisor.now_ms())
        return registry,policy


def scope_runtime(policy,registry,runtime,base_runtime,now_ms):
    from tools.gold_strategy_budget_enrollment import runtime_check
    scope_contract.current_scope_key(policy,registry,now_ms)
    base=runtime if base_runtime is None else base_runtime
    runtime_check(base,policy['executor_release_commit']);runtime_check(runtime,policy['executor_release_commit'])
    require(_digest(base)==policy['scope_enrollment']['runtime_sha256'],'APPROVAL_SCOPE_SIGNED_RUNTIME_CHANGED')
    expected=deepcopy(base)
    if base['schema']=='GOLD_BAR_AUTO_EPISODE_INPUT_V1':expected['episode_id']=runtime.get('episode_id')
    require(runtime==expected,'APPROVAL_SCOPE_EFFECTIVE_RUNTIME_CHANGED')


def candidate_policy(policy,*,registry,request_sha256,constraints_sha256,now_ms):
    """Unsigned data for one exact human decision; this function grants nothing."""
    if is_scope(policy):
        scope_contract.current_scope_key(policy,registry,now_ms)
        proposed={key:deepcopy(policy[key]) for key in scope_contract.COMMON}
        proposed.update(schema=GOLD_STRATEGY_BUDGET_POLICY_SCHEMA,budget_group=deepcopy(policy['budget_group']))
        if policy['schema']==scope_contract.SCHEMA_V2:
            proposed.update(schema=GOLD_STRATEGY_BUDGET_POLICY_SCHEMA_V2,original_scope=deepcopy(policy))
    else:validate_policy(policy);proposed=deepcopy(policy)
    proposed['policy_id']='gold-decision-'+request_sha256
    proposed['bindings']['execution_constraints_sha256']=constraints_sha256
    validate_policy(proposed)
    return proposed


def scope_decision_evidence(decision,runtime):
    """A Scope decision must identify the original market evidence to review."""
    names=['bar_signal_sha256']
    if runtime['schema']=='GOLD_BAR_RETAINED_EPISODE_INPUT_V1':
        require('bar_continuation_sha256' not in decision,'APPROVAL_SCOPE_BAR_EVIDENCE')
    else:names.append('bar_continuation_sha256')
    require(all(type(decision.get(name)) is str and re.fullmatch('[a-f0-9]{64}',decision[name])
        for name in names),'APPROVAL_SCOPE_BAR_EVIDENCE')


def prepare(result, *, runtime, policy, now_ms, registry=None, base_runtime=None):
    from tools.gold_strategy_controller import validate_result

    scoped=is_scope(policy)
    if scoped:scope_runtime(policy,registry,runtime,base_runtime,now_ms)
    else:
        validate_policy(policy)
        require(policy["schema"] in GOLD_BUDGET_POLICY_SCHEMAS, "APPROVAL_REQUEST_GROUP")
    require(
        type(now_ms) is int and policy["valid_from_ms"] <= now_ms < policy["valid_until_ms"],
        "APPROVAL_REQUEST_POLICY_EXPIRED",
    )
    require(runtime.get("source_commit") == policy["executor_release_commit"], "APPROVAL_REQUEST_RELEASE")
    decision = validate_result(
        result,
        source_commit=policy["executor_release_commit"],
        bundle_sha256=_digest(runtime),
        account=policy["account_ref_sha256"],
    )
    require(decision.get("status") == "DECISION_REQUIRES_APPROVAL", "APPROVAL_REQUEST_NO_ACTION")
    if scoped:
        require(decision['draft'].get('schema') in ('GOLD_BAR_DEMO_ENTRY_DRAFT_V1',
            'GOLD_BAR_DEMO_EXIT_DRAFT_V1'),'APPROVAL_SCOPE_BAR_DECISION_REQUIRED')
        scope_decision_evidence(decision,runtime)
    constraints = constraints_from_draft(decision["draft"], now_ms=now_ms)
    require(constraints == decision["execution_constraints"], "APPROVAL_REQUEST_CONSTRAINTS")
    require(
        constraints["strategy_definition_sha256"] == policy["budget_group"]["strategy_definition_sha256"],
        "APPROVAL_REQUEST_STRATEGY",
    )
    if scoped:
        from live.execution.demo_request_inputs import order_scope
        from services.execution.perpetual_ledger_v2 import ledger_decimal_context
        declared=candidate_policy(policy,registry=registry,request_sha256=_digest(result),
            constraints_sha256=_digest(constraints),now_ms=now_ms)
        with ledger_decimal_context():order_scope(decision['draft']['orders'],declared)
    # This records the existing budget identity, not remaining capacity. Only
    # the original issuer store may count claims and approve a decision.
    body = dict(
        schema="GOLD_STRATEGY_INDEPENDENT_APPROVAL_REQUEST_V2" if scoped else "GOLD_STRATEGY_INDEPENDENT_APPROVAL_REQUEST_V1",
        status="INDEPENDENT_APPROVAL_REQUIRED",
        source_commit=policy["executor_release_commit"],
        account_ref_sha256=policy["account_ref_sha256"],
        result_sha256=_digest(result),
        runtime_sha256=_digest(runtime),
        current_policy_sha256=_digest(policy),
        budget_group_binding_sha256=scope_contract.budget_binding(policy) if scoped else budget_group_binding(policy),
        budget_group=deepcopy(policy["budget_group"]),
        decision=deepcopy(decision),
        constraints_sha256=_digest(constraints),
        valid_until_ms=min(policy["valid_until_ms"], constraints["valid_until_ms"]),
        approval_granted=False,
        submission_enabled=False,
        economic_authority=False,
    )
    return body | {"request_sha256": _digest(body)}


def review(request, *, result, runtime, policy, now_ms, registry=None, base_runtime=None):
    """Check against independently loaded originals, never trust embedded copies alone."""
    expected = prepare(result, runtime=runtime, policy=policy, now_ms=now_ms,registry=registry,base_runtime=base_runtime)
    require(type(request) is dict and request == expected, "APPROVAL_REQUEST_CHANGED")
    return dict(
        schema="GOLD_STRATEGY_APPROVAL_REQUEST_REVIEW_V1",
        status="REQUEST_REVIEWED_NOT_APPROVED",
        request_sha256=expected["request_sha256"],
        result_sha256=expected["result_sha256"],
        budget_group_binding_sha256=expected["budget_group_binding_sha256"],
        constraints_sha256=expected["constraints_sha256"],
        valid_until_ms=expected["valid_until_ms"],
        approval_granted=False,
        submission_enabled=False,
        economic_authority=False,
    )


def policy_candidate(request, *, result, runtime, policy, now_ms, registry=None, base_runtime=None):
    """Draft exact-decision policy data; installation and approval remain external."""
    checked = review(request, result=result, runtime=runtime, policy=policy, now_ms=now_ms,
        registry=registry,base_runtime=base_runtime)
    proposed=candidate_policy(policy,registry=registry,request_sha256=checked['request_sha256'],
        constraints_sha256=checked['constraints_sha256'],now_ms=now_ms)
    require(
        budget_group_binding(proposed) == checked["budget_group_binding_sha256"], "APPROVAL_CANDIDATE_BUDGET_CHANGED"
    )
    return dict(
        schema="GOLD_STRATEGY_POLICY_CANDIDATE_V2" if is_scope(policy) else "GOLD_STRATEGY_POLICY_CANDIDATE_V1",
        status="POLICY_CANDIDATE_REQUIRES_INDEPENDENT_APPROVAL",
        request_sha256=checked["request_sha256"],
        result_sha256=checked["result_sha256"],
        replaces_policy_sha256=_digest(policy),
        policy_candidate=proposed,
        policy_candidate_sha256=_digest(proposed),
        budget_group_binding_sha256=checked["budget_group_binding_sha256"],
        decision_valid_until_ms=checked["valid_until_ms"],
        approval_granted=False,
        submission_enabled=False,
        economic_authority=False,
    )


def executor_evidence_root(registry):
    """Market evidence belongs to the registered executor, not the controller."""
    from tools.demo_operating_bundle import EXECUTOR
    from tools.demo_resident_recovery import _check_path
    require(type(registry) is dict and type(registry.get('executor_uid')) is int
        and registry['executor_uid'] >= 0, 'APPROVAL_EXECUTOR_IDENTITY')
    require(EXECUTOR.is_absolute() and EXECUTOR.resolve() == EXECUTOR, 'APPROVAL_EXECUTOR_ROOT')
    _check_path(EXECUTOR, registry['executor_uid'], directory=True)
    return EXECUTOR


def review_bar_prefix(*, root, runtime, decision, source_commit, uid):
    """Rebuild retained market inputs without capturing, approving or signing."""
    from tools.gold_bar_fresh_inputs import FreshEpisode
    from tools.gold_bar_episode_tail import restore_snapshot
    require(runtime.get('schema') in ('GOLD_BAR_AUTO_EPISODE_INPUT_V1', 'GOLD_BAR_FRESH_EPISODE_INPUT_V1'),
        'APPROVAL_BAR_RUNTIME')
    episode = FreshEpisode(root, runtime, source_commit=source_commit, uid=uid, read_only=True)
    original = episode.recheck()
    engine = restore_snapshot(root, bundle_sha256=_digest(runtime), engine=episode.engine,
        entry_decision_ms=episode.entry_decision_ms, snapshot_sha256=decision['bar_continuation_sha256'])
    require(engine.verified_definition()['definition_hash'] == decision['strategy_definition_sha256']
        and _digest(engine.signal_at(episode.entry_decision_ms)) == decision['bar_signal_sha256'],
        'APPROVAL_BAR_DEFINITION_OR_SIGNAL')
    require(type(decision['observed_at_ms']) is int
        and engine._cursor.last_received <= decision['observed_at_ms']
        and all(bar.available_ms <= decision['observed_at_ms'] for bar in engine._cursor.bars),
        'APPROVAL_BAR_FUTURE_INPUT')
    require(episode.recheck() == original, 'APPROVAL_BAR_ORIGINAL_CHANGED')
    return dict(snapshot_sha256=decision['bar_continuation_sha256'],
        definition_hash=decision['strategy_definition_sha256'], bar_signal_sha256=decision['bar_signal_sha256'],
        original=original, approval_granted=False, submission_enabled=False)


def review_retained(*, source_commit, stored_request_sha256, prepare_candidate=False):
    """Read a fixed controller request and its original result under existing custody checks."""
    import re
    from services.governance.json_integrity import load_json_without_duplicate_keys as decode
    from tools import demo_supervisor as supervisor, gold_pilot_retention as retention

    require(
        type(stored_request_sha256) is str and re.fullmatch("[a-f0-9]{64}", stored_request_sha256),
        "APPROVAL_REQUEST_STORAGE_ID",
    )
    require(type(prepare_candidate) is bool, "APPROVAL_CANDIDATE_MODE")
    registry, policy = approval_configuration(source_commit)
    request_raw = retention.read_controller_approval(supervisor.STATE, stored_request_sha256,
        source_commit=source_commit, account=policy['account_ref_sha256'])
    request = decode(request_raw)
    require(type(request) is dict and _digest(request) == stored_request_sha256, "APPROVAL_REQUEST_STORAGE_CHANGED")
    identity = request.get("result_sha256")
    require(type(identity) is str and re.fullmatch("[a-f0-9]{64}", identity), "APPROVAL_REQUEST_RESULT_ID")
    result_raw = retention.read_controller_result(supervisor.STATE, identity,
        source_commit=source_commit, account=policy['account_ref_sha256'])
    result = decode(result_raw)
    require(_digest(result) == identity, "APPROVAL_REQUEST_RESULT_CHANGED")
    runtime_path = supervisor.STATE / "strategy-runtime.json"
    runtime_raw = supervisor._read_root_owned_json(runtime_path)
    base_runtime = decode(runtime_raw)
    def current_runtime():
        if base_runtime.get("schema") == "GOLD_BAR_AUTO_EPISODE_INPUT_V1":
            from tools.gold_bar_rotation import load
            return load(root=supervisor.STATE, base=base_runtime,
                account=policy["account_ref_sha256"], read_only=True)
        return base_runtime
    effective_runtime = current_runtime()
    def prefix_review():
        if is_scope(policy):
            scope_decision_evidence(result['decision'],effective_runtime)
            from tools.gold_strategy_dispatch import review_bar_runtime
            return review_bar_runtime(root=executor_evidence_root(registry),runtime=effective_runtime,
                decision=result['decision'],source_commit=source_commit,uid=registry['executor_uid'])
        if 'bar_continuation_sha256' not in result['decision']:
            return None
        return review_bar_prefix(root=executor_evidence_root(registry), runtime=effective_runtime,
            decision=result['decision'], source_commit=source_commit, uid=registry['executor_uid'])
    prefix = prefix_review()
    consumer = policy_candidate if prepare_candidate else review
    checked = consumer(request, result=result, runtime=effective_runtime, policy=policy, now_ms=supervisor.now_ms(),
        registry=registry,base_runtime=base_runtime)
    if prepare_candidate and is_scope(policy):
        checked=checked|{'dispatcher_configuration':scope_dispatch_configuration(policy,now_ms=supervisor.now_ms())}
    require(
        approval_configuration(source_commit) == (registry, policy)
        and retention.read_controller_approval(supervisor.STATE, stored_request_sha256,
            source_commit=source_commit, account=policy['account_ref_sha256']) == request_raw
        and retention.read_controller_result(supervisor.STATE, identity,
            source_commit=source_commit, account=policy['account_ref_sha256']) == result_raw
        and supervisor._read_root_owned_json(runtime_path) == runtime_raw
        and current_runtime() == effective_runtime
        and prefix_review() == prefix,
        "APPROVAL_REQUEST_INPUTS_CHANGED",
    )
    # Rebuilding the retained prefix can consume the remaining decision lifetime.
    # Check the original deadline again after all potentially slow input checks.
    review(request, result=result, runtime=effective_runtime, policy=policy, now_ms=supervisor.now_ms(),
        registry=registry,base_runtime=base_runtime)
    if prefix is not None:
        checked = checked | {"bar_prefix_review": prefix}
    return checked | {"stored_request_sha256": stored_request_sha256}


def scope_dispatch_configuration(policy,*,now_ms):
    """Use the original signed stop image, never default/bootstrap/reset state."""
    import hashlib
    import tempfile
    from pathlib import Path
    from services.risk.pre_trade.kill_switch_store import KillSwitchStateStore
    from tools import demo_operator_setup as setup, demo_operating_bundle as installer
    path=installer.EXECUTOR/'kill-switch.sqlite3';image=setup._stop_image(path=path)
    require(hashlib.sha256(image).hexdigest()==policy['scope_enrollment']['stop_image_sha256'],
        'APPROVAL_SCOPE_STOP_IMAGE_CHANGED')
    with tempfile.TemporaryDirectory(prefix='quantos-scope-stop-review-') as directory:
        copy=Path(directory)/'stop.sqlite3';copy.write_bytes(image);copy.chmod(0o600)
        store=KillSwitchStateStore(str(copy),read_only=True)
        try:snapshot=store.verified_snapshot()
        finally:store.close()
    state=snapshot['state']
    require(not state.tripped and max(snapshot['recorded_at'],state.armed_at,state.reset_at)<=now_ms,
        'APPROVAL_SCOPE_STOP_ACTIVE_OR_FUTURE')
    require(setup._stop_image(path=path)==image,'APPROVAL_SCOPE_STOP_IMAGE_CHANGED')
    return dict(schema='GOLD_STRATEGY_APPROVED_POLICY_DISPATCH_V1',source_commit=policy['executor_release_commit'],
        account_ref_sha256=policy['account_ref_sha256'],budget_group_binding_sha256=scope_contract.budget_binding(policy),
        kill_switch=state.model_dump(mode='json'))


def main(argv=None):
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Review retained gold approval material without approving or submitting."
    )
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--stored-request-sha256", required=True)
    parser.add_argument(
        "--prepare-policy-candidate",
        action="store_true",
        help="Return an unapproved candidate; never install or sign it",
    )
    args = parser.parse_args(argv)
    try:
        result = review_retained(
            source_commit=args.source_commit,
            stored_request_sha256=args.stored_request_sha256,
            prepare_candidate=args.prepare_policy_candidate,
        )
    except (ValueError, OSError, KeyError, TypeError):
        print(
            json.dumps(
                dict(status="APPROVAL_REQUEST_REVIEW_REJECTED", approval_granted=False, submission_enabled=False)
            )
        )
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
