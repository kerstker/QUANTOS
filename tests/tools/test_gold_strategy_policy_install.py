"""Independent local policy installation, without signing or sending orders."""
from copy import deepcopy
import json
import os

import pytest

from live.execution.demo_batch_risk import _digest
from tests.live.test_gold_strategy_constraints import case as case, NOW
from tests.tools.test_gold_strategy_approval_request import inputs


def candidate_for(case):
    from tools.gold_strategy_approval_request import prepare, policy_candidate

    result, runtime, policy = inputs(case)
    request = prepare(result, runtime=runtime, policy=policy, now_ms=NOW + 10)
    value = policy_candidate(request, result=result, runtime=runtime, policy=policy, now_ms=NOW + 20)
    value["stored_request_sha256"] = _digest(request)
    return value, policy, case[3]


def test_install_requires_local_operator_before_reading_protected_material(monkeypatch, capsys):
    from tools import gold_strategy_policy_install as tool

    monkeypatch.setattr(tool.os, "geteuid", lambda: 1000)

    def forbidden(*args, **kwargs):
        raise AssertionError("protected material must not be read")

    monkeypatch.setattr(tool, "review_retained", forbidden)
    assert tool.main([
        "--source-commit", "c" * 40,
        "--stored-request-sha256", "a" * 64,
        "--install-approved",
    ]) == 3
    assert "LOCAL_OPERATOR_REQUIRED" in capsys.readouterr().out


def test_candidate_preserves_existing_authority_and_shared_budget(case):
    from tools.gold_strategy_policy_install import validate_candidate

    candidate, policy, registry = candidate_for(case)
    assert validate_candidate(candidate, policy=policy, registry=registry, now_ms=NOW + 20) == candidate
    assert candidate["approval_granted"] is False
    assert candidate["policy_candidate"]["budget_group"] == policy["budget_group"]


@pytest.mark.parametrize("damage", ["no_tty", "no_sudo", "root_sudo", "wrong_user"])
def test_operator_gate_rejects_unverified_or_noninteractive_identity(monkeypatch, damage):
    from types import SimpleNamespace
    from tools import gold_strategy_policy_install as tool

    monkeypatch.setattr(tool.os, "geteuid", lambda: 0)
    monkeypatch.setattr(tool.sys.stdin, "isatty", lambda: damage != "no_tty")
    monkeypatch.setattr(tool.sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("SUDO_UID", "0" if damage == "root_sudo" else "1000")
    monkeypatch.setenv("SUDO_USER", "wrong" if damage == "wrong_user" else "operator-fixture")
    if damage == "no_sudo":
        monkeypatch.delenv("SUDO_UID")
    monkeypatch.setattr(tool.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_name="operator-fixture"))
    with pytest.raises(ValueError):
        tool.local_operator()


@pytest.mark.parametrize("damage", ["account", "authority", "mandate", "scope", "budget", "window", "approval"])
def test_rehashed_candidate_cannot_expand_existing_permission(case, damage):
    from tools.gold_strategy_policy_install import validate_candidate

    candidate, policy, registry = candidate_for(case)
    proposed = candidate["policy_candidate"]
    if damage == "account":
        proposed["account_ref_sha256"] = "e" * 64
    elif damage == "authority":
        proposed["key_id"] = "replacement-key"
    elif damage == "mandate":
        proposed["bindings"]["mandate_sha256"] = "e" * 64
    elif damage == "scope":
        proposed["scope"]["max_notional_per_order"] = "999"
    elif damage == "budget":
        proposed["budget_group"]["max_orders"] += 1
    elif damage == "window":
        proposed["valid_until_ms"] -= 1
    else:
        candidate["approval_granted"] = True
    candidate["policy_candidate_sha256"] = _digest(proposed)
    with pytest.raises(ValueError):
        validate_candidate(candidate, policy=policy, registry=registry, now_ms=NOW + 20)


@pytest.fixture
def installation(case, tmp_path, monkeypatch):
    """Original retained reviewer and real flock/replace, in isolated fixtures.

    Only local operator identity and root custody are substituted. No installed
    paths, services, real keys or exchange transports enter this fixture.
    """
    from tools import gold_strategy_policy_install as tool
    from tools.gold_strategy_approval_request import prepare

    candidate, policy, registry = candidate_for(case)
    result, runtime, _ = inputs(case)
    request = prepare(result, runtime=runtime, policy=policy, now_ms=NOW + 10)
    controller, issuer = tmp_path / "controller", tmp_path / "issuer"
    executor = tmp_path / "retention-legacy-executor"
    executor.mkdir(mode=0o700)
    monkeypatch.setattr(tool.installer, "EXECUTOR", executor)
    controller.mkdir(mode=0o700)
    issuer.mkdir(mode=0o700)
    for name, value in (
        ("strategy-runtime.json", runtime),
        ("strategy-decisions/" + _digest(result) + ".json", result),
        ("strategy-approval-requests/" + _digest(request) + ".json", request),
    ):
        path = controller / name
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.write_bytes(tool.installer.canonical(value))
    policy_path = issuer / "policy.json"
    policy_path.write_bytes(tool.installer.canonical(policy))
    clock = [NOW + 20]
    writes = []

    def read(path):
        path.relative_to(tmp_path)
        assert not path.is_symlink()
        return path.read_bytes()

    def directory(path, uid, gid, *, mode=0o700):
        path.relative_to(tmp_path)
        assert uid == gid == 0
        path.mkdir(mode=mode)

    def write(path, body, *, uid=0, gid=0, mode=0o600):
        path.relative_to(tmp_path)
        assert uid == 0
        writes.append((path.name, gid, mode))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())

    def custody(path):
        path.relative_to(tmp_path)
        assert path.is_dir() and not path.is_symlink()

    def configuration(source):
        assert source == "c" * 40
        return deepcopy(registry), json.loads(read(policy_path))

    monkeypatch.setattr(tool.supervisor, "STATE", controller)
    monkeypatch.setattr(tool.supervisor, "ISSUER_POLICY", policy_path)
    monkeypatch.setattr(tool.supervisor, "configuration", configuration)
    monkeypatch.setattr(tool.supervisor, "_read_root_owned_json", read)
    monkeypatch.setattr(tool.supervisor, "_root_private_directory", custody)
    monkeypatch.setattr(tool.supervisor, "now_ms", lambda: clock[0])
    monkeypatch.setattr(tool.installer, "directory", directory)
    monkeypatch.setattr(tool.installer, "write_new", write)
    monkeypatch.setattr(tool, "local_operator", lambda: "isolated-operator-fixture")
    monkeypatch.setattr(tool, "operator_confirmation", lambda value, operator: dict(
        schema="GOLD_STRATEGY_LOCAL_OPERATOR_APPROVAL_V1", candidate_sha256=_digest(value),
        operator_id=operator, approved_at_ms=clock[0], economic_authority=False))
    return dict(tool=tool, candidate=candidate, policy=policy, registry=registry,
        policy_path=policy_path, controller=controller, issuer=issuer, clock=clock,
        write=write, writes=writes, case=case)


def install(values):
    return values["tool"].install_approved(source_commit="c" * 40,
        stored_request_sha256=values["candidate"]["stored_request_sha256"])


def test_install_retains_originals_and_only_replaces_policy(installation):
    values = installation
    result = install(values)
    assert json.loads(values["policy_path"].read_bytes()) == values["candidate"]["policy_candidate"]
    assert result["installed"] is True and result["orders_submitted"] is False
    assert result["budget_group_binding_sha256"] == values["candidate"]["budget_group_binding_sha256"]
    stage = values["issuer"] / "strategy-approvals" / _digest(values["candidate"])
    assert json.loads((stage / "previous-policy.json").read_bytes()) == values["policy"]
    assert json.loads((stage / "operator-approval.json").read_bytes())["candidate_sha256"] == _digest(values["candidate"])
    assert json.loads((stage / "result.json").read_bytes()) == result
    assert any(name.startswith(".gold-policy-") and gid == values["registry"]["issuer_uid"] and mode == 0o640
        for name, gid, mode in values["writes"])
    assert list(values["controller"].glob("tasks*")) == []
    assert values["case"][1]["dispatch"].snapshot() == []
    assert values["case"][1]["oms"].orders() == []


def test_default_cli_only_reviews_without_confirmation_or_writes(installation, monkeypatch, capsys):
    values = installation

    def forbidden(*args):
        raise AssertionError("default command must not request approval")

    monkeypatch.setattr(values["tool"], "operator_confirmation", forbidden)
    original = values["policy_path"].read_bytes()
    assert values["tool"].main(["--source-commit", "c" * 40,
        "--stored-request-sha256", values["candidate"]["stored_request_sha256"]]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["approval_granted"] is False and report["submission_enabled"] is False
    assert values["policy_path"].read_bytes() == original
    assert not (values["issuer"] / "strategy-approvals").exists()


@pytest.mark.parametrize("damage", ["not_confirmed", "expired", "changed_policy"])
def test_confirmation_failure_preserves_original_configuration(installation, monkeypatch, damage):
    values = installation
    tool = values["tool"]
    original = values["policy_path"].read_bytes()

    def confirmation(candidate, operator):
        if damage == "not_confirmed":
            tool.require(False, "POLICY_INSTALL_NOT_CONFIRMED")
        if damage == "expired":
            values["clock"][0] = NOW + 100
        if damage == "changed_policy":
            changed = deepcopy(values["policy"])
            changed["policy_id"] = "independent-policy-change"
            values["policy_path"].write_bytes(tool.installer.canonical(changed))
        return {}

    monkeypatch.setattr(tool, "operator_confirmation", confirmation)
    with pytest.raises(ValueError):
        install(values)
    assert not (values["issuer"] / "strategy-approvals").exists()
    if damage != "changed_policy":
        assert values["policy_path"].read_bytes() == original
    else:
        assert json.loads(values["policy_path"].read_bytes())["policy_id"] == "independent-policy-change"


def test_expiry_during_staging_keeps_preimage_and_does_not_activate(installation, monkeypatch):
    values = installation
    original = values["policy_path"].read_bytes()

    def write(path, body, **kwargs):
        values["write"](path, body, **kwargs)
        if path.name.startswith(".gold-policy-"):
            values["clock"][0] = NOW + 100

    monkeypatch.setattr(values["tool"].installer, "write_new", write)
    with pytest.raises(ValueError):
        install(values)
    assert values["policy_path"].read_bytes() == original
    stage = values["issuer"] / "strategy-approvals" / _digest(values["candidate"])
    assert (stage / "operator-approval.json").exists()
    assert not (stage / "result.json").exists()


def test_controller_shared_lease_blocks_installation(installation):
    values = installation
    original = values["policy_path"].read_bytes()
    with values["tool"].supervisor.controller_lease():
        with pytest.raises(ValueError, match="CONTROLLER_MAINTENANCE_BUSY"):
            install(values)
    assert values["policy_path"].read_bytes() == original
    assert not (values["issuer"] / "strategy-approvals").exists()


def test_interrupted_install_cannot_automatically_replay(installation, monkeypatch):
    values = installation
    original = values["policy_path"].read_bytes()

    def write(path, body, **kwargs):
        if path.name == "operator-approval.json":
            raise OSError("simulated interrupted staging")
        values["write"](path, body, **kwargs)

    monkeypatch.setattr(values["tool"].installer, "write_new", write)
    with pytest.raises(OSError):
        install(values)
    monkeypatch.setattr(values["tool"].installer, "write_new", values["write"])
    with pytest.raises(ValueError, match="POLICY_INSTALL_ALREADY_RETAINED"):
        install(values)
    assert values["policy_path"].read_bytes() == original


def test_failed_readback_reports_unknown_installation_and_retains_audit(installation, monkeypatch, capsys):
    values = installation
    tool = values["tool"]
    original_reader = tool.supervisor._read_root_owned_json

    def read(path):
        raw = original_reader(path)
        if path == values["policy_path"] and json.loads(raw) == values["candidate"]["policy_candidate"]:
            raise OSError("simulated post-activation read failure")
        return raw

    monkeypatch.setattr(tool.supervisor, "_read_root_owned_json", read)
    assert tool.main(["--source-commit", "c" * 40,
        "--stored-request-sha256", values["candidate"]["stored_request_sha256"],
        "--install-approved"]) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["installed"] is None
    assert json.loads(values["policy_path"].read_bytes()) == values["candidate"]["policy_candidate"]
    stage = values["issuer"] / "strategy-approvals" / _digest(values["candidate"])
    assert (stage / "operator-approval.json").exists()
    assert (stage / "previous-policy.json").exists()
    assert not (stage / "result.json").exists()


def test_installed_policy_uses_original_issuer_and_signature_gates(installation):
    from live.execution import demo_request_inputs as builder, demo_issuer as issuer
    from live.execution.demo_execution_session import ExecutionSession
    from services.validation import demo_submission
    from tests.live.test_gold_strategy_constraints import request_inputs

    values = installation
    install(values)
    private, executor, _, registry, _, issuer_store = values["case"]
    args = request_inputs(values["case"])
    args["issuer_policy"] = json.loads(values["policy_path"].read_bytes())
    result, _, _ = inputs(values["case"])
    args["execution_constraints"] = result["decision"]["execution_constraints"]
    built = builder.build(**args)
    session = ExecutionSession(**{k: executor[k] for k in ("dispatch", "recovery", "oms")},
        evidence_directory=values["controller"] / "isolated-session")
    handoff = session.propose(json.dumps(built["request"]).encode(), now_ms=NOW + 21)
    grant = issuer.issue_submission(handoff, policy=args["issuer_policy"], registry=registry,
        key=private, store=issuer_store, now_ms=NOW + 22)
    verified = demo_submission.verify(grant["envelope"], registry=registry,
        expected_context=handoff["proposal"]["context"], orders=args["orders"], now_ms=NOW + 23)
    assert verified["signature_verified"] is True
    assert executor["dispatch"].snapshot() == [] and executor["oms"].orders() == []
