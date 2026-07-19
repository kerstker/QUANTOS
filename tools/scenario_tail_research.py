"""现实主义引擎 RE-2 应用驱动器:把 carry / crowd_contra 的逐周 book 收益拉过真实历史危机窗口(2021-05 崩盘、
2022-05 LUNA、2022-11 FTX、2023-03 银行/USDC)+ 各自最差回撤窗口,并量化尾部风险(历史 VaR/CVaR、Hill 尾指数、
与 BTC 的下尾依赖=崩盘 beta);补 RE-1 欠的 trend 容量(从 daily 库取 volume)。落 data/cache/scenario_tail_snapshot.json。

诚实预判(数字为准):美元中性 L/S book 对市场普跌大概率免疫(风险是截面价差、非 beta),真正尾部是内生价差爆炸。
纯离线、读本地库、无网络、无真单;研究诊断,不驱动晋级/下单。
"""
from __future__ import annotations

import json
import os
import statistics
import time

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research import alpha_expression as ax
from services.research.realism.capacity_curve import capacity_band
from services.research.realism.scenario import CRYPTO_CRISES, iso_to_ms, max_drawdown_window, window_stats
from services.research.realism.tail_risk import historical_var_cvar, hill_tail_index, lower_tail_dependence
from tools.capacity_research import _AUM_GRID, _COEF_LO_HI, _carry_panel, _weekly_rows
from tools.crypto_carry_strategy import COMPOSITE as CARRY_COMPOSITE, QUANTILE as CARRY_Q, _legs as carry_legs
from tools.crypto_oi_research import build_panel as oi_build_panel, _legs as oi_legs, QUANTILE as OI_Q
from tools.crypto_oi_verify import CROWD_CONTRA

_OUT = os.path.join("data", "cache", "scenario_tail_snapshot.json")
_CACHE = os.path.join("data", "cache")


def _book_series(insts, close, funding, ev, sig, legs_fn, q):
    """逐周 (gross_returns, timestamps, btc_returns):gross=Σ w_i·(close[next]/close[now]-1)(+资金费),
    ts=调仓周,btc=BTC 同期价格收益(市场代理)。"""
    idx = {inst: j for j, inst in enumerate(insts)}
    btc = next((inst for inst in insts if "BTC" in inst.upper()), None)
    bj = idx.get(btc)
    rows = _weekly_rows(ev)
    wk = []
    for r in rows:
        legs = legs_fn(sig[r], q)
        if legs:
            wk.append((r, {insts[i]: w for i, w in legs.items()}))
    gross, ts, btc_ret = [], [], []
    for k in range(len(wk) - 1):
        r, w = wk[k]
        rn = wk[k + 1][0]
        g = 0.0
        for inst, wi in w.items():
            j = idx[inst]
            p0, p1 = close[r][j], close[rn][j]
            if p0 and p1 and p0 > 0:
                g += wi * (p1 / p0 - 1.0)
            if funding is not None:
                fr = funding[r][j]
                if fr is not None:
                    g += -fr * wi
        gross.append(g)
        ts.append(int(ev[r]))
        if bj is not None and close[r][bj] and close[rn][bj] and close[r][bj] > 0:
            btc_ret.append(close[rn][bj] / close[r][bj] - 1.0)
        else:
            btc_ret.append(0.0)
    return gross, ts, btc_ret


def _d(ms):
    return None if ms is None else time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def _analyze(name, gross, ts, btc):
    crises = {}
    for cname, s, e in CRYPTO_CRISES:
        crises[cname] = window_stats(gross, ts, iso_to_ms(s), iso_to_ms(e))
    mdd = max_drawdown_window(gross, ts)
    return {
        "n_periods": len(gross),
        "crises": crises,
        "worst_drawdown_window": {**mdd, "peak_date": _d(mdd["peak_ts"]), "trough_date": _d(mdd["trough_ts"])},
        "var_cvar_95": historical_var_cvar(gross, 0.95),
        "var_cvar_99": historical_var_cvar(gross, 0.99),
        "hill_tail_index": hill_tail_index(gross, 0.10),
        "lower_tail_dep_vs_btc": lower_tail_dependence(gross, btc, 0.10),
    }


def _median_quote_adv(bars, *, volume_unit):
    from services.data.adapters import quote_notional_volume

    notionals = [
        quote_notional_volume(close=bar.close, volume=bar.volume, volume_unit=volume_unit)
        for bar in bars
        if bar.close > 0 and bar.volume > 0
    ]
    return statistics.median(notionals) if notionals else 0.0


def _trend_capacity():
    """补 RE-1 欠的 trend 容量(daily 库取 volume 算 ADV;短样本 ~1 年,标注)。"""
    from tools.coingecko_volume_fetch import MC_SPEC
    from tools.dry_run_trend_multi import _COMPOSITE, _DAILY_DB, _Q, _SNAP, _legs_and_prices  # noqa: F401
    instruments = sorted(c["instrument_id"] for c in json.load(open(_SNAP, encoding="utf-8")).get("meta", {}).get("coins", []))
    store = CachingPITStore(SqlitePITStore(_DAILY_DB, create=False))
    adv = {}
    for inst in instruments:
        adv[inst] = _median_quote_adv(
            store.fetch_bars(inst, 0, 10 ** 14), volume_unit=MC_SPEC.volume_unit,
        )
    event_times = sorted({b.event_time for inst in instruments for b in store.fetch_bars(inst, 0, 10 ** 14)})
    weekly = event_times[::7]
    wk = []
    for t in weekly:
        w, px = _legs_and_prices(store, instruments, t)
        if w:
            wk.append((int(t), w))
    gross, turnover, prevw = [], [], {}
    for k in range(len(wk) - 1):
        t, w = wk[k]
        tn = wk[k + 1][0]
        g = 0.0
        for inst, wi in w.items():
            b0 = store.latest_as_of(inst, t)
            b1 = store.latest_as_of(inst, tn)
            if b0 and b1 and b0.close > 0:
                g += wi * (b1.close / b0.close - 1.0)
        gross.append(g)
        names = set(w) | set(prevw)
        turnover.append([(abs(w.get(n, 0.0) - prevw.get(n, 0.0)), adv.get(n, 0.0)) for n in names])
        prevw = w
    band = capacity_band(gross, turnover, _AUM_GRID, _COEF_LO_HI, max_participation=0.10)
    # Supplement the explicit SourceSpec unit contract with a conservative dispersion guard.
    from services.data.quality import check_volume_dispersion
    advs = [a for a in adv.values() if a > 0]
    spread = (max(advs) / min(advs)) if advs else 0.0
    extreme_dispersion = not check_volume_dispersion(adv, max_ratio=1e6).passed
    out = {"aum_low": band["aum_low"], "aum_high": band["aum_high"],
           "participation_cap_aum": band["participation_cap_aum"], "n_periods": len(gross),
           "adv_max_min_spread": spread,
           "data_quality": "EXTREME_VOLUME_DISPERSION" if extreme_dispersion else "OK"}
    out["note"] = ("trend capacity withheld: quote-volume semantics are consistent, but ADV "
                   "dispersion %.0e exceeds the conservative guard." % spread) if extreme_dispersion \
        else "Short daily sample (~1 year); quote-volume semantics verified; interpret cautiously."
    return out


def _fmt(v):
    if v is None:
        return "> grid"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"${v / 1e3:.0f}k"
    return f"${v:.0f}"


def main() -> None:
    snap = {"provenance": "REAL_HISTORICAL_REPLAY", "crises": [c[0] for c in CRYPTO_CRISES], "books": {}}

    cp = _carry_panel()
    cg, cts, cbtc = _book_series(cp.instruments, cp.matrices["close"], cp.matrices["funding"],
                                 cp.event_times, ax.evaluate(CARRY_COMPOSITE, cp), carry_legs, CARRY_Q)
    snap["books"]["carry"] = _analyze("carry", cg, cts, cbtc)

    op, _ = oi_build_panel()
    og, ots, obtc = _book_series(op.instruments, op.matrices["close"], op.matrices["funding"],
                                 op.event_times, ax.evaluate(CROWD_CONTRA, op), oi_legs, OI_Q)
    snap["books"]["crowd_contra"] = _analyze("crowd_contra", og, ots, obtc)

    snap["trend_capacity"] = _trend_capacity()

    json.dump(snap, open(_OUT, "w", encoding="utf-8"), indent=2, default=str)

    print("=== 历史危机情景重演 + 尾部风险(REAL_HISTORICAL_REPLAY)===", flush=True)
    for name, a in snap["books"].items():
        print(f"\n[{name}] {a['n_periods']} 周", flush=True)
        for cname, ws in a["crises"].items():
            if ws["n_periods"]:
                print(f"  危机 {cname:22s}: 收益 {ws['total_return']*100:+6.1f}% · 窗内回撤 {ws['max_drawdown']*100:4.0f}% "
                      f"· 最差单周 {ws['worst_period']*100:+.1f}% ({ws['n_periods']}周)", flush=True)
            else:
                print(f"  危机 {cname:22s}: 无数据", flush=True)
        w = a["worst_drawdown_window"]
        print(f"  最差回撤窗口: {w['drawdown']*100:.0f}%  {w['peak_date']} → {w['trough_date']} ({w['peak_to_trough_periods']}周)", flush=True)
        print(f"  尾部: CVaR95 {a['var_cvar_95']['cvar']*100:.1f}% · CVaR99 {a['var_cvar_99']['cvar']*100:.1f}% "
              f"· Hill尾指数 {a['hill_tail_index']} · 与BTC下尾依赖(崩盘beta) {a['lower_tail_dep_vs_btc']}", flush=True)
    tc = snap["trend_capacity"]
    if tc["data_quality"] == "EXTREME_VOLUME_DISPERSION":
        print(f"\n[trend] capacity withheld (EXTREME_VOLUME_DISPERSION): {tc['note']}",
              flush=True)
    else:
        print(f"\n[trend] 容量带 {_fmt(tc['aum_low'])} – {_fmt(tc['aum_high'])} (参与率上限 {_fmt(tc['participation_cap_aum'])} · {tc['n_periods']}周,短样本)", flush=True)
    print(f"-> wrote {_OUT}", flush=True)


if __name__ == "__main__":
    main()
