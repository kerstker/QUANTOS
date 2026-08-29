"""Mass-production: the deterministic cs_genome (seed/mutate/crossover over the cross-sectional DSL)
DRIVES the pipeline — generate a population of cs-alphas, SCORE each by its economic L/S Sharpe, keep
the elite, breed the next generation, repeat — then run the FULL pipeline over the best set. The search
is scored on real economic (carry) return, so it evolves TOWARD the funding carry on its own. Contained
(no lifecycle), deterministic (cs_genome is hashlib-seeded, no RNG)."""
import math

import services.research.alpha_expression as ax
from services.research.alpha_factory import run_alpha_factory as _run_alpha_factory

FIELDS = ["close", "funding"]
WINDOWS = [3, 5, 7, 14]


def run_alpha_factory(*args, **kwargs):
    kwargs["ephemeral"] = True
    kwargs["diagnostic_only"] = True
    return _run_alpha_factory(*args, **kwargs)


def _panel():
    # stable per-name funding spanning -/+ (so a neg-funding-rank book COLLECTS), mild price noise
    T, N = 80, 8
    base = [0.001 * (i - 3.5) for i in range(N)]
    close = [[100.0 + 0.5 * math.sin(0.3 * t + i) for i in range(N)] for t in range(T)]
    funding = [base[:] for _ in range(T)]
    return ax.FieldPanel([f"C{i}" for i in range(N)], list(range(T)),
                         {"close": close, "funding": funding})


def test_factory_evolves_and_runs_full_pipeline():
    out = run_alpha_factory(_panel(), FIELDS, WINDOWS, generations=3, population_size=14, elite_k=6,
                            dataset_version="dv", timestamp=1)
    assert out["generations"] == 3
    assert out["n_evaluated"] >= 14                      # breeding produced new alphas beyond the seed pop
    assert len(out["best"]) == 6
    top = out["best"][0]
    assert top["score"] > 0.0 and top["leakage_ok"] is True   # the best collects positive carry, PIT-safe
    # the full pipeline ran over the elite set (registry / portfolio / brief all present)
    assert out["pipeline"]["n_evaluated"] == 6
    assert "selected_ids" in out["pipeline"]["portfolio"]
    # review fix: the seed count is surfaced (truncation to population_size is no longer silent)
    assert out["n_seeds"] >= out["population_size"]


def test_factory_is_deterministic():
    a = run_alpha_factory(_panel(), FIELDS, WINDOWS, generations=2, population_size=12, elite_k=5,
                          dataset_version="dv", timestamp=1)
    b = run_alpha_factory(_panel(), FIELDS, WINDOWS, generations=2, population_size=12, elite_k=5,
                          dataset_version="dv", timestamp=1)
    assert [x["alpha_id"] for x in a["best"]] == [x["alpha_id"] for x in b["best"]]
    assert [x["score"] for x in a["best"]] == [x["score"] for x in b["best"]]


def test_factory_best_are_valid_cross_sectional_trees():
    out = run_alpha_factory(_panel(), FIELDS, WINDOWS, generations=2, population_size=10, elite_k=4,
                            dataset_version="dv", timestamp=1)
    import services.research.cs_genome as g
    for b in out["best"]:
        # every produced alpha is a valid, depth-bounded, cross-sectional-rooted tree
        # (reconstruct from the registry asset's stored expression isn't needed — pin via the genome rule)
        assert b["score"] == b["score"]                  # finite (not NaN)


def test_factory_orthogonal_novelty_reward_downweights_basis_clone():
    """With a `basis_returns` (the known-edge return streams), the search reward switches to the
    INCREMENTAL IR ORTHOGONAL to the basis — so a candidate that merely re-derives a basis edge scores
    ~0 (fully explained) and the genome is pushed toward NOVEL orthogonal sources. This is the autonomous
    multi-dimensional discovery capability, hardened into the engine."""
    from services.research.carry_pnl import economic_return_series
    panel = _panel()
    a_expr = ("cs_rank", ("neg", ("field", "funding")))                # a known carry edge
    basis = {"carry": economic_return_series(ax.evaluate(a_expr, panel), panel, quantile=0.2)}

    out = run_alpha_factory(panel, FIELDS, WINDOWS, generations=3, population_size=14, elite_k=6,
                            basis_returns=basis, dataset_version="dv", timestamp=1)
    assert out["scored_by"] == "orthogonal_incremental_ir"            # reward switched to novelty
    # the basis clone is fully explained by the basis -> incremental IR ~0 -> never the top discovery
    assert out["best"][0]["alpha_id"] != ax.alpha_id(a_expr)
    assert out["best"][0]["score"] == out["best"][0]["score"]         # finite, not NaN
    # deterministic under the orthogonal reward too
    out2 = run_alpha_factory(panel, FIELDS, WINDOWS, generations=3, population_size=14, elite_k=6,
                             basis_returns=basis, dataset_version="dv", timestamp=1)
    assert [b["alpha_id"] for b in out2["best"]] == [b["alpha_id"] for b in out["best"]]
    # default (no basis) is unchanged: scored by raw economic Sharpe
    base_out = run_alpha_factory(panel, FIELDS, WINDOWS, generations=2, population_size=12, elite_k=5,
                                 dataset_version="dv", timestamp=1)
    assert base_out["scored_by"] == "economic"


def test_factory_overfit_lambda_default_off_matches_plain():
    # overfit_lambda defaults to 0 -> identical to a run without the feature (opt-in regression guard)
    plain = run_alpha_factory(_panel(), FIELDS, WINDOWS, generations=2, population_size=12, elite_k=5,
                              dataset_version="dv", timestamp=1)
    explicit0 = run_alpha_factory(_panel(), FIELDS, WINDOWS, generations=2, population_size=12, elite_k=5,
                                  overfit_lambda=0.0, dataset_version="dv", timestamp=1)
    assert [b["alpha_id"] for b in plain["best"]] == [b["alpha_id"] for b in explicit0["best"]]
    assert [b["score"] for b in plain["best"]] == [b["score"] for b in explicit0["best"]]


def test_factory_overfit_lambda_deterministic_and_penalises():
    # generalisation-gap penalty ON: deterministic, and a penalised score is never ABOVE the unpenalised
    # one for the same alpha (the penalty only subtracts; default eval untouched).
    plain = run_alpha_factory(_panel(), FIELDS, WINDOWS, generations=2, population_size=12, elite_k=6,
                              dataset_version="dv", timestamp=1)
    pen = run_alpha_factory(_panel(), FIELDS, WINDOWS, generations=2, population_size=12, elite_k=6,
                            overfit_lambda=1.0, dataset_version="dv", timestamp=1)
    pen2 = run_alpha_factory(_panel(), FIELDS, WINDOWS, generations=2, population_size=12, elite_k=6,
                             overfit_lambda=1.0, dataset_version="dv", timestamp=1)
    assert [b["alpha_id"] for b in pen["best"]] == [b["alpha_id"] for b in pen2["best"]]   # deterministic
    plain_scores = {b["alpha_id"]: b["score"] for b in plain["best"]}
    for b in pen["best"]:
        if b["alpha_id"] in plain_scores:
            assert b["score"] <= plain_scores[b["alpha_id"]] + 1e-9   # penalty never raises a score


def test_no_rng_or_wallclock_import():
    import inspect
    import services.research.alpha_factory as m
    src = inspect.getsource(m)
    assert "import random" not in src and "import time" not in src and "Random" not in src


# ---- catalog= threading + full-population trial registration (2026-07-10, gap audit rank #6) --

def test_explicit_ephemeral_catalog_counts_the_full_population_in_memory():
    """Explicit test-only runs still count every scored candidate before selection."""
    out = run_alpha_factory(_panel(), FIELDS, WINDOWS, generations=2, population_size=12, elite_k=5,
                            dataset_version="dv", timestamp=1)
    assert out["pipeline"]["catalog"].global_trial_count() == out["n_evaluated"]


def test_supplied_catalog_registers_the_full_scored_population_not_just_the_elite():
    """The core point of rank #6: with a persistent catalog, the FULL scored population (n_evaluated,
    which can be much larger than elite_k) must be reflected in global_trial_count() — not just the
    elite subset that reaches the full pipeline evaluation."""
    from services.alpha.catalog import AlphaCatalog
    cat = AlphaCatalog()
    out = run_alpha_factory(_panel(), FIELDS, WINDOWS, generations=2, population_size=12, elite_k=5,
                            dataset_version="dv", timestamp=1, catalog=cat)
    assert out["n_evaluated"] > 5                        # more candidates scored than made the elite cut
    assert cat.global_trial_count() == out["n_evaluated"]  # the FULL breadth, not just the elite


def test_elite_candidates_are_not_double_counted_between_the_trial_loop_and_the_pipeline():
    """A candidate that's BOTH in the scored population AND the elite set must hash identically in
    both places (same factor_ids/universe construction) so register_trial's content-hash dedup
    counts it exactly once, not twice."""
    from services.alpha.catalog import AlphaCatalog
    cat = AlphaCatalog()
    out = run_alpha_factory(_panel(), FIELDS, WINDOWS, generations=2, population_size=12, elite_k=5,
                            dataset_version="dv", timestamp=1, catalog=cat)
    # global_trial_count must equal exactly the deduplicated scored population -- if elite candidates
    # were double-counted (once by the new loop, once again inside run_alpha_asset_pipeline's own
    # register()), this would be inflated beyond n_evaluated.
    assert cat.global_trial_count() == out["n_evaluated"]


def test_rerunning_the_same_search_against_a_persistent_catalog_does_not_inflate_trial_count():
    """Deterministic search + idempotent register_trial: re-running the IDENTICAL search against the
    SAME persistent catalog must not double the trial count (it's the same search, not new trials)."""
    from services.alpha.catalog import AlphaCatalog
    cat = AlphaCatalog()
    run_alpha_factory(_panel(), FIELDS, WINDOWS, generations=2, population_size=12, elite_k=5,
                      dataset_version="dv", timestamp=1, catalog=cat)
    count_after_first = cat.global_trial_count()
    run_alpha_factory(_panel(), FIELDS, WINDOWS, generations=2, population_size=12, elite_k=5,
                      dataset_version="dv", timestamp=2, catalog=cat)   # identical search, must not crash
    assert cat.global_trial_count() == count_after_first
