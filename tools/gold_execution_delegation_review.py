"""Read original bounded-delegation material; no execution consumer is enabled.

Only independently signed public files are accepted. Installed paths cannot be
selected by the caller. This first registration supports the already installed
EXIT_BUDGET policy and group only; scope/reserve/mandate transitions need a
separate continuity protocol. No key, grant, task or replacement book is made.
"""

import argparse
from contextlib import closing, ExitStack
import hashlib
import json
from pathlib import Path
import re

from live.execution import demo_gold_execution_delegation as contract
from live.execution import demo_gold_pilot_enrollment as enrollment
from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import (
    GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA,
    budget_group_binding,
    current_key,
    require,
)
from live.execution.demo_issuer_store import IssuerStore, consume_group, MAX_BYTES
from live.execution.demo_request_inputs import profile_declarations
from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_operating_bundle as installer, demo_supervisor as supervisor
from tools import demo_issuer, demo_successor_setup as successor, demo_operator_setup as setup
from tools import gold_strategy_budget_enrollment as strategy
from tools.gold_pilot_install import _read_target
from tools.demo_resident_recovery import _check_path
from tools.gold_strategy_controller import observation_configuration


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _public(path, kind):
    path = Path(path)
    parent = supervisor.STATE / "gold-execution-delegation-materials" / kind
    require(
        path.is_absolute() and path.parent == parent and re.fullmatch("[a-f0-9]{64}\\.json", path.name) is not None,
        "DELEGATION_PUBLIC_MATERIAL_PATH",
    )
    supervisor._root_private_directory(parent)
    raw = installer.read_private(path, maximum=65536)
    value = decode(raw)
    require(_digest(value) == path.stem, "DELEGATION_PUBLIC_MATERIAL_DIGEST")
    return raw, value


def _operator_check(value, delegation, *, signing_operation=None):
    terms = delegation["terms"]
    if delegation.get('schema')==contract.SCHEMA_V2:
        from live.execution.demo_issuer_group_continuity import operation_check
        require(type(signing_operation) is dict and signing_operation.get('confirmation')==value,
            'DELEGATION_V2_SIGN_OPERATION_REQUIRED')
        operation_check(signing_operation,purpose='DELEGATION_V2_SIGN')
        require(_digest(value)==terms['operator_approval_sha256'] and value['approved_at_ms']==terms['confirmed_at_ms'],
            'DELEGATION_OPERATOR_ORIGINAL_REQUIRED')
        return
    _fields(
        value,
        "schema purpose operator_id approved_at_ms source_commit account_ref_sha256 "
        "terms_sha256 submission_enabled economic_authority",
    )
    require(
        value["schema"] == "GOLD_EXECUTION_DELEGATION_OPERATOR_MATERIAL_V1"
        and value["purpose"] == terms["confirmation"]
        and type(value["operator_id"]) is str
        and 0 < len(value["operator_id"]) <= 128
        and type(value["approved_at_ms"]) is int
        and value["approved_at_ms"] == terms["confirmed_at_ms"]
        and value["source_commit"] == delegation["policy_template"]["executor_release_commit"]
        and value["account_ref_sha256"] == delegation["policy_template"]["account_ref_sha256"]
        and value["terms_sha256"] == _digest({k: v for k, v in terms.items() if k != "operator_approval_sha256"})
        and value["submission_enabled"] is False
        and value["economic_authority"] is False
        and _digest(value) == terms["operator_approval_sha256"],
        "DELEGATION_OPERATOR_ORIGINAL_REQUIRED",
    )


@ledger_decimal_context()
def _issuer_history(registry, policy):
    root = installer.ISSUER
    demo_issuer._state_custody(root, registry["issuer_uid"])
    path = root / "issuance.sqlite3"
    # Original IssuerStore uses rollback journals. Reading a WAL store as root
    # could create sidecars with the wrong owner; require separate role review.
    for suffix in ("-wal", "-shm", "-journal"):
        other = root / ("issuance.sqlite3" + suffix)
        require(not other.exists() and not other.is_symlink(), "DELEGATION_ISSUER_BUSY_OR_WAL")
    raw = _read_target(path, uid=registry["issuer_uid"], maximum=MAX_BYTES)
    require(
        raw[:16] == b"SQLite format 3\x00" and raw[18:20] == b"\x01\x01", "DELEGATION_ISSUER_WAL_REQUIRES_ROLE_REVIEW"
    )
    info = path.lstat()
    with closing(IssuerStore(path, read_only=True)) as store:
        rows = store.snapshot()  # Native chain, claim-index and all budgets.
    from live.execution.demo_gold_future_template import SCHEMA as TEMPLATE_SCHEMA
    if policy['schema']==TEMPLATE_SCHEMA:
        from live.execution.demo_issuer_group_continuity import history_anchor
        history=history_anchor(rows,authorization=policy)
        require(history['group_present'] and history['records']>=2,'DELEGATION_CONTINUITY_REQUIRED')
        require(_read_target(path,uid=registry['issuer_uid'],maximum=MAX_BYTES)==raw
            and (path.lstat().st_dev,path.lstat().st_ino)==(info.st_dev,info.st_ino),'DELEGATION_ISSUER_CHANGED')
        demo_issuer._state_custody(root,registry['issuer_uid'])
        return {**history,'database_sha256':_sha(raw),'device':info.st_dev,'inode':info.st_ino}
    groups = {}
    reserved = []
    signed = set()
    for row in rows:
        if row["kind"] == "RESERVED":
            value = row["payload"]
            consume_group(groups, value["policy"], value["plan"])
            reserved.append(row["proposal_sha256"])
        else:
            signed.add(row["proposal_sha256"])
    group = groups.get(policy["budget_group"]["group_id"])
    require(group is not None and group["binding"] == budget_group_binding(policy), "DELEGATION_CONTINUITY_REQUIRED")
    require(
        _read_target(path, uid=registry["issuer_uid"], maximum=MAX_BYTES) == raw
        and (path.lstat().st_dev, path.lstat().st_ino) == (info.st_dev, info.st_ino),
        "DELEGATION_ISSUER_CHANGED",
    )
    demo_issuer._state_custody(root, registry["issuer_uid"])
    consumed = {
        purpose: dict(grants=values[0], orders=values[1], notional=canonical_decimal(values[2]))
        for purpose, values in group.items()
        if purpose != "binding"
    }
    return dict(
        head_sha256=rows[-1]["record_hash"],
        records=len(rows),
        history_sha256=_digest(rows),
        database_sha256=_sha(raw),
        device=info.st_dev,
        inode=info.st_ino,
        reserved=len(reserved),
        unsigned_reservations=len(set(reserved) - signed),
        group_binding_sha256=group["binding"],
        group_consumed=consumed,
    )


def _originals(*, source_commit, delegation_file, operator_approval_file, definition_session=None):
    registry, policy = observation_configuration(source_commit)
    from live.execution.demo_gold_future_template import SCHEMA as TEMPLATE_SCHEMA
    if policy['schema']==TEMPLATE_SCHEMA:
        return _originals_v2(source_commit=source_commit,delegation_file=delegation_file,
            operator_approval_file=operator_approval_file,registry=registry,policy=policy,
            definition_session=definition_session)
    require(policy["schema"] == GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA, "DELEGATION_CONTINUITY_REQUIRED")
    raw, delegation = _public(delegation_file, "delegations")
    operator_raw, operator = _public(operator_approval_file, "operators")
    now = supervisor.now_ms()
    contract.verify(
        delegation,
        registry=registry,
        source_commit=source_commit,
        account_ref_sha256=policy["account_ref_sha256"],
        now_ms=now,
    )
    require(
        delegation["policy_template"] == policy
        and delegation["terms"]["original_budget_group_binding_sha256"] == budget_group_binding(policy),
        "DELEGATION_CONTINUITY_REQUIRED",
    )
    current_key(policy, registry, now)
    _operator_check(operator, delegation)
    terms = delegation["terms"]
    require(now < terms["opening_until_ms"], "DELEGATION_OPENING_WINDOW_EXPIRED")
    root = installer.EXECUTOR
    require(root.is_absolute() and root.resolve() == root, "DELEGATION_EXECUTOR_ROOT")
    uid = registry["executor_uid"]
    _check_path(root, uid, directory=True)
    area = root / "gold-pilot"
    _check_path(area, uid, directory=True)
    require(
        not (area / "stopped.json").exists() and not (area / "stopped.json").is_symlink(), "DELEGATION_PILOT_STOPPED"
    )
    evidence = {}

    def executor_read(path, maximum=32768):
        body = _read_target(path, uid=uid, maximum=maximum)
        evidence[str(path)] = _sha(body)
        return decode(body)

    request = executor_read(root / "gold-pilot-request.json")
    enrollment._request_check(request, source_commit=source_commit, account=policy["account_ref_sha256"])
    configuration = executor_read(area / "configuration.json")
    receipt = executor_read(area / "enrollment.json")
    intent_path = area / "enrollment-intent.json"
    intent = (
        executor_read(intent_path, enrollment.INTENT_MAXIMUM)
        if intent_path.exists() or intent_path.is_symlink()
        else None
    )
    require(intent is None or type(intent) is dict, "DELEGATION_PILOT_INTENT")
    config = enrollment.completed_configuration_records(
        configuration=configuration, receipt=receipt, intent=intent, request=request
    )
    require(
        config == terms["pilot_configuration"] and _digest(request) == terms["pilot_request_sha256"],
        "DELEGATION_PILOT_ORIGINAL_CHANGED",
    )
    runtime_raw = supervisor._read_root_owned_json(supervisor.STATE / "strategy-runtime.json")
    profile_raw = supervisor._read_root_owned_json(installer.CONFIG / "profile.json")
    runtime, profile = decode(runtime_raw), decode(profile_raw)
    strategy.runtime_check(runtime, source_commit)
    require(
        _digest(runtime) == terms["runtime_sha256"]
        and runtime["archive_source_commit"] == terms["strategy_source_commit"]
        and runtime["trial_manifest_sha256"] == terms["trial_manifest_sha256"],
        "DELEGATION_RUNTIME_CHANGED",
    )
    definition = strategy.load_definition(root, runtime, source_commit=source_commit, uid=uid,
        definition_session=definition_session)
    require(
        definition["definition_hash"] == policy["budget_group"]["strategy_definition_sha256"]
        and definition["size_and_timing"]["entry_timeout_ms"] == terms["entry_timeout_ms"]
        and terms["max_holding_ms"] <= definition["rules"]["max_holding_bars"] * 900000,
        "DELEGATION_DEFINITION_CHANGED",
    )
    profile_declarations(profile, policy, {"source_sha256": strategy.market_source_hash(), "symbol": "XAUUSDT"})
    mandate = strategy._mandate(profile["mandate"])
    require(
        mandate.issued_at <= terms["confirmed_at_ms"] < terms["exit_until_ms"] <= mandate.expires_at,
        "DELEGATION_MANDATE_WINDOW",
    )
    stage = successor.active_setup()
    installed, spec = successor.read(stage / "result.json"), setup.spec_check(successor.read(stage / "spec.json"))
    _fields(
        installed,
        "schema status spec_sha256 enrollment_sha256 source_commit task_sha256 orders_submitted economic_authority",
    )
    require(
        installed.get("schema") == "DEMO_OPERATOR_SETUP_RESULT_V1"
        and installed.get("status") == "INSTALLED_PENDING_CYCLE"
        and installed.get("orders_submitted") is False
        and installed.get("economic_authority") is False
        and installed.get("source_commit") == spec["source_commit"] == source_commit
        and installed.get("spec_sha256") == _digest(spec)
        and spec["symbol"] == "XAUUSDT"
        and spec["account_ref_sha256"] == policy["account_ref_sha256"],
        "DELEGATION_ORIGINAL_SETUP",
    )
    # The original context worker calls mkdir(exist_ok=True). Require its
    # complete pre-existing Gold scope so this review cannot initialize it.
    cycle = _digest(installed)
    cycle_area = root / "resident-recovery" / "functional-cycles" / cycle
    for directory in (cycle_area.parent.parent, cycle_area.parent, cycle_area):
        _check_path(directory, uid, directory=True)
    from tools.demo_functional_worker import scope_record

    require(
        executor_read(cycle_area / "cycle-scope.json", 1024)
        == scope_record(cycle, policy["bindings"]["strategy_id"], symbol="XAUUSDT"),
        "DELEGATION_ORIGINAL_CYCLE_SCOPE",
    )
    context = successor.context_for(source_commit, registry, cycle, allow_day_checkpoint=False)
    require(
        type(context) is dict
        and context.get("schema") == "DEMO_CANONICAL_POSITION_CONTEXT_V1"
        and context.get("symbol") == "XAUUSDT"
        and context.get("account_ref_sha256") == policy["account_ref_sha256"]
        and context.get("submission_enabled") is False
        and context.get("economic_authority") is False,
        "DELEGATION_EXECUTOR_CONTEXT",
    )
    from services.validation.demo_submission import _digest as check_digest

    check_digest(context.get("ledger_state_hash"))
    history = _issuer_history(registry, policy)
    checked_at = supervisor.now_ms()
    contract.verify(
        delegation,
        registry=registry,
        source_commit=source_commit,
        account_ref_sha256=policy["account_ref_sha256"],
        now_ms=checked_at,
    )
    require(checked_at < terms["opening_until_ms"], "DELEGATION_OPENING_WINDOW_EXPIRED")
    return dict(
        schema="GOLD_EXECUTION_DELEGATION_REVIEW_V1",
        status="DELEGATION_REVIEWED_CONSUMER_DISABLED",
        source_commit=source_commit,
        account_ref_sha256=policy["account_ref_sha256"],
        delegation=delegation,
        delegation_sha256=_digest(delegation),
        signed_file_sha256=_sha(raw),
        operator_material=operator,
        operator_file_sha256=_sha(operator_raw),
        registry_sha256=_digest(registry),
        policy_sha256=_digest(policy),
        profile_sha256=_sha(profile_raw),
        runtime_sha256=_sha(runtime_raw),
        definition_sha256=_digest(definition),
        pilot_originals=evidence,
        original_setup_sha256=_digest(installed),
        original_spec_sha256=_digest(spec),
        executor_context_sha256=_digest(context),
        executor_ledger_state_hash=context["ledger_state_hash"],
        issuer_history=history,
        consumer_enabled=False,
        submission_enabled=False,
        economic_authority=False,
    )


def _executor_state(registry,*,source_commit,account_ref_sha256):
    """Root's native RO state review, never an executor position-context claim."""
    from live.execution.demo_dispatch_store import DemoDispatchStore
    from live.execution.order_recovery import SqliteOrderRecoveryStore
    from live.execution.position_ledger import LiveLedger
    from live.execution.demo_pre_submit import _state
    root=installer.EXECUTOR;uid=registry['executor_uid']
    require(root.is_absolute() and root.resolve()==root,'DELEGATION_EXECUTOR_ROOT')
    _check_path(root,uid,directory=True)
    metadata={}
    def info(path):
        _check_path(path,uid,directory=False);value=path.lstat()
        return tuple(getattr(value,key) for key in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns'))
    with ExitStack() as stack:
        stores={}
        for name,factory in (('dispatch',DemoDispatchStore),('recovery',SqliteOrderRecoveryStore),('oms',LiveLedger)):
            path=root/(name+'.sqlite3');metadata[path]=info(path)
            for suffix in ('-wal','-shm','-journal'):
                extra=path.with_name(path.name+suffix)
                if extra.exists() or extra.is_symlink():metadata[extra]=info(extra)
            stores[name]=factory(str(path),read_only=True);stack.callback(stores[name].close)
        state=_state(**stores)
        require(_state(**stores)==state,'DELEGATION_EXECUTOR_STATE_CHANGED')
        require(all(info(path)==original for path,original in metadata.items()),'DELEGATION_EXECUTOR_FILES_CHANGED')
    _check_path(root,uid,directory=True)
    return dict(schema='GOLD_DELEGATION_EXECUTOR_STATE_REVIEW_V1',source_commit=source_commit,
        account_ref_sha256=account_ref_sha256,ledger_state_hash=_digest(state),canonical_position_context=False,
        submission_enabled=False,economic_authority=False)


def _originals_v2(*,source_commit,delegation_file,operator_approval_file,registry,policy,definition_session=None):
    from live.execution import demo_issuer_group_continuity as continuity
    from live.execution import demo_gold_budget_scope as scope_contract
    from tools import gold_future_budget_materials as future
    raw,delegation=_public(delegation_file,'delegations');operator_raw,operator=_public(operator_approval_file,'operators')
    require(delegation.get('schema')==contract.SCHEMA_V2,'DELEGATION_V2_SIGNED_ORIGINAL_REQUIRED')
    rows=future.issuer_rows(registry)
    group=[row for row in rows if row['record_hash']==delegation['continuity_event_sha256'] and row['kind'] in continuity.GROUP_KINDS]
    signed=[row for row in rows if row['kind']=='DELEGATION_V2_SIGNED' and row['payload']['artifact']==delegation]
    require(len(group)==len(signed)==1 and group[0]['seq']<signed[0]['seq'],'DELEGATION_V2_NATIVE_ADMIN_HISTORY_REQUIRED')
    event=group[0];receipt=signed[0];continuity.verify_group_change_history(event)
    _operator_check(operator,delegation,signing_operation=receipt['payload']['operation'])
    now=supervisor.now_ms();contract.verify(delegation,registry=registry,source_commit=source_commit,
        account_ref_sha256=policy['account_ref_sha256'],now_ms=now,continuity_event=event)
    require(delegation['future_template']==policy,'DELEGATION_CONTINUITY_REQUIRED')
    terms=delegation['terms'];require(now<terms['opening_until_ms'],'DELEGATION_OPENING_WINDOW_EXPIRED')
    previous=event['payload']['previous_authorization']
    records=future.authorization_ancestry(previous,registry,source_commit)
    documents=future.current_documents(source_commit=source_commit,authorization=policy,registry=registry,
        definition_session=definition_session)
    profile=documents['profile'];runtime=documents['runtime'];definition=documents['definition'];original_pilot=documents['pilot']
    require(enrollment.completed_configuration_records(**original_pilot)==terms['pilot_configuration']
        and _digest(original_pilot['request'])==terms['pilot_request_sha256']
        and _digest(runtime)==terms['runtime_sha256'] and definition['definition_hash']==policy['budget_group']['strategy_definition_sha256']
        and _digest(records['result'])==policy['origin']['setup_sha256'] and _digest(records['spec'])==policy['origin']['spec_sha256']
        and _digest(profile)==policy['origin']['profile_sha256'],'DELEGATION_V2_ORIGINALS_CHANGED')
    declaration=scope_contract._declaration_view(previous) if scope_contract.is_scope(previous) else previous
    profile_declarations(profile,declaration,{'source_sha256':strategy.market_source_hash(),'symbol':'XAUUSDT'})
    mandate=strategy._mandate(profile['mandate'])
    require(mandate.issued_at<=terms['confirmed_at_ms']<terms['exit_until_ms']<=mandate.expires_at
        and definition['size_and_timing']['entry_timeout_ms']==terms['entry_timeout_ms']
        and terms['max_holding_ms']<=definition['rules']['max_holding_bars']*900000,'DELEGATION_V2_LIMITS')
    uid=registry['executor_uid'];root=installer.EXECUTOR;area=root/'gold-pilot'
    evidence={}
    for path,maximum in ((root/'gold-pilot-request.json',32768),(area/'configuration.json',32768),
        (area/'enrollment.json',32768),(area/'enrollment-intent.json',enrollment.INTENT_MAXIMUM)):
        if path.exists() or path.is_symlink():evidence[str(path)]=_sha(_read_target(path,uid=uid,maximum=maximum))
    context=_executor_state(registry,source_commit=source_commit,account_ref_sha256=policy['account_ref_sha256'])
    history=_issuer_history(registry,policy)
    require(history['history_sha256']==_digest(rows),'DELEGATION_ISSUER_CHANGED')
    contract.verify(delegation,registry=registry,source_commit=source_commit,account_ref_sha256=policy['account_ref_sha256'],
        now_ms=supervisor.now_ms(),continuity_event=event)
    require(supervisor.now_ms()<terms['opening_until_ms'],'DELEGATION_OPENING_WINDOW_EXPIRED')
    return dict(schema='GOLD_EXECUTION_DELEGATION_REVIEW_V2',status='DELEGATION_REVIEWED_CONSUMER_DISABLED',
        source_commit=source_commit,account_ref_sha256=policy['account_ref_sha256'],delegation=delegation,
        continuity_event=event,delegation_sha256=_digest(delegation),signed_file_sha256=_sha(raw),operator_material=operator,
        operator_file_sha256=_sha(operator_raw),registry_sha256=_digest(registry),policy_sha256=_digest(policy),
        profile_sha256=_sha(supervisor._read_root_owned_json(installer.CONFIG/'profile.json')),
        runtime_sha256=_sha(supervisor._read_root_owned_json(supervisor.STATE/'strategy-runtime.json')),
        definition_sha256=_digest(definition),pilot_originals=evidence,original_setup_sha256=_digest(records['result']),
        original_spec_sha256=_digest(records['spec']),executor_context_sha256=_digest(context),
        executor_ledger_state_hash=context['ledger_state_hash'],issuer_history=history,
        consumer_enabled=False,submission_enabled=False,economic_authority=False)


def _review_locked(*, definition_session=None, **args):
    first = _originals(**args, definition_session=definition_session)
    require(_originals(**args, definition_session=definition_session) == first, "DELEGATION_ORIGINALS_CHANGED")
    return first


def review(**args):
    with supervisor.controller_lease(maintenance=True):
        return _review_locked(**args, definition_session=strategy.DefinitionReadSession())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--delegation-file", required=True)
    parser.add_argument("--operator-approval-file", required=True)
    args = vars(parser.parse_args(argv))
    try:
        print(json.dumps(review(**args), sort_keys=True, allow_nan=False))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                dict(
                    status="DELEGATION_REVIEW_REJECTED",
                    reason="CONTINUITY_REQUIRED" if "CONTINUITY_REQUIRED" in str(exc) else "ORIGINALS_UNRESOLVED",
                    consumer_enabled=False,
                    submission_enabled=False,
                    economic_authority=False,
                )
            )
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
