"""No-peek walk-forward selection OOS test — the honest "is the in-sample edge real?"
check. We craft deterministic factor return series so the mechanism is verifiable:
a genuinely-good factor stays positive everywhere; a "lucky" factor is strongly
positive only in the early train window then flips negative — so in-sample selection
on the early window picks the lucky one and its OOS collapses, while a later window
correctly picks the good one. This exposes selection overfit, which a single
full-sample Sharpe cannot.
"""
import pytest

from services.research.oos_validation import walk_forward_selection_oos, full_sample_best

GOOD = [0.02, 0.01, 0.03, 0.02, 0.01, 0.02, 0.02, 0.01, 0.03, 0.02, 0.03, 0.01]   # all positive
LUCK = [0.05, 0.06, 0.04, 0.05, 0.06, 0.05, -0.04, -0.05, -0.06, -0.05, -0.04, -0.06]  # train-only edge
NOISE = [0.001, -0.001, 0.002, -0.002, 0.001, -0.001, 0.001, -0.001, 0.002, -0.002, 0.001, -0.001]
FR = {"good": GOOD, "luck": LUCK, "noise": NOISE}


def test_selection_overfit_is_exposed_oos():
    out = walk_forward_selection_oos(FR, train_size=6, test_size=3, step=3, top_k=1)
    assert out["n_folds"] == 2
    f0, f1 = out["folds"]
    # window 0: train[0:6] — LUCK has the highest in-sample Sharpe, gets selected,
    # but its held-out test[6:9] is negative -> OOS collapse
    assert f0["selected"] == ["luck"] and f0["is_sharpe"] > 0 and f0["oos_sharpe"] < 0
    # window 1: train[3:9] — LUCK is now mixed, GOOD wins and holds OOS
    assert f1["selected"] == ["good"] and f1["oos_sharpe"] > 0
    # the honest aggregate: OOS is worse than in-sample (selection overfit haircut)
    assert out["mean_oos_sharpe"] < out["mean_is_sharpe"]
    assert out["oos_minus_is"] == pytest.approx(out["mean_oos_sharpe"] - out["mean_is_sharpe"])
    assert 0.0 <= out["oos_positive_frac"] <= 1.0


def test_full_sample_best_is_the_in_sample_illusion():
    # picking the best over the WHOLE sample (peeking) prefers the genuinely-good
    # all-positive series; LUCK's full-sample Sharpe is mediocre (it cancels out)
    best_id, best_sr = full_sample_best(FR)
    assert best_id == "good" and best_sr > 0


def test_embargo_shrinks_train_and_can_drop_a_fold():
    # a large embargo eats the train window so a fold cannot be assessed -> fail closed
    out = walk_forward_selection_oos(FR, train_size=6, test_size=3, step=3, embargo=5)
    # train (6) minus embargo (5) = 1 usable period (< 2) -> every fold skipped
    assert out["n_folds"] == 0 and out["mean_oos_sharpe"] is None


def test_fail_closed_on_bad_input():
    with pytest.raises(ValueError):
        walk_forward_selection_oos({"only": GOOD}, train_size=6, test_size=3)   # < 2 factors
    with pytest.raises(ValueError):
        walk_forward_selection_oos({"a": GOOD, "b": GOOD[:-1]}, train_size=6, test_size=3)  # unequal length
    with pytest.raises(ValueError):
        walk_forward_selection_oos(FR, train_size=1, test_size=3)               # train_size < 2


def test_top_k_combines_selected_factors():
    out = walk_forward_selection_oos(FR, train_size=6, test_size=3, step=3, top_k=2)
    assert out["top_k"] == 2
    for fold in out["folds"]:
        assert len(fold["selected"]) == 2          # equal-weight blend of the top 2
