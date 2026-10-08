"""Replay a retained original backtest in its own full frozen population."""

from quantos.contracts import gold_frozen_validation_policy as shape
from quantos.contracts import gold_forward_evaluation_design as forward
from quantos.contracts import gold_native_source
from quantos.contracts.research_evidence_json import digest
from services.validation import project_research_evidence_artifact as artifact
from services.validation import project_research_evidence_authority as authority
from services.validation.project_research_population import validate_project_cut


def review_original(*, reports, project_cut, subject, candidate_id, policy, native_result_hash, snapshot_as_of_ms):
    shape.validate(policy)
    validate_project_cut(project_cut)
    ref = policy["reference"]
    rows = getattr(reports, "original_review_rows", None)
    shape.require(isinstance(rows, dict) and ref["review_id"] in rows, "ORIGINAL_ROW_REQUIRED")
    row = rows[ref["review_id"]]
    shape.require(
        row["run_id"] == ref["review_id"]
        and row["content_hash"] == ref["content_hash"]
        and row["recorded_ts"] == ref["registered_at_ms"],
        "ROW_BINDING",
    )
    # Check the earlier BACKTEST and its ancestry before replaying its artifact.
    # This forbids selecting a FORWARD row or making cyclic review dependencies.
    value = artifact.decode(row["evaluation_json"].encode())
    snapshot = value["snapshot"]
    old = snapshot["project_cut"]
    old_subject = snapshot["subject"]
    shape.require(
        value["schema"] == artifact.STATISTICAL_SCHEMA
        and old_subject["evidence_kind"] == "BACKTEST"
        and all(old_subject[k] == ref[k] for k in ("invocation_id", "native_trial_id", "config_hash"))
        and old["cut_sha256"] == ref["project_cut_sha256"]
        and value["assessment"]["checked_at_ms"] == ref["reviewed_at_ms"],
        "BACKTEST_REFERENCE",
    )
    old_run = old["runs"][ref["invocation_id"]]
    core = old_run["plan"].get("evaluation_core")
    shape.require(
        core is not None
        and core["evidence_kind"] == "BACKTEST"
        and core["selection"]["candidate_id"] == candidate_id == policy["candidate_id"],
        "SELECTED_BACKTEST",
    )
    shape.require(core["selection"]["validation_policy_sha256"] == ref["statistical_policy_sha256"], "POLICY_BINDING")
    artifact.verify_registry_prefix(
        value, population=project_cut["registry_population"], history=project_cut["registry_history"]
    )
    shape.require(
        all(
            k in project_cut["runs"] and authority.canonical(v) == authority.canonical(project_cut["runs"][k])
            for k, v in old["runs"].items()
        ),
        "NATIVE_RUN_PREFIX",
    )
    current_attempts = {(a["invocation_id"], a["native_trial_id"]): a for a in project_cut["attempts"]}
    shape.require(
        all(current_attempts.get((a["invocation_id"], a["native_trial_id"])) == a for a in old["attempts"]),
        "ATTEMPT_PREFIX",
    )
    metadata = {r["run_id"]: r for r in project_cut["registry_history"]["evaluations"]}
    shape.require(
        ref["review_id"] in metadata and metadata[ref["review_id"]] == {k: row[k] for k in artifact.METADATA_FIELDS},
        "PERSISTED_ROW_METADATA",
    )
    current = project_cut["runs"][subject["invocation_id"]]["plan"]
    design = current["forward_design"]
    origin = design["risk_policy"]["net_forward_policy"]["series"]
    selected = next(t for t in current["trials"] if t["candidate_id"] == candidate_id)
    shape.require(
        current["search"]["search_kind"] == "FORWARD"
        and design["risk_policy"].get("frozen_validation_policy") == policy
        and design["selection"]["candidate_id"] == candidate_id
        and all(subject[k] == selected[k] for k in ("native_trial_id", "config_hash")),
        "CURRENT_DECLARATION",
    )
    original_report = reports[subject["invocation_id"]]
    actual = (
        original_report["portfolio"]
        if candidate_id == "net_portfolio"
        else next(t for t in original_report["trials"] if t["candidate"] == candidate_id)
    )
    shape.require(actual["simulation"]["result_hash"] == native_result_hash, "CURRENT_RESULT")
    series_designs = [
        r["plan"]["forward_design"]
        for r in project_cut["runs"].values()
        if r["plan"].get("forward_design", {}).get("risk_policy", {}).get("net_forward_policy", {}).get("series")
        == origin
    ]
    shape.require(
        any(d["metrics"]["start_ms"] == origin["start_ms"] for d in series_designs)
        and all(d["risk_policy"].get("frozen_validation_policy") == policy for d in series_designs),
        "SERIES_ORIGINAL_DECLARATIONS",
    )
    shape.require(
        policy["declared_at_ms"] == origin["declared_at_ms"]
        and policy["series_start_ms"] == origin["start_ms"]
        and policy["strategy_definition_sha256"] == origin["strategy_definition_sha256"]
        and current["source_identity"] == old_run["plan"]["source_identity"],
        "FORWARD_ORIGIN",
    )
    original_config = authority._original(old_run, "input.json")
    config = {
        k: original_config[k]
        for k in ("quantity", "initial_collateral", "fee_rate", "portfolio_plan", "settlement_window_ms")
        if k in original_config
    }
    shape.require(
        config == design["configuration"]
        and next(
            r["strategy_definition_sha256"]
            for r in forward._cohort(digest(old_run["plan"]["source_identity"]), config)
            if r["candidate_id"] == candidate_id
        )
        == policy["strategy_definition_sha256"],
        "STRATEGY_DEFINITION",
    )
    shape.require(
        ref["invocation_id"] in reports.source_inputs
        and len(reports.source_inputs[ref["invocation_id"]]) > 0
        and gold_native_source.MANIFEST in old_run["originals"],
        "ORIGINAL_INPUTS",
    )
    artifact.validate_row(row, reports=reports)
    stats = snapshot["statistical_evidence"]
    report = stats["report"]
    shape.require(
        digest(report) == ref["statistical_report_sha256"]
        and digest(stats["policy"]) == ref["statistical_policy_sha256"]
        and old["attempt_count"] == ref["attempt_count"]
        and old["unique_definition_count"] == ref["unique_definition_count"]
        and report["as_of_ms"] <= ref["reviewed_at_ms"] < policy["declared_at_ms"] <= snapshot_as_of_ms,
        "FROZEN_STATISTICS",
    )
    result = dict(
        schema=shape.REVIEW_SCHEMA,
        status="AVAILABLE",
        policy_sha256=shape.policy_sha256(policy),
        reference=dict(ref),
        **{k: subject[k] for k in ("invocation_id", "native_trial_id", "config_hash")},
        candidate_id=candidate_id,
        native_result_hash=native_result_hash,
        project_cut_sha256=project_cut["cut_sha256"],
        snapshot_as_of_ms=snapshot_as_of_ms,
        validation_as_of_ms=report["as_of_ms"],
        validation_confidence=report["metrics"]["dsr"],
        pbo=report["metrics"]["pbo"],
        explicit_overfit=any("OVERFIT_DETECTED" in r or "OVERFIT_CONFIRMED" in r for r in report["reasons"]),
        statistical_decision=report["statistical_decision"],
        statistical_reasons=list(report["reasons"]),
        frozen_attempt_count=old["attempt_count"],
        frozen_unique_definition_count=old["unique_definition_count"],
        current_attempt_count=project_cut["attempt_count"],
        current_unique_definition_count=project_cut["unique_definition_count"],
        selection_history_status="COMPLETE_RETAINED_PREFIX",
        independent_oos_verified=False,
        limitations=["FROZEN_BACKTEST_NOT_FORWARD_PERFORMANCE", "INDEPENDENT_OOS_AND_GLOBAL_LEAKAGE_STILL_REQUIRED"],
        economic_authority=False,
        submission_enabled=False,
        promotion_approved=False,
    )
    result["review_sha256"] = shape.digest(result)
    return shape.validate_review(
        result,
        policy=policy,
        subject=subject,
        candidate_id=candidate_id,
        native_result_hash=native_result_hash,
        project_cut_sha256=project_cut["cut_sha256"],
        snapshot_as_of_ms=snapshot_as_of_ms,
    )
