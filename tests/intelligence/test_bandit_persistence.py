"""#5 — UCBSearch snapshot/restore so the agent's learned arm statistics survive
across processes (mirrors catalog/registry persistence)."""
from services.intelligence.search_space import HypothesisSpace
from services.intelligence.search_strategy import UCBSearch


def _space():
    return HypothesisSpace(factors=("a", "b"), operators=("op",), windows=(10, 20))


def test_snapshot_restore_round_trips_learned_state():
    s = UCBSearch(_space())
    s.propose(2)                                     # marks proposed
    for hyp in s.space.hypotheses():
        s.update(hyp, 1.0 if hyp[2] == 20 else 0.0)
    snap = s.snapshot()

    t = UCBSearch(_space())
    t.restore(snap)
    assert t.arm_means() == s.arm_means()
    assert t.n_evaluated == s.n_evaluated
    assert t.proposed == s.proposed                  # don't re-propose what was already tried
    # the learned preference is preserved
    assert t.arm_means()[("window", "20")] > t.arm_means()[("window", "10")]


def test_restore_is_json_safe_round_trip():
    import json
    s = UCBSearch(_space())
    for hyp in s.space.hypotheses():
        s.update(hyp, 0.5)
    snap = json.loads(json.dumps(s.snapshot()))      # survives JSON (lists, not tuples)
    t = UCBSearch(_space())
    t.restore(snap)
    assert t.arm_means() == s.arm_means() and t.n_evaluated == s.n_evaluated
