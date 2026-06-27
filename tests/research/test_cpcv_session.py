"""The research session must surface the CPCV OOS distribution over the run's OWN discovered factors —
a richer honesty signal than the single no-peek path. This pins the read-only helper directly (no store
harness needed): it aligns the catalog's stored net returns and runs cpcv_summary, fail-closed."""
from quantos.contracts.alpha import AlphaDefinition
from services.alpha.catalog import AlphaCatalog
from services.research import session


def _good(T):
    return [0.010 + 0.001 * ((i % 3) - 1) for i in range(T)]


def _noise(T, seed):
    return [0.02 * (1 if (i + seed) % 2 == 0 else -1) for i in range(T)]


def _add(cat, aid, returns):
    # _cpcv_validate iterates catalog.all_ids() (registered alphas), mirroring the evaluator which
    # register()s then set_returns() — so a test must do both.
    cat.register(AlphaDefinition(alpha_id=aid, name=aid, factor_ids=(aid,), universe="u", params={}))
    cat.set_returns(aid, returns)


def test_cpcv_validate_summarizes_run_factors():
    cat = AlphaCatalog()
    _add(cat, "good", _good(60))
    for j in range(4):
        _add(cat, f"n{j}", _noise(60, j))
    s = session._cpcv_validate(cat, embargo=1, n_groups=6, k_test=2)
    assert s is not None
    assert s["n_splits"] == 15
    assert s["frac_positive"] > 0.8                       # the good factor generalizes OOS
    assert "good" in s["selected_counts"]


def test_cpcv_validate_fail_closed_too_few_or_short():
    cat = AlphaCatalog()
    assert session._cpcv_validate(cat) is None             # no factors
    _add(cat, "a", _good(60))
    assert session._cpcv_validate(cat) is None             # only one factor
    _add(cat, "b", _noise(8, 1))
    assert session._cpcv_validate(cat) is None             # common length 8 < n_groups*2
