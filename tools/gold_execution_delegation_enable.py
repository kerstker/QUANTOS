"""Independently human-enable one registered bounded delegation, never send.

Original disabled registration is preserved. This new purpose only publishes
root-owned public admission evidence after reviewing the same pilot and issuer
history. It never opens a signing key, replaces policy or starts a service.
"""

import argparse
from contextlib import closing
import json
import os
import re
import stat

from live.execution import demo_gold_delegation_enable as contract
from live.execution.demo_authority import _require_protected
from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import require
from live.execution.demo_issuer_store import IssuerStore
from quantos.contracts.killswitch import KillSwitchState
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_operating_bundle as installer, demo_supervisor as supervisor
from tools import gold_execution_delegation_review as original
from tools.gold_strategy_policy_install import local_operator


class EnableIncomplete(ValueError):
    def __init__(self, result):
        super().__init__("DELEGATION_INSPECT_PARTIAL_ENABLE")
        self.result = result


def _anchor(history):
    if history.get('schema')=='DEMO_GOLD_ISSUER_HISTORY_ANCHOR_V2':
        result={key:value for key,value in history.items() if key not in ('database_sha256','device','inode')}
        contract.anchor_check(result)
        return result
    return {name: history[name] for name in contract.ANCHOR_FIELDS.split()}


def _registration(source_commit, identity):
    require(type(identity) is str and re.fullmatch("[a-f0-9]{64}", identity) is not None, "DELEGATION_ENABLE_IDENTITY")
    area = supervisor.STATE / "gold-execution-delegations" / identity
    supervisor._root_private_directory(area)
    names = {
        "candidate.json",
        "operator-material.json",
        "operator-confirmation.json",
        "delegation.json",
        "registration.json",
    }
    require(set(os.listdir(area)) == names, "DELEGATION_ENABLE_REGISTRATION_INCOMPLETE")
    raw = {name: supervisor._read_root_owned_json(area / name) for name in names}
    values = {name: decode(body) for name, body in raw.items()}
    candidate = values["candidate.json"]
    registration = values["registration.json"]
    confirmation = values["operator-confirmation.json"]
    require(
        type(candidate) is dict
        and candidate.get("schema") in ("GOLD_EXECUTION_DELEGATION_REVIEW_V1","GOLD_EXECUTION_DELEGATION_REVIEW_V2")
        and candidate.get("status") == "DELEGATION_REVIEWED_CONSUMER_DISABLED"
        and candidate.get("source_commit") == source_commit
        and all(candidate.get(k) is False for k in ("consumer_enabled", "submission_enabled", "economic_authority")),
        "DELEGATION_ENABLE_REGISTRATION_CANDIDATE",
    )
    _fields(
        registration,
        "schema status source_commit delegation_sha256 candidate_sha256 operator_confirmation_sha256 "
        "issuer_history executor_ledger_state_hash registered consumer_enabled submission_enabled economic_authority",
    )
    require(
        registration["schema"] == ("GOLD_EXECUTION_DELEGATION_REGISTRATION_V2" if candidate['schema']=='GOLD_EXECUTION_DELEGATION_REVIEW_V2'
            else "GOLD_EXECUTION_DELEGATION_REGISTRATION_V1")
        and registration["status"] == "DELEGATION_REGISTERED_CONSUMER_DISABLED"
        and registration["source_commit"] == source_commit
        and registration["delegation_sha256"] == identity
        and registration["candidate_sha256"] == _digest(candidate)
        and registration["operator_confirmation_sha256"] == _digest(confirmation)
        and registration["issuer_history"] == candidate["issuer_history"]
        and registration["executor_ledger_state_hash"] == candidate["executor_ledger_state_hash"]
        and registration["registered"] is True
        and all(registration[k] is False for k in ("consumer_enabled", "submission_enabled", "economic_authority")),
        "DELEGATION_ENABLE_DISABLED_REGISTRATION_REQUIRED",
    )
    _fields(
        confirmation,
        "schema purpose candidate_sha256 operator_id approved_at_ms "
        "consumer_enabled submission_enabled economic_authority",
    )
    require(
        confirmation["schema"] == "GOLD_EXECUTION_DELEGATION_REGISTRATION_APPROVAL_V1"
        and confirmation["purpose"] == "REGISTER_BOUNDED_FUTURE_SIGNALS_CONSUMER_DISABLED"
        and confirmation["candidate_sha256"] == _digest(candidate)
        and confirmation["operator_id"] == candidate["operator_material"]["operator_id"]
        and type(confirmation["approved_at_ms"]) is int
        and candidate["delegation"]["terms"]["confirmed_at_ms"] <= confirmation["approved_at_ms"] <= supervisor.now_ms()
        and all(confirmation[k] is False for k in ("consumer_enabled", "submission_enabled", "economic_authority")),
        "DELEGATION_ENABLE_REGISTRATION_CONFIRMATION",
    )
    require(
        values["delegation.json"] == candidate["delegation"]
        and _digest(values["delegation.json"]) == candidate["delegation_sha256"] == identity
        and values["operator-material.json"] == candidate["operator_material"]
        and original._sha(raw["delegation.json"]) == candidate["signed_file_sha256"]
        and original._sha(raw["operator-material.json"]) == candidate["operator_file_sha256"],
        "DELEGATION_ENABLE_REGISTRATION_ORIGINAL",
    )
    require(
        all(supervisor._read_root_owned_json(area / name) == body for name, body in raw.items())
        and set(os.listdir(area)) == names,
        "DELEGATION_ENABLE_REGISTRATION_CHANGED",
    )
    return candidate, registration, raw


def _review_locked(*, source_commit, delegation_sha256, definition_session=None):
    previous, registration, raw = _registration(source_commit, delegation_sha256)
    material = supervisor.STATE / "gold-execution-delegation-materials"
    args = dict(
        source_commit=source_commit,
        delegation_file=material / "delegations" / (delegation_sha256 + ".json"),
        operator_approval_file=material / "operators" / (_digest(previous["operator_material"]) + ".json"),
    )
    checked = original._review_locked(**args, definition_session=definition_session)
    changing = {"issuer_history", "executor_context_sha256", "executor_ledger_state_hash"}
    require(
        {k: v for k, v in previous.items() if k not in changing}
        == {k: v for k, v in checked.items() if k not in changing},
        "DELEGATION_ENABLE_REGISTERED_ORIGINAL_CHANGED",
    )
    registry, policy = original.observation_configuration(source_commit)
    original.demo_issuer._state_custody(installer.ISSUER, registry["issuer_uid"])
    with closing(IssuerStore(installer.ISSUER / "issuance.sqlite3", read_only=True)) as store:
        rows = store.snapshot()
    require(_digest(rows) == checked["issuer_history"]["history_sha256"], "DELEGATION_ENABLE_HISTORY_CHANGED")
    anchor = _anchor(registration["issuer_history"])
    contract.verify_history_prefix(anchor, rows, policy=policy)
    runtime = decode(supervisor._read_root_owned_json(supervisor.STATE / "strategy-runtime.json"))
    definition = original.strategy.load_definition(
        installer.EXECUTOR, runtime, source_commit=source_commit, uid=registry["executor_uid"],
        definition_session=definition_session,
    )
    require(
        _digest(runtime) == checked["delegation"]["terms"]["runtime_sha256"]
        and _digest(definition) == checked["definition_sha256"],
        "DELEGATION_ENABLE_RUNTIME_CHANGED",
    )
    dispatch_raw = supervisor._read_root_owned_json(supervisor.STATE / "strategy-dispatch.json")
    dispatch = decode(dispatch_raw)
    _fields(dispatch, "schema source_commit account_ref_sha256 budget_group_binding_sha256 kill_switch")
    require(
        dispatch["schema"] == "GOLD_STRATEGY_APPROVED_POLICY_DISPATCH_V1"
        and dispatch["source_commit"] == source_commit
        and dispatch["account_ref_sha256"] == policy["account_ref_sha256"]
        and dispatch["budget_group_binding_sha256"] == anchor["group_binding_sha256"],
        "DELEGATION_ENABLE_DISPATCH_BINDING",
    )
    _fields(dispatch["kill_switch"], " ".join(KillSwitchState.model_fields))
    kill = KillSwitchState.model_validate_json(json.dumps(dispatch["kill_switch"]), strict=True)
    require(
        not kill.tripped and max(kill.armed_at, kill.reset_at) <= supervisor.now_ms(), "DELEGATION_ENABLE_DISPATCH_STOP"
    )
    binding = {
        name: checked[name]
        for name in contract.BINDING_FIELDS.split()
        if name not in ("issuer_current", "dispatcher_sha256")
    }
    binding["issuer_current"] = _anchor(checked["issuer_history"])
    binding["dispatcher_sha256"] = _digest(dispatch)
    result = dict(
        schema="GOLD_EXECUTION_DELEGATION_ENABLE_CANDIDATE_V2" if checked['schema']=='GOLD_EXECUTION_DELEGATION_REVIEW_V2'
            else "GOLD_EXECUTION_DELEGATION_ENABLE_CANDIDATE_V1",
        delegation=checked["delegation"],
        runtime=runtime,
        definition=definition,
        registration_sha256=_digest(registration),
        registration_candidate_sha256=_digest(previous),
        issuer_anchor=anchor,
        original_binding=binding,
        consumer_enabled=False,
        submission_enabled=False,
        economic_authority=False,
    )
    if checked['schema']=='GOLD_EXECUTION_DELEGATION_REVIEW_V2':result['continuity_event']=checked['continuity_event']
    require(
        _registration(source_commit, delegation_sha256)[2] == raw
        and original._review_locked(**args, definition_session=definition_session) == checked
        and supervisor._read_root_owned_json(supervisor.STATE / "strategy-dispatch.json") == dispatch_raw,
        "DELEGATION_ENABLE_ORIGINALS_CHANGED",
    )
    require(len(installer.canonical(result)) <= 60000, "DELEGATION_ENABLE_PUBLIC_BUDGET")
    return result


def review(**args):
    with supervisor.controller_lease(maintenance=True):
        return _review_locked(**args)


def confirmation(candidate, operator):
    require(local_operator() == operator, "DELEGATION_ENABLE_OPERATOR_CHANGED")
    print(json.dumps(candidate, ensure_ascii=False, indent=2))
    print(
        "启用所示有限未来信号消费者；固定原 pilot、策略与共享预算。仍需每次独立证明、风险及 issuer 签名；不续期、不下单。"
    )
    expected = "ENABLE GOLD DELEGATION " + _digest(candidate)
    require(input("请输入 " + expected + "\n").strip() == expected, "DELEGATION_ENABLE_NOT_CONFIRMED")
    require(local_operator() == operator, "DELEGATION_ENABLE_OPERATOR_CHANGED")
    return dict(
        schema="GOLD_EXECUTION_DELEGATION_ENABLE_APPROVAL_V1",
        purpose="ENABLE_BOUNDED_FUTURE_SIGNALS",
        candidate_sha256=_digest(candidate),
        operator_id=operator,
        approved_at_ms=supervisor.now_ms(),
        consumer_enabled=True,
        submission_enabled=False,
        economic_authority=False,
    )


def _same(candidate, operator, args, definition_session=None):
    require(local_operator() == operator and _review_locked(**args, definition_session=definition_session) == candidate,
            "DELEGATION_ENABLE_ORIGINALS_CHANGED")


def _failure(args):
    return dict(
        schema="GOLD_EXECUTION_DELEGATION_ENABLE_FAILURE_V1",
        status="INSPECT_PARTIAL_DELEGATION_ENABLE",
        delegation_sha256=args["delegation_sha256"],
        consumer_enabled=False,
        submission_enabled=False,
        economic_authority=False,
    )


def _require_public_parent(info):
    _require_protected(info, directory=True)
    require(info.st_mode & stat.S_IXOTH, "DELEGATION_ENABLE_PUBLIC_PARENT")


def _record_failure(result):
    try:
        installer.write_new(contract.PUBLIC_ROOT / "failure.json", installer.canonical(result), mode=0o444)
    except (OSError, ValueError):
        # Keep all attempted files, but make the strict public reader reject.
        os.chmod(contract.PUBLIC_ROOT, 0o700, follow_symlinks=False)
        fd = os.open(contract.PUBLIC_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _publish_locked(*, candidate, operator, args, registry, policy, value, definition_session=None):
    try:
        body = installer.canonical(value)
        require(len(body) <= 65536, "DELEGATION_ENABLE_PUBLIC_BUDGET")
        _same(candidate, operator, args, definition_session)
        installer.write_new(contract.PUBLIC_ROOT / "enable.json", body, mode=0o444)
        require(contract._read("enable.json") == body, "DELEGATION_ENABLE_READBACK_CHANGED")
        _same(candidate, operator, args, definition_session)
        complete = dict(
            schema="DEMO_GOLD_DELEGATION_ENABLE_COMPLETE_V1",
            enable_sha256=_digest(value),
            consumer_enabled=True,
            submission_enabled=False,
            economic_authority=False,
        )
        installer.write_new(contract.PUBLIC_ROOT / "complete.json", installer.canonical(complete), mode=0o444)
        require(
            contract._load_locked(
                registry=registry,
                source_commit=args["source_commit"],
                account_ref_sha256=policy["account_ref_sha256"],
                now_ms=supervisor.now_ms(),
            )
            == value,
            "DELEGATION_ENABLE_READBACK_CHANGED",
        )
        _same(candidate, operator, args, definition_session)
        return dict(
            schema="GOLD_EXECUTION_DELEGATION_ENABLE_RESULT_V1",
            status="DELEGATION_ENABLED_REQUIRES_ORIGINAL_GATES",
            source_commit=args["source_commit"],
            enable_sha256=_digest(value),
            delegation_sha256=args["delegation_sha256"],
            consumer_enabled=True,
            submission_enabled=False,
            economic_authority=False,
        )
    except Exception as exc:
        result = _failure(args)
        _record_failure(result)  # Still holding the exclusive public-directory lease.
        raise EnableIncomplete(result) from exc


def enable_approved(**args):
    operator = local_operator()
    with supervisor.controller_lease(maintenance=True):
        require(
            not contract.PUBLIC_ROOT.exists() and not contract.PUBLIC_ROOT.is_symlink(),
            "DELEGATION_ENABLE_ALREADY_OR_PARTIALLY_INSTALLED",
        )
        definition_session = original.strategy.DefinitionReadSession()
        candidate = _review_locked(**args, definition_session=definition_session)
        original_candidate, _, _ = _registration(args["source_commit"], args["delegation_sha256"])
        require(
            original_candidate["operator_material"]["operator_id"] == operator, "DELEGATION_ENABLE_OPERATOR_ORIGINAL"
        )
        approval = confirmation(candidate, operator)
        registry, policy = original.observation_configuration(args["source_commit"])
        value = {**candidate, "schema": contract.SCHEMA_V2 if candidate['schema']=='GOLD_EXECUTION_DELEGATION_ENABLE_CANDIDATE_V2'
            else contract.SCHEMA, "consumer_enabled": True, "operator_confirmation": approval}
        contract.validate(
            value,
            registry=registry,
            source_commit=args["source_commit"],
            account_ref_sha256=policy["account_ref_sha256"],
            now_ms=supervisor.now_ms(),
        )
        require(approval["operator_id"] == operator, "DELEGATION_ENABLE_OPERATOR_CHANGED")
        _same(candidate, operator, args, definition_session)
        for parent in reversed((contract.PUBLIC_ROOT.parent, *contract.PUBLIC_ROOT.parent.parents)):
            _require_public_parent(parent.lstat())
        mutated = False
        try:
            # Mark ownership only after O_EXCL-equivalent mkdir succeeds. A
            # competing pre-existing directory must never receive our failure.
            contract.PUBLIC_ROOT.mkdir(mode=0o755)
            mutated = True
            os.chown(contract.PUBLIC_ROOT, 0, 0)
            os.chmod(contract.PUBLIC_ROOT, 0o755)
            parent_fd = os.open(contract.PUBLIC_ROOT.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            with contract.public_lease(exclusive=True):
                return _publish_locked(
                    candidate=candidate, operator=operator, args=args, registry=registry, policy=policy, value=value,
                    definition_session=definition_session,
                )
        except Exception as exc:
            if isinstance(exc, EnableIncomplete):
                raise
            if not mutated:
                raise
            result = _failure(args)
            if contract.PUBLIC_ROOT.is_dir() and not contract.PUBLIC_ROOT.is_symlink():
                _record_failure(result)
            raise EnableIncomplete(result) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--delegation-sha256", required=True)
    parser.add_argument("--enable-approved", action="store_true")
    args = vars(parser.parse_args(argv))
    approved = args.pop("enable_approved")
    previous = os.umask(0o077)
    try:
        result = enable_approved(**args) if approved else review(**args)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except Exception as exc:
        result = (
            exc.result
            if isinstance(exc, EnableIncomplete)
            else dict(
                status="DELEGATION_ENABLE_REJECTED",
                consumer_enabled=False,
                submission_enabled=False,
                economic_authority=False,
            )
        )
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 3
    finally:
        os.umask(previous)


if __name__ == "__main__":
    raise SystemExit(main())
