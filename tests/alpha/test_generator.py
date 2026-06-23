"""Phase 6 step 12 — generator: deterministic, inert, proposal-only."""
from quantos.contracts.alpha import AlphaDefinition
from services.alpha.generator import propose


def test_propose_enumerates_combinations_deterministically():
    a = propose(["f2", "f1"], max_factors=2, universe="CN")
    b = propose(["f1", "f2"], max_factors=2, universe="CN")
    ids = [d.alpha_id for d in a]
    assert ids == [d.alpha_id for d in b]                 # order-independent input -> same output
    assert ids == ["alpha_f1", "alpha_f2", "alpha_f1_f2"]
    assert all(isinstance(d, AlphaDefinition) for d in a)


def test_proposals_are_frozen_inert_specs():
    d = propose(["f1"])[0]
    assert d.factor_ids == ("f1",) and d.universe == "DEFAULT"
    # frozen: cannot be mutated into a live/promoted thing
    import pytest
    with pytest.raises(Exception):
        d.factor_ids = ("hacked",)


def test_deduplicates_factor_ids():
    out = propose(["f1", "f1", "f2"], max_factors=1)
    assert [d.alpha_id for d in out] == ["alpha_f1", "alpha_f2"]
