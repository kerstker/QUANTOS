"""退出策略实验室驱动器(W4,spec §8)→ `evidence/exit_policy_lab.json`。

把 8 个退出策略 × 2 本书(crowd_contra 周度面板书 / btc_spot_trend 迟滞趋势书)过**同一条**
`run_multi_period_dry_run`(门/对账/成本零改动):策略只经 `apply_exit_policy` 后处理权重流
(信号权重 × [0,1] 乘数),baseline_signal_only 恒为对照。每本书 × 策略报:增量净 PnL、夏普增量、
maxDD 增量、盈利因子、右尾贡献(top-decile 期份额)、交易数、成本合计与增量、增量集中度
(selection_warning 诚实);选择规则**预注册**在制品里(见 SELECTION_RULE_TEXT),判定
"worth further study" 必须同时过 4 条(夏普↑ / maxDD 不恶化>2pp / 增量不集中 / PF≥基线),
排名永远多准则、绝不以最终净 PnL 单独排名(spec §2.3:退出不得掩盖无效 Alpha——每本书先报
「纯信号退出下底层信号是否盈利」,一切策略都是该基线上的增量)。

成本口径:与 canonical 驱动器同价同 seed 同 AUM;成本合计用**零成本孪生跑**精确反解
(同一权重流成本 0 vs cost_bps 的最终权益差 = 佣金总额——手数只取决于固定 AUM 与价格,
与成本无关,故两跑成交逐笔相同)。crowd=10bps(canonical 同);btc=5bps 敏感性口径
(canonical 0 费率已在 dry_run.db 存证,实验室要看得见成本增量)。

与 canonical 存盘 run 的两点**刻意偏差**(实验室内自洽,不影响结论):
1. 不叠 crowding gate(那是独立的已实现风险覆盖层,非"纯信号基线"的一部分);
2. 每个 book 名单全期定价(canonical crowd 驱动器只给当期腿定价——信号轮换出的名单
   平仓单缺价被跳过会留陈旧持仓;实验室全期定价保证轮换/退出干净成交)。

实验数据全部 PIT:Binance 日线走 t+1d available-time 并拒绝旧 open-time 行；MEXC
仍走其既有 own-event-time bulk 读；退出核心再按决策点过滤。
不写 dry_run.db、不发事件、无网络、无墙钟进制品(as_of 取数据最大时间戳)。
旧实现保留供合成输入下的纯函数测试和 V2 迁移审计；CLI 在读取历史缓存或写制品前
无条件隔离，避免重新生成 `INVALIDATED_PERP_LEDGER_V1` 盈利声明。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.data.available_time import fetch_bars_for_event_decisions
from services.research import alpha_expression as ax
from services.research import exit_policies as ep
from services.execution.backtest_authority import quarantine_legacy_backtest
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from tools.cache_io import atomic_write_json
from tools.crypto_oi_research import build_panel, _legs, QUANTILE
from tools.crypto_oi_verify import CROWD_CONTRA
from tools.dry_run_btc_spot_multi import _signal_series_hysteresis

_DB_FUNDING = os.path.join("data", "cache", "crypto_funding.db")
_DB_MEXC = os.path.join("data", "cache", "crypto_mexc.db")
_OUT = os.path.join("evidence", "exit_policy_lab.json")

_AUM = 1_000_000.0
_WEEK_MS = 7 * 86_400_000
_COST_BPS = {"crowd_contra": 10.0, "btc_spot_trend": 5.0}
_VOL_TARGET = {
    "crowd_contra": {"target_ann": 0.10, "lookback": 8, "lev_cap": 3.0, "ppy": 52},
    "btc_spot_trend": {"target_ann": 0.10, "lookback": 8, "lev_cap": 1.0, "ppy": 52},
}


@dataclass
class LabBook:
    """一本书的全部实验输入(权重流已归一化:零权重省略,与管线 q==0 跳过语义一致)。"""
    name: str
    strategy_id: str
    periods: list
    weights_by_period: dict
    prices_by_period: dict
    bars_by_instrument: dict            # name -> tuple[ExitBar](全历史,as_of 过滤在核心)
    cost_bps: float
    vol_target: dict
    cash_flow_fn: Optional[Callable] = None
    vol_by_period: Optional[dict] = None
    data_source: str = ""
    note: str = ""


def _to_exit_bars(rows) -> tuple:
    return tuple(ep.ExitBar(int(b.event_time), float(b.open), float(b.high),
                            float(b.low), float(b.close)) for b in rows)


def _normalize(weights_by_period) -> dict:
    """零权重省略(基线恒等式的前提:管线里 0 权重本就无目标手数)。"""
    return {int(t): {i: float(w) for i, w in w_t.items() if w != 0.0}
            for t, w_t in weights_by_period.items()
            if any(w != 0.0 for w in w_t.values())}


def build_crowd_book(db_path: str = _DB_FUNDING) -> LabBook:
    """crowd_contra 周度面板书:与 tools/dry_run_crowd_contra_multi.py 同一信号/腿/采样,
    差异 = 无 crowding-gate 覆盖层 + 全 book 名单全期定价(见模块 docstring)。"""
    panel, instruments = build_panel()
    insts = panel.instruments
    close = panel.matrices["close"]
    fmat = panel.matrices["funding"]
    ev = panel.event_times
    sig = ax.evaluate(CROWD_CONTRA, panel)
    rows, last = [], None
    for r in range(len(ev)):
        if last is None or ev[r] - last >= _WEEK_MS:
            rows.append(r)
            last = ev[r]
    periods, weights, prices, rates = [], {}, {}, {}
    book_names = set()
    for r in rows:
        legs = _legs(sig[r], QUANTILE)
        if not legs:
            continue
        t = int(ev[r])
        periods.append(t)
        weights[t] = {insts[i]: w for i, w in legs.items()}
        prices[t] = {insts[i]: close[r][i] for i in legs
                     if close[r][i] and close[r][i] > 0}
        rates[t] = {insts[i]: (fmat[r][i] or 0.0) for i in legs}
        book_names |= {insts[i] for i in legs}
    store = CachingPITStore(SqlitePITStore(db_path, create=False))
    close_by_name = {n: {} for n in book_names}
    bars = {}
    for n in sorted(book_names):
        rows_n = fetch_bars_for_event_decisions(
            store,
            n,
            -10**18,
            10**18,
            decision_lag=86_400_000,
            minimum_knowledge_lag=86_400_000,
        )
        bars[n] = _to_exit_bars(rows_n)
        for b in rows_n:
            close_by_name[n][int(b.event_time)] = float(b.close)
    # 全 book 名单全期定价(偏差 2):每期给所有名单内收盘,保证轮换/退出干净成交+MTM
    full_prices = {t: {n: close_by_name[n][t] for n in book_names
                       if close_by_name[n].get(t) and close_by_name[n][t] > 0}
                   for t in periods}

    def cash_flow_fn(held, px, period):
        r = rates.get(period, {})
        return sum(-r.get(i, 0.0) * q * px.get(i, 0.0) for i, q in held.items())

    return LabBook("crowd_contra", "crowd_contra", periods, _normalize(weights),
                   full_prices, bars, _COST_BPS["crowd_contra"],
                   dict(_VOL_TARGET["crowd_contra"]), cash_flow_fn=cash_flow_fn,
                   data_source="crypto_funding.db(PIT)",
                   note="OI 大币周度截面多空;资金费现金流在现金流的真边;无 crowding-gate 覆盖层")


def build_btc_book(db_path: str = _DB_MEXC) -> LabBook:
    """btc_spot_trend 迟滞趋势书:`_signal_series_hysteresis`(Donchian20∧动量30 进场,
    动量转负退场),MEXC PIT 日线,每 7 bar 一期(canonical 同网格),现货 long-only。"""
    store = CachingPITStore(SqlitePITStore(db_path, create=False))
    bars = sorted(store.fetch_bars_pit("BTC", -10 ** 18, 10 ** 18), key=lambda b: b.event_time)
    closes = [b.close for b in bars]
    sig = _signal_series_hysteresis(closes)
    periods, weights, prices, vols = [], {}, {}, {}
    for i in range(0, len(bars), 7):
        b = bars[i]
        if b.close <= 0:
            continue
        t = int(b.event_time)
        periods.append(t)
        if sig[i] > 0:
            weights[t] = {"BTC": 1.0}
        prices[t] = {"BTC": float(b.close)}
        vols[t] = float(b.volume) if b.volume and b.volume > 0 else 1e9
    return LabBook("btc_spot_trend", "btc_spot_trend_hold", periods, _normalize(weights),
                   prices, {"BTC": _to_exit_bars(bars)}, _COST_BPS["btc_spot_trend"],
                   dict(_VOL_TARGET["btc_spot_trend"]), cash_flow_fn=None,
                   vol_by_period=vols, data_source="crypto_mexc.db(MEXC PIT)",
                   note="BTC 现货 long-only 迟滞趋势;5bps 敏感性成本口径(canonical 0 费率另存)")


def _make_gate_and_ctx(book: LabBook):
    """与两个 canonical 驱动器同构的宽限门(实验室要测的是退出策略,不是门限)。"""
    ks = KillSwitch()
    limits = RiskLimits(
        allowed_instruments=frozenset(book.bars_by_instrument), allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=10 ** 13, max_order_notional=1e14, fat_finger_qty=10 ** 13,
        max_gross_exposure=1e14, max_net_exposure=1e14, max_leverage=1e7, max_single_name_pct=1e7,
        max_turnover=1e7, max_participation_rate=1e7, max_instrument_vol=5.0, daily_loss_limit=1e14,
        max_drawdown=1.0)
    gate = PreTradeRiskGate(limits, kill_switch=ks)

    def ctx_factory(intent, prices, period):
        return RiskContext(
            account=AccountRiskState(account_id="paper", cash=_AUM * 10, equity=_AUM * 10,
                                     day_pnl=0.0, peak_equity=_AUM * 10, positions=()),
            market=MarketContext(instrument_id=intent.instrument_id, known=True,
                                 reference_price=prices.get(intent.instrument_id, 1.0),
                                 market_open=True, price_as_of=period,
                                 est_daily_volume=(book.vol_by_period or {}).get(period, 1e12),
                                 instrument_vol=0.8),
            strategy=StrategyApproval(strategy_id=book.strategy_id, approved=True,
                                      approval_id="research-exit-lab"),
            now=period, max_staleness=10 ** 15)

    return gate, ctx_factory


def _run_stream(
    book: LabBook,
    weights: dict,
    cost_bps: float,
    gate,
    ctx_factory,
    suffix: str,
    *,
    runner: Callable,
):
    """一条权重流过同一管线(不落库、不发事件)。返回 DryRunRun。"""
    return runner(
        periods=book.periods, weights_by_period=weights, prices_by_period=book.prices_by_period,
        gate=gate, ctx_factory=ctx_factory, adapter=DryRunAdapterEngine(),
        strategy_id=f"exitlab_{book.strategy_id}_{suffix}", aum=_AUM, cost_bps=cost_bps,
        cash_flow_fn=book.cash_flow_fn, vol_target=book.vol_target,
        event_bus=None, publish_completion_event=False)


def _curve(run) -> list:
    return list((run.detail or {}).get("equity_curve") or [])


def _abs_metrics(run, run_zero_cost, events) -> dict:
    curve = _curve(run)
    equities = [p["equity"] for p in curve]
    pnls = ep.period_dollar_pnl(equities, _AUM)
    n_exit = sum(1 for e in events if e.action == ep.EXIT and e.reason_code != "STOP_LOCKOUT")
    n_lockout = sum(1 for e in events if e.reason_code == "STOP_LOCKOUT")
    n_reduce = sum(1 for e in events if e.action == ep.REDUCE)
    return {
        "net_pnl": round(equities[-1] - _AUM, 6) if equities else 0.0,
        "total_return": run.pnl["total_return"],
        "sharpe": run.pnl["sharpe"],
        "max_drawdown": run.pnl["max_drawdown"],
        "sharpe_vt": run.pnl.get("sharpe_vt"),
        "max_drawdown_vt": run.pnl.get("max_drawdown_vt"),
        "profit_factor": ep.profit_factor(pnls),
        "right_tail_share": ep.right_tail_share(pnls),
        "trade_count": int((run.detail or {}).get("n_fills", 0)),
        "cost_total": round(_curve(run_zero_cost)[-1]["equity"] - equities[-1], 6) if equities else 0.0,
        "n_exit_events": n_exit, "n_lockout_periods": n_lockout, "n_reduce_events": n_reduce,
        "residual": run.pnl["residual"],
    }


def _delta_metrics(base: dict, pol: dict) -> dict:
    def d(key, nd=6):
        a, b = base.get(key), pol.get(key)
        if a is None or b is None:
            return None
        return round(b - a, nd)
    return {"delta_net_pnl": d("net_pnl"), "delta_sharpe": d("sharpe", 4),
            "delta_max_drawdown": d("max_drawdown", 4), "delta_profit_factor": d("profit_factor", 4),
            "delta_trade_count": (pol["trade_count"] - base["trade_count"]),
            "delta_cost_total": d("cost_total")}


def _run_book_legacy_diagnostic(
    book: LabBook,
    *,
    runner: Callable,
    policies: Optional[Sequence[str]] = None,
) -> dict:
    """一本书 × 全部策略:同一门/同一成本/同一 AUM,逐策略后处理权重流 → 管线 → 增量指标。"""
    names = list(policies) if policies is not None else list(ep.POLICY_REGISTRY)
    if "baseline_signal_only" not in names:
        names = ["baseline_signal_only"] + names
    gate, ctx_factory = _make_gate_and_ctx(book)
    streams, runs, runs0 = {}, {}, {}
    for name in names:
        stream = ep.apply_exit_policy(periods=book.periods, weights_by_period=book.weights_by_period,
                                      bars_by_instrument=book.bars_by_instrument, policy=name)
        streams[name] = stream
        runs[name] = _run_stream(
            book,
            stream.weights,
            book.cost_bps,
            gate,
            ctx_factory,
            name,
            runner=runner,
        )
        runs0[name] = _run_stream(
            book,
            stream.weights,
            0.0,
            gate,
            ctx_factory,
            f"{name}_c0",
            runner=runner,
        )
    base_abs = _abs_metrics(runs["baseline_signal_only"], runs0["baseline_signal_only"],
                            streams["baseline_signal_only"].events)
    base_pnls = ep.period_dollar_pnl([p["equity"] for p in _curve(runs["baseline_signal_only"])], _AUM)
    out_policies = {}
    for name in names:
        if name == "baseline_signal_only":
            continue
        pol_abs = _abs_metrics(runs[name], runs0[name], streams[name].events)
        pol_pnls = ep.period_dollar_pnl([p["equity"] for p in _curve(runs[name])], _AUM)
        assert len(pol_pnls) == len(base_pnls), "同一 periods 网格,逐期必须对齐"
        deltas = _delta_metrics(base_abs, pol_abs)
        conc = ep.delta_concentration([p - b for p, b in zip(pol_pnls, base_pnls)])
        sel = ep.evaluate_selection(
            sharpe_delta=deltas["delta_sharpe"], mdd_delta=deltas["delta_max_drawdown"],
            profit_factor_value=pol_abs["profit_factor"],
            baseline_profit_factor=base_abs["profit_factor"],
            delta_pnl=conc["total_delta"], periods_for_delta=conc["periods_for_delta"])
        reg = ep.POLICY_REGISTRY[name]
        out_policies[name] = {**pol_abs, **deltas, "concentration": conc,
                              "selection": sel, "selection_warning": conc["selection_warning"],
                              "policy_version": reg.version,
                              "params": {k: v for k, v in vars(reg.params).items()}}
    # 多准则排名(绝不以净 PnL 单独排名):夏普增量降序 / maxDD 增量升序 / PF 增量降序
    by_sharpe = sorted(out_policies, key=lambda n: -(out_policies[n]["delta_sharpe"] or -1e18))
    by_mdd = sorted(out_policies, key=lambda n: (out_policies[n]["delta_max_drawdown"]
                                                 if out_policies[n]["delta_max_drawdown"] is not None else 1e18))
    by_pf = sorted(out_policies, key=lambda n: -(out_policies[n]["delta_profit_factor"] or -1e18)
                   if out_policies[n]["delta_profit_factor"] is not None else 1e18)
    rankings = {"by_sharpe_delta_desc": by_sharpe, "by_maxdd_delta_asc": by_mdd,
                "by_profit_factor_delta_desc": by_pf,
                "note": "multi-criteria ranking only; selection by final net PnL alone is forbidden (spec §8/§2.3)"}
    profitable = (base_abs["sharpe"] or 0) > 0 and base_abs["net_pnl"] > 0
    base_abs["signal_only_assessment"] = (
        "PROFITABLE_WITH_SIGNAL_ONLY_EXITS" if profitable else "NOT_PROFITABLE_WITH_SIGNAL_ONLY_EXITS")
    worth = [n for n in out_policies if out_policies[n]["selection"]["worth_further_study"]]
    verdict = (
        f"signal-only baseline: sharpe={base_abs['sharpe']} net=${base_abs['net_pnl']:,.0f} "
        f"maxDD={base_abs['max_drawdown'] * 100:.1f}% PF={base_abs['profit_factor']} → "
        + ("; ".join(f"{n} worth further study" for n in worth) if worth
           else "no exit policy clears the pre-registered bar — signal-only exits are the "
                "honest default for this book"))
    return {"strategy_id": book.strategy_id, "data_source": book.data_source, "note": book.note,
            "n_periods": len(book.periods), "aum": _AUM, "cost_bps": book.cost_bps,
            "cadence": "weekly_decision_grid", "baseline": base_abs,
            "policies": out_policies, "rankings": rankings, "verdict": verdict,
            "worth_further_study": worth}


def run_book(*_args, **_kwargs) -> dict:
    quarantine_legacy_backtest("tools.exit_policy_lab.run_book")


def build_artifact(book_results: dict, omitted: Optional[list] = None) -> dict:
    """组装制品(无墙钟——as_of 取数据最大期戳,两次运行逐字节一致)。"""
    as_of = 0
    for res in book_results.values():
        # periods 不在 res 里;由调用方把最大期戳放进 res["_as_of"](不进制品)
        as_of = max(as_of, int(res.pop("_as_of", 0)))
    return {
        "schema_version": 1,
        "evidence_class": "RESEARCH_DIAGNOSTIC",
        "provenance": "REAL_EXIT_POLICY_LAB",
        "readiness": "L2_DRY_RUN_ONLY",
        "as_of": as_of,
        "selection_rule": {"version": ep.SELECTION_RULE_VERSION, "text": ep.SELECTION_RULE_TEXT,
                           "thresholds": {"mdd_tolerance": 0.02, "min_periods_for_delta": 5,
                                          "soft_warning_periods": 10}},
        "metric_definitions": {
            "profit_factor": "sum(positive period $PnL) / |sum(negative period $PnL)|; None if no losing period",
            "right_tail_share": "share of total net $PnL from the top ceil(n/10) periods by $PnL; None if total <= 0 (>1 means the rest of the book nets negative)",
            "delta_concentration": "min count of top period (policy-baseline) $ deltas needed to cover the total positive delta; <5 hard-fails selection, <10 warns",
            "cost_total": "final_equity(zero-cost twin run) - final_equity(cost run); fills are identical across the twin (quantities depend on fixed AUM+prices only)",
            "incremental": "every delta is policy minus signal-only baseline on the SAME periods/costs/AUM/seed"},
        "policies": [{"name": p.name, "version": p.version, "description": p.description,
                      "params": {k: v for k, v in vars(p.params).items()},
                      "threshold_provenance": "UNCALIBRATED_ENGINEERING_DEFAULT"}
                     for p in ep.POLICY_REGISTRY.values()],
        "books": book_results,
        "books_omitted": omitted or [],
        "honest_limits": [
            "退出判定只在决策点(周度调仓网格)用尾随日线做出;周内止损路径未模拟——对所有策略与基线同一网格,相对比较公平,绝对数值偏乐观。",
            "每策略单一参数点(UNCALIBRATED 工程默认),非参数扫描——扫描会放大试验数;预注册规则已按 4 条件把关。",
            "实验室基线不叠 crowding gate、且全 book 名单全期定价——与 canonical 存盘 run 的两点刻意偏差(见驱动器 docstring),实验室内自洽。",
            "全样本已知历史内的诊断,非前向证据;过关策略只赢得「进一步研究」,不赢得晋级/仓位。",
            "RECOVERY ramp 等 W6 组合级语义在仓位级简化为「乘数随修复即时回升」(无 ramp)。",
        ],
    }


def main() -> None:
    quarantine_legacy_backtest("tools.exit_policy_lab")

    # Unreachable while V1 timing/perpetual-ledger semantics are invalidated.
    # The pure builders/runners above remain available to synthetic unit tests.
    books = [build_crowd_book()]
    omitted = []
    if os.path.isfile(_DB_MEXC):
        books.append(build_btc_book())
    else:
        omitted.append({"book": "btc_spot_trend", "reason": f"missing {_DB_MEXC}"})
    results = {}
    for book in books:
        print(f"=== EXIT LAB · {book.name} · {len(book.periods)} 期 · 成本 {book.cost_bps:.0f}bps ===",
              flush=True)
        res = run_book(book)
        res["_as_of"] = max(book.periods) if book.periods else 0
        b = res["baseline"]
        print(f"  baseline: sharpe={b['sharpe']} net=${b['net_pnl']:,.0f} "
              f"maxDD={b['max_drawdown'] * 100:.1f}% PF={b['profit_factor']} "
              f"trades={b['trade_count']} cost=${b['cost_total']:,.0f}", flush=True)
        for n, p in res["policies"].items():
            flag = "[WORTH]" if p["selection"]["worth_further_study"] else "[--]   "
            warn = f" WARN:{p['selection_warning']}" if p["selection_warning"] else ""
            print(f"  {flag} {n:22s} dSharpe={p['delta_sharpe']:+.3f} "
                  f"dMdd={p['delta_max_drawdown']:+.3f} dNet=${p['delta_net_pnl']:+,.0f} "
                  f"PF={p['profit_factor']} exits={p['n_exit_events']}{warn}", flush=True)
        print(f"  VERDICT: {res['verdict']}", flush=True)
        results[book.name] = res
    artifact = build_artifact(results, omitted)
    atomic_write_json(_OUT, artifact, indent=2)
    print(f"-> {_OUT}", flush=True)


if __name__ == "__main__":
    main()
