"""Prospective net-performance maintenance, without IC or lifecycle authority."""

import copy
from decimal import Decimal

from quantos.contracts import gold_net_forward_policy as forward
from quantos.contracts.decimal_arithmetic import canonical_decimal, ledger_decimal_context
from quantos.contracts.registered_research_run import canonical, digest, _hash
from quantos.streaming_identity import exact_stream_identity_hash as stream_digest

SCHEMA = "QUANTOS_GOLD_NET_MAINTENANCE_POLICY_V1"
REVIEW_SCHEMA = "QUANTOS_GOLD_NET_MAINTENANCE_REVIEW_V1"
SCOPE = "ORIGINAL_COST_NET_PERFORMANCE_DECAY_NOT_IC_OR_LIFECYCLE_AUTHORITY"
METHOD = dict(
    baseline_periods=4,
    confirm_periods=3,
    maximum_performance_gap="0.6",
    baseline="FIRST_FOUR_COMPLETE_ACTIVE_NET_WEEKS_POSITIVE_ARITHMETIC_MEAN",
    comparison="EACH_SUBSEQUENT_NET_WEEK_AGAINST_ORIGINAL_BASELINE",
    breach="STRICTLY_GREATER_THAN_ORIGINAL_ALPHA_MAX_PERF_GAP",
    confirmation="THREE_CONSECUTIVE_ACTIVE_WEEK_BREACHES",
    known_failure="RETAIN_FIRST_CONFIRMED_DECAY_NO_AUTOMATIC_RECOVERY",
    healthy="MATURE_FORWARD_CONTIGUOUS_ACTIVE_COVERAGE_AND_NO_RECENT_PENDING_BREACH",
    account_mode=forward.METHOD["account_mode"],
    invented_ic=False,
    lifecycle_mutation=False,
    independent_authority=False,
)


def require(ok, reason):
    if not ok:
        raise ValueError("GOLD_NET_MAINTENANCE_" + reason)


def series_sha256(net_forward_policy):
    return digest(dict(schema=SCHEMA, method=METHOD, forward_series_sha256=forward.series_sha256(net_forward_policy)))


def validate(value, *, net_forward_policy):
    f = forward.validate(net_forward_policy)
    require(
        type(value) is dict
        and set(value)
        == {
            "schema",
            "method",
            "sample",
            "declared_at_ms",
            "net_forward_policy_sha256",
            "series_sha256",
            "independent_verification",
            "economic_authority",
            "submission_enabled",
        },
        "POLICY_FIELDS",
    )
    canonical(value, maximum=65_536)
    require(value["schema"] == SCHEMA and canonical(value["method"]) == canonical(METHOD), "METHOD")
    require(
        type(value["declared_at_ms"]) is int
        and value["declared_at_ms"] == f["declared_at_ms"]
        and canonical(value["sample"]) == canonical(f["sample"])
        and value["net_forward_policy_sha256"] == forward.policy_sha256(f)
        and value["series_sha256"] == series_sha256(f),
        "FORWARD_BINDING",
    )
    require(
        all(value[k] is False for k in ("independent_verification", "economic_authority", "submission_enabled")),
        "AUTHORITY",
    )
    return value


def declare(*, net_forward_policy):
    f = forward.validate(net_forward_policy)
    return validate(
        dict(
            schema=SCHEMA,
            method=copy.deepcopy(METHOD),
            sample=copy.deepcopy(f["sample"]),
            declared_at_ms=f["declared_at_ms"],
            net_forward_policy_sha256=forward.policy_sha256(f),
            series_sha256=series_sha256(f),
            independent_verification=False,
            economic_authority=False,
            submission_enabled=False,
        ),
        net_forward_policy=f,
    )


def policy_sha256(value, *, net_forward_policy):
    return digest(validate(value, net_forward_policy=net_forward_policy))


@ledger_decimal_context()
def period_assessment(net_forward_review, *, declarations_ready):
    """Deterministic receipt-level semantics, also checked at model consumption."""
    periods = sorted(net_forward_review["periods"], key=lambda p: p["index"])
    by_index = {p["index"]: p for p in periods}
    reasons = set()
    if not declarations_ready:
        reasons.add("PREREGISTERED_MAINTENANCE_DECLARATIONS_REQUIRED")
    if net_forward_review["status"] != "MATURE":
        reasons.add("ORIGINAL_EIGHT_WEEK_FORWARD_MATURITY_REQUIRED")
    baseline_indices = list(range(METHOD["baseline_periods"]))
    baseline = None
    if not all(i in by_index and by_index[i]["active_position_interval"] for i in baseline_indices):
        reasons.add("FIRST_FOUR_COMPLETE_ACTIVE_NET_WEEKS_REQUIRED")
    else:
        baseline = sum((Decimal(by_index[i]["net_return"]) for i in baseline_indices), Decimal(0)) / len(
            baseline_indices
        )
        if baseline <= 0:
            reasons.add("POSITIVE_ORIGINAL_NET_BASELINE_REQUIRED")
    if (
        not periods
        or any(p["index"] != index for index, p in enumerate(periods))
        or any(not p["active_position_interval"] for p in periods)
    ):
        reasons.add("CONTIGUOUS_ACTIVE_ORIGINAL_WEEK_COVERAGE_REQUIRED")
    after = [p for p in periods if p["index"] >= METHOD["baseline_periods"]]
    recent = after[-METHOD["confirm_periods"] :]
    if len(recent) < METHOD["confirm_periods"]:
        reasons.add("THREE_RECENT_ACTIVE_NET_WEEKS_REQUIRED")
    assessed = []
    run = []
    confirmed = []
    for p in after:
        gap = None if baseline is None or baseline <= 0 else (baseline - Decimal(p["net_return"])) / baseline
        breached = None if gap is None else gap > Decimal(METHOD["maximum_performance_gap"])
        if breached and p["active_position_interval"]:
            if run and p["index"] != run[-1] + 1:
                run = []
            run.append(p["index"])
            if len(run) >= METHOD["confirm_periods"] and not confirmed:
                confirmed = run[-METHOD["confirm_periods"] :]
        else:
            run = []
        assessed.append(
            dict(
                index=p["index"],
                original_period_sha256=stream_digest(p),
                performance_gap=None if gap is None else canonical_decimal(gap),
                breached=breached,
            )
        )
    recent_ids = [p["index"] for p in recent]
    if any(p["breached"] for p in assessed if p["index"] in recent_ids) and not confirmed:
        reasons.add("PENDING_DECAY_CONFIRMATION")
    if confirmed and declarations_ready:
        reasons.add("SUSTAINED_COST_NET_PERFORMANCE_GAP")
        status, flag = "FAIL", True
    else:
        status, flag = ("INSUFFICIENT", None) if reasons else ("PASS", False)
    recent_mean = (
        None
        if len(recent) < METHOD["confirm_periods"]
        else sum((Decimal(p["net_return"]) for p in recent), Decimal(0)) / len(recent)
    )
    return dict(
        status=status,
        decay_flag=flag,
        reasons=sorted(reasons),
        baseline_period_indices=baseline_indices,
        baseline_mean_net_return=None if baseline is None else canonical_decimal(baseline),
        recent_period_indices=recent_ids,
        recent_mean_net_return=None if recent_mean is None else canonical_decimal(recent_mean),
        first_confirmed_decay_period_indices=confirmed,
        period_assessments=assessed,
    )


def validate_declarations(rows, *, policy, net_forward_policy, net_forward_review):
    require(type(rows) is list and len(rows) == len(net_forward_review["episodes"]), "DECLARATION_SHAPE")
    identity = ("invocation_id", "native_trial_id", "config_hash")
    by_id = {tuple(r[k] for k in identity): r for r in rows}
    require(len(by_id) == len(rows), "DUPLICATE_DECLARATION")
    ready = True
    origin = False
    for receipt in net_forward_review["episodes"]:
        row = by_id.get(tuple(receipt[k] for k in identity))
        require(row is not None and type(row["available"]) is bool, "DECLARATION_BINDING")
        if not row["available"]:
            ready = False
            continue
        require(
            _hash(row["design_sha256"])
            and _hash(row["policy_sha256"])
            and row["forward_policy_sha256"] == receipt["policy_sha256"]
            and row["series_sha256"] == policy["series_sha256"]
            and canonical(row["sample"]) == canonical(receipt["sample"])
            and type(row["declared_at_ms"]) is int
            and 0 <= row["declared_at_ms"] < row["sample"]["start_ms"],
            "DECLARATION_BINDING",
        )
        if all(row[k] == net_forward_review[k] for k in identity):
            require(
                row["policy_sha256"] == policy_sha256(policy, net_forward_policy=net_forward_policy), "SELECTED_POLICY"
            )
        if receipt["sample"]["start_ms"] == net_forward_policy["series"]["start_ms"]:
            origin = True
            require(row["declared_at_ms"] == net_forward_policy["series"]["declared_at_ms"], "ORIGIN_DECLARATION")
    return ready and origin


@ledger_decimal_context()
def validate_review(
    value,
    *,
    policy,
    net_forward_policy,
    net_forward_review,
    subject,
    candidate_id,
    native_result_hash,
    project_cut_sha256,
    snapshot_as_of_ms,
):
    validate(policy, net_forward_policy=net_forward_policy)
    forward.validate_review(
        net_forward_review,
        policy=net_forward_policy,
        subject=subject,
        candidate_id=candidate_id,
        native_result_hash=native_result_hash,
        project_cut_sha256=project_cut_sha256,
        snapshot_as_of_ms=snapshot_as_of_ms,
    )
    require(
        type(value) is dict
        and value["schema"] == REVIEW_SCHEMA
        and value["scope"] == SCOPE
        and value["review_sha256"] == stream_digest({k: v for k, v in value.items() if k != "review_sha256"}),
        "REVIEW_CHANGED",
    )
    require(
        all(value[k] == subject[k] for k in ("invocation_id", "native_trial_id", "config_hash"))
        and value["candidate_id"] == candidate_id
        and value["native_result_hash"] == native_result_hash
        and value["project_cut_sha256"] == project_cut_sha256
        and value["snapshot_as_of_ms"] == snapshot_as_of_ms
        and value["policy_sha256"] == policy_sha256(policy, net_forward_policy=net_forward_policy)
        and value["series_sha256"] == policy["series_sha256"]
        and value["net_forward_review_sha256"] == net_forward_review["review_sha256"],
        "REVIEW_BINDING",
    )
    require(
        all(
            value[k] is False
            for k in (
                "independent_verification",
                "economic_authority",
                "submission_enabled",
                "lifecycle_changed",
                "invented_ic",
            )
        ),
        "REVIEW_AUTHORITY",
    )
    ready = validate_declarations(
        value["declarations"],
        policy=policy,
        net_forward_policy=net_forward_policy,
        net_forward_review=net_forward_review,
    )
    expected = period_assessment(net_forward_review, declarations_ready=ready)
    require(all(canonical(value[k]) == canonical(v) for k, v in expected.items()), "REVIEW_FINDING")
    return value
