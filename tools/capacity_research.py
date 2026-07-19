"""现实主义引擎 RE-1 应用驱动器:把 carry / crowd_contra 两条 book 的逐周权重 + 每名 ADV + 逐期毛收益(价格
MTM + 资金费,与干跑同法)喂进容量带,回答「每条 edge 能扛多大规模」。落 data/cache/capacity_snapshot.json。

诚实:冲击系数无法校准(L2 无 tick)→ 扫 [5,20]bps 区间给容量带、标 UNCALIBRATED_PRINCIPLED,不报假单点。
trend 用 PIT-store as_of 形态(无 volume 矩阵)且边最弱,本轮不算容量——留作 RE-1 后续(诚实待办)。
纯离线、读本地库、无网络、无真单;研究诊断,不驱动晋级/下单。
"""
from __future__ import annotations

import json
import os
import statistics

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research import alpha_expression as ax
from services.research.realism.capacity_curve import capacity_band
from tools.crypto_carry_strategy import COMPOSITE as CARRY_COMPOSITE, QUANTILE as CARRY_Q, _legs as carry_legs
from tools.crypto_oi_research import build_panel as oi_build_panel, _legs as oi_legs, QUANTILE as OI_Q
from tools.crypto_oi_verify import CROWD_CONTRA

_OUT = os.path.join("data", "cache", "capacity_snapshot.json")
_AUM_GRID = [1e5, 3e5, 1e6, 3e6, 1e7, 3e7, 1e8, 3e8, 1e9, 3e9, 1e10]
_COEF_LO_HI = (5.0, 20.0)          # 保守→激进的临时冲击系数带(bps)
_WEEK_MS = 7 * 24 * 3600 * 1000
_CACHE = os.path.join("data", "cache")


def _weekly_rows(ev):
    rows, last = [], None
    for r in range(len(ev)):
        if last is None or ev[r] - last >= _WEEK_MS:
            rows.append(r)
            last = ev[r]
    return rows


def _adv(insts, close, volume, ev):
    adv = {}
    for j, inst in enumerate(insts):
        dv = [close[r][j] * volume[r][j] for r in range(len(ev))
              if close[r][j] and volume[r][j] and close[r][j] > 0 and volume[r][j] > 0]
        adv[inst] = statistics.median(dv) if dv else 0.0
    return adv


def _book_inputs(insts, close, volume, funding, ev, sig, legs_fn, quantile):
    """(gross_returns, turnover_by_period):逐期毛收益=Σ w_i·(close[next]/close[now]-1)(+资金费),
    换手=|Δw_i| 配 ADV_i。turnover[k] 与 gross[k] 同指向 wk[k] 的 book(对齐)。"""
    idx = {inst: j for j, inst in enumerate(insts)}
    adv = _adv(insts, close, volume, ev)
    rows = _weekly_rows(ev)
    wk = []
    for r in rows:
        legs = legs_fn(sig[r], quantile)
        if legs:
            wk.append((r, {insts[i]: w for i, w in legs.items()}))
    gross, turnover, prevw = [], [], {}
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
                    g += -fr * wi            # 资金费收益,与干跑 cash_flow_fn 同法(用该期费率)
        gross.append(g)
        names = set(w) | set(prevw)
        turnover.append([(abs(w.get(n, 0.0) - prevw.get(n, 0.0)), adv.get(n, 0.0)) for n in names])
        prevw = w
    return gross, turnover


def _carry_panel():
    """复刻 tools/dry_run_carry_multi.py 的流动半宇宙 carry 面板(close+volume+funding)。"""
    instruments = sorted(json.load(open(os.path.join(_CACHE, "crypto_funding_snapshot.json"), encoding="utf-8"))["instruments"])
    store = CachingPITStore(SqlitePITStore(os.path.join(_CACHE, "crypto_funding.db"), create=False))
    funding = {tk: {int(k): v for k, v in d.items()}
               for tk, d in json.load(open(os.path.join(_CACHE, "crypto_funding_rates.json"), encoding="utf-8")).items()}
    liq = {}
    for inst in instruments:
        dv = [b.close * b.volume for b in store.fetch_bars(inst, -10 ** 18, 10 ** 18)
              if b.close > 0 and b.volume > 0]
        liq[inst] = statistics.median(dv) if dv else 0.0
    ranked = sorted(instruments, key=lambda x: liq[x])
    pctile = {inst: (i + 1) / len(ranked) for i, inst in enumerate(ranked)}
    liquid = [inst for inst in instruments if pctile[inst] >= 0.5]
    ets = sorted({b.event_time for inst in liquid for b in store.fetch_bars(inst, -10 ** 18, 10 ** 18)})
    panel = ax.build_field_panel(store, liquid, ets, ("close", "volume"))
    panel.matrices["funding"] = [[funding.get(i, {}).get(t) for i in panel.instruments]
                                 for t in panel.event_times]
    return panel


_PARTICIP_GRID = [0.05, 0.10, 0.20]      # 单日参与率上限
_HORIZON_GRID = [1, 5]                    # 周度调仓铺开执行天数


def _band_for(name, gross, turnover):
    band = capacity_band(gross, turnover, _AUM_GRID, _COEF_LO_HI, ppy=52, sharpe_floor=0.3,
                         max_participation=0.10, volume_dispersion_max_ratio=1e6)
    if band.get("data_quality") == "EXTREME_VOLUME_DISPERSION":
        return {"aum_low": None, "aum_high": None, "impact_aum_low": None, "impact_aum_high": None,
                "participation_cap_aum": None, "n_periods": len(gross),
                "data_quality": "EXTREME_VOLUME_DISPERSION"}
    # 校准:参与率 × 执行铺开天数 敏感性网格 → honest binding 容量随假设怎么动
    sens = {}
    for p in _PARTICIP_GRID:
        for h in _HORIZON_GRID:
            b = capacity_band(gross, turnover, _AUM_GRID, _COEF_LO_HI, ppy=52, sharpe_floor=0.3,
                              max_participation=p, execution_horizon=h)
            sens[f"p{int(p*100)}_h{h}"] = b["aum_low"]
    vals = [v for v in sens.values() if v is not None]
    return {"aum_low": band["aum_low"], "aum_high": band["aum_high"],          # honest binding (含参与率上限)
            "impact_aum_low": band["impact_aum_low"], "impact_aum_high": band["impact_aum_high"],
            "participation_cap_aum": band["participation_cap_aum"], "n_periods": len(gross),
            "gross_sharpe": round(band["curves"]["conservative"][0]["net_sharpe"], 3),
            "binding": ("participation" if band["participation_cap_aum"] is not None and
                        (band["impact_aum_high"] is None or band["participation_cap_aum"] < band["impact_aum_high"])
                        else "impact"),
            "sensitivity_particip_horizon": sens,
            "calibrated_range": {"low": min(vals) if vals else None, "high": max(vals) if vals else None},
            "curve_conservative": band["curves"]["conservative"], "data_quality": "OK"}


def _fmt(v):
    return "> grid" if v is None else (f"${v/1e9:.2f}B" if v >= 1e9 else f"${v/1e6:.1f}M")


def main() -> None:
    snap = {"provenance": "UNCALIBRATED_PRINCIPLED", "aum_grid": _AUM_GRID,
            "impact_coef_lo_hi": _COEF_LO_HI, "sharpe_floor": 0.3, "books": {},
            "note": "冲击系数未校准(L2 无 tick),容量为区间带;trend(PIT as_of 无 volume)本轮未算,留 RE-1 后续。"}

    # carry
    cp = _carry_panel()
    csig = ax.evaluate(CARRY_COMPOSITE, cp)
    cg, ct = _book_inputs(cp.instruments, cp.matrices["close"], cp.matrices["volume"],
                          cp.matrices["funding"], cp.event_times, csig, carry_legs, CARRY_Q)
    snap["books"]["carry"] = _band_for("carry", cg, ct)

    # crowd_contra
    op, _oi = oi_build_panel()
    osig = ax.evaluate(CROWD_CONTRA, op)
    og, ot = _book_inputs(op.instruments, op.matrices["close"], op.matrices["volume"],
                          op.matrices["funding"], op.event_times, osig, oi_legs, OI_Q)
    snap["books"]["crowd_contra"] = _band_for("crowd_contra", og, ot)

    json.dump(snap, open(_OUT, "w", encoding="utf-8"), indent=2, default=str)
    print("=== 容量带(honest binding = min(冲击-夏普, 参与率上限);系数 5–20bps;UNCALIBRATED_PRINCIPLED)===", flush=True)
    for name, b in snap["books"].items():
        if b.get("data_quality") == "EXTREME_VOLUME_DISPERSION":
            print(f"  {name:14s} capacity EXTREME_VOLUME_DISPERSION (estimate withheld) · "
                  f"{b['n_periods']} 周", flush=True)
            continue
        cr = b["calibrated_range"]
        print(f"  {name:14s} 校准区间 {_fmt(cr['low'])} – {_fmt(cr['high'])}(参与率 5–20% × 执行铺开 1–5 天)· "
              f"绑定={b['binding']} · 毛夏普 {b['gross_sharpe']} · {b['n_periods']} 周", flush=True)
        print(f"    {'':14s} 敏感性: " + " ".join(f"{k}={_fmt(v)}" for k, v in b["sensitivity_particip_horizon"].items()), flush=True)
    print("  trend: 本轮未算(PIT as_of 无 volume 矩阵,边最弱)——RE-1 后续。", flush=True)
    print(f"-> wrote {_OUT}", flush=True)


if __name__ == "__main__":
    main()
