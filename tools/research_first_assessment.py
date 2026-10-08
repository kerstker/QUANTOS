"""Guarded, read-only research-first projection over the TOP22 evidence inventory.

Design boundaries (2026-09-20 DGX research-first decision):

- The existing top-tier assessment is inventory only.  A legacy ``PASS`` produced
  by ``paths_exist`` or ``text_contains_all`` cannot prove research behavior,
  cost-aware P&L or profitability, so it never promotes a research verdict.
- Without run evidence the six research dimensions remain UNKNOWN. Optional
  RF2 saved-receipt checks can show partial evidence, never independent OOS or
  trading readiness. Every TOP22 per-requirement research verdict stays UNKNOWN.
- Deferred institutional requirements do not block experimentation, but they are
  never auto-converted to PASS.
- Inputs are opened read-only; this module never writes evidence.

The CLI accepts explicit ``--registry`` / ``--assessment`` / ``--profile`` paths
with repository-relative defaults and writes one JSON document to stdout only.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import sys
from typing import Any

SCHEMA_VERSION = 1
ASSESSMENT_KIND = "research_first_dimensions"

OVERALL_VERDICT = "RESEARCH_EVIDENCE_INCOMPLETE"
RESEARCH_VERDICT = "UNKNOWN"
EVIDENCE_STATE = "INSUFFICIENT"

REASON_NO_RUN_EVIDENCE = "INDEPENDENT_RUN_EVIDENCE_CONTRACT_NOT_INTEGRATED"
REASON_STATIC_CHECKER = "STATIC_CHECKER_CANNOT_PROVE_RESEARCH_BEHAVIOR"

DISTRIBUTION_REQUIRED = "REQUIRED_RESEARCH_CORE"
DISTRIBUTION_ADAPTED = "ADAPTED_SCOPED_CHECK"
DISTRIBUTION_DEFERRED = "DEFERRED_INSTITUTIONAL_REQUIREMENT"
DISTRIBUTIONS = frozenset({DISTRIBUTION_REQUIRED, DISTRIBUTION_ADAPTED, DISTRIBUTION_DEFERRED})

# Static checkers can only prove that a path or a text marker exists.
STATIC_CHECKERS = frozenset({"paths_exist", "text_contains_all"})
NO_DEFERRAL_MARKER = "无"

# The six research dimensions are separate from the original top-tier verdict.
RESEARCH_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("data_trust", "Market scope, data digests, timezone, decision-time availability, gaps and revisions are explicit."),
    ("backtest_correctness", "V2 trade-by-trade ledger where fees, funding, slippage, capacity, margin and liquidation actually apply."),
    ("experiment_validity", "Pre-registered hypothesis, trial budget, train/validation/locked-OOS, benchmarks and retire rules."),
    ("profitability_evidence", "Frozen-OOS net of costs is necessary not sufficient: benchmark delta, drawdown, trade count, effective sample, cost stress, cross-period stability."),
    ("forward_and_testnet_evidence", "Frozen signal recorded before evaluation; per-order testnet receipts and cash reconciliation; independent testnet net PnL and cost calibration."),
    ("research_efficiency", "Cold/warm runtime, peak memory, cache hit rate, failure rate and recovery time against a registered baseline budget."),
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = _REPO_ROOT / "config" / "top_tier_requirements.json"
DEFAULT_ASSESSMENT = _REPO_ROOT / "evidence" / "top_tier_assessment.json"
DEFAULT_PROFILE = _REPO_ROOT / "config" / "research_first_scope.json"

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(_REPO_ROOT))


class ResearchFirstAssessmentError(Exception):
    """Fail-closed input or contract error carrying a machine-readable reason."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


class _DuplicateJSONKey(Exception):
    pass


class _NonFiniteJSONConstant(Exception):
    pass


def _reject_constant(token: str) -> Any:
    raise _NonFiniteJSONConstant(token)


def _object_pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey(key)
        result[key] = value
    return result


def load_json_document(path: str | Path) -> Any:
    """Read one JSON document, rejecting duplicate keys and non-finite literals."""

    p = Path(path)
    if not p.is_file():
        raise ResearchFirstAssessmentError("INPUT_NOT_FOUND", f"input file does not exist: {p}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ResearchFirstAssessmentError("INPUT_UNREADABLE", f"cannot read {p}: {exc}") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_pairs_no_duplicates,
            parse_constant=_reject_constant,
        )
    except _DuplicateJSONKey as exc:
        raise ResearchFirstAssessmentError("DUPLICATE_JSON_KEY", f"duplicate JSON key {exc!s} in {p}") from exc
    except _NonFiniteJSONConstant as exc:
        raise ResearchFirstAssessmentError("NONFINITE_NUMBER", f"non-finite numeric literal {exc!s} in {p}") from exc
    except ValueError as exc:
        raise ResearchFirstAssessmentError("MALFORMED_JSON", f"{p}: {exc}") from exc


def _require_mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResearchFirstAssessmentError("MALFORMED_INPUT", f"{where} must be a JSON object")
    return value


def _require_list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise ResearchFirstAssessmentError("MALFORMED_INPUT", f"{where} must be a JSON array")
    return value


def _require_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchFirstAssessmentError("MALFORMED_INPUT", f"{where} must be a non-empty string")
    return value


def _require_bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ResearchFirstAssessmentError(
            "BOOLEAN_FIELD_TYPE", f"{where} must be a JSON boolean, got {type(value).__name__}"
        )
    return value


def _require_finite_number(value: Any, where: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResearchFirstAssessmentError("NUMERIC_FIELD_TYPE", f"{where} must be a finite JSON number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ResearchFirstAssessmentError("NONFINITE_NUMBER", f"{where} must be finite")
    return value


def _reject_nonfinite_tree(value: Any, where: str) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ResearchFirstAssessmentError("NONFINITE_NUMBER", f"{where} contains a non-finite number")
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_nonfinite_tree(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_nonfinite_tree(item, f"{where}[{index}]")


def _index_registry(registry_doc: Any) -> dict[str, dict[str, Any]]:
    doc = _require_mapping(registry_doc, "registry")
    _require_finite_number(doc.get("schema_version"), "registry.schema_version")
    entries = _require_list(doc.get("requirements"), "registry.requirements")
    if not entries:
        raise ResearchFirstAssessmentError("MALFORMED_INPUT", "registry.requirements is empty")
    indexed: dict[str, dict[str, Any]] = {}
    for position, raw in enumerate(entries):
        entry = _require_mapping(raw, f"registry.requirements[{position}]")
        rid = _require_str(entry.get("id"), f"registry.requirements[{position}].id")
        if rid in indexed:
            raise ResearchFirstAssessmentError("DUPLICATE_ID", f"registry declares {rid!r} more than once")
        _require_str(entry.get("domain"), f"registry[{rid}].domain")
        _require_str(entry.get("tier"), f"registry[{rid}].tier")
        _require_str(entry.get("target_maturity"), f"registry[{rid}].target_maturity")
        _require_str(entry.get("checker"), f"registry[{rid}].checker")
        _require_bool(entry.get("red_line"), f"registry[{rid}].red_line")
        indexed[rid] = entry
    return indexed


def _index_assessment(assessment_doc: Any) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    doc = _require_mapping(assessment_doc, "assessment")
    entries = _require_list(doc.get("requirements"), "assessment.requirements")
    if not entries:
        raise ResearchFirstAssessmentError("MALFORMED_INPUT", "assessment.requirements is empty")
    indexed: dict[str, dict[str, Any]] = {}
    for position, raw in enumerate(entries):
        entry = _require_mapping(raw, f"assessment.requirements[{position}]")
        rid = _require_str(entry.get("id"), f"assessment.requirements[{position}].id")
        if rid in indexed:
            raise ResearchFirstAssessmentError("DUPLICATE_ID", f"assessment declares {rid!r} more than once")
        _require_str(entry.get("status"), f"assessment[{rid}].status")
        _require_str(entry.get("current_maturity"), f"assessment[{rid}].current_maturity")
        _require_str(entry.get("target_maturity"), f"assessment[{rid}].target_maturity")
        _require_str(entry.get("checker"), f"assessment[{rid}].checker")
        _require_bool(entry.get("meets_target"), f"assessment[{rid}].meets_target")
        _require_bool(entry.get("red_line"), f"assessment[{rid}].red_line")
        indexed[rid] = entry
    summary = doc.get("summary")
    if summary is not None:
        summary = _require_mapping(summary, "assessment.summary")
        for key, value in summary.items():
            _require_finite_number(value, f"assessment.summary.{key}")
    validation = doc.get("validation")
    if validation is not None:
        validation = _require_mapping(validation, "assessment.validation")
        if "current" in validation:
            _require_bool(validation["current"], "assessment.validation.current")
        if "generated_at" in validation:
            _require_finite_number(validation["generated_at"], "assessment.validation.generated_at")
    _reject_nonfinite_tree(doc, "assessment")
    return indexed, doc


def _index_profile(profile_doc: Any, registry_ids: set[str]) -> tuple[dict[str, dict[str, Any]], str]:
    doc = _require_mapping(profile_doc, "profile")
    _require_finite_number(doc.get("schema_version"), "profile.schema_version")
    profile_name = _require_str(doc.get("profile_name"), "profile.profile_name")
    entries = _require_list(doc.get("requirements"), "profile.requirements")
    indexed: dict[str, dict[str, Any]] = {}
    for position, raw in enumerate(entries):
        entry = _require_mapping(raw, f"profile.requirements[{position}]")
        rid = _require_str(entry.get("id"), f"profile.requirements[{position}].id")
        if rid in indexed:
            raise ResearchFirstAssessmentError("DUPLICATE_ID", f"profile declares {rid!r} more than once")
        distribution = _require_str(entry.get("distribution"), f"profile[{rid}].distribution")
        if distribution not in DISTRIBUTIONS:
            raise ResearchFirstAssessmentError(
                "UNKNOWN_DISTRIBUTION", f"profile[{rid}] has unknown distribution {distribution!r}"
            )
        _require_str(entry.get("research_stage_requirement"), f"profile[{rid}].research_stage_requirement")
        deferred_scope = _require_str(entry.get("deferred_scope"), f"profile[{rid}].deferred_scope")
        _require_str(entry.get("reason"), f"profile[{rid}].reason")
        deferred = _require_bool(entry.get("deferred"), f"profile[{rid}].deferred")
        if (distribution == DISTRIBUTION_REQUIRED) != (deferred is False):
            raise ResearchFirstAssessmentError(
                "PROFILE_DEFERRAL_INCONSISTENT", f"profile[{rid}] distribution and deferred flag disagree"
            )
        if (deferred is False) != (deferred_scope == NO_DEFERRAL_MARKER):
            raise ResearchFirstAssessmentError(
                "PROFILE_DEFERRAL_INCONSISTENT", f"profile[{rid}] deferred_scope and deferred flag disagree"
            )
        indexed[rid] = entry
    seen = set(indexed)
    if seen - registry_ids:
        raise ResearchFirstAssessmentError(
            "UNKNOWN_ID", f"profile has ids absent from registry: {sorted(seen - registry_ids)}"
        )
    if registry_ids - seen:
        raise ResearchFirstAssessmentError(
            "MISSING_ID", f"profile omits registry ids: {sorted(registry_ids - seen)}"
        )
    return indexed, profile_name


def _research_evidence_reason(checker: str) -> tuple[str, str]:
    if checker in STATIC_CHECKERS:
        return (
            REASON_STATIC_CHECKER,
            f"Legacy checker {checker!r} only proves file existence or text markers; it cannot prove research "
            "behavior, cost-aware P&L or profitability.",
        )
    return (
        REASON_NO_RUN_EVIDENCE,
        "A research verdict requires an independent run-evidence contract (registered hypotheses, V2 ledger "
        "reconciliation, locked OOS, cost-aware results); it is not integrated, so the legacy status is inventory only.",
    )


def assess(registry_doc: Any, assessment_doc: Any, profile_doc: Any) -> dict[str, Any]:
    """Build the deterministic research-first projection from parsed JSON documents."""

    registry = _index_registry(registry_doc)
    assessment, assessment_map = _index_assessment(assessment_doc)
    registry_ids = set(registry)
    profile, profile_name = _index_profile(profile_doc, registry_ids)

    missing = registry_ids - set(assessment)
    if missing:
        raise ResearchFirstAssessmentError("MISSING_ID", f"assessment omits registry ids: {sorted(missing)}")
    unknown = set(assessment) - registry_ids
    if unknown:
        raise ResearchFirstAssessmentError("UNKNOWN_ID", f"assessment has ids absent from registry: {sorted(unknown)}")

    legacy_verdict = None
    if "verdict" in assessment_map:
        legacy_verdict = _require_str(assessment_map["verdict"], "assessment.verdict")

    requirements: list[dict[str, Any]] = []
    distribution_counts = {
        DISTRIBUTION_REQUIRED: 0,
        DISTRIBUTION_ADAPTED: 0,
        DISTRIBUTION_DEFERRED: 0,
    }
    deferred_count = 0
    legacy_pass_count = 0
    for rid in sorted(registry_ids):
        reg = registry[rid]
        asr = assessment[rid]
        prof = profile[rid]
        distribution = prof["distribution"]
        distribution_counts[distribution] += 1
        deferred = prof["deferred"]
        if deferred:
            deferred_count += 1
        reason_code, reason_text = _research_evidence_reason(asr["checker"])
        if asr["status"] == "PASS":
            legacy_pass_count += 1
        requirements.append(
            {
                "id": rid,
                "domain": reg["domain"],
                "tier": reg["tier"],
                "distribution": distribution,
                "distribution_reason": prof["reason"],
                "research_stage_requirement": prof["research_stage_requirement"],
                "deferred_scope": prof["deferred_scope"],
                "deferred": deferred,
                "deferred_does_not_block_experimentation": deferred,
                "deferred_is_not_pass": deferred,
                "research_verdict": RESEARCH_VERDICT,
                "evidence_state": EVIDENCE_STATE,
                "evidence_reason_code": reason_code,
                "evidence_reason": reason_text,
                "legacy_status_inventory_only": {
                    "inventory_only": True,
                    "checker": asr["checker"],
                    "status": asr["status"],
                    "current_maturity": asr["current_maturity"],
                    "target_maturity": reg["target_maturity"],
                    "meets_target": asr["meets_target"],
                    "red_line": reg["red_line"],
                },
            }
        )

    dimensions = [
        {
            "id": dimension_id,
            "research_dimension": True,
            "verdict": RESEARCH_VERDICT,
            "evidence_state": EVIDENCE_STATE,
            "reason_code": REASON_NO_RUN_EVIDENCE,
            "description": description,
        }
        for dimension_id, description in RESEARCH_DIMENSIONS
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "assessment_kind": ASSESSMENT_KIND,
        "profile_name": profile_name,
        "overall": {
            "verdict": OVERALL_VERDICT,
            "evidence_state": EVIDENCE_STATE,
            "reason_code": REASON_NO_RUN_EVIDENCE,
            "aggregate_readiness_score_emitted": False,
            "legacy_top_tier_verdict_inventory_only": legacy_verdict,
            "legacy_top_tier_is_inventory_only": True,
        },
        "policy": {
            "source_top_tier_statuses_are_inventory_only": True,
            "legacy_pass_does_not_imply_research_pass": True,
            "static_checks_cannot_prove_research_behavior_or_profitability": True,
            "deferred_items_do_not_block_experimentation": True,
            "deferred_items_are_not_pass": True,
            "no_aggregate_readiness_or_percent_emitted": True,
        },
        "counts": {
            "requirements": len(requirements),
            "deferred": deferred_count,
            "required_research_core": distribution_counts[DISTRIBUTION_REQUIRED],
            "adapted_scoped_check": distribution_counts[DISTRIBUTION_ADAPTED],
            "deferred_institutional_requirement": distribution_counts[DISTRIBUTION_DEFERRED],
            "research_unknown": len(requirements),
            "research_pass": 0,
            "legacy_pass_inventory": legacy_pass_count,
        },
        "dimensions": dimensions,
        "requirements": requirements,
    }


def assess_paths(registry_path: str | Path, assessment_path: str | Path, profile_path: str | Path) -> dict[str, Any]:
    """Load the three read-only inputs and return the research-first projection."""

    return assess(
        load_json_document(registry_path),
        load_json_document(assessment_path),
        load_json_document(profile_path),
    )


def with_rf2_evidence(assessment: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """Attach reader-verified saved receipts without promoting old TOP22 scores.

    The caller must obtain ``run`` from inspect_rf2, not from a user-written
    report. These local consistency checks are not original-data PIT, untouched
    OOS, a signed preregistration clock or trading authorization.
    """
    if (not isinstance(run, dict)
            or run.get('status') != 'VERIFIED_SAVED_RECEIPTS_TECHNICAL_ONLY'
            or run.get('history_class') != 'SEEN_HISTORY_NOT_UNTOUCHED_OOS'
            or run.get('authority') is not False or run.get('economic_eligible') is not False):
        raise ResearchFirstAssessmentError('INVALID_RUN_EVIDENCE', 'expected reader-verified RF2 technical history')
    result = copy.deepcopy(assessment)
    result['run_evidence'] = copy.deepcopy(run)
    reasons = {
        'data_trust': ('PARTIAL', 'RECORDED_DATA_IDENTITY_NOT_RAW_DATA_OR_PIT_VERIFICATION'),
        'backtest_correctness': ('PARTIAL', 'SAVED_COMPLETED_CASE_RECEIPTS_RECONCILED_NOT_RAW_INPUT_REPLAY'),
        'experiment_validity': ('PARTIAL', 'LOCAL_TRIAL_HISTORY_CHECKED_NOT_SIGNED_PREREGISTRATION_OR_OOS'),
        'profitability_evidence': ('INSUFFICIENT', 'SEEN_HISTORY_NET_RESULTS_NOT_INDEPENDENT_PROFIT_EVIDENCE'),
        'forward_and_testnet_evidence': ('UNKNOWN', 'NO_FORWARD_OR_TESTNET_EVIDENCE_IN_THIS_INPUT'),
        'research_efficiency': ('PARTIAL', 'REPORTED_CASE_TIMINGS_NOT_LOAD_TIME_MEMORY_OR_PERFORMANCE_BUDGET'),
    }
    for row in result['dimensions']:
        verdict, reason = reasons[row['id']]
        row.update(verdict=verdict, evidence_state='PARTIAL' if verdict == 'PARTIAL' else 'INSUFFICIENT',
                   reason_code=reason, evidence_reference='/run_evidence')
    result['overall']['reason_code'] = 'SAVED_RUN_EVIDENCE_PRESENT_INDEPENDENT_OOS_AND_FORWARD_NOT_PROVIDED'
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="research-first-assessment",
        description="Read-only research-first projection over the TOP22 evidence inventory.",
    )
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--assessment", default=str(DEFAULT_ASSESSMENT))
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--rf2-run", help="optional absolute RF2 output directory; verify saved receipts read-only")
    args = parser.parse_args(argv)
    try:
        result = assess_paths(args.registry, args.assessment, args.profile)
        if args.rf2_run:
            from tools.research_first_run_evidence import inspect_rf2
            try:
                result = with_rf2_evidence(result, inspect_rf2(args.rf2_run))
            except Exception as exc:
                raise ResearchFirstAssessmentError('INVALID_RUN_EVIDENCE', str(exc)) from exc
    except ResearchFirstAssessmentError as exc:
        error = {"status": "ERROR", "reason_code": exc.reason_code, "detail": exc.detail}
        sys.stderr.write(json.dumps(error, sort_keys=True, ensure_ascii=False) + "\n")
        return 2
    sys.stdout.write(json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
