"""Three isolated custody roots; real signatures, archives, pilot and issuer log.

Source admission, root/role ownership and canonical-context child transport are
explicit fixture boundaries. No actual key, service, network or order is used.
"""

from copy import deepcopy
import base64
import json
import os
from pathlib import Path
import shutil
import stat
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tests.live.test_gold_strategy_constraints import case as case, request_inputs, NOW
from tests.live.test_gold_execution_delegation import fixture as contract_fixture
from tests.tools.test_gold_runtime_evidence import original_evidence as original_evidence
from tests.tools.test_gold_pilot_capacity import write


def sign(value):
    from live.execution import demo_gold_execution_delegation as contract

    value.delegation["terms"]["operator_approval_sha256"] = _digest(value.operator)
    value.delegation["signature_base64url"] = (
        base64.urlsafe_b64encode(value.private.sign(contract.signing_payload(value.delegation))).rstrip(b"=").decode()
    )
    inbox = value.controller / "gold-execution-delegation-materials"
    for name in ("delegations", "operators"):
        (inbox / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    value.args["delegation_file"] = inbox / "delegations" / (_digest(value.delegation) + ".json")
    value.args["operator_approval_file"] = inbox / "operators" / (_digest(value.operator) + ".json")
    write(value.args["delegation_file"], value.delegation)
    write(value.args["operator_approval_file"], value.operator)


@pytest.fixture
def local(case, original_evidence, tmp_path, monkeypatch):
    from tools import gold_execution_delegation_review as review
    from tools import demo_operating_bundle as installer, demo_supervisor as supervisor
    from tools import demo_successor_setup as successor, demo_operator_setup as setup
    from tools import gold_strategy_budget_enrollment as strategy, demo_issuer
    from tools.gold_pilot_install import _read_target
    from live.execution import demo_position_context as position, demo_authority
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from live.execution.demo_issuer_store import IssuerStore
    from tests.live.test_demo_pre_submit import stores
    from services.validation.demo_submission import registry_sha256

    _, private, registry, delegation = contract_fixture(case)
    controller, executor, issuer = (tmp_path / name for name in ("controller", "executor", "issuer"))
    controller.mkdir(mode=0o700)
    issuer.mkdir(mode=0o700)
    shutil.copytree(original_evidence["root"], executor)
    runtime = deepcopy(original_evidence["fresh"])
    policy = delegation["policy_template"]
    policy["budget_group"]["strategy_definition_sha256"] = original_evidence["definition"]
    profile = request_inputs(case)["profile"]
    profile["strategy"]["strategy_id"] = "demo-functional-" + "a" * 16
    policy["bindings"]["strategy_id"] = profile["strategy"]["strategy_id"]
    policy["bindings"]["strategy_approval_sha256"] = _digest(profile["strategy"])
    profile["mandate"]["expires_at"] = policy["valid_until_ms"]
    policy["bindings"]["mandate_sha256"] = _digest(profile["mandate"])
    policy["registry_sha256"] = registry_sha256(registry)
    config_dir = tmp_path / "config"
    config_dir.mkdir(mode=0o700)
    setup_dir = tmp_path / "setup"
    setup_dir.mkdir(mode=0o700)
    for name, value in (("authority.json", registry), ("policy.json", policy), ("profile.json", profile)):
        write(config_dir / name, value)
    write(controller / "authority.json", registry)
    write(controller / "strategy-runtime.json", runtime)
    uid = os.getuid()

    def root_read(path):
        path.relative_to(tmp_path)
        return _read_target(path, uid=uid, maximum=16_000_000)

    def root_directory(path):
        path.relative_to(tmp_path)
        info = path.lstat()
        assert stat.S_ISDIR(info.st_mode) and info.st_uid == uid and not info.st_mode & 0o077

    for module in (supervisor, successor):
        monkeypatch.setattr(module, "_read_root_owned_json", root_read)
    monkeypatch.setattr(supervisor, "_root_private_directory", root_directory)
    monkeypatch.setattr(supervisor, "verify_source", lambda source: source == "c" * 40 or pytest.fail("source"))
    monkeypatch.setattr(supervisor.os, "geteuid", lambda: 0)
    monkeypatch.setattr(supervisor, "STATE", controller)
    monkeypatch.setattr(supervisor, "EXECUTOR_REGISTRY", controller / "authority.json")
    monkeypatch.setattr(supervisor, "ISSUER_REGISTRY", config_dir / "authority.json")
    monkeypatch.setattr(supervisor, "ISSUER_POLICY", config_dir / "policy.json")
    monkeypatch.setattr(installer, "CONFIG", config_dir)
    monkeypatch.setattr(installer, "EXECUTOR", executor)
    monkeypatch.setattr(installer, "ISSUER", issuer)
    monkeypatch.setattr(successor, "SETUP", setup_dir)
    clock = [NOW + 20]
    monkeypatch.setattr(supervisor, "now_ms", lambda: clock[0])
    monkeypatch.setattr(installer, "now_ms", lambda: clock[0])
    native_load = strategy.load_definition
    monkeypatch.setattr(strategy, "load_definition", lambda *a, **kw: native_load(*a, **(kw | {"uid": uid})))

    def executor_read(path, *, uid, maximum=65536):
        role = "executor" if executor in path.parents else "issuer"
        path.relative_to(executor if role == "executor" else issuer)
        assert uid == registry[role + "_uid"]
        return _read_target(path, uid=os.getuid(), maximum=maximum)

    monkeypatch.setattr(review, "_read_target", executor_read)

    def executor_custody(path, actual, *, directory):
        assert actual == registry["executor_uid"]
        path.relative_to(executor)
        from tools.demo_resident_recovery import _check_path

        _check_path(path, uid, directory=directory)

    monkeypatch.setattr(review, "_check_path", executor_custody)
    monkeypatch.setattr(demo_authority, "_require_protected", lambda *a, **kw: None)
    monkeypatch.setattr(position, "_ROOT", executor)
    monkeypatch.setattr(demo_authority, "load_registry_for_executor", lambda: {"executor_uid": uid})
    request = dict(
        schema="GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1",
        source_commit="c" * 40,
        account_ref_sha256=policy["account_ref_sha256"],
        duration_seconds=86400,
        max_loss_fraction="0.05",
        submission_enabled=False,
    )
    write(executor / "gold-pilot-request.json", request)
    native_stores = stores(executor)
    original = enrollment.enroll_requested(
        root=executor,
        source_commit="c" * 40,
        origin=case[1]["origin"],
        **dict(zip(("dispatch", "recovery", "oms"), native_stores, strict=True)),
        now_ms=NOW,
    )
    for store in native_stores:
        store.close()
    terms = delegation["terms"]
    terms.update(
        runtime_sha256=_digest(runtime),
        strategy_source_commit=runtime["archive_source_commit"],
        trial_manifest_sha256=runtime["trial_manifest_sha256"],
        pilot_request_sha256=_digest(request),
        pilot_configuration=original,
        confirmed_at_ms=NOW + 10,
        original_budget_group_binding_sha256=review.budget_group_binding(policy),
        max_holding_ms=900000,
    )
    operator = dict(
        schema="GOLD_EXECUTION_DELEGATION_OPERATOR_MATERIAL_V1",
        purpose="AUTHORIZE_BOUNDED_FUTURE_SIGNALS_AND_RESERVED_EXITS",
        operator_id="isolated-operator",
        approved_at_ms=terms["confirmed_at_ms"],
        source_commit="c" * 40,
        account_ref_sha256=policy["account_ref_sha256"],
        terms_sha256=_digest({k: v for k, v in terms.items() if k != "operator_approval_sha256"}),
        submission_enabled=False,
        economic_authority=False,
    )
    spec = {
        **setup.FIXED,
        "schema": setup.GOLD_SCHEMA,
        "symbol": "XAUUSDT",
        "source_commit": "c" * 40,
        "account_ref_sha256": policy["account_ref_sha256"],
        "registry_sha256": policy["registry_sha256"],
        "observed_origin_sha256": _digest(case[1]["origin"]),
    }
    installed = dict(
        schema="DEMO_OPERATOR_SETUP_RESULT_V1",
        status="INSTALLED_PENDING_CYCLE",
        spec_sha256=_digest(spec),
        enrollment_sha256="a" * 64,
        source_commit="c" * 40,
        task_sha256="f" * 64,
        orders_submitted=False,
        economic_authority=False,
    )
    write(setup_dir / "spec.json", spec)
    write(setup_dir / "result.json", installed)
    from tools.demo_functional_worker import scope_record

    cycle_area = executor / "resident-recovery/functional-cycles" / _digest(installed)
    cycle_area.mkdir(mode=0o700, parents=True)
    for directory in (cycle_area.parent.parent, cycle_area.parent):
        directory.chmod(0o700)
    write(
        cycle_area / "cycle-scope.json",
        scope_record(_digest(installed), policy["bindings"]["strategy_id"], symbol="XAUUSDT"),
    )
    context = dict(
        schema="DEMO_CANONICAL_POSITION_CONTEXT_V1",
        symbol="XAUUSDT",
        account_ref_sha256=policy["account_ref_sha256"],
        ledger_state_hash="b" * 64,
        quantity="0",
        day_id=NOW // 86400000,
        submission_enabled=False,
        economic_authority=False,
    )
    context_calls = []
    native_context_for = successor.context_for

    def context_for(source, current, cycle, *, allow_day_checkpoint):
        assert source == "c" * 40 and current == registry and cycle == _digest(installed)
        assert allow_day_checkpoint is False
        context_calls.append(1)
        return deepcopy(context)

    monkeypatch.setattr(successor, "context_for", context_for)

    def issuer_custody(path, actual):
        assert path == issuer and actual == registry["issuer_uid"]
        root_directory(path)
        _read_target(path / "issuance.sqlite3", uid=uid, maximum=128 * 1024 * 1024)

    monkeypatch.setattr(demo_issuer, "_state_custody", issuer_custody)
    log = IssuerStore(issuer / "issuance.sqlite3", create=True)
    proposal = case[2]["proposal"]
    ctx = deepcopy(proposal["context"])
    for key in ("deployment_sha256", "registry_sha256", "account_ref_sha256"):
        ctx[key] = policy[key]
    for key in ("mandate_sha256", "validation_sha256"):
        ctx[key] = policy["bindings"][key]
    ctx["execution_constraints_sha256"] = policy["bindings"]["execution_constraints_sha256"]
    plan = dict(
        purpose="submission",
        proposal_sha256="d" * 64,
        context=ctx,
        orders=deepcopy(case[1]["orders"]),
        commands=[],
        issued_at_ms=NOW + 11,
        expires_at_ms=NOW + 100,
        notional="120",
    )
    log.reserve(plan, policy=policy, registry=registry)

    def private_read(path, *, maximum):
        path = Path(path)
        path.relative_to(tmp_path)
        return _read_target(path, uid=uid, maximum=maximum)

    monkeypatch.setattr(installer, "read_private", private_read)
    value = SimpleNamespace(
        root=tmp_path,
        controller=controller,
        executor=executor,
        issuer=issuer,
        config_dir=config_dir,
        setup_dir=setup_dir,
        registry=registry,
        policy=policy,
        profile=profile,
        delegation=delegation,
        operator=operator,
        private=private,
        log=log,
        clock=clock,
        context=context,
        context_calls=context_calls,
        plan=plan,
        args=dict(
            source_commit="c" * 40,
            delegation_file=tmp_path / "delegation.json",
            operator_approval_file=tmp_path / "operator.json",
        ),
    )
    value.native_context_for = native_context_for
    value.cycle_area = cycle_area
    sign(value)
    try:
        yield value
    finally:
        log.close()


def test_review_binds_native_originals_and_unsigned_reservation_without_mutation(local):
    from tools.gold_execution_delegation_review import review
    from tests.tools.test_gold_runtime_evidence import evidence_snapshot

    before = local.log.snapshot()
    original = {p: evidence_snapshot(p) for p in (local.executor, local.issuer, local.controller)}
    result = review(**local.args)
    assert result["status"] == "DELEGATION_REVIEWED_CONSUMER_DISABLED"
    assert result["issuer_history"]["head_sha256"] == before[-1]["record_hash"]
    assert result["issuer_history"]["group_consumed"]["submission"] == dict(grants=1, orders=1, notional="120")
    assert result["issuer_history"]["unsigned_reservations"] == 1
    assert result["executor_context_sha256"] == _digest(local.context)
    assert result["consumer_enabled"] is result["submission_enabled"] is result["economic_authority"] is False
    assert local.log.snapshot() == before and local.context_calls
    assert original == {p: evidence_snapshot(p) for p in original}


@pytest.mark.parametrize(
    "damage",
    ["pilot", "runtime", "archive", "operator", "profile", "mirror", "signature", "expired", "stopped", "claim"],
)
def test_substituted_originals_fail_closed(local, damage):
    from tools.gold_execution_delegation_review import review

    if damage == "pilot":
        path = local.executor / "gold-pilot/configuration.json"
        value = json.loads(path.read_bytes())
        value["initial_equity"] = "6000"
        write(path, value)
    elif damage == "runtime":
        path = local.controller / "strategy-runtime.json"
        value = json.loads(path.read_bytes())
        value["candidate"] = "trend"
        write(path, value)
    elif damage == "archive":
        path = next((local.executor / "strategy-bar-trials").iterdir()) / "report.json"
        path.write_text("{}")
    elif damage == "operator":
        local.operator["purpose"] = "OBSERVATION_ONLY"
        write(local.args["operator_approval_file"], local.operator)
    elif damage == "profile":
        local.profile["mandate"]["expires_at"] += 1
        write(local.config_dir / "profile.json", local.profile)
    elif damage == "mirror":
        changed = deepcopy(local.registry)
        changed["deployment_sha256"] = "e" * 64
        write(local.config_dir / "authority.json", changed)
    elif damage == "signature":
        local.delegation["signature_base64url"] = "A" * 86
        write(local.args["delegation_file"], local.delegation)
    elif damage == "expired":
        local.clock[0] = local.policy["valid_until_ms"]
    elif damage == "stopped":
        write(local.executor / "gold-pilot/stopped.json", {"status": "STOPPED"})
    else:
        local.log.db.execute("DROP TRIGGER issuer_order_claims_no_delete")
        local.log.db.execute("DELETE FROM issuer_order_claims")
        local.log.db.commit()
    with pytest.raises(ValueError):
        review(**local.args)


@pytest.mark.parametrize("damage", ["reserve", "mandate", "group", "old_scope"])
def test_scope_or_budget_transition_requires_separate_continuity(local, damage):
    from tools.gold_execution_delegation_review import review

    if damage == "old_scope":
        changed = deepcopy(local.policy)
        changed["schema"] = "DEMO_GOLD_STRATEGY_BUDGET_POLICY_V1"
        changed["budget_group"].pop("exit_reserve")
        write(local.config_dir / "policy.json", changed)
    else:
        if damage == "reserve":
            local.delegation["policy_template"]["budget_group"]["exit_reserve"]["notional"] = "121"
        elif damage == "mandate":
            local.delegation["policy_template"]["bindings"]["mandate_sha256"] = "a" * 64
        else:
            local.delegation["policy_template"]["budget_group"]["group_id"] = "new-group"
        from live.execution.demo_issuer_policy import budget_group_binding

        local.delegation["terms"]["original_budget_group_binding_sha256"] = budget_group_binding(
            local.delegation["policy_template"]
        )
        local.operator["terms_sha256"] = _digest(
            {k: v for k, v in local.delegation["terms"].items() if k != "operator_approval_sha256"}
        )
        sign(local)
    with pytest.raises(ValueError, match="CONTINUITY_REQUIRED"):
        review(**local.args)


def test_arbitrary_root_path_is_rejected_before_any_public_file_read(local, monkeypatch):
    from tools import gold_execution_delegation_review as tool

    monkeypatch.setattr(tool.installer, "read_private", lambda *a, **kw: pytest.fail("unapproved path read"))
    with pytest.raises(ValueError, match="PUBLIC_MATERIAL_PATH"):
        tool.review(**(local.args | {"delegation_file": local.config_dir / "signing-key.json"}))


@pytest.mark.parametrize(
    "damage", ["executor_uid", "pilot_link", "issuer_link", "wal", "context_account", "context_hash"]
)
def test_custody_and_canonical_context_do_not_fall_back(local, monkeypatch, damage):
    from tools import gold_execution_delegation_review as tool

    if damage == "executor_uid":
        from tools.gold_pilot_install import _read_target

        monkeypatch.setattr(tool, "_read_target", _read_target)
    elif damage in ("pilot_link", "issuer_link"):
        path = (
            local.executor / "gold-pilot/configuration.json"
            if damage == "pilot_link"
            else local.issuer / "issuance.sqlite3"
        )
        moved = path.with_suffix(".original")
        path.rename(moved)
        path.symlink_to(moved)
    elif damage == "wal":
        (local.issuer / "issuance.sqlite3-wal").write_bytes(b"isolated-partial")
    elif damage == "context_account":
        local.context["account_ref_sha256"] = "a" * 64
    else:
        local.context["ledger_state_hash"] = "bad"
    with pytest.raises((ValueError, OSError)):
        tool.review(**local.args)


def test_original_context_helper_cannot_capture_a_day_checkpoint(local, monkeypatch):
    from tools import gold_execution_delegation_review as tool, demo_functional_cycle

    calls = []

    class Runtime:
        def __init__(self, *args):
            pass

        def invoke(self, operation, **kw):
            calls.append(operation)
            assert operation == "context"
            return dict(status="CANONICAL_CONTEXT", context=dict(local.context, day_id=local.context["day_id"] - 1))

        def close(self):
            calls.append("close")

    monkeypatch.setattr(demo_functional_cycle, "Runtime", Runtime)
    monkeypatch.setattr(tool.successor, "context_for", local.native_context_for)
    with pytest.raises(ValueError, match="DAY_CHECKPOINT_REQUIRED"):
        tool.review(**local.args)
    assert calls == ["context", "close"]


def test_key_expiring_during_original_reads_cannot_return_a_review(local, monkeypatch):
    from tools import gold_execution_delegation_review as tool

    original = tool._issuer_history

    def expire(*args):
        result = original(*args)
        local.clock[0] = local.policy["valid_until_ms"]
        return result

    monkeypatch.setattr(tool, "_issuer_history", expire)
    with pytest.raises(ValueError):
        tool.review(**local.args)


def test_missing_original_cycle_is_rejected_before_context_worker_can_create_it(local, monkeypatch):
    from tools import gold_execution_delegation_review as tool

    local.cycle_area.rename(local.cycle_area.with_name("retained-isolated-original"))
    monkeypatch.setattr(
        tool.successor, "context_for", lambda *a, **kw: pytest.fail("missing cycle launched context worker")
    )
    with pytest.raises((ValueError, OSError)):
        tool.review(**local.args)
    assert not local.cycle_area.exists()
