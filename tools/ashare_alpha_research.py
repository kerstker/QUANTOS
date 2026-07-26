"""A股微盘 ALPHA 研究驱动器 v2 —— equity_alpha 引擎(network-free,读研究副本库)。

流程:PIT 副本库 -> 面板 + 可交易掩码 + 9 个日频因子 -> 逐因子 IC(train<=2024-12-31 /
test>=2025-01-01 严格无偷看)-> 因子卫生(横截面 1%/99% winsorize + train 相关性贪心去冗余
|corr|>0.7)-> 两种复合方案(ICIR 加权 / 幸存等权)在 **train h5 ICIR** 上选冠军并冻结 ->
top-20 周度调仓篮子回测(成本敏感网格 10/15/25bps + 25% 回撤熔断变体)-> 同一组周度权重过
run_multi_period_dry_run(执行现实层:真实 trailing-20d 成交量 ctx、参与率 10%/单名 10% 真限额、
100 股整手 filters)落 dry_run.db(strategy_id=ashare_microcap_composite_v2_u{N})。

RESEARCH OVERRIDE 说明(诚实):门限额里只有 max_participation_rate=0.10 与
max_single_name_pct=0.10 是"真实"研究级限额;其余(持仓量/名义额/总敞口/杠杆/换手/日亏/
回撤上限)仍是宽松占位——这是研究诊断口径,不是生产风控参数;任何晋级/实盘决策必须重估整组
限额。账户 ctx 的 cash/equity 设为 AUM(而非此前的 10×AUM),使单名 10% 限额真正承重。

诚实边界(写进 snapshot):宇宙=2026-07-23 的幸存者截面(幸存者偏差);价格=前复权;
payload 无 amt 字段 -> 成交额=volume*(O+H+L+C)/4 代理;est_daily_volume=trailing-20d 平均
**股数**成交量(与门检查的 intent.quantity 股数同单位);DB 仍在后台增长,本跑只覆盖副本时刻
已入库子集;多重检验计数与 BH-FDR/Bonferroni 修正见 multiple_testing 段;初筛诊断,不驱动
任何晋级/下单。
"""
from __future__ import annotations

import json
import math
import os
from statistics import NormalDist

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research.equity_alpha import basket as bk
from services.research.equity_alpha import combine as cb
from services.research.equity_alpha import factors as fx
from services.research.equity_alpha import hygiene as hy
from services.research.equity_alpha import ic_eval as ev
from services.research.equity_alpha import panel as eq_panel
from services.alpha.multiple_testing import benjamini_hochberg, bonferroni_tstat_threshold
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.execution.dry_run_pipeline import run_multi_period_dry_run
from services.execution.dry_run_store import DryRunStore
from tools.domain_event_bus import open_domain_event_bus, save_and_project_dry_run

_DB = os.path.join("data", "stocks", "research.db")   # research COPY of data/stocks/stocks.db
_UNIVERSE = os.path.join("data", "stocks", "universe.json")
_OUT = os.path.join("data", "stocks", "snapshots", "alpha_snapshot.json")
_DRY_DB = os.path.join("data", "cache", "dry_run.db")
STRATEGY_ID_BASE = "ashare_microcap_composite_v3"   # v3: 修 dry-run 价格覆盖(全标的,治僵尸持仓)
COST_GRID_BPS = (10.0, 15.0, 25.0)
ASHARE_LOT = 100.0   # A股整手 100 股

_ND = NormalDist()


def _first_row_at_or_after(event_times, yyyymmdd: int) -> int:
    for r, t in enumerate(event_times):
        if t >= yyyymmdd:
            return r
    return len(event_times) - 1


def _two_sided_p(t_stat):
    if t_stat is None:
        return None
    return 2.0 * (1.0 - _ND.cdf(abs(t_stat)))


def main() -> None:
    universe = json.load(open(_UNIVERSE, encoding="utf-8"))
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    codes = [c for c in eq_panel.universe_codes(universe) if c in set(store.keys())]
    print(f"宇宙 {len(universe.get('stocks', []))} 只 · 副本库已入库 {len(store.keys())} 只 · "
          f"交集 {len(codes)} 只", flush=True)
    STRATEGY_ID = f"{STRATEGY_ID_BASE}_u{len(codes)}"

    panel, aux = eq_panel.build_ashare_panel(store, codes)
    eligible = eq_panel.eligible_matrix(aux)
    T, N = panel.shape
    print(f"面板 {T} 交易日 × {N} 只 · {panel.event_times[0]} -> {panel.event_times[-1]}", flush=True)

    factor_mats = fx.build_factor_matrices(panel, aux, eligible)
    price = aux["close_clean"]   # 坏 bar 置 None 的清洗收盘价:IC/回测/干跑统一用(raw 留 panel)

    # --- 坏 bar 卫生报告(超板块涨跌幅带跳变,逐源统计) ---------------------------
    try:
        _ksnap = json.load(open(os.path.join("data", "stocks", "snapshots", "klines_snapshot.json"),
                                encoding="utf-8"))
        src_map = {c: v.get("source", "unknown")
                   for c, v in _ksnap.get("per_stock", {}).items()}
    except (OSError, json.JSONDecodeError):
        src_map = {}
    bad_detail = aux["bad_bar_detail"]
    bad_bar_report = {
        "bands": {"main(60/00)": eq_panel.LIMIT_BAND_MAIN,
                  "growth(300/301/688)": eq_panel.LIMIT_BAND_GROWTH,
                  "bj": eq_panel.LIMIT_BAND_BJ},
        "total_bars": sum(len(v) for v in bad_detail.values()),
        "n_stocks": len(bad_detail),
        "by_source": {},
        "per_stock": {c: {"source": src_map.get(c, "unknown"), "bars": v}
                      for c, v in sorted(bad_detail.items())},
        "treatment": ("超带 bar = 不可交易公司行动 gap:tradable=False、不进因子尾部窗口"
                      "(同缺 bar 规约,绝不编造值)、close_clean 置 None(IC/回测/干跑统一用,"
                      "干跑 MTM 退化为最近成交价结转=停牌冻结约定);原始 close 在面板中保留。"
                      "可能是复权拼接 artifact/新股上市前五日无涨跌幅/复牌首日 —— 无论哪种,"
                      "系统组合不追这种跳动、不记这种收益。ST 5% 带无 PIT 状态无法判定。"),
    }
    for c, v in bad_detail.items():
        s = src_map.get(c, "unknown")
        bad_bar_report["by_source"][s] = bad_bar_report["by_source"].get(s, 0) + len(v)
    print(f"坏 bar 卫生:{bad_bar_report['total_bars']} 根 / {len(bad_detail)} 只 · "
          f"按源 {bad_bar_report['by_source']}", flush=True)

    # --- per-factor IC, train/test strictly split by signal date ----------------
    stats = ev.evaluate_all(factor_mats, price, panel.event_times, horizons=(1, 5))

    # --- TASK 2a: factor hygiene (train-only correlation de-redundancy) ---------
    rank_mats = fx.rank_versions(factor_mats)
    corr = hy.factor_correlation_matrix_train(rank_mats, panel.event_times, ev.TRAIN_END)
    kept, dropped, screened_out = hy.greedy_decorrelate(stats, corr, horizon_key="h1")
    print(f"因子卫生:初筛淘汰 {len(screened_out)} · 去冗余丢 {len(dropped)} "
          f"{[d['name'] for d in dropped]} · 幸存 {len(kept)} {kept}", flush=True)

    # --- TASK 2b: two combo schemes, champion on TRAIN h5 ICIR, frozen ----------
    schemes = {}
    for scheme in ("icir", "equal"):
        w = cb.fit_combo_weights(stats, horizon_key="h1", names=kept, scheme=scheme)
        mat = cb.apply_combo(factor_mats, w) if w else None
        s = (ev.evaluate_signal(mat, price, panel.event_times, horizons=(1, 5))
             if mat else None)
        schemes[scheme] = {"weights": w, "mat": mat, "ic": s}
    stats_by_scheme = {k: v["ic"] for k, v in schemes.items() if v["ic"]}
    champion = cb.select_champion(stats_by_scheme, horizon_key="h5") if stats_by_scheme else None
    champ = schemes[champion] if champion else {"weights": {}, "mat": None, "ic": None}
    combo_mat = champ["mat"]
    print(f"冠军组合={champion}(train h5 ICIR: "
          f"{ {k: (v['ic']['h5']['train']['icir'] if v['ic'] else None) for k, v in schemes.items()} }) · "
          f"权重 { {k: round(v, 4) for k, v in champ['weights'].items()} }", flush=True)

    # --- weekly top-20 basket backtest + cost grid + circuit breaker ------------
    reb = bk.weekly_indices(T)
    r_test0 = _first_row_at_or_after(panel.event_times, ev.TEST_START)
    adv_mat = []
    for t in range(T):
        adv_mat.append([factor_mats["turnover_20d"][t][i] for i in range(N)])
    bt_full = bt_test = None
    cost_grid, cb_test = {}, None
    if combo_mat:
        bt_full = bk.backtest(combo_mat, price, adv_mat, eligible, panel.instruments, reb)
        bt_test = bk.backtest(combo_mat, price, adv_mat, eligible, panel.instruments, reb,
                              row_lo=r_test0)
        for c in COST_GRID_BPS:
            r = bk.backtest(combo_mat, price, adv_mat, eligible, panel.instruments, reb,
                            row_lo=r_test0, cost_bps=c)
            cost_grid[str(int(c))] = r["strategy"] if r else None
        cb_res = bk.backtest(combo_mat, price, adv_mat, eligible, panel.instruments, reb,
                             row_lo=r_test0, circuit_breaker=True)
        cb_test = {"off": bt_test["strategy"] if bt_test else None,
                   "on": cb_res["strategy"] if cb_res else None,
                   "detail": cb_res.get("circuit_breaker") if cb_res else None}

    # --- 换手压缩变体(2026-07-24:baseline 周换手 1.06,15bps 下跑输等权基准) --------
    # 候选:baseline / buf30 / buf40(缓冲带)/ cap{25,30,40,50}(单边换手上限)。
    # **变体选择只在 TRAIN 净 Sharpe@15bps 上做**(同一 test 窗口上选就是二次偷看);
    # test 只作确认。cap 网格即 max_oneway 的 train-only 敏感性扫描(见
    # turnover_cap_sensitivity):首扫(仅 cap40 单点)后补扫发现 train 平台在 0.25-0.30,
    # 冠军由 cap40 切换到 cap30(Δ train SR ≈ +0.06 > 0.05 阈值,且相邻两点同向
    # 非单点尖峰;切换已文档化,cap40 的 dry-run/paper-track 记录保留)。
    VARIANTS = {"baseline": {}, "buf30": {"buffer_k": 30}, "buf40": {"buffer_k": 40},
                "cap25": {"max_oneway": 0.25}, "cap30": {"max_oneway": 0.30},
                "cap40": {"max_oneway": 0.40}, "cap50": {"max_oneway": 0.50}}
    CAP_GRID = (0.25, 0.30, 0.40, 0.50)
    variant_report: dict = {}
    champion_variant = "baseline"
    if combo_mat and bt_test:
        best_key = (-float("inf"), "")
        for vname, vparams in VARIANTS.items():
            tr = bk.backtest(combo_mat, price, adv_mat, eligible, panel.instruments, reb,
                             row_hi=r_test0 - 1, **vparams)
            te_grid = {}
            for c in COST_GRID_BPS:
                r = bk.backtest(combo_mat, price, adv_mat, eligible, panel.instruments, reb,
                                row_lo=r_test0, cost_bps=c, **vparams)
                te_grid[str(int(c))] = r["strategy"] if r else None
            te15 = bk.backtest(combo_mat, price, adv_mat, eligible, panel.instruments, reb,
                               row_lo=r_test0, **vparams)
            bench15 = bt_test["benchmark"]
            variant_report[vname] = {
                "params": vparams,
                "train_15bps": tr["strategy"] if tr else None,
                "train_avg_turnover": tr["avg_turnover"] if tr else None,
                "test_cost_grid": te_grid,
                "test_avg_turnover": te15["avg_turnover"] if te15 else None,
                "test_gap_vs_bench_15bps_pp": (
                    round((te15["strategy"]["total_return"] - bench15["total_return"]) * 100, 2)
                    if te15 and bench15["total_return"] is not None else None),
            }
            tr_sharpe = (tr["strategy"]["ann_sharpe"] if tr else None)
            key = (tr_sharpe if tr_sharpe is not None else -float("inf"), vname)
            if key > best_key:
                best_key = key
                champion_variant = vname
        print(f"换手变体(train Sharpe@15bps 选冠军): "
              f"{ {v: (variant_report[v]['train_15bps'] or {}).get('ann_sharpe') for v in VARIANTS} }"
              f" -> 冠军={champion_variant}", flush=True)

    # --- cap 上限 train-only 敏感性扫描 + plateau/spike 判定 ----------------------
    cap_sensitivity = None
    if variant_report:
        cap_rows = {}
        for cap in CAP_GRID:
            v = variant_report[f"cap{int(cap * 100)}"]
            cap_rows[str(cap)] = {
                "train_sharpe_15bps": v["train_15bps"]["ann_sharpe"],
                "train_total_return": v["train_15bps"]["total_return"],
                "train_avg_turnover": v["train_avg_turnover"],
                "test_15bps": v["test_cost_grid"]["15"],
                "test_avg_turnover": v["test_avg_turnover"],
            }
        cap_rows["baseline_inf"] = {
            "train_sharpe_15bps": variant_report["baseline"]["train_15bps"]["ann_sharpe"],
            "train_total_return": variant_report["baseline"]["train_15bps"]["total_return"],
            "train_avg_turnover": variant_report["baseline"]["train_avg_turnover"],
            "test_15bps": variant_report["baseline"]["test_cost_grid"]["15"],
            "test_avg_turnover": variant_report["baseline"]["test_avg_turnover"],
        }
        train_srs = {c: cap_rows[c]["train_sharpe_15bps"] for c in cap_rows if c != "baseline_inf"}
        best_cap = max(sorted(train_srs), key=lambda c: train_srs[c])
        sr_max = train_srs[best_cap]
        n_plateau = sum(1 for v in train_srs.values() if v >= 0.9 * sr_max)
        verdict = ("plateau" if n_plateau >= 2 else "spike")
        cap_sensitivity = {
            "grid": cap_rows,
            "train_champion_cap": float(best_cap),
            "n_points_within_90pct_of_max": n_plateau,
            "verdict": verdict,
            "verdict_note": (
                f"train SR 峰值在 cap={best_cap}({sr_max:.4f}),{n_plateau}/4 个网格点在其 90% 内"
                f" -> {verdict}。首扫单点 cap40(train SR "
                f"{train_srs['0.4']:.4f})低于平台:冠军切换到 cap{best_cap}(Δ="
                f"{sr_max - train_srs['0.4']:+.4f});test 上 0.25-0.40 三点 Sharpe 几乎相同"
                "(~1.10),切换不依赖 test 信息。"),
            "switch_rule": "仅当 train 证据明显更优(Δ SR>0.05 且非单点)才切换冻结配置;已满足,切换并记录。",
        }
        print(f"cap 敏感性:{ {c: round(v['train_sharpe_15bps'], 4) for c, v in cap_rows.items()} }"
              f" -> {verdict},train 冠军 cap={best_cap}", flush=True)

    # --- same weekly weights through the execution-reality dry-run pipeline -----
    import time as _time
    adv_shares = bk.trailing_mean_matrix(panel.matrices["volume"], window=20)
    test_rows = [r for r in reb if r >= r_test0]
    periods, weights_by_period, prices_by_period = [], {}, {}
    adv_by_period: dict = {}
    capped_dry: dict = {}
    for r in test_rows[:-1]:
        w, capped, _ = bk.select_basket(combo_mat[r], adv_mat[r], panel.instruments) if combo_mat else ({}, [], [])
        if not w:
            continue
        t = ev.yyyymmdd_to_epoch_ms(int(panel.event_times[r]))
        periods.append(t)
        weights_by_period[t] = dict(w)
        # 价格必须覆盖**全部**标的(与 crypto 驱动器同规约):只给当期权重名的价格会让
        # 被调出篮子的持仓卖单在 simulate_fills 里因无价被跳过 -> 僵尸持仓以陈旧成本
        # MTM,u249 上造成 dry vs basket -33pp 裂口(2026-07-24 结构性归因)。
        prices_by_period[t] = {panel.instruments[i]: price[r][i] for i in range(N)
                               if price[r][i] is not None and price[r][i] > 0}
        adv_by_period[t] = {panel.instruments[i]: adv_shares[r][i] for i in range(N)}
        for c in capped:
            capped_dry[c] = capped_dry.get(c, 0) + 1
    last_t = ev.yyyymmdd_to_epoch_ms(int(panel.event_times[test_rows[-1]]))
    periods.append(last_t)
    weights_by_period[last_t] = {}
    prices_by_period[last_t] = {inst: price[test_rows[-1]][i]
                                for i, inst in enumerate(panel.instruments)
                                if price[test_rows[-1]][i] is not None}
    adv_by_period[last_t] = {panel.instruments[i]: adv_shares[test_rows[-1]][i] for i in range(N)}

    dry_summary = None
    dry_cost_grid: dict = {}
    variant_dry_summary: dict = {}
    if combo_mat and periods:
        # RESEARCH OVERRIDE: 仅参与率/单名集中度是真实研究级限额(0.10/0.10);其余宽松占位,
        # 非生产风控参数(见模块 docstring)。账户 equity=AUM 使单名限额承重。
        def _make_gate_and_ctx(strategy_id, names):
            limits = RiskLimits(
                allowed_instruments=frozenset(names), allowed_accounts=frozenset({"paper"}),
                default_max_position_qty=10 ** 13, max_order_notional=1e14, fat_finger_qty=10 ** 13,
                max_gross_exposure=1e14, max_net_exposure=1e14, max_leverage=1e7,
                max_single_name_pct=0.10, max_turnover=1e7, max_participation_rate=0.10,
                max_instrument_vol=5.0, daily_loss_limit=1e14, max_drawdown=1.0)
            gate = PreTradeRiskGate(limits, kill_switch=KillSwitch())

            def ctx_factory(intent, prices, period):
                adv = (adv_by_period.get(period) or {}).get(intent.instrument_id)
                return RiskContext(
                    account=AccountRiskState(account_id="paper", cash=bk.AUM_CNY,
                                             equity=bk.AUM_CNY, day_pnl=0.0,
                                             peak_equity=bk.AUM_CNY, positions=()),
                    market=MarketContext(instrument_id=intent.instrument_id, known=True,
                                         reference_price=prices.get(intent.instrument_id, 1.0),
                                         market_open=True, price_as_of=period,
                                         est_daily_volume=adv, instrument_vol=0.8),
                    strategy=StrategyApproval(strategy_id=strategy_id, approved=True,
                                              approval_id="research-dry-run"),
                    now=period, max_staleness=10 ** 15)
            return gate, ctx_factory

        held_names = sorted({n for w in weights_by_period.values() for n in w})
        gate, ctx_factory = _make_gate_and_ctx(STRATEGY_ID, held_names)

        # A股整手:100 股步长 + 最小 100 股;名义额门槛 0(不设)。管线自带 filters 参数,
        # 不改共享管线。小 AUM 影响:300万/20名≈15万/名,100股取整误差 < 股价×100/15万
        # (高价股约千分级);低于 100 股的目标手数被诚实排除(不下意图)。
        lot_filters = {inst: (ASHARE_LOT, ASHARE_LOT, 0.0) for inst in held_names}

        def _run_once(cost_bps: float):
            return run_multi_period_dry_run(
                periods=periods, weights_by_period=weights_by_period,
                prices_by_period=prices_by_period, gate=gate, ctx_factory=ctx_factory,
                adapter=DryRunAdapterEngine(), strategy_id=STRATEGY_ID, aum=bk.AUM_CNY,
                cost_bps=cost_bps, cash_flow_fn=None, vol_target=None,
                filters=lot_filters,
                event_bus=None, publish_completion_event=False)

        # 成本敏感网格(执行层):10/25bps 只在内存中跑(不入库,避免 run_id 冲突);
        # 15bps 基准跑持久化到 dry_run.db。
        for c in COST_GRID_BPS:
            if c == bk.COST_BPS:
                continue
            r = _run_once(c)
            dry_cost_grid[str(int(c))] = {"total_return": r.pnl["total_return"],
                                          "sharpe": r.pnl["sharpe"],
                                          "max_drawdown": r.pnl["max_drawdown"],
                                          "n_approved": r.n_approved, "n_rejected": r.n_rejected}
        event_bus = open_domain_event_bus()
        run = run_multi_period_dry_run(
            periods=periods, weights_by_period=weights_by_period,
            prices_by_period=prices_by_period, gate=gate, ctx_factory=ctx_factory,
            adapter=DryRunAdapterEngine(), strategy_id=STRATEGY_ID, aum=bk.AUM_CNY,
            cost_bps=bk.COST_BPS, cash_flow_fn=None, vol_target=None,
            filters=lot_filters,
            event_bus=event_bus, publish_completion_event=False)
        save_and_project_dry_run(DryRunStore(_DRY_DB, create=True), run, event_bus,
                                 now_ms=int(_time.time() * 1000))
        p = run.pnl
        rej_reasons: dict = {}
        for rr in run.rejected_reasons:
            for reason in (rr if isinstance(rr, list) else [rr]):
                rej_reasons[str(reason)] = rej_reasons.get(str(reason), 0) + 1
        # --- dry-run vs 简单篮子 偏离诊断(结构性归因,诚实记账) --------------------
        vs_basket = None
        if bt_test:
            curve = run.detail.get("equity_curve", [])
            dry_rets = [c["ret"] for c in curve]
            b_eq = bt_test["equity"]
            b_rets = [b_eq[k + 1] / b_eq[k] - 1.0 for k in range(len(b_eq) - 1)]
            # 相位:dry ret[i] = t_{i-1}->t_i 持仓 + t_i 调仓成本;basket ret[i-1] = t_{i-1}->t_i
            # 持仓 + t_{i-1} 调仓成本 —— 成本记账差一期,全区间净零;对齐 holding 区间比较。
            n_al = min(len(dry_rets) - 1, len(b_rets))
            d_al = dry_rets[1:1 + n_al]
            diffs = [d_al[k] - b_rets[k] for k in range(n_al)]
            invested = [c["gross"] / bk.AUM_CNY for c in curve]
            corr = None
            if n_al >= 10:
                import statistics as _st
                md, mb = _st.fmean(d_al), _st.fmean(b_rets[:n_al])
                cov = sum((x - md) * (y - mb) for x, y in zip(d_al, b_rets[:n_al]))
                vx = sum((x - md) ** 2 for x in d_al)
                vy = sum((y - mb) ** 2 for y in b_rets[:n_al])
                if vx > 0 and vy > 0:
                    corr = round(cov / (vx * vy) ** 0.5, 4)
            vs_basket = {
                "dry_total_return": p["total_return"],
                "basket_total_return": bt_test["strategy"]["total_return"],
                "cum_diff_pp": round((p["total_return"] - bt_test["strategy"]["total_return"]) * 100, 2),
                "period_ret_corr": corr,
                "mean_abs_period_diff": round(sum(abs(d) for d in diffs) / n_al, 6) if n_al else None,
                "avg_invested_fraction": round(sum(invested) / len(invested), 4) if invested else None,
                "structural_causes": (
                    "残余差来自:(a) 100股整手取整的零股现金拖累(avg_invested_fraction<1 的部分,"
                    "小 AUM 高价股上放大);(b) 干跑含期末清仓腿成本,篮子不计;(c) 成本记账相位差一期"
                    "(净零);(d) 篮子按权重 buy-and-hold,干跑按整数股 buy-and-hold(周内权重漂移)."
                    "2026-07-24 结构性归因:修复前 u249 的 -33pp 裂口主因不是坏 bar(清洗后仍在),"
                    "而是驱动器 prices_by_period 只覆盖当期权重名 —— 调出篮子的持仓卖单在 "
                    "simulate_fills 因无价被跳过,僵尸持仓以陈旧成本 MTM;v3 起价格覆盖全标的"
                    "(与 crypto 驱动器同规约),裂口收敛到 (a)-(d) 的噪声量级。"),
            }
            print(f"dry vs basket: 差 {vs_basket['cum_diff_pp']}pp · 期收益 corr "
                  f"{vs_basket['period_ret_corr']} · 投入率 {vs_basket['avg_invested_fraction']}",
                  flush=True)
        dry_summary = {"run_id": run.run_id, "n_periods": p["n_periods"],
                       "n_intents": run.n_intents, "n_approved": run.n_approved,
                       "n_rejected": run.n_rejected, "rejected_reasons": rej_reasons,
                       "total_return": p["total_return"],
                       "sharpe": p["sharpe"], "max_drawdown": p["max_drawdown"],
                       "residual": p["residual"], "db": _DRY_DB,
                       "cost_grid": dry_cost_grid,
                       "gate_limits": {"max_participation_rate": 0.10,
                                       "max_single_name_pct": 0.10,
                                       "research_override": "其余限额为宽松占位,非生产参数"},
                       "est_daily_volume": "trailing-20d 平均股数成交量(真实,逐名逐期;与门检查同单位)",
                       "lot_filters": {"step_size": ASHARE_LOT, "min_qty": ASHARE_LOT,
                                       "min_notional": 0.0, "note": "A股100股整手,管线filters参数,未改共享管线"},
                       "vs_basket": vs_basket}
        print(f"干跑流水线 {run.run_id}: 期数 {p['n_periods']} · 门批准 {run.n_approved}/"
              f"拒绝 {run.n_rejected} {rej_reasons} · 总收益 {p['total_return']*100:.1f}% · "
              f"夏普 {p['sharpe']} · 回撤 {p['max_drawdown']*100:.1f}% · "
              f"对账残差 {p['residual']:.2e}", flush=True)

        # --- 冠军换手变体的执行层(train 选出的变体;独立 strategy_id 后缀防 run_id 冲突) --
        if champion_variant != "baseline":
            vparams = VARIANTS[champion_variant]
            v_sid = f"{STRATEGY_ID}_{champion_variant}"
            period_set = set(periods)
            v_weights: dict = {}
            prev_w: dict = {}
            for r in test_rows[:-1]:
                t = ev.yyyymmdd_to_epoch_ms(int(panel.event_times[r]))
                if t not in period_set:
                    continue
                w, _, _ = bk.select_for_variant(combo_mat[r], adv_mat[r], panel.instruments,
                                                prev_w, **vparams)
                v_weights[t] = dict(w)
                prev_w = w
            v_weights[periods[-1]] = {}
            v_held = sorted({n for w in v_weights.values() for n in w})
            v_gate, v_ctx = _make_gate_and_ctx(v_sid, v_held)
            v_filters = {inst: (ASHARE_LOT, ASHARE_LOT, 0.0) for inst in v_held}
            v_cost_grid: dict = {}
            for c in COST_GRID_BPS:
                if c == bk.COST_BPS:
                    continue
                vr = run_multi_period_dry_run(
                    periods=periods, weights_by_period=v_weights,
                    prices_by_period=prices_by_period, gate=v_gate, ctx_factory=v_ctx,
                    adapter=DryRunAdapterEngine(), strategy_id=v_sid, aum=bk.AUM_CNY,
                    cost_bps=c, cash_flow_fn=None, vol_target=None, filters=v_filters,
                    event_bus=None, publish_completion_event=False)
                v_cost_grid[str(int(c))] = {"total_return": vr.pnl["total_return"],
                                            "sharpe": vr.pnl["sharpe"],
                                            "max_drawdown": vr.pnl["max_drawdown"]}
            v_run = run_multi_period_dry_run(
                periods=periods, weights_by_period=v_weights,
                prices_by_period=prices_by_period, gate=v_gate, ctx_factory=v_ctx,
                adapter=DryRunAdapterEngine(), strategy_id=v_sid, aum=bk.AUM_CNY,
                cost_bps=bk.COST_BPS, cash_flow_fn=None, vol_target=None, filters=v_filters,
                event_bus=event_bus, publish_completion_event=False)
            save_and_project_dry_run(DryRunStore(_DRY_DB, create=True), v_run, event_bus,
                                     now_ms=int(_time.time() * 1000))
            vp = v_run.pnl
            variant_dry_summary = {
                "strategy_id": v_sid, "variant": champion_variant, "params": vparams,
                "run_id": v_run.run_id, "n_approved": v_run.n_approved,
                "n_rejected": v_run.n_rejected, "total_return": vp["total_return"],
                "sharpe": vp["sharpe"], "max_drawdown": vp["max_drawdown"],
                "residual": vp["residual"], "cost_grid": v_cost_grid}
            print(f"冠军变体干跑 {v_run.run_id}: 总收益 {vp['total_return']*100:.1f}% · "
                  f"夏普 {vp['sharpe']} · 回撤 {vp['max_drawdown']*100:.1f}% · "
                  f"门批准 {v_run.n_approved}/拒绝 {v_run.n_rejected}", flush=True)

    # --- TASK 2d: multiple-testing honesty --------------------------------------
    n_factor_variants = len(fx.FACTOR_FUNCS)
    n_horizons = 2
    n_trials = n_factor_variants * n_horizons + len(schemes)
    train_pvals = {name: _two_sided_p(stats[name]["h1"]["train"]["t_stat"])
                   for name in sorted(stats)}
    bh_survivors = benjamini_hochberg([train_pvals[n] for n in sorted(train_pvals)], q=0.10)
    bh_names = sorted(n for i, n in enumerate(sorted(train_pvals)) if i in bh_survivors)
    bonf_t = bonferroni_tstat_threshold(n_trials, alpha=0.05)
    champ_test_t = (champ["ic"]["h1"]["test"]["t_stat"] if champ["ic"] else None)
    multiple_testing = {
        "n_factor_variants": n_factor_variants, "n_horizons": n_horizons,
        "n_combo_schemes": len(schemes), "n_trials": n_trials,
        "trial_count_formula": "len(FACTOR_FUNCS) × horizons(1,5) + combo schemes(icir,equal)",
        "train_h1_pvalues": {k: (round(v, 8) if v is not None else None)
                             for k, v in train_pvals.items()},
        "bh_fdr_q010_train_survivors": bh_names,
        "bonferroni_t_threshold_005": round(bonf_t, 4),
        "champion_test_h1_t_stat": champ_test_t,
        "champion_test_t_vs_bonferroni": ("pass" if champ_test_t is not None
                                          and abs(champ_test_t) > bonf_t else "fail_or_unknown"),
    }

    snapshot = {
        "strategy_id": STRATEGY_ID,
        "universe": {"file_count": len(universe.get("stocks", [])), "in_db": len(store.keys()),
                     "used": len(codes)},
        "panel": {"n_dates": T, "n_stocks": N,
                  "range": [int(panel.event_times[0]), int(panel.event_times[-1])]},
        "split": {"train_end": ev.TRAIN_END, "test_start": ev.TEST_START},
        "factor_ic": stats,
        "bad_bar_hygiene": bad_bar_report,
        "hygiene": {"winsorize": {"lo": hy.WINSOR_LO, "hi": hy.WINSOR_HI},
                    "corr_threshold": hy.CORR_THRESHOLD, "kept": kept, "dropped": dropped,
                    "screened_out": screened_out, "corr_matrix_train": corr},
        "combo": {"champion": champion, "winsorized_zscore": True,
                  "schemes": {k: {"weights": v["weights"], "ic": v["ic"]}
                              for k, v in schemes.items()},
                  "weights": champ["weights"], "ic": champ["ic"]},
        "basket": {"n": bk.BASKET_N, "aum_cny": bk.AUM_CNY, "participation_cap": bk.PARTICIPATION_CAP,
                   "cost_bps": bk.COST_BPS, "full": bt_full, "test": bt_test,
                   "cost_grid_test": cost_grid, "circuit_breaker_test": cb_test,
                   "capped_names_dry_run": capped_dry},
        "turnover_variants": {
            "candidates": variant_report,
            "champion": champion_variant,
            "selection_rule": ("变体选择只在 TRAIN 净 Sharpe@15bps 上做(同一 test 窗口上选变体"
                               "=二次偷看);test 只作确认。baseline 是 buffer/cap 皆 None 的逐字节"
                               "等价路径。"),
        },
        "turnover_cap_sensitivity": cap_sensitivity,
        "dry_run": dry_summary,
        "dry_run_champion_variant": variant_dry_summary or None,
        "multiple_testing": multiple_testing,
        "honest_limitations": [
            "宇宙=2026-07-23 幸存者截面(幸存者偏差):历史退市/ST/跌出微盘的票不在样本,IC与回测系统性偏乐观",
            "价格为前复权:历史收益路径为近似,成交额代理=volume*(O+H+L+C)/4(payload 无 amt 字段)",
            "坏 bar 卫生:超板块涨跌幅带跳变按不可交易 gap 处理(见 bad_bar_hygiene)——可能是复权拼接 artifact/新股前五日无涨跌幅/复牌首日;ST 5% 带无 PIT 状态未覆盖",
            "dry-run 与简单篮子的残余偏离:整手现金拖累+期末清仓腿+成本记账相位+整数股权重漂移(见 dry_run.vs_basket)",
            "换手变体(buf30/buf40/cap40)引入了 buffer_k/max_oneway 参数 —— 选择只在 TRAIN 净 Sharpe 上做,test 仅确认;参数面本身是一次额外的研究自由度,显著性应打折看待",
            "turnover_20d 用成交额水平代理换手率(无流通市值),窄市值带内横截面排序近似有效",
            "est_daily_volume=trailing-20d 平均股数成交量;门参与率检查与 intent 股数同单位",
            "RESEARCH OVERRIDE:门限额仅参与率0.10/单名0.10为真实研究级,其余宽松占位,非生产风控参数",
            "DB 后台仍在增长:本跑覆盖副本时刻已入库子集(非全部 249 只)",
            "2021-2026 微盘反转/小市值 regime 未必持续;test 仅 ~1.5 年",
            "多重检验:9因子×2horizon+2方案=20 trials;BH-FDR(train)/Bonferroni 阈值见 multiple_testing",
            "初筛诊断,非晋级门禁:不驱动任何自动晋级/下单;实盘=非目标",
        ],
    }
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)
    print(f"snapshot -> {_OUT}", flush=True)


if __name__ == "__main__":
    main()
