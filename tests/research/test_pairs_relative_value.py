import pytest

from services.research.alpha_expression import FieldPanel
from services.research.pairs_relative_value import (
    PairExperimentConfig,
    build_cycle_plan,
    build_pair_periods,
    pair_signal_leakage_ok,
    select_distance_pairs,
    select_hash_pairs,
)


def _panel(rows=9):
    instruments = ("A", "B", "C", "D")
    a = [10.0 + i for i in range(rows)]
    b = [20.0 + 2.0 * i + (0.1 if i == 3 else 0.0) for i in range(rows)]
    c = [30.0 - 0.3 * i for i in range(rows)]
    d = [60.0 - 0.6 * i + (0.2 if i == 2 else 0.0) for i in range(rows)]
    close = [[a[i], b[i], c[i], d[i]] for i in range(rows)]
    volume = [[1000.0, 900.0, 800.0, 700.0] for _ in range(rows)]
    return FieldPanel(instruments, tuple(range(rows)), {"close": close, "volume": volume})


CFG = PairExperimentConfig(
    formation_rows=4,
    trade_rows=2,
    universe_size=4,
    pair_count=2,
)


def test_distance_selection_is_disjoint_and_uses_normalized_paths():
    paths = {
        "A": (1.0, 1.1, 1.2),
        "B": (1.0, 1.1, 1.201),
        "C": (1.0, 0.9, 0.8),
        "D": (1.0, 0.9, 0.799),
    }
    assert set(select_distance_pairs(paths, 2)) == {("A", "B"), ("C", "D")}


def test_hash_control_is_deterministic_and_disjoint():
    first = select_hash_pairs(("A", "B", "C", "D"), 2)
    second = select_hash_pairs(("D", "C", "B", "A"), 2)
    assert first == second
    assert len({name for pair in first for name in pair}) == 4


def test_cycle_plan_is_formation_only_and_future_invariant():
    panel = _panel()
    base = build_cycle_plan(panel, 0, CFG, selection="distance")
    changed = _panel()
    changed.matrices["close"][5][0] *= 100.0
    changed.matrices["volume"][6][1] *= 100.0
    assert build_cycle_plan(changed, 0, CFG, selection="distance") == base
    assert set(base.pairs) == {("A", "B"), ("C", "D")}


def test_convergence_and_continuation_are_exact_sign_controls():
    panel = _panel()
    convergence, _ = build_pair_periods(panel, CFG, selection="distance", direction="convergence")
    continuation, _ = build_pair_periods(panel, CFG, selection="distance", direction="continuation")
    assert len(convergence) == 2
    assert [p.event_time for p in convergence] == [4, 5]
    assert convergence[0].instruments == continuation[0].instruments
    assert convergence[0].signals == tuple(-x for x in continuation[0].signals)
    assert convergence[0].forward_returns == continuation[0].forward_returns


def test_forward_pair_return_and_adv_are_leg_balanced():
    panel = _panel()
    periods, _ = build_pair_periods(panel, CFG, selection="distance", direction="convergence")
    first = periods[0]
    pair_index = first.instruments.index("A~B")
    left = panel.matrices["close"][4][0]
    right = panel.matrices["close"][4][1]
    expected = 0.5 * (
        panel.matrices["close"][5][0] / left - 1.0
        - (panel.matrices["close"][5][1] / right - 1.0)
    )
    assert first.forward_returns[pair_index] == pytest.approx(expected)
    assert first.adv_notional[pair_index] == min(left * 1000.0, right * 900.0)


def test_signal_leakage_proof_perturbs_strict_future():
    panel = _panel()
    assert pair_signal_leakage_ok(panel, CFG, selection="distance") is True


def test_missing_trade_or_label_price_fails_closed():
    panel = _panel()
    panel.matrices["close"][5][0] = None
    with pytest.raises(ValueError, match="missing trade close"):
        build_pair_periods(panel, CFG, selection="distance", direction="convergence")


def test_invalid_or_incomplete_formation_fails_closed():
    panel = _panel()
    panel.matrices["volume"][2][0] = None
    panel.matrices["volume"][2][1] = None
    with pytest.raises(ValueError, match="insufficient complete formation instruments"):
        build_cycle_plan(panel, 0, CFG, selection="distance")
