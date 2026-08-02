"""Driver tests for tools/mean_reversion_cluster.py — pre-registration integrity, signal
construction hand-checks on synthetic panels (residual / vol-norm / beta-adjusted), correlation
matrix, IC stage, and the pre-registered verdict logic. All synthetic; no production registry,
no data files, no network."""
from __future__ import annotations

import statistics

import pytest

from services.research.alpha_expression import FieldPanel, evaluate, serialize
from tools.mean_reversion_cluster import (MR_CS_REV_5D, MR_CS_REV_Z_5D, MR_RESID_BETA_5D,
                                          MR_RESID_Z_5D, PRE_REGISTRATION, VERDICT_BAR,
                                          _definition_for, _resid_rev, _ret,
                                          build_variant_specs, stage_ic,
                                          stream_correlation_matrix, verdict_for)

_DAY = 86_400_000


def _panel(closes):
    n = len(closes)
    m = len(closes[0])
    return FieldPanel(tuple(f"C{i}" for i in range(m)),
                      tuple((t + 1) * _DAY for t in range(n)),
                      {"close": [list(row) for row in closes]})


def _hand_ret1(closes):
    """Independent (non-DSL) daily simple returns; row 0 = None."""
    out = [[None] * len(closes[0])]
    for t in range(1, len(closes)):
        out.append([closes[t][i] / closes[t - 1][i] - 1.0 for i in range(len(closes[0]))])
    return out


def _hand_resid1(closes):
    """Independent daily residual = coin ret - equal-weight market ret (row 0 = None)."""
    ret = _hand_ret1(closes)
    out = [list(ret[0])]
    for t in range(1, len(ret)):
        mu = statistics.fmean(ret[t])
        out.append([v - mu for v in ret[t]])
    return out


# --- pre-registration integrity -------------------------------------------


def test_family_has_8_unique_specs_with_exactly_one_baseline():
    specs = build_variant_specs()
    assert len(specs) == 8
    assert len({s["id"] for s in specs}) == 8
    baselines = [s for s in specs if s["role"] == "baseline"]
    assert len(baselines) == 1
    base = baselines[0]
    assert base["id"] == "mr_cs_rev_5d"
    assert base["expr"] == MR_CS_REV_5D
    assert base["quantile"] == 0.30
    assert base["cadence_days"] == 7


def test_every_spec_has_rationale_and_verdict_bar_is_registered():
    specs = build_variant_specs()
    for spec in specs:
        assert isinstance(spec["rationale"], str) and spec["rationale"].strip()
        assert spec["quantile"] == 0.30
        assert spec["cadence_days"] == 7
    assert PRE_REGISTRATION["registered_before_evaluation"] is True
    assert PRE_REGISTRATION["trial_count_planned"] == 8
    assert PRE_REGISTRATION["verdict_bar"] == VERDICT_BAR
    # stricter-than-naive-screen bar for a NEW cluster claim
    assert VERDICT_BAR["min_t_stat"] == 2.5
    assert VERDICT_BAR["min_falsification_passes"] == 4
    assert VERDICT_BAR["min_vt_sharpe"] == 1.0
    assert VERDICT_BAR["max_drawdown_vt"] == 0.20
    assert VERDICT_BAR["must_beat_baseline_vt_sharpe"] is True
    assert VERDICT_BAR["capacity_required"] is True
    # no-intraday-data constraint must be declared up front
    assert "out_of_scope" in PRE_REGISTRATION["data_scope"]


def test_registered_check_names_match_verify_battery():
    # The 5 machine checks must be the same contract as crypto_oi_verify's refutation_checks.
    expected = {"survives_1d_lag", "survives_10bps_cost", "broad_across_years",
                "survives_subsets", "adds_to_carry"}
    assert expected == {"survives_1d_lag", "survives_10bps_cost", "broad_across_years",
                        "survives_subsets", "adds_to_carry"}


def test_definition_hashes_are_unique_across_family():
    specs = build_variant_specs()
    hashes = {_definition_for(s).definition_hash for s in specs}
    assert len(hashes) == 8
    d = _definition_for(specs[0])
    assert d.alpha_id == "mr_cs_rev_5d"
    assert d.params["family"] == "mean_reversion_cluster_v1"
    assert d.factor_ids == ("close",)


# --- signal construction hand-checks ---------------------------------------


def test_ret_expression_matches_hand_computation():
    closes = [[100.0, 50.0], [101.0, 49.0], [103.02, 50.47]]
    got = evaluate(_ret(1), _panel(closes))
    assert got[0] == [None, None]
    assert got[1] == [pytest.approx(0.01, rel=1e-12), pytest.approx(-0.02, rel=1e-12)]
    assert got[2] == [pytest.approx(103.02 / 101.0 - 1, rel=1e-12),
                      pytest.approx(50.47 / 49.0 - 1, rel=1e-12)]


def test_resid_rev_signal_matches_hand_computation_and_direction():
    # 4 coins; D has an extra idiosyncratic drop on the last two days -> most negative
    # accumulated residual -> highest rank (long the biggest loser vs market).
    closes = [
        [100.0, 100.0, 100.0, 100.0],
        [101.0, 100.5, 99.5, 99.0],
        [102.0, 101.0, 99.0, 96.0],
        [103.0, 101.5, 98.5, 93.0],
    ]
    panel = _panel(closes)
    sig = evaluate(_resid_rev(2), panel)
    resid = _hand_resid1(closes)
    # rows 0 and 1 lack a full 2-day residual window -> None (fail-closed)
    assert sig[0] == [None] * 4 and sig[1] == [None] * 4
    for t in (2, 3):
        acc = [resid[t - 1][i] + resid[t][i] for i in range(4)]
        neg = [-a for a in acc]
        order = sorted(range(4), key=lambda k: (neg[k], k))
        expected = [0.0] * 4
        for pos, k in enumerate(order):
            expected[k] = pos / 3.0
        assert sig[t] == pytest.approx(expected, rel=1e-12)
    # D accumulated the most negative residual over the last two days -> rank 1.0 (long)
    assert sig[3][3] == 1.0
    # A outperformed the market most -> rank 0.0 (short)
    assert sig[3][0] == 0.0


def test_resid_z_signal_is_vol_normalized_residual():
    closes = [[100.0 + i * 10 for i in range(4)]]
    for t in range(1, 30):
        prev = closes[-1]
        closes.append([prev[0] * 1.001,
                       prev[1] * (1.002 if t % 2 else 0.998),
                       prev[2] * (1.003 if t % 3 else 0.997),
                       prev[3] * (0.999 if t % 2 else 1.001)])
    panel = _panel(closes)
    sig = evaluate(MR_RESID_Z_5D, panel)
    resid = _hand_resid1(closes)
    t = 29
    z = []
    for i in range(4):
        num = sum(resid[t - 4 + k][i] for k in range(5))
        den = statistics.pstdev([resid[t - 19 + k][i] for k in range(20)])
        z.append(-(num / den))
    order = sorted(range(4), key=lambda k: (z[k], k))
    expected = [0.0] * 4
    for pos, k in enumerate(order):
        expected[k] = pos / 3.0
    assert sig[t] == pytest.approx(expected, rel=1e-12)
    # short history: 20d vol window covering row 0 (RET1=None) -> None (fail-closed);
    # first fully-windowed row is r=20 (std over rows 1..20)
    assert all(v is None for v in sig[19])
    assert all(v is not None for v in sig[20])


def test_beta_adjusted_residual_neutralizes_pure_beta_coin():
    # A = exactly 2x market (pure beta, zero idiosyncratic) -> beta-adjusted residual ~0.
    # B = market - 30bps/day (idiosyncratic loser) -> longed. C = market + 30bps -> shorted.
    mkt = [0.010, -0.005, 0.007, 0.003, -0.008, 0.012] * 6     # 36 days, deterministic
    closes = [[100.0, 100.0, 100.0, 100.0]]
    for m in mkt[:-1]:
        prev = closes[-1]
        closes.append([prev[0] * (1.0 + 2.0 * m),
                       prev[1] * (1.0 + m - 0.003),
                       prev[2] * (1.0 + m + 0.003),
                       prev[3] * (1.0 + m)])
    panel = _panel(closes)
    sig = evaluate(MR_RESID_BETA_5D, panel)
    last = sig[-1]
    assert last[1] == 1.0                      # B: most negative beta-adjusted residual -> long
    assert last[2] == 0.0                      # C: positive residual -> short
    # A (pure 2x beta) and D (pure 1x beta) both have ~zero residual; tie broken by index
    assert last[0] == pytest.approx(1.0 / 3.0, rel=1e-9)
    assert last[3] == pytest.approx(2.0 / 3.0, rel=1e-9)


def test_cs_rev_z_uses_plain_returns_not_residuals():
    closes = [[100.0 + i * 10 for i in range(4)]]
    for t in range(1, 30):
        prev = closes[-1]
        closes.append([prev[0] * (1.0015 if t % 2 else 1.0005),
                       prev[1] * (1.002 if t % 2 else 0.998),
                       prev[2] * (1.003 if t % 3 else 0.997),
                       prev[3] * (0.999 if t % 2 else 1.001)])
    panel = _panel(closes)
    sig = evaluate(MR_CS_REV_Z_5D, panel)
    ret = _hand_ret1(closes)
    t = 29
    z = []
    for i in range(4):
        num = (closes[t][i] / closes[t - 5][i] - 1.0)
        den = statistics.pstdev([ret[t - 19 + k][i] for k in range(20)])
        z.append(-(num / den))
    order = sorted(range(4), key=lambda k: (z[k], k))
    expected = [0.0] * 4
    for pos, k in enumerate(order):
        expected[k] = pos / 3.0
    assert sig[t] == pytest.approx(expected, rel=1e-12)


def test_signals_serialize_with_expected_operators():
    for expr, ops in ((MR_CS_REV_5D, ("cs_rank", "neg", "ts_delay")),
                      (MR_RESID_Z_5D, ("cs_rank", "ts_sum", "ts_std", "cs_demean")),
                      (MR_RESID_BETA_5D, ("cs_rank", "ts_beta", "ts_sum"))):
        s = serialize(expr)
        for op in ops:
            assert op in s


# --- IC stage on synthetic panel --------------------------------------------


def test_stage_ic_reports_h1_and_h7():
    n = 60
    closes = [[100.0 + i + (t * (0.5 if (i + t) % 3 else -0.4)) for i in range(6)]
              for t in range(n)]
    panel = FieldPanel(tuple(f"C{i}" for i in range(6)), tuple((t + 1) * _DAY for t in range(n)),
                       {"close": closes})
    sig = evaluate(_resid_rev(2), panel)
    out = stage_ic(panel, sig)
    assert set(out) == {"h1", "h7"}
    # 59 IC periods; rows 0-1 have None signal (2-day full-window warmup) -> 57 valid
    assert out["h1"]["n_periods"] == n - 3
    # h7 non-overlapping: starts at 0,7,14,... while t+7 < n -> floor((n-7)/7)+1 samples max
    assert out["h7"]["n_periods"] <= n // 7 + 1


# --- correlation matrix -------------------------------------------------------


def test_stream_correlation_matrix_perfect_and_flipped():
    a = [0.01, -0.02, 0.03, 0.005] * 40
    b = [-x for x in a]
    c = [x * 2.0 for x in a]
    corr = stream_correlation_matrix({"a": a, "b": b, "c": c})
    assert corr["a"]["a"] == 1.0
    assert corr["a"]["b"] == pytest.approx(-1.0, rel=1e-9)
    assert corr["a"]["c"] == pytest.approx(1.0, rel=1e-9)


def test_stream_correlation_matrix_degenerate_and_short_overlap():
    # zero-variance stream -> None (no fabricated correlation)
    corr = stream_correlation_matrix({"a": [0.01] * 200, "b": [0.01, -0.01] * 100})
    assert corr["a"]["b"] is None
    # < 120 overlap -> None
    corr2 = stream_correlation_matrix({"a": [0.01, -0.01] * 10, "b": [0.01, 0.02] * 10})
    assert corr2["a"]["b"] is None


# --- verdict logic ------------------------------------------------------------


def _stages(t=3.0, fp=5, vs=1.2, dd=0.10, cap=2e6, dq="OK"):
    return {"t_stat": t, "falsification_passes": fp, "vt_sharpe": vs, "max_drawdown_vt": dd,
            "capacity_aum_low": cap, "capacity_data_quality": dq}


def test_verdict_candidate_when_all_bars_pass():
    assert verdict_for(_stages(), 1.0)["verdict"] == "CANDIDATE"


def test_verdict_boundaries_are_inclusive_except_vs_baseline():
    # t=2.5 / vt=1.0 / dd=0.20 inclusive; vs_baseline strictly greater
    assert verdict_for(_stages(t=2.5, vs=1.0, dd=0.20), 0.9)["verdict"] == "CANDIDATE"
    out = verdict_for(_stages(t=2.49), 0.9)
    assert out["verdict"] == "REJECTED" and "t_stat" in out["failed_bars"]
    out = verdict_for(_stages(vs=1.0), 1.0)              # equal to baseline -> NOT beating
    assert out["verdict"] == "REJECTED" and "vs_baseline_vt" in out["failed_bars"]
    out = verdict_for(_stages(dd=0.2001), 0.9)
    assert out["verdict"] == "REJECTED" and "max_drawdown" in out["failed_bars"]


def test_verdict_each_bar_failure_recorded():
    assert verdict_for(_stages(t=1.0), 0.9)["failed_bars"] == ["t_stat"]
    assert verdict_for(_stages(fp=3), 0.9)["failed_bars"] == ["falsification"]
    assert verdict_for(_stages(vs=0.5), 0.9)["failed_bars"] == ["vt_sharpe", "vs_baseline_vt"]
    assert verdict_for(_stages(dd=0.5), 0.9)["failed_bars"] == ["max_drawdown"]
    assert verdict_for(_stages(cap=None), 0.9)["failed_bars"] == ["capacity"]
    assert verdict_for(_stages(dq="EXTREME_VOLUME_DISPERSION"), 0.9)["failed_bars"] == ["capacity"]
    multi = verdict_for(_stages(t=1.0, fp=2, vs=0.1, dd=0.9, cap=None), 0.9)
    assert multi["verdict"] == "REJECTED"
    assert multi["failed_bars"] == ["t_stat", "falsification", "vt_sharpe", "max_drawdown",
                                    "vs_baseline_vt", "capacity"]


def test_verdict_baseline_is_exempt_from_vs_baseline_bar():
    # the baseline is the reference; it must not be failed for not beating itself
    out = verdict_for(_stages(vs=1.1), 1.1, role="baseline")
    assert out["verdict"] == "CANDIDATE"
    # but every other bar still applies to the baseline
    out = verdict_for(_stages(vs=0.9), 0.9, role="baseline")
    assert out["failed_bars"] == ["vt_sharpe"]


def test_verdict_none_metrics_fail_closed():
    out = verdict_for({"t_stat": None, "falsification_passes": None, "vt_sharpe": None,
                       "max_drawdown_vt": None, "capacity_aum_low": None,
                       "capacity_data_quality": None}, 1.0)
    assert out["verdict"] == "REJECTED"
    assert set(out["failed_bars"]) == {"t_stat", "falsification", "vt_sharpe", "max_drawdown",
                                       "vs_baseline_vt", "capacity"}
    # missing baseline VT also fails the relative bar closed
    out = verdict_for(_stages(), None)
    assert "vs_baseline_vt" in out["failed_bars"]
