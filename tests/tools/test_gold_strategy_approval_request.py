from copy import deepcopy
import os
import pytest
from live.execution.demo_batch_risk import _digest
from tests.live.test_gold_strategy_constraints import case as case, request_inputs, NOW
from tools.gold_strategy_request import constraints_from_draft


@pytest.fixture(autouse=True)
def _isolated_legacy_executor(tmp_path, monkeypatch):
    from tools import demo_operating_bundle as installer
    executor = tmp_path/'retention-legacy-executor'
    executor.mkdir(mode=0o700)
    monkeypatch.setattr(installer, 'EXECUTOR', executor)


def inputs(case):
    args = request_inputs(case)
    policy = deepcopy(args["issuer_policy"])
    policy["schema"] = "DEMO_GOLD_STRATEGY_BUDGET_POLICY_V1"
    policy["bindings"]["execution_constraints_sha256"] = "f" * 64
    policy["budget_group"] = dict(
        group_id="approval-request-test",
        strategy_definition_sha256="a" * 64,
        **{
            k: policy[k]
            for k in (
                "valid_from_ms",
                "valid_until_ms",
                "max_grants",
                "max_orders",
                "max_total_notional",
                "max_cancel_grants",
                "max_cancel_orders",
            )
        },
    )
    runtime = dict(
        schema="GOLD_STRATEGY_WORKER_INPUT_V1",
        source_commit="c" * 40,
        observation_sha256="a" * 64,
        trial_manifest_sha256="b" * 64,
    )
    draft = dict(
        schema="GOLD_DEMO_STRATEGY_ENTRY_DRAFT_V1",
        status="STRATEGY_ENTRY_DRAFT",
        environment="BINANCE_USDM_DEMO",
        observed_at_ms=NOW,
        valid_until_ms=NOW + 100,
        strategy_definition_sha256="a" * 64,
        origin_sha256=_digest(args["origin"]),
        orders=args["orders"],
        submission_enabled=False,
        economic_authority=False,
        risk_status="NOT_RUN",
    )
    draft["result_hash"] = _digest(draft)
    constraints = constraints_from_draft(draft, now_ms=NOW + 10)
    decision = dict(
        schema="GOLD_STRATEGY_CYCLE_DECISION_V1",
        source_commit="c" * 40,
        bundle_sha256=_digest(runtime),
        account_ref_sha256=policy["account_ref_sha256"],
        status="DECISION_REQUIRES_APPROVAL",
        draft=draft,
        execution_constraints=constraints,
        submission_enabled=False,
        economic_authority=False,
    )
    result = dict(
        schema="GOLD_STRATEGY_WORKER_RESULT_V1",
        status="DECISION_RETAINED",
        source_commit="c" * 40,
        bundle_sha256=_digest(runtime),
        decision_sha256=_digest(decision),
        decision=decision,
        submission_enabled=False,
        economic_authority=False,
    )
    return result, runtime, policy


def test_request_binds_exact_decision_and_preserves_shared_budget(case):
    from tools.gold_strategy_approval_request import prepare

    result, runtime, policy = inputs(case)
    before = deepcopy((result, runtime, policy))
    value = prepare(result, runtime=runtime, policy=policy, now_ms=NOW + 10)
    assert value["status"] == "INDEPENDENT_APPROVAL_REQUIRED"
    assert value["result_sha256"] == _digest(result)
    assert value["constraints_sha256"] == _digest(result["decision"]["execution_constraints"])
    assert value["budget_group"] == policy["budget_group"]
    assert value["submission_enabled"] is False and value["approval_granted"] is False
    assert (result, runtime, policy) == before
    assert prepare(result, runtime=runtime, policy=policy, now_ms=NOW + 20) == value


@pytest.mark.parametrize("damage", ["expired", "account", "strategy", "constraints", "policy_expired"])
def test_invalid_request_never_creates_approval(case, damage):
    from tools.gold_strategy_approval_request import prepare

    result, runtime, policy = inputs(case)
    now = NOW + 10
    if damage == "expired":
        now = NOW + 101
    if damage == "account":
        policy["account_ref_sha256"] = "e" * 64
    if damage == "strategy":
        policy["budget_group"]["strategy_definition_sha256"] = "e" * 64
    if damage == "constraints":
        result["decision"]["execution_constraints"]["orders_sha256"] = "e" * 64
    if damage == "policy_expired":
        now = policy["valid_until_ms"] + 1
    with pytest.raises(ValueError):
        prepare(result, runtime=runtime, policy=policy, now_ms=now)


def test_controller_retains_one_request_without_task_or_policy_mutation(case, tmp_path, monkeypatch):
    import json
    from tools import gold_strategy_controller as controller

    result, runtime, policy = inputs(case)
    before = deepcopy(policy)
    monkeypatch.setattr(controller.supervisor, "STATE", tmp_path)
    monkeypatch.setattr(controller.supervisor, "now_ms", lambda: NOW + 10)
    first = controller.retain_approval_request(result, bundle=runtime, policy=policy)
    assert controller.retain_approval_request(result, bundle=runtime, policy=policy) == first
    files = list((tmp_path / "strategy-approval-requests").iterdir())
    assert len(files) == 1 and json.loads(files[0].read_bytes()) == first
    assert not (tmp_path / "tasks").exists() and not (tmp_path / "task-claims").exists()
    assert policy == before


@pytest.mark.parametrize("damage", [None, "request", "rehash_budget", "result", "expired", "policy"])
def test_independent_receiver_revalidates_original_material(case, damage):
    from tools.gold_strategy_approval_request import prepare, review

    result, runtime, policy = inputs(case)
    request = prepare(result, runtime=runtime, policy=policy, now_ms=NOW + 10)
    now = NOW + 20
    if damage == "request":
        request["approval_granted"] = True
    if damage == "rehash_budget":
        request["budget_group"]["max_total_notional"] = "999"
        request["request_sha256"] = _digest({k: v for k, v in request.items() if k != "request_sha256"})
    if damage == "result":
        result["decision"]["draft"]["orders"][0]["quantity"] = "999"
    if damage == "expired":
        now = NOW + 101
    if damage == "policy":
        policy["bindings"]["execution_constraints_sha256"] = "e" * 64
    if damage:
        with pytest.raises(ValueError):
            review(request, result=result, runtime=runtime, policy=policy, now_ms=now)
    else:
        checked = review(request, result=result, runtime=runtime, policy=policy, now_ms=now)
        assert checked["status"] == "REQUEST_REVIEWED_NOT_APPROVED"
        assert checked["request_sha256"] == request["request_sha256"]
        assert checked["approval_granted"] is False and checked["submission_enabled"] is False


@pytest.mark.parametrize("candidate", [False, True])
@pytest.mark.parametrize("damage", [None, "request_file", "runtime_during_review"])
def test_retained_review_cli_reads_originals_and_detects_changes(
    case, tmp_path, monkeypatch, capsys, damage, candidate
):
    import json
    from tools import gold_strategy_approval_request as tool
    from tools import demo_supervisor as supervisor

    result, runtime, policy = inputs(case)
    request = tool.prepare(result, runtime=runtime, policy=policy, now_ms=NOW + 10)
    for directory in ("strategy-approval-requests", "strategy-decisions"):
        (tmp_path / directory).mkdir()
    request_path = tmp_path / "strategy-approval-requests" / (_digest(request) + ".json")
    result_path = tmp_path / "strategy-decisions" / (_digest(result) + ".json")
    runtime_path = tmp_path / "strategy-runtime.json"
    for path, body in ((request_path, request), (result_path, result), (runtime_path, runtime)):
        path.write_text(json.dumps(body))
    monkeypatch.setattr(supervisor, "STATE", tmp_path)
    monkeypatch.setattr(supervisor, "_read_root_owned_json", lambda path: path.read_bytes())
    monkeypatch.setattr(supervisor, "now_ms", lambda: NOW + 20)
    calls = []

    def config(source):
        calls.append(source)
        if damage == "runtime_during_review" and len(calls) == 2:
            runtime_path.write_text("{}")
        return {}, policy

    monkeypatch.setattr(supervisor, "configuration", config)
    if damage == "request_file":
        request_path.write_text("{}")
    code = tool.main(
        ["--source-commit", "c" * 40, "--stored-request-sha256", _digest(request)]
        + (["--prepare-policy-candidate"] if candidate else [])
    )
    output = json.loads(capsys.readouterr().out)
    assert code == (3 if damage else 0)
    assert output["status"] == (
        "APPROVAL_REQUEST_REVIEW_REJECTED"
        if damage
        else "POLICY_CANDIDATE_REQUIRES_INDEPENDENT_APPROVAL"
        if candidate
        else "REQUEST_REVIEWED_NOT_APPROVED"
    )
    assert output["approval_granted"] is False and output["submission_enabled"] is False
    assert not (tmp_path / "tasks").exists()


def test_policy_candidate_pins_decision_without_resetting_group_or_approving(case):
    from tools.gold_strategy_approval_request import prepare, policy_candidate
    from live.execution.demo_issuer_policy import budget_group_binding, validate_policy, strategy_constraints_check

    result, runtime, policy = inputs(case)
    request = prepare(result, runtime=runtime, policy=policy, now_ms=NOW + 10)
    before = deepcopy(policy)
    candidate = policy_candidate(request, result=result, runtime=runtime, policy=policy, now_ms=NOW + 20)
    assert candidate["status"] == "POLICY_CANDIDATE_REQUIRES_INDEPENDENT_APPROVAL"
    assert candidate["approval_granted"] is False
    proposed = candidate["policy_candidate"]
    validate_policy(proposed)
    strategy_constraints_check(
        result["decision"]["execution_constraints"],
        policy=proposed,
        orders=result["decision"]["draft"]["orders"],
        now_ms=NOW + 20,
    )
    assert proposed["policy_id"] != policy["policy_id"]
    assert budget_group_binding(proposed) == budget_group_binding(policy)
    assert {k for k in policy if proposed[k] != policy[k]} == {"policy_id", "bindings"}
    assert policy == before
    assert policy_candidate(request, result=result, runtime=runtime, policy=policy, now_ms=NOW + 21) == candidate


def test_candidate_after_independent_fixture_approval_enters_original_issuer(case, tmp_path):
    """Actual request/session/issuer path, isolated keys, no transport or installed policy."""
    import json
    from tools.gold_strategy_approval_request import prepare, policy_candidate
    from live.execution import demo_request_inputs as builder, demo_issuer as issuer
    from live.execution.demo_execution_session import ExecutionSession
    from services.validation import demo_submission

    private, values, _, registry, _, issuer_store = case
    result, runtime, policy = inputs(case)
    request = prepare(result, runtime=runtime, policy=policy, now_ms=NOW + 10)
    candidate = policy_candidate(request, result=result, runtime=runtime, policy=policy, now_ms=NOW + 11)
    # This explicit isolated fixture supplies the independent approval outcome;
    # the candidate generator is never invoked as a live policy installer.
    approved_policy = candidate["policy_candidate"]
    args = request_inputs(case)
    args["issuer_policy"] = approved_policy
    args["execution_constraints"] = result["decision"]["execution_constraints"]
    built = builder.build(**args)
    assert built["precheck_status"] == "RISK_CHECKS_PASSED"
    session = ExecutionSession(
        **{k: values[k] for k in ("dispatch", "recovery", "oms")}, evidence_directory=tmp_path / "approval-handoff"
    )
    handoff = session.propose(json.dumps(built["request"]).encode(), now_ms=NOW + 12)
    grant = issuer.issue_submission(
        handoff, policy=approved_policy, registry=registry, key=private, store=issuer_store, now_ms=NOW + 13
    )
    verified = demo_submission.verify(
        grant["envelope"],
        registry=registry,
        expected_context=handoff["proposal"]["context"],
        orders=args["orders"],
        now_ms=NOW + 14,
    )
    assert verified["signature_verified"] is True
    assert candidate["approval_granted"] is False
    assert values["dispatch"].snapshot() == [] and values["oms"].orders() == []


@pytest.mark.parametrize('prefix', [False, True])
@pytest.mark.parametrize('damage', [None, 'stale_request', 'rotation_during_review'])
def test_independent_review_resolves_current_rotated_runtime(case, tmp_path, monkeypatch, damage, prefix):
    import json
    from tools import gold_strategy_approval_request as tool, gold_bar_rotation as rotation
    from tools import demo_supervisor as supervisor
    from tests.tools.test_gold_bar_rotation import bundle, outcome

    result, _, policy = inputs(case)
    from tools import demo_operating_bundle as installer
    executor = tmp_path/'executor'; executor.mkdir(mode=0o700)
    monkeypatch.setattr(installer, 'EXECUTOR', executor)
    registry = {'executor_uid': os.getuid()}
    base = bundle()
    account = policy['account_ref_sha256']
    monkeypatch.setattr(supervisor, '_root_private_directory', lambda _: None)
    monkeypatch.setattr(supervisor, '_read_root_owned_json', lambda path: path.read_bytes())
    prior = outcome(base)
    prior['decision']['account_ref_sha256'] = account
    prior['decision_sha256'] = _digest(prior['decision'])
    current = rotation.advance(root=tmp_path, base=base, account=account, current=base, result=prior)
    requested_runtime = base if damage == 'stale_request' else current
    prefix_calls = []
    if prefix:
        result['decision']['bar_continuation_sha256'] = 'a'*64
    def prefix_check(**kwargs):
        prefix_calls.append(kwargs)
        assert kwargs['runtime'] == current
        assert kwargs['decision']['bar_continuation_sha256'] == 'a'*64
        return {'fixture': 'verified prefix'}
    monkeypatch.setattr(tool, 'review_bar_prefix', prefix_check)
    result['bundle_sha256'] = _digest(requested_runtime)
    result['decision']['bundle_sha256'] = _digest(requested_runtime)
    result['decision_sha256'] = _digest(result['decision'])
    request = tool.prepare(result, runtime=requested_runtime, policy=policy, now_ms=NOW+10)
    for name, body in (('strategy-runtime.json', base),
                       ('strategy-decisions/'+_digest(result)+'.json', result),
                       ('strategy-approval-requests/'+_digest(request)+'.json', request)):
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body))
    monkeypatch.setattr(supervisor, 'STATE', tmp_path)
    monkeypatch.setattr(supervisor, 'now_ms', lambda: NOW+20)
    calls = []
    def config(source):
        calls.append(source)
        if damage == 'rotation_during_review' and len(calls) == 2:
            next_result = outcome(current)
            next_result['decision']['account_ref_sha256'] = account
            next_result['decision_sha256'] = _digest(next_result['decision'])
            rotation.advance(root=tmp_path, base=base, account=account, current=current, result=next_result)
        return registry, policy
    monkeypatch.setattr(supervisor, 'configuration', config)
    if damage:
        with pytest.raises(ValueError):
            tool.review_retained(source_commit='c'*40, stored_request_sha256=_digest(request))
    else:
        checked = tool.review_retained(source_commit='c'*40, stored_request_sha256=_digest(request))
        assert checked['status'] == 'REQUEST_REVIEWED_NOT_APPROVED'
        assert checked['approval_granted'] is False and checked['submission_enabled'] is False
        assert len(prefix_calls) == (2 if prefix else 0)
        if prefix:
            assert checked['bar_prefix_review'] == {'fixture': 'verified prefix'}
    assert not (tmp_path/'tasks').exists()


@pytest.mark.parametrize("candidate", [False, True])
@pytest.mark.parametrize("finish_offset", [99, 100, 101])
def test_retained_review_rechecks_deadline_after_final_input_review(
    case, tmp_path, monkeypatch, candidate, finish_offset
):
    import json
    from tools import gold_strategy_approval_request as tool, demo_supervisor as supervisor

    result, runtime, policy = inputs(case)
    from tools import demo_operating_bundle as installer
    executor = tmp_path/'executor'; executor.mkdir(mode=0o700)
    monkeypatch.setattr(installer, 'EXECUTOR', executor)
    registry = {'executor_uid': os.getuid()}
    result["decision"]["bar_continuation_sha256"] = "a" * 64
    result["decision_sha256"] = _digest(result["decision"])
    request = tool.prepare(result, runtime=runtime, policy=policy, now_ms=NOW + 10)
    for name, body in (
        ("strategy-runtime.json", runtime),
        ("strategy-decisions/" + _digest(result) + ".json", result),
        ("strategy-approval-requests/" + _digest(request) + ".json", request),
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body))
    clock = [NOW + 20]
    calls = []
    def slow_prefix(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            clock[0] = NOW + finish_offset
        return {"fixture": "same retained prefix"}
    monkeypatch.setattr(tool, "review_bar_prefix", slow_prefix)
    monkeypatch.setattr(supervisor, "STATE", tmp_path)
    monkeypatch.setattr(supervisor, "_read_root_owned_json", lambda path: path.read_bytes())
    monkeypatch.setattr(supervisor, "configuration", lambda source: (registry, policy))
    monkeypatch.setattr(supervisor, "now_ms", lambda: clock[0])
    args = dict(source_commit="c" * 40, stored_request_sha256=_digest(request), prepare_candidate=candidate)
    if finish_offset >= 100:
        with pytest.raises(ValueError):
            tool.review_retained(**args)
    else:
        checked = tool.review_retained(**args)
        assert checked["approval_granted"] is False and checked["submission_enabled"] is False
    assert len(calls) == 2
