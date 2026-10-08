"""W5 条件组合配置器评估驱动器(spec §9)→ evidence/allocator_research.json。

在 dry_run.db canonical 干跑曲线(carry_oi_majors / crowd_contra / top_contra / btc_spot_trend,
RAW equity_curve = 策略内净成本后周收益;top_contra 已在治理层 PAUSED,其历史序列仍是有效研究输入)
的公共周网格上,走前评估 6 个保留配置器(§9:等权=不可删除对照):
  equal_weight / inverse_vol / cluster_risk_parity(W3 簇图)/ constrained_max_sharpe /
  turnover_penalized(λ=0.02 包 max_sharpe)/ regime_conditioned(CRP 向逆波动回退)

**锁定切分(pre-registered)**:fit ≤ 2024-12(设计/暖机;trailing 窗口可用),locked ≥ 2025-W01
(唯一评分窗)。所有参数 trailing-only,周度 PIT 再平衡(第 t 周权重只用 ≤ t−1 已完结周信息)。
成本 = 10bps × Σ|Δw|(与干跑同成本口径,权重差=总调仓名义)。

regime 输入(全 PIT,FRED 工作的全样本分位是**描述性**的,这里一律 trailing 规则):
  VIX / DVOL-BTC → expanding 三分位(macro_regime.expanding_tercile_labels,截断不变);
  资金费截面均值(14 OI 大币)/ BTC 已实现波动 → trailing z(52w/26w);
  W3 相关冲击的 PIT 变体(rolling 26w mean|rho| vs 扩展均值+0.25,W3 阈值是全样本=回顾性)。
regime 只调候选权重(向稳健基线倾斜),低置信(<0.6 = 5 维中 <3 维可知)→ 精确回退逆波动。

预注册判定:最强非等权配置器在 locked 窗净夏普 > 等权+0.1 且回撤 ≤ 1.5x 等权 →
ADOPTED-AS-RESEARCH-FINDING,否则 EW_REMAINS(诚实)。

vol-target 块(EW/CRP + 10% ann 目标,lev_cap 2)= 杠杆诊断,与 unlevered 主矩阵分开,
不进判定(杠杆口径不可与等权对照直接比)。单策略参考行(crowd_contra 等)= 无配置层换手的
上下文(先前诚实发现:naive blend 净口径跑输 crowd_contra 单条),不参与判定但如实列出。

纯离线读本地库(Binance 价格走显式 t+1d available-time；Deribit/FRED 维持各自 PIT
接口；JSON),无网络、无真单;
研究诊断非门禁,不接任何 live 路径。无墙钟输出(证据确定性)。
"""
from __future__ import annotations

import bisect
import json
import os
import time

from services.data.caching_store import CachingPITStore
from services.data.available_time import fetch_bars_for_event_decisions
from services.data.fundamental_store import SqliteFundamentalStore
from services.data.persistent_store import SqlitePITStore
from services.execution.backtest_authority import quarantine_legacy_backtest
from services.execution.dry_run_store import DryRunStore
from services.research import allocators as al
from services.research import factor_decomp as fd
from tools.common_risk_research import (BOOK, FACTOR_UNIVERSE, _r, canonical_runs,
                                        curve_series, weekly_funding, weekly_price_agg)

_CACHE = os.path.join("data", "cache")
_DRY_RUN_DB = os.path.join(_CACHE, "dry_run.db")
_PRICE_DB = os.path.join(_CACHE, "crypto_funding.db")
_DERIBIT_DB = os.path.join(_CACHE, "deribit_vol.db")
_MACRO_DB = os.path.join(_CACHE, "macro_fred.db")
_FUNDING_JSON = os.path.join(_CACHE, "crypto_funding_rates.json")
_W3_EVIDENCE = os.path.join("evidence", "common_risk_decomposition.json")
_OUT = os.path.join("evidence", "allocator_research.json")

CONSTRAINTS = al.AllocationConstraints(gross=1.0, strategy_cap=0.40, cluster_cap=0.40)
_LOCK_WEEK = "2025-W01"          # 锁定切分:fit < 2025-W01 <= locked(唯一评分窗)
_START_IDX = 52                  # 走前起点:max lookback(52w cov/z)暖机完成
_COST_BPS = 10.0                 # 与干跑同成本口径
_IV_LOOKBACK, _IV_MIN = 26, 12
_MS_LOOKBACK, _MS_MIN, _MS_SHRINK, _MS_GRID = 52, 30, 0.5, 0.02
_TP_LAMBDA = 0.02
_RC_CONF, _RC_TILT = 0.6, 0.5
_VT = {"target_ann": 0.10, "lookback": 26, "lev_cap": 2.0, "min_history": 26}


# ---------------------------------------------------------------------------
# 数据组装(driver 侧;纯函数可合成数据测试)
# ---------------------------------------------------------------------------

def week_anchor_ts(series: dict) -> dict:
    """{sid: (rets, ts)} → {week: 该周最早 anchor ts(跨策略取 min,保守 = 决策不晚于此时)。"""
    anchors: dict = {}
    for sid in sorted(series):
        for t in series[sid][1]:
            w = fd.iso_week(int(t))
            anchors[w] = int(t) if w not in anchors else min(anchors[w], int(t))
    return anchors


def latest_leq(sorted_pairs: list, ts: int):
    """[(ts, value)] 升序 → 最后一个 event_time ≤ ts 的值;无 → None(bisect 右插入)。"""
    keys = [p[0] for p in sorted_pairs]
    i = bisect.bisect_right(keys, ts)
    return sorted_pairs[i - 1][1] if i > 0 else None


def build_regime_inputs(weeks: list, anchors: dict, macro_store, dvol_pairs: list,
                        funding_mean: dict, btc_rv: dict) -> list:
    """逐周 regime 原始值(anchors 为保守决策时点)→ build_regime_series 的 dims 列表。"""
    vix, dvol, fund, rv = [], [], [], []
    for w in weeks:
        a = anchors[w]
        fv = macro_store.latest_as_of("VIXCLS", "MACRO", _ymd(a))
        vix.append(fv.value if fv is not None else None)
        dvol.append(latest_leq(dvol_pairs, a))
        fund.append(funding_mean.get(w))
        rv.append(btc_rv.get(w))
    return [("vix", "tercile", vix), ("dvol", "tercile", dvol),
            ("funding", "zscore", fund), ("realized_vol", "zscore", rv)]


def _ymd(ms: int) -> int:
    return int(time.strftime("%Y%m%d", time.gmtime(ms / 1000)))


def load_cluster_map(path: str = _W3_EVIDENCE) -> dict:
    """W3 证据的残差簇图 → {strategy: cluster_id}。缺文件/缺块 → RuntimeError(fail-closed,
    簇风险预算配置器的承重输入,不静默造簇)。"""
    if not os.path.exists(path):
        raise RuntimeError(f"缺 W3 簇图证据 {path} —— 先跑 tools/common_risk_research.py")
    doc = json.load(open(path, encoding="utf-8"))
    clusters = (doc.get("clusters") or {}).get("residual")
    if not clusters:
        raise RuntimeError(f"{path} 缺 clusters.residual 块")
    out = {}
    for c in clusters:
        for m in c["members"]:
            out[m] = c["cluster_id"]
    return out


def make_allocators(returns_by_id: dict, cluster_map: dict, regime_series: list) -> dict:
    """绑定 6 个保留配置器为引擎可调用签名 (end_idx, prev_weights) -> (weights, diagnostics)。
    regime_conditioned 的 base=cluster_risk_parity、fallback=inverse_vol(预注册固定)。"""
    ids = sorted(returns_by_id)

    def _unpack(res: dict):
        return res["weights"], res["diagnostics"]

    def _crp(t):
        return al.cluster_risk_parity(cluster_map, returns_by_id, t, lookback=_IV_LOOKBACK,
                                      min_history=_IV_MIN, constraints=CONSTRAINTS)

    def _iv(t):
        return al.inverse_vol(returns_by_id, t, lookback=_IV_LOOKBACK, min_history=_IV_MIN,
                              constraints=CONSTRAINTS, cluster_map=cluster_map)

    def _ms(t):
        return al.constrained_max_sharpe(returns_by_id, t, lookback=_MS_LOOKBACK,
                                         min_history=_MS_MIN, mean_shrink=_MS_SHRINK,
                                         grid_step=_MS_GRID, constraints=CONSTRAINTS,
                                         cluster_map=cluster_map)

    def _tp(t, prev):
        base = _ms(t)["weights"]
        return _unpack(al.turnover_penalized(base, prev, _TP_LAMBDA))

    def _rc(t, prev):
        state = regime_series[t]
        return _unpack(al.regime_conditioned(_crp(t)["weights"], state["stress_score"],
                                             state["confidence"], _iv(t)["weights"],
                                             confidence_threshold=_RC_CONF, max_tilt=_RC_TILT))

    return {
        "equal_weight": lambda t, prev: _unpack(al.equal_weight(ids, CONSTRAINTS, cluster_map)),
        "inverse_vol": lambda t, prev: _unpack(_iv(t)),
        "cluster_risk_parity": lambda t, prev: _unpack(_crp(t)),
        "constrained_max_sharpe": lambda t, prev: _unpack(_ms(t)),
        "turnover_penalized_max_sharpe": _tp,
        "regime_conditioned_crp": _rc,
    }


def single_strategy_path(returns_by_id: dict, weeks: list, sid: str, start_idx: int) -> list:
    """单策略参考行:恒定 100% 权重,无配置层换手(成本 0,文档化口径)。"""
    path = []
    for t in range(start_idx, len(weeks)):
        r = returns_by_id[sid][t]
        path.append({"t": t, "week": weeks[t], "weights": {sid: 1.0}, "gross": r, "cost": 0.0,
                     "net": r, "turnover": 0.0, "hhi": 1.0, "used_fallback": False})
    return path


def locked_rel_indices(weeks: list, start_idx: int, lock_week: str = _LOCK_WEEK) -> list:
    return [t - start_idx for t in range(start_idx, len(weeks)) if weeks[t] >= lock_week]


# ---------------------------------------------------------------------------
# main:IO + 编排
# ---------------------------------------------------------------------------

def main() -> None:
    quarantine_legacy_backtest("tools.allocator_research")
    store = DryRunStore(_DRY_RUN_DB, create=False)
    canonical = canonical_runs(store.all_runs())
    raw_series, meta = {}, {}
    for sid, display in BOOK:
        if sid not in canonical:
            raise KeyError(f"dry_run.db 缺策略 {sid!r}({display})——先跑对应 dry_run_*_multi 驱动器")
        rec = canonical[sid]
        raw_series[display] = curve_series(rec, "equity_curve")
        meta[display] = {"db_strategy_id": sid, "run_id": rec["run_id"], "curve": "equity_curve",
                         "n_periods": len(raw_series[display][0])}
    book_ids = [d for _, d in BOOK]
    aligned = fd.align_weekly({s: raw_series[s] for s in book_ids})
    weeks, book = aligned["weeks"], aligned["data"]
    if len(weeks) < 120:
        raise RuntimeError(f"公共网格太短({len(weeks)} 周)——拒绝在 n<120 上评估配置器")
    anchors = week_anchor_ts(raw_series)

    cluster_map = load_cluster_map()

    # regime 原始输入(全 PIT 保守时点)
    macro_store = SqliteFundamentalStore(_MACRO_DB)
    dvol_store = SqlitePITStore(_DERIBIT_DB, create=False)
    dvol_bars = dvol_store.fetch_bars_pit("DVOL-BTC", 0, 10 ** 18)      # PIT 安全批量读
    dvol_pairs = sorted((int(b.event_time), float(b.close)) for b in dvol_bars)
    funding_json = json.load(open(_FUNDING_JSON, encoding="utf-8"))
    funding_mean = fd.cross_sectional_mean(
        {c: weekly_funding(funding_json[c]) for c in FACTOR_UNIVERSE if c in funding_json}, weeks)
    px_store = CachingPITStore(SqlitePITStore(_PRICE_DB, create=False))
    btc_bars = fetch_bars_for_event_decisions(
        px_store,
        "BTC",
        0,
        10**18,
        decision_lag=86_400_000,
        minimum_knowledge_lag=86_400_000,
    )
    btc_rv = {w: fd.annualized_vol(a["daily_ret"])
              for w, a in weekly_price_agg(btc_bars).items()}

    dims = build_regime_inputs(weeks, anchors, macro_store, dvol_pairs, funding_mean, btc_rv)
    regime_series = al.build_regime_series(book, dims)
    regime_labels = {}
    for t in locked_rel_indices(weeks, _START_IDX):
        lb = regime_series[t + _START_IDX]["label"]
        regime_labels[lb] = regime_labels.get(lb, 0) + 1

    allocators = make_allocators(book, cluster_map, regime_series)
    results = al.evaluate_walk_forward(book, weeks, allocators, lock_week=_LOCK_WEEK,
                                       start_idx=_START_IDX, cost_bps=_COST_BPS,
                                       cluster_map=cluster_map, constraints=CONSTRAINTS)

    # vol-target 杠杆诊断块(EW/CRP,预注册固定选择)
    lock_rel = locked_rel_indices(weeks, _START_IDX)
    vt_block = {}
    for name in ("equal_weight", "cluster_risk_parity"):
        vt_path = al.apply_vol_target(results[name]["path"], cost_bps=_COST_BPS, **_VT)
        vt_block[name + "_vol_target"] = {
            "locked": al.window_metrics(vt_path, lock_rel),
            "mean_scale": round(sum(p["vol_target_scale"] for p in vt_path) / len(vt_path), 4)}

    # 单策略参考行(上下文,不进判定)
    ref = {}
    for sid in book_ids:
        path = single_strategy_path(book, weeks, sid, _START_IDX)
        ref[sid] = {"locked": al.window_metrics(path, lock_rel)}

    # 敏感性(描述性,非主判定):剔除 top_contra 的 3 策略 book 同码重跑 —— 回答
    # 「配置器的胜利是不是只是 top_contra 的锁定窗尾边」。(top_contra 治理层 PAUSED,
    # 前向证据为负;其锁定窗强势是历史口径。)相关冲击维随 book 内生,重建 regime 序列。
    ex_ids = [s for s in book_ids if s != "top_contra"]
    book_ex = {s: book[s] for s in ex_ids}
    cmap_ex = {s: cluster_map.get(s, s) for s in ex_ids}
    regime_ex = al.build_regime_series(book_ex, dims)
    res_ex = al.evaluate_walk_forward(book_ex, weeks, make_allocators(book_ex, cmap_ex, regime_ex),
                                      lock_week=_LOCK_WEEK, start_idx=_START_IDX,
                                      cost_bps=_COST_BPS, cluster_map=cmap_ex,
                                      constraints=CONSTRAINTS)
    locked_ex = {name: res_ex[name]["locked"] for name in sorted(res_ex)}
    vd_ex = al.verdict(locked_ex)

    locked_matrix = {name: results[name]["locked"] for name in sorted(results)}
    vd = al.verdict(locked_matrix)

    snap = {
        "provenance": "REAL_ALLOCATOR_RESEARCH",
        "spec": "profit-maximization §9 W5: conditional portfolio allocator vs retained baselines",
        "generated_from": {"dry_run_db": _DRY_RUN_DB,
                           "price_db": _PRICE_DB + " (available at t+1d; legacy rows rejected)",
                           "deribit_db": _DERIBIT_DB, "macro_db": _MACRO_DB,
                           "funding_json": _FUNDING_JSON, "w3_cluster_map": _W3_EVIDENCE},
        "strategies": meta,
        "grid": {"common_weeks": len(weeks), "first": weeks[0], "last": weeks[-1],
                 "note": "4 book 策略公共周(RAW equity_curve = 策略内净成本后周收益)"},
        "locked_split": {"lock_week": _LOCK_WEEK,
                         "rule": "fit/design <= 2024-12(trailing 暖机可用);locked >= 2025-W01 = "
                                 "唯一评分窗;所有参数 trailing-only,周度 PIT 再平衡",
                         "walk_start_week": weeks[_START_IDX],
                         "fit_weeks": sum(1 for t in range(_START_IDX, len(weeks))
                                          if weeks[t] < _LOCK_WEEK),
                         "locked_weeks": len(lock_rel)},
        "constraints": {"gross": CONSTRAINTS.gross, "strategy_cap": CONSTRAINTS.strategy_cap,
                        "cluster_cap": CONSTRAINTS.cluster_cap,
                        "note": "策略权重 = 非负资本配置(配置层不做空策略);calibrated: "
                                + al.UNCALIBRATED},
        "cost_model": {"turnover_cost_bps": _COST_BPS,
                       "basis": "cost = bps/1e4 x Σ|Δw|(权重差=总调仓名义,与干跑同口径)"},
        "allocator_params": {
            "inverse_vol": {"lookback": _IV_LOOKBACK, "min_history": _IV_MIN},
            "cluster_risk_parity": {"lookback": _IV_LOOKBACK, "min_history": _IV_MIN,
                                    "cluster_map_source": "W3 clusters.residual(全单例簇)"},
            "constrained_max_sharpe": {"lookback": _MS_LOOKBACK, "min_history": _MS_MIN,
                                       "mean_shrink_toward_zero": _MS_SHRINK,
                                       "grid_step": _MS_GRID, "solver": "确定性单形网格+argmax"},
            "turnover_penalized_max_sharpe": {"lambda_deadzone": _TP_LAMBDA, "base": "constrained_max_sharpe"},
            "regime_conditioned_crp": {"base": "cluster_risk_parity", "fallback": "inverse_vol",
                                       "confidence_threshold": _RC_CONF, "max_tilt": _RC_TILT},
            "params_calibrated": al.UNCALIBRATED},
        "regime_model": {
            "dims": [{"name": n, "kind": k} for n, k, _ in dims] +
                    [{"name": "correlation_shock", "kind": "endogenous_pit_variant_of_W3"}],
            "decision_timing": "第 t 周决策只用 <= t-1 已完结周信息(shift 内置)",
            "pit_note": "FRED 工作的全样本分位标签是描述性的;此处全部 trailing 规则"
                        "(expanding 三分位/trailing z/扩展均值冲击阈值),截断不变有性质测试",
            "locked_window_label_counts": regime_labels},
        "allocators": {name: {"locked": _r(results[name]["locked"]),
                              "fit": _r(results[name]["fit"])} for name in sorted(results)},
        "vol_target_block": {
            "note": "杠杆诊断口径(gross 可 >1 至 lev_cap),与 unlevered 主矩阵分开,不进判定",
            "params": {**_VT, "params_calibrated": al.UNCALIBRATED},
            **{k: {"locked": _r(v["locked"]), "mean_scale": v["mean_scale"]}
               for k, v in vt_block.items()}},
        "reference_single_strategies": {
            "note": "恒定 100% 单策略,无配置层换手成本;上下文对照(先前发现:naive blend 净口径"
                    "跑输 crowd_contra 单条),不参与判定",
            **{sid: {"locked": _r(v["locked"])} for sid, v in ref.items()}},
        "verdict": _r(vd),
        "sensitivity_excluding_top_contra": {
            "note": "描述性稳健性(非主判定):剔除 top_contra 的 3 策略 book 同码重跑——"
                    "检验配置层胜利是否只是 top_contra 锁定窗尾边的载体;top_contra 治理层 "
                    "PAUSED(前向证据为负),其锁定窗强势是历史口径",
            "book": ex_ids,
            "allocators": {name: {"locked": _r(res_ex[name]["locked"]),
                                  "fit": _r(res_ex[name]["fit"])} for name in sorted(res_ex)},
            "verdict_descriptive": _r(vd_ex)},
        "trial_discipline": {
            "design": "单次预注册评估:6 个配置器 + 判定规则(margin 0.1 / 1.5x maxDD)先于结果写死;"
                      "无变体扫描、无锁定窗上的配置器选择(VT 块/参考行不进判定)",
            "caveat": "6 个配置器同属一个评估家族,判定只做「是否取代等权」的相对结论,"
                      "不做绝对可部署声明;参数 UNCALIBRATED"},
        "honesty": {
            "caveats": [
                "RAW 曲线已含策略内执行成本/整手/门现实;配置层只加 10bps 调仓成本",
                "W3 簇图全单例 → cluster_risk_parity ≡ inverse_vol 恒等(簇帽未绑定);"
                "regime_conditioned 的 base=CRP 与 fallback=IV 因此重合,tilt 结构性空转——"
                "锁定窗 4 个 stress 周触发了 tilt 但权重零变化;机制经测试就绪,待簇图出现"
                "多成员簇(CRP≠IV)即生效",
                "top_contra 单条锁定窗净夏普高于全部配置器;max_sharpe 系的胜利集中在"
                "top_contra 尾边(HHI 0.33 vs EW 0.25)——见 sensitivity_excluding_top_contra;"
                "top_contra 治理层 PAUSED(前向证据为负),主判定是研究发现,非部署建议",
                "locked 窗 ~1.3 年单段;窗口内最大回撤 = 窗口局部口径(非峰值起算)",
                "fit→locked 全配置器夏普退化可见(1.7~1.9 → 1.4~1.7),锁定窗是更难的环境",
                "max_sharpe 均值收缩 0.5 / 网格 0.02 等参数 UNCALIBRATED,网格精度 ±0.02 权重",
            ],
            "diagnostic_not_gate": True,
            "no_live_wiring": "不接 live-track/driver/governance;live 回撤控制器独立缩放 deploy",
        },
    }

    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    json.dump(snap, open(_OUT, "w", encoding="utf-8"), indent=2, ensure_ascii=False, default=str)

    # 控制台摘要(Windows GBK:只 ASCII)
    print(f"=== W5 allocator research | {len(weeks)} common weeks ({weeks[0]}~{weeks[-1]}) | "
          f"locked >= {_LOCK_WEEK} ({len(lock_rel)}w) | cost {_COST_BPS:.0f}bps (REAL) ===", flush=True)
    print(f"  {'allocator':30s} {'netSR':>7s} {'grsSR':>7s} {'maxDD':>7s} {'turn/w':>7s} "
          f"{'HHI':>6s} {'fb%':>6s} {'netTot':>8s}", flush=True)
    for name in sorted(results):
        m = results[name]["locked"]
        print(f"  {name:30s} {_f(m['net_sharpe'])} {_f(m['gross_sharpe'])} {_f(m['max_drawdown'])} "
              f"{_f(m['mean_weekly_turnover'])} {_f(m['cluster_hhi_mean'])} "
              f"{_f(m['fallback_rate'])} {_f(m['net_total_return'])}", flush=True)
    for k, v in vt_block.items():
        m = v["locked"]
        print(f"  {k:30s} {_f(m['net_sharpe'])} {_f(m['gross_sharpe'])} {_f(m['max_drawdown'])} "
              f"{_f(m['mean_weekly_turnover'])} {'(vt)':>6s} {_f(m['fallback_rate'])} "
              f"{_f(m['net_total_return'])}  scale~{v['mean_scale']:.2f}", flush=True)
    for sid in book_ids:
        m = ref[sid]["locked"]
        print(f"  {'ref:' + sid:30s} {_f(m['net_sharpe'])} {_f(m['gross_sharpe'])} "
              f"{_f(m['max_drawdown'])} {'0':>7s} {'1.0':>6s} {'-':>6s} {_f(m['net_total_return'])}",
              flush=True)
    print(f"VERDICT: {vd['verdict']} | best non-EW={vd.get('best_non_ew')} "
          f"netSR={_f(vd.get('best_net_sharpe'))} vs EW={_f(vd.get('ew_net_sharpe'))} "
          f"delta={_f(vd.get('delta_sharpe'))} dd_ok={vd.get('maxdd_constraint_ok')}", flush=True)
    print(f"SENSITIVITY ex-top_contra: {vd_ex['verdict']} | best={vd_ex.get('best_non_ew')} "
          f"netSR={_f(vd_ex.get('best_net_sharpe'))} vs EW={_f(vd_ex.get('ew_net_sharpe'))} "
          f"delta={_f(vd_ex.get('delta_sharpe'))}", flush=True)
    print(f"-> wrote {_OUT}", flush=True)


def _f(x, nd=3):
    if x is None:
        return f"{'None':>7s}"
    return f"{x:>7.{nd}f}" if abs(x) < 100 else f"{x:>7.1f}"


if __name__ == "__main__":
    main()
