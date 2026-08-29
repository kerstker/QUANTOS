"""BTC 现货 long-only 趋势策略 —— 周度滚动干跑(MEXC 零手续费现货口径)。

背景:MEXC 现货 BTC/USDT 挂单/吃单 0 费率,这是本驱动的成本口径(cost_bps=0)。现货**无做空、不可杠杆**
(vol_target lev_cap=1.0),故策略为 long-only:信号开 → 全仓 BTC(weight=1),信号关 → 持现金(weight=0)。

信号(简单可解释,Donchian 突破 + 动量复合):
    持有 BTC 当且仅当  close > 前 _DONCHIAN_N 日最高收盘(不含当日)  且  close > _MOM_N 日前收盘;
    否则持现金。周线调仓(每 7 个日线 bar 取一期,与既有 trend 驱动器同法)。

数据(2026-07 MEXC 对接,双源对照):
- **主口径 = MEXC PIT** `data/cache/crypto_mexc.db`(BTC 日线,由 tools/live_data_refresh.py 第 5 源
  或 tools/mexc_klines_fetch.py 落库;volume = MEXC base 成交量/根)。canonical strategy_id
  (`btc_spot_trend` 等 4 条)只跑在 MEXC 数据上——同名策略在两个源上的结果是不同证据,
  `detail.data_source` 显式打标,绝不互相顶用(切换前的旧 run 无标记,按 LEGACY 计、不挡新跑)。
- **对照 = CoinGecko PIT** `data/cache/crypto_funding.db`(原数据源,USD 24h rolling notional 口径),
  以 `_cg` 后缀 strategy_id 落库,供诚实对比两源结论是否一致。
- MEXC 库缺失(本机未跑过 MEXC 抓取)→ 只跑 `_cg` 对照并明示原因,canonical id 绝不用 CoinGecko
  数据充数。PIT 读法与既有驱动器一致(≤t 的 bar 才算数,无前视)。

走同一个 `run_multi_period_dry_run`:aum=1e6,主口径 cost_bps=0(MEXC 0 费率),另跑一组 cost_bps=5
的敏感性对照(万一零费率结束/含买卖价差的现实成本)。两条都落 data/cache/dry_run.db
(确定性 run_id + data_source 打标,重复跑自动复用)。ctx_factory 用真实 est_daily_volume。
并打印 buy-and-hold 基准做诚实对比。

前向 paper-track(MEXC_INTEGRATION.md §8 阶段1 出口标准):MEXC 主口径字面变体的逐周 RAW 收益
按诚实 train/paper 切分(前 70% = 回测参照,后 30% = 前向 paper)写进 append-only
`data/cache/paper_track.db`(断点2 持久层,幂等)。单名趋势无截面 → realized_ic 恒 None(诚实)。

墙钟仅此边界;读本地库、无网络、无真单/真钱/真凭证。实盘 = 同接口换 MEXC 现货适配器 + 人工批准 = 非目标。

用法: python -m tools.dry_run_btc_spot_multi
"""
from __future__ import annotations

import math
import os
import statistics

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research.backtest_engine.metrics import sharpe
from services.research.paper_trading.advance import advance_paper_track
from services.research.paper_trading.store import PaperTrackStore
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.execution.backtest_authority import quarantine_legacy_backtest
from services.execution.dry_run_pipeline import run_multi_period_dry_run
from services.execution.dry_run_store import DryRunStore
from tools.domain_event_bus import open_domain_event_bus, save_and_project_dry_run

_DB_MEXC = os.path.join("data", "cache", "crypto_mexc.db")
_DB_COINGECKO = os.path.join("data", "cache", "crypto_funding.db")
_OUT = os.path.join("data", "cache", "dry_run.db")
_PAPER_DB = os.path.join("data", "cache", "paper_track.db")
_INST = "BTC"
_AUM = 1_000_000.0
_DONCHIAN_N = 20   # 20 日最高收盘突破(唐奇安)
_MOM_N = 30        # 30 日动量为正
_COST_BPS_MAIN = 0.0    # MEXC 现货 BTC/USDT 0 费率口径(挂单吃单均 0)
_COST_BPS_SENS = 5.0    # 敏感性对照:零费率结束 / 含价差成本的现实情形
# 现货 long-only:杠杆上限 1.0(spot 不可杠杆,vol_target 只在高波动时降仓,永不放大)
_VOL_TARGET = {"target_ann": 0.10, "lookback": 8, "lev_cap": 1.0, "ppy": 52}
_WEEK_MS = 7 * 86_400_000
_PAPER_BOUNDARY_FRAC = 0.7     # 前 70% = 回测参照,后 30% = 前向 paper(与 crypto_paper_track 同口径)
_SRC_MEXC = "MEXC_PIT"
_SRC_COINGECKO = "COINGECKO_PIT"


def _signal(closes: list[float]) -> float:
    """PIT 信号(字面口径):Donchian 突破 ∧ 动量>0 → 1.0(持有 BTC),否则 0.0(持现金)。"""
    if len(closes) < max(_DONCHIAN_N, _MOM_N) + 1:
        return 0.0
    px = closes[-1]
    donchian_hi = max(closes[-(_DONCHIAN_N + 1):-1])  # 前 N 日最高收盘,不含当日
    mom_ref = closes[-(_MOM_N + 1)]
    return 1.0 if (px > donchian_hi and px > mom_ref) else 0.0


def _signal_series_hysteresis(closes: list[float]) -> list[float]:
    """迟滞变体(经典趋势跟踪):Donchian 突破 ∧ 动量>0 进场;进场后持有,直到动量转负才退场。
    与字面口径的差别只在退出规则(字面口径要求每周都在创新高,约 9 成时间被迫空仓)。"""
    out, holding = [], False
    for i in range(len(closes)):
        if i < max(_DONCHIAN_N, _MOM_N):
            out.append(0.0)
            continue
        px = closes[i]
        donchian_hi = max(closes[i - _DONCHIAN_N:i])
        mom_pos = px > closes[i - _MOM_N]
        if not holding:
            holding = bool(px > donchian_hi and mom_pos)
        elif not mom_pos:
            holding = False
        out.append(1.0 if holding else 0.0)
    return out


def _build_periods(store, hysteresis: bool = False) -> tuple[list, dict, dict, dict]:
    """逐周(每 7 个日线 bar)取一期:信号权重 + 当期价 + 真实 ADV(volume)。"""
    bars = sorted(store.fetch_bars(_INST, -10 ** 18, 10 ** 18), key=lambda b: b.event_time)
    closes = [b.close for b in bars]
    sig = _signal_series_hysteresis(closes) if hysteresis else None
    weekly_idx = list(range(0, len(bars), 7))
    periods, weights_by_period, prices_by_period, vol_by_period = [], {}, {}, {}
    for i in weekly_idx:
        b = bars[i]
        w = sig[i] if sig is not None else _signal(closes[: i + 1])  # ≤t 的收盘,无前视
        if b.close <= 0 or not math.isfinite(b.close):
            continue
        t = int(b.event_time)
        periods.append(t)
        weights_by_period[t] = {_INST: w}
        prices_by_period[t] = {_INST: float(b.close)}
        vol_by_period[t] = float(b.volume) if b.volume and b.volume > 0 else 1e9
    return periods, weights_by_period, prices_by_period, vol_by_period


def _make_gate_and_ctx(vol_by_period):
    ks = KillSwitch()
    limits = RiskLimits(
        allowed_instruments=frozenset({_INST}), allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=10 ** 13, max_order_notional=1e14, fat_finger_qty=10 ** 13,
        max_gross_exposure=1e14, max_net_exposure=1e14, max_leverage=1e7, max_single_name_pct=1e7,
        max_turnover=1e7, max_participation_rate=1e7, max_instrument_vol=5.0, daily_loss_limit=1e14,
        max_drawdown=1.0)
    gate = PreTradeRiskGate(limits, kill_switch=ks)

    def ctx_factory(intent, prices, period):
        return RiskContext(
            account=AccountRiskState(account_id="paper", cash=_AUM * 10, equity=_AUM * 10, day_pnl=0.0,
                                     peak_equity=_AUM * 10, positions=()),
            market=MarketContext(instrument_id=intent.instrument_id, known=True,
                                 reference_price=prices.get(intent.instrument_id, 1.0), market_open=True,
                                 price_as_of=period,
                                 est_daily_volume=vol_by_period.get(period, 1e9),  # 真实 BTC volume
                                 instrument_vol=0.8),
            strategy=StrategyApproval(strategy_id="btc_spot_trend", approved=True,
                                      approval_id="research-dry-run"),
            now=period, max_staleness=10 ** 15)

    return gate, ctx_factory


def _existing_run(strategy_id: str, data_source: str) -> dict | None:
    """幂等:同 (strategy_id, data_source) 已由同参跑过则复用(store 与事件权威 append-only,
    不重发)。data_source 打标是 2026-07 MEXC 切换引入的——同名策略在 CoinGecko/MEXC 两源上的
    结果是不同证据,绝不能互相顶用;切换前的旧 run 没有该标记,按 LEGACY 计,不会挡住
    MEXC 主口径的新跑。"""
    if not os.path.isfile(_OUT):
        return None
    store = None
    try:
        store = DryRunStore(_OUT, create=False)
        records = [r for r in store.all_runs()
                   if isinstance(r, dict) and r.get("strategy_id") == strategy_id
                   and (r.get("detail") or {}).get("data_source", "LEGACY") == data_source]
        return records[-1] if records else None
    except Exception:
        return None
    finally:
        if store is not None:
            store.close()


def _run_one(store, strategy_id: str, cost_bps: float, periods, weights_by_period,
             prices_by_period, vol_by_period, event_bus, data_source: str,
             persist: bool = True):
    existing = _existing_run(strategy_id, data_source) if persist else None
    if existing is not None:
        print(f"[{strategy_id}] 已复用持久化运行 {existing.get('run_id')} "
              f"({data_source}) sharpe={(existing.get('pnl') or {}).get('sharpe')} "
              f"residual={(existing.get('pnl') or {}).get('residual')}", flush=True)
        return existing
    gate, ctx_factory = _make_gate_and_ctx(vol_by_period)
    run = run_multi_period_dry_run(
        periods=periods, weights_by_period=weights_by_period, prices_by_period=prices_by_period,
        gate=gate, ctx_factory=ctx_factory, adapter=DryRunAdapterEngine(), strategy_id=strategy_id,
        aum=_AUM, cost_bps=cost_bps, cash_flow_fn=None,  # 现货趋势 = 价格边,无资金费现金流
        vol_target=_VOL_TARGET, event_bus=event_bus, publish_completion_event=False)
    run.detail["data_source"] = data_source   # 数据源打标(见 _existing_run)
    if persist:
        save_and_project_dry_run(DryRunStore(_OUT, create=True), run, event_bus,
                                 now_ms=int(run.timestamp))
    p = run.pnl
    # 多期曲线无 turnover 字段:用信号权重变化量 |Δweight| 算单边换手(long-only 全进全出 → 每次进出=1.0)
    ws = [weights_by_period[t][_INST] for t in periods]
    turnover = sum(abs(ws[i] - ws[i - 1]) for i in range(1, len(ws)))
    print(f"=== {strategy_id} · BTC 现货 long-only Donchian({_DONCHIAN_N}d)+动量({_MOM_N}d) · "
          f"{p['n_periods']} 周 · 成本 {cost_bps:.0f}bps · {data_source} ===", flush=True)
    print(f"DryRunRun {run.run_id}: 累计意图 {run.n_intents} · 门批准 {run.n_approved} · "
          f"门拒绝 {run.n_rejected}", flush=True)
    print(f"  RAW   : 总收益 {p['total_return']*100:.1f}% · 夏普(周→年) {p['sharpe']} · "
          f"最大回撤 {p['max_drawdown']*100:.1f}%", flush=True)
    vt_s = f"{p['sharpe_vt']}" if p["sharpe_vt"] is not None else "None"
    vt_d = f"{p['max_drawdown_vt']*100:.1f}%" if p["max_drawdown_vt"] is not None else "None"
    vt_eq = run.detail.get("equity_curve_vt") or []
    vt_ret = (vt_eq[-1]["equity"] / _AUM - 1.0) if vt_eq else None
    print(f"  VT(10%目标/杠杆上限1.0): 总收益 {(vt_ret*100 if vt_ret is not None else 0):.1f}% · "
          f"夏普 {vt_s} · 最大回撤 {vt_d}", flush=True)
    print(f"  累计换手 {turnover:.2f}× · 对账残差 {p['residual']} -> {_OUT}", flush=True)
    return run


def _buy_and_hold(periods, prices_by_period) -> None:
    """buy-and-hold 基准:同期全程持有 BTC,同口径周收益统计。"""
    px = [prices_by_period[t][_INST] for t in periods]
    rets = [px[i] / px[i - 1] - 1.0 for i in range(1, len(px))]
    total = px[-1] / px[0] - 1.0
    mu, sd = statistics.fmean(rets), statistics.pstdev(rets)
    sharpe_bnh = round(mu / sd * math.sqrt(52), 3) if sd > 0 else 0.0
    peak, mdd = 1.0, 0.0
    e = 1.0
    for r in rets:
        e *= (1.0 + r)
        peak = max(peak, e)
        mdd = max(mdd, 1.0 - e / peak)
    print(f"--- 基准 BUY-AND-HOLD(同期全程持有 BTC): 总收益 {total*100:.1f}% · "
          f"夏普(周→年) {sharpe_bnh} · 最大回撤 {mdd*100:.1f}% ---", flush=True)


def _run_variant_set(store, *, ids: dict, data_source: str, event_bus) -> dict:
    """跑 4 个变体(字面/迟滞 × 0/5bps),返回 {strategy_id: run_or_record}。"""
    periods, weights_by_period, prices_by_period, vol_by_period = _build_periods(store)
    n_long = sum(1 for t in periods if weights_by_period[t][_INST] > 0)
    print(f"[btc_spot] {data_source} 日线 PIT "
          f"{len(store.fetch_bars(_INST, -10**18, 10**18))} bar → {len(periods)} 周 · "
          f"字面口径持仓周 {n_long} / 空仓周 {len(periods)-n_long}", flush=True)
    runs = {}
    runs[ids["main"]] = _run_one(store, ids["main"], _COST_BPS_MAIN, periods,
                                 weights_by_period, prices_by_period, vol_by_period,
                                 event_bus, data_source)
    runs[ids["c5"]] = _run_one(store, ids["c5"], _COST_BPS_SENS, periods,
                               weights_by_period, prices_by_period, vol_by_period,
                               event_bus, data_source)
    p2, w2, px2, v2 = _build_periods(store, hysteresis=True)
    n_long2 = sum(1 for t in p2 if w2[t][_INST] > 0)
    print(f"[btc_spot] 迟滞变体持仓周 {n_long2} / 空仓周 {len(p2)-n_long2}", flush=True)
    runs[ids["hold"]] = _run_one(store, ids["hold"], _COST_BPS_MAIN, p2, w2, px2, v2,
                                 event_bus, data_source)
    runs[ids["hold_c5"]] = _run_one(store, ids["hold_c5"], _COST_BPS_SENS, p2, w2, px2, v2,
                                    event_bus, data_source)
    _buy_and_hold(periods, prices_by_period)
    return runs


def _detail_of(run_or_record) -> dict:
    """run 对象(刚跑完)与复用记录(dict,来自 store)的统一 detail 视图。"""
    if isinstance(run_or_record, dict):
        return run_or_record.get("detail") or {}
    return run_or_record.detail


def _advance_paper_track(detail: dict, *, alpha_id: str = "btc_spot_trend",
                         db_path: str = _PAPER_DB) -> None:
    """断点2 前向 paper-track 存证(MEXC_INTEGRATION.md §8 阶段1 出口标准):MEXC 主口径
    字面变体的逐周 RAW 收益按诚实 train/paper 切分([:boundary]=回测参照,[boundary:]=前向
    paper)写进 append-only paper_track.db。幂等(主键去重,重跑不双记)。单名 BTC 趋势
    无截面 → realized_ic 恒 None(诚实:IC 是截面概念,不硬凑)。"""
    curve = (detail or {}).get("equity_curve") or []
    rets = [float(p["ret"]) for p in curve
            if isinstance(p, dict) and p.get("ret") is not None]
    if len(rets) < 20:
        print(f"[paper-track] {alpha_id} 曲线不足 20 期({len(rets)}),跳过前向存证", flush=True)
        return
    boundary = max(10, int(len(rets) * _PAPER_BOUNDARY_FRAC))
    if boundary >= len(rets):
        print(f"[paper-track] {alpha_id} boundary={boundary} 覆盖全序列,跳过", flush=True)
        return
    ts0 = int(curve[0]["t"])
    bt_sharpe = round(sharpe(rets[:boundary]), 4)
    store = PaperTrackStore(db_path, create=True)
    n_new = advance_paper_track(store, alpha_id, rets, None, boundary=boundary,
                                source="OOS_TAIL", ts0=ts0, dt=_WEEK_MS,
                                backtest_sharpe=bt_sharpe)
    track = store.track(alpha_id)
    print(f"[paper-track] {alpha_id}: 新增 {n_new} 期 · n={track['n_periods']} · "
          f"paper_sharpe={track['paper_sharpe']} vs backtest {track['backtest_sharpe']} "
          f"(gap {track['perf_gap']}) -> {db_path}", flush=True)


def main() -> None:
    quarantine_legacy_backtest("tools.dry_run_btc_spot_multi")
    event_bus = open_domain_event_bus()

    if os.path.isfile(_DB_MEXC):
        # 主口径:MEXC PIT(canonical strategy_id;console mexc-strategy/readiness 读这些 id)
        mexc_store = CachingPITStore(SqlitePITStore(_DB_MEXC, create=False))
        runs = _run_variant_set(
            mexc_store, event_bus=event_bus, data_source=_SRC_MEXC,
            ids={"main": "btc_spot_trend", "c5": "btc_spot_trend_c5",
                 "hold": "btc_spot_trend_hold", "hold_c5": "btc_spot_trend_hold_c5"})
        _advance_paper_track(_detail_of(runs["btc_spot_trend"]))
        # 对照:CoinGecko PIT(_cg 后缀,数据源对照——canonical id 绝不用 CoinGecko 数据充数)
        cg_store = CachingPITStore(SqlitePITStore(_DB_COINGECKO, create=False))
        print(f"[btc_spot] 对照源 CoinGecko PIT({_DB_COINGECKO}),_cg 后缀落库", flush=True)
        _run_variant_set(
            cg_store, event_bus=event_bus, data_source=_SRC_COINGECKO,
            ids={"main": "btc_spot_trend_cg", "c5": "btc_spot_trend_cg_c5",
                 "hold": "btc_spot_trend_cg_hold", "hold_c5": "btc_spot_trend_cg_hold_c5"})
    else:
        # MEXC 库缺失(本机未跑过 MEXC 抓取)→ 只跑 CoinGecko 对照,canonical id 留待 MEXC 数据
        print(f"[btc_spot] MEXC PIT 库缺失({_DB_MEXC})——先跑 tools/live_data_refresh 或 "
              f"tools/mexc_klines_fetch;本轮只落 CoinGecko 对照(_cg 后缀),"
              f"canonical strategy_id 不用 CoinGecko 数据充数", flush=True)
        cg_store = CachingPITStore(SqlitePITStore(_DB_COINGECKO, create=False))
        _run_variant_set(
            cg_store, event_bus=event_bus, data_source=_SRC_COINGECKO,
            ids={"main": "btc_spot_trend_cg", "c5": "btc_spot_trend_cg_c5",
                 "hold": "btc_spot_trend_cg_hold", "hold_c5": "btc_spot_trend_cg_hold_c5"})


if __name__ == "__main__":
    main()
