"""Phase 6 — catalog: catalog-wide trial counter (monotonic, dedup), running
var_sr over the whole candidate population, survivorship-free state."""
import pytest

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog


def _def(aid, factors=("f1",), universe="CN"):
    return AlphaDefinition(alpha_id=aid, name=aid, factor_ids=factors, universe=universe)


def test_trial_count_monotonic_and_dedups_identical_configs():
    cat = AlphaCatalog()
    cat.register(_def("a1", factors=("f1",)))
    cat.register(_def("a2", factors=("f2",)))      # different config
    assert cat.global_trial_count() == 2
    # re-running an identical config (same factor_ids/universe/params) does not inflate
    cat.register_trial(_def("dup", factors=("f1",)).definition_hash)
    assert cat.global_trial_count() == 2
    # a genuinely new config does
    cat.register_trial(_def("new", factors=("f3",)).definition_hash)
    assert cat.global_trial_count() == 3


def test_duplicate_alpha_id_rejected():
    cat = AlphaCatalog()
    cat.register(_def("a1"))
    with pytest.raises(ValueError):
        cat.register(_def("a1", factors=("fx",)))


def test_catalog_var_sr_is_none_below_two_populated_then_real():
    cat = AlphaCatalog()
    assert cat.catalog_var_sr() is None                  # no honest estimate yet (review P0)
    cat.register(_def("a1")); cat.register(_def("a2")); cat.register(_def("a3"))
    cat.set_returns("a1", [0.01, 0.02, 0.0, 0.03])
    assert cat.catalog_var_sr() is None                  # still only 1 populated
    cat.set_returns("a2", [-0.01, 0.04, 0.02, -0.02])
    cat.set_returns("a3", [0.05, -0.03, 0.01, 0.02])
    assert cat.catalog_var_sr() > 1e-4                   # reflects the population spread


def test_survivorship_free_and_approved_set():
    cat = AlphaCatalog()
    for a in ("a1", "a2"):
        cat.register(_def(a))
        cat.set_returns(a, [0.01, 0.02, -0.01, 0.03])
    cat._set_state("a1", AlphaState.APPROVED)
    cat._set_state("a2", AlphaState.RETIRED)
    assert cat.approved_set().keys() == {"a1"}
    # retired alpha is still queryable (no survivorship bias in the catalog)
    assert "a2" in cat.all_ids() and cat.state("a2") == AlphaState.RETIRED
