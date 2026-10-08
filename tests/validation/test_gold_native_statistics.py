"""Native Gold statistics must use cash facts only after their original receipt."""

import copy
import importlib

import pytest

from quantos.contracts.research_statistics import fixed_frequency_equity
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments


def module():
    return importlib.import_module("services.validation.gold_native_statistics")


def funding_case():
    history = [
        dict(timestamp=t, sequence=i, phase="INITIAL" if i == 0 else "MARKET", equity=e)
        for i, (t, e) in enumerate([(0, "100"), (10, "100.2"), (20, "99.2"), (30, "99.3"), (40, "99.4")])
    ]
    event = dict(
        event_type="FUNDING_SETTLEMENT",
        timestamp=20,
        instrument_id="XAUUSDT",
        funding_rate="0.01",
        mark_price="100",
        cash_flow="-1",
        pre_settlement_quantity="1",
    )
    settlement = dict(
        settlement_at_ms=20,
        available_at_ms=31,
        requested_at_ms=30,
        instrument_id="XAUUSDT",
        funding_rate="0.01",
        mark_price="100",
    )
    return dict(equity_history=history, ledger_events=[event]), [settlement]


def test_funding_cash_and_subsequent_marks_wait_for_original_receipt():
    simulation, settlements = funding_case()
    before = copy.deepcopy(simulation)
    history = module().receipt_available_history(simulation, settlements)
    grid = fixed_frequency_equity(history, start_ms=0, end_ms=40, frequency_ms=10)
    assert [p["equity"] for p in grid["points"]] == ["100", "100.2", "100.2", "100.2", "99.4"]
    assert [p["timestamp"] for p in history] == [0, 10, 31, 31, 40]
    assert [p["equity"] for p in history] == [p["equity"] for p in before["equity_history"]]
    assert simulation == before


@pytest.mark.parametrize("damage", ["missing", "extra", "rate", "mark", "instrument", "early", "unordered"])
def test_missing_or_mismatched_cash_knowledge_is_rejected(damage):
    simulation, settlements = funding_case()
    if damage == "missing":
        settlements = []
    elif damage == "extra":
        settlements.append({**settlements[0], "settlement_at_ms": 21})
    elif damage in ("rate", "mark", "instrument"):
        settlements[0][{"rate": "funding_rate", "mark": "mark_price", "instrument": "instrument_id"}[damage]] = {
            "rate": "0.02",
            "mark": "101",
            "instrument": "BTCUSDT",
        }[damage]
    elif damage == "early":
        settlements[0]["available_at_ms"] = 19
    else:
        simulation["equity_history"][2]["sequence"] = 0
    with pytest.raises(ValueError):
        module().receipt_available_history(simulation, settlements)


def test_multiple_delayed_facts_do_not_rewrite_an_earlier_grid():
    simulation, settlements = funding_case()
    early = module().receipt_available_history(simulation, settlements)
    simulation["equity_history"] += [
        dict(timestamp=50, sequence=5, phase="MARKET", equity="98.4"),
        dict(timestamp=60, sequence=6, phase="MARKET", equity="98.5"),
    ]
    simulation["ledger_events"].append({**simulation["ledger_events"][0], "timestamp": 50})
    settlements.append({**settlements[0], "settlement_at_ms": 50, "requested_at_ms": 60, "available_at_ms": 61})
    later = module().receipt_available_history(simulation, settlements)
    assert fixed_frequency_equity(early, start_ms=0, end_ms=40, frequency_ms=10) == fixed_frequency_equity(
        later, start_ms=0, end_ms=40, frequency_ms=10
    )


def test_initial_equity_precedes_same_timestamp_cash_settlement():
    simulation, settlements = funding_case()
    simulation["equity_history"] = [
        dict(timestamp=0, sequence=0, phase="INITIAL", equity="100"),
        dict(timestamp=0, sequence=1, phase="MARKET", equity="99"),
        dict(timestamp=1, sequence=2, phase="MARKET", equity="99.1"),
    ]
    simulation["ledger_events"][0]["timestamp"] = 0
    settlements[0].update(settlement_at_ms=0, requested_at_ms=1, available_at_ms=2)
    history = module().receipt_available_history(simulation, settlements)
    assert [p["equity"] for p in fixed_frequency_equity(history, start_ms=0, end_ms=2, frequency_ms=1)["points"]] == [
        "100",
        "100",
        "99.1",
    ]


def test_failed_native_attempts_have_no_invented_cash_curve_or_baseline(tmp_path, monkeypatch, technical_arguments):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from tests.tools.test_demo_gold_research_checkpoint import native_program, originals
    from tools import registered_gold_collection as bridge

    f = native_program(
        tmp_path,
        monkeypatch,
        source=technical_arguments["release_identity"],
        extended=True,
        register_original_trials=True,
        failure="research",
    )
    base = f["root"] / "data/cache/registered_research"
    cut = ProjectResearchEvidenceReader(base / "project", registry_database=base / "registry.db")()
    before = originals(f["root"])
    view = module().project_gold_statistics(project_cut=cut, invocation_id=bridge.invocation_id(1))
    assert view["population"]["attempt_count"] == 4
    assert {a["status"] for a in view["population"]["all_attempts"]} == {"FAILED"}
    assert view["benchmark"] is None
    assert len(view["review"]["comparison"]["included_attempts"]) == 4
    assert view["review"]["comparison"]["excluded_attempts"] == []
    assert view["review"]["population"]["failed_attempt_count"] == 4
    assert view["review"]["metrics"]["matrix_sha256"] is None
    assert all(
        a["grid"] is a["metrics"] is None and a["reasons"] == ["NATIVE_ATTEMPT_NOT_COMPLETE"] for a in view["attempts"]
    )
    assert originals(f["root"]) == before


def test_actual_gold_originals_supply_candidate_portfolio_baseline_and_independent_facts(
    tmp_path, monkeypatch, technical_arguments
):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.validation import project_research_evidence_authority as facts
    from tests.tools.test_demo_gold_research_checkpoint import native_program, originals
    from tools import registered_gold_collection as bridge, gold_bar_research

    f = native_program(
        tmp_path,
        monkeypatch,
        source=technical_arguments["release_identity"],
        extended=True,
        register_original_trials=True,
    )
    base = f["root"] / "data/cache/registered_research"
    cut = ProjectResearchEvidenceReader(base / "project", registry_database=base / "registry.db")()
    invocation = bridge.invocation_id(1)
    run = cut["runs"][invocation]
    first = run["plan"]["trials"][0]
    subject = facts.project_subject(cut, invocation_id=invocation, native_trial_id=first["native_trial_id"])
    independent = facts.project_facts(cut, subject)
    assert independent["metrics"] is not None
    before = originals(f["root"])
    view = module().project_gold_statistics(project_cut=cut, invocation_id=invocation)
    assert view["population"]["attempt_count"] == 4
    assert [a["candidate_id"] for a in view["attempts"]] == ["channel", "trend", "reversion", "net_portfolio"]
    assert view["status"] == "TECHNICAL_VALIDATION_ONLY"
    assert view["statistical_review"] == "COMPLETED"
    assert view["review"]["metrics"]["n_trials"] == 4
    assert view["benchmark"]["id"] == "GOLD_NO_ORDER_ORIGINAL_ACCOUNT_V1"
    assert {p["equity"] for p in view["benchmark"]["grid"]["points"]} == {"1000"}
    selected = view["attempts"][0]
    assert selected["metrics"]["fees"] == "0.08" and selected["metrics"]["funding"] == "-0.8"
    assert selected["metrics"]["net_equity_change"] == "-1.04"
    close = run["plan"]["forward_design"]["window"]["closed_bars"][0]
    points = {p["timestamp"]: p for p in selected["grid"]["points"]}
    assert points[close + 30000]["equity"] == "999.87996"
    assert points[close + 40000]["equity"] == "999.07996"
    assert selected["grid"]["statistics_status"] == "INSUFFICIENT_DURATION"
    assert selected["grid"]["annualized_sharpe"] is None
    assert selected["full_run_covered"] is False
    assert "FULL_RUN_TERMINAL_NOT_COVERED" in selected["reasons"]
    assert independent["metrics"] == selected["metrics"]
    assert independent["native_result_hash"] == selected["native_result_sha256"]
    assert "PROJECT_NATIVE_RESULT_PROJECTION_UNAVAILABLE" not in independent["limitations"]
    assert "GOLD_EVALUATION_CORE_ADAPTER_UNAVAILABLE" in independent["limitations"]
    assert independent["promotion_approved"] is independent["submission_enabled"] is False
    retained = gold_bar_research.retained_publication(f["root"] / "data/cache/gold_bar_research/results/1", 1)
    assert retained["report"]["native_statistics"] == view
    assert f["result"]["rounds"][0]["report"]["native_statistics"] == view
    assert originals(f["root"]) == before
    document = facts._original(run, "input.json")
    dataset = facts._original(run, "archive/report.json")["dataset_identity"]
    for key, value in [("received_at", close + 20001), ("payload", []), ("response_sha256", "0" * 64)]:
        changed = copy.deepcopy(document)
        changed["observed_funding_records"][0][key] = value
        with pytest.raises(ValueError):
            module().funding_settlements(changed, dataset)
    # A later failed attempt belongs to a fresh review, but cannot rewrite the
    # complete population snapshot originally published with this old report.
    from tools import registered_research_run as native, registered_research_job as job
    from tools import gold_bar_experiment

    arguments = gold_bar_research._arguments({"attempt": 1})[0]
    failed = native.prepare_gold(
        arguments,
        project_root=base / "project",
        search_id="later-failed-search",
        producer_id="REGISTERED_GOLD_COLLECTOR_V1",
        actor_id="AUTOMATED_RESEARCH",
        dataset_version=native.contract.digest(native._gold_input(arguments)),
        registered_at_ms=f["clock"][0],
        invocation_id="9" * 64,
    )

    def fail(_arguments):
        raise ValueError("RETAINED_LATER_NATIVE_FAILURE")

    with monkeypatch.context() as patch:
        patch.setattr(gold_bar_experiment, "_execute", fail)
        with job._registry(base / "registry.db", initialize=False) as registry:
            with pytest.raises(ValueError, match="RETAINED_LATER_NATIVE_FAILURE"):
                native.run_gold(
                    arguments, destination=base / "project/runs" / failed["invocation_id"], registry=registry
                )
    current = ProjectResearchEvidenceReader(base / "project", registry_database=base / "registry.db")()
    fresh = module().project_gold_statistics(project_cut=current, invocation_id=invocation)
    assert fresh["population"]["attempt_count"] == 8
    assert [a["status"] for a in fresh["population"]["all_attempts"]].count("FAILED") == 4
    assert fresh["review"]["metrics"]["n_trials"] == 8
    assert fresh["review"]["metrics"]["matrix_sha256"] is not None
    assert fresh["review"]["metrics"]["dsr"] is fresh["review"]["metrics"]["var_sr"] is None
    assert fresh["review"]["checks"]["dsr"] == "INSUFFICIENT"
    assert "GLOBAL_DSR_VARIANCE_NOT_COMPARABLE" in fresh["review"]["reasons"]
    assert len(fresh["review"]["comparison"]["excluded_attempts"]) == 4
    assert fresh["review"]["population"]["failed_attempt_count"] == 4
    before = originals(f["root"])
    retained = gold_bar_research.retained_publication(f["root"] / "data/cache/gold_bar_research/results/1", 1)
    assert retained["report"]["native_statistics"] == view
    assert originals(f["root"]) == before
    # Recovery is read only: losing the published cut cannot recreate a new
    # historical population from the now larger project.
    path = f["root"] / "data/cache/gold_bar_research/results/1" / bridge.STATISTICS_CUT
    backup = path.with_suffix(".retained-test-backup")
    path.rename(backup)
    try:
        with pytest.raises(ValueError, match="GOLD_STATISTICS_PUBLICATION_CUT_MISSING"):
            gold_bar_research.retained_publication(path.parent, 1)
        assert not path.exists()
    finally:
        backup.rename(path)
