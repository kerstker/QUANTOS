"""A股微盘复合 alpha 的 paper-track 前向追踪驱动器(network-free,读研究副本库)。

把 ashare_alpha_research 的冠军复合篮子接进平台前向 paper-trading 持久层
(services/research/paper_trading/store.py,append-only data/cache/paper_track.db):

  周度 advance step = 在 t 时刻用 PIT 可知数据算复合分(面板/因子全部由 PIT 副本库构建,
  knowledge_time <= event_time)-> 同一参与率上限规则选 top-20 篮子 -> 记录纸面持仓 +
  下周实现净 P&L(15bps 单边成本,与 basket.backtest 逐位同口径——收益序列直接取回测净值
  曲线的周收益,保证 paper 记录与回测引擎不分叉)-> 周度截面 rank IC(复合分 vs 下周实现
  收益,ic_panel.compute_horizon_ic_series 非重叠 h=5,与调仓相位一致)-> 等权全宇宙基准
  对比。

PROVENANCE(诚实):store 有 source 字段。本工具是事后研究驱动器——所有行的信号与收益都
来自**已存在**的历史数据,因此全部标 BACKFILL;只有当未来每周新数据落地后重跑本工具追加的
新周才接近真 FORWARD(即便如此,本工具仍是 retrospective 计算,故始终坚持标 BACKFILL,
FORWARD 留给真正的周度在线 advance)。train/paper 划分:paper 区间 = test(TEST_START=2025-01-01
起,严格无偷看,复合权重只用 train 拟合);backtest 参考 Sharpe = TRAIN 窗口篮子 Sharpe。

幂等:append_periods 跳过 (alpha_id, as_of_ts) 已存在且内容逐位相同的行;同键不同内容
fail-closed 抛 PaperTrackStoreError(数据/代码变化导致历史周收益改变时会显式报错而非静默
覆盖)。alpha_id 带宇宙规模后缀 u{N} —— 宇宙 vintage 变了就是一条新 track,不污染旧记录。
"""
from __future__ import annotations

import json
import os
import sys

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research import ic_panel
from services.research.equity_alpha import basket as bk
from services.research.equity_alpha import combine as cb
from services.research.equity_alpha import factors as fx
from services.research.equity_alpha import hygiene as hy
from services.research.equity_alpha import ic_eval as ev
from services.research.equity_alpha import panel as eq_panel
from services.research.paper_trading.store import PaperTrackStore

_DB = os.path.join("data", "cache", "ashare_daily_research.db")   # research COPY
_UNIVERSE = os.path.join("data", "cache", "ashare_universe.json")
_TRACK_DB = os.path.join("data", "cache", "paper_track.db")
_OUT = os.path.join("data", "cache", "ashare_paper_track_snapshot.json")
STRATEGY_ID_BASE = "ashare_microcap_composite_v3"
SOURCE = "BACKFILL"
# 换手变体参数(与 ashare_alpha_research.VARIANTS 同步;baseline = 无参数)
VARIANT_PARAMS = {"baseline": {}, "buf30": {"buffer_k": 30}, "buf40": {"buffer_k": 40},
                  "cap25": {"max_oneway": 0.25}, "cap30": {"max_oneway": 0.30},
                  "cap40": {"max_oneway": 0.40}, "cap50": {"max_oneway": 0.50}}


def _first_row_at_or_after(event_times, yyyymmdd: int) -> int:
    for r, t in enumerate(event_times):
        if t >= yyyymmdd:
            return r
    return len(event_times) - 1


def build_champion_combo(store, codes):
    """与 tools/ashare_alpha_research 同一条代码路径:面板 -> 因子 -> IC -> 卫生 ->
    双方案 -> train h5 ICIR 冠军。返回 (panel, aux, eligible, combo_mat, champion, kept,
    dropped, adv_mat)。"""
    panel, aux = eq_panel.build_ashare_panel(store, codes)
    eligible = eq_panel.eligible_matrix(aux)
    factor_mats = fx.build_factor_matrices(panel, aux, eligible)
    price = aux["close_clean"]   # 坏 bar 置 None(与主驱动器同一清洗口径)
    stats = ev.evaluate_all(factor_mats, price, panel.event_times, horizons=(1, 5))
    rank_mats = fx.rank_versions(factor_mats)
    corr = hy.factor_correlation_matrix_train(rank_mats, panel.event_times, ev.TRAIN_END)
    kept, dropped, _screened = hy.greedy_decorrelate(stats, corr, horizon_key="h1")
    schemes = {}
    for scheme in ("icir", "equal"):
        w = cb.fit_combo_weights(stats, horizon_key="h1", names=kept, scheme=scheme)
        mat = cb.apply_combo(factor_mats, w) if w else None
        s = (ev.evaluate_signal(mat, price, panel.event_times, horizons=(1, 5))
             if mat else None)
        schemes[scheme] = {"mat": mat, "ic": s}
    stats_by_scheme = {k: v["ic"] for k, v in schemes.items() if v["ic"]}
    champion = cb.select_champion(stats_by_scheme, horizon_key="h5") if stats_by_scheme else None
    combo_mat = schemes[champion]["mat"] if champion else None
    T, N = panel.shape
    adv_mat = [[factor_mats["turnover_20d"][t][i] for i in range(N)] for t in range(T)]
    return panel, eligible, combo_mat, champion, kept, dropped, adv_mat, price


def main(variant: str = "baseline") -> None:
    if variant not in VARIANT_PARAMS:
        raise SystemExit(f"unknown variant {variant!r}; choose from {sorted(VARIANT_PARAMS)}")
    vparams = VARIANT_PARAMS[variant]
    universe = json.load(open(_UNIVERSE, encoding="utf-8"))
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    codes = [c for c in eq_panel.universe_codes(universe) if c in set(store.keys())]
    alpha_id = f"{STRATEGY_ID_BASE}_u{len(codes)}" + (f"_{variant}" if variant != "baseline" else "")
    print(f"paper-track: {alpha_id} · 宇宙交集 {len(codes)} 只 · 变体 {variant}", flush=True)

    panel, eligible, combo_mat, champion, kept, dropped, adv_mat, price = \
        build_champion_combo(store, codes)
    if combo_mat is None:
        print("无冠军复合(无因子过初筛)—— 诚实空结果,不写 paper track", flush=True)
        return
    T = panel.shape[0]
    reb = bk.weekly_indices(T)
    r_test0 = _first_row_at_or_after(panel.event_times, ev.TEST_START)
    test_rows = [r for r in reb if r >= r_test0]

    # 周度实现净收益(与 basket.backtest 同口径,变体参数透传)+ 基准(同一回测的 bench 腿)
    bt_test = bk.backtest(combo_mat, price, adv_mat, eligible, panel.instruments, reb,
                          row_lo=r_test0, **vparams)
    bt_train = bk.backtest(combo_mat, price, adv_mat, eligible, panel.instruments, reb,
                           row_hi=r_test0 - 1, **vparams)
    eq, bench_eq = bt_test["equity"], bt_test["bench_equity"]
    weekly_rets = [eq[k + 1] / eq[k] - 1.0 for k in range(len(eq) - 1)]
    bench_rets = [bench_eq[k + 1] / bench_eq[k] - 1.0 for k in range(len(bench_eq) - 1)]

    # 周度截面 rank IC(非重叠 h=5,相位 0 与 reb 一致)-> {signal_row: ic}
    ics, starts = ic_panel.compute_horizon_ic_series(combo_mat, price, bk.WEEK_STEP)
    ic_by_row = {r: v for r, v in zip(starts, ics)}

    # 组装 store 行:每周一行 (as_of_ts=调仓日 epoch ms, 实现净收益, 周 IC, BACKFILL)
    rows, weekly_table = [], []
    for k in range(len(test_rows) - 1):
        r = test_rows[k]
        ts = ev.yyyymmdd_to_epoch_ms(int(panel.event_times[r]))
        ic = ic_by_row.get(r)
        rows.append((ts, weekly_rets[k], ic, SOURCE))
        weekly_table.append({"date": int(panel.event_times[r]),
                             "paper_ret": round(weekly_rets[k], 6),
                             "bench_ret": round(bench_rets[k], 6),
                             "weekly_ic": (round(ic, 6) if ic is not None else None)})

    track_store = PaperTrackStore(_TRACK_DB, create=True)
    n_before = len(track_store.periods(alpha_id))
    track_store.append_periods(alpha_id, rows)
    bt_sharpe_ref = bt_train["strategy"]["ann_sharpe"] if bt_train else None
    track_store.recompute_summary(alpha_id, backtest_sharpe=bt_sharpe_ref)
    track = track_store.track(alpha_id)
    n_new = len(track_store.periods(alpha_id)) - n_before

    # 汇总:paper IC 聚合(均值/t) + paper vs 基准 Sharpe + 周数
    ic_vals = [w["weekly_ic"] for w in weekly_table if w["weekly_ic"] is not None]
    ic_ts = [ev.yyyymmdd_to_epoch_ms(w["date"]) for w in weekly_table
             if w["weekly_ic"] is not None]
    ic_agg = ic_panel.aggregate_ic_series(ic_vals, ic_ts)
    bench_stats = bt_test["benchmark"]
    # HONEST ANNUALIZATION NOTE: PaperTrackStore.recompute_summary 调
    # services/validation/performance.sharpe 的默认 periods_per_year=252 —— 对周度序列
    # 高估 sqrt(252/52)≈2.2 倍。共享平台存储不改;此处按 52 周/年重算正确口径并列报告。
    from services.validation.performance import sharpe as _sharpe
    paper_sharpe_wk = round(_sharpe(weekly_rets, periods_per_year=52), 4)
    bench_sharpe_wk = round(_sharpe(bench_rets, periods_per_year=52), 4)

    snapshot = {
        "alpha_id": alpha_id,
        "variant": variant, "variant_params": vparams,
        "champion_combo": champion, "hygiene_kept": kept, "hygiene_dropped": dropped,
        "provenance": {
            "source_field": SOURCE,
            "note": "所有当前行均为历史回填(BACKFILL):信号与收益来自已存在数据。真 FORWARD "
                    "行需要周度在线 advance(新数据落地当周末计算下一周);本工具是 retrospective "
                    "研究驱动器,始终标 BACKFILL。",
            "train_paper_split": {"train_end": ev.TRAIN_END, "paper_start": ev.TEST_START},
        },
        "summary": {
            "n_weeks": len(weekly_table), "n_new_rows_appended": n_new,
            "paper_cum_return": track["cum_return"],
            "paper_sharpe_weekly_annualized": paper_sharpe_wk,
            "paper_sharpe_store_raw_252": track["paper_sharpe"],
            "annualization_note": "store 的 paper_sharpe 用共享默认 periods_per_year=252,"
                                  "周度序列被高估 ~2.2 倍;正确口径看 *_weekly_annualized",
            "paper_ic_mean": track["paper_ic"],
            "paper_ic_t_stat": ic_agg.get("t_stat"), "paper_ic_icir": ic_agg.get("icir"),
            "paper_ic_hit_rate": ic_agg.get("hit_rate"),
            "bench_total_return": bench_stats["total_return"],
            "bench_sharpe_weekly_annualized": bench_sharpe_wk,
            "backtest_ref_sharpe_train": bt_sharpe_ref,
            "perf_gap_vs_train": track["perf_gap"],
        },
        "basket_params": {"n": bk.BASKET_N, "aum_cny": bk.AUM_CNY,
                          "participation_cap": bk.PARTICIPATION_CAP, "cost_bps": bk.COST_BPS},
        "weekly": weekly_table,
        "store_db": _TRACK_DB,
    }
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)
    s = snapshot["summary"]
    print(f"paper-track {alpha_id}: {s['n_weeks']} 周(新增 {n_new})· "
          f"paper 累计 {s['paper_cum_return']*100:.1f}% · "
          f"Sharpe(52周/年) {s['paper_sharpe_weekly_annualized']} · "
          f"IC 均值 {s['paper_ic_mean']}(t={s['paper_ic_t_stat']})· "
          f"基准 Sharpe {s['bench_sharpe_weekly_annualized']} · "
          f"train 参考 Sharpe {s['backtest_ref_sharpe_train']}",
          flush=True)
    print(f"snapshot -> {_OUT}", flush=True)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--variant") + 1] if "--variant" in sys.argv else "baseline")
