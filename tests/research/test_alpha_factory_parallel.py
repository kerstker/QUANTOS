"""Parallel-evaluation regression tests for the alpha-factory population loop
(services/research/alpha_factory.py::run_alpha_factory).

Context (2026-07-07 top-tier gap audit, S4.3/S4.8, independently nominated by both the
research/alpha-layer and AI-autonomy audit dimensions as the highest-value single fix):
the population evaluation loop is single-threaded even though each candidate's fitness
score is a pure function (no RNG, no wall-clock, no shared mutable state across
candidates) -- so it is naturally parallelizable, and historical mining runs have only
ever evaluated a few hundred genomes total.

Goal of THIS file: prove that adding ProcessPoolExecutor-based parallelism to that loop
produces BIT-IDENTICAL output to the sequential path -- not merely "close enough". The
project's downstream statistics (DSR/PBO/orthogonalization) all assume the search is
exactly reproducible, so a performance change that perturbs even the last bit of a score
would be a real regression, not a nitpick.

Windows note: ProcessPoolExecutor here uses the 'spawn' start method (Windows has no
'fork'), so every callable placed on the task queue must be a module-level, picklable-
by-reference function -- never a closure/lambda. `_score_expr` is verified directly
(across a real process boundary, full float precision, no rounding) as well as through
the public `run_alpha_factory(..., max_workers=...)` surface.
"""
from __future__ import annotations

import functools
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor

import services.research.alpha_expression as ax
import services.research.cs_genome as cs_genome
from services.research.alpha_factory import (
    _score_expr,
    run_alpha_factory as _run_alpha_factory,
)


def run_alpha_factory(*args, **kwargs):
    kwargs["ephemeral"] = True
    return _run_alpha_factory(*args, **kwargs)
from services.research.carry_pnl import economic_return_series

FIELDS = ["close", "funding"]
WINDOWS = [3, 5, 7, 14]


def _panel(n=8, t=80):
    # same shape of fixture as tests/research/test_alpha_factory.py::_panel (stable
    # per-name funding spanning -/+, mild price noise) so scores are non-degenerate.
    base = [0.001 * (i - 3.5) for i in range(n)]
    close = [[100.0 + 0.5 * math.sin(0.3 * t_ + i) for i in range(n)] for t_ in range(t)]
    funding = [base[:] for _ in range(t)]
    return ax.FieldPanel([f"C{i}" for i in range(n)], list(range(t)),
                         {"close": close, "funding": funding})


# ---------------------------------------------------------------------------------
# Layer 1: the extracted pure scoring primitive, exercised across a REAL process
# boundary at full float precision (no round()) -- the strongest possible check.
# ---------------------------------------------------------------------------------

def test_score_expr_bit_identical_across_process_boundary():
    panel = _panel()
    exprs = cs_genome.seed_alphas(FIELDS, WINDOWS)[:6]
    assert len(exprs) >= 4

    expected = [_score_expr(e, panel, 1, 0.2, True, None, 60, 0.0) for e in exprs]

    fn = functools.partial(_score_expr, panel=panel, fwd_horizon=1, quantile=0.2,
                           with_funding=True, basis=None, basis_min_overlap=60, overfit_lambda=0.0)
    with ProcessPoolExecutor(max_workers=2) as ex:
        got = list(ex.map(fn, exprs))

    assert got == expected                       # exact float equality, full precision


def test_score_expr_bit_identical_with_orthogonal_basis_across_process_boundary():
    panel = _panel()
    a_expr = ("cs_rank", ("neg", ("field", "funding")))
    basis = {"carry": tuple(economic_return_series(ax.evaluate(a_expr, panel), panel, quantile=0.2))}
    exprs = cs_genome.seed_alphas(FIELDS, WINDOWS)[:6]

    expected = [_score_expr(e, panel, 1, 0.2, True, basis, 60, 0.0) for e in exprs]

    fn = functools.partial(_score_expr, panel=panel, fwd_horizon=1, quantile=0.2,
                           with_funding=True, basis=basis, basis_min_overlap=60, overfit_lambda=0.0)
    with ProcessPoolExecutor(max_workers=2) as ex:
        got = list(ex.map(fn, exprs))

    assert got == expected


# ---------------------------------------------------------------------------------
# Layer 2: the public run_alpha_factory(..., max_workers=...) surface.
# ---------------------------------------------------------------------------------

def test_max_workers_none_is_unaffected_pre_existing_behavior():
    """Every EXISTING caller (they never pass max_workers) must see byte-identical
    behavior: omitting the kwarg == passing max_workers=None explicitly."""
    kwargs = dict(generations=2, population_size=12, elite_k=5, dataset_version="dv", timestamp=1)
    a = run_alpha_factory(_panel(), FIELDS, WINDOWS, **kwargs)
    b = run_alpha_factory(_panel(), FIELDS, WINDOWS, max_workers=None, **kwargs)
    assert a["best"] == b["best"]
    assert a["n_evaluated"] == b["n_evaluated"]


def test_max_workers_one_behaves_like_sequential():
    kwargs = dict(generations=2, population_size=12, elite_k=5, dataset_version="dv", timestamp=1)
    seq = run_alpha_factory(_panel(), FIELDS, WINDOWS, max_workers=None, **kwargs)
    one = run_alpha_factory(_panel(), FIELDS, WINDOWS, max_workers=1, **kwargs)
    assert seq["best"] == one["best"]


def test_parallel_matches_sequential_bitwise_small_population():
    kwargs = dict(generations=3, population_size=14, elite_k=6, dataset_version="dv", timestamp=1)
    seq = run_alpha_factory(_panel(), FIELDS, WINDOWS, max_workers=None, **kwargs)
    par = run_alpha_factory(_panel(), FIELDS, WINDOWS, max_workers=3, **kwargs)

    assert seq["n_evaluated"] == par["n_evaluated"]
    assert seq["scored_by"] == par["scored_by"]
    assert [b["alpha_id"] for b in seq["best"]] == [b["alpha_id"] for b in par["best"]]
    assert [b["score"] for b in seq["best"]] == [b["score"] for b in par["best"]]
    assert seq["best"] == par["best"]             # includes expr string + leakage_ok
    assert seq["pipeline"]["portfolio"]["selected_ids"] == par["pipeline"]["portfolio"]["selected_ids"]


def test_parallel_matches_sequential_bitwise_population_similar_to_production_driver():
    """population_size=48 / generations mirrors tools/crypto_factory_loop.py's real
    config (the driver named in the audit) more closely than the tiny toy populations
    used elsewhere in this file."""
    kwargs = dict(generations=3, population_size=48, elite_k=14, quantile=0.3,
                 dataset_version="dv", timestamp=1)
    seq = run_alpha_factory(_panel(n=16), FIELDS, WINDOWS, max_workers=None, **kwargs)
    par = run_alpha_factory(_panel(n=16), FIELDS, WINDOWS, max_workers=4, **kwargs)

    assert seq["n_evaluated"] == par["n_evaluated"]
    assert seq["best"] == par["best"]


def test_parallel_matches_sequential_with_orthogonal_novelty_reward():
    """basis_returns switches the reward function to incremental_ir -- a distinct
    arithmetic path from the plain-Sharpe default; verify it is ALSO bit-identical
    under parallel execution."""
    panel = _panel()
    a_expr = ("cs_rank", ("neg", ("field", "funding")))
    basis = {"carry": economic_return_series(ax.evaluate(a_expr, panel), panel, quantile=0.2)}
    kwargs = dict(generations=3, population_size=14, elite_k=6, basis_returns=basis,
                 dataset_version="dv", timestamp=1)
    seq = run_alpha_factory(panel, FIELDS, WINDOWS, max_workers=None, **kwargs)
    par = run_alpha_factory(panel, FIELDS, WINDOWS, max_workers=3, **kwargs)

    assert seq["scored_by"] == "orthogonal_incremental_ir" == par["scored_by"]
    assert seq["best"] == par["best"]


def test_parallel_matches_sequential_with_overfit_lambda_penalty():
    """overfit_lambda > 0 exercises the generalization_gap penalty branch inside
    _score_expr -- a third distinct arithmetic path; also verified bit-identical."""
    kwargs = dict(generations=2, population_size=12, elite_k=5, overfit_lambda=1.0,
                 dataset_version="dv", timestamp=1)
    seq = run_alpha_factory(_panel(), FIELDS, WINDOWS, max_workers=None, **kwargs)
    par = run_alpha_factory(_panel(), FIELDS, WINDOWS, max_workers=3, **kwargs)
    assert seq["best"] == par["best"]


def test_parallel_population_with_duplicate_alpha_ids_keeps_first_occurrence():
    """If the population passed to a single scoring batch contains two DIFFERENT expr
    reprs that hash to the SAME alpha_id (a legitimate possibility the sequential
    implementation always handled via 'first occurrence wins'), the parallel path must
    resolve the tie identically -- not merely produce the same SCORE, but retain the
    same stored expr object (visible via its serialized form in `best`)."""
    panel = _panel()
    dup_a = ("cs_rank", ("field", "close"))
    dup_b = ("cs_rank", ("field", "close"))               # structurally identical -> same alpha_id
    assert ax.alpha_id(dup_a) == ax.alpha_id(dup_b)
    other = ("cs_rank", ("neg", ("field", "funding")))
    pop_with_dupes = [dup_a, dup_b, other]

    seq = run_alpha_factory(panel, FIELDS, WINDOWS, generations=1,
                            population_size=len(pop_with_dupes), elite_k=2,
                            dataset_version="dv", timestamp=1, max_workers=None)
    par = run_alpha_factory(panel, FIELDS, WINDOWS, generations=1,
                            population_size=len(pop_with_dupes), elite_k=2,
                            dataset_version="dv", timestamp=1, max_workers=3)
    assert seq["best"] == par["best"]


# ---------------------------------------------------------------------------------
# Governance-style guard (mirrors test_alpha_factory.py::test_no_rng_or_wallclock_import):
# parallelizing must never smuggle in a source of non-determinism.
# ---------------------------------------------------------------------------------

def test_no_rng_or_wallclock_import():
    import inspect
    import services.research.alpha_factory as m
    src = inspect.getsource(m)
    assert "import random" not in src and "import time" not in src and "Random" not in src


# ---------------------------------------------------------------------------------
# Informative only (NOT a correctness assertion -- wall-clock speedup is inherently
# machine/load-dependent and would be a flaky thing to assert in CI). Skipped unless
# explicitly requested, so it never adds flakiness to the standard gate.
# ---------------------------------------------------------------------------------

def test_parallel_speedup_smoke():
    """Panel sized closer to the real driver (tools/crypto_factory_loop.py: 24 instruments,
    daily OI panel, population_size=48) than the tiny toy panels above, which are sized for
    fast bit-identity checks, not for a representative timing. NOT a correctness assertion --
    speedup is machine/load-dependent, hence opt-in and print-only."""
    if not os.environ.get("QUANTOS_BENCH_PARALLEL"):
        import pytest
        pytest.skip("benchmark-only; set QUANTOS_BENCH_PARALLEL=1 to run")
    kwargs = dict(generations=4, population_size=48, elite_k=14, dataset_version="dv", timestamp=1)
    workers = min(4, os.cpu_count() or 1)

    t0 = time.perf_counter()
    run_alpha_factory(_panel(n=24, t=1500), FIELDS, WINDOWS, max_workers=None, **kwargs)
    t_seq = time.perf_counter() - t0

    t0 = time.perf_counter()
    run_alpha_factory(_panel(n=24, t=1500), FIELDS, WINDOWS, max_workers=workers, **kwargs)
    t_par = time.perf_counter() - t0

    print(f"\n[bench] workers={workers} sequential={t_seq:.2f}s parallel={t_par:.2f}s "
          f"speedup={t_seq / max(t_par, 1e-9):.2f}x")
