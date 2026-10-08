"""New-purpose sudo/TTY registration retains public evidence, never authority."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_execution_delegation_review import (
    local as local,
    case as case,
    original_evidence as original_evidence,
)


@pytest.fixture
def installation(local, monkeypatch):
    from tools import gold_execution_delegation_install as install
    from tools import demo_operating_bundle as installer

    monkeypatch.setattr(install, "local_operator", lambda: "isolated-operator")
    monkeypatch.setattr(installer.os, "chown", lambda path, uid, gid: None)
    monkeypatch.setattr(installer.os, "fchown", lambda fd, uid, gid: None)
    confirmations = []

    def confirm(candidate, operator):
        confirmations.append(candidate)
        return dict(
            schema="GOLD_EXECUTION_DELEGATION_REGISTRATION_APPROVAL_V1",
            purpose="REGISTER_BOUNDED_FUTURE_SIGNALS_CONSUMER_DISABLED",
            candidate_sha256=_digest(candidate),
            operator_id=operator,
            approved_at_ms=local.clock[0],
            consumer_enabled=False,
            submission_enabled=False,
            economic_authority=False,
        )

    monkeypatch.setattr(install, "confirmation", confirm)
    return install, local, confirmations


def test_native_install_retains_registered_material_without_replacing_policy_or_ledger(installation):
    install, value, confirmations = installation
    old_policy = (value.config_dir / "policy.json").read_bytes()
    old = value.log.snapshot()
    result = install.install_approved(**value.args)
    assert result["status"] == "DELEGATION_REGISTERED_CONSUMER_DISABLED"
    assert result["registered"] is True and result["consumer_enabled"] is False
    assert result["submission_enabled"] is result["economic_authority"] is False
    assert len(confirmations) == 1 and value.log.snapshot() == old
    assert (value.config_dir / "policy.json").read_bytes() == old_policy
    target = value.controller / "gold-execution-delegations" / _digest(value.delegation)
    assert json.loads((target / "delegation.json").read_bytes()) == value.delegation
    assert json.loads((target / "registration.json").read_bytes()) == result
    assert not (value.controller / "tasks").exists()
    before = {p: p.read_bytes() for p in target.iterdir()}
    with pytest.raises(ValueError):
        install.install_approved(**value.args)
    assert before == {p: p.read_bytes() for p in target.iterdir()}


@pytest.mark.parametrize("damage", ["runtime", "ledger", "operator", "expiry"])
def test_prompt_drift_cannot_produce_registration(installation, monkeypatch, damage):
    from services.validation.demo_submission import batch_sha256, GOLD_BATCH_SCHEMA

    install, value, _ = installation
    original = install.confirmation

    def changed(candidate, operator):
        approval = original(candidate, operator)
        if damage == "runtime":
            path = value.controller / "strategy-runtime.json"
            path.write_text("{}")
        elif damage == "ledger":
            plan = deepcopy(value.plan)
            plan["proposal_sha256"] = "e" * 64
            plan["orders"][0].update(newClientOrderId="QDdelegationunknown002", reduceOnly=True)
            plan["context"]["batch_sha256"] = batch_sha256(plan["orders"], schema=GOLD_BATCH_SCHEMA)
            plan["issued_at_ms"] += 1
            value.log.reserve(plan, policy=value.policy, registry=value.registry)
        elif damage == "operator":
            monkeypatch.setattr(install, "local_operator", lambda: "changed-operator")
        else:
            value.clock[0] = value.policy["valid_until_ms"]
        return approval

    monkeypatch.setattr(install, "confirmation", changed)
    with pytest.raises(ValueError):
        install.install_approved(**value.args)
    assert not (value.controller / "gold-execution-delegations").exists()


def test_original_shared_controller_lease_blocks_registration(installation):
    install, value, _ = installation
    with install.supervisor.controller_lease():
        with pytest.raises(ValueError, match="CONTROLLER_MAINTENANCE_BUSY"):
            install.install_approved(**value.args)
    assert not (value.controller / "gold-execution-delegations").exists()


@pytest.mark.parametrize("damage", ["root", "tty", "sudo", "operator"])
def test_local_operator_gate_is_required_before_any_protected_read(monkeypatch, damage):
    from tools import gold_execution_delegation_install as install
    from tools import gold_strategy_policy_install as gate

    monkeypatch.setattr(gate.os, "geteuid", lambda: 1000 if damage == "root" else 0)
    monkeypatch.setattr(gate.sys.stdin, "isatty", lambda: damage != "tty")
    monkeypatch.setattr(gate.sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("SUDO_UID", "0" if damage == "sudo" else "1000")
    monkeypatch.setenv("SUDO_USER", "other" if damage == "operator" else "isolated-operator")
    monkeypatch.setattr(gate.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_name="isolated-operator"))
    monkeypatch.setattr(install, "review", lambda **kw: pytest.fail("read before local human gate"))
    with pytest.raises(ValueError):
        install.install_approved(
            source_commit="c" * 40,
            delegation_file="/isolated/delegation.json",
            operator_approval_file="/isolated/operator.json",
        )


def test_registration_confirmation_uses_new_explicit_purpose(monkeypatch, capsys):
    from tools import gold_execution_delegation_install as install

    monkeypatch.setattr(install, "local_operator", lambda: "isolated-operator")
    candidate = {"consumer_enabled": False, "delegation_sha256": "a" * 64}
    expected = "REGISTER GOLD DELEGATION " + _digest(candidate)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or expected)
    result = install.confirmation(candidate, "isolated-operator")
    assert result["purpose"] == "REGISTER_BOUNDED_FUTURE_SIGNALS_CONSUMER_DISABLED"
    assert result["candidate_sha256"] == _digest(candidate) and expected in prompts[0]
    assert "consumer" in capsys.readouterr().out


def test_drift_during_writes_keeps_partial_audit_and_no_success(installation, monkeypatch):
    install, value, _ = installation
    original = install.installer.write_new

    def changed(path, body, **kw):
        result = original(path, body, **kw)
        if path.name == "delegation.json":
            value.context["ledger_state_hash"] = "f" * 64
        return result

    monkeypatch.setattr(install.installer, "write_new", changed)
    with pytest.raises(install.RegistrationIncomplete):
        install.install_approved(**value.args)
    stage = value.controller / "gold-execution-delegations" / _digest(value.delegation)
    assert (stage / "candidate.json").exists() and (stage / "failure.json").exists()
    assert not (stage / "registration.json").exists()
    before = {p: p.read_bytes() for p in stage.iterdir()}
    with pytest.raises(ValueError):
        install.install_approved(**value.args)
    assert before == {p: p.read_bytes() for p in stage.iterdir()}
