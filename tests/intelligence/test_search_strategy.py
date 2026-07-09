import pytest
from services.intelligence.search_space import HypothesisSpace
from services.intelligence.search_strategy import UCBSearch


def _space():
    return HypothesisSpace(factors=("a", "b"), operators=("op",), windows=(10, 20))


def test_proposes_distinct_and_marks_proposed():
    s = UCBSearch(_space())
    first = s.propose(2)
    assert len(first) == 2 and len(set(first)) == 2
    second = s.propose(2)
    assert set(first).isdisjoint(second)                 # never re-proposes
    assert s.propose(99) == []                           # grid (4) exhausted


def test_update_raises_arm_mean_and_strategy_favors_good_arm():
    s = UCBSearch(_space())
    for hyp in s.space.hypotheses():
        s.update(hyp, 1.0 if hyp[2] == 20 else 0.0)
    assert s.arm_means()[("window", "20")] > s.arm_means()[("window", "10")]


def test_is_deterministic():
    a = UCBSearch(_space()).propose(4)
    b = UCBSearch(_space()).propose(4)
    assert a == b


# --- regret tracking ---------------------------------------------------------

def test_first_update_records_none_regret_no_known_best_yet():
    s = UCBSearch(_space())
    s.update(("a", "op", 10), 1.0)
    assert s.regret_history == [None]


def test_regret_computed_against_prior_best_known_arm_mean():
    s = UCBSearch(_space())
    # round 1: no arm pulled yet -> best_known_mean undefined -> None
    s.update(("a", "op", 10), 1.0)
    assert s.regret_history == [None]
    # round 2: best_known_mean (before this update) = max over arm means from
    # round 1 = 1.0 (factor a / operator op / window 10 all mean 1.0).
    # regret = best_known_mean - reward = 1.0 - 0.4 = 0.6
    s.update(("b", "op", 20), 0.4)
    assert s.regret_history[1] == pytest.approx(0.6)
    # round 3: best_known_mean (before this update) = max of
    #   factor a=1.0, operator op=0.7 (=(1.0+0.4)/2), window10=1.0,
    #   factor b=0.4, window20=0.4  -> max = 1.0
    # regret = 1.0 - 2.0 = -1.0 (new reward beat the previously best-known mean)
    s.update(("a", "op", 20), 2.0)
    assert s.regret_history[2] == pytest.approx(-1.0)


def test_cumulative_regret_sums_non_none_and_skips_none():
    s = UCBSearch(_space())
    s.update(("a", "op", 10), 1.0)     # None
    s.update(("b", "op", 20), 0.4)     # 0.6
    s.update(("a", "op", 20), 2.0)     # -1.0
    assert s.cumulative_regret() == pytest.approx(0.6 + (-1.0))


def test_cumulative_regret_zero_when_no_updates_yet():
    s = UCBSearch(_space())
    assert s.cumulative_regret() == 0.0


def test_snapshot_restore_roundtrip_preserves_regret_history():
    s = UCBSearch(_space())
    s.update(("a", "op", 10), 1.0)
    s.update(("b", "op", 20), 0.4)
    snap = s.snapshot()
    restored = UCBSearch(_space())
    restored.restore(snap)
    assert restored.regret_history == s.regret_history
    assert restored.cumulative_regret() == pytest.approx(s.cumulative_regret())


def test_restore_legacy_snapshot_without_regret_history_defaults_to_empty_list():
    # hand-built legacy (pre-regret-tracking) snapshot: no "regret_history" key at all.
    legacy_snap = {
        "version": 1,
        "pulls": [[["factor", "a"], 1], [["operator", "op"], 1], [["window", "10"], 1]],
        "reward_sum": [[["factor", "a"], 1.0], [["operator", "op"], 1.0], [["window", "10"], 1.0]],
        "n_evaluated": 1,
        "proposed": [["a", "op", 10]],
    }
    s = UCBSearch(_space())
    s.restore(legacy_snap)                            # must not raise (no KeyError)
    assert s.regret_history == []
    assert s.cumulative_regret() == 0.0
    # the rest of the legacy state still restores correctly
    assert s.n_evaluated == 1
    assert s.pulls[("factor", "a")] == 1
