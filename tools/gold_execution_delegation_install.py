"""Human-register public bounded-delegation evidence, with consumers disabled.

No policy replacement, key read, grant, task, store initialization or resident
service change exists here. Review uses the existing read-only context worker.
A future consumer must independently recheck every original and decision;
this registration alone is never execution permission.
"""

import argparse
import json
import os
import re

from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import require
from tools import demo_operating_bundle as installer, demo_supervisor as supervisor
from tools.gold_execution_delegation_review import _review_locked as review, _public, _sha
from tools.gold_strategy_budget_enrollment import DefinitionReadSession
from tools.gold_strategy_policy_install import local_operator


class RegistrationIncomplete(ValueError):
    def __init__(self, result):
        super().__init__("DELEGATION_INSPECT_PARTIAL_REGISTRATION")
        self.result = result


def confirmation(candidate, operator):
    require(local_operator() == operator, "DELEGATION_OPERATOR_CHANGED")
    print(json.dumps(candidate, ensure_ascii=False, indent=2))
    print("登记有限未来信号委托原件；consumer_enabled=false。此登记不启用执行，不延长期限或预算。")
    expected = "REGISTER GOLD DELEGATION " + _digest(candidate)
    require(input("请输入 " + expected + "\n").strip() == expected, "DELEGATION_NOT_CONFIRMED")
    require(local_operator() == operator, "DELEGATION_OPERATOR_CHANGED")
    return dict(
        schema="GOLD_EXECUTION_DELEGATION_REGISTRATION_APPROVAL_V1",
        purpose="REGISTER_BOUNDED_FUTURE_SIGNALS_CONSUMER_DISABLED",
        candidate_sha256=_digest(candidate),
        operator_id=operator,
        approved_at_ms=supervisor.now_ms(),
        consumer_enabled=False,
        submission_enabled=False,
        economic_authority=False,
    )


def _same(candidate, operator, args, definition_session):
    require(local_operator() == operator and review(**args, definition_session=definition_session) == candidate,
        "DELEGATION_ORIGINALS_CHANGED")


def install_approved(**args):
    operator = local_operator()
    with supervisor.controller_lease(maintenance=True):
        definition_session = DefinitionReadSession()
        candidate = review(**args, definition_session=definition_session)
        require(candidate["operator_material"]["operator_id"] == operator, "DELEGATION_OPERATOR_ORIGINAL_REQUIRED")
        area = supervisor.STATE / "gold-execution-delegations"
        stage = area / candidate["delegation_sha256"]
        require(not stage.exists() and not stage.is_symlink(), "DELEGATION_ALREADY_RETAINED")
        if area.exists() or area.is_symlink():
            supervisor._root_private_directory(area)
            entries = list(area.iterdir())
            require(len(entries) < 4096, "DELEGATION_HISTORY_BUDGET")
            for entry in entries:
                require(re.fullmatch("[a-f0-9]{64}", entry.name) is not None, "DELEGATION_HISTORY_IDENTITY")
                supervisor._root_private_directory(entry)
        approval = confirmation(candidate, operator)
        _fields(
            approval,
            "schema purpose candidate_sha256 operator_id approved_at_ms "
            "consumer_enabled submission_enabled economic_authority",
        )
        require(
            approval["schema"] == "GOLD_EXECUTION_DELEGATION_REGISTRATION_APPROVAL_V1"
            and approval["purpose"] == "REGISTER_BOUNDED_FUTURE_SIGNALS_CONSUMER_DISABLED"
            and approval["candidate_sha256"] == _digest(candidate)
            and approval["operator_id"] == operator
            and type(approval["approved_at_ms"]) is int
            and candidate["delegation"]["terms"]["confirmed_at_ms"] <= approval["approved_at_ms"] <= supervisor.now_ms()
            and all(approval[k] is False for k in ("consumer_enabled", "submission_enabled", "economic_authority")),
            "DELEGATION_CONFIRMATION_CHANGED",
        )
        _same(candidate, operator, args, definition_session)
        mutated = False
        try:
            if not area.exists():
                mutated = True
                installer.directory(area, 0, 0)
            supervisor._root_private_directory(area)
            mutated = True
            installer.directory(stage, 0, 0)
            documents = {
                "candidate.json": candidate,
                "operator-material.json": candidate["operator_material"],
                "operator-confirmation.json": approval,
                "delegation.json": candidate["delegation"],
            }
            signed_raw, _ = _public(args["delegation_file"], "delegations")
            operator_raw, _ = _public(args["operator_approval_file"], "operators")
            require(
                _sha(signed_raw) == candidate["signed_file_sha256"]
                and _sha(operator_raw) == candidate["operator_file_sha256"],
                "DELEGATION_ORIGINALS_CHANGED",
            )
            for name, value in documents.items():
                body = (
                    signed_raw
                    if name == "delegation.json"
                    else operator_raw
                    if name == "operator-material.json"
                    else installer.canonical(value)
                )
                require(len(body) <= 196608, "DELEGATION_RECORD_BUDGET")
                installer.write_new(stage / name, body)
                require(supervisor._read_root_owned_json(stage / name) == body, "DELEGATION_READBACK_CHANGED")
            _same(candidate, operator, args, definition_session)
            result = dict(
                schema="GOLD_EXECUTION_DELEGATION_REGISTRATION_V2" if candidate['schema']=='GOLD_EXECUTION_DELEGATION_REVIEW_V2'
                    else "GOLD_EXECUTION_DELEGATION_REGISTRATION_V1",
                status="DELEGATION_REGISTERED_CONSUMER_DISABLED",
                source_commit=args["source_commit"],
                delegation_sha256=candidate["delegation_sha256"],
                candidate_sha256=_digest(candidate),
                operator_confirmation_sha256=_digest(approval),
                issuer_history=candidate["issuer_history"],
                executor_ledger_state_hash=candidate["executor_ledger_state_hash"],
                registered=True,
                consumer_enabled=False,
                submission_enabled=False,
                economic_authority=False,
            )
            body = installer.canonical(result)
            installer.write_new(stage / "registration.json", body)
            require(
                supervisor._read_root_owned_json(stage / "registration.json") == body, "DELEGATION_READBACK_CHANGED"
            )
            _same(candidate, operator, args, definition_session)
            return result
        except Exception as exc:
            if not mutated:
                raise
            result = dict(
                schema="GOLD_EXECUTION_DELEGATION_REGISTRATION_FAILURE_V1",
                status="INSPECT_PARTIAL_DELEGATION_REGISTRATION",
                delegation_sha256=candidate["delegation_sha256"],
                registered=False,
                consumer_enabled=False,
                submission_enabled=False,
                economic_authority=False,
            )
            if stage.is_dir() and not stage.is_symlink():
                try:
                    installer.write_new(stage / "failure.json", installer.canonical(result))
                except (OSError, ValueError):
                    pass
            raise RegistrationIncomplete(result) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--delegation-file", required=True)
    parser.add_argument("--operator-approval-file", required=True)
    parser.add_argument("--register-approved", action="store_true")
    args = vars(parser.parse_args(argv))
    approved = args.pop("register_approved")
    previous = os.umask(0o077)
    try:
        if approved:
            result = install_approved(**args)
        else:
            from tools.gold_execution_delegation_review import review as read_only

            result = read_only(**args)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except Exception as exc:
        result = (
            exc.result
            if isinstance(exc, RegistrationIncomplete)
            else dict(
                status="DELEGATION_REGISTRATION_REJECTED",
                reason="CONTINUITY_REQUIRED" if "CONTINUITY_REQUIRED" in str(exc) else "ORIGINALS_UNRESOLVED",
                registered=False,
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
