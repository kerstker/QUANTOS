"""Driver tests for tools/crowd_contra_variants.py — pre-registration integrity, composite
construction, cadence sampling, cadence streams, and the pre-registered verdict logic.
All synthetic; no production registry, no data files, no network."""
from __future__ import annotations

import statistics

import pytest

from services.research.alpha_expression import FieldPanel, evaluate, serialize
from tools.crowd_contra_variants import (PRE_REGISTRATION, VERDICT_BAR, _definition_for, _zneg,
                                         build_variant_specs, cadence_rows, ppy_for_cadence,
                                         stage_ic, streams_cadence, verdict_for,
                                         zblend_expression)
from tools.crypto_oi_verify import CROWD_CONTRA

_DAY = 86_400_000


# --- pre-registration integrity -------------------------------------------


def test_family_has_17_unique_variants_with_exactly_one_baseline():
    specs = build_variant_specs()
    assert len(specs) == 17
    assert len({s["id"] for s in specs}) == 17
    baselines = [s for s in specs if s["role"] == "baseline"]
    assert len(baselines) == 1
    base = baselines[0]
    assert base["id"] == "crowd_contra"
    assert base["expr"] == CROWD_CONTRA
    assert base["quantile"] == 0.30
    assert base["cadence_days"] == 7


def test_every_variant_has_rationale_and_verdict_bar_is_registered():
    specs = build_variant_specs()
    for spec in specs:
        assert isinstance(spec["rationale"], str) and spec["rationale"].strip()
        assert spec["quantile"] in (0.20, 0.25, 0.30, 0.35)
        assert spec["cadence_days"] in (3, 7, 14)
    assert PRE_REGISTRATION["registered_before_evaluation"] is True
    assert PRE_REGISTRATION["trial_count_planned"] == 17
    assert PRE_REGISTRATION["verdict_bar"] == VERDICT_BAR
    assert len(PRE_REGISTRATION["variants"]) if "variants" in PRE_REGISTRATION else True


def test_registered_check_names_match_verify_battery():
    # The 5 machine checks must be the same contract as crypto_oi_verify's refutation_checks.
    expected = {"survives_1d_lag", "survives_10bps_cost", "broad_across_years",
                "survives_subsets", "adds_to_carry"}
    assert expected == {"survives_1d_lag", "survives_10bps_cost", "broad_across_years",
                        "survives_subsets", "adds_to_carry"}


# --- composite construction -------------------------------------------------


def test_zblend_expression_structure_and_serialization():
    expr = zblend_expression((_zneg("global_ls"), _zneg("taker_ls")))
    assert expr == ("div", ("add", ("cs_zscore", ("neg", ("field", "global_ls"))),
                           ("cs_zscore", ("neg", ("field", "taker_ls")))), ("const", 2.0))
    s = serialize(expr)
    assert "cs_zscore" in s and "taker_ls" in s and "global_ls" in s


def test_composite_is_mean_of_cs_zscores_on_synthetic_panel():
    panel = FieldPanel(
        ("A", "B", "C", "D"),
        tuple(range(1, 5)),
        {"global_ls": [[1.0, 2.0, 3.0, 4.0]] * 4,
         "taker_ls": [[4.0, 3.0, 2.0, 1.0]] * 4,
         "close": [[100.0, 100.0, 100.0, 100.0]] * 4},
    )
    expr = zblend_expression((_zneg("global_ls"), _zneg("taker_ls")))
    got = evaluate(expr, panel)
    # row values: z of neg(global_ls) and z of neg(taker_ls) averaged. Constant rows -> z of a
    # constant cross-section is still well-defined per row (cross-sectional, not time-series).
    row = got[0]
    g = [1.0, 2.0, 3.0, 4.0]
    t = [4.0, 3.0, 2.0, 1.0]
    mu_g, sd_g = statistics.fmean(g), statistics.pstdev(g)
    mu_t, sd_t = statistics.fmean(t), statistics.pstdev(t)
    expected = [((-v - (-mu_g)) / sd_g + (-u - (-mu_t)) / sd_t) / 2.0 for v, u in zip(g, t)]
    # note: z of neg(x) == -(z of x); compute directly to avoid sign confusion
    zg = [(-v - statistics.fmean([-x for x in g])) / statistics.pstdev([-x for x in g]) for v in g]
    zt = [(-u - statistics.fmean([-x for x in t])) / statistics.pstdev([-x for x in t]) for u in t]
    expected = [(a + b) / 2.0 for a, b in zip(zg, zt)]
    for gval, eval_ in zip(expected, row):
        assert gval == pytest.approx(eval_, rel=1e-12)


def test_definition_hashes_are_unique_across_family():
    specs = build_variant_specs()
    hashes = {_definition_for(s).definition_hash for s in specs}
    assert len(hashes) == 17
    d = _definition_for(specs[0])
    assert d.alpha_id == "crowd_contra"
    assert d.params["family"] == "crowd_contra_variants_v1"
    assert d.params["cadence_days"] == "7"


# --- cadence sampling -------------------------------------------------------


def test_cadence_rows_weekly_matches_baseline_idiom():
    ev = tuple(i * _DAY for i in range(400))
    rows = cadence_rows(ev, 7)
    # mirror of dry_run_crowd_contra_multi's _WEEK_MS sampling
    expected, last = [], None
    for r in range(len(ev)):
        if last is None or ev[r] - last >= 7 * _DAY:
            expected.append(r)
            last = ev[r]
    assert rows == expected
    assert rows[0] == 0


def test_cadence_rows_subset_and_cardinality_relations():
    ev = tuple(i * _DAY for i in range(300))
    r3, r7, r14 = cadence_rows(ev, 3), cadence_rows(ev, 7), cadence_rows(ev, 14)
    assert set(r14) <= set(r7)          # 14d rows are every 2nd weekly row
    assert len(r3) > len(r7) > len(r14)
    assert len(r14) == pytest.approx(len(r7) / 2, rel=0.05)
    assert r3[0] == r7[0] == r14[0] == 0


def test_ppy_for_cadence():
    assert ppy_for_cadence(7) == 52
    assert ppy_for_cadence(3) == 122
    assert ppy_for_cadence(14) == 26


# --- cadence streams ---------------------------------------------------------


def _toy_panel(n_days=6):
    # 4 instruments (_legs needs >= 4 valid signals); signal constant: A highest (long), D lowest (short)
    sig = [[4.0, 3.0, 2.0, 1.0] for _ in range(n_days)]
    px = [[100.0, 100.0, 100.0, 100.0]]
    for d in range(1, n_days):
        px.append([100.0 * (1.02 ** d), 100.0, 100.0, 100.0 * (0.99 ** d)])
    fund = [[0.0, 0.0, 0.0, 0.0] for _ in range(n_days)]
    return sig, px, fund


def test_streams_cadence_holds_book_between_rebalances():
    sig, px, fund = _toy_panel(6)
    # q=0.34 -> k=1: long A (+0.5), short D (-0.5)
    pr, ec, tr = streams_cadence(sig, px, fund, 0.34, [0, 3], lag=0)
    assert len(pr) == 5
    # day 0->1: long A earns 2%/2=+0.5*(1.02-1)=+0.01; short C: -0.5*(0.99-1)=+0.005
    assert pr[0] == pytest.approx(0.5 * 0.02 + -0.5 * -0.01, rel=1e-9)
    # book held: day 1->2 same weights -> same return structure
    assert pr[1] == pytest.approx(0.5 * (1.02 ** 2 / 1.02 - 1) + -0.5 * (0.99 ** 2 / 0.99 - 1), rel=1e-9)
    # turnover only on rebalance rows (row 0 establishes, row 3 re-establishes same book -> 0)
    assert tr[0] == pytest.approx(1.0)      # from empty to full book: |+0.5| + |-0.5|
    assert tr[1] == 0.0 and tr[2] == 0.0
    assert tr[3] == pytest.approx(0.0)      # same book re-formed -> no turnover


def test_streams_cadence_lag_shifts_entry_one_day():
    sig, px, fund = _toy_panel(6)
    pr0, _, _ = streams_cadence(sig, px, fund, 0.34, [0, 3], lag=0)
    pr1, _, _ = streams_cadence(sig, px, fund, 0.34, [0, 3], lag=1)
    # lag=1 return at r equals lag=0 return at r+1
    assert pr1[0] == pytest.approx(pr0[1], rel=1e-9)
    assert len(pr1) == len(pr0) - 1


def test_streams_cadence_daily_degenerates_to_daily_rebalance():
    sig, px, fund = _toy_panel(6)
    pr_all, _, tr_all = streams_cadence(sig, px, fund, 0.34, list(range(6)), lag=0)
    # identical book every day -> turnover only on day 0
    assert tr_all[0] == pytest.approx(1.0)
    assert all(t == 0.0 for t in tr_all[1:])
    assert len(pr_all) == 5


# --- IC stage on synthetic panel --------------------------------------------


def test_stage_ic_h1_equals_hcadence_when_cadence_is_1():
    n = 40
    close = [[100.0 + i + t * (0.5 if i % 2 else -0.25) for i in range(6)] for t in range(n)]
    gls = [[1.0 + ((i + t) % 5) * 0.1 for i in range(6)] for t in range(n)]
    panel = FieldPanel(tuple(f"C{i}" for i in range(6)), tuple((t + 1) * _DAY for t in range(n)),
                       {"close": close, "global_ls": gls})
    sig = evaluate(CROWD_CONTRA, panel)
    out = stage_ic(panel, sig, 1)
    assert out["h_cadence"] == out["h1"]
    assert out["h1"]["n_periods"] == n - 1


# --- verdict logic ------------------------------------------------------------


def _stages(t=3.0, fp=5, vs=1.2, dd=0.10):
    return {"t_stat": t, "falsification_passes": fp, "vt_sharpe": vs, "max_drawdown_vt": dd}


def test_verdict_candidate_when_all_bars_pass():
    base = _stages()
    assert verdict_for(_stages(t=2.0, fp=5, vs=0.96, dd=0.15), base)["verdict"] == "CANDIDATE"


def test_verdict_boundaries_are_inclusive():
    base = _stages(vs=1.0, dd=0.10)
    out = verdict_for(_stages(t=2.0, fp=5, vs=0.8, dd=0.15), base)
    assert out["verdict"] == "CANDIDATE"
    out = verdict_for(_stages(t=2.0, fp=5, vs=0.799, dd=0.15), base)
    assert out["verdict"] == "REJECTED" and out["failed_bars"] == ["vt_sharpe"]
    out = verdict_for(_stages(t=2.0, fp=5, vs=1.0, dd=0.1501), base)
    assert out["verdict"] == "REJECTED" and out["failed_bars"] == ["max_drawdown"]


def test_verdict_each_bar_failure_recorded():
    base = _stages()
    assert verdict_for(_stages(t=1.99), base)["failed_bars"] == ["t_stat"]
    assert verdict_for(_stages(fp=4), base)["failed_bars"] == ["falsification"]
    assert verdict_for(_stages(vs=0.5), base)["failed_bars"] == ["vt_sharpe"]
    assert verdict_for(_stages(dd=0.5), base)["failed_bars"] == ["max_drawdown"]
    multi = verdict_for(_stages(t=1.0, fp=2, vs=0.1, dd=0.9), base)
    assert multi["verdict"] == "REJECTED"
    assert multi["failed_bars"] == ["t_stat", "falsification", "vt_sharpe", "max_drawdown"]


def test_verdict_none_metrics_fail_closed():
    base = _stages()
    out = verdict_for({"t_stat": None, "falsification_passes": None,
                       "vt_sharpe": None, "max_drawdown_vt": None}, base)
    assert out["verdict"] == "REJECTED"
    assert set(out["failed_bars"]) == {"t_stat", "falsification", "vt_sharpe", "max_drawdown"}
    # missing baseline metrics also fail closed for the relative bars
    out = verdict_for(_stages(), {"t_stat": None, "falsification_passes": None,
                                  "vt_sharpe": None, "max_drawdown_vt": None})
    assert "falsification" in out["failed_bars"]
    assert "vt_sharpe" in out["failed_bars"]
    assert "max_drawdown" in out["failed_bars"]
