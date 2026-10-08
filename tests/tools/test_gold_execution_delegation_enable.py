"""Native registration/issuer/pilot originals; synthetic operator and role IDs."""

from copy import deepcopy
import json
import os
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from live.execution import demo_gold_delegation_enable as contract
from tools import gold_execution_delegation_enable as tool
from tests.tools.test_gold_execution_delegation_install import installation as installation
from tests.tools.test_gold_execution_delegation_review import (
    local as local,
    case as case,
    original_evidence as original_evidence,
)


@pytest.fixture
def enabled_case(installation, case, monkeypatch):
    register, value, _ = installation
    registration = register.install_approved(**value.args)
    from live.execution import demo_authority
    from live.execution.demo_issuer_policy import budget_group_binding
    from tests.live.test_gold_strategy_constraints import request_inputs
    from tests.tools.test_gold_pilot_capacity import write

    write(
        value.controller / "strategy-dispatch.json",
        dict(
            schema="GOLD_STRATEGY_APPROVED_POLICY_DISPATCH_V1",
            source_commit="c" * 40,
            account_ref_sha256=value.policy["account_ref_sha256"],
            budget_group_binding_sha256=budget_group_binding(value.policy),
            kill_switch=request_inputs(case)["kill_state"].model_dump(mode="json"),
        ),
    )
    monkeypatch.setattr(tool, "_require_protected", demo_authority._require_protected)
    # Existing fixture virtualizes protected ancestors below pytest's private
    # temporary hierarchy; public leaf/root metadata stay independently native.
    monkeypatch.setattr(tool, "_require_public_parent", lambda info: tool._require_protected(info, directory=True))

    monkeypatch.setattr(contract, "PUBLIC_ROOT", value.root / "public-enable")
    monkeypatch.setattr(tool, "local_operator", lambda: "isolated-operator")
    native_metadata = contract._public_metadata

    def metadata(info, *, directory):
        # Map only installed owner IDs; actual mode/link/type/size remain native.
        assert info.st_uid == os.getuid()
        mapped = SimpleNamespace(
            **{name: getattr(info, name) for name in ("st_mode", "st_nlink", "st_size")}, st_uid=0, st_gid=0
        )
        return native_metadata(mapped, directory=directory)

    monkeypatch.setattr(contract, "_public_metadata", metadata)
    confirmations = []

    def confirm(candidate, operator):
        confirmations.append(deepcopy(candidate))
        return dict(
            schema="GOLD_EXECUTION_DELEGATION_ENABLE_APPROVAL_V1",
            purpose="ENABLE_BOUNDED_FUTURE_SIGNALS",
            candidate_sha256=_digest(candidate),
            operator_id=operator,
            approved_at_ms=value.clock[0],
            consumer_enabled=True,
            submission_enabled=False,
            economic_authority=False,
        )

    monkeypatch.setattr(tool, "confirmation", confirm)
    args = dict(source_commit="c" * 40, delegation_sha256=_digest(value.delegation))
    return SimpleNamespace(
        tool=tool, contract=contract, value=value, registration=registration, confirmations=confirmations, args=args
    )


def read(case):
    return case.contract.load_enabled(
        registry=case.value.registry,
        source_commit="c" * 40,
        account_ref_sha256=case.value.policy["account_ref_sha256"],
        now_ms=case.value.clock[0],
        role="controller",
    )


def test_disabled_registration_is_not_an_enabled_consumer(enabled_case):
    f = enabled_case
    assert f.registration["consumer_enabled"] is False
    with pytest.raises((ValueError, OSError)):
        read(f)
    assert not f.contract.PUBLIC_ROOT.exists()


def test_native_enable_preserves_policy_history_and_original_registration(enabled_case):
    f = enabled_case
    value = f.value
    old_policy = (value.config_dir / "policy.json").read_bytes()
    stage = value.controller / "gold-execution-delegations" / f.args["delegation_sha256"]
    old = {p: p.read_bytes() for p in stage.iterdir()}
    history = value.log.snapshot()
    result = f.tool.enable_approved(**f.args)
    public = read(f)
    assert result["status"] == "DELEGATION_ENABLED_REQUIRES_ORIGINAL_GATES"
    assert result["enable_sha256"] == _digest(public)
    assert public["delegation"] == value.delegation
    assert public["runtime"] == json.loads((value.controller / "strategy-runtime.json").read_bytes())
    assert public["definition"]["definition_hash"] == value.policy["budget_group"]["strategy_definition_sha256"]
    assert public["issuer_anchor"]["head_sha256"] == history[-1]["record_hash"]
    assert public["issuer_anchor"]["records"] == len(history)
    assert public["issuer_anchor"]["unsigned_reservations"] == 1
    assert public["consumer_enabled"] is True
    assert public["submission_enabled"] is public["economic_authority"] is False
    assert len(f.confirmations) == 1
    assert old == {p: p.read_bytes() for p in stage.iterdir()}
    assert old_policy == (value.config_dir / "policy.json").read_bytes()
    assert history == value.log.snapshot()
    assert not (value.controller / "tasks").exists()
    for path in f.contract.PUBLIC_ROOT.iterdir():
        assert path.stat().st_mode & 0o777 == 0o444
    with pytest.raises(ValueError):
        f.tool.enable_approved(**f.args)


@pytest.mark.parametrize("damage", ["runtime", "operator", "expiry", "registration", "history", "dispatcher"])
def test_drift_during_new_confirmation_never_enables(enabled_case, monkeypatch, damage):
    f = enabled_case
    original = f.tool.confirmation

    def changed(candidate, operator):
        approval = original(candidate, operator)
        if damage == "runtime":
            (f.value.controller / "strategy-runtime.json").write_text("{}")
        elif damage == "operator":
            monkeypatch.setattr(f.tool, "local_operator", lambda: "different-person")
        elif damage == "expiry":
            f.value.clock[0] = f.value.delegation["terms"]["opening_until_ms"]
        elif damage == "registration":
            stage = f.value.controller / "gold-execution-delegations" / f.args["delegation_sha256"]
            (stage / "registration.json").write_text("{}")
        elif damage == "dispatcher":
            (f.value.controller / "strategy-dispatch.json").write_text("{}")
        else:
            append_reservation(f)
        return approval

    monkeypatch.setattr(f.tool, "confirmation", changed)
    with pytest.raises((ValueError, OSError)):
        f.tool.enable_approved(**f.args)
    with pytest.raises((ValueError, OSError)):
        read(f)


def append_reservation(f):
    from services.validation.demo_submission import batch_sha256, GOLD_BATCH_SCHEMA

    plan = deepcopy(f.value.plan)
    plan["proposal_sha256"] = "e" * 64
    plan["orders"][0].update(newClientOrderId="QDdelegationenableextra01", reduceOnly=True)
    plan["context"]["batch_sha256"] = batch_sha256(plan["orders"], schema=GOLD_BATCH_SCHEMA)
    plan["issued_at_ms"] += 1
    f.value.log.reserve(plan, policy=f.value.policy, registry=f.value.registry)


def test_valid_append_since_registration_keeps_original_issuer_anchor(enabled_case):
    f = enabled_case
    before = f.value.log.snapshot()
    append_reservation(f)
    f.tool.enable_approved(**f.args)
    public = read(f)
    assert public["issuer_anchor"]["head_sha256"] == before[-1]["record_hash"]
    assert public["issuer_anchor"]["records"] == len(before)
    f.contract.verify_history_prefix(public["issuer_anchor"], f.value.log.snapshot(), policy=f.value.policy)


@pytest.mark.parametrize(
    "damage", ["definition", "runtime", "disabled", "old_confirmation", "identity", "expired", "anchor"]
)
def test_strict_public_contract_rejects_changed_or_old_authority(enabled_case, damage):
    f = enabled_case
    f.tool.enable_approved(**f.args)
    public = deepcopy(read(f))
    args = dict(
        registry=f.value.registry,
        source_commit="c" * 40,
        account_ref_sha256=f.value.policy["account_ref_sha256"],
        now_ms=f.value.clock[0],
    )
    if damage == "definition":
        public["definition"]["size_and_timing"]["entry_timeout_ms"] = 6000
    elif damage == "runtime":
        public["runtime"]["trial_manifest_sha256"] = "f" * 64
    elif damage == "disabled":
        public["consumer_enabled"] = False
    elif damage == "old_confirmation":
        public["operator_confirmation"]["purpose"] = "REGISTER_BOUNDED_FUTURE_SIGNALS_CONSUMER_DISABLED"
    elif damage == "identity":
        args["source_commit"] = "f" * 40
    elif damage == "expired":
        args["now_ms"] = f.value.delegation["terms"]["exit_until_ms"]
    else:
        public["issuer_anchor"]["group_consumed"]["submission"]["grants"] = False
    with pytest.raises(ValueError):
        f.contract.validate(public, **args)


@pytest.mark.parametrize("target", ["enable.json", "complete.json"])
def test_partial_publication_failure_is_read_rejected_and_not_repaired(enabled_case, monkeypatch, target):
    f = enabled_case
    native = f.tool.installer.write_new

    def fail(path, body, **kwargs):
        native(path, body, **kwargs)
        if path.name == target:
            raise OSError("isolated post-fsync fault")

    monkeypatch.setattr(f.tool.installer, "write_new", fail)
    with pytest.raises(f.tool.EnableIncomplete) as raised:
        f.tool.enable_approved(**f.args)
    assert raised.value.result["consumer_enabled"] is False
    with pytest.raises((ValueError, OSError)):
        read(f)
    before = {p: p.read_bytes() for p in f.contract.PUBLIC_ROOT.iterdir()}
    with pytest.raises(ValueError):
        f.tool.enable_approved(**f.args)
    assert before == {p: p.read_bytes() for p in f.contract.PUBLIC_ROOT.iterdir()}


@pytest.mark.parametrize("damage", ["missing_complete", "failure", "symlink", "writable", "wrong_role"])
def test_fixed_public_reader_rejects_partial_or_bad_custody(enabled_case, damage, monkeypatch):
    f = enabled_case
    f.tool.enable_approved(**f.args)
    root = f.contract.PUBLIC_ROOT
    if damage == "missing_complete":
        (root / "complete.json").unlink()
    elif damage == "failure":
        (root / "failure.json").write_text("{}")
    elif damage == "symlink":
        (root / "enable.json").rename(root / "elsewhere.json")
        (root / "enable.json").symlink_to(root / "elsewhere.json")
    elif damage == "writable":
        (root / "enable.json").chmod(0o644)
    else:
        monkeypatch.setattr(os, "geteuid", lambda: f.value.registry["issuer_uid"])
    with pytest.raises((ValueError, OSError)):
        read(f)


def test_confirmation_uses_new_full_candidate_hash_and_rechecks_operator(enabled_case, monkeypatch):
    f = enabled_case
    candidate = f.tool.review(**f.args)
    # Retrieve original function, not the deliberate human-confirmation fixture.
    import importlib

    module = importlib.util.module_from_spec(
        importlib.util.spec_from_file_location("enable_confirmation", f.tool.__file__)
    )
    module.__spec__.loader.exec_module(module)
    monkeypatch.setattr(module, "local_operator", lambda: "isolated-operator")
    seen = []
    monkeypatch.setattr(
        "builtins.input", lambda prompt: seen.append(prompt) or "ENABLE GOLD DELEGATION " + _digest(candidate)
    )
    result = module.confirmation(candidate, "isolated-operator")
    assert seen and _digest(candidate) in seen[0]
    assert result["purpose"] == "ENABLE_BOUNDED_FUTURE_SIGNALS"
    assert result["candidate_sha256"] == _digest(candidate)


def test_partial_failure_is_not_visible_between_commit_and_failure_marker(enabled_case, monkeypatch):
    f = enabled_case
    native = f.tool.installer.write_new
    seen = []

    def fault(path, body, **kwargs):
        if path.name == "failure.json":
            with pytest.raises(ValueError, match="PUBLIC_BUSY"):
                read(f)
            seen.append(True)
        native(path, body, **kwargs)
        if path.name == "complete.json":
            raise OSError("postcommit fault")

    monkeypatch.setattr(f.tool.installer, "write_new", fault)
    with pytest.raises(f.tool.EnableIncomplete):
        f.tool.enable_approved(**f.args)
    assert seen
    with pytest.raises(ValueError):
        read(f)


def test_public_directory_replacement_during_read_is_rejected(enabled_case, monkeypatch):
    import shutil

    f = enabled_case
    f.tool.enable_approved(**f.args)
    native = f.contract._read
    changed = []

    def replace(name):
        raw = native(name)
        if not changed:
            root = f.contract.PUBLIC_ROOT
            old = root.with_name("old-public-inode")
            root.rename(old)
            shutil.copytree(old, root)
            changed.append(True)
        return raw

    monkeypatch.setattr(f.contract, "_read", replace)
    with pytest.raises(ValueError, match="PUBLIC_CHANGED"):
        read(f)


def test_public_role_readers_use_same_fixed_bytes_and_reject_other_identity(enabled_case, monkeypatch):
    f = enabled_case
    f.tool.enable_approved(**f.args)
    expected = read(f)
    for role in ("executor", "issuer"):
        uid = f.value.registry[role + "_uid"]
        with monkeypatch.context() as patch:
            patch.setattr(os, "geteuid", lambda: uid)
            patch.setattr(os, "getegid", lambda: uid)
            patch.setattr(os, "getgroups", lambda: [uid])
            assert (
                f.contract.load_enabled(
                    registry=f.value.registry,
                    source_commit="c" * 40,
                    account_ref_sha256=f.value.policy["account_ref_sha256"],
                    now_ms=f.value.clock[0],
                    role=role,
                )
                == expected
            )
            patch.setattr(os, "getgroups", lambda: [uid, uid + 1])
            with pytest.raises(ValueError, match="ROLE_IDENTITY"):
                f.contract.load_enabled(
                    registry=f.value.registry,
                    source_commit="c" * 40,
                    account_ref_sha256=f.value.policy["account_ref_sha256"],
                    now_ms=f.value.clock[0],
                    role=role,
                )


def test_original_and_enable_time_history_anchors_both_detect_rollback(enabled_case):
    f = enabled_case
    older = f.value.log.snapshot()
    append_reservation(f)
    f.tool.enable_approved(**f.args)
    public = read(f)
    rows = f.value.log.snapshot()
    for anchor in (public["issuer_anchor"], public["original_binding"]["issuer_current"]):
        f.contract.verify_history_prefix(anchor, rows, policy=f.value.policy)
        for damage in ("empty", "head", "counts"):
            candidate = deepcopy(anchor)
            chain = rows
            if damage == "empty":
                chain = []
            elif damage == "head":
                candidate["head_sha256"] = "e" * 64
            else:
                candidate["group_consumed"]["submission"]["notional"] = "0"
            with pytest.raises(ValueError):
                f.contract.verify_history_prefix(candidate, chain, policy=f.value.policy)
    with pytest.raises(ValueError):
        f.contract.verify_history_prefix(public["original_binding"]["issuer_current"], older, policy=f.value.policy)


@pytest.mark.parametrize("damage", ["absent", "group", "stop"])
def test_enable_requires_original_dispatch_group_and_stop_snapshot(enabled_case, damage):
    f = enabled_case
    path = f.value.controller / "strategy-dispatch.json"
    if damage == "absent":
        path.unlink()
    else:
        value = json.loads(path.read_bytes())
        if damage == "group":
            value["budget_group_binding_sha256"] = "e" * 64
        else:
            value["kill_switch"]["tripped"] = True
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    with pytest.raises((ValueError, OSError)):
        f.tool.enable_approved(**f.args)
    assert f.confirmations == [] and not f.contract.PUBLIC_ROOT.exists()


def test_public_parent_must_be_searchable_by_both_unprivileged_roles():
    import stat

    info = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0)
    with pytest.raises(ValueError, match="PUBLIC_PARENT"):
        tool._require_public_parent(info)
    info.st_mode = stat.S_IFDIR | 0o755
    tool._require_public_parent(info)


def test_directory_created_during_confirmation_is_never_modified(enabled_case, monkeypatch):
    f = enabled_case
    original = f.tool.confirmation

    def create(candidate, operator):
        result = original(candidate, operator)
        f.contract.PUBLIC_ROOT.mkdir(mode=0o755)
        (f.contract.PUBLIC_ROOT / "sentinel").write_bytes(b"pre-existing unrelated evidence")
        return result

    monkeypatch.setattr(f.tool, "confirmation", create)
    with pytest.raises((ValueError, OSError)):
        f.tool.enable_approved(**f.args)
    assert set(os.listdir(f.contract.PUBLIC_ROOT)) == {"sentinel"}
    assert (f.contract.PUBLIC_ROOT / "sentinel").read_bytes() == b"pre-existing unrelated evidence"
