"""Synthetic originals exercise the real backtest-to-forward software seam."""

import json
import time
from contextlib import closing

import pytest

from tests.tools.test_gold_source_reanalysis import older_source as older_source
from tests.tools.test_registered_research_run import registered_case as registered_case
from tests.tools.test_registered_gold_source import short_source as short_source
from quantos.contracts.research_evidence_json import canonical, digest


@pytest.fixture
def handoff(older_source, monkeypatch, tmp_path):
    from tests.validation.test_gold_reanalysis_statistics import export_backtest
    from tests.research.test_registered_research_review_job import independent_response
    from tools import registered_research_review_job as review, registered_gold_chunked_job as job
    from services.research.experiment_registry.store import SqliteExperimentStore
    from services.research.project_result_material_reader import ProjectResultMaterialReader
    from quantos.contracts.gold_frozen_validation_policy import declare as frozen_policy
    from quantos.contracts.gold_forward_evaluation_design import declare, _cohort
    from quantos.contracts.gold_net_forward_policy import declare as forward_policy
    from quantos.contracts.gold_net_maintenance_policy import declare as maintenance_policy
    from tests.validation.test_gold_research_risk_policy import policy as risk_policy
    from tests.validation.test_gold_market_states import policy as market_policy
    from tests.validation.test_gold_capacity_inputs import policy as capacity_policy
    from quantos.contracts.gold_causality_policy import declare as causality_policy

    f, back, stat_policy, _, request, packet = export_backtest(older_source, monkeypatch)
    trust = f["root"] / "synthetic-trust/trust.json"
    independent_response(packet, review.response_directory(request), trust)
    registry = json.loads(trust.read_bytes())
    registry["producer_ids"] = ["REGISTERED_GOLD_REANALYSIS_RUNNER_V1", "REGISTERED_GOLD_COLLECTOR_V1"]
    registry["keys"]["fixture"]["evidence_kinds"] = ["BACKTEST", "FORWARD"]
    trust.write_bytes(canonical(registry))
    trust.chmod(0o444)
    monkeypatch.setenv("QUANTOS_PROJECT_REVIEW_TRUST_FILE", str(trust))
    monkeypatch.setenv("QUANTOS_PROJECT_REVIEW_TRUST_SHA256", digest(trust.read_bytes()))
    monkeypatch.setenv("QUANTOS_PROJECT_REVIEW_MIN_GENERATION", "1")
    original_review = review.consume_review(dict(review_request_id=request))
    material_reader = ProjectResultMaterialReader(f["root"] / "project")
    with closing(
        SqliteExperimentStore(
            str(f["root"] / "registry.db"), create=False, read_only=True, project_result_reader=material_reader
        )
    ) as store:
        row = store.get_project_research_evidence(original_review["review_id"])
    retained = json.loads(row["evaluation_json"])
    old = retained["snapshot"]
    ref = dict(
        review_id=row["run_id"],
        content_hash=row["content_hash"],
        project_cut_sha256=old["project_cut"]["cut_sha256"],
        **{k: old["subject"][k] for k in ("invocation_id", "native_trial_id", "config_hash")},
        statistical_policy_sha256=digest(old["statistical_evidence"]["policy"]),
        statistical_report_sha256=digest(old["statistical_evidence"]["report"]),
        reviewed_at_ms=retained["assessment"]["checked_at_ms"],
        registered_at_ms=row["recorded_ts"],
        attempt_count=old["project_cut"]["attempt_count"],
        unique_definition_count=old["project_cut"]["unique_definition_count"],
    )
    # New synthetic future tape, physically retained with the original producer.
    from tests.research.test_gold_segmented_registration import request as collection_request
    from tools.gold_bar_research import read_arguments
    from tools import gold_chunked_source
    from quantos.contracts.gold_segmented_input import documents

    origin = ((row["recorded_ts"] // 900_000) + 4) * 900_000
    from tests.execution import test_perpetual_public_replay as public_fixture
    from tests.tools import (
        test_gold_bar_observed_replay as bars_fixture,
        test_gold_segmented_replay as segments_fixture,
    )

    for module in (public_fixture, bars_fixture, segments_fixture):
        monkeypatch.setattr(module, "START", origin + 10_000)
    monkeypatch.setattr(time, "time_ns", lambda: origin * 1_000_000)
    (tmp_path / "future").mkdir()
    collected = collection_request(
        tmp_path / "future", monkeypatch, dict(release_identity=back["source_identity"]), declared=True
    )
    arguments = read_arguments(collected, original_source=back["source_identity"])
    basic = json.loads((collected / "plan.json").read_bytes())["forward_design"]
    config = basic["configuration"]
    candidate = "channel"
    declared = basic["declared_at_ms"]
    start = basic["metrics"]["start_ms"]
    definition = next(
        r["strategy_definition_sha256"]
        for r in _cohort(digest(back["source_identity"]), config)
        if r["candidate_id"] == candidate
    )
    frozen = frozen_policy(
        reference=ref,
        candidate_id=candidate,
        strategy_definition_sha256=definition,
        declared_at_ms=declared,
        series_start_ms=start,
    )
    sample = {k: basic["metrics"][k] for k in ("start_ms", "end_ms", "frequency_ms")}
    forward = forward_policy(
        sample=sample,
        declared_at_ms=declared,
        series=dict(
            start_ms=start,
            declared_at_ms=declared,
            candidate_id=candidate,
            strategy_definition_sha256=definition,
            proposal_sha256="a" * 64,
        ),
    )
    design = declare(
        program_id=2,
        attempt=1,
        source_identity=back["source_identity"],
        configuration=config,
        first_close_ms=basic["window"]["closed_bars"][0],
        window_bars=len(basic["window"]["closed_bars"]),
        declared_at_ms=declared,
        long_window=True,
        selected_candidate=candidate,
        proposal_sha256="a" * 64,
        research_risk_policy=risk_policy(
            start=sample["start_ms"], end=sample["end_ms"], frequency=sample["frequency_ms"], declared=declared
        ),
        market_state_policy=market_policy(sample, declared=declared),
        capacity_policy=capacity_policy(sample, declared=declared),
        causality_policy=causality_policy(sample=sample, declared_at_ms=declared),
        net_forward_policy=forward,
        maintenance_policy=maintenance_policy(net_forward_policy=forward),
        frozen_validation_policy=frozen,
    )
    parts = list(documents(arguments))
    parts[0] = {k: v for k, v in parts[0].items() if k not in ("input_schema", "continuation_segments")}
    root = f["root"] / "future-originals"
    gold_chunked_source.freeze(parts, root, maximum_bytes=32_000_000)
    # Restore the original project/state bootstrap after fixture generation.
    monkeypatch.setenv("QUANTOS_STATE_ROOT", str(f["root"]))
    from tools import registered_research_job as workspace

    monkeypatch.setattr(workspace, "workspace_paths", lambda: (f["root"] / "project", f["root"] / "registry.db"))
    options = dict(
        older_source[2],
        search_id="future-bound",
        invocation_id="d" * 64,
        registered_at_ms=time.time_ns() // 1_000_000,
        forward_design=design,
    )
    f["clock"][0] = options["registered_at_ms"]
    plan = job.prepare_source_run(root, **options)
    job.run_research(dict(native_invocation_id=plan["invocation_id"]))
    exported = review.prepare_review_request(dict(native_invocation_id=plan["invocation_id"]))
    packet = review.read_request(exported["review_request_id"])
    return f, back, plan, row, packet, exported["review_request_id"], trust


def test_original_backtest_confidence_and_complete_history_reach_future_request(handoff):
    f, back, plan, row, packet, _, _ = handoff
    old = json.loads(row["evaluation_json"])["snapshot"]["statistical_evidence"]["report"]
    projected = packet["projection"]
    assert projected["schema"] == "QUANTOS_PROJECT_RESEARCH_FACTS_V18"
    model = projected["execution_risk_review"]["account_risk_inputs"]["model_inputs"]
    original = model["frozen_validation_review"]
    assert (
        original["status"] == "AVAILABLE"
        and original["frozen_attempt_count"] == 3
        and original["current_attempt_count"] == 6
    )
    assert original["statistical_decision"] == old["statistical_decision"]
    assert original["validation_confidence"] == old["metrics"]["dsr"]
    assert model["facts"]["validation_confidence"]["source"] == "ORIGINAL_FROZEN_BACKTEST_GLOBAL_DSR"
    assert original["independent_oos_verified"] is False
    assert model["facts"]["leakage_flag"]["value"] is None
    assert packet["project_cut"]["attempt_count"] == 6
    assert back["trials"][0]["config_hash"] != plan["trials"][0]["config_hash"]
    # The same original capability survives nested historical subsets, and
    # expires with its physical owning lease. Modified references cannot pass.
    from copy import deepcopy
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.validation.project_research_evidence_artifact import retained_reports

    policy = plan["forward_design"]["risk_policy"]["frozen_validation_policy"]
    args = dict(
        project_cut=packet["project_cut"],
        subject=packet["subject"],
        candidate_id="channel",
        policy=policy,
        native_result_hash=projected["native_result_hash"],
        snapshot_as_of_ms=packet["statistical_evidence"]["report"]["as_of_ms"],
    )
    with ProjectResearchEvidenceReader(
        f["root"] / "project", registry_database=f["root"] / "registry.db"
    ).evidence() as (cut, reports):
        nested = retained_reports(cut, retained_reports(cut, reports))
        actual = nested.review_gold_frozen_validation(**args)
        assert actual == original
        wrong_native = deepcopy(args)
        wrong_native["native_result_hash"] = "f" * 64
        with pytest.raises(ValueError, match="CURRENT_RESULT"):
            nested.review_gold_frozen_validation(**wrong_native)
        wrong_subject = deepcopy(args)
        wrong_subject["subject"]["native_trial_id"] = "f" * 64
        with pytest.raises(ValueError, match="CURRENT_DECLARATION"):
            nested.review_gold_frozen_validation(**wrong_subject)
        for field in ("content_hash", "statistical_report_sha256", "invocation_id", "project_cut_sha256"):
            changed = deepcopy(args)
            changed["policy"]["reference"][field] = "f" * 64
            with pytest.raises(ValueError):
                nested.review_gold_frozen_validation(**changed)
        wrong = deepcopy(args)
        wrong["policy"]["strategy_definition_sha256"] = "f" * 64
        with pytest.raises(ValueError):
            nested.review_gold_frozen_validation(**wrong)
        missing = reports.original_review_rows.pop(row["run_id"])
        with pytest.raises(ValueError, match="ORIGINAL_ROW_REQUIRED"):
            nested.review_gold_frozen_validation(**args)
        reports.original_review_rows[row["run_id"]] = missing
    with pytest.raises(ValueError, match="CONTEXT_CLOSED"):
        nested.review_gold_frozen_validation(**args)


def test_http_carries_the_same_frozen_original_and_complete_population(handoff, monkeypatch):
    from tests.research.test_console_api_contract import client
    from services.console.api import research_run_views as views
    import shutil

    f, _, _, _, packet, request, _ = handoff
    state = f["root"] / "console-copy"
    target = state / "data/cache/registered_research/review_requests" / request
    target.parent.mkdir(parents=True, mode=0o700)
    shutil.copytree(f["root"] / "review_requests" / request, target)
    monkeypatch.setattr(views, "runtime_state_root", lambda: str(state))
    response = client.get("/api/v1/console/registered-review-request", params=dict(review_request_id=request))
    assert response.status_code == 200
    assert response.json()["review_request"] == packet
    assert response.json()["authoritative"] is False


def test_future_review_persists_recovers_and_cold_verifies_original_backtest(handoff, monkeypatch):
    from tests.research.test_registered_research_review_job import independent_response
    from tools import registered_research_review_job as review
    from services.research.experiment_registry.store import SqliteExperimentStore
    from services.research.project_result_material_reader import ProjectResultMaterialReader

    f, back, plan, row, packet, request, trust = handoff
    later_trust = f["root"] / "synthetic-second-trust/trust.json"
    independent_response(packet, review.response_directory(request), later_trust)
    registry = json.loads(later_trust.read_bytes())
    registry["producer_ids"] = ["REGISTERED_GOLD_REANALYSIS_RUNNER_V1", "REGISTERED_GOLD_COLLECTOR_V1"]
    registry["keys"]["fixture"]["evidence_kinds"] = ["BACKTEST", "FORWARD"]
    later_trust.write_bytes(canonical(registry))
    later_trust.chmod(0o444)
    monkeypatch.setenv("QUANTOS_PROJECT_REVIEW_TRUST_FILE", str(later_trust))
    monkeypatch.setenv("QUANTOS_PROJECT_REVIEW_TRUST_SHA256", digest(later_trust.read_bytes()))
    result = review.consume_review(dict(review_request_id=request))
    assert result["qualification"] in ("REJECTED", "INSUFFICIENT")
    assert review.recover_review(dict(review_request_id=request))["review_id"] == result["review_id"]
    with closing(
        SqliteExperimentStore(
            str(f["root"] / "registry.db"),
            create=False,
            read_only=True,
            project_result_reader=ProjectResultMaterialReader(f["root"] / "project"),
        )
    ) as store:
        future = store.get_project_research_evidence(result["review_id"])
        assert (
            json.loads(future["evaluation_json"])["assessment"]["projection"]["execution_risk_review"][
                "account_risk_inputs"
            ]["model_inputs"]["frozen_validation_review"]["reference"]["review_id"]
            == row["run_id"]
        )
