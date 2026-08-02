"""F4 — health propagation: fail-closed rulings (UNKNOWN != healthy), factor verdicts,
alpha pause propagation. Pure core, facts injected — no IO in the module under test."""
import pytest

from services.factor_garden import (
    DatasetHealth,
    DependencyGraph,
    evaluate_factor_health,
    propagate,
)
from services.factor_garden.health import (
    FACTOR_HEALTHY,
    FACTOR_PAUSED,
    FACTOR_STALE,
)


def _graph():
    return DependencyGraph(
        {
            "f_carry": ("crypto_funding", "crypto_funding_rates"),
            "f_crowd": ("crypto_funding", "crypto_oi_metrics"),
        },
        alpha_factors={"a_combo": ("f_carry", "f_crowd")},
    )


def _facts(**over):
    base = {
        "crypto_funding": DatasetHealth("crypto_funding", "PASS"),
        "crypto_funding_rates": DatasetHealth("crypto_funding_rates", "PASS"),
        "crypto_oi_metrics": DatasetHealth("crypto_oi_metrics", "PASS"),
    }
    base.update(over)
    return base


def test_all_pass_is_healthy():
    v = evaluate_factor_health(_graph(), _facts(), factor_id="f_crowd")
    assert v.status == FACTOR_HEALTHY
    assert v.blocking_datasets == () and v.reasons == ()


def test_block_pauses_with_detail():
    facts = _facts(crypto_oi_metrics=DatasetHealth("crypto_oi_metrics", "BLOCK",
                                                   "admission BLOCK: lineage mismatch"))
    v = evaluate_factor_health(_graph(), facts, factor_id="f_crowd")
    assert v.status == FACTOR_PAUSED
    assert v.blocking_datasets == ("crypto_oi_metrics",)
    assert v.reasons == ("crypto_oi_metrics: BLOCK (admission BLOCK: lineage mismatch)",)


def test_missing_fact_is_unknown_and_pauses_fail_closed():
    facts = _facts()
    del facts["crypto_oi_metrics"]                          # no fact injected
    v = evaluate_factor_health(_graph(), facts, factor_id="f_crowd")
    assert v.status == FACTOR_PAUSED                        # UNKNOWN never passes
    assert v.blocking_datasets == ("crypto_oi_metrics",)
    assert "UNKNOWN" in v.reasons[0]


def test_stale_alone_is_stale_not_paused():
    facts = _facts(crypto_funding=DatasetHealth("crypto_funding", "STALE", "watermark 3d"))
    v = evaluate_factor_health(_graph(), facts, factor_id="f_carry")
    assert v.status == FACTOR_STALE
    assert v.blocking_datasets == ()
    assert v.reasons == ("crypto_funding: STALE (watermark 3d)",)


def test_block_beats_stale():
    facts = _facts(
        crypto_funding=DatasetHealth("crypto_funding", "STALE"),
        crypto_oi_metrics=DatasetHealth("crypto_oi_metrics", "BLOCK"),
    )
    v = evaluate_factor_health(_graph(), facts, factor_id="f_crowd")
    assert v.status == FACTOR_PAUSED


def test_factor_with_no_datasets_or_unknown_factor_pauses():
    g = DependencyGraph({"f_orphan": ()})
    assert evaluate_factor_health(g, {}, factor_id="f_orphan").status == FACTOR_PAUSED
    assert evaluate_factor_health(g, {}, factor_id="ghost").status == FACTOR_PAUSED


def test_dataset_health_validation():
    with pytest.raises(ValueError):
        DatasetHealth("d", "SOMEWHAT_FINE")
    with pytest.raises(ValueError):
        DatasetHealth("", "PASS")


def test_propagate_pauses_alpha_on_unhealthy_factor():
    facts = _facts(crypto_oi_metrics=DatasetHealth("crypto_oi_metrics", "BLOCK", "x"))
    out = propagate(_graph(), facts)
    assert out["factors"]["f_carry"].status == FACTOR_HEALTHY
    assert out["factors"]["f_crowd"].status == FACTOR_PAUSED
    alpha = out["alphas"]["a_combo"]
    assert alpha["status"] == FACTOR_PAUSED
    assert alpha["blocking_factors"] == ("f_crowd",)
    assert alpha["reasons"] == ("f_crowd: PAUSED",)


def test_propagate_all_healthy_and_extra_alphas_argument():
    out = propagate(_graph(), _facts(), alphas={"a_extra": ("f_carry",)})
    assert out["alphas"]["a_combo"]["status"] == FACTOR_HEALTHY
    assert out["alphas"]["a_extra"] == {
        "status": FACTOR_HEALTHY, "blocking_factors": (), "reasons": (),
    }
    # an alpha referencing a factor outside the graph pauses (fail-closed)
    out2 = propagate(_graph(), _facts(), alphas={"a_ghost": ("ghost_factor",)})
    assert out2["alphas"]["a_ghost"]["status"] == FACTOR_PAUSED
    assert out2["alphas"]["a_ghost"]["blocking_factors"] == ("ghost_factor",)
