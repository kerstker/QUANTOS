"""RF3 daily-target to V2 account adapter regressions; all receipts and clocks synthetic.

No public data, no network, no registration run and no real forward window: the
plan, the fourteen published slot bundles and the completed market are built
here, and the canonical replay is the real one.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path

import numpy as np
import pytest

from services.research import rf3_daily_forward as rf3
from services.research import rf3_forward_account as account

MINUTE = 60_000
CREATED_MS = 1_735_689_600_000
IDENTITY = "c" * 64
ANCESTRY = "a" * 64
# Day-zero 100, a gentle 14-day oscillation: small enough never to trip the
# 1350 loss budget, large enough to move marked equity every day.
ANCHORS = (100.0, 101.0, 102.0, 101.0, 100.0, 99.0, 100.0, 101.0, 102.0, 103.0,
           104.0, 103.0, 102.0, 101.0, 102.0)
# Slot index % 3 selects a rising, falling or flat 90-close window.
WINDOWS = ((100.0, 110.0), (110.0, 100.0), (100.0, 100.0))


def _raw(slot_ms, first, last):
    """Ninety consecutive closed UTC daily bars; only the first/last close set the sign."""
    first_open = slot_ms - rf3.WINDOW_DAYS * rf3.DAY_MS
    rows = []
    for index in range(rf3.WINDOW_DAYS):
        open_ms = first_open + index * rf3.DAY_MS
        close = first + (last - first) * index / (rf3.WINDOW_DAYS - 1)
        rows.append([open_ms, close, close, close, close, 1.0,
                     open_ms + rf3.DAY_MS - 1, 1.0])
    return json.dumps(rows, separators=(",", ":"))


def _bundle(plan, index):
    """One slot's persisted artifacts exactly as the RF3 observer writes them."""
    slot_ms = plan["slots"][index]
    receipt = {"ok": True, "url": rf3.fixed_url(slot_ms), "requested_ms": slot_ms + 1000,
               "received_ms": slot_ms + 2000, "raw_utf8": _raw(slot_ms, *WINDOWS[index % 3])}
    issued_ms = slot_ms + 30_000
    signal = rf3.issue_target(plan, receipt, issued_ms)
    recorded_ms = issued_ms + 100
    saved = {**signal, "attempt": 1, "recorded_ms": recorded_ms}
    return {
        "signal": saved,
        "attempt": {"schema": "QUANTOS_RF3_ATTEMPT_V1", "slot_index": index, "slot_ms": slot_ms,
                    "attempt": 1, "status": "PUBLISHED", "reason": None, "recorded_ms": recorded_ms,
                    "url": receipt["url"], "requested_ms": receipt["requested_ms"],
                    "received_ms": receipt["received_ms"],
                    "receipt_sha256": signal["receipt_sha256"], "raw_utf8": receipt["raw_utf8"]},
        "publication": {"status": "PUBLISHED", "completed_ms": recorded_ms + 10,
                        "signal_hash": rf3.digest(saved)},
    }


def _reseal(bundle):
    """Recompute the publication hash after a deliberate artifact mutation."""
    bundle["publication"]["signal_hash"] = rf3.digest(bundle["signal"])
    return bundle


def _market(plan):
    """A complete one-minute captured window between the plan's two rebalances."""
    start = plan["slots"][0] + rf3.DEADLINE_MS
    end = start + rf3.SLOT_COUNT * rf3.DAY_MS
    times = np.arange(start, end + MINUTE, MINUTE, dtype=np.int64)
    prices = np.empty(len(times), dtype=float)
    last = len(ANCHORS) - 1
    for index, at in enumerate(times):
        offset = int(at) - start
        day, fraction = offset // rf3.DAY_MS, (offset % rf3.DAY_MS) / rf3.DAY_MS
        prices[index] = ANCHORS[day] + (ANCHORS[min(day + 1, last)] - ANCHORS[day]) * fraction
    funding = [{"time": start + k * 480 * MINUTE, "rate": D("0.0001"),
                "mark": D(str(prices[k * 480]))}
               for k in range(1, (end - start) // (480 * MINUTE))]
    return {"times": times, "prices": prices, "marks": prices.copy(), "funding": funding,
            "start": start, "end": end, "maximum_quantity": D("50"),
            "daily_volume": {day: D("10000000")
                             for day in range(start // rf3.DAY_MS, end // rf3.DAY_MS + 2)}}


@pytest.fixture(scope="module")
def base():
    plan = rf3.make_plan(CREATED_MS, IDENTITY, ANCESTRY)
    return plan, [_bundle(plan, index) for index in range(rf3.SLOT_COUNT)], _market(plan)


@pytest.fixture
def observation(base):
    plan, bundles, market = base
    return copy.deepcopy(plan), copy.deepcopy(bundles), copy.deepcopy(market)


def _evaluate(plan, bundles, market, evaluated_ms=None):
    return account.evaluate(plan, bundles, market, evaluated_ms=(
        market["end"] + account.MATURE_OFFSET_MS + 1 if evaluated_ms is None else evaluated_ms))


def _code(plan, bundles, market, evaluated_ms=None):
    with pytest.raises(rf3.Rf3Rejection) as error:
        _evaluate(plan, bundles, market, evaluated_ms)
    return error.value


def _case(result, name, slippage):
    return next(c for c in result["cases"]
                if c["account"] == name and c["slippage_bps"] == slippage)


# --- the real calculation ---------------------------------------------------------
def test_all_nine_cases_replay_and_attribute_without_authority(observation):
    plan, bundles, market = observation
    result = _evaluate(plan, bundles, market)
    assert result["schema"] == "QUANTOS_RF3_FORWARD_ACCOUNT_V1"
    assert result["status"] == account.STATUS == "TECHNICAL_VALIDATION_ONLY"
    assert result["plan_hash"] == plan["plan_hash"]
    assert result["start_ms"] == plan["slots"][0] + rf3.DEADLINE_MS
    assert result["end_ms"] == result["start_ms"] + rf3.SLOT_COUNT * rf3.DAY_MS
    assert result["mature_at_ms"] == result["end_ms"] + account.MATURE_OFFSET_MS
    assert result["slot_count"] == rf3.SLOT_COUNT == 14
    assert len(result["cases"]) == 9
    assert {(c["account"], c["slippage_bps"]) for c in result["cases"]} == {
        (name, slip) for name in ("CASH", "PASSIVE_25", "FROZEN_TREND")
        for slip in account.SLIPPAGE_BPS}
    assert {c["fee_bps"] for c in result["cases"]} == {4}
    for case in result["cases"]:
        assert case["attribution"]["status"] == "RECONCILED_TECHNICAL_ONLY"
        assert case["attribution"]["reason"] is None
        assert case["attribution"]["economic_eligible"] is False
        assert case["attribution"]["actual_venue_fees"] is None
        assert case["receipt"]["reconciliation"]["residual"] == "0"
        assert case["receipt"]["cash_model"] == "CANONICAL_LINEAR_USDC_PERPETUAL"
        assert case["receipt"]["authoritative"] is False
        assert case["receipt"]["live_eligible"] is False
    assert result["economic_eligible"] is False
    assert result["authority"] is False
    assert result["order_submission"] is False
    assert result["pnl"] is None
    assert "FINAL_EQUITY_IS_MARKED_TO_MARKET_AND_MAY_INCLUDE_AN_OPEN_POSITION" in result["limitations"]
    assert "PUBLICATION_CLOCKS_ARE_UNSIGNED_LOCAL_TIMES_WITHOUT_TIME_PROOF" in result["limitations"]


def test_frozen_trend_takes_long_and_short_positions_pays_fees_and_realizes_pnl(observation):
    plan, bundles, market = observation
    result = _evaluate(plan, bundles, market)
    case = _case(result, "FROZEN_TREND", 10)
    receipt, report = case["receipt"], case["attribution"]
    # The fourteen saved targets are the fixed 90-close signs, recomputed from raw receipts.
    assert [d["target"] for d in receipt["decisions"]] == [
        float(b["signal"]["target_weight"]) for b in bundles]
    assert {d["target"] for d in receipt["decisions"]} == {0.25, 0.0, -0.25}
    assert len(receipt["fills"]) == 14
    assert receipt["halted_at"] is None
    assert D(report["position_contribution"]["long"]) > 0
    assert D(report["position_contribution"]["short"]) < 0
    assert D(report["fees"]) > 0 and D(report["embedded_slippage"]) > 0
    assert D(report["funding"]) != 0
    assert D(report["net_pnl"]) != 0
    assert D(report["net_pnl"]) == D(receipt["reconciliation"]["ending_equity"]) - D(
        receipt["reconciliation"]["initial_equity"])
    for fill in receipt["fills"]:
        charged = abs(D(fill["quantity"])) * D(fill["price"]) * D("4") / D("10000")
        assert abs(D(fill["fee"]) - charged) <= D("1e-18")


def test_slippage_scenarios_change_the_realized_cost_of_the_same_targets(observation):
    plan, bundles, market = observation
    result = _evaluate(plan, bundles, market)
    net = {slip: D(_case(result, "FROZEN_TREND", slip)["attribution"]["net_pnl"])
           for slip in account.SLIPPAGE_BPS}
    assert net[10] < net[2]
    assert len({str(value) for value in net.values()}) == 2
    assert _case(result, "CASH", 2)["attribution"]["net_pnl"] == "0"


def test_cash_account_never_trades_and_keeps_the_canonical_initial_equity(observation):
    plan, bundles, market = observation
    result = _evaluate(plan, bundles, market)
    for slip in account.SLIPPAGE_BPS:
        receipt = _case(result, "CASH", slip)["receipt"]
        assert receipt["fills"] == [] and receipt["orders"] == []
        assert receipt["reconciliation"]["ending_equity"] == "1500"
        assert _case(result, "CASH", slip)["attribution"]["diagnosis"] == "NO_TRADING_EVIDENCE"


def test_account_is_continuous_and_never_reset_to_1500_each_day(observation):
    plan, bundles, market = observation
    result = _evaluate(plan, bundles, market)
    receipt = _case(result, "PASSIVE_25", 5)["receipt"]
    daily = receipt["daily"]
    assert len(daily) == rf3.SLOT_COUNT + 1
    assert [row["time"] for row in daily] == [result["start_ms"] + index * rf3.DAY_MS
                                              for index in range(rf3.SLOT_COUNT + 1)]
    assert daily[0]["equity"] == "1500" and daily[0]["quantity"] == "0"
    # In this fixture small rebalance deltas fall below minimum notional, so the
    # initial position persists. Daily decisions must not reset the account.
    assert {row["quantity"] for row in daily[1:]} == {"3.75"}
    assert all(D(row["equity"]) != D("1500") for row in daily[1:])
    assert receipt["reconciliation"]["initial_equity"] == "1500"
    assert D(_case(result, "PASSIVE_25", 5)["attribution"]["position_contribution"]["short"]) == 0
    assert D(_case(result, "PASSIVE_25", 5)["attribution"]["position_contribution"]["long"]) > 0


def test_artifact_digest_binds_the_exact_bundle_list(observation):
    plan, bundles, market = observation
    result = _evaluate(plan, bundles, market)
    assert result["artifacts_digest"] == rf3.digest({"plan_hash": plan["plan_hash"],
                                                     "artifacts": bundles})
    assert len(result["artifacts_digest"]) == 64
    changed = copy.deepcopy(bundles)
    changed[-1]["signal"]["issued_ms"] += 1
    assert result["artifacts_digest"] != rf3.digest({"plan_hash": plan["plan_hash"],
                                                     "artifacts": changed})


def test_evaluate_does_not_mutate_its_inputs(observation):
    plan, bundles, market = observation
    bundles_before = copy.deepcopy(bundles)
    market_before = copy.deepcopy(market)
    before = copy.deepcopy(plan)
    _evaluate(plan, bundles, market)
    assert plan == before and bundles == bundles_before
    assert set(market) == set(market_before)
    for key, value in market_before.items():
        if isinstance(value, np.ndarray):
            assert np.array_equal(market[key], value)
        else:
            assert market[key] == value


# --- artifact and slot binding ----------------------------------------------------
def test_missing_slot_bundle_is_rejected(observation):
    plan, bundles, market = observation
    assert _code(plan, bundles[:-1], market).code == "BUNDLES_INCOMPLETE"
    assert _code(plan, bundles + [bundles[0]], market).code == "BUNDLES_INCOMPLETE"


def test_duplicate_or_reordered_slots_are_rejected(observation):
    plan, bundles, market = observation
    duplicated = copy.deepcopy(bundles)
    duplicated[1] = copy.deepcopy(duplicated[0])
    assert _code(plan, duplicated, market).code == "SLOT_MISMATCH"
    swapped = copy.deepcopy(bundles)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    assert _code(plan, swapped, market).code == "SLOT_MISMATCH"
    forged = copy.deepcopy(bundles)
    moved = forged[1]
    moved["attempt"]["slot_index"] = 0
    moved["attempt"]["slot_ms"] = plan["slots"][0]
    forged[0] = moved
    assert _code(plan, forged, market).code == "SLOT_REORDERED"


def test_fabricated_target_or_injected_signal_field_is_rejected(observation):
    plan, bundles, market = observation
    fabricated = copy.deepcopy(bundles)
    fabricated[0]["signal"]["target_weight"] = "-0.25"
    _reseal(fabricated[0])
    assert _code(plan, fabricated, market).code == "SIGNAL_MISMATCH"
    injected = copy.deepcopy(bundles)
    injected[0]["signal"]["authority"] = True
    _reseal(injected[0])
    assert _code(plan, injected, market).code == "SIGNAL_MISMATCH"
    extra = copy.deepcopy(bundles)
    extra[0]["signal"]["override_weight"] = 1.0
    _reseal(extra[0])
    assert _code(plan, extra, market).code == "SIGNAL_MISMATCH"
    # A numeric flag or a float weight must not pass for the persisted JSON type.
    numeric = copy.deepcopy(bundles)
    numeric[0]["signal"]["authority"] = 0
    _reseal(numeric[0])
    assert _code(plan, numeric, market).code == "SIGNAL_MISMATCH"
    drifted = copy.deepcopy(bundles)
    drifted[0]["signal"]["target_weight"] = 0.25
    _reseal(drifted[0])
    assert _code(plan, drifted, market).code == "SIGNAL_MISMATCH"
    shaped = copy.deepcopy(bundles)
    shaped[0]["decision"] = lambda *_: 0.0
    assert _code(plan, shaped, market).code == "BUNDLE_INVALID"


def test_corrupt_raw_receipt_or_hash_is_rejected(observation):
    plan, bundles, market = observation
    tampered = copy.deepcopy(bundles)
    rows = json.loads(tampered[0]["attempt"]["raw_utf8"])
    rows[4][4] = 123.0
    tampered[0]["attempt"]["raw_utf8"] = json.dumps(rows, separators=(",", ":"))
    assert _code(plan, tampered, market).code == "RECEIPT_MISMATCH"
    forged = copy.deepcopy(bundles)
    rows = json.loads(forged[0]["attempt"]["raw_utf8"])
    rows[0][4] = 99.0
    raw = json.dumps(rows, separators=(",", ":"))
    forged[0]["attempt"]["raw_utf8"] = raw
    forged[0]["attempt"]["receipt_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    _reseal(forged[0])
    assert _code(plan, forged, market).code == "SIGNAL_MISMATCH"
    hashed = copy.deepcopy(bundles)
    hashed[0]["attempt"]["receipt_sha256"] = "0" * 64
    assert _code(plan, hashed, market).code == "RECEIPT_MISMATCH"


def test_corrupt_publication_receipt_is_rejected(observation):
    plan, bundles, market = observation
    unsealed = copy.deepcopy(bundles)
    unsealed[0]["publication"]["signal_hash"] = "0" * 64
    assert _code(plan, unsealed, market).code == "PUBLICATION_HASH"
    late = copy.deepcopy(bundles)
    late[0]["publication"]["completed_ms"] = late[0]["signal"]["entry_ms"]
    assert _code(plan, late, market).code == "PUBLICATION_CLOCK"
    early = copy.deepcopy(bundles)
    early[0]["publication"]["completed_ms"] = early[0]["signal"]["recorded_ms"] - 1
    assert _code(plan, early, market).code == "PUBLICATION_CLOCK"
    unfinal = copy.deepcopy(bundles)
    unfinal[0]["publication"]["status"] = "LATE"
    assert _code(plan, unfinal, market).code == "PUBLICATION_STATUS"


def test_attempt_record_must_agree_with_its_signal(observation):
    plan, bundles, market = observation
    stale = copy.deepcopy(bundles)
    stale[0]["attempt"]["recorded_ms"] += 1
    assert _code(plan, stale, market).code == "RECORDED_CLOCK"
    mismatched = copy.deepcopy(bundles)
    mismatched[0]["attempt"]["attempt"] = 2
    assert _code(plan, mismatched, market).code == "ATTEMPT_INVALID"
    out_of_range = copy.deepcopy(bundles)
    out_of_range[0]["signal"]["attempt"] = 4
    out_of_range[0]["attempt"]["attempt"] = 4
    _reseal(out_of_range[0])
    assert _code(plan, out_of_range, market).code == "ATTEMPT_INVALID"
    unpublished = copy.deepcopy(bundles)
    unpublished[0]["attempt"]["status"] = "REJECTED"
    assert _code(plan, unpublished, market).code == "ATTEMPT_STATUS"
    boolean = copy.deepcopy(bundles)
    boolean[0]["attempt"]["attempt"] = True
    assert _code(plan, boolean, market).code == "ATTEMPT_INVALID"
    float_slot = copy.deepcopy(bundles)
    float_slot[0]["attempt"]["slot_index"] = float(0)
    assert _code(plan, float_slot, market).code == "SLOT_MISMATCH"
    float_ms = copy.deepcopy(bundles)
    float_ms[0]["attempt"]["slot_ms"] = float(plan["slots"][0])
    assert _code(plan, float_ms, market).code == "SLOT_MISMATCH"
    float_record = copy.deepcopy(bundles)
    float_record[0]["attempt"]["recorded_ms"] = float(float_record[0]["signal"]["recorded_ms"])
    assert _code(plan, float_record, market).code == "RECORDED_CLOCK"


def test_deadline_and_boolean_clocks_are_rejected(observation):
    plan, bundles, market = observation
    slot_ms = plan["slots"][0]
    late = copy.deepcopy(bundles)
    late[0]["signal"]["issued_ms"] = slot_ms + rf3.DEADLINE_MS
    late[0]["signal"]["recorded_ms"] = slot_ms + rf3.DEADLINE_MS + 100
    late[0]["attempt"]["recorded_ms"] = slot_ms + rf3.DEADLINE_MS + 100
    late[0]["publication"]["completed_ms"] = slot_ms + rf3.DEADLINE_MS + 110
    _reseal(late[0])
    assert _code(plan, late, market).code == "LATE"
    boolean = copy.deepcopy(bundles)
    boolean[0]["signal"]["issued_ms"] = True
    assert _code(plan, boolean, market).code == "CLOCK_INVALID"
    float_clock = copy.deepcopy(bundles)
    float_clock[0]["attempt"]["requested_ms"] = float(slot_ms + 1000)
    assert _code(plan, float_clock, market).code == "CLOCK_INVALID"


def test_no_replay_starts_before_every_slot_verifies(observation, monkeypatch):
    plan, bundles, market = observation

    def boom(*args, **kwargs):
        raise AssertionError("replay must not start for unverified artifacts")

    monkeypatch.setattr(account, "replay", boom)
    bundles[9]["publication"]["signal_hash"] = "0" * 64
    assert _code(plan, bundles, market).code == "PUBLICATION_HASH"


def test_plan_tamper_is_rejected(observation):
    plan, bundles, market = observation
    plan["slots"][0] += 1
    assert _code(plan, bundles, market).code == "PLAN_HASH_MISMATCH"


# --- clock, market and parameter surface -------------------------------------------
def test_evaluation_before_the_closing_minute_is_immature(observation):
    plan, bundles, market = observation
    for offset in (0, account.MATURE_OFFSET_MS - 1, account.MATURE_OFFSET_MS):
        assert _code(plan, bundles, market,
                     market["end"] + offset).code == "IMMATURE"
    assert _evaluate(plan, bundles, market,
                     market["end"] + account.MATURE_OFFSET_MS + 1)["evaluated_ms"] == (
        market["end"] + account.MATURE_OFFSET_MS + 1)
    assert _code(plan, bundles, market, True).code == "CLOCK_INVALID"


def test_market_bounds_must_equal_the_plan_window(observation):
    plan, bundles, market = observation
    shifted = copy.deepcopy(market)
    shifted["end"] += MINUTE
    assert _code(plan, bundles, shifted).code == "MARKET_BOUNDS"
    shifted = copy.deepcopy(market)
    shifted["start"] -= MINUTE
    assert _code(plan, bundles, shifted).code == "MARKET_BOUNDS"
    shifted = copy.deepcopy(market)
    shifted["start"] = float(shifted["start"])
    assert _code(plan, bundles, shifted).code == "MARKET_INVALID"


def test_market_must_carry_only_the_captured_usdc_surface(observation):
    plan, bundles, market = observation
    assert _evaluate(plan, bundles, {**market, "quote": "USDC"})["status"] == account.STATUS
    assert _code(plan, bundles, {**market, "quote": "USDT"}).code == "MARKET_INVALID"
    assert _code(plan, bundles, {**market, "stop": False}).code == "MARKET_INVALID"
    assert _code(plan, bundles, {k: v for k, v in market.items()
                                 if k != "daily_volume"}).code == "MARKET_INVALID"
    assert _code(plan, bundles, {**market, "maximum_quantity": 50}).code == "MARKET_INVALID"
    assert _code(plan, bundles, {**market, "maximum_quantity": D("1")}).code == "MARKET_INVALID"


def test_canonical_engine_still_validates_the_arrays(observation):
    plan, bundles, market = observation
    broken = copy.deepcopy(market)
    broken["times"] = np.delete(broken["times"], 20)
    with pytest.raises(ValueError):
        _evaluate(plan, bundles, broken)


@pytest.mark.parametrize("override", [
    {"slip_bps": 1}, {"taker_fee_bps": D("0")}, {"stop": False},
    {"decision": lambda *_: 0.0}, {"context_decision": lambda *_: None},
    {"decision_interval_ms": MINUTE}, {"maximum_quantity": D("1")},
    {"target_weight": D("1")}, {"max_position_weight": D("1")},
    {"target_policy": "SLEEVE_NET_V1"}, {"initial_equity": D("1e9")},
])
def test_replay_parameters_can_never_be_overridden_by_the_caller(observation, override):
    plan, bundles, market = observation
    with pytest.raises(TypeError):
        account.evaluate(plan, bundles, market, evaluated_ms=market["end"] + MINUTE, **override)


def test_unknown_attribution_is_a_rejection_not_a_profit(monkeypatch, observation):
    plan, bundles, market = observation
    monkeypatch.setattr(account, "attribute_replay", lambda receipt: {
        "status": "UNKNOWN", "reason": "INCOMPLETE_REPLAY_RECEIPTS", "net_pnl": None,
        "gross_reference_pnl": None, "economic_eligible": False})
    error = _code(plan, bundles, market)
    assert error.code == "ATTRIBUTION_UNKNOWN"
    assert "INCOMPLETE_REPLAY_RECEIPTS" in error.detail


def test_adapter_is_offline_and_reuses_the_single_canonical_engine():
    source = Path(account.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules, bound = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            modules.add(node.module)
            bound.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    assert modules == {"__future__", "decimal", "services.research",
                       "services.research.perpetual_alpha",
                       "services.research.profitability_attribution"}
    # The single canonical replay and its attribution, plus the frozen RF3 contract.
    assert bound == {"__future__.annotations", "decimal.Decimal",
                     "services.research.rf3_daily_forward",
                     "services.research.perpetual_alpha.replay",
                     "services.research.profitability_attribution.attribute_replay"}
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not names & {"open", "eval", "exec", "compile", "__import__", "input", "print"}
