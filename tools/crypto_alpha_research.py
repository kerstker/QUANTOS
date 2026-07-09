"""Out-of-gate, OFFLINE cross-sectional alpha research on the MULTI-YEAR data.

Uses the new alpha-expression engine (services.research.alpha_expression) to evaluate a curated set of
REAL cross-sectional alphas — Alpha101-style compositions of time-series + cross-sectional operators
that the old per-instrument SIGNAL_OPS could NOT express — over the already-ingested 5.5-year crypto
panel (data/cache/crypto_history.db; CachingPITStore -> fast). For each alpha: the contained
ResearchAgentLoop verdict (DSR/IC, agent can never reach APPROVED) + full-sample net Sharpe + the
per-CALENDAR-YEAR net Sharpe (the cross-regime test incl. the 2022 bear). Answers: do RICHER factors
find a cross-regime edge the simple 8 could not? Records/promotes/fetches nothing.
"""
from __future__ import annotations

import math
import statistics
import time
from collections import defaultdict

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe
from services.alpha.panel_backtest import run_panel_backtest
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.intelligence.agent_loop import ResearchAgentLoop
from quantos.contracts.alpha import AlphaDefinition

import os
_DB = os.path.join("data", "cache", "crypto_history.db")
FWD = 1

C = ("field", "close")
V = ("field", "volume")
def _ret(d): return ("div", ("ts_delta", C, d), ("ts_delay", C, d))   # d-bar % return

# curated REAL cross-sectional alphas (none expressible with the old per-instrument grammar)
ALPHAS = {
    "xs_mom_5":       ("cs_rank", ("ts_delta", C, 5)),
    "xs_mom_20":      ("cs_rank", ("ts_delta", C, 20)),
    "st_reversal":    ("cs_rank", ("neg", ("ts_delta", C, 1))),
    "pv_corr_10":     ("neg", ("cs_rank", ("ts_corr", C, V, 10))),
    "norm_mom_10":    ("cs_rank", _ret(10)),
    "low_vol_20":     ("neg", ("cs_rank", ("ts_std", _ret(1), 20))),
    "decay_mom_10":   ("cs_zscore", ("decay_linear", ("ts_delta", C, 1), 10)),
    "vol_mom_5":      ("cs_rank", ("ts_delta", V, 5)),
    "dist_high_cs20": ("cs_rank", ("div", C, ("ts_max", C, 20))),
    "accel_cs":       ("cs_rank", ("sub", ("ts_delta", C, 5), ("ts_delay", ("ts_delta", C, 5), 5))),
    "argmax_cs20":    ("cs_rank", ("ts_argmax", C, 20)),
    "risk_adj_mom":   ("cs_rank", ("div", ("ts_delta", C, 10), ("ts_std", C, 10))),
    "pv_div_5":       ("cs_rank", ("mul", ("ts_delta", C, 5), ("ts_delta", V, 5))),
    "meanrev_z10":    ("neg", ("cs_zscore", ("div", ("sub", C, ("ts_mean", C, 10)), ("ts_std", C, 10)))),
    "amihud_illiq":   ("cs_rank", ("div", ("abs", _ret(1)), V)),
}


def _periods_timed(alpha, panel):
    px = panel.matrices["close"]
    T, N = len(alpha), len(alpha[0]) if alpha else 0
    periods, times = [], []
    for r in range(T - FWD):
        rf = r + FWD
        insts, sigs, fwds = [], [], []
        for i in range(N):
            a, p0, p1 = alpha[r][i], px[r][i], px[rf][i]
            if a is None or not math.isfinite(a) or p0 is None or p1 is None or p0 <= 0:
                continue
            insts.append(panel.instruments[i])
            sigs.append(float(a))
            fwds.append(p1 / p0 - 1.0)
        if len(insts) >= 2:
            from services.alpha.panel_backtest import PanelPeriod
            periods.append(PanelPeriod(tuple(insts), tuple(sigs), tuple(fwds),
                                       event_time=int(panel.event_times[r])))
            times.append(panel.event_times[r])
    return periods, times


def _per_year(times, ret):
    by = defaultdict(list)
    for t, r in zip(times, ret):
        by[time.gmtime(t / 1000).tm_year].append(r)
    return {y: round(sharpe(by[y]), 2) for y in sorted(by) if len(by[y]) > 1}


def main() -> None:
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    instruments = store.keys()
    event_times = sorted({b.event_time for inst in instruments for b in store.fetch_bars(inst, -10**18, 10**18)})
    print(f"cross-sectional alpha research: {len(instruments)} coins x {len(event_times)} daily periods")
    t0 = time.perf_counter()
    panel = ax.build_field_panel(store, instruments, event_times, ("close", "volume"))
    print(f"field panel built in {time.perf_counter()-t0:.1f}s\n")

    catalog = AlphaCatalog()
    loop = ResearchAgentLoop(catalog=catalog, config=AlphaFactoryConfig(compute_regime_evidence=True))
    rows = []
    for name, expr in ALPHAS.items():
        alpha = ax.evaluate(expr, panel)
        periods, times = _periods_timed(alpha, panel)
        if len(periods) < 24:
            print(f"{name:15s} too few periods"); continue
        ret = list(run_panel_backtest(periods).period_returns)
        full = round(sharpe(ret), 3)
        py = _per_year(times, ret)
        pos_years = sum(1 for v in py.values() if v > 0)
        defn = AlphaDefinition(alpha_id=ax.alpha_id(expr), name=name, factor_ids=("close",),
                               params={"family": "cs_alpha", "expr": ax.serialize(expr)})
        out = loop.run([(defn, periods)], timestamp=0)
        ev = next(iter(out["report"].values())).get("evaluation")
        rows.append((name, full, ev.deflated_sharpe if ev else None, ev.ic_mean if ev else None,
                     pos_years, len(py), py, ev.verdict.value if ev else "n/a"))

    rows.sort(key=lambda r: r[1], reverse=True)
    auto = sum(1 for aid in catalog.all_ids() if str(catalog.state(aid).value) == "APPROVED")
    print(f"=== {len(rows)} cross-sectional alphas (full 5.5y) · auto_approved={auto} (must be 0) ===")
    print(f"{'alpha':15s} {'fullSR':>7} {'DSR':>7} {'IC':>8} {'+yrs':>6}   per-year")
    for name, full, dsr, ic, posy, ny, py, verdict in rows:
        dsr_s = f"{dsr:.3f}" if dsr is not None else "n/a"
        ic_s = f"{ic:.4f}" if ic is not None else "n/a"
        pys = " ".join(f"{y}:{v:+.1f}" for y, v in py.items())
        print(f"{name:15s} {full:>7.3f} {dsr_s:>7} {ic_s:>8} {posy:>3}/{ny:<2}  {pys}")
    survivors = [r for r in rows if r[4] >= max(1, r[5] - 1) and r[1] > 0]   # +ve in all-but-one year
    print(f"\ncross-regime survivors (full-sample +ve AND positive in all-but-one year): "
          f"{[r[0] for r in survivors] or 'NONE — richer factors still found no cross-regime edge'}")


if __name__ == "__main__":
    main()
