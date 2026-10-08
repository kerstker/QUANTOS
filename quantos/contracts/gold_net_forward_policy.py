"""Prospective weekly maturity of separate original cost-net Gold episodes."""

import copy
from decimal import Decimal

from quantos.contracts.decimal_arithmetic import canonical_decimal, ledger_decimal_context

from quantos.contracts.gold_market_state_policy import validate as validate_axis
from quantos.contracts.registered_research_run import canonical, digest, _hash

SCHEMA = "QUANTOS_GOLD_NET_FORWARD_POLICY_V1"
REVIEW_SCHEMA = "QUANTOS_GOLD_NET_FORWARD_REVIEW_V1"
WEEK_MS = 7 * 86_400_000
SCOPE = "ORIGINAL_COST_NET_FORWARD_MATURITY_NOT_PROFITABILITY_OR_INDEPENDENT_AUTHORITY"
METHOD = dict(
    period_ms=WEEK_MS,
    required_periods=8,
    account_mode="SEPARATE_ORIGINAL_EPISODES_NO_CASH_CONCATENATION",
    confidence="COMPLETED_ACTIVE_WEEKS_DIVIDED_BY_EIGHT_CAPPED_AT_ONE",
    incomplete_maturity="NO_MODEL_CONFIDENCE_UNTIL_EIGHT_COMPLETE_ACTIVE_WEEKS",
    exposure="ORIGINAL_NONZERO_POSITION_HELD_FOR_POSITIVE_TIME_IN_PERIOD",
    coverage="FULL_ORIGINAL_NET_ACCOUNT_GRID_POSITIONS_AND_RECEIVED_FUNDING",
    overlap="REJECT_DUPLICATE_COMPLETE_PERIOD",
    partial_period="DO_NOT_COUNT",
    origin="ORIGINAL_PREOBSERVATION_SELECTED_CANDIDATE_DECLARATION_REQUIRED",
    continuation="EXACT_FROZEN_STRATEGY_DEFINITION_AND_PROPOSAL",
    gross_mark_forward_reuse=False,
    maturity_is_profit_probability=False,
)
BUDGET = dict(maximum_episodes=32)


def require(ok, reason):
    if not ok:
        raise ValueError("GOLD_NET_FORWARD_" + reason)


def validate(value, *, sample=None, declared_at_ms=None):
    require(
        type(value) is dict
        and set(value)
        == {
            "schema",
            "method",
            "budget",
            "series",
            "sample",
            "declared_at_ms",
            "independent_verification",
            "economic_authority",
            "submission_enabled",
        },
        "POLICY_FIELDS",
    )
    canonical(value, maximum=65_536)
    require(value["schema"] == SCHEMA and canonical(value["method"]) == canonical(METHOD), "METHOD")
    from quantos.contracts.gold_market_state_policy import SCHEMA as AXIS_SCHEMA, POLICY as AXIS_METHOD

    axis = {k: v for k, v in value.items() if k not in ("budget", "series")}
    validate_axis(axis | dict(schema=AXIS_SCHEMA, method=AXIS_METHOD), sample=sample, declared_at_ms=declared_at_ms)
    series = value["series"]
    require(
        type(series) is dict
        and set(series)
        == {"start_ms", "declared_at_ms", "candidate_id", "strategy_definition_sha256", "proposal_sha256"},
        "SERIES_FIELDS",
    )
    require(
        all(type(series[k]) is int and 0 <= series[k] < 2**63 for k in ("start_ms", "declared_at_ms"))
        and series["declared_at_ms"] < series["start_ms"] <= value["sample"]["start_ms"]
        and series["declared_at_ms"] <= value["declared_at_ms"],
        "SERIES_TIME",
    )
    require(
        series["candidate_id"] in ("channel", "trend", "reversion", "net_portfolio")
        and all(_hash(series[k]) for k in ("strategy_definition_sha256", "proposal_sha256")),
        "SERIES_IDENTITY",
    )
    require(
        type(value["budget"]) is dict
        and set(value["budget"]) == set(BUDGET)
        and type(value["budget"]["maximum_episodes"]) is int
        and 1 <= value["budget"]["maximum_episodes"] <= BUDGET["maximum_episodes"],
        "BUDGET",
    )
    return value


def declare(*, sample, declared_at_ms, series, budget=None):
    return validate(
        dict(
            schema=SCHEMA,
            method=copy.deepcopy(METHOD),
            budget=copy.deepcopy(BUDGET if budget is None else budget),
            sample=copy.deepcopy(sample),
            declared_at_ms=declared_at_ms,
            series=copy.deepcopy(series),
            independent_verification=False,
            economic_authority=False,
            submission_enabled=False,
        )
    )


def policy_sha256(value):
    return digest(validate(value))


def series_sha256(value):
    return digest(dict(schema=SCHEMA, series=validate(value)["series"], method=value["method"], budget=value["budget"]))


@ledger_decimal_context()
def validate_review(value, *, policy, subject, candidate_id, native_result_hash, project_cut_sha256, snapshot_as_of_ms):
    """Check the bound result of the owning-source port before model consumption."""
    from quantos.streaming_identity import exact_stream_identity_hash as review_digest

    IDENTITY = ("invocation_id", "native_trial_id", "config_hash")
    validate(policy)
    require(
        type(value) is dict
        and value["schema"] == REVIEW_SCHEMA
        and value["scope"] == SCOPE
        and value["review_sha256"] == review_digest({k: v for k, v in value.items() if k != "review_sha256"}),
        "REVIEW_CHANGED",
    )
    require(
        all(value[k] == subject[k] for k in IDENTITY)
        and value["candidate_id"] == candidate_id == policy["series"]["candidate_id"]
        and value["native_result_hash"] == native_result_hash
        and value["project_cut_sha256"] == project_cut_sha256
        and value["snapshot_as_of_ms"] == snapshot_as_of_ms
        and value["policy_sha256"] == policy_sha256(policy)
        and value["series_sha256"] == series_sha256(policy),
        "REVIEW_BINDING",
    )
    require(
        all(
            value[k] is False
            for k in (
                "independent_verification",
                "economic_authority",
                "submission_enabled",
                "maturity_is_profit_probability",
            )
        )
        and value["account_mode"] == METHOD["account_mode"],
        "REVIEW_AUTHORITY",
    )
    require(
        type(value["required_periods"]) is int
        and value["required_periods"] == METHOD["required_periods"]
        and type(value["completed_active_periods"]) is int
        and value["completed_active_periods"] >= 0,
        "REVIEW_COUNTS",
    )
    require(
        type(value["episodes"]) is list
        and len(value["episodes"]) <= policy["budget"]["maximum_episodes"]
        and type(value["periods"]) is list,
        "REVIEW_SHAPE",
    )
    receipts = {review_digest(e): e for e in value["episodes"]}
    require(len(receipts) == len(value["episodes"]), "DUPLICATE_EPISODE")
    indices = set()
    active = 0
    for row in value["periods"]:
        i = row["index"]
        require(type(i) is int and i >= 0 and i not in indices, "REVIEW_PERIODS")
        indices.add(i)
        require(
            row["start_ms"] == policy["series"]["start_ms"] + i * WEEK_MS
            and row["end_ms"] == row["start_ms"] + WEEK_MS
            and row["end_ms"] <= snapshot_as_of_ms
            and type(row["active_position_interval"]) is bool,
            "REVIEW_PERIOD_AXIS",
        )
        receipt = receipts.get(row["account_receipt_sha256"])
        require(
            receipt is not None
            and receipt["full_run_covered"] is True
            and i in receipt["period_indices"]
            and all(row[k] == receipt[k] for k in IDENTITY),
            "REVIEW_PERIOD_ORIGINAL",
        )
        left, right = Decimal(row["opening_net_equity"]), Decimal(row["ending_net_equity"])
        require(
            left.is_finite()
            and right.is_finite()
            and left > 0
            and right > 0
            and canonical_decimal(right / left - 1) == row["net_return"],
            "REVIEW_NET_RETURN",
        )
        active += row["active_position_interval"]
    maturity = canonical_decimal(min(Decimal(1), Decimal(active) / METHOD["required_periods"]))
    require(value["completed_active_periods"] == active and value["normalized_maturity"] == maturity, "REVIEW_MATURITY")
    require(
        (
            value["status"] == "MATURE"
            and active >= METHOD["required_periods"]
            and value["paper_confidence"] == maturity
            and not value["reasons"]
            and not value["incomplete_invocations"]
        )
        or (value["status"] == "INSUFFICIENT" and value["paper_confidence"] is None and value["reasons"]),
        "REVIEW_VERDICT",
    )
    return value
