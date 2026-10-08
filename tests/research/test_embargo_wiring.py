"""When forward-return labels of horizon h overlap, a walk-forward fold's opening points share their
label window with the prior fold and are not independent — the honest purge is embargo = h-1. The
research session knows the horizon; the evaluator applies the embargo. This pins that the horizon is
WIRED into the contained loop's evaluator (it was previously hard-coded to 0), and that horizon=1
is unchanged (embargo 0) so no existing single-horizon result moves."""
from services.alpha.catalog import AlphaCatalog
from services.intelligence import research_session as session


def test_inner_loop_embargo_derives_from_horizon():
    cat = AlphaCatalog()
    # horizon 1 (the common case) -> embargo 0: identical to the previous default, nothing moves.
    assert session._inner_loop(cat, False, fwd_horizon=1).evaluator.config.wf_embargo == 0
    # horizon h>1 -> embargo h-1: overlapping label points are purged at each fold boundary.
    assert session._inner_loop(cat, False, fwd_horizon=5).evaluator.config.wf_embargo == 4
    assert session._inner_loop(cat, True, fwd_horizon=10).evaluator.config.wf_embargo == 9


def test_inner_loop_regime_flag_still_threads():
    cat = AlphaCatalog()
    assert session._inner_loop(cat, True, fwd_horizon=1).evaluator.config.compute_regime_evidence is True
    assert session._inner_loop(cat, False, fwd_horizon=1).evaluator.config.compute_regime_evidence is False
