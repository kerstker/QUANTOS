"""Rank #10a — crowding gate hardening: max-single-name concentration criterion (the
dilution fix from the external review's P0) + optional ADV/liquidity dimension. The
gross-weighted average alone lets a few extremely-crowded names hide behind many low-
crowding ones; a concentrated AND crowded name must trip the gate independently."""
import pytest

from services.portfolio.crowding_gate import evaluate_crowding_gate


def test_concentrated_crowded_name_blocks_despite_diluted_average():
    # One concentrated (30% of gross) name at extreme crowding, rest low-crowding.
    # Gross-weighted average = 0.30*0.95 + 0.70*0.15 = 0.39 -> old logic PASSES (< warn 0.45);
    # the concentration criterion BLOCKS on the single crowded 30% name.
    weights = {"CROWDED": 0.30, "A": 0.18, "B": 0.18, "C": 0.17, "D": 0.17}
    crowding = {"CROWDED": 0.95, "A": 0.15, "B": 0.15, "C": 0.15, "D": 0.15}
    decision = evaluate_crowding_gate(weights, crowding, threshold=0.60, warn_threshold=0.45)
    assert decision.status == "BLOCK"
    assert any("concentrated_crowded_block:CROWDED" in r for r in decision.reasons)
    # sanity: the diluted average really is in PASS range (proves the average alone would miss it)
    assert decision.score == pytest.approx(0.39, abs=1e-6)


def test_average_only_book_is_unaffected_when_no_name_is_concentrated_and_crowded():
    # every name < max_name_weight (0.25): the concentration criterion never fires,
    # so a low-average book still PASSes with the unchanged reason tuple.
    weights = {f"S{i}": 0.10 for i in range(10)}
    crowding = {f"S{i}": 0.20 for i in range(10)}
    decision = evaluate_crowding_gate(weights, crowding)
    assert decision.status == "PASS"
    assert decision.reasons == ("crowding_score_ok",)


def test_concentrated_but_not_crowded_does_not_trip():
    weights = {"BIG": 0.60, "S": 0.40}
    crowding = {"BIG": 0.10, "S": 0.10}          # concentrated but low crowding -> PASS
    decision = evaluate_crowding_gate(weights, crowding)
    assert decision.status == "PASS"


def test_concentrated_crowded_in_warn_band_warns():
    weights = {"BIG": 0.60, "S": 0.40}
    crowding = {"BIG": 0.50, "S": 0.05}          # BIG concentrated, crowding in (warn, threshold]
    decision = evaluate_crowding_gate(weights, crowding, threshold=0.60, warn_threshold=0.45)
    assert decision.status == "WARN"
    assert any("concentrated_crowded_warn:BIG" in r for r in decision.reasons)


def test_concentration_threshold_is_configurable():
    weights = {"BIG": 0.30, "A": 0.40, "B": 0.30}
    crowding = {"BIG": 0.95, "A": 0.10, "B": 0.10}
    # with a high max_name_weight, BIG (0.30) is NOT concentrated -> only the (low) average applies
    decision = evaluate_crowding_gate(weights, crowding, max_name_weight=0.50)
    assert decision.status == "PASS"


# ---- optional ADV / liquidity dimension ----

def test_illiquid_position_warns_on_exit_days():
    weights = {"A": 0.5, "B": 0.5}
    crowding = {"A": 0.10, "B": 0.10}            # crowding fine -> base PASS
    # A: position 0.5*$10M = $5M; ADV $0.5M -> exit 10 days > max 5 -> WARN
    decision = evaluate_crowding_gate(
        weights, crowding, adv_by_symbol={"A": 0.5e6, "B": 100e6}, book_notional=10e6, max_exit_days=5.0)
    assert decision.status == "WARN"
    assert any("exit_days:A" in r for r in decision.reasons)


def test_liquid_book_stays_pass_with_adv_supplied():
    weights = {"A": 0.5, "B": 0.5}
    crowding = {"A": 0.10, "B": 0.10}
    decision = evaluate_crowding_gate(
        weights, crowding, adv_by_symbol={"A": 100e6, "B": 100e6}, book_notional=10e6, max_exit_days=5.0)
    assert decision.status == "PASS"
    assert decision.reasons == ("crowding_score_ok",)


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_adv_for_covered_name_fails_closed(bad):
    decision = evaluate_crowding_gate(
        {"A": 1.0}, {"A": 0.10}, adv_by_symbol={"A": bad}, book_notional=10e6)
    assert decision.status == "BLOCK"
    assert any("invalid_adv:A" in r for r in decision.reasons)


# ---- Rank #10 wiring helper: aggregate per-period decisions for run detail ----

def test_summarize_gate_decisions_counts_and_worst_score():
    from services.portfolio.crowding_gate import summarize_gate_decisions
    ds = [
        evaluate_crowding_gate({"A": 0.5, "B": -0.5}, {"A": 0.10, "B": 0.10}),   # PASS
        evaluate_crowding_gate({"A": 0.5, "B": -0.5}, {"A": 0.50, "B": 0.54}),   # WARN ~0.52
        evaluate_crowding_gate({"A": 0.6, "B": -0.4}, {"A": 0.70, "B": 0.80}),   # BLOCK ~0.74
    ]
    s = summarize_gate_decisions(ds)
    assert s["n_periods"] == 3 and s["n_pass"] == 1 and s["n_warn"] == 1 and s["n_block"] == 1
    assert s["worst_score"] == pytest.approx(0.74, abs=1e-6)
    assert s["provenance"] == "REAL_CROWDING_GATE"
    assert s["sample_reasons"]                                # WARN/BLOCK reasons collected


def test_summarize_empty_is_zeroed():
    from services.portfolio.crowding_gate import summarize_gate_decisions
    s = summarize_gate_decisions([])
    assert s["n_periods"] == 0 and s["worst_score"] is None and s["sample_reasons"] == []
