"""Prospective comparison membership never removes global search history."""

import copy

import pytest

from tests.tools.test_registered_research_run import technical_arguments as technical_arguments


def test_comparison_scope_is_fixed_before_any_observation():
    from tests.contracts.test_gold_forward_evaluation_design import declaration
    from quantos.contracts import gold_forward_evaluation_design as design

    value = declaration()
    assert value["schema"] == "QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V3"
    policy = value["statistical_policy"]["comparison_policy"]
    assert policy["candidate_matrix"] == "ALL_ATTEMPTS_BOUND_TO_THIS_DECLARATION"
    assert policy["other_declarations"] == "RETAIN_INCLUDE_IN_GLOBAL_TRIAL_COUNT"
    assert policy["global_dsr_variance"] == "FULL_PROJECT_COMPARABLE_OR_INSUFFICIENT"
    weakened = copy.deepcopy(value)
    weakened["statistical_policy"]["comparison_policy"]["other_declarations"] = "DROP"
    with pytest.raises(ValueError):
        design.validate(weakened)


def two_windows(tmp_path, monkeypatch, source):
    from tests.tools.test_demo_gold_research_checkpoint import native_program
    from tests.tools import test_gold_extended_window as inputs
    from tests.execution import test_perpetual_public_replay as public
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    with monkeypatch.context() as patch:
        first = native_program(tmp_path / "first", patch, source=source,
            extended=True, register_original_trials=True, failure="research")
    with monkeypatch.context() as patch:
        # A genuinely later synthetic window: never backdate a second
        # declaration into the already completed first episode.
        patch.setattr(inputs, "START", public.START + 5 * inputs.STEP)
        patch.setattr(public, "START", public.START + 5 * inputs.STEP)
        second = native_program(tmp_path / "second", patch, source=source, state=first["root"],
            extended=True, register_original_trials=True, program_id=2, first_attempt=2)
    base = second["root"] / "data/cache/registered_research"
    cut = ProjectResearchEvidenceReader(base / "project", registry_database=base / "registry.db")()
    return first, second, cut


def test_new_window_compares_every_declared_candidate_and_retains_old_failures(
    tmp_path, monkeypatch, technical_arguments
):
    from tests.tools.test_demo_gold_research_checkpoint import originals
    from services.validation import gold_project_statistics as review
    from services.validation import project_research_statistical_evidence as evidence
    from tools import registered_gold_collection as bridge

    first, second, cut = two_windows(tmp_path, monkeypatch, technical_arguments["release_identity"])
    invocation = bridge.invocation_id(2)
    policy = cut["runs"][invocation]["plan"]["forward_design"]
    first_policy = cut["runs"][bridge.invocation_id(1)]["plan"]["forward_design"]
    assert policy["declared_at_ms"] > first["clock"][0]
    assert policy["window"]["observation_start_ms"] > first_policy["window"]["closed_bars"][-1]
    before = originals(second["root"])
    cohort = review.project_cohort(project_cut=cut, invocation_id=invocation)
    value = evidence.prepare(project_cut=cut, policy=policy, cohort=cohort, as_of_ms=cohort["frozen_at_ms"])
    report = value["report"]
    assert report["metrics"]["matrix_sha256"] is not None
    assert report["schema"] == "QUANTOS_GOLD_PROJECT_STATISTICAL_REPORT_V2"
    assert report["population"]["attempt_count"] == report["metrics"]["n_trials"] == 8
    assert report["population"]["failed_attempt_count"] == 4
    comparison = report["comparison"]
    assert len(comparison["included_attempts"]) == len(comparison["excluded_attempts"]) == 4
    assert {row["invocation_id"] for row in comparison["included_attempts"]} == {invocation}
    assert report["metrics"]["matrix_sha256"] is not None
    assert report["metrics"]["pbo"] is not None
    assert report["checks"]["dsr"] == "INSUFFICIENT"
    assert report["metrics"]["dsr"] is report["metrics"]["var_sr"] is None
    assert "GLOBAL_DSR_VARIANCE_NOT_COMPARABLE" in report["reasons"]
    assert report["statistical_decision"] in ("FAIL", "INSUFFICIENT")
    assert evidence.validate(value, project_cut=cut) == value
    assert originals(second["root"]) == before
    for field in ("economic_authority", "independent_oos_verified", "promotion_approved", "submission_enabled"):
        assert report[field] is False
    forged = copy.deepcopy(value)
    forged["report"]["comparison"]["excluded_attempts"] = []
    with pytest.raises(ValueError):
        evidence.validate(forged, project_cut=cut)


def test_two_window_scope_survives_original_export_signed_review_and_tamper_rejection(tmp_path, monkeypatch):
    from tools.perpetual_replay_research import source_identity
    from tools import registered_gold_collection as bridge, registered_research_review_job as entry
    from tests.validation.test_gold_project_statistical_review import private_review_source
    from tests.validation.test_project_research_statistical_evidence import signed_statistics
    from services.validation.project_research_evidence_artifact import last_project_fact_ms
    from services.validation import project_research_evidence_authority as authority
    from services.validation import project_research_statistical_evidence as evidence

    first, second, cut = two_windows(tmp_path, monkeypatch, source_identity())
    assert first["root"] == second["root"]
    private_review_source(tmp_path, monkeypatch)
    second["clock"][0] = last_project_fact_ms(cut) + 1000
    monkeypatch.setattr(entry.time, "time_ns", lambda: second["clock"][0] * 1_000_000)
    monkeypatch.setenv("QUANTOS_STATE_ROOT", str(second["root"]))
    result = entry.prepare_review_request({"native_invocation_id": bridge.invocation_id(2)})
    packet = entry.read_request(result["review_request_id"])
    value = packet["statistical_evidence"]
    assert packet["project_cut"]["attempt_count"] == result["attempt_count"] == 8
    assert len(value["report"]["comparison"]["included_attempts"]) == 4
    assert len(value["report"]["comparison"]["excluded_attempts"]) == 4
    assert packet["projection"]["statistical_review"] == evidence.projection(value)
    assert packet["projection"]["statistical_review"]["comparison"] == value["report"]["comparison"]
    assert result["authentication"] == "UNSIGNED_REQUEST"
    context = dict(cut=packet["project_cut"], cohort=value["cohort"], f=second)
    snapshot, _ = signed_statistics(context, value, source_manifest=packet["source_manifest"])
    verified = authority.verify_project_evidence(snapshot, now_ms=second["clock"][0])
    assert verified["authentication"] == "VERIFIED_SOURCE_AND_RESULT"
    assert verified["qualification"] == "REJECTED"
    assert verified["projection"]["statistical_review"]["metrics"]["n_trials"] == 8
    assert verified["projection"]["statistical_review"]["metrics"]["dsr"] is None
    assert verified["promotion_approved"] is verified["submission_enabled"] is False
    forged = copy.deepcopy(value)
    comparison = forged["cohort"]["comparison"]
    comparison["included_attempts"][-1], comparison["excluded_attempts"][-1] = (
        comparison["excluded_attempts"][-1], comparison["included_attempts"][-1])
    with pytest.raises(ValueError):
        evidence.validate(forged, project_cut=packet["project_cut"])


def test_replaying_one_declaration_retains_failed_attempts_and_rejects_its_budget(tmp_path, monkeypatch, technical_arguments):
    from tests.tools.test_demo_gold_research_checkpoint import native_program
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.validation import gold_native_statistics as statistics
    from tools import registered_gold_collection as bridge, gold_bar_research
    from tools import registered_research_run as native, registered_research_job as job, gold_bar_experiment

    f = native_program(tmp_path, monkeypatch, source=technical_arguments["release_identity"],
        extended=True, register_original_trials=True)
    base = f["root"] / "data/cache/registered_research"
    reader = ProjectResearchEvidenceReader(base / "project", registry_database=base / "registry.db")
    cut = reader()
    declaration = cut["runs"][bridge.invocation_id(1)]["plan"]["forward_design"]
    arguments = gold_bar_research._arguments({"attempt": 1})[0]
    plan = native.prepare_gold(arguments, project_root=base / "project", search_id="duplicate-declaration",
        producer_id="REGISTERED_GOLD_COLLECTOR_V1", actor_id="AUTOMATED_RESEARCH",
        dataset_version=native.contract.digest(native._gold_input(arguments)),
        registered_at_ms=f["clock"][0], invocation_id="f" * 64, forward_design=declaration)

    def failed(_arguments):
        raise ValueError("SYNTHETIC_DUPLICATE_FAILURE")

    with monkeypatch.context() as patch:
        patch.setattr(gold_bar_experiment, "_execute", failed)
        with job._registry(base / "registry.db", initialize=False) as registry:
            with pytest.raises(ValueError, match="SYNTHETIC_DUPLICATE_FAILURE"):
                native.run_gold(arguments, destination=base / "project/runs" / plan["invocation_id"], registry=registry)
    cut = reader()
    view = statistics.project_gold_statistics(project_cut=cut, invocation_id=bridge.invocation_id(1))
    report = view["review"]
    assert report["population"]["attempt_count"] == report["metrics"]["n_trials"] == 8
    assert len(report["comparison"]["included_attempts"]) == 8
    assert report["comparison"]["excluded_attempts"] == []
    assert report["population"]["failed_attempt_count"] == 4
    assert "DECLARATION_ATTEMPT_BUDGET_MISMATCH" in report["reasons"]
    assert report["metrics"]["matrix_sha256"] is None
    assert all(report["checks"][name] == "INSUFFICIENT" for name in ("dsr", "pbo", "cpcv_positive", "cpcv_tail"))
    assert report["statistical_decision"] != "PASS"
