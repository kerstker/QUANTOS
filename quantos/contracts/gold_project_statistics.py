"""Prospective Gold study and complete population bindings, without authority."""

import copy

from quantos.contracts import gold_forward_evaluation_design as design
from quantos.contracts import research_statistical_review as common

COHORT_SCHEMA = "QUANTOS_GOLD_STATISTICAL_COHORT_V1"
SCOPED_COHORT_SCHEMA = "QUANTOS_GOLD_STATISTICAL_COHORT_V2"
BACKTEST_COHORT_SCHEMA = "QUANTOS_GOLD_BACKTEST_STATISTICAL_COHORT_V1"
COMPARISON_SCHEMA = "QUANTOS_GOLD_DECLARED_COMPARISON_V1"


def study_sha256(policy):
    from quantos.contracts import gold_reanalysis_statistics as backtest
    if policy.get('schema') == backtest.SCHEMA:
        backtest.validate(policy)
        return common.digest({key:value for key, value in policy.items() if key != 'study_scope'})
    design.validate(policy)
    common.require(policy["schema"] in design.FULL_REVIEW_SCHEMAS, "GOLD_FULL_POLICY_REQUIRED")
    # Program numbers and local registration times do not replace study content.
    return common.digest({key: policy[key] for key in (
        "source_identity_sha256", "configuration", "window", "cohort", "selection", "budget",
        "metrics", "statistical_policy", "benchmark_policy", "risk_policy", "termination", "oos_status")})


def validate_cohort(value):
    common.canonical(value, maximum=1_000_000)
    scoped = value.get("schema") in (SCOPED_COHORT_SCHEMA, BACKTEST_COHORT_SCHEMA)
    common.fields(value, "schema study_sha256 policy_sha256 project_cut_sha256 frozen_at_ms "
        "invocation_id attempts selected_attempt benchmark_attempt" + (" comparison" if scoped else ""))
    common.require(value["schema"] in (COHORT_SCHEMA, SCOPED_COHORT_SCHEMA, BACKTEST_COHORT_SCHEMA), "GOLD_COHORT_SCHEMA")
    for key in ("study_sha256", "policy_sha256", "project_cut_sha256", "invocation_id"):
        common.hashed(value[key])
    common.integer(value["frozen_at_ms"])
    rows = value["attempts"]
    common.require(type(rows) is list and 1 <= len(rows) <= common.MAX_ATTEMPTS, "GOLD_COHORT_SIZE")
    for row in rows:
        common.identity(row)
    keys = [(r["invocation_id"], r["native_trial_id"]) for r in rows]
    common.require(keys == sorted(set(keys)), "GOLD_COHORT_ORDER")
    if value["selected_attempt"] is not None:
        common.identity(value["selected_attempt"])
        common.require(value["selected_attempt"] in rows
            and value["selected_attempt"]["invocation_id"] == value["invocation_id"], "GOLD_SELECTION")
    common.embedded_benchmark(value["benchmark_attempt"])
    common.require(value["benchmark_attempt"]["invocation_id"] == value["invocation_id"], "GOLD_BENCHMARK")
    if scoped:
        validate_comparison(value["comparison"], rows, value["policy_sha256"])
    return copy.deepcopy(value)


def validate_comparison(value, attempts, policy_sha256):
    common.fields(value, "schema declaration_sha256 declared_candidate_count included_attempts excluded_attempts")
    common.require(value["schema"] == COMPARISON_SCHEMA
        and value["declaration_sha256"] == policy_sha256, "GOLD_COMPARISON_DECLARATION")
    common.integer(value["declared_candidate_count"], minimum=3, maximum=4)
    groups = []
    for name in ("included_attempts", "excluded_attempts"):
        rows = value[name]
        common.require(type(rows) is list and len(rows) <= common.MAX_ATTEMPTS, "GOLD_COMPARISON_SIZE")
        for row in rows:
            common.identity(row)
        keys = [(r["invocation_id"], r["native_trial_id"]) for r in rows]
        common.require(keys == sorted(set(keys)), "GOLD_COMPARISON_ORDER")
        groups.append(keys)
    common.require(groups[0] and not set(groups[0]) & set(groups[1])
        and sorted(groups[0] + groups[1]) == [(r["invocation_id"], r["native_trial_id"]) for r in attempts],
        "GOLD_COMPARISON_PARTITION")
    return value
