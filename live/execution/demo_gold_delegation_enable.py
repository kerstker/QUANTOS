"""Strict public ENABLE evidence, separate from disabled registration.

Only fixed root-owned public files are read. This module never signs, grants,
constructs a human identity, reads an executor archive or changes a budget.
"""

from contextlib import contextmanager
from decimal import Decimal
import fcntl
import os
from pathlib import Path
import stat

from live.execution import demo_gold_execution_delegation as delegation_contract
from live.execution.demo_authority import _read_root_owned_json
from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import budget_group_binding, require
from services.execution.backtest_authority import exact_identity_hash
from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from services.validation.demo_submission import _integer, _digest as check_digest

PUBLIC_ROOT = Path("/etc/quantos/demo-gold-delegation")
SCHEMA = "DEMO_GOLD_DELEGATION_ENABLE_V1"
SCHEMA_V2 = "DEMO_GOLD_DELEGATION_ENABLE_V2"
FIELDS = (
    "schema delegation runtime definition registration_sha256 registration_candidate_sha256 "
    "issuer_anchor original_binding operator_confirmation consumer_enabled submission_enabled economic_authority"
)
ANCHOR_FIELDS = "head_sha256 records history_sha256 group_binding_sha256 group_consumed reserved unsigned_reservations"
BINDING_FIELDS = (
    "registry_sha256 policy_sha256 profile_sha256 runtime_sha256 definition_sha256 "
    "original_setup_sha256 original_spec_sha256 executor_ledger_state_hash dispatcher_sha256 issuer_current"
)


def candidate(value):
    """Recreate exactly the candidate the independent operator confirmed."""
    return {
        **{k: v for k, v in value.items() if k not in ("schema", "operator_confirmation", "consumer_enabled")},
        "schema": "GOLD_EXECUTION_DELEGATION_ENABLE_CANDIDATE_V2" if value.get('schema')==SCHEMA_V2
            else "GOLD_EXECUTION_DELEGATION_ENABLE_CANDIDATE_V1",
        "consumer_enabled": False,
    }


@ledger_decimal_context()
def anchor_check(anchor):
    if type(anchor) is dict and anchor.get('schema')=='DEMO_GOLD_ISSUER_HISTORY_ANCHOR_V2':
        from live.execution.demo_issuer_group_continuity import _anchor_check
        return _anchor_check(anchor)
    _fields(anchor, ANCHOR_FIELDS)
    for name in ("head_sha256", "history_sha256", "group_binding_sha256"):
        check_digest(anchor[name])
    for name in ("records", "reserved", "unsigned_reservations"):
        _integer(anchor[name])
    require(
        0 < anchor["records"] <= 50000
        and 0 < anchor["reserved"] <= anchor["records"]
        and 0 <= anchor["unsigned_reservations"] <= anchor["reserved"],
        "DELEGATION_ENABLE_HISTORY_COUNTS",
    )
    _fields(anchor["group_consumed"], "submission cancellation")
    for counts in anchor["group_consumed"].values():
        _fields(counts, "grants orders notional")
        _integer(counts["grants"])
        _integer(counts["orders"])
        require(
            type(counts["notional"]) is str
            and Decimal(counts["notional"]).is_finite()
            and Decimal(counts["notional"]) >= 0
            and canonical_decimal(Decimal(counts["notional"])) == counts["notional"],
            "DELEGATION_ENABLE_HISTORY_NOTIONAL",
        )
    return anchor


@ledger_decimal_context()
def verify_history_prefix(anchor, rows, *, policy):
    """Check a native IssuerStore-verified chain, including unsigned charges."""
    if type(anchor) is dict and anchor.get('schema')=='DEMO_GOLD_ISSUER_HISTORY_ANCHOR_V2':
        from live.execution.demo_issuer_group_continuity import verify_history_prefix as verify_prefix
        return verify_prefix(anchor,rows,authorization=policy)
    # Lazy import permits IssuerStore to call this inside its original transaction.
    from live.execution.demo_issuer_store import consume_group

    anchor_check(anchor)
    require(type(rows) is list and len(rows) >= anchor["records"], "DELEGATION_ENABLE_HISTORY_ROLLBACK")
    prefix = rows[: anchor["records"]]
    require(
        prefix[-1]["seq"] == anchor["records"]
        and prefix[-1]["record_hash"] == anchor["head_sha256"]
        and _digest(prefix) == anchor["history_sha256"],
        "DELEGATION_ENABLE_HISTORY_PREFIX",
    )
    groups = {}
    reserved = []
    signed = set()
    for row in prefix:
        if row["kind"] == "RESERVED":
            consume_group(groups, row["payload"]["policy"], row["payload"]["plan"])
            reserved.append(row["proposal_sha256"])
        else:
            require(row["kind"] == "SIGNED", "DELEGATION_ENABLE_HISTORY_KIND")
            signed.add(row["proposal_sha256"])
    group = groups.get(policy["budget_group"]["group_id"])
    require(
        group is not None and group["binding"] == anchor["group_binding_sha256"] == budget_group_binding(policy),
        "DELEGATION_ENABLE_HISTORY_GROUP",
    )
    consumed = {
        purpose: dict(grants=values[0], orders=values[1], notional=canonical_decimal(values[2]))
        for purpose, values in group.items()
        if purpose != "binding"
    }
    require(
        consumed == anchor["group_consumed"]
        and len(reserved) == anchor["reserved"]
        and len(set(reserved) - signed) == anchor["unsigned_reservations"],
        "DELEGATION_ENABLE_HISTORY_CONSUMED",
    )


def _runtime(runtime, terms, source_commit):
    extra = {
        "GOLD_BAR_RETAINED_EPISODE_INPUT_V1": "observation_sha256 archive_source_commit entry_decision_ms",
        "GOLD_BAR_FRESH_EPISODE_INPUT_V1": "archive_source_commit candidate seconds history_sha256",
        "GOLD_BAR_AUTO_EPISODE_INPUT_V1": "archive_source_commit candidate seconds episode_id",
    }
    require(type(runtime) is dict and runtime.get("schema") in extra, "DELEGATION_ENABLE_RUNTIME")
    _fields(runtime, "schema source_commit trial_manifest_sha256 " + extra[runtime["schema"]])
    require(
        runtime["source_commit"] == source_commit
        and _digest(runtime) == terms["runtime_sha256"]
        and runtime["archive_source_commit"] == terms["strategy_source_commit"]
        and runtime["trial_manifest_sha256"] == terms["trial_manifest_sha256"],
        "DELEGATION_ENABLE_RUNTIME_BINDING",
    )
    for name in ("trial_manifest_sha256", "observation_sha256", "history_sha256", "episode_id"):
        if name in runtime:
            check_digest(runtime[name])
    if "entry_decision_ms" in runtime:
        _integer(runtime["entry_decision_ms"])
    else:
        require(
            runtime["candidate"] in ("channel", "trend", "reversion")
            and type(runtime["seconds"]) is int
            and 1 <= runtime["seconds"] <= 900,
            "DELEGATION_ENABLE_RUNTIME_PLAN",
        )


def validate(value, *, registry, source_commit, account_ref_sha256, now_ms):
    """Validate content; every actual role must separately use load_enabled."""
    future=type(value) is dict and value.get('schema')==SCHEMA_V2
    _fields(value, FIELDS+(' continuity_event' if future else ''))
    require(
        value["schema"] == (SCHEMA_V2 if future else SCHEMA)
        and value["consumer_enabled"] is True
        and value["submission_enabled"] is False
        and value["economic_authority"] is False,
        "DELEGATION_ENABLE_REQUIRED",
    )
    delegation = value["delegation"]
    require(type(delegation) is dict and delegation.get('schema')==(
        delegation_contract.SCHEMA_V2 if future else delegation_contract.SCHEMA),'DELEGATION_ENABLE_VERSION')
    delegation_contract.verify(
        delegation, registry=registry, source_commit=source_commit, account_ref_sha256=account_ref_sha256, now_ms=now_ms,
        **({'continuity_event':value['continuity_event']} if future else {})
    )
    policy = delegation_contract.delegation_base(delegation)
    if future:
        from live.execution.demo_gold_future_template import template_group_binding
        binding=template_group_binding(policy)
    else:binding=budget_group_binding(policy)
    terms = delegation["terms"]
    for name in ("registration_sha256", "registration_candidate_sha256"):
        check_digest(value[name])
    anchor_check(value["issuer_anchor"])
    require(value["issuer_anchor"]["group_binding_sha256"] == binding, "DELEGATION_ENABLE_GROUP")
    _fields(value["original_binding"], BINDING_FIELDS)
    for name, digest in value["original_binding"].items():
        if name != "issuer_current":
            check_digest(digest)
    anchor_check(value["original_binding"]["issuer_current"])
    require(
        value["original_binding"]["issuer_current"]["group_binding_sha256"] == binding
        and value["original_binding"]["issuer_current"]["records"] >= value["issuer_anchor"]["records"],
        "DELEGATION_ENABLE_CURRENT_HISTORY",
    )
    if future:
        for anchor in (value['issuer_anchor'],value['original_binding']['issuer_current']):
            require(anchor.get('schema')=='DEMO_GOLD_ISSUER_HISTORY_ANCHOR_V2'
                and anchor['records']>=2 and anchor['group_present'] is True
                and anchor['continuity_event_sha256']==delegation['continuity_event_sha256'],
                'DELEGATION_ENABLE_ADMIN_HISTORY_REQUIRED')
    require(
        value["original_binding"]["registry_sha256"] == _digest(registry)
        and value["original_binding"]["policy_sha256"] == _digest(policy),
        "DELEGATION_ENABLE_ORIGINAL_BINDING",
    )
    _runtime(value["runtime"], terms, source_commit)
    definition = value["definition"]
    require(
        type(definition) is dict
        and definition.get("schema") == "GOLD_BAR_RESEARCH_DEFINITION_V1"
        and definition.get("definition_hash") == policy["budget_group"]["strategy_definition_sha256"]
        and exact_identity_hash(
            {k: v for k, v in definition.items() if k != "definition_hash"}, name="gold_bar_research_definition"
        )
        == definition["definition_hash"]
        and _digest(definition) == value["original_binding"]["definition_sha256"]
        and definition["source"]["commit"] == terms["strategy_source_commit"]
        and definition["size_and_timing"]["entry_timeout_ms"] == terms["entry_timeout_ms"]
        and terms["max_holding_ms"] <= definition["rules"]["max_holding_bars"] * 900000,
        "DELEGATION_ENABLE_DEFINITION",
    )
    approval = value["operator_confirmation"]
    _fields(
        approval,
        "schema purpose candidate_sha256 operator_id approved_at_ms "
        "consumer_enabled submission_enabled economic_authority",
    )
    require(
        approval["schema"] == "GOLD_EXECUTION_DELEGATION_ENABLE_APPROVAL_V1"
        and approval["purpose"] == "ENABLE_BOUNDED_FUTURE_SIGNALS"
        and approval["candidate_sha256"] == _digest(candidate(value))
        and type(approval["operator_id"]) is str
        and 0 < len(approval["operator_id"]) <= 128
        and type(approval["approved_at_ms"]) is int
        and terms["confirmed_at_ms"] <= approval["approved_at_ms"] < terms["opening_until_ms"]
        and approval["approved_at_ms"] <= now_ms
        and approval["consumer_enabled"] is True
        and approval["submission_enabled"] is False
        and approval["economic_authority"] is False,
        "DELEGATION_ENABLE_OPERATOR_CONFIRMATION",
    )
    return value


def _public_metadata(info, *, directory):
    require(
        (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        and info.st_uid == info.st_gid == 0
        and stat.S_IMODE(info.st_mode) == (0o755 if directory else 0o444),
        "DELEGATION_ENABLE_PUBLIC_CUSTODY",
    )
    if not directory:
        require(info.st_nlink == 1 and 0 < info.st_size <= 65536, "DELEGATION_ENABLE_PUBLIC_FILE")


@contextmanager
def public_lease(*, exclusive=False):
    require(PUBLIC_ROOT.is_absolute() and ".." not in PUBLIC_ROOT.parts, "DELEGATION_ENABLE_PUBLIC_PATH")
    fd = os.open(PUBLIC_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _public_metadata(os.fstat(fd), directory=True)
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("DELEGATION_ENABLE_PUBLIC_BUSY") from None
        require(
            (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == (PUBLIC_ROOT.lstat().st_dev, PUBLIC_ROOT.lstat().st_ino),
            "DELEGATION_ENABLE_PUBLIC_CHANGED",
        )
        yield fd
        current = PUBLIC_ROOT.lstat()
        require(
            (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == (current.st_dev, current.st_ino),
            "DELEGATION_ENABLE_PUBLIC_CHANGED",
        )
        _public_metadata(current, directory=True)
    finally:
        os.close(fd)


def _read(name):
    require(name in ('enable.json','complete.json'),'DELEGATION_ENABLE_PUBLIC_NAME')
    def metadata(info):
        _public_metadata(info,directory=False)
        if name=='complete.json':require(info.st_size<=4096,'DELEGATION_ENABLE_COMPLETE_BUDGET')
    return _read_root_owned_json(PUBLIC_ROOT / name, file_check=metadata)


def _load_locked(*, registry, source_commit, account_ref_sha256, now_ms):
    require(set(os.listdir(PUBLIC_ROOT)) == {"enable.json", "complete.json"}, "DELEGATION_ENABLE_INCOMPLETE")
    raw = _read("enable.json")
    complete_raw = _read("complete.json")
    value = validate(
        decode(raw),
        registry=registry,
        source_commit=source_commit,
        account_ref_sha256=account_ref_sha256,
        now_ms=now_ms,
    )
    complete = decode(complete_raw)
    _fields(complete, "schema enable_sha256 consumer_enabled submission_enabled economic_authority")
    require(
        complete
        == dict(
            schema="DEMO_GOLD_DELEGATION_ENABLE_COMPLETE_V1",
            enable_sha256=_digest(value),
            consumer_enabled=True,
            submission_enabled=False,
            economic_authority=False,
        ),
        "DELEGATION_ENABLE_INCOMPLETE",
    )
    require(
        _read("enable.json") == raw
        and _read("complete.json") == complete_raw
        and set(os.listdir(PUBLIC_ROOT)) == {"enable.json", "complete.json"},
        "DELEGATION_ENABLE_PUBLIC_CHANGED",
    )
    return value


def load_enabled(*, registry, source_commit, account_ref_sha256, now_ms, role):
    require(role in ("controller", "executor", "issuer"), "DELEGATION_ENABLE_ROLE")
    if role == "controller":
        require(os.geteuid() == 0, "DELEGATION_ENABLE_ROLE_IDENTITY")
    else:
        uid = registry[role + "_uid"]
        require(os.geteuid() == os.getegid() == uid and set(os.getgroups()) <= {uid}, "DELEGATION_ENABLE_ROLE_IDENTITY")
    with public_lease():
        return _load_locked(
            registry=registry, source_commit=source_commit, account_ref_sha256=account_ref_sha256, now_ms=now_ms
        )
