"""Gold review uses prospective rules and actual native project originals."""

import copy

import pytest

from tests.tools.test_registered_research_run import technical_arguments as technical_arguments


def test_new_declaration_fixes_complete_review_before_window():
    from tests.contracts.test_gold_forward_evaluation_design import declaration
    from quantos.contracts import gold_forward_evaluation_design as design

    value = declaration()
    assert value["schema"] == "QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V3"
    policy = value["statistical_policy"]
    assert policy["dsr_policy"]["moments"] == "EMPIRICAL_POPULATION_CENTRAL_MOMENTS_V1"
    assert policy["split_policy"]["embargo_ms"] == 940_000
    assert policy["economic_checks"]["minimum_duration_ms"] == 30 * 86_400_000
    for group, key, damage in [
        ("dsr_policy", "minimum_probability", "0.5"),
        ("split_policy", "embargo_ms", 10_000),
        ("economic_checks", "minimum_duration_ms", 10_000),
    ]:
        changed = copy.deepcopy(value)
        changed["statistical_policy"][group][key] = damage
        with pytest.raises(ValueError):
            design.validate(changed)


def test_original_gold_program_completes_review_without_inventing_baseline_trial(
    tmp_path, monkeypatch, technical_arguments
):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.validation import gold_project_statistics as review
    from services.validation import project_research_statistical_evidence as evidence
    from tests.tools.test_demo_gold_research_checkpoint import native_program, originals
    from tools import registered_gold_collection as bridge

    f = native_program(tmp_path, monkeypatch, source=technical_arguments["release_identity"],
        extended=True, register_original_trials=True)
    base = f["root"] / "data/cache/registered_research"
    cut = ProjectResearchEvidenceReader(base / "project", registry_database=base / "registry.db")()
    before = originals(f["root"])
    invocation = bridge.invocation_id(1)
    policy = cut["runs"][invocation]["plan"]["forward_design"]
    from quantos.contracts.registered_research_run import digest
    assert all(t["validation_policy_digest"] == digest(policy)
        for t in cut["runs"][invocation]["plan"]["trials"])
    for history in cut["runs"][invocation]["ledger"]["history"].values():
        assert all(e["validation_policy_digest"] == digest(policy) for e in history)
    from tools.demo_gold_research_checkpoint import _gold_archive
    checkpoint = _gold_archive(base / "project/runs" / invocation / "archive")
    assert checkpoint["terminal"] == "PASSED"
    cohort = review.project_cohort(project_cut=cut, invocation_id=invocation)
    actual = evidence.prepare(project_cut=cut, policy=policy, cohort=cohort,
        as_of_ms=cohort["frozen_at_ms"])
    report = actual["report"]
    assert report["population"]["attempt_count"] == report["metrics"]["n_trials"] == 4
    assert report["benchmark_attempt"]["kind"] == "ORIGINAL_EMBEDDED_CASH_ACCOUNT"
    assert report["metrics"]["matrix_sha256"] is not None
    assert policy["statistical_policy"]["split_policy"]["maximum_information_horizon_ms"] == 3_640_000
    assert report["metrics"]["pbo"] is not None
    assert "SAMPLE_TOO_SHORT" in report["reasons"]
    assert "FULL_RUN_TERMINAL_NOT_COVERED" in report["reasons"]
    assert report["statistical_decision"] in ("FAIL", "INSUFFICIENT")
    assert all(report[k] is False for k in (
        "independent_oos_verified", "economic_authority", "promotion_approved", "submission_enabled"))
    assert evidence.validate(actual, project_cut=cut) == actual
    assert originals(f["root"]) == before
    assert f["result"]["rounds"][0]["report"]["native_statistics"]["review"] == report
    forged = copy.deepcopy(actual)
    forged["report"]["metrics"]["pbo"] = 0.123456
    with pytest.raises(ValueError):
        evidence.validate(forged, project_cut=cut)


def private_review_source(tmp_path, monkeypatch):
    from tools import registered_research_review_job as entry
    # Export the real Python/lock bytes from a private fixture, keeping the
    # actual Git identity. Never change workspace permissions to satisfy the
    # reader's custody gate or depend on the checkout's ambient umask.
    original_root = entry.ROOT
    private_source = tmp_path / "review-source"
    private_source.mkdir(mode=0o700)
    paths = sorted(p for folder in ("services", "quantos", "tools")
        for p in (original_root / folder).rglob("*.py"))
    paths.append(original_root / "requirements-ci-linux-aarch64.lock")
    for path in paths:
        relative = path.relative_to(original_root)
        parent = private_source
        for part in relative.parts[:-1]:
            parent /= part
            parent.mkdir(mode=0o700, exist_ok=True)
        target = private_source / relative
        with target.open("xb") as stream:
            target.chmod(0o600)
            stream.write(path.read_bytes())
    monkeypatch.setattr(entry, "ROOT", private_source)


def test_actual_gold_review_request_exports_complete_native_material(tmp_path, monkeypatch):
    from tools.perpetual_replay_research import source_identity
    from tools import registered_gold_collection as bridge, registered_research_review_job as entry
    from services.validation.project_research_evidence_artifact import last_project_fact_ms
    from services.validation.project_research_population import validate_project_cut
    from tests.tools.test_demo_gold_research_checkpoint import native_program

    private_review_source(tmp_path, monkeypatch)

    f = native_program(tmp_path, monkeypatch, source=source_identity(),
        extended=True, register_original_trials=True)
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    project, database = entry.producer.workspace_paths()
    cut = ProjectResearchEvidenceReader(project, registry_database=database)()
    f["clock"][0] = last_project_fact_ms(cut) + 1000
    unsafe = entry.ROOT / "tools/registered_research_run.py"
    unsafe.chmod(0o660)
    with pytest.raises(ValueError, match="RESEARCH_EVIDENCE_FILE_CUSTODY"):
        entry.prepare_review_request({"native_invocation_id": bridge.invocation_id(1)})
    unsafe.chmod(0o600)
    result = entry.prepare_review_request({"native_invocation_id": bridge.invocation_id(1)})
    packet = entry.read_request(result["review_request_id"])
    assert validate_project_cut(packet["project_cut"])["cut_sha256"] == cut["cut_sha256"]
    assert packet["statistical_evidence"]["report"]["schema"] == "QUANTOS_GOLD_PROJECT_STATISTICAL_REPORT_V2"
    assert result["authentication"] == "UNSIGNED_REQUEST"
    assert result["statistical_decision"] in ("FAIL", "INSUFFICIENT")
    assert packet["projection"]["statistical_review"]["metrics"]["n_trials"] == 4
    assert result["economic_authority"] is result["promotion_approved"] is result["submission_enabled"] is False
    # Isolated fixture signatures verify the original forward scope; no trust
    # file or credential is installed, and a signature cannot change the result.
    from tests.validation.test_project_research_statistical_evidence import signed_statistics
    from services.validation import project_research_evidence_authority as authority
    context = dict(cut=packet["project_cut"], cohort=packet["statistical_evidence"]["cohort"], f=f)
    snapshot, _ = signed_statistics(context, packet["statistical_evidence"], source_manifest=packet["source_manifest"])
    checked = authority.verify_project_evidence(snapshot, now_ms=f["clock"][0])
    assert checked["authentication"] == "VERIFIED_SOURCE_AND_RESULT"
    assert checked["qualification"] == "REJECTED"
    assert checked["promotion_approved"] is checked["submission_enabled"] is False
    assert "STATISTICAL_REVIEW_NOT_IMPLEMENTED" not in checked["projection"]["limitations"]
