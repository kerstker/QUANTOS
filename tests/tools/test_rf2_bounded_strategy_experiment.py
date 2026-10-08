"""RF2 bounded 12-case driver: integration, boundary and fail-closed tests.

The happy path runs a genuinely small canonical integration fixture through
``services.research.perpetual_alpha.replay`` and the real LinearPerpetualLedger
(a few thousand consecutive synthetic minutes), not a mock ledger.  Only the archive
reader ``load_data`` and the release probe ``source_identity`` are injected;
the driver itself has no production bypass flag.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

import numpy as np
import pytest

from services.research.trial_ledger import TrialLedger
from tools import rf2_bounded_strategy_experiment as driver

DAY_MS = 86_400_000
MINUTE_MS = 60_000
BASE_LOG = 10.0
GROWTH = 0.01
NOISE = 0.002
EXPECTED_CASES = sorted(f"{name}/{slip}" for name in ("cash", "passive25", "return_sign90", "log_slope90")
                        for slip in (2, 5, 10))

BASE_SOURCE = {"commit": "1" * 40, "python_source_sha256": "2" * 64,
               "dependency_lock_sha256": "3" * 64, "python_version": "3.12.0",
               "architecture": "aarch64"}
DRIFTED_SOURCE = {**BASE_SOURCE, "commit": "9" * 40}


def _utc_ms(text):
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) * 1000


def _closes(count, noise):
    values = []
    for index in range(count):
        wiggle = 0.0 if not noise else noise * ((index * 7919) % 101) / 101.0
        values.append(math.exp(BASE_LOG + GROWTH * index + wiggle))
    return np.array(values, dtype=float)


def _fixture(*, first_day="2025-04-01", daily_days=98, start_day="2025-07-01", decisions=4,
             noise=NOISE, nan_indices=(), maximum_quantity="50", volume="1000000", funding_events=True):
    """Synthetic daily closes and consecutive minutes passed to the real ledger."""
    day_start = _utc_ms(first_day)
    days = np.arange(day_start, day_start + daily_days * DAY_MS, DAY_MS, dtype=np.int64)
    clean = _closes(len(days), noise)
    daily = np.zeros((len(days), 5), dtype=float)
    daily[:, 0] = clean
    daily[:, 1] = clean * 1.001
    daily[:, 2] = clean * 0.999
    daily[:, 3] = clean
    daily[:, 4] = 1.0
    for index in nan_indices:
        daily[index, 3] = np.nan
    # Minute prices stay finite where a daily close record is missing: the gap
    # models an unavailable daily aggregate, not a broken archive.
    close_by_day = {int(day) // DAY_MS: float(value) for day, value in zip(days, clean)}
    start = _utc_ms(start_day) + 5 * MINUTE_MS
    end = start + decisions * DAY_MS
    times = np.arange(start, end + MINUTE_MS, MINUTE_MS, dtype=np.int64)
    prices = np.array([close_by_day[int(stamp) // DAY_MS - 1] for stamp in times], dtype=float)
    marks = prices.copy()
    volumes = {}
    for index in range(int(start) // DAY_MS, int(end) // DAY_MS + 1):
        volumes[index] = D(volume)
        volumes[index + 1] = D(volume)  # load_data keys completed days by index+1
    funding = []
    if funding_events:
        for offset in (1, 3):
            at = int(start) + offset * DAY_MS
            funding.append({"time": at, "rate": D("0.0001"),
                            "mark": D(str(float(marks[int(np.searchsorted(times, at))])))})
    provenance = {"archives": [{"name": "fixture-usdm-BTCUSDC-1m.zip"}], "rules_sha256": "c" * 64,
                  "funding_capture_sha256": "d" * 64, "unmatched_trade_minutes": 0,
                  "unmatched_mark_minutes": 0, "incomplete_signal_days": int(np.isnan(daily[:, 3]).sum()),
                  "aligned_minutes": int(len(times))}
    market = {"times": times, "prices": prices, "marks": marks, "funding": funding, "start": int(start),
              "end": int(end), "daily_volume": volumes, "maximum_quantity": D(maximum_quantity)}
    return market, days, daily, np.zeros(len(days)), np.zeros(len(days)), provenance


def _loader(expected_input, fixture):
    def load(*, include_intrabar=False, directory=None):
        assert directory == str(expected_input), "driver must read the validated input directory"
        assert include_intrabar is False, "driver must not request intrabar series"
        return fixture
    return load


def _probe_sequence(limit, drifted=DRIFTED_SOURCE, error_at=None):
    state = {"calls": 0}

    def probe():
        state["calls"] += 1
        if error_at is not None and state["calls"] >= error_at:
            raise RuntimeError("release probe unavailable")
        return dict(BASE_SOURCE) if state["calls"] <= limit else dict(drifted)

    return probe


def _run(tmp_path, fixture=None, *, source_probe=None, plan_probe=None, name="output"):
    source = tmp_path / "input"
    source.mkdir(parents=True)
    (source / "fixture-manifest.json").write_text('{"fixture": "rf2-tiny"}\n')
    output = tmp_path / name
    data = _fixture() if fixture is None else fixture
    saved = (driver.load_data, driver.source_identity, driver._plan_digest)
    driver.load_data = _loader(source, data)
    driver.source_identity = source_probe or (lambda: dict(BASE_SOURCE))
    if plan_probe is not None:
        driver._plan_digest = plan_probe
    try:
        summary = driver.run_experiment(str(source), str(output))
    finally:
        driver.load_data, driver.source_identity, driver._plan_digest = saved
    return source, output, summary


def _snapshot(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def _case(output, candidate, slip):
    return json.loads((output / f"case-{candidate}-{slip}.json").read_text())


def _ledger_statuses(output):
    connection = sqlite3.connect(output / "trials.sqlite")
    try:
        ids = [row[0] for row in connection.execute("SELECT DISTINCT trial_id FROM trial_events")]
    finally:
        connection.close()
    with TrialLedger(str(output / "trials.sqlite")) as ledger:
        ledger.verify()
        return {trial_id: ledger.load(trial_id).status for trial_id in ids}


def _sign(value):
    return (value > 0) - (value < 0)


def _module_experiment(tmp_path_factory):
    base = tmp_path_factory.mktemp("rf2")
    source = base / "input"
    source.mkdir()
    (source / "fixture-manifest.json").write_text('{"fixture": "rf2-tiny"}\n')
    before = _snapshot(source)
    output = base / "output"
    fixture = _fixture()
    saved = (driver.load_data, driver.source_identity)
    driver.load_data = _loader(source, fixture)
    driver.source_identity = lambda: dict(BASE_SOURCE)
    try:
        summary = driver.run_experiment(str(source), str(output))
    finally:
        driver.load_data, driver.source_identity = saved
    return {"source": source, "output": output, "summary": summary, "before": before,
            "after": _snapshot(source), "fixture": fixture}


experiment = pytest.fixture(scope="module")(_module_experiment)


def test_registered_plan_is_retained_with_matching_digest(experiment):
    saved = json.loads((experiment['output'] / 'registration.json').read_text())
    actual = hashlib.sha256(saved['plan_text'].encode('utf-8')).hexdigest()
    assert actual == saved['plan_sha256'] == experiment['summary']['identity']['plan_sha256']


def test_registers_all_twelve_planned_trials_before_first_replay(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    output = tmp_path / "output"
    seen = []
    snapshots = []
    real_replay = driver.replay

    def spy(**kwargs):
        # Collect only: an assertion raised here would be swallowed as a case
        # failure by the driver's per-case error boundary.
        if not snapshots:
            connection = sqlite3.connect(f"file:{output / 'trials.sqlite'}?mode=ro", uri=True)
            try:
                trials = connection.execute("SELECT COUNT(DISTINCT trial_id) FROM trial_events").fetchone()[0]
                advanced = connection.execute(
                    "SELECT event_index, status FROM trial_events WHERE event_index>0").fetchall()
            finally:
                connection.close()
            snapshots.append((trials, advanced))
        seen.append(kwargs)
        return real_replay(**kwargs)

    saved = (driver.load_data, driver.source_identity, driver.replay)
    driver.load_data = _loader(source, _fixture())
    driver.source_identity = lambda: dict(BASE_SOURCE)
    driver.replay = spy
    try:
        summary = driver.run_experiment(str(source), str(output))
    finally:
        driver.load_data, driver.source_identity, driver.replay = saved
    assert summary["failed_cases"] == 0
    trials, advanced = snapshots[0]
    assert trials == driver.EXPECTED_CASE_COUNT == 12
    assert advanced == [(1, "RUNNING")], (
        "all twelve trials must be registered PLANNED before the first replay, with only the "
        "case being replayed advanced to RUNNING")
    assert len(seen) == 12, "exactly one replay per pre-registered case"
    assert {entry["slip_bps"] for entry in seen} == {2, 5, 10}
    assert all(entry["stop"] is True for entry in seen)
    assert all(entry["taker_fee_bps"] == D("4") for entry in seen)
    assert all(entry["target_weight"] == D(".25") and entry["max_position_weight"] == D(".30") for entry in seen)


def test_full_batch_is_technical_validation_only_with_twelve_cases(experiment):
    summary = experiment["summary"]
    assert summary["status"] == "TECHNICAL_VALIDATION_ONLY"
    assert summary["evidence_class"] == "TECHNICAL_VALIDATION_ONLY"
    assert summary["history_class"] == "SEEN_HISTORY_NOT_UNTOUCHED_OOS"
    assert summary["authoritative"] is False and summary["authority_admitted"] is False
    assert summary["live_eligible"] is False and summary["paper_admitted"] is False
    assert "SEEN_HISTORY_NOT_UNTOUCHED_OOS" in summary["limitations"]
    assert sorted(summary["cases"]) == EXPECTED_CASES
    assert summary["planned_trials"] == 12 and len(summary["planned_trial_ids"]) == 12
    assert len(set(summary["planned_trial_ids"])) == 12
    assert summary["completed_cases"] == 12 and summary["failed_cases"] == 0
    assert summary["trial_ledger_verified"] is True
    identity = summary["identity"]
    assert identity["source"] == BASE_SOURCE
    assert identity["plan_sha256"] == hashlib.sha256(driver.PLAN.read_bytes()).hexdigest()
    market, _, _, _, _, _ = experiment["fixture"]
    assert identity["dataset"]["start_ms"] == market["start"]
    assert identity["dataset"]["end_ms"] == market["end"]
    profile = identity["risk_cost_profile"]
    assert profile["stop_enabled"] is True and profile["initial_collateral"] == "1500"
    assert profile["cumulative_loss_budget"] == "150" and profile["taker_fee_bps"] == "4"
    assert profile["slippage_bps"] == [2, 5, 10] and profile["target_weight"] == "0.25"
    assert profile["max_position_weight"] == "0.30" and profile["daily_capacity_fraction"] == "0.01"
    assert _ledger_statuses(experiment["output"]) == {
        trial: "PASSED" for trial in summary["planned_trial_ids"]}
    artifact = _case(experiment["output"], "log_slope90", 5)
    assert artifact["trial_id"] in summary["planned_trial_ids"]
    assert artifact["cost_profile"]["slippage_bps"] == 5
    assert artifact["authority_admitted"] is False and artifact["live_eligible"] is False
    assert artifact["paper_admitted"] is False


def test_case_artifacts_are_exclusive_nan_free_json(experiment):
    output = experiment["output"]
    for key in EXPECTED_CASES:
        name, slip = key.split("/")
        text = (output / f"case-{name}-{slip}.json").read_text()
        assert "NaN" not in text and "Infinity" not in text
        json.loads(text, parse_constant=lambda value: pytest.fail(f"non-finite {value} in {key}"))
    assert sorted(path.name for path in output.glob("case-*.json")) == [
        f"case-{name}-{slip}.json" for name, slip in
        sorted((key.split("/")[0], key.split("/")[1]) for key in EXPECTED_CASES)]
    assert not list(output.glob("invalidated*.json"))
    assert (output / "identity.json").is_file() and (output / "summary.json").is_file()


def test_decisions_track_actual_ledger_quantity(experiment):
    for key in EXPECTED_CASES:
        name, slip = key.split("/")
        for record in _case(experiment["output"], name, slip)["decisions"]:
            assert record["held_sign_actual"] == _sign(D(record["actual_quantity"]))
            assert record["actual_held_sign"] == record["held_sign_actual"]
            assert D(record["replay_target"]) == D(record["target_weight"])
            assert record["held_sign_intended"] == _sign(D(record["target_weight"]))
            assert isinstance(record["signal_reason"], str) and record["signal_reason"]
        assert _case(experiment["output"], name, slip)["metrics"]["decision_count"] == 4
    log_case = _case(experiment["output"], "log_slope90", 5)
    assert log_case["decisions"][0]["signal_status"] == "ACTIVE"
    assert log_case["decisions"][0]["target_weight"] == "0.25"
    assert log_case["decisions"][0]["actual_quantity"] == "0"
    assert log_case["metrics"]["fills"] >= 1
    passive = _case(experiment["output"], "passive25", 5)
    assert all(record["target_weight"] == "0.25" for record in passive["decisions"])
    cash = _case(experiment["output"], "cash", 5)
    assert all(record["target_weight"] == "0" for record in cash["decisions"])
    assert cash["metrics"]["fills"] == 0 and D(cash["attribution"]["net_pnl"]) == 0


def test_rejected_target_keeps_actual_quantity_and_reports_feedback(tmp_path):
    source, output, summary = _run(tmp_path, _fixture(maximum_quantity="0.0001"))
    assert summary["failed_cases"] == 0
    artifact = _case(output, "log_slope90", 5)
    assert artifact["metrics"]["fills"] == 0
    assert artifact["metrics"]["rejected_orders"] >= 1
    assert {order["status"] for order in artifact["replay"]["orders"]} == {"REJECTED_MAX_QUANTITY"}
    for record in artifact["decisions"]:
        assert record["actual_quantity"] == "0" and record["held_sign_actual"] == 0
    assert artifact["decisions"][0]["target_weight"] == "0.25"
    assert artifact["decisions"][0]["held_sign_intended"] == 1
    assert all(record["target_weight"] == "0.25" for record in artifact["decisions"]), (
        "a rejected entry must not be remembered as an executed position")


def test_gross_is_not_double_deducted(experiment):
    artifact = _case(experiment["output"], "log_slope90", 5)
    attribution, reconciliation = artifact["attribution"], artifact["replay"]["reconciliation"]
    assert attribution["status"] == "RECONCILED_TECHNICAL_ONLY"
    assert attribution["reconciliation_residual"] == "0" and D(reconciliation["residual"]) == 0
    net, gross = D(attribution["net_pnl"]), D(attribution["gross_reference_pnl"])
    fees, funding = D(attribution["fees"]), D(attribution["funding"])
    assert fees > 0 and artifact["metrics"]["fills"] >= 1
    assert net == D(reconciliation["ending_equity"]) - D(reconciliation["initial_equity"])
    assert gross == D(reconciliation["price_pnl"]) + D(attribution["embedded_slippage"])
    assert net == (D(reconciliation["price_pnl"]) - fees - D(reconciliation["spread"])
                   - D(reconciliation["impact"]) + funding)
    assert net != gross - 2 * fees, "fees must not be deducted twice"


def test_screening_fails_when_pre_registered_conditions_unmet(experiment):
    screening = experiment["summary"]["screening"]
    assert screening["passed"] is False
    assert screening["reason"] == "PRE_REGISTERED_CONDITIONS_NOT_MET"
    assert set(screening["checks"]) == {
        "all_three_cost_cases_net_positive", "center_both_natural_years_positive",
        "center_at_least_ten_fills", "center_max_drawdown_at_most_ten_percent",
        "center_net_above_passive_center", "center_turnover_below_return_sign90_center"}
    assert screening["checks"]["center_at_least_ten_fills"] is False
    assert screening["checks"]["center_both_natural_years_positive"] is False
    assert screening["authority_admitted"] is False and screening["paper_admitted"] is False
    assert screening["live_eligible"] is False
    assert set(screening["failed_checks"]) == {k for k, ok in screening["checks"].items() if not ok}
    assert screening["failed_checks"], "an unmet condition must be named, never hidden"


def test_input_unchanged_and_existing_output_rejected(experiment):
    assert experiment["before"] == experiment["after"]
    output = experiment["output"]
    assert (experiment["source"] / "fixture-manifest.json").read_text() == '{"fixture": "rf2-tiny"}\n'
    before = _snapshot(output)
    with pytest.raises(driver.PathRejection):
        driver.run_experiment(str(experiment["source"]), str(output))
    assert _snapshot(output) == before, "a rejected rerun must leave the first batch untouched"


def test_rejects_existing_output_without_touching_it(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    output = tmp_path / "existing"
    output.mkdir()
    (output / "sentinel.txt").write_text("keep me")
    with pytest.raises(driver.PathRejection, match="must not exist"):
        driver.run_experiment(str(source), str(output))
    assert (output / "sentinel.txt").read_text() == "keep me"
    assert not (output / "trials.sqlite").exists()
    assert sorted(path.name for path in output.iterdir()) == ["sentinel.txt"]


def test_rejects_relative_missing_and_overlapping_paths(tmp_path):
    with pytest.raises(driver.PathRejection):
        driver.run_experiment("relative-input", str(tmp_path / "out"))
    with pytest.raises(driver.PathRejection):
        driver.run_experiment(str(tmp_path / "input"), "relative-output")
    with pytest.raises(driver.PathRejection):
        driver.run_experiment(str(tmp_path / "missing"), str(tmp_path / "out"))
    source = tmp_path / "input"
    source.mkdir()
    with pytest.raises(driver.PathRejection, match="overlap"):
        driver.run_experiment(str(source), str(source / "nested" / "out"))
    assert not (source / "nested").exists(), "a rejected path must not create directories"


def test_rejects_symlinked_input_output_and_parent(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    os.symlink(real, linked)
    with pytest.raises(driver.PathRejection):
        driver.run_experiment(str(linked), str(tmp_path / "out-a"))
    with pytest.raises(driver.PathRejection):
        driver.run_experiment(str(real), str(linked))
    with pytest.raises(driver.PathRejection):
        driver.run_experiment(str(real), str(linked / "out-b"))
    dangling = tmp_path / "dangling"
    os.symlink(tmp_path / "nowhere", dangling)
    with pytest.raises(driver.PathRejection):
        driver.run_experiment(str(real), str(dangling))
    assert not (tmp_path / "out-a").exists() and not (real / "out-b").exists()


def test_rejects_output_under_srv_or_opt(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    for forbidden in ("/srv/rf2-bounded-output", "/opt/rf2-bounded-output"):
        with pytest.raises(driver.PathRejection, match="forbidden"):
            driver.run_experiment(str(source), forbidden)


def test_identity_drift_after_loading_writes_no_summary(tmp_path):
    with pytest.raises(driver.IdentityDriftError):
        _run(tmp_path, source_probe=_probe_sequence(1))
    output = tmp_path / "output"
    assert not (output / "summary.json").exists()
    assert not list(output.glob("case-*.json"))
    invalidated = json.loads((output / "invalidated.json").read_text())
    assert invalidated["status"] == "INVALIDATED_IDENTITY_DRIFT"
    assert invalidated["authority_admitted"] is False and invalidated["live_eligible"] is False


def test_identity_drift_during_a_case_cancels_the_remaining_trials(tmp_path):
    # Probe order: 1 freeze, 2 pre-load, 3 post-load, 4 pre-case, 5 post-case.
    # Drifting from call 5 on means case one completes and is then invalidated.
    with pytest.raises(driver.IdentityDriftError):
        _run(tmp_path, source_probe=_probe_sequence(4))
    output = tmp_path / "output"
    assert not (output / "summary.json").exists()
    assert not list(output.glob("case-*.json")), "a drifted case must not publish an artifact"
    statuses = _ledger_statuses(output)
    assert sorted(statuses.values()) == ["CANCELLED"] * 11 + ["FAILED"]
    drifted = list(output.glob("invalidated-*.json"))
    assert len(drifted) == 1
    invalidated = json.loads(drifted[0].read_text())
    assert invalidated["status"] == "INVALIDATED_IDENTITY_DRIFT"
    assert invalidated["trial_id"] in statuses


def test_plan_digest_drift_is_detected(tmp_path):
    state = {"calls": 0}

    def plan_probe():
        state["calls"] += 1
        return "e" * 64 if state["calls"] <= 1 else "f" * 64

    with pytest.raises(driver.IdentityDriftError, match="plan"):
        _run(tmp_path, plan_probe=plan_probe)
    assert not (tmp_path / "output" / "summary.json").exists()


def test_identity_probe_error_fails_closed(tmp_path):
    with pytest.raises(driver.IdentityDriftError, match="identity check failed"):
        _run(tmp_path, source_probe=_probe_sequence(0, error_at=2))
    assert not (tmp_path / "output" / "summary.json").exists()


def test_malformed_window_fails_signal_cases_only(tmp_path):
    source, output, summary = _run(tmp_path, _fixture(nan_indices=(10,)))
    assert summary["failed_cases"] == 6 and summary["completed_cases"] == 6
    for name in ("cash", "passive25"):
        for slip in (2, 5, 10):
            assert _case(output, name, slip)["status"] == "TECHNICAL_VALIDATION_ONLY"
    for name in ("return_sign90", "log_slope90"):
        for slip in (2, 5, 10):
            artifact = _case(output, name, slip)
            assert artifact["status"] == "FAILED"
            assert "MALFORMED_INPUT" in artifact["error"]
            assert summary["cases"][f"{name}/{slip}"]["status"] == "FAILED"
            assert summary["cases"][f"{name}/{slip}"]["error"]
    assert summary["screening"]["reason"] == "FAILED_COMPUTATION"
    assert summary["screening"]["passed"] is False
    assert sorted(_ledger_statuses(output).values()) == ["FAILED"] * 6 + ["PASSED"] * 6


def test_missing_warmup_price_is_not_a_failure(tmp_path):
    source, output, summary = _run(tmp_path, _fixture(nan_indices=(0,)))
    assert summary["failed_cases"] == 0
    artifact = _case(output, "log_slope90", 5)
    assert artifact["status"] == "TECHNICAL_VALIDATION_ONLY"
    first = artifact["decisions"][0]
    assert first["signal_status"] == "ACTIVE" and first["target_weight"] == "0.25"
    assert first["window_last_day"] == "2025-06-30"


def test_insufficient_history_is_reported_not_failed(tmp_path):
    source, output, summary = _run(tmp_path, _fixture(first_day="2025-06-28", daily_days=10))
    assert summary["failed_cases"] == 0
    artifact = _case(output, "log_slope90", 5)
    assert artifact["status"] == "TECHNICAL_VALIDATION_ONLY"
    assert artifact["metrics"]["fills"] == 0
    for record in artifact["decisions"]:
        assert record["signal_status"] == "INSUFFICIENT_HISTORY"
        assert record["target_weight"] == "0" and "insufficient history" in record["signal_reason"]
        assert record["statistic"] is None
    assert summary["screening"]["passed"] is False


def test_degenerate_estimate_is_reported_with_zero_target(tmp_path):
    source, output, summary = _run(tmp_path, _fixture(noise=0.0))
    artifact = _case(output, "log_slope90", 5)
    assert artifact["status"] == "TECHNICAL_VALIDATION_ONLY"
    first = artifact["decisions"][0]
    assert first["signal_status"] == "DEGENERATE_ESTIMATE"
    assert first["statistic"] is None and first["target_weight"] == "0"
    assert "degenerate" in first["signal_reason"]
    assert artifact["metrics"]["fills"] == 0


def test_deterministic_case_set_across_two_runs(tmp_path):
    source_a, output_a, summary_a = _run(tmp_path / "a", name="out")
    source_b, output_b, summary_b = _run(tmp_path / "b", name="out")
    assert summary_a["planned_trial_ids"] == summary_b["planned_trial_ids"]
    assert sorted(summary_a["cases"]) == sorted(summary_b["cases"]) == EXPECTED_CASES
    for key in EXPECTED_CASES:
        name, slip = key.split("/")
        case_a, case_b = _case(output_a, name, slip), _case(output_b, name, slip)
        assert case_a["trial_id"] == case_b["trial_id"]
        assert case_a["status"] == case_b["status"]
        keys = ("time", "signal_status", "signal_reason", "statistic", "target_weight",
                "held_sign_actual", "held_sign_intended")
        assert [{k: r[k] for k in keys} for r in case_a["decisions"]] == [
            {k: r[k] for k in keys} for r in case_b["decisions"]]
        assert summary_a["cases"][key]["net_pnl"] == summary_b["cases"][key]["net_pnl"]


def test_cli_requires_both_arguments_and_returns_zero_on_success(tmp_path):
    with pytest.raises(SystemExit):
        driver.main([])
    with pytest.raises(SystemExit):
        driver.main(["--input", str(tmp_path)])
    source = tmp_path / "input"
    source.mkdir()
    output = tmp_path / "output"
    saved = (driver.load_data, driver.source_identity)
    driver.load_data = _loader(source, _fixture())
    driver.source_identity = lambda: dict(BASE_SOURCE)
    try:
        assert driver.main(["--input", str(source), "--output", str(output)]) == 0
        assert (output / "summary.json").is_file()
        assert driver.main(["--input", str(source), "--output", str(output)]) == 1
    finally:
        driver.load_data, driver.source_identity = saved
