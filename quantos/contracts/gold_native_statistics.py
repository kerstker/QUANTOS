"""Bounded original Gold equity projection; no statistical or trading approval."""

from quantos.contracts import registered_research_run as native
from quantos.contracts.research_statistics import fixed_frequency_equity

SCHEMA = "QUANTOS_GOLD_NATIVE_STATISTICS_V1"
REVIEW_SCHEMA = "QUANTOS_GOLD_NATIVE_STATISTICS_V2"
FIELDS = frozenset(
    "schema status evidence_kind method native_invocation_id plan_sha256 input_sha256 "
    "source_identity_sha256 declaration_sha256 dataset_manifest_sha256 original_record_sha256 project_cut_sha256 "
    "sample attempts benchmark population statistical_review limitations economic_authority promotion_approved "
    "submission_enabled projection_sha256".split()
)
SERIES_FIELDS = frozenset(
    "status reasons native_result_sha256 available_history_sha256 grid metrics full_run_covered".split()
)
METRIC_FIELDS = frozenset(
    "initial_equity ending_equity net_equity_change fees funding reconciliation_residual "
    "max_observed_drawdown frequency_ms sample_count annualized_sharpe statistics_status".split()
)


def _series(value, sample, fields, *, reviewed=False):
    native.require(
        type(value) is dict
        and set(value) == SERIES_FIELDS | fields
        and value["status"] == "INSUFFICIENT"
        and type(value["reasons"]) is list
        and value["reasons"] == sorted(set(value["reasons"]))
        and value["reasons"]
        and type(value["full_run_covered"]) is bool,
        "GOLD_STATISTICS_SERIES",
    )
    if value["metrics"] is not None and not reviewed:
        native.require("STATISTICAL_REVIEW_NOT_IMPLEMENTED" in value["reasons"], "GOLD_STATISTICS_QUALIFICATION")
    if value["grid"] is None:
        if value["metrics"] is None:
            native.require(
                value["native_result_sha256"] is value["available_history_sha256"] is None
                and value["full_run_covered"] is False
                and value["reasons"] == ["NATIVE_ATTEMPT_NOT_COMPLETE"],
                "GOLD_STATISTICS_MISSING_SERIES",
            )
        else:
            native.require(
                type(value["metrics"]) is dict
                and set(value["metrics"]) == METRIC_FIELDS
                and native._hash(value["native_result_sha256"])
                and native._hash(value["available_history_sha256"])
                and value["metrics"]["statistics_status"] == "INSUFFICIENT_SAMPLE_COVERAGE"
                and value["metrics"]["sample_count"] == 0
                and value["metrics"]["annualized_sharpe"] is None
                and value["full_run_covered"] is False
                and "INSUFFICIENT_SAMPLE_COVERAGE" in value["reasons"],
                "GOLD_STATISTICS_UNCOVERED_SERIES",
            )
            native.require(
                {
                    "NO_INDEPENDENT_OOS_PROOF",
                    "ACCOUNT_COSTS_NOT_CALIBRATED",
                    "LOCAL_CLOCK_NOT_INDEPENDENTLY_VERIFIED",
                    "FULL_RUN_TERMINAL_NOT_COVERED",
                }
                <= set(value["reasons"]),
                "GOLD_STATISTICS_QUALIFICATION",
            )
        return
    native.require(
        native._hash(value["native_result_sha256"]) and native._hash(value["available_history_sha256"]),
        "GOLD_STATISTICS_SERIES_IDENTITY",
    )
    grid = value["grid"]
    metrics = value["metrics"]
    native.require(
        type(grid) is dict
        and type(grid.get("points")) is list
        and len(grid["points"]) <= 100_001
        and type(metrics) is dict
        and set(metrics) == METRIC_FIELDS,
        "GOLD_STATISTICS_GRID",
    )
    expected_times = list(range(sample["start_ms"], sample["end_ms"] + 1, sample["frequency_ms"]))
    native.require([p["timestamp"] for p in grid["points"]] == expected_times, "GOLD_STATISTICS_GRID_TIMES")
    recalculated = fixed_frequency_equity(
        [dict(timestamp=p["timestamp"], sequence=i, equity=p["equity"]) for i, p in enumerate(grid["points"])], **sample
    )
    native.require(
        grid == recalculated
        and metrics["frequency_ms"] == sample["frequency_ms"]
        and metrics["sample_count"] == len(grid["points"]) - 1
        and metrics["annualized_sharpe"] == grid["annualized_sharpe"]
        and metrics["statistics_status"] == grid["statistics_status"]
        and metrics["reconciliation_residual"] == "0",
        "GOLD_STATISTICS_GRID_BINDING",
    )
    native.require(
        {
            "NO_INDEPENDENT_OOS_PROOF",
            "ACCOUNT_COSTS_NOT_CALIBRATED",
            "LOCAL_CLOCK_NOT_INDEPENDENTLY_VERIFIED",
        }
        <= set(value["reasons"])
        and (value["full_run_covered"] or "FULL_RUN_TERMINAL_NOT_COVERED" in value["reasons"]),
        "GOLD_STATISTICS_QUALIFICATION",
    )


def validate(value):
    native.require(type(value) is dict, "GOLD_STATISTICS_FIELDS")
    reviewed = value.get("schema") == REVIEW_SCHEMA
    native.require(set(value) == FIELDS | ({"review"} if reviewed else set()), "GOLD_STATISTICS_FIELDS")
    native.canonical(value, maximum=native.MAX_OUTPUT_BYTES)
    native.require(
        value["schema"] in (SCHEMA, REVIEW_SCHEMA)
        and value["status"] == "TECHNICAL_VALIDATION_ONLY"
        and value["evidence_kind"] == "FORWARD"
        and value["method"] == "NATIVE_GOLD_RECEIPT_AVAILABLE_EQUITY_GRID_V1"
        and value["statistical_review"] == ("COMPLETED" if reviewed else "NOT_IMPLEMENTED")
        and all(value[k] is False for k in ("economic_authority", "promotion_approved", "submission_enabled")),
        "GOLD_STATISTICS_AUTHORITY",
    )
    for key in (
        "native_invocation_id",
        "plan_sha256",
        "input_sha256",
        "source_identity_sha256",
        "declaration_sha256",
        "project_cut_sha256",
        "projection_sha256",
    ):
        native.require(native._hash(value[key]), "GOLD_STATISTICS_IDENTITY")
    for key in ("dataset_manifest_sha256", "original_record_sha256"):
        native.require(value[key] is None or native._hash(value[key]), "GOLD_STATISTICS_IDENTITY")
    native.require(
        value["projection_sha256"] == native.digest({k: v for k, v in value.items() if k != "projection_sha256"}),
        "GOLD_STATISTICS_PROJECTION_HASH",
    )
    sample = value["sample"]
    native.require(
        type(sample) is dict
        and set(sample) == {"start_ms", "end_ms", "frequency_ms"}
        and all(type(v) is int and 0 <= v < 2**63 for v in sample.values())
        and sample["frequency_ms"] == 10_000
        and 0 <= sample["end_ms"] - sample["start_ms"] <= 2_710_000
        and (sample["end_ms"] - sample["start_ms"]) % sample["frequency_ms"] == 0,
        "GOLD_STATISTICS_SAMPLE",
    )
    attempts = value["attempts"]
    native.require(
        type(attempts) is list
        and len(attempts) in (3, 4)
        and [a["candidate_id"] for a in attempts]
        == ["channel", "trend", "reversion"] + (["net_portfolio"] if len(attempts) == 4 else [])
        and len({a["native_trial_id"] for a in attempts}) == len(attempts),
        "GOLD_STATISTICS_ATTEMPTS",
    )
    for attempt in attempts:
        native.require(
            native._hash(attempt["native_trial_id"]) and native._hash(attempt["config_hash"]),
            "GOLD_STATISTICS_ATTEMPT_IDENTITY",
        )
        _series(attempt, sample, {"candidate_id", "native_trial_id", "config_hash"}, reviewed=reviewed)
    baseline = value["benchmark"]
    if baseline is not None:
        _series(baseline, sample, {"id"}, reviewed=reviewed)
        native.require(
            baseline["id"] == "GOLD_NO_ORDER_ORIGINAL_ACCOUNT_V1"
            and baseline["metrics"]["net_equity_change"]
            == baseline["metrics"]["fees"]
            == baseline["metrics"]["funding"]
            == "0"
            and (
                baseline["grid"] is None
                or all(p["equity"] == baseline["metrics"]["initial_equity"] for p in baseline["grid"]["points"])
            ),
            "GOLD_STATISTICS_BENCHMARK",
        )
    population = value["population"]
    native.require(
        type(population) is dict
        and set(population)
        == {"attempt_count", "unique_definition_count", "coverage", "all_attempts", "unfinished_runs"}
        and type(population["all_attempts"]) is list
        and type(population["attempt_count"]) is int
        and population["attempt_count"] == len(population["all_attempts"])
        and population["coverage"] in ("COMPLETE", "UNMAPPED")
        and type(population["unfinished_runs"]) is list,
        "GOLD_STATISTICS_POPULATION",
    )
    selected = [a for a in population["all_attempts"] if a["invocation_id"] == value["native_invocation_id"]]
    native.require(
        {(a["native_trial_id"], a["config_hash"]) for a in selected}
        == {(a["native_trial_id"], a["config_hash"]) for a in attempts},
        "GOLD_STATISTICS_POPULATION_BINDING",
    )
    native.require(
        type(value["limitations"]) is list and "NO_RESEARCH_OR_EXECUTION_AUTHORITY" in value["limitations"],
        "GOLD_STATISTICS_LIMITATIONS",
    )
    if reviewed:
        from quantos.contracts import research_statistical_review as statistical
        review = statistical.validate_report(value["review"])
        native.require(review["schema"] in statistical.GOLD_REPORT_SCHEMAS
            and review["project_cut_sha256"] == value["project_cut_sha256"]
            and review["policy_sha256"] == value["declaration_sha256"]
            and review["population"]["attempt_count"] == population["attempt_count"]
            and review["benchmark_attempt"]["invocation_id"] == value["native_invocation_id"]
            and review["benchmark_attempt"]["native_result_sha256"]
                == (baseline["native_result_sha256"] if baseline is not None else None)
            and review["benchmark_attempt"]["original_record_sha256"] == value["original_record_sha256"],
            "GOLD_STATISTICS_REVIEW_BINDING")
        native.require({(r["identity"]["invocation_id"], r["identity"]["native_trial_id"], r["identity"]["config_hash"])
            for r in review["attempts"]} == {(r["invocation_id"], r["native_trial_id"], r["config_hash"])
            for r in population["all_attempts"]}, "GOLD_STATISTICS_REVIEW_POPULATION")
        if review["schema"] in statistical.SCOPED_GOLD_REPORT_SCHEMAS:
            included = {(r["invocation_id"], r["native_trial_id"], r["config_hash"])
                for r in review["comparison"]["included_attempts"]}
            current = {(value["native_invocation_id"], r["native_trial_id"], r["config_hash"]) for r in attempts}
            native.require(current <= included, "GOLD_STATISTICS_COMPARISON_BINDING")
            if current != included:
                native.require("DECLARATION_ATTEMPT_BUDGET_MISMATCH" in review["reasons"]
                    and review["metrics"]["matrix_sha256"] is None
                    and all(review["checks"][k] == "INSUFFICIENT"
                        for k in ("dsr", "pbo", "cpcv_positive", "cpcv_tail")), "GOLD_STATISTICS_DUPLICATE_DECLARATION")
        selected_review = review['selected_attempt']
        if selected_review is not None:
            matches = [a for a in attempts if a['native_trial_id'] == selected_review['native_trial_id']]
            native.require(selected_review['invocation_id'] == value['native_invocation_id']
                and len(matches) == 1 and matches[0]['config_hash'] == selected_review['config_hash'],
                'GOLD_STATISTICS_REVIEW_SELECTION')
            cash = review['metrics']['selected_cash']
            native.require((cash['full_run'] if cash is not None else None) == matches[0]['metrics'],
                'GOLD_STATISTICS_REVIEW_CASH')
    return value
