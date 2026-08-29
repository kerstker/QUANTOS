"""W3 公共风险分解驱动器(spec §7):策略簇 & 共同风险分解 → evidence/common_risk_decomposition.json。

读 dry_run.db 的 canonical 干跑曲线(carry=carry_oi_majors / crowd_contra / top_contra /
btc_spot_trend + 基准 btc_spot_trend_hold=买入持有 BTC,验证因子模型应 R²≈1/残差≈0),
从既有本地数据构造 §7.1 因子库子集(crypto_funding.db 价格按 t+1d 显式可知时间读取 +
crypto_funding_rates.json + crypto_oi_metrics.json),过 services/research/factor_decomp.py
纯核:OLS 因子模型 → 残差 → 原始/残差双相关矩阵 → complete-linkage 聚类 → 簇级组合输出
(effective breadth)→ 26 周滚动相关冲击。

纯离线读本地库、无网络、无真单;Binance 旧 open-time 行显式拒绝。诊断非门禁。
"""
from __future__ import annotations

import json
import math
import os
import statistics

from services.data.caching_store import CachingPITStore
from services.data.available_time import fetch_bars_for_event_decisions
from services.data.persistent_store import SqlitePITStore
from services.execution.backtest_authority import quarantine_legacy_backtest
from services.execution.dry_run_store import DryRunStore
from services.research import factor_decomp as fd
from services.research.book_combine import combine_returns, inverse_vol_weights

_CACHE = os.path.join("data", "cache")
_DRY_RUN_DB = os.path.join(_CACHE, "dry_run.db")
_PRICE_DB = os.path.join(_CACHE, "crypto_funding.db")
_FUNDING_JSON = os.path.join(_CACHE, "crypto_funding_rates.json")
_OI_JSON = os.path.join(_CACHE, "crypto_oi_metrics.json")
_OUT = os.path.join("evidence", "common_risk_decomposition.json")

# 组合 book(§7.3 要求 carry_oi_majors/crowd_contra/top_contra;btc_spot_trend 为第 4 条干跑曲线)。
# (dry_run.db strategy_id, 展示名)
BOOK = (("carry", "carry_oi_majors"), ("crowd_contra", "crowd_contra"),
        ("top_contra", "top_contra"), ("btc_spot_trend", "btc_spot_trend"))
# 验证基准:等权主流币篮子(由价格库合成,非干跑策略)——应被市场因子解释(R2 高)、
# beta_BTC 显著、alpha≈0。注:btc_spot_trend_hold 干跑曲线不是干净买入持有(信号门控,
# 窗口内 119/216 周空仓零收益),不能当基准 —— 这是首跑实测发现,诚实记录。

# 因子宇宙:OI 主流 14 币(与 crowd_contra 宇宙同口径),要求价格/资金费/OI 三源俱在。
FACTOR_UNIVERSE = ("BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE",
                   "AVAX", "LINK", "DOT", "LTC", "BCH", "TRX", "ATOM")

_RHO_CUT = 0.5            # UNCALIBRATED 工程默认:|ρ|≥0.5 才视为同一底层敞口
_RHO_SENSITIVITY = (0.3, 0.7)
_SHOCK_WINDOW = 26        # 半年滚动窗
_SHOCK_MARGIN = 0.25      # UNCALIBRATED:rolling mean|ρ| > 全样本 mean|ρ| + 0.25 → 冲击

MISSING_FACTORS = [
    {"factor": "liquidation_cascade", "status": "MISSING_NO_LOCAL_DATA",
     "note": "本地无清算/强平数据;§7.1 流动性螺旋维度的关键缺口"},
    {"factor": "amihud_illiquidity", "status": "PROXIED",
     "note": "以 liquidity_volume_change(美元成交量周环比)代理;经典 Amihud |r|/volume 未建"},
    {"factor": "options_skew", "status": "MISSING_HISTORY_TOO_SHORT",
     "note": "Deribit 25Δ skew 快照自 2026-07-24 向前积累,免费历史不存在"},
    {"factor": "onchain", "status": "MISSING_NO_LOCAL_DATA", "note": "无链上数据"},
    {"factor": "cross_asset", "status": "MISSING_SOURCE_BLOCKED",
     "note": "股票/黄金免费源本环境被墙(Stooq bot-wall/Yahoo 429,不绕过);FRED 宏观为 regime 级而非资产收益"},
]


# ---------------------------------------------------------------------------
# 数据组装(driver 侧;每周聚合逻辑为纯函数,可合成数据测试)
# ---------------------------------------------------------------------------

def canonical_runs(records: list) -> dict:
    """每个 strategy_id 取 all_runs() 顺序里的最后一条(与 console「latest」同语义),确定性。"""
    out = {}
    for rec in records:
        out[rec["strategy_id"]] = rec
    return out


def curve_series(record: dict, key: str = "equity_curve") -> tuple:
    """dry_run detail 的净值曲线 → (rets, ts_ms)。key=equity_curve(raw)或 equity_curve_vt。"""
    curve = record.get("detail", {}).get(key) or []
    rets = [float(p["ret"]) for p in curve]
    ts = [int(p["t"]) for p in curve]
    return rets, ts


def weekly_price_agg(bars: list) -> dict:
    """日线 bars → {week: {"close": 周末收盘, "dollar_vol": 周内美元成交量和, "daily_ret": [日内收益]}}。
    crypto_funding.db 的 volume 是 base 单位 → 美元量 = close×volume(与 capacity_research 同口径;
    勿与 crypto_daily.db 的 USD 名义 volume 混用)。"""
    by_day = sorted((b.event_time, float(b.close), float(b.volume)) for b in bars)
    weeks: dict = {}
    prev_close = None
    for t, close, vol in by_day:
        w = fd.iso_week(t)
        slot = weeks.setdefault(w, {"close": None, "dollar_vol": 0.0, "daily_ret": [], "_t": -1})
        if t >= slot["_t"]:                       # 周末收盘 = 周内最大 event_time 的 close
            slot["_t"], slot["close"] = t, close
        slot["dollar_vol"] += close * vol
        if prev_close is not None and prev_close > 0:
            slot["daily_ret"].append(close / prev_close - 1.0)
        prev_close = close
    return {w: {"close": s["close"], "dollar_vol": s["dollar_vol"], "daily_ret": s["daily_ret"]}
            for w, s in weeks.items()}


def weekly_funding(coin_series: dict) -> dict:
    """crypto_funding_rates.json 单币 {ts_ms_str: daily_funding} → {week: 周累计资金费}。"""
    out: dict = {}
    for k, v in coin_series.items():
        if v is None:
            continue
        w = fd.iso_week(int(k))
        out[w] = out.get(w, 0.0) + float(v)
    return out


def weekly_oi(coin_daily: dict, closes_by_day: dict) -> dict:
    """OI 日度 {ts: {oi, oi_value, global_ls}} → {week: {"gls": 周均, "oi_value": 周均, "basis": 周均}}。
    perp_mark = oi_value/oi;basis = perp/spot_close − 1(同 crypto_oi_research add_basis 口径)。"""
    acc: dict = {}
    for k, rec in coin_daily.items():
        if not isinstance(rec, dict):
            continue
        w = fd.iso_week(int(k))
        slot = acc.setdefault(w, {"gls": [], "oi_value": [], "basis": []})
        if rec.get("global_ls") is not None:
            slot["gls"].append(float(rec["global_ls"]))
        if rec.get("oi_value") is not None:
            slot["oi_value"].append(float(rec["oi_value"]))
        o, ov = rec.get("oi"), rec.get("oi_value")
        spot = closes_by_day.get(int(k))
        if o and ov is not None and spot and spot > 0:
            slot["basis"].append((float(ov) / float(o)) / float(spot) - 1.0)
    return {w: {"gls": statistics.fmean(s["gls"]) if s["gls"] else None,
                "oi_value": statistics.fmean(s["oi_value"]) if s["oi_value"] else None,
                "basis": statistics.fmean(s["basis"]) if s["basis"] else None}
            for w, s in acc.items()}


def build_factor_library(weeks: list, price_agg: dict, funding: dict, oi: dict) -> dict:
    """纯组装:周聚合 → §7.1 因子库 {name: {week: value}}(全在给定 master weeks 上定义)。

    price_agg: {coin: weekly_price_agg 输出};funding: {coin: {week: 周累计}};
    oi: {coin: {week: {"gls","oi_value","basis"}}}。"""
    closes = {c: {w: a["close"] for w, a in agg.items()} for c, agg in price_agg.items()}
    btc, eth = closes["BTC"], closes["ETH"]
    realized = {w: fd.annualized_vol(a["daily_ret"]) for w, a in price_agg["BTC"].items()}
    realized = {w: v for w, v in realized.items() if v is not None}
    dollar_vol = fd.cross_sectional_sum({c: {w: a["dollar_vol"] for w, a in agg.items()}
                                         for c, agg in price_agg.items()}, weeks)
    oi_value = fd.cross_sectional_sum({c: {w: v["oi_value"] for w, v in m.items()
                                           if v["oi_value"] is not None} for c, m in oi.items()}, weeks)
    gls = fd.cross_sectional_mean({c: {w: v["gls"] for w, v in m.items()
                                       if v["gls"] is not None} for c, m in oi.items()}, weeks)
    basis = fd.cross_sectional_mean({c: {w: v["basis"] for w, v in m.items()
                                         if v["basis"] is not None} for c, m in oi.items()}, weeks)
    return {
        "btc_market": fd.weekly_returns(btc, weeks),
        "eth_market": fd.weekly_returns(eth, weeks),
        "market_trend_4w": fd.trailing_return(btc, weeks, 4),
        "short_term_reversal_1w": fd.reversal_lag(btc, weeks),
        "realized_vol_change": fd.diff_map(realized, weeks),
        "liquidity_volume_change": fd.pct_change_map(dollar_vol, weeks),
        "size_oi_change": fd.pct_change_map(oi_value, weeks),
        "funding_weekly": fd.cross_sectional_mean(funding, weeks),
        "basis_change": fd.diff_map(basis, weeks),
        "oi_crowding_change": fd.diff_map(gls, weeks),
    }


def common_grid(factor_maps: dict, strategy_weeks: list) -> list:
    """策略共同周 ∩ 所有因子都有值的周 → 分解网格(升序)。"""
    grid = set(strategy_weeks)
    for m in factor_maps.values():
        grid &= {w for w, v in m.items() if v is not None and math.isfinite(v)}
    return sorted(grid)


# ---------------------------------------------------------------------------
# 输出辅助
# ---------------------------------------------------------------------------

def _r(x, nd=6):
    if isinstance(x, float):
        return round(x, nd) if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _r(v, nd) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_r(v, nd) for v in x]
    return x


def _cluster_dicts(clusters) -> list:
    return [{"cluster_id": c.cluster_id, "members": list(c.member_ids),
             "intra_corr": _r(c.intra_corr)} for c in clusters]


def _edge_verdict(name: str, stats: dict) -> dict:
    """机械判读(文档化规则):alpha(截距)|t| ≥ 2 → 边不被因子库解释(非伪装 beta);
    R² ≥ 0.5 且 alpha |t| < 1 → 收益大多为因子复制。中间态诚实标 MIXED。"""
    r2, raw_t, a_t = stats["r2"], stats["raw"]["tstat"], stats["alpha_tstat"]
    if a_t is not None and abs(a_t) >= 2.0:
        verdict = "EDGE_NOT_EXPLAINED_BY_FACTOR_LIBRARY"
    elif r2 is not None and r2 >= 0.5 and (a_t is None or abs(a_t) < 1.0):
        verdict = "MOSTLY_FACTOR_REPLICATION"
    else:
        verdict = "MIXED"
    return {"strategy": name, "r2": _r(r2), "raw_tstat": _r(raw_t), "alpha_tstat": _r(a_t),
            "alpha_annualized": _r(stats["alpha_annualized"]),
            "mean_attribution_weekly": _r(stats["mean_attribution"]),
            "verdict": verdict,
            "rule": "alpha(截距)|t|>=2 → 边不被因子库解释;R2>=0.5 且 alpha |t|<1 → 大多为因子复制"}


def _cluster_portfolio(returns_by_sid: dict, weights: dict, clusters) -> dict:
    """簇级组合输出(§7.3 portfolio-relevant):簇权重、簇周收益(簇内权重归一再组合)、
    簇年化夏普/波动、簇间相关矩阵。W5 组合层消费此结构。"""
    cl_ret = {}
    cl_info = []
    for ci, c in enumerate(clusters):
        members = list(c.member_ids)
        w_sum = sum(weights.get(m, 0.0) for m in members)
        if w_sum > 0:
            inner = [weights[m] / w_sum for m in members]
            rets = combine_returns([returns_by_sid[m] for m in members], inner)
        else:                                   # 权重全缺 → 簇内等权(文档化回退)
            inner = [1.0 / len(members)] * len(members)
            rets = combine_returns([returns_by_sid[m] for m in members], inner)
        name = f"cluster_{ci}"
        cl_ret[name] = rets
        sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
        cl_info.append({"cluster": name, "cluster_id": c.cluster_id, "members": members,
                        "weight": _r(w_sum), "ann_sharpe": _r(fd.ann_sharpe(rets)),
                        "ann_vol": _r(sd * math.sqrt(52))})
    return {"clusters": cl_info, "cluster_corr": _r(fd.correlation_matrix(cl_ret))}


# ---------------------------------------------------------------------------
# main:IO + 编排
# ---------------------------------------------------------------------------

def _load_book_series(store: DryRunStore, key: str) -> tuple:
    """(display → (rets, ts), run 元信息)。缺失策略 → KeyError(fail-closed,不静默降级)。"""
    canonical = canonical_runs(store.all_runs())
    series, meta = {}, {}
    for sid, display in BOOK:
        if sid not in canonical:
            raise KeyError(f"dry_run.db 缺策略 {sid!r}({display})——先跑对应 dry_run_*_multi 驱动器")
        rec = canonical[sid]
        series[display] = curve_series(rec, key)
        meta[display] = {"db_strategy_id": sid, "run_id": rec["run_id"],
                         "curve": key, "n_periods": len(series[display][0])}
    return series, meta


def main() -> None:
    quarantine_legacy_backtest("tools.common_risk_research")
    # 1) 策略周收益(raw 主口径 = equity_curve;VT 稳健性 = equity_curve_vt)
    store = DryRunStore(_DRY_RUN_DB, create=False)
    raw_series, meta = _load_book_series(store, "equity_curve")
    vt_series, _ = _load_book_series(store, "equity_curve_vt")

    book_ids = [d for _, d in BOOK]
    book_aligned = fd.align_weekly({s: raw_series[s] for s in book_ids})
    strat_weeks = book_aligned["weeks"]
    book_raw = book_aligned["data"]

    # 2) 因子库(master 周 = 价格数据全周;趋势/反转 lookback 需要网格前的历史周)
    funding_json = json.load(open(_FUNDING_JSON, encoding="utf-8"))
    oi_json = json.load(open(_OI_JSON, encoding="utf-8"))["data"]
    store_px = CachingPITStore(SqlitePITStore(_PRICE_DB, create=False))
    price_agg, funding_w, oi_w, used = {}, {}, {}, []
    for coin in FACTOR_UNIVERSE:
        bars = fetch_bars_for_event_decisions(
            store_px,
            coin,
            0,
            10**18,
            decision_lag=86_400_000,
            minimum_knowledge_lag=86_400_000,
        )
        if not bars or coin not in funding_json or coin not in oi_json:
            continue
        closes_by_day = {int(b.event_time): float(b.close) for b in bars}
        price_agg[coin] = weekly_price_agg(bars)
        funding_w[coin] = weekly_funding(funding_json[coin])
        oi_w[coin] = weekly_oi(oi_json[coin], closes_by_day)
        used.append(coin)
    master_weeks = sorted({w for agg in price_agg.values() for w in agg})
    factor_maps = build_factor_library(master_weeks, price_agg, funding_w, oi_w)

    # 3) 网格 = 策略共同周 ∩ 因子有效周
    grid = common_grid(factor_maps, strat_weeks)
    if len(grid) < 60:
        raise RuntimeError(f"共同网格太短({len(grid)} 周)——数据缺口,拒绝在 n<60 上做分解")
    factors = {f: [m[w] for w in grid] for f, m in factor_maps.items()}
    book_grid = {s: [rets[strat_weeks.index(w)] for w in grid] for s, rets in book_raw.items()}

    # 4) 主分解(raw)+ VT 稳健性 + 等权篮子基准验证(合成,非干跑策略)
    deco = fd.decompose_strategies(book_grid, factors, weeks=grid, rho_cut=_RHO_CUT,
                                   shock_window=_SHOCK_WINDOW, shock_margin=_SHOCK_MARGIN)
    closes = {c: {w: a["close"] for w, a in agg.items()} for c, agg in price_agg.items()}
    basket_w = fd.cross_sectional_mean({c: fd.weekly_returns(closes[c], master_weeks)
                                        for c in closes}, master_weeks)
    basket_grid = [basket_w[w] for w in grid if w in basket_w]
    bench_model = fd.fit_factor_model(basket_grid, [[v for i, v in enumerate(factors[f])
                                                     if grid[i] in basket_w] for f in sorted(factors)],
                                      sorted(factors))

    vt_aligned = fd.align_weekly({s: vt_series[s] for s in book_ids})
    vt_weeks = vt_aligned["weeks"]
    vt_week_set = set(vt_weeks)
    vt_grid_weeks = [w for w in grid if w in vt_week_set]
    vt_grid = {s: [vt_aligned["data"][s][vt_weeks.index(w)] for w in vt_grid_weeks]
               for s in vt_aligned["data"]}
    vt_factors = {f: [m[w] for w in vt_grid_weeks] for f, m in factor_maps.items()}
    deco_vt = fd.decompose_strategies(vt_grid, vt_factors, weeks=vt_grid_weeks, rho_cut=_RHO_CUT)

    # 5) 簇级组合 + breadth(逆波动权重,raw 口径)
    w_list = inverse_vol_weights([book_grid[s] for s in sorted(book_grid)])
    weights = dict(zip(sorted(book_grid), w_list))
    breadth_raw = fd.effective_breadth(weights, deco["clusters_raw"])
    breadth_res = fd.effective_breadth(weights, deco["clusters_residual"])
    cluster_pf = _cluster_portfolio(book_grid, weights, deco["clusters_residual"])

    sensitivity = {str(cut): [sorted(c.member_ids) for c in
                              fd.agglomerative_cluster(deco["residuals"], rho_cut=cut)]
                   for cut in _RHO_SENSITIVITY}

    # 6) 证据
    snap = {
        "provenance": "REAL_COMMON_RISK_DECOMPOSITION",
        "spec": "profit-maximization §7 W3: strategy cluster & common-risk decomposition",
        "generated_from": {"dry_run_db": _DRY_RUN_DB,
                           "price_db": _PRICE_DB + " (available at t+1d; legacy rows rejected)",
                           "funding_json": _FUNDING_JSON, "oi_json": _OI_JSON},
        "strategies": meta,
        "factor_universe": used,
        "grid": {"common_weeks": len(grid), "first": grid[0], "last": grid[-1],
                 "note": "4 book 策略共同周 ∩ 全部因子有效周;top_contra(2022-06 起)为短边"},
        "factor_library": {
            "built": sorted(factor_maps),
            "definitions": "见 services/research/factor_decomp.py 模块 docstring(逐因子口径)",
            "missing": MISSING_FACTORS,
            "model": "OLS(截距+k 因子,numpy lstsq 闭式,确定性);全样本 in-sample = 回顾性敞口描述,"
                     "非 PIT 可交易择时;k=10,n≈192(顶帽)自由度充足但小样本",
        },
        "decomposition": _r({s: deco["strategies"][s] for s in book_ids}, 6),
        "edge_verdicts": [_edge_verdict(s, deco["strategies"][s]) for s in
                          ("carry_oi_majors", "crowd_contra", "top_contra")],
        "raw_corr": _r(deco["raw_corr"], 4),
        "residual_corr": _r(deco["residual_corr"], 4),
        "clusters": {
            "method": "complete-linkage agglomerative on |Pearson ρ|(复用 services/alpha/cluster.py);"
                      "cluster_id=stable_hash(sorted members)",
            "rho_cut": _RHO_CUT, "rho_cut_calibrated": "UNCALIBRATED_ENGINEERING_DEFAULT",
            "raw": _cluster_dicts(deco["clusters_raw"]),
            "residual": _cluster_dicts(deco["clusters_residual"]),
            "residual_sensitivity": sensitivity,
        },
        "breadth": {
            "weights_inverse_vol_raw": _r(weights),
            "weight_convention": "策略逆波动权重(Σ=1)→ 簇权重 W_c=簇内权重和;breadth=1/ΣW_c²"
                                 "(簇内=同一 bet、簇间≈不相关的 ENB 近似)",
            "n_strategies": len(book_ids),
            "effective_breadth_raw_clusters": _r(breadth_raw["effective_breadth"]),
            "effective_breadth_residual_clusters": _r(breadth_res["effective_breadth"]),
            "cluster_weights_residual": _r(breadth_res["cluster_weights"]),
        },
        "cluster_portfolio": cluster_pf,
        "correlation_shock": _r(deco["correlation_shock_raw"], 4),
        "correlation_shock_residual": _r(deco["correlation_shock_residual"], 4),
        "vt_robustness": {
            "note": "equity_curve_vt(vol-target 可部署口径)同分解的头条数字,对照 raw 主口径",
            "per_strategy": _r({s: {"r2": deco_vt["strategies"][s]["r2"],
                                    "alpha_tstat": deco_vt["strategies"][s]["alpha_tstat"]}
                                for s in book_ids}),
            "clusters_residual": _cluster_dicts(deco_vt["clusters_residual"]),
            "effective_breadth_residual_clusters": _r(fd.effective_breadth(
                weights, deco_vt["clusters_residual"])["effective_breadth"]),
        },
        "validation_benchmark": {
            "series": "alt_basket_equal_weight(合成:14 主流币周收益等权均值)",
            "role": "因子模型 sanity:市场篮子应被市场因子解释(R2 高)·beta_BTC 显著·alpha≈0",
            "why_not_dry_run_hold": "btc_spot_trend_hold 曲线非干净买入持有(信号门控,窗口内 119/216 周零收益),不能当基准",
            "r2": _r(bench_model.r2), "betas": _r(dict(zip(bench_model.factor_names,
                                                          bench_model.betas))),
            "beta_tstats": _r(dict(zip(bench_model.factor_names, bench_model.tstats[1:]))),
            "alpha_weekly": _r(bench_model.intercept), "alpha_tstat": _r(bench_model.tstats[0]),
            "excluded_from": "book 聚类/breadth(基准非策略)",
        },
        "honesty": {
            "weekly_n": len(grid),
            "caveats": [
                "全样本 in-sample OLS = 回顾性敞口描述;因子敞口随时间漂移,非 PIT 可交易择时",
                "iid t-stat,无 Newey-West;周收益自相关低但小样本(n≈192)±0.5 t 不确定",
                "因子库缺口见 factor_library.missing —— 残差相关可能吸收未建模公共因子",
                "rho_cut=0.5 / 冲击窗 26w+边际 0.25 均 UNCALIBRATED 工程默认",
                "4 条 book 曲线同一条干跑流水线(同成本/门/整手),执行现实共性已含在收益里",
                "ashare_* 策略为 A 股宇宙(不同因子库),本轮不纳入,记 coverage",
            ],
            "diagnostic_not_gate": True,
        },
    }
    snap["factor_library"]["definitions"] = "见 services/research/factor_decomp.py 模块 docstring(逐因子口径)"

    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    json.dump(snap, open(_OUT, "w", encoding="utf-8"), indent=2, ensure_ascii=False, default=str)

    # 7) 控制台摘要(Windows GBK 控制台:只用 GBK 可编码字符,勿用 ²/α/≈/≥/ρ 等特殊符号)
    print(f"=== W3 公共风险分解 · {len(grid)} 共同周({grid[0]}~{grid[-1]})· "
          f"{len(book_ids)} book 策略 x {len(factor_maps)} 因子 (REAL) ===", flush=True)
    for s in book_ids:
        st = deco["strategies"][s]
        print(f"  {s:16s} R2={st['r2']:.3f}  raw t={st['raw']['tstat']:+.2f} -> "
              f"alpha t={st['alpha_tstat']:+.2f}  alpha={st['alpha_annualized']*100:+.1f}%/y",
              flush=True)
    print(f"  聚类(残差 |rho|>={_RHO_CUT}): {[sorted(c.member_ids) for c in deco['clusters_residual']]}",
          flush=True)
    print(f"  breadth: raw 簇 {_r(breadth_raw['effective_breadth'])} / "
          f"残差簇 {_r(breadth_res['effective_breadth'])}(naive N={len(book_ids)})", flush=True)
    shock = deco["correlation_shock_raw"]
    print(f"  相关冲击(raw): 当前 mean|rho|={_r(shock['current']['mean_abs_corr'] if shock['current'] else None)} "
          f"阈值={_r(shock['threshold'])} shock_now={shock['in_shock_now']} "
          f"历史冲击周={shock['n_shock_weeks']}", flush=True)
    print(f"-> wrote {_OUT}", flush=True)


if __name__ == "__main__":
    main()
