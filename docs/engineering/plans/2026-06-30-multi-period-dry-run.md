# 多期滚动干跑(P&L 曲线)实现计划

> **Execution:** Follow the repository's task-by-task TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把单次干跑升级为「全历史周度滚动干跑」——逐周增量调仓单过真预交易门(fail-closed)→纸面成交→价格 MTM + 资金费现金流→raw 与 vol-target 两条净值曲线→一条门强制、可审计、可复现的多期 `DryRunRun`。

**Architecture:** 在 `services/execution/dry_run_pipeline.py` 新增 `run_multi_period_dry_run`(薄确定性循环,复用 `plan_target_quantities`/`build_intents`/`screen_intents`/`simulate_fills`/`reconcile`/`GlobalAuditLog`);多期结果塞进现有 `DryRunRun.detail`(不改 store schema/端点契约);驱动器 `tools/dry_run_carry_multi.py` 跑全历史周度 carry;干跑页 `DryRunPipeline.tsx` 检测 `detail.mode==="multi_period"` 画 SVG 净值曲线。

**Tech Stack:** Python 标准库(statistics/math/hashlib/sqlite)+ 复用 services 真零件;前端 React18+Vite6+TS+Tailwind(纯 SVG sparkline,无新依赖)。

## Global Constraints

- L2_DRY_RUN_ONLY:每期门 **fail-closed**;kill-switch latched(永久)→ 全期零成交;五个 LIVE 标志恒 false;干跑适配器无券商 SDK/不真发;纸面账户无真凭证。
- 核心确定性:`services/execution/` 无 RNG、无墙钟;periods/价格/funding/seed 只在驱动器边界注入。
- **无网络/券商 import**(`services/` 机器强制);**不 import `tools/`**(分层);`run_multi_period_dry_run` 绝不进 `research_runner`。
- 不改 `DryRunStore` schema(多期塞 `detail`);不改 `/api/v1/console/dry-run` 顶层 payload 形状(契约不变)。
- 周度年化用 √52(不用 backtest_engine.metrics 的 √252);funding 现金流是研究建模,非真实结算。
- 单一门禁 `python scripts/validate.py` 必须绿;隔离 pytest 用 `PYTHONPATH=. python -m pytest ...` 从仓库根跑。提交中文,结尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

## File Structure

- `services/execution/dry_run_pipeline.py` (Modify) — 加 `_ann_sharpe` / `_max_drawdown_from_returns` / `_vol_target_returns` helpers + `run_multi_period_dry_run`。
- `tests/execution/test_dry_run_pipeline.py` (Modify) — 加多期测试(良性/kill-switch/funding/确定性/vol-target)。
- `tools/dry_run_carry_multi.py` (Create) — 全历史周度 carry 驱动器。
- `apps/research-console/src/lib/types/research.ts` (Modify) — `DryRunRunRow.detail` 加可选多期字段。
- `apps/research-console/src/pages/DryRunPipeline.tsx` (Modify) — 多期分支:SVG 曲线 + 多期 KPI。

## 复用的现有真签名(本会话已探明,实现期先 Read 复核)

- `plan_target_quantities(weights, prices, aum) -> dict[str,int]`;`build_intents(targets, current, *, strategy_id="") -> list[OrderIntent]`(delta>0 BUY/<0 SELL,qty=abs)。
- `screen_intents(intents, *, gate, ctx_factory) -> {"approved":[(intent,ref)],"rejected":[(intent,[reason])]}`(`ctx_factory(intent)`,fail-closed)。
- `simulate_fills(approved, prices, *, adapter, run_id, timestamp, cost_bps=0.0) -> {"fills":[PaperFill],"reports":[...]}`;`PaperFill.side` ∈ paper `OrderSide.{BUY,SELL}`,`.quantity` 正、`.instrument_id`、`.price`。
- `reconcile(fills, marks=None, initial_capital=0.0) -> ReconciliationResult`,属性 `.equity` / `.reconciliation_residual()`。
- `GlobalAuditLog().append(*, event_id, actor, action, timestamp, target="", metadata=None)`(**metadata 值必须全 str**);`.seal().head_hash`。
- `DryRunRun(run_id, strategy_id, timestamp, n_intents, n_approved, n_rejected, rejected_reasons, pnl, audit_root, detail)`;`DryRunStore(path, *, create=False).save_run/all_runs`。
- `_det_id(*parts)`(已在 dry_run_pipeline.py)、`PaperSide`(= paper OrderSide,已 import)。
- carry 信号:`tools.crypto_carry_strategy.{COMPOSITE,QUANTILE,_legs}`;`alpha_expression.{build_field_panel,evaluate}`;`ui/format.stamp(ts)`(前端时间戳格式化)。

---

### Task 1: 多期核心循环 `run_multi_period_dry_run` + 指标 helpers

**Files:**
- Modify: `services/execution/dry_run_pipeline.py`
- Test: `tests/execution/test_dry_run_pipeline.py`

**Interfaces:**
- Consumes: `plan_target_quantities`/`build_intents`/`screen_intents`/`simulate_fills`/`reconcile`/`GlobalAuditLog`/`DryRunRun`/`_det_id`/`PaperSide`(均已在本模块或其 import 中)。
- Produces: `run_multi_period_dry_run(*, periods, weights_by_period, prices_by_period, gate, ctx_factory, adapter, strategy_id, aum, cost_bps=0.0, cash_flow_fn=None, vol_target=None) -> DryRunRun`;helpers `_ann_sharpe(returns, ppy=52)->float`、`_max_drawdown_from_returns(returns)->float`、`_vol_target_returns(returns, *, target_ann=0.10, lookback=8, lev_cap=3.0, ppy=52)->list[float]`。`ctx_factory(intent, prices, period)->RiskContext`。`detail.mode=="multi_period"`,`detail.equity_curve=[{t,equity,ret,approved,rejected,fills}]`。

- [ ] **Step 1: 写失败测试**(良性多期 + kill-switch + funding 方向 + 确定性)

```python
# 追加进 tests/execution/test_dry_run_pipeline.py
from services.execution.dry_run_pipeline import run_multi_period_dry_run
from services.risk.pre_trade.kill_switch import KillSwitch as _KS  # already imported as KillSwitch; alias for clarity


def _mp_ctx(intent, prices, period):
    return RiskContext(
        account=AccountRiskState(account_id="paper", cash=1e9, equity=1e9, day_pnl=0.0, peak_equity=1e9,
                                 positions=()),
        market=MarketContext(instrument_id=intent.instrument_id, known=True,
                             reference_price=prices.get(intent.instrument_id, 1.0), market_open=True,
                             price_as_of=period, est_daily_volume=1e12, instrument_vol=0.3),
        strategy=StrategyApproval(strategy_id="carry", approved=True, approval_id="ok"),
        now=period, max_staleness=10 ** 15)


_PERIODS = [1, 2, 3]
_W = {1: {"BTC": 0.5, "ETH": -0.5}, 2: {"BTC": 0.5, "ETH": -0.5}, 3: {"BTC": 0.4, "ETH": -0.6}}
_PX = {1: {"BTC": 100.0, "ETH": 50.0}, 2: {"BTC": 110.0, "ETH": 48.0}, 3: {"BTC": 105.0, "ETH": 52.0}}


def _mp(gate=None, cash_flow_fn=None):
    return run_multi_period_dry_run(
        periods=_PERIODS, weights_by_period=_W, prices_by_period=_PX, gate=gate or _gate(),
        ctx_factory=_mp_ctx, adapter=DryRunAdapterEngine(), strategy_id="carry", aum=1e6,
        cost_bps=5.0, cash_flow_fn=cash_flow_fn)


def test_multi_period_benign_curve():
    r = _mp()
    assert r.detail["mode"] == "multi_period" and r.detail["n_periods"] == 3
    assert len(r.detail["equity_curve"]) == 3
    assert r.pnl["residual"] < 1e-6                       # price-leg reconciliation identity holds
    assert r.pnl["final_equity"] == r.detail["equity_curve"][-1]["equity"]
    assert r.n_approved > 0 and r.audit_root


def test_multi_period_kill_switch_latched_zero_fills():
    ks = KillSwitch()
    gate = _gate(kill_switch=ks)
    ks.arm(Actor(actor_id="ops", kind=ActorKind.SYSTEM), "halt", at=1)  # latched (permanent) before the run
    r = _mp(gate=gate)
    assert r.n_approved == 0 and all(p["fills"] == 0 for p in r.detail["equity_curve"])
    assert all(abs(p["equity"] - 1e6) < 1e-6 for p in r.detail["equity_curve"])  # flat at aum, no fills/funding


def test_multi_period_funding_flows_into_equity():
    # +funding each period (carry collects) must lift the curve above the no-funding (price-only) curve
    flat = _mp(cash_flow_fn=None).pnl["final_equity"]
    with_fund = _mp(cash_flow_fn=lambda held, prices, period: 1000.0 if held else 0.0).pnl["final_equity"]
    assert with_fund > flat


def test_multi_period_deterministic():
    a, b = _mp(), _mp()
    assert (a.audit_root, a.pnl, a.detail["equity_curve"]) == (b.audit_root, b.pnl, b.detail["equity_curve"])
```

- [ ] **Step 2: 跑确认 FAIL** — `PYTHONPATH=. python -m pytest tests/execution/test_dry_run_pipeline.py -k multi_period -q` → FAIL(函数未定义)

- [ ] **Step 3: 实现**(追加到 `services/execution/dry_run_pipeline.py` 末尾)

```python
import math
import statistics


def _ann_sharpe(returns, ppy: int = 52) -> float:
    """年化夏普(周度→√52)。样本不足/零波动 → 0。"""
    if len(returns) < 2:
        return 0.0
    sd = statistics.pstdev(returns)
    return round(statistics.fmean(returns) / sd * math.sqrt(ppy), 4) if sd else 0.0


def _max_drawdown_from_returns(returns) -> float:
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in returns:
        eq *= (1.0 + r)
        peak = max(peak, eq)
        if peak > 0:
            mdd = max(mdd, (peak - eq) / peak)
    return round(mdd, 4)


def _vol_target_returns(returns, *, target_ann: float = 0.10, lookback: int = 8, lev_cap: float = 3.0,
                        ppy: int = 52):
    """PIT 尾随实现波动杠杆标量(同 carry vol-target):lev_t = min(cap, 目标每期波动 / 尾随实现波动),
    用 t 之前的收益估计(前 lookback 期原样穿过)。重塑风险、不造 edge。"""
    target_per = target_ann / math.sqrt(ppy)
    out = []
    for i in range(len(returns)):
        if i < lookback:
            out.append(returns[i])
            continue
        sd = statistics.pstdev(returns[i - lookback:i]) or 1e-9
        out.append(min(lev_cap, target_per / sd) * returns[i])
    return out


def run_multi_period_dry_run(*, periods, weights_by_period, prices_by_period, gate, ctx_factory, adapter,
                             strategy_id: str, aum: float, cost_bps: float = 0.0, cash_flow_fn=None,
                             vol_target: dict = None) -> DryRunRun:
    """全历史周度滚动干跑(薄、确定性):逐期重平衡,每期增量调仓单过门(fail-closed)→纸面成交(不真发)→
    价格 MTM(reconcile)+ 资金费现金流(cash_flow_fn)→权益点→哈希链审计→raw/vol-target 两条净值曲线。
    ctx_factory(intent, prices, period);kill-switch 由 gate 持有(latched→全期被拒)。墙钟在驱动器边界。"""
    run_id = _det_id(strategy_id, "multi", len(periods), tuple(periods[:1]))
    audit = GlobalAuditLog()
    audit.append(event_id=_det_id("mstart", run_id), actor=f"dry_run:{strategy_id}", action="MULTI_START",
                 timestamp=int(periods[0]) if periods else 0, target=run_id,
                 metadata={"n_periods": str(len(periods)), "aum": str(aum)})

    held: dict = {}                       # instrument -> signed qty
    all_fills: list = []
    cum_cash = 0.0
    curve: list = []
    tot_appr = tot_rej = 0
    prev_equity = aum
    for t in periods:
        prices_t = prices_by_period[t]
        targets = plan_target_quantities(weights_by_period.get(t, {}), prices_t, aum)
        intents = build_intents(targets, held, strategy_id=strategy_id)
        screened = screen_intents(intents, gate=gate,
                                  ctx_factory=lambda i, _p=prices_t, _t=t: ctx_factory(i, _p, _t))
        tot_appr += len(screened["approved"])
        tot_rej += len(screened["rejected"])
        filled = simulate_fills(screened["approved"], prices_t, adapter=adapter,
                                run_id=f"{run_id}:{t}", timestamp=int(t), cost_bps=cost_bps)
        for f in filled["fills"]:
            all_fills.append(f)
            held[f.instrument_id] = held.get(f.instrument_id, 0) + (
                f.quantity if f.side == PaperSide.BUY else -f.quantity)
        if cash_flow_fn is not None:
            cum_cash += float(cash_flow_fn(held, prices_t, t))
        equity_t = reconcile(all_fills, marks=prices_t, initial_capital=aum).equity + cum_cash
        ret = (equity_t / prev_equity - 1.0) if prev_equity else 0.0
        prev_equity = equity_t
        curve.append({"t": int(t), "equity": round(equity_t, 6), "ret": round(ret, 8),
                      "approved": len(screened["approved"]), "rejected": len(screened["rejected"]),
                      "fills": len(filled["fills"])})
        audit.append(event_id=_det_id("per", run_id, t), actor=f"dry_run:{strategy_id}", action="PERIOD",
                     timestamp=int(t), target=str(t),
                     metadata={"equity": str(round(equity_t, 2)), "ret": str(round(ret, 6)),
                               "approved": str(len(screened["approved"])),
                               "rejected": str(len(screened["rejected"]))})

    returns = [p["ret"] for p in curve]
    vt_curve: list = []
    if vol_target is not None and curve:
        e = aum
        for p, r in zip(curve, _vol_target_returns(returns, **vol_target)):
            e *= (1.0 + r)
            vt_curve.append({"t": p["t"], "equity": round(e, 6), "ret": round(r, 8)})

    final_equity = curve[-1]["equity"] if curve else aum
    residual = (reconcile(all_fills, marks=prices_by_period[periods[-1]], initial_capital=aum)
                .reconciliation_residual()) if periods else 0.0
    pnl = {"total_return": round(final_equity / aum - 1.0, 6), "sharpe": _ann_sharpe(returns),
           "max_drawdown": _max_drawdown_from_returns(returns), "final_equity": round(final_equity, 6),
           "total_funding": round(cum_cash, 6), "n_periods": len(periods), "total_rejected": tot_rej,
           "residual": round(residual, 9)}
    return DryRunRun(
        run_id=run_id, strategy_id=strategy_id, timestamp=int(periods[-1]) if periods else 0,
        n_intents=tot_appr + tot_rej, n_approved=tot_appr, n_rejected=tot_rej, rejected_reasons=[],
        pnl=pnl, audit_root=str(audit.seal().head_hash),
        detail={"mode": "multi_period", "n_periods": len(periods), "cadence": "weekly",
                "equity_curve": curve, "equity_curve_vt": vt_curve})
```

- [ ] **Step 4: 跑确认 PASS** — `PYTHONPATH=. python -m pytest tests/execution/test_dry_run_pipeline.py -k multi_period -q` → PASS（4 个多期 + 原有不回归)

- [ ] **Step 5: 提交** — `git add services/execution/dry_run_pipeline.py tests/execution/test_dry_run_pipeline.py && git commit -m "feat(dry-run): run_multi_period_dry_run——周度滚动干跑核心(门 fail-closed + funding 现金流 + 净值曲线 + 确定性)"`

---

### Task 2: vol-target 第二条曲线(单元验证)

**Files:**
- Modify: `tests/execution/test_dry_run_pipeline.py`

**Interfaces:**
- Consumes: `run_multi_period_dry_run`(Task 1)、`_vol_target_returns`(Task 1)。
- Produces: 无新代码(Task 1 已实现 `vol_target`/`equity_curve_vt`);本任务只补测试钉死行为。

- [ ] **Step 1: 写失败测试**(若 Task 1 已实现则直接 PASS;此处显式钉死 vt 行为)

```python
# 追加进 tests/execution/test_dry_run_pipeline.py
from services.execution.dry_run_pipeline import _vol_target_returns


def test_vol_target_returns_passthrough_then_scales():
    # 前 lookback 期原样穿过;之后按 min(cap, target/trailing_sd) 缩放
    rets = [0.01, -0.01, 0.02, -0.02, 0.03]
    out = _vol_target_returns(rets, target_ann=0.10, lookback=2, lev_cap=3.0, ppy=52)
    assert out[:2] == rets[:2]                     # warmup passthrough
    assert len(out) == len(rets)
    assert out[2] != rets[2]                       # scaled once trailing vol is available


def test_multi_period_vol_target_curve_present():
    r = run_multi_period_dry_run(
        periods=_PERIODS, weights_by_period=_W, prices_by_period=_PX, gate=_gate(), ctx_factory=_mp_ctx,
        adapter=DryRunAdapterEngine(), strategy_id="carry", aum=1e6, cost_bps=5.0,
        vol_target={"target_ann": 0.10, "lookback": 1, "lev_cap": 3.0, "ppy": 52})
    assert len(r.detail["equity_curve_vt"]) == len(r.detail["equity_curve"]) == 3
```

- [ ] **Step 2: 跑确认** — `PYTHONPATH=. python -m pytest tests/execution/test_dry_run_pipeline.py -k "vol_target" -q` → PASS（实现已在 Task 1;若 FAIL 回 Task 1 修)

- [ ] **Step 3: 提交** — `git add tests/execution/test_dry_run_pipeline.py && git commit -m "test(dry-run): vol-target 第二条曲线行为钉死(穿过+缩放)"`

---

### Task 3: 驱动器 `tools/dry_run_carry_multi.py`(全历史周度真跑)

**Files:**
- Create: `tools/dry_run_carry_multi.py`

**Interfaces:**
- Consumes: `run_multi_period_dry_run`(Task 1)、`DryRunStore`、carry 信号(`COMPOSITE/QUANTILE/_legs`)、`crypto_funding.db`/`crypto_funding_rates.json`、`PreTradeRiskGate`/`RiskLimits`/`RiskContext` 装配(同 `tools/dry_run_carry.py`)。

- [ ] **Step 1: 实现**(参照已存在的 `tools/dry_run_carry.py` 的载数据 + RiskContext 装配段;实现期先 Read 它复用)

```python
# tools/dry_run_carry_multi.py
"""全历史周度滚动干跑驱动器:逐周 carry COMPOSITE 多空腿 → 权重,经真预交易门(fail-closed)+ 干跑适配器
(纸面、绝不真发)滚动跑完整历史,资金费现金流单独累计(carry 的真边),产出 raw 与 vol-target 两条净值曲线,
落一条多期 DryRunRun 到 data/cache/dry_run.db。墙钟/seed 仅此边界;读本地库、无网络、无真单/真钱/真凭证。"""
from __future__ import annotations

import json
import os
import statistics
import time

from quantos.contracts.config import SafetyConfig  # noqa: F401  (ctx safety overwritten by gate; kept explicit)
from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research import alpha_expression as ax
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext, StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.execution.dry_run_pipeline import run_multi_period_dry_run
from services.execution.dry_run_store import DryRunStore
from tools.crypto_carry_strategy import COMPOSITE, QUANTILE, _legs
from tools.crypto_carry_stress import _slip_bps

_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_FUND = os.path.join("data", "cache", "crypto_funding_rates.json")
_OUT = os.path.join("data", "cache", "dry_run.db")
_AUM = 1_000_000.0
_WEEK_MS = 7 * 24 * 3600 * 1000


def main() -> None:
    instruments = sorted(json.load(open(_SNAP, encoding="utf-8"))["instruments"])
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    funding = {tk: {int(k): v for k, v in d.items()}
               for tk, d in json.load(open(_FUND, encoding="utf-8")).items()}
    # liquid half (same deployable universe as carry validation)
    liq = {}
    for inst in instruments:
        dv = [b.close * b.volume for b in store.fetch_bars(inst, -10 ** 18, 10 ** 18)
              if b.close > 0 and b.volume > 0]
        liq[inst] = statistics.median(dv) if dv else 0.0
    ranked = sorted(instruments, key=lambda x: liq[x])
    pctile = {inst: (i + 1) / len(ranked) for i, inst in enumerate(ranked)}
    slip = {inst: _slip_bps(pctile[inst]) for inst in instruments}
    liquid = [inst for inst in instruments if pctile[inst] >= 0.5]

    ets = sorted({b.event_time for inst in liquid for b in store.fetch_bars(inst, -10 ** 18, 10 ** 18)})
    panel = ax.build_field_panel(store, liquid, ets, ("close", "volume"))
    panel.matrices["funding"] = [[funding.get(i, {}).get(t) for i in panel.instruments]
                                 for t in panel.event_times]
    sig = ax.evaluate(COMPOSITE, panel)
    insts, close, fmat, ev = panel.instruments, panel.matrices["close"], panel.matrices["funding"], panel.event_times

    # weekly sampling: first row, then every >= _WEEK_MS later
    rows, last = [], None
    for r in range(len(ev)):
        if last is None or ev[r] - last >= _WEEK_MS:
            rows.append(r)
            last = ev[r]

    periods, weights_by_period, prices_by_period, rate_by_period = [], {}, {}, {}
    for r in rows:
        legs = _legs(sig[r], QUANTILE)
        if not legs:
            continue
        t = int(ev[r])
        periods.append(t)
        weights_by_period[t] = {insts[i]: w for i, w in legs.items()}
        prices_by_period[t] = {insts[i]: close[r][i] for i in legs if close[r][i] and close[r][i] > 0}
        rate_by_period[t] = {insts[i]: (fmat[r][i] or 0.0) for i in legs}

    held_names = sorted({n for w in weights_by_period.values() for n in w})
    cost_bps = statistics.fmean([slip[n] for n in held_names]) if held_names else 0.0

    def cash_flow_fn(held, prices, period):
        # carry funding cash flow on the held book: sum_i (-rate_i * qty_i * price_i)
        rates = rate_by_period.get(period, {})
        return sum(-rates.get(i, 0.0) * q * prices.get(i, 0.0) for i, q in held.items())

    ks = KillSwitch()
    limits = RiskLimits(
        allowed_instruments=frozenset(held_names), allowed_accounts=frozenset({"paper"}),
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
                                 price_as_of=period, est_daily_volume=1e12, instrument_vol=0.8),
            strategy=StrategyApproval(strategy_id="carry", approved=True, approval_id="research-dry-run"),
            now=period, max_staleness=10 ** 15)

    run = run_multi_period_dry_run(
        periods=periods, weights_by_period=weights_by_period, prices_by_period=prices_by_period, gate=gate,
        ctx_factory=ctx_factory, adapter=DryRunAdapterEngine(), strategy_id="carry", aum=_AUM,
        cost_bps=cost_bps, cash_flow_fn=cash_flow_fn,
        vol_target={"target_ann": 0.10, "lookback": 8, "lev_cap": 3.0, "ppy": 52})

    if os.path.exists(_OUT):
        os.remove(_OUT)
    DryRunStore(_OUT, create=True).save_run(run)
    p = run.pnl
    print(f"=== CARRY 周度滚动干跑 · 流动 {len(liquid)} 币 · {p['n_periods']} 周 · 成本 {cost_bps:.1f}bps ===", flush=True)
    print(f"DryRunRun {run.run_id}: 累计意图 {run.n_intents} · 门批准 {run.n_approved} · 门拒绝 {run.n_rejected}", flush=True)
    print(f"  总收益 {p['total_return']*100:.1f}% · 夏普(周→年) {p['sharpe']} · 最大回撤 {p['max_drawdown']*100:.0f}% "
          f"· 累计资金费 ${p['total_funding']:,.0f} · 对账残差 {p['residual']}", flush=True)
    print(f"  raw 曲线 {len(run.detail['equity_curve'])} 点 · vol-target 曲线 {len(run.detail['equity_curve_vt'])} 点 "
          f"-> {_OUT}", flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: import 检查 + 真跑** — `PYTHONPATH=. python -c "import tools.dry_run_carry_multi"`(OK);`PYTHONPATH=. python -m tools.dry_run_carry_multi`(打印周度摘要;`data/cache/dry_run.db` 重写为多期 run)。若门把全部期拒了:Read 拒绝原因 + 修 RiskContext 装配(常见:instrument_vol/allowed_instruments)。曲线应非空、残差≈0、累计资金费 > 0(carry 收 funding)。
- [ ] **Step 3: 提交** — `git add tools/dry_run_carry_multi.py && git commit -m "research(dry-run): 全历史周度滚动 carry 干跑驱动器——门强制净值曲线 + 资金费现金流落库"`

---

### Task 4: 前端多期曲线(类型 + SVG 曲线 + 多期 KPI)

**Files:**
- Modify: `apps/research-console/src/lib/types/research.ts`
- Modify: `apps/research-console/src/pages/DryRunPipeline.tsx`

**Interfaces:**
- Consumes: `DryRunPipelinePayload`/`DryRunRunRow`(已存在);后端 `detail.mode/equity_curve/equity_curve_vt`。
- Produces: 干跑页多期分支渲染(端点 payload 顶层不变 → 契约测试不变)。

- [ ] **Step 1: 类型加可选多期字段**

`apps/research-console/src/lib/types/research.ts` —— 把 `DryRunRunRow.detail` 改为(在现有字段基础上加可选项):

```ts
  detail: {
    n_fills?: number; n_adapter_rejected?: number; targets?: Record<string, number>;
    mode?: string; n_periods?: number; cadence?: string;
    equity_curve?: { t: number; equity: number; ret: number; approved?: number; rejected?: number; fills?: number }[];
    equity_curve_vt?: { t: number; equity: number; ret: number }[];
  };
```

- [ ] **Step 2: 干跑页多期分支**

`apps/research-console/src/pages/DryRunPipeline.tsx` —— 在 `latest` 解构后加多期判定,并在「流水线」面板下方插入曲线面板。新增一个纯 SVG sparkline 子组件(无新依赖)与多期 KPI:

```tsx
// 顶部已有 import；latest/pnl 已解构。加:
const mp = latest?.detail?.mode === 'multi_period' ? latest.detail : null;

// 纯 SVG 双线净值曲线(raw=accent, vt=info);确定性,无 Date/随机
function EquityCurve({ raw, vt }: { raw: { equity: number }[]; vt: { equity: number }[] }) {
  const W = 640, H = 160, pad = 6;
  const all = [...raw.map(p => p.equity), ...vt.map(p => p.equity)];
  if (!raw.length || !all.length) return null;
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const path = (pts: { equity: number }[]) => pts.map((p, i) => {
    const x = pad + (i / Math.max(pts.length - 1, 1)) * (W - 2 * pad);
    const y = H - pad - ((p.equity - lo) / span) * (H - 2 * pad);
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="equity curve">
      <path d={path(raw)} fill="none" stroke="var(--accent, #6ea8fe)" strokeWidth={1.5} />
      {vt.length > 0 && <path d={path(vt)} fill="none" stroke="var(--info, #4fd1c5)" strokeWidth={1.2} strokeDasharray="3 2" />}
    </svg>
  );
}
```

并在 JSX 中(「最新组合」面板之前)插入:

```tsx
      {mp && (
        <>
          <KpiStrip cols={5}>
            <StatTile label={bi('总收益', 'Total return')} value={pnl.total_return == null ? '—' : `${(pnl.total_return * 100).toFixed(1)}%`}
              tone={(pnl.total_return ?? 0) >= 0 ? 'pos' : 'neg'} accent />
            <StatTile label={bi('夏普(周→年)', 'Sharpe (wk→yr)')} value={pnl.sharpe == null ? '—' : signed(pnl.sharpe, 2)} tone="info" />
            <StatTile label={bi('最大回撤', 'Max drawdown')} value={pnl.max_drawdown == null ? '—' : `${(pnl.max_drawdown * 100).toFixed(0)}%`} tone="warn" />
            <StatTile label={bi('周期数', 'Periods')} value={String(mp.n_periods ?? mp.equity_curve?.length ?? 0)} tone="muted" sub={bi('周度', 'weekly')} />
            <StatTile label={bi('累计被门拒', 'Total gate-rejected')} value={String(latest?.n_rejected ?? 0)}
              tone={(latest?.n_rejected ?? 0) > 0 ? 'neg' : 'muted'} />
          </KpiStrip>
          <Panel eyebrow={bi('净值曲线', 'EQUITY CURVE')}
            title={bi('门强制下的滚动净值(蓝=raw,虚线=vol-target 压尾)· 含资金费现金流',
              'Gate-enforced rolling equity (blue=raw, dashed=vol-target) · funding cash-flow included')}>
            <EquityCurve raw={mp.equity_curve ?? []} vt={mp.equity_curve_vt ?? []} />
            <div className="mt-1 text-2xs text-muted">
              {bi(`累计资金费 $${(pnl.total_funding ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })} · 对账残差 ${pnl.residual ?? '—'}(价格腿恒等式)`,
                `funding $${(pnl.total_funding ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })} · reconciliation residual ${pnl.residual ?? '—'}`)}
            </div>
          </Panel>
        </>
      )}
```

- [ ] **Step 3: typecheck + build** — `cd apps/research-console && npm run typecheck`(无错)`&& npm run build`(✓ built)。
- [ ] **Step 4: 提交** — `git add apps/research-console/src/lib/types/research.ts apps/research-console/src/pages/DryRunPipeline.tsx && git commit -m "feat(console): 干跑页多期净值曲线(SVG raw+vol-target 双线)+ 多期 KPI"`

---

### Task 5: 全门绿 + 浏览器验证 + 文档/记忆 + 收尾

**Files:** 无新代码(验证 + 文档)

- [ ] **Step 1: 全门禁** — `python scripts/validate.py` → `VALIDATE PASSED`(新多期测试 + 治理:`services/` 无网络 import 绿、`test_research_runner_boundary` 绿、`test_console_api_contract` 绿——顶层 payload 未变)。
- [ ] **Step 2: 浏览器验证** — 后台起 `python -m uvicorn services.research.console_api.main:app --port 8400`;`preview_start research-console-live`;导航 `/research/dry-run`;`preview_snapshot` 确认渲染多期 KPI + 曲线 SVG、`preview_console_logs` 无 error;`preview_screenshot` 留证;停服务。
- [ ] **Step 3: 治理点检** — grep `tools/dry_run_carry_multi.py` 与 `dry_run_pipeline.py` 新增段:无 `requests/urllib/socket/aiohttp` import、无券商 SDK、`services/execution` 不 import `tools/`、无 `research_runner` 引用。
- [ ] **Step 4: 文档/记忆** — `CLAUDE.md` 干跑条目补「+ `run_multi_period_dry_run` 周度滚动 + 净值曲线 + console 曲线页」;`docs/PROJECT_STATE.md` §3 执行/干跑行补多期;记忆 `dry-run-pipeline.md` 末补多期已落地 + carry 周度滚动首跑数(总收益/夏普/回撤/资金费)。
- [ ] **Step 5: 提交** — `git add -A && git commit -m "docs(state): 多期滚动干跑(P&L 曲线)落地 + carry 周度首跑记入活文档"`

---

## Self-Review

**Spec coverage**(逐节对 spec):
- §3.1 `run_multi_period_dry_run`(循环/equity/funding/vt/pnl/ctx_factory 闭包)→ Task 1。✓
- §3.2 DryRunRun 复用(detail.mode/equity_curve/equity_curve_vt,不改 schema)→ Task 1(detail)+ Task 3(落库)。✓
- §3.3 驱动器(全历史周度/funding cash_flow_fn/落库)→ Task 3。✓
- §3.4 console(mode 分支/SVG 曲线/多期 KPI/类型可选字段/契约不变)→ Task 4。✓
- §4 数据流 → Task 1 循环顺序。✓
- §5 测试(良性/kill-switch/funding 方向/确定性/vt/驱动器真跑/console)→ Task 1+2 测试、Task 3 真跑、Task 4 typecheck/build、Task 5 浏览器+门绿。✓
- §6 安全(每期门 fail-closed、kill-switch latched、不真发、确定性、无网络/券商/不进 research_runner)→ Task 1(kill-switch 测试)、Global Constraints、Task 5 治理点检。✓
- §7 YAGNI(固定名义本金、不复利反馈、不改 schema/契约)→ 计划遵守。✓

**Placeholder scan:** 无 TBD/TODO。多处"实现期先 Read 复核"是诚实的接口校验提示(非占位);每步给了按已探签名书写的完整代码。Task 3 的 RiskContext 装配直接复用已存在且已真跑过的 `tools/dry_run_carry.py` 同构代码。

**Type consistency:** `run_multi_period_dry_run` 返回 `DryRunRun`(字段与 Task 1 store 一致);`detail.mode/equity_curve/equity_curve_vt`(Task 1)被 Task 3 落库、Task 4 前端类型 + 渲染一致消费;`cash_flow_fn(held, prices, period)` 签名 Task 1 定义、Task 3 闭包实现一致;`_vol_target_returns`/`_ann_sharpe`/`_max_drawdown_from_returns` 跨 Task 1/2 同名;前端 `pnl.{total_return,sharpe,max_drawdown,total_funding,residual}` 与 Task 1 pnl dict 键一致。✓

> 实现期红线:每个 Task 第一步先 `Read` 它要调的现有模块/文件(`dry_run_pipeline.py` 现状、`tools/dry_run_carry.py`、`DryRunPipeline.tsx`、`research.ts`),核对后再落代码。
