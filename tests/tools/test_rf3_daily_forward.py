"""RF3 daily forward signal publication tests.

Every receipt is synthetic and the public-HTTP boundary is a stub: no network,
no real public data and no real registration run.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.research import rf3_daily_forward as rf3
from tools import rf3_daily_forward as tool

REPO = Path(__file__).resolve().parents[2]
CREATED = 1_787_000_000_000
IDENTITY = "c" * 64
ANCESTRY = "a" * 64


def _raw(slot, closes):
    start = slot - rf3.WINDOW_DAYS * rf3.DAY_MS
    rows = [[start + index * rf3.DAY_MS, "1", "1", "1", str(close), "1",
             start + index * rf3.DAY_MS + rf3.DAY_MS - 1, "1", 1, "1", "1", "0"]
            for index, close in enumerate(closes)]
    return json.dumps(rows, separators=(",", ":"))


def _receipt(slot, closes, *, requested=None, received=None, url=None, ok=True):
    base = slot + 1000
    receipt = {"ok": ok, "url": rf3.fixed_url(slot) if url is None else url,
               "requested_ms": base if requested is None else requested,
               "received_ms": base + 1000 if received is None else received}
    if ok:
        receipt["raw_utf8"] = _raw(slot, closes)
    return receipt


class _Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def _fetcher(slot, closes, *, calls=None, advance=None, ok=True,
             category="TRANSPORT_ERROR"):
    def fetch(url, clock_arg):
        if calls is not None:
            calls.append(url)
        requested = clock_arg()
        if advance is not None:
            advance()
        received = clock_arg()
        receipt = {"ok": ok, "url": url, "requested_ms": requested, "received_ms": received}
        if ok:
            receipt["raw_utf8"] = _raw(slot, closes)
        else:
            receipt["category"] = category
        return receipt

    return fetch


def _rise():
    return [100 + index for index in range(rf3.WINDOW_DAYS)]


def _case(**override):
    case = {"schema": "QUANTOS_RF2_BOUNDED_CASE_V1", "status": "TECHNICAL_VALIDATION_ONLY",
            "evidence_class": "TECHNICAL_VALIDATION_ONLY", "candidate": "return_sign90",
            "signal": "RETURN_SIGN_90_SAME_WINDOW_NO_HYSTERESIS_V1", "slip_bps": 2,
            "authoritative": False, "authority_admitted": False, "live_eligible": False,
            "paper_admitted": False}
    case.update(override)
    return case


def _write_case(directory, **override):
    path = directory / "rf2-case.json"
    path.write_text(json.dumps(_case(**override)), encoding="utf-8")
    return path


@pytest.fixture
def plan():
    return rf3.make_plan(CREATED, IDENTITY, ANCESTRY)


@pytest.fixture
def state(tmp_path):
    root = tmp_path / "state"
    tool.register(root, _write_case(tmp_path), clock=lambda: CREATED, repo_root=REPO)
    return root


def _slot(root, index=0):
    return json.loads((root / "plan.json").read_text(encoding="utf-8"))["slots"][index]


def _identity(root):
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    return lambda: plan["collector_identity"]


# --- plan contract -----------------------------------------------------------------
def test_make_plan_next_utc_day_and_fourteen_slots(plan):
    created_day = datetime.fromtimestamp(CREATED / 1000, timezone.utc).date()
    start = datetime.fromtimestamp(plan["slots"][0] / 1000, timezone.utc)
    assert start.date() == created_day + timedelta(days=1)
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert len(plan["slots"]) == rf3.SLOT_COUNT == 14
    assert all(b - a == rf3.DAY_MS for a, b in zip(plan["slots"], plan["slots"][1:]))
    assert plan["window_days"] == 90 and plan["deadline_offset_ms"] == 300_000
    assert plan["authority"] is False and plan["order_submission"] is False and plan["pnl"] is False
    assert plan["created_ms"] < plan["slots"][0]


def test_make_plan_rejects_malformed_registration_inputs():
    with pytest.raises(rf3.Rf3Rejection) as bool_error:
        rf3.make_plan(True, IDENTITY, ANCESTRY)
    assert bool_error.value.code == "MALFORMED_INPUT"
    with pytest.raises(rf3.Rf3Rejection):
        rf3.make_plan(CREATED, "", ANCESTRY)
    with pytest.raises(rf3.Rf3Rejection):
        rf3.make_plan(CREATED, IDENTITY, "not-a-digest")


def test_verify_plan_detects_hash_tamper(plan):
    plan["slots"][0] += 1
    with pytest.raises(rf3.Rf3Rejection) as error:
        rf3.verify_plan(plan)
    assert error.value.code == "PLAN_HASH_MISMATCH"


def test_verify_plan_detects_fixed_field_tamper_even_with_recomputed_hash(plan):
    plan["window_days"] = 89
    plan["plan_hash"] = rf3.digest({k: v for k, v in plan.items() if k != "plan_hash"})
    with pytest.raises(rf3.Rf3Rejection) as fixed_error:
        rf3.verify_plan(plan)
    assert fixed_error.value.code == "PLAN_INVALID"
    plan["window_days"] = 90
    plan["extra"] = True
    with pytest.raises(rf3.Rf3Rejection) as key_error:
        rf3.verify_plan(plan)
    assert key_error.value.code == "PLAN_INVALID"


# --- issue_target ------------------------------------------------------------------
def test_issue_target_exact_window_target_and_provenance(plan):
    slot = plan["slots"][0]
    receipt = _receipt(slot, _rise())
    signal = rf3.issue_target(plan, receipt, slot + 2000)
    assert signal["status"] == "SIGNAL_ONLY_NO_ORDER_NO_PNL"
    assert signal["target_weight"] == "0.25"
    assert signal["bar_count"] == 90 and signal["window_days"] == 90
    assert signal["window_open_ms"] == slot - 90 * rf3.DAY_MS
    assert signal["window_close_ms"] == slot - 1
    assert signal["intended_only"] is True and signal["executed_position"] is None
    assert signal["receipt_sha256"] == hashlib.sha256(receipt["raw_utf8"].encode()).hexdigest()
    assert signal["plan_hash"] == plan["plan_hash"] and signal["ancestry_hash"] == ANCESTRY
    assert signal["entry_ms"] == slot + rf3.DEADLINE_MS
    assert signal["exit_ms"] == slot + rf3.DEADLINE_MS + rf3.DAY_MS
    assert signal["authority"] is False and signal["order_submission"] is False
    assert signal["pnl"] is None


def test_issue_target_flat_and_falling_windows(plan):
    slot = plan["slots"][0]
    flat = rf3.issue_target(plan, _receipt(slot, [100] * rf3.WINDOW_DAYS), slot + 3000)
    falling = rf3.issue_target(plan, _receipt(slot, [100 - i for i in range(90)]), slot + 3000)
    assert flat["target_weight"] == "0"
    assert falling["target_weight"] == "-0.25"


def test_issue_target_retains_actual_receipt_timestamps(plan):
    slot = plan["slots"][0]
    receipt = _receipt(slot, _rise(), requested=slot + 1500, received=slot + 2500)
    signal = rf3.issue_target(plan, receipt, slot + 2600)
    assert (signal["requested_ms"], signal["received_ms"], signal["issued_ms"]) == (
        slot + 1500, slot + 2500, slot + 2600)


@pytest.mark.parametrize("issued_offset,requested_offset,received_offset", [
    (rf3.DEADLINE_MS, 1000, 2000),
    (-1, 1000, 2000),
    (3000, -1, 2000),
    (3000, 2000, 1500),
    (3000, 1000, 3500),
])
def test_issue_target_rejects_bad_clocks(plan, issued_offset, requested_offset, received_offset):
    slot = plan["slots"][0]
    receipt = _receipt(slot, _rise(), requested=slot + requested_offset,
                       received=slot + received_offset)
    with pytest.raises(rf3.Rf3Rejection) as error:
        rf3.issue_target(plan, receipt, slot + issued_offset)
    assert error.value.code in ("LATE", "CLOCK_ORDER")


def test_issue_target_rejects_late_publication(plan):
    slot = plan["slots"][0]
    with pytest.raises(rf3.Rf3Rejection) as error:
        rf3.issue_target(plan, _receipt(slot, _rise()), slot + rf3.DEADLINE_MS)
    assert error.value.code == "LATE"


def test_issue_target_rejects_wrong_window_or_symbol(plan):
    slot = plan["slots"][0]
    for url in (rf3.fixed_url(plan["slots"][-1] + rf3.DAY_MS),
                rf3.fixed_url(slot).replace("symbol=BTCUSDC", "symbol=ETHUSDC"),
                rf3.fixed_url(slot) + "&extra=1"):
        with pytest.raises(rf3.Rf3Rejection) as error:
            rf3.issue_target(plan, _receipt(slot, _rise(), url=url), slot + 3000)
        assert error.value.code == "WRONG_WINDOW"


def test_issue_target_rejects_bad_bar_sets(plan):
    slot = plan["slots"][0]
    with pytest.raises(rf3.Rf3Rejection) as missing:
        rf3.issue_target(plan, _receipt(slot, _rise()[:-1]), slot + 3000)
    assert missing.value.code == "MISSING_BAR"

    rows = json.loads(_raw(slot, _rise()))
    rows[5][0] = rows[4][0]
    duplicate = _receipt(slot, _rise())
    duplicate["raw_utf8"] = json.dumps(rows, separators=(",", ":"))
    with pytest.raises(rf3.Rf3Rejection) as misaligned:
        rf3.issue_target(plan, duplicate, slot + 3000)
    assert misaligned.value.code == "BAR_ALIGNMENT"

    rows = json.loads(_raw(slot, _rise()))
    rows[3][6] = rows[3][6] - 1
    bad_close = _receipt(slot, _rise())
    bad_close["raw_utf8"] = json.dumps(rows, separators=(",", ":"))
    with pytest.raises(rf3.Rf3Rejection) as close_error:
        rf3.issue_target(plan, bad_close, slot + 3000)
    assert close_error.value.code == "BAR_ALIGNMENT"

    rows = json.loads(_raw(slot, _rise()))
    rows[4][0] = True
    bool_clock = _receipt(slot, _rise())
    bool_clock["raw_utf8"] = json.dumps(rows, separators=(",", ":"))
    with pytest.raises(rf3.Rf3Rejection) as bool_error:
        rf3.issue_target(plan, bool_clock, slot + 3000)
    assert bool_error.value.code == "MALFORMED_BAR"


@pytest.mark.parametrize("close", ["NaN", "-1", "0", True, None])
def test_issue_target_rejects_bad_closes(plan, close):
    slot = plan["slots"][0]
    rows = json.loads(_raw(slot, _rise()))
    rows[7][4] = close
    receipt = _receipt(slot, _rise())
    receipt["raw_utf8"] = json.dumps(rows, separators=(",", ":"))
    with pytest.raises(rf3.Rf3Rejection) as error:
        rf3.issue_target(plan, receipt, slot + 3000)
    assert error.value.code == "MALFORMED_BAR"


def test_issue_target_rejects_unsupported_receipt_payloads(plan):
    slot = plan["slots"][0]
    with pytest.raises(rf3.Rf3Rejection) as unsupported:
        rf3.issue_target(plan, _receipt(slot, _rise(), ok=False), slot + 3000)
    assert unsupported.value.code == "UNSUPPORTED_RECEIPT"

    raw_cases = {
        "RAW_NOT_JSON": "not json at all",
        "DUPLICATE_KEY": '[{"a": 1, "a": 2}]',
        "NONFINITE_JSON": "[" + ",".join(["[0,0,0,0,NaN,0,0]]" for _ in range(90)]) + "]",
    }
    for code, raw in raw_cases.items():
        receipt = _receipt(slot, _rise())
        receipt["raw_utf8"] = raw
        with pytest.raises(rf3.Rf3Rejection) as error:
            rf3.issue_target(plan, receipt, slot + 3000)
        assert error.value.code == code


# --- registration ------------------------------------------------------------------
def test_register_seals_ancestry_plan_and_existing_root(tmp_path):
    result = _write_case(tmp_path)
    root = tmp_path / "state"
    summary = tool.register(root, result, clock=lambda: CREATED, repo_root=REPO)
    ancestry = json.loads((root / "ancestry.json").read_text(encoding="utf-8"))
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    assert ancestry["sha256"] == hashlib.sha256(result.read_bytes()).hexdigest()
    assert ancestry["candidate"] == "return_sign90" and ancestry["authoritative"] is False
    assert summary["ancestry_hash"] == ancestry["sha256"]
    assert rf3.verify_plan(plan)["created_ms"] == CREATED
    assert plan["ancestry_hash"] == ancestry["sha256"]
    assert plan["collector_identity"] == tool.collector_identity(REPO)
    before = (root / "plan.json").read_bytes()
    with pytest.raises(tool.PathRejection):
        tool.register(root, result, clock=lambda: CREATED, repo_root=REPO)
    assert (root / "plan.json").read_bytes() == before


@pytest.mark.parametrize("override", [
    {"schema": "QUANTOS_OTHER_V1"},
    {"candidate": "log_slope90"},
    {"status": "FAILED"},
    {"authoritative": True},
    {"authority_admitted": True},
    {"live_eligible": True},
    {"signal": "CASH_V1"},
])
def test_register_rejects_non_technical_or_authoritative_ancestry(tmp_path, override):
    result = _write_case(tmp_path, **override)
    with pytest.raises(tool.RegistrationRejection):
        tool.register(tmp_path / "state", result, clock=lambda: CREATED, repo_root=REPO)
    assert not (tmp_path / "state").exists()


def test_register_uses_no_network(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("registration must not perform network access")

    monkeypatch.setattr(tool, "public_fetch", boom)
    root = tmp_path / "state"
    assert tool.register(root, _write_case(tmp_path), clock=lambda: CREATED,
                         repo_root=REPO)["state_root"] == str(root)


# --- source fingerprint ------------------------------------------------------------
def test_source_fingerprint_is_scoped_and_detects_component_drift(tmp_path):
    tree = tmp_path / "repo"
    for relative in tool.SCOPE_FILES:
        target = tree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / relative, target)
    (tree / tool.LOCKFILE).write_bytes((REPO / tool.LOCKFILE).read_bytes())
    baseline = tool.source_fingerprint(tree)
    assert set(baseline["components"]) == set(tool.SCOPE_FILES) | {"python", "lock"}
    (tree / "README.md").write_text("unrelated change\n", encoding="utf-8")
    assert tool.source_fingerprint(tree) == baseline
    (tree / "services/research/rf2_bounded_strategy.py").write_text("# drift\n", encoding="utf-8")
    assert tool.source_fingerprint(tree)["fingerprint"] != baseline["fingerprint"]


# --- single step -------------------------------------------------------------------
def test_once_publishes_one_signal_and_retains_receipt(state):
    slot = _slot(state)
    calls = []
    outcome = tool.run_once(state, clock=_Clock(slot + 1000),
                            fetch=_fetcher(slot, _rise(), calls=calls),
                            identity=_identity(state), repo_root=REPO)
    assert outcome["outcome"] == "PUBLISHED" and len(calls) == 1
    signal = outcome["signal"]
    assert signal["target_weight"] == "0.25"
    assert signal["requested_ms"] == slot + 1000 and signal["received_ms"] == slot + 1000
    assert signal["status"] == "SIGNAL_ONLY_NO_ORDER_NO_PNL"
    saved = json.loads((state / "signal-0.json").read_text(encoding="utf-8"))
    assert saved["receipt_sha256"] == signal["receipt_sha256"]
    attempt = json.loads((state / "attempt-0-1.json").read_text(encoding="utf-8"))
    assert attempt["status"] == "PUBLISHED" and attempt["attempt"] == 1
    assert attempt["raw_utf8"] == _raw(slot, _rise())


def test_once_is_idempotent_without_network(state):
    slot = _slot(state)
    calls = []
    fetch = _fetcher(slot, _rise(), calls=calls)
    clock = _Clock(slot + 1000)
    first = tool.run_once(state, clock=clock, fetch=fetch, identity=_identity(state), repo_root=REPO)
    files = sorted(path.name for path in state.iterdir())
    contents = {path.name: path.read_bytes() for path in state.iterdir()}
    second = tool.run_once(state, clock=clock, fetch=fetch, identity=_identity(state), repo_root=REPO)
    assert second["outcome"] == "ALREADY_ISSUED" and second["signal"] == first["signal"]
    assert len(calls) == 1
    assert sorted(path.name for path in state.iterdir()) == files
    assert {path.name: path.read_bytes() for path in state.iterdir()} == contents


def test_once_outside_window_never_fetches_or_backfills(state):
    slot = _slot(state)
    calls = []
    fetch = _fetcher(slot, _rise(), calls=calls)
    for moment in (CREATED, slot + 6 * 60 * 1000, slot + 20 * 60 * 60 * 1000):
        outcome = tool.run_once(state, clock=_Clock(moment), fetch=fetch,
                                identity=_identity(state), repo_root=REPO)
        assert outcome["outcome"] == "WAITING_OUTSIDE_WINDOW"
    assert calls == []
    assert sorted(path.name for path in state.iterdir()) == ["ancestry.json", "lock", "plan.json"]


def test_once_closed_window_reports_gaps_without_fetching(state):
    slot = _slot(state)
    calls = []
    outcome = tool.run_once(state, clock=_Clock(slot + 20 * rf3.DAY_MS),
                            fetch=_fetcher(slot, _rise(), calls=calls),
                            identity=_identity(state), repo_root=REPO)
    assert outcome["outcome"] == "WINDOW_CLOSED" and outcome["missed"] == list(range(14))
    assert calls == []


def test_once_never_publishes_after_the_deadline(state):
    slot = _slot(state)
    clock = _Clock(slot + 1000)
    calls = []
    fetch = _fetcher(slot, _rise(), calls=calls,
                     advance=lambda: setattr(clock, "value", slot + rf3.DEADLINE_MS))
    outcome = tool.run_once(state, clock=clock, fetch=fetch, identity=_identity(state), repo_root=REPO)
    assert outcome["outcome"] == "LATE" and len(calls) == 1
    assert not (state / "signal-0.json").exists()
    assert json.loads((state / "attempt-0-1.json").read_text(encoding="utf-8"))["status"] == "LATE"


def test_once_budget_is_three_attempts_per_slot(state):
    slot = _slot(state)
    calls = []
    fetch = _fetcher(slot, _rise(), calls=calls, ok=False, category="TRANSPORT_ERROR")
    clock = _Clock(slot + 1000)
    for expected in (1, 2, 3):
        outcome = tool.run_once(state, clock=clock, fetch=fetch,
                                identity=_identity(state), repo_root=REPO)
        assert outcome["outcome"] == "FETCH_FAILED"
        assert len(list(state.glob("attempt-0-*.json"))) == expected
    exhausted = tool.run_once(state, clock=clock, fetch=fetch,
                              identity=_identity(state), repo_root=REPO)
    assert exhausted["outcome"] == "ATTEMPTS_EXHAUSTED" and len(calls) == 3
    assert not (state / "signal-0.json").exists()


def test_once_aborts_on_component_drift_without_fetching(state):
    slot = _slot(state)
    calls = []
    outcome = tool.run_once(state, clock=_Clock(slot + 1000),
                            fetch=_fetcher(slot, _rise(), calls=calls),
                            identity=lambda: "0" * 64, repo_root=REPO)
    assert outcome["outcome"] == "IDENTITY_DRIFT" and calls == []
    attempt = json.loads((state / "attempt-0-1.json").read_text(encoding="utf-8"))
    assert attempt["status"] == "IDENTITY_DRIFT"
    assert not (state / "signal-0.json").exists()


def test_once_detects_plan_tamper(state):
    plan = json.loads((state / "plan.json").read_text(encoding="utf-8"))
    plan["slots"][0] += 1
    (state / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(rf3.Rf3Rejection) as error:
        tool.run_once(state, clock=_Clock(CREATED), fetch=_fetcher(0, _rise()),
                      identity=_identity(state), repo_root=REPO)
    assert error.value.code == "PLAN_HASH_MISMATCH"


def test_once_detects_drift_after_fetch_and_before_publication(state):
    slot = _slot(state)
    real = _identity(state)()
    calls = []
    after_fetch = iter([real, "0" * 64])
    outcome = tool.run_once(state, clock=_Clock(slot + 1000),
                            fetch=_fetcher(slot, _rise(), calls=calls),
                            identity=lambda: next(after_fetch, "0" * 64), repo_root=REPO)
    assert outcome["outcome"] == "IDENTITY_DRIFT" and len(calls) == 1
    attempt = json.loads((state / "attempt-0-1.json").read_text(encoding="utf-8"))
    assert attempt["status"] == "IDENTITY_DRIFT" and attempt["receipt_sha256"]
    assert not (state / "signal-0.json").exists()
    before_publication = iter([real, real, "0" * 64])
    outcome = tool.run_once(state, clock=_Clock(slot + 1000),
                            fetch=_fetcher(slot, _rise(), calls=calls),
                            identity=lambda: next(before_publication, "0" * 64), repo_root=REPO)
    assert outcome["outcome"] == "IDENTITY_DRIFT" and len(calls) == 2
    assert json.loads((state / "attempt-0-2.json").read_text(encoding="utf-8"))["status"] == "IDENTITY_DRIFT"
    assert not (state / "signal-0.json").exists()


def test_public_fetch_bounded_child_and_failure_mapping(monkeypatch):
    captured = {}

    class Completed:
        returncode = 0
        stdout = json.dumps({"ok": True, "payload": [], "raw_utf8": "[]"})

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    clock = _Clock(4000)
    receipt = tool.public_fetch(rf3.fixed_url(0), clock)
    assert captured["argv"][0] == sys.executable
    assert captured["argv"][1:3] == ["-m", "tools.profitability_public_http"]
    assert captured["argv"][4:6] == [str(tool.CHILD_REQUEST_TIMEOUT_S), "--include-raw"]
    assert captured["kwargs"]["timeout"] == tool.FETCH_TIMEOUT_S
    assert captured["kwargs"]["capture_output"] is True
    assert receipt["ok"] is True and receipt["raw_utf8"] == "[]"
    assert receipt["requested_ms"] == 4000 and receipt["received_ms"] == 4000

    Completed.returncode = 1
    assert tool.public_fetch(rf3.fixed_url(0), clock)["category"] == "SUBPROCESS_FAILED"
    Completed.returncode = 0
    Completed.stdout = "not json"
    assert tool.public_fetch(rf3.fixed_url(0), clock)["category"] == "INVALID_SUBPROCESS_JSON"
    Completed.stdout = json.dumps({"ok": False, "category": "TIMEOUT"})
    assert tool.public_fetch(rf3.fixed_url(0), clock)["category"] == "TIMEOUT"


def test_public_fetch_timeout_maps_to_timeout(monkeypatch):
    def fake_run(argv, **kwargs):
        raise tool.subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    receipt = tool.public_fetch(rf3.fixed_url(0), _Clock(4000))
    assert receipt["ok"] is False and receipt["category"] == "TIMEOUT"



def test_public_fetch_refuses_unregistered_urls(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("an unregistered URL must never reach the child process")

    monkeypatch.setattr(tool.subprocess, "run", boom)
    receipt = tool.public_fetch("https://example.com/klines", _Clock(4000))
    assert receipt["ok"] is False and receipt["category"] == "UNREGISTERED_URL"
    assert receipt["requested_ms"] == 4000 and receipt["received_ms"] == 4000


# --- status and CLI ----------------------------------------------------------------
def test_status_reports_gaps_and_remaining_attempts_without_pnl(state, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("status must not perform network access")

    monkeypatch.setattr(tool, "public_fetch", boom)
    slot = _slot(state)
    report = tool.status(state, clock=_Clock(slot + 6 * 60 * 1000), repo_root=REPO)
    assert report["pnl"] is None and report["authority"] is False
    assert report["gaps"] == [0] and report["published"] == []
    assert report["slots"][0]["remaining_attempts"] == 3
    assert report["identity_matches"] is True
    assert json.dumps(report, allow_nan=False)
    assert sorted(path.name for path in state.iterdir()) == ["ancestry.json", "lock", "plan.json"]


def test_cli_status_register_and_no_clock_override(tmp_path, capsys):
    result = _write_case(tmp_path)
    root = tmp_path / "state"
    assert tool.main(["--state-root", str(root), "--register",
                      "--research-result", str(result)]) == 0
    assert json.loads(capsys.readouterr().out)["ancestry_hash"]
    assert tool.main(["--state-root", str(root), "--status"]) == 0
    assert json.loads(capsys.readouterr().out)["pnl"] is None
    for argv in (["--state-root", str(root), "--once", "--now", "1"],
                 ["--state-root", str(root), "--once", "--created-ms", "1"],
                 ["--state-root", str(root), "--register"],
                 ["--state-root", str(root), "--once", "--research-result", str(result)]):
        with pytest.raises(SystemExit):
            tool.main(argv)


# --- path safety -------------------------------------------------------------------
def test_paths_must_be_absolute(tmp_path):
    with pytest.raises(tool.PathRejection):
        tool.run_once("relative/state", identity=lambda: IDENTITY, repo_root=REPO)
    with pytest.raises(tool.PathRejection):
        tool.register(tmp_path / "state", "relative/case.json", clock=lambda: CREATED)


def test_symlinked_state_root_is_rejected(tmp_path, state):
    link = tmp_path / "link"
    link.symlink_to(state, target_is_directory=True)
    with pytest.raises(tool.PathRejection):
        tool.run_once(link, clock=_Clock(CREATED), identity=_identity(state), repo_root=REPO)
    chained = tmp_path / "chained"
    chained.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(tool.PathRejection):
        tool.register(chained / "state", _write_case(tmp_path), clock=lambda: CREATED)


def test_state_root_must_be_an_rf3_state_directory(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(tool.PathRejection):
        tool.run_once(empty, identity=lambda: IDENTITY, repo_root=REPO)
    with pytest.raises(tool.PathRejection):
        tool.status(empty, repo_root=REPO)


def test_forbidden_state_root_prefixes_are_rejected(tmp_path):
    case = tmp_path / "case.json"
    case.write_text("{}", encoding="utf-8")
    for forbidden in ("/srv/quantos-rf3-test", "/opt/quantos-rf3-test"):
        with pytest.raises(tool.PathRejection):
            tool.register(forbidden, case, clock=lambda: CREATED)
        with pytest.raises(tool.PathRejection):
            tool.run_once(forbidden, identity=lambda: IDENTITY, repo_root=REPO)
    assert not Path("/srv/quantos-rf3-test").exists()
    assert not Path("/opt/quantos-rf3-test").exists()
