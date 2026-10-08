"""RF1: read-only research-first projection over the TOP22 evidence inventory.

These tests pin the fail-closed contract: the legacy top-tier assessment is
inventory only, a legacy PASS never promotes a research verdict, deferred items
are never auto-passed, and missing/duplicated/unknown ids or malformed numeric
fields are rejected.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools.research_first_assessment import (
    DISTRIBUTION_DEFERRED,
    DISTRIBUTION_REQUIRED,
    OVERALL_VERDICT,
    REASON_NO_RUN_EVIDENCE,
    REASON_STATIC_CHECKER,
    RESEARCH_DIMENSIONS,
    ResearchFirstAssessmentError,
    assess,
    assess_paths,
    load_json_document,
)

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "config" / "top_tier_requirements.json"
ASSESSMENT_PATH = ROOT / "evidence" / "top_tier_assessment.json"
PROFILE_PATH = ROOT / "config" / "research_first_scope.json"
TOOL_PATH = ROOT / "tools" / "research_first_assessment.py"

STATIC_CHECKERS = {"paths_exist", "text_contains_all"}
FORBIDDEN_KEYS = {"percent", "percentage", "readiness", "ready", "score", "completion", "aggregate_score"}


def _load(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path: Path, doc) -> Path:
    Path(path).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return Path(path)


def _run_cli(*extra: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(TOOL_PATH), *extra],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def _requirement(result: dict, rid: str) -> dict:
    for item in result["requirements"]:
        if item["id"] == rid:
            return item
    raise AssertionError(f"missing requirement {rid}")


@pytest.fixture()
def docs():
    return _load(REGISTRY_PATH), _load(ASSESSMENT_PATH), _load(PROFILE_PATH)


def test_repository_inputs_cover_all_22_ids_exactly_once(docs):
    registry, assessment, profile = docs
    registry_ids = [r["id"] for r in registry["requirements"]]
    profile_ids = [r["id"] for r in profile["requirements"]]
    assessment_ids = [r["id"] for r in assessment["requirements"]]
    assert len(registry_ids) == 22
    assert len(set(registry_ids)) == 22
    assert len(profile_ids) == len(set(profile_ids)) == 22
    assert set(profile_ids) == set(registry_ids)
    assert set(assessment_ids) == set(registry_ids)


def test_projection_reports_research_evidence_incomplete(docs):
    result = assess(*docs)
    assert result["overall"]["verdict"] == OVERALL_VERDICT
    assert result["overall"]["evidence_state"] == "INSUFFICIENT"
    assert result["overall"]["aggregate_readiness_score_emitted"] is False
    assert result["counts"]["requirements"] == 22
    assert result["counts"]["research_pass"] == 0
    assert result["counts"]["research_unknown"] == 22
    assert all(item["research_verdict"] == "UNKNOWN" for item in result["requirements"])


def test_saved_run_evidence_is_partial_not_research_or_trading_approval(docs):
    from tools.research_first_assessment import with_rf2_evidence
    original = assess(*docs)
    before = copy.deepcopy(original)
    run = {'status': 'VERIFIED_SAVED_RECEIPTS_TECHNICAL_ONLY',
           'history_class': 'SEEN_HISTORY_NOT_UNTOUCHED_OOS',
           'authority': False, 'economic_eligible': False}
    result = with_rf2_evidence(original, run)
    assert original == before
    assert result['overall']['verdict'] == OVERALL_VERDICT
    assert result['counts'] == original['counts']
    assert result['requirements'] == original['requirements']
    dimensions = {row['id']: row for row in result['dimensions']}
    assert dimensions['backtest_correctness']['verdict'] == 'PARTIAL'
    assert dimensions['profitability_evidence']['verdict'] == 'INSUFFICIENT'
    assert dimensions['forward_and_testnet_evidence']['verdict'] == 'UNKNOWN'
    assert all(row['verdict'] != 'PASS' for row in dimensions.values())
    assert result['run_evidence'] == run


@pytest.mark.parametrize('key,value', [('authority', 0), ('economic_eligible', True),
                                     ('history_class', 'UNTOUCHED_OOS')])
def test_unverified_or_upgraded_evidence_cannot_be_attached(docs, key, value):
    from tools.research_first_assessment import with_rf2_evidence
    run = {'status': 'VERIFIED_SAVED_RECEIPTS_TECHNICAL_ONLY',
           'history_class': 'SEEN_HISTORY_NOT_UNTOUCHED_OOS',
           'authority': False, 'economic_eligible': False, key: value}
    with pytest.raises(ResearchFirstAssessmentError):
        with_rf2_evidence(assess(*docs), run)


def test_six_research_dimensions_are_separate_unknown_verdicts(docs):
    result = assess(*docs)
    assert [d["id"] for d in result["dimensions"]] == [d for d, _ in RESEARCH_DIMENSIONS]
    assert len(result["dimensions"]) == 6
    for dimension in result["dimensions"]:
        assert dimension["research_dimension"] is True
        assert dimension["verdict"] == "UNKNOWN"
        assert dimension["reason_code"] == REASON_NO_RUN_EVIDENCE
    # The original top-tier verdict is carried as inventory only, never merged.
    _, assessment, _ = docs
    assert result["overall"]["legacy_top_tier_verdict_inventory_only"] == assessment["verdict"]
    assert result["overall"]["legacy_top_tier_is_inventory_only"] is True


def test_legacy_static_pass_cannot_promote_to_research_pass(docs):
    result = assess(*docs)
    item = _requirement(result, "DATA.PIT.CONTRACT")
    assert item["legacy_status_inventory_only"]["status"] == "PASS"
    assert item["legacy_status_inventory_only"]["checker"] == "paths_exist"
    assert item["legacy_status_inventory_only"]["inventory_only"] is True
    assert item["research_verdict"] == "UNKNOWN"
    assert item["evidence_reason_code"] == REASON_STATIC_CHECKER
    assert result["policy"]["legacy_pass_does_not_imply_research_pass"] is True
    assert result["counts"]["legacy_pass_inventory"] > 0
    assert result["counts"]["research_pass"] == 0


def test_checker_determines_machine_readable_reason(docs):
    result = assess(*docs)
    for item in result["requirements"]:
        checker = item["legacy_status_inventory_only"]["checker"]
        expected = REASON_STATIC_CHECKER if checker in STATIC_CHECKERS else REASON_NO_RUN_EVIDENCE
        assert item["evidence_reason_code"] == expected
        assert item["evidence_state"] == "INSUFFICIENT"


def test_deferred_item_is_never_auto_pass(docs):
    result = assess(*docs)
    deferred = [item for item in result["requirements"] if item["deferred"]]
    assert deferred
    assert result["counts"]["deferred"] == len(deferred)
    for item in deferred:
        assert item["distribution"] == DISTRIBUTION_DEFERRED or item["deferred"] is True
        assert item["deferred_is_not_pass"] is True
        assert item["deferred_does_not_block_experimentation"] is True
        assert item["research_verdict"] == "UNKNOWN"
        assert item["research_verdict"] != "PASS"
    assert result["policy"]["deferred_items_are_not_pass"] is True


def test_required_core_distributions_are_not_deferred(docs):
    result = assess(*docs)
    required = [item for item in result["requirements"] if item["distribution"] == DISTRIBUTION_REQUIRED]
    assert len(required) == 7
    for item in required:
        assert item["deferred"] is False
        assert item["deferred_scope"] == "无"


def test_duplicate_registry_id_fails_closed(docs, tmp_path):
    registry, assessment, profile = copy.deepcopy(docs)
    registry["requirements"].append(copy.deepcopy(registry["requirements"][0]))
    with pytest.raises(ResearchFirstAssessmentError) as excinfo:
        assess(registry, assessment, profile)
    assert excinfo.value.reason_code == "DUPLICATE_ID"


def test_duplicate_profile_id_fails_closed(docs, tmp_path):
    registry, assessment, profile = copy.deepcopy(docs)
    profile["requirements"].append(copy.deepcopy(profile["requirements"][0]))
    with pytest.raises(ResearchFirstAssessmentError) as excinfo:
        assess(registry, assessment, profile)
    assert excinfo.value.reason_code == "DUPLICATE_ID"


def test_unknown_profile_id_fails_closed(docs):
    registry, assessment, profile = copy.deepcopy(docs)
    profile["requirements"][0]["id"] = "UNKNOWN.ID"
    with pytest.raises(ResearchFirstAssessmentError) as excinfo:
        assess(registry, assessment, profile)
    assert excinfo.value.reason_code == "UNKNOWN_ID"


def test_missing_profile_id_fails_closed(docs):
    registry, assessment, profile = copy.deepcopy(docs)
    profile["requirements"] = profile["requirements"][1:]
    with pytest.raises(ResearchFirstAssessmentError) as excinfo:
        assess(registry, assessment, profile)
    assert excinfo.value.reason_code == "MISSING_ID"


def test_registry_assessment_id_mismatch_fails_closed(docs):
    registry, assessment, profile = copy.deepcopy(docs)
    assessment["requirements"] = assessment["requirements"][1:]
    with pytest.raises(ResearchFirstAssessmentError) as excinfo:
        assess(registry, assessment, profile)
    assert excinfo.value.reason_code == "MISSING_ID"

    registry, assessment, profile = copy.deepcopy(docs)
    assessment["requirements"].append(
        {"id": "EXTRA.ID", "status": "PASS", "current_maturity": "M4", "target_maturity": "M4",
         "checker": "paths_exist", "meets_target": True, "red_line": False}
    )
    with pytest.raises(ResearchFirstAssessmentError) as excinfo:
        assess(registry, assessment, profile)
    assert excinfo.value.reason_code == "UNKNOWN_ID"


def test_malformed_json_fails_closed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ResearchFirstAssessmentError) as excinfo:
        load_json_document(bad)
    assert excinfo.value.reason_code == "MALFORMED_JSON"


def test_duplicate_json_key_fails_closed(tmp_path):
    bad = tmp_path / "dup.json"
    bad.write_text('{"schema_version": 1, "schema_version": 2}', encoding="utf-8")
    with pytest.raises(ResearchFirstAssessmentError) as excinfo:
        load_json_document(bad)
    assert excinfo.value.reason_code == "DUPLICATE_JSON_KEY"


def test_missing_file_fails_closed(tmp_path):
    with pytest.raises(ResearchFirstAssessmentError) as excinfo:
        assess_paths(tmp_path / "nope.json", ASSESSMENT_PATH, PROFILE_PATH)
    assert excinfo.value.reason_code == "INPUT_NOT_FOUND"


def test_boolean_field_wrong_type_fails_closed(docs):
    registry, assessment, profile = copy.deepcopy(docs)
    for entry in assessment["requirements"]:
        entry["meets_target"] = 1
    with pytest.raises(ResearchFirstAssessmentError) as excinfo:
        assess(registry, assessment, profile)
    assert excinfo.value.reason_code == "BOOLEAN_FIELD_TYPE"


def test_numeric_field_with_boolean_fails_closed(docs):
    registry, assessment, profile = copy.deepcopy(docs)
    assessment["summary"]["passed"] = True
    with pytest.raises(ResearchFirstAssessmentError) as excinfo:
        assess(registry, assessment, profile)
    assert excinfo.value.reason_code == "NUMERIC_FIELD_TYPE"


def test_nonfinite_numeric_fails_closed(tmp_path):
    bad = tmp_path / "nan.json"
    bad.write_text('{"schema_version": 1, "summary": {"passed": NaN}}', encoding="utf-8")
    with pytest.raises(ResearchFirstAssessmentError) as excinfo:
        load_json_document(bad)
    assert excinfo.value.reason_code == "NONFINITE_NUMBER"


def test_profile_deferral_inconsistency_fails_closed(docs):
    registry, assessment, profile = copy.deepcopy(docs)
    for entry in profile["requirements"]:
        if entry["distribution"] == DISTRIBUTION_REQUIRED:
            entry["deferred"] = True
            break
    with pytest.raises(ResearchFirstAssessmentError) as excinfo:
        assess(registry, assessment, profile)
    assert excinfo.value.reason_code == "PROFILE_DEFERRAL_INCONSISTENT"


def test_unknown_distribution_fails_closed(docs):
    registry, assessment, profile = copy.deepcopy(docs)
    profile["requirements"][0]["distribution"] = "BOGUS"
    with pytest.raises(ResearchFirstAssessmentError) as excinfo:
        assess(registry, assessment, profile)
    assert excinfo.value.reason_code == "UNKNOWN_DISTRIBUTION"


def test_cli_stdout_only_and_deterministic():
    first = _run_cli()
    second = _run_cli()
    assert first.returncode == 0
    assert first.stderr == ""
    assert second.returncode == 0
    assert first.stdout == second.stdout
    payload = json.loads(first.stdout)
    assert payload["overall"]["verdict"] == OVERALL_VERDICT
    assert len(payload["requirements"]) == 22


def test_cli_fails_closed_with_empty_stdout(tmp_path):
    bad_profile = _write(tmp_path / "bad_profile.json", {"schema_version": 1, "profile_name": "x"})
    completed = _run_cli("--profile", str(bad_profile))
    assert completed.returncode == 2
    assert completed.stdout == ""
    error = json.loads(completed.stderr)
    assert error["status"] == "ERROR"
    assert error["reason_code"] == "MALFORMED_INPUT"


def test_assessment_never_mutates_inputs():
    before = {p: _sha256(p) for p in (REGISTRY_PATH, ASSESSMENT_PATH, PROFILE_PATH)}
    assess_paths(REGISTRY_PATH, ASSESSMENT_PATH, PROFILE_PATH)
    assert _run_cli().returncode == 0
    after = {p: _sha256(p) for p in (REGISTRY_PATH, ASSESSMENT_PATH, PROFILE_PATH)}
    assert before == after


def test_no_fabricated_readiness_or_percent_keys(docs):
    result = assess(*docs)

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key, item
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)

    for key, value in walk(result):
        assert key.lower() not in FORBIDDEN_KEYS
        if key == "verdict":
            assert value in {"UNKNOWN", OVERALL_VERDICT}
