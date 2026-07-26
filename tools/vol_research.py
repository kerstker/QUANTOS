"""波动率数据类研究驱动器(第四数据类 options/vol 的首个诊断)—— 落 evidence/vol_research.json。

三个问题:
1. **时序预测筛选**(BTC/ETH):DVOL 因子族(level / 7d change / IV−RV spread / 30d z-score)
   + 期限结构(年化基差 / 斜率) vs 同一标的的 1d/7d 前向对数收益。Pearson/Spearman IC、
   t-stat、分年 IC;n<10 全 None(与 services/research/ic_panel.py 同一小样本诚实口径)。
   **对齐规则(无前视)**:因子 = t 日收盘值(DVOL 日线与标的价格日线同为 UTC EOD);
   前向收益 = log(close[t+h]/close[t])——t 收盘同时是因子观测点与收益起点(EOD 信号
   标准约定:t 收盘决策,t→t+h 持有);7d 用**非重叠**抽样(stride 7,重叠会让 t-stat
   虚高;相位固定 0,确定性、已知局限,与 ic_panel.compute_horizon_ic_series 同法)。
   标的价格读 data/cache/crypto_funding.db(Binance USD-M perp mark 日线,2020-06+,
   覆盖 DVOL 全历史;crypto_daily.db 只 2025-06+,深度不够,诚实替换并标注)。
2. **regime overlay**:carry / crowd_contra 逐周 gross 收益(与 tools/macro_regime_research.py
   完全同一数据访问)按 BTC DVOL 全样本三分位分桶——回答「既有 crypto 边有没有 DVOL-regime
   依赖」(对照 VIX 发现:carry 爱平静、crowd 爱压力)。全样本分位 = 回顾性描述,非 PIT 择时。
3. **诚实块**:实际数据深度、skew 无免费历史、基差深度、多重比较与全样本分位告诫。

纯离线读本地库(网络在 tools/deribit_fetch.py,门外);PIT 读走 fetch_bars_pit/as_of,
零裸读;研究诊断,不驱动晋级/下单,不是门禁。
"""
from __future__ import annotations

import math
import os
import time

from services.data.persistent_store import SqlitePITStore
from services.research import alpha_expression as ax
from services.research import vol_factors as vf
from services.research.macro_regime import bucket_returns, tercile_labels
from tools.cache_io import atomic_write_json

_DERIBIT_DB = os.path.join("data", "cache", "deribit_vol.db")
_PX_DB = os.path.join("data", "cache", "crypto_funding.db")
_OUT = os.path.join("evidence", "vol_research.json")
_DAY = 86_400_000

# 筛选因子窗口(日)
_RV_WINDOW = 30
_Z_WINDOW = 30
_CHG_N = 7
# 期限结构选取规则:近端 dte>=14 天的最近到期;远端 = dte 比近端再大 >=21 天的最近到期
_NEAR_MIN_DTE = 14
_FAR_MIN_GAP = 21


# --- 数据装载(PIT 安全读;各自在工具内测过)---

def _series_from_store(store, key):
    """fetch_bars_pit(kt<=et 过滤;本驱动只把每根 bar 用在它自己的 event_date 上——
    正是 fetch_bars_pit 的 PIT 安全消费形态)-> (times, closes) 升序。"""
    bars = store.fetch_bars_pit(key, -10 ** 18, 10 ** 18)
    times = [int(b.event_time) for b in bars]
    closes = [float(b.close) for b in bars]
    return times, closes


def _map_by_day(times, values):
    return {int(t): v for t, v in zip(times, values)}


# --- 纯计算(可测)---

def _fwd_log_return(px: dict, t: int, h: int):
    """log(close[t+h]/close[t]);任一端缺失/非正 → None。"""
    p0, p1 = px.get(t), px.get(t + h * _DAY)
    if p0 is None or p1 is None or p0 <= 0 or p1 <= 0:
        return None
    return math.log(p1 / p0)


def screen_factor(grid, factor_map, px: dict, h: int, event_times=None):
    """单因子时序筛选:grid 日 t 的因子 vs t→t+h 前向对数收益。
    h>1 → 非重叠抽样(stride h)。返回 ts_ic_stats + per_year_ics + 覆盖日数。"""
    stride = h if h > 1 else 1
    pairs, times = [], []
    for t in grid[::stride]:
        f = factor_map.get(t)
        r = _fwd_log_return(px, t, h)
        pairs.append((f, r))
        times.append(t)
    stats = vf.ts_ic_stats(pairs)
    per_year = vf.per_year_ics(pairs, times)
    return {**stats, "per_year": per_year, "horizon_days": h}


def build_term_series(grid, perp_map: dict, dated: list):
    """期限结构时序(纯函数):逐日挑近端/远端到期合约,算年化基差与斜率。
    dated = [(expiry_day_ms, {day: close}), ...]。返回 {t: {"basis","slope","near","far"}}。"""
    out = {}
    for t in grid:
        perp = perp_map.get(t)
        if perp is None or perp <= 0:
            continue
        cands = []
        for expiry_ms, closes in dated:
            dte = (expiry_ms - t) / _DAY
            c = closes.get(t)
            if c is not None and c > 0 and dte > 0:
                cands.append((dte, expiry_ms, c))
        cands.sort()
        near = next((c for c in cands if c[0] >= _NEAR_MIN_DTE), None)
        if near is None:
            continue
        far = next((c for c in cands if c[0] >= near[0] + _FAR_MIN_GAP), None)
        basis = vf.annualized_basis(perp, near[2], near[0])
        slope = (vf.term_structure_slope(perp, near[2], far[2], near[0], far[0])
                 if far is not None else None)
        out[t] = {"basis": basis, "slope": slope,
                  "near": near[1], "far": far[1] if far else None,
                  "dte_near": round(near[0], 1),
                  "dte_far": round(far[0], 1) if far else None}
    return out


def factor_series_for_underlying(grid, dvol_map, px_map, perp_map, dated):
    """拼出某个标的的全部筛选因子序列:{factor_name: {day: value}}。纯函数。"""
    dvol = [dvol_map.get(t) for t in grid]
    closes = [px_map.get(t) for t in grid]
    chg = vf.dvol_change(dvol, _CHG_N)
    spread = vf.iv_rv_spread(dvol, closes, _RV_WINDOW)
    z = vf.zscore(dvol, _Z_WINDOW)
    term = build_term_series(grid, perp_map, dated)
    return {
        "dvol_level": {t: (None if v is None else v / 100.0) for t, v in zip(grid, dvol)},
        "dvol_change_7d": dict(zip(grid, chg)),
        "iv_rv_spread_30d": dict(zip(grid, spread)),
        "dvol_zscore_30d": dict(zip(grid, z)),
        "fut_basis_ann": {t: term[t]["basis"] for t in term},
        "fut_term_slope": {t: term[t]["slope"] for t in term if term[t]["slope"] is not None},
    }


# --- regime overlay(与 macro_regime_research 同一数据访问)---

def _book_weekly(name):
    """(gross_returns, week_ts):carry / crowd_contra 逐周 gross,复用 scenario_tail 的路径。"""
    from tools.scenario_tail_research import _book_series
    from tools.capacity_research import _carry_panel
    from tools.crypto_carry_strategy import (COMPOSITE as CARRY_COMPOSITE,
                                             QUANTILE as CARRY_Q, _legs as carry_legs)
    from tools.crypto_oi_research import (QUANTILE as OI_Q, _legs as oi_legs,
                                          build_panel as oi_build_panel)
    from tools.crypto_oi_verify import CROWD_CONTRA
    if name == "carry":
        panel = _carry_panel()
        sig = ax.evaluate(CARRY_COMPOSITE, panel)
        gross, ts, _ = _book_series(panel.instruments, panel.matrices["close"],
                                    panel.matrices["funding"], panel.event_times,
                                    sig, carry_legs, CARRY_Q)
        return gross, ts
    panel, _ = oi_build_panel()
    sig = ax.evaluate(CROWD_CONTRA, panel)
    gross, ts, _ = _book_series(panel.instruments, panel.matrices["close"],
                                panel.matrices["funding"], panel.event_times,
                                sig, oi_legs, OI_Q)
    return gross, ts


def _dvol_labels_for_weeks(dvol_map, week_ts):
    """逐调仓周取该周可知的最新 DVOL(<= 当周),全样本三分位(回顾性,与 FRED 工作同口径)。"""
    days = sorted(dvol_map)
    vals = []
    for ts in week_ts:
        day = (int(ts) // _DAY) * _DAY
        v = None
        while day >= days[0]:
            if day in dvol_map:
                v = dvol_map[day]
                break
            day -= _DAY
        vals.append(v)
    return tercile_labels(vals), vals


def main() -> None:
    dvol_store = SqlitePITStore(_DERIBIT_DB, create=False)
    px_store = SqlitePITStore(_PX_DB, create=False)

    snap = {"provenance": "REAL_DERIBIT_VOL_RESEARCH", "generated_utc":
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "alignment_rule": ("factor = day-t UTC close; forward return = log(close[t+h]/close[t]); "
                               "h=7 非重叠抽样(stride 7,相位固定 0);因子与收益只共享 t 收盘边界"),
            "screen": {}, "regime": {}, "honesty": {}}

    # 1) 时序预测筛选
    for ccy in ("BTC", "ETH"):
        dt_, dc = _series_from_store(dvol_store, f"DVOL-{ccy}")
        pt_, pc = _series_from_store(px_store, ccy)
        dvol_map, px_map = _map_by_day(dt_, dc), _map_by_day(pt_, pc)
        grid = [t for t in dt_ if t in px_map]
        perp_map = _map_by_day(*_series_from_store(dvol_store, f"{ccy}-PERPETUAL"))
        dated = []
        for bar_key in _instrument_keys(_DERIBIT_DB, prefix=f"{ccy}-"):
            if bar_key in (f"{ccy}-PERPETUAL", f"DVOL-{ccy}"):
                continue
            expiry_ms = _expiry_from_key(bar_key)
            if expiry_ms is None:
                continue
            dated.append((expiry_ms, _map_by_day(*_series_from_store(dvol_store, bar_key))))
        factors = factor_series_for_underlying(grid, dvol_map, px_map, perp_map, dated)
        res = {}
        for name, fmap in factors.items():
            res[name] = {f"h{h}": screen_factor(grid, fmap, px_map, h) for h in (1, 7)}
        term_days = sum(1 for t in grid if factors["fut_basis_ann"].get(t) is not None)
        snap["screen"][ccy] = {"factors": res, "grid_days": len(grid),
                               "grid_span": [_d(grid[0]), _d(grid[-1])] if grid else None,
                               "term_structure_days": term_days,
                               "dated_instruments": len(dated)}
        print(f"[{ccy}] grid {len(grid)} days [{snap['screen'][ccy]['grid_span'][0]} .. "
              f"{snap['screen'][ccy]['grid_span'][1]}], dated futures: {len(dated)}", flush=True)
        for name, r in res.items():
            h1, h7 = r["h1"], r["h7"]
            print(f"  {name:18s} h1: spear {_fmt(h1['spearman'])} t {_fmt(h1['t_stat'])} "
                  f"(n{h1['n']}) | h7: spear {_fmt(h7['spearman'])} t {_fmt(h7['t_stat'])} "
                  f"(n{h7['n']})", flush=True)

    # 2) regime overlay(BTC DVOL 三分位 × 逐周 gross)
    dvol_t, dvol_c = _series_from_store(dvol_store, "DVOL-BTC")
    dvol_map = _map_by_day(dvol_t, dvol_c)
    for book in ("carry", "crowd_contra"):
        gross, ts = _book_weekly(book)
        labels, vals = _dvol_labels_for_weeks(dvol_map, ts)
        buckets = bucket_returns(gross, labels)
        covered = sum(v["n"] for v in buckets.values())
        snap["regime"][book] = {"dim": "dvol_BTC_tercile", "buckets": buckets,
                                "n_periods": len(gross), "covered_periods": covered}
        parts = " · ".join(f"{lb}: SR {b['ann_sharpe']:+.2f}(n{b['n']})"
                           for lb, b in sorted(buckets.items()))
        print(f"[regime {book}] {parts} [覆盖 {covered}/{len(gross)}]", flush=True)

    # 3) 诚实块(数据深度读**库内实际存贮**,非最近一次增量抓取的快照)
    snap["honesty"] = {
        "data_depth": _stored_depth(_DERIBIT_DB),
        "skew_history": ("免费 skew 历史不存在;25Δ skew 从首次抓取日(见 "
                         "data/cache/deribit_options_skew.json)起向前积累——本轮研究不含 skew 因子。"),
        "basis_depth": ("已结算期货历史由合约名探测重建(get_instruments?expired=true 已不回全量"
                        "名单;已知名字的已结算合约仍给全量日线,Phase 0 实测 2026-07-24);"
                        "覆盖 2021-01 起每月度合约,深度以 data_depth 实际为准。"),
        "retrospective_quantile": ("regime 标签 = 全样本三分位(回顾性描述,非 PIT 可交易择时);"
                                   "IC 为全样本初筛诊断,多重比较未校正(6 因子 × 2 标的 × 2 horizon "
                                   "= 24 次检验,|t| 门槛应按族膨胀理解)。"),
        "price_source": ("标的价格 = data/cache/crypto_funding.db(Binance USD-M perp mark 日线,"
                         "2020-06+,与 carry/crowd 研究同一价格口径);crypto_daily.db 仅 2025-06+ "
                         "深度不够,未用。"),
        "not_a_gate": "初筛诊断,不碰 evaluator/AlphaCatalog,不驱动任何晋级/下单。",
    }
    atomic_write_json(_OUT, snap, indent=1)
    print(f"-> wrote {_OUT}", flush=True)


def _instrument_keys(db_path, prefix):
    import sqlite3
    con = sqlite3.connect(db_path)
    try:
        return [r[0] for r in con.execute(
            "SELECT DISTINCT key FROM bars WHERE key LIKE ? ORDER BY key", (prefix + "%",))]
    finally:
        con.close()


def _stored_depth(db_path):
    """库内实际存贮深度:{key: [first_date, last_date, rows]}(直接 SQL MIN/MAX/COUNT,
    与 price_watermarks 同模式;反映全量历史,非最近一次增量抓取)。"""
    import sqlite3
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT key, MIN(event_time), MAX(event_time), COUNT(*) "
            "FROM bars GROUP BY key ORDER BY key").fetchall()
    finally:
        con.close()
    return {k: [_d(mn), _d(mx), c] for k, mn, mx, c in rows}


def _expiry_from_key(key):
    from tools.deribit_fetch import parse_instrument_name
    parsed = parse_instrument_name(key)
    return parsed[1] if parsed else None


def _d(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def _fmt(v):
    return f"{v:+.3f}" if v is not None else "  —  "


if __name__ == "__main__":
    main()
