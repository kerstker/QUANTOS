# crowd_contra 走干跑流水线 实现计划

> **Execution:** Follow the repository's task-by-task TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 crowd_contra 走 `run_multi_period_dry_run`,产出可复现审计净值曲线落 dry_run.db,如实记录执行现实结论。

**Architecture:** 新单文件驱动器 `tools/dry_run_crowd_contra_multi.py`,紧凑平行 `dry_run_carry_multi.py`(流水线接线)+ `crypto_oi_verify.py`(OI 面板/信号)。gate 外工具。

**Tech Stack:** Python、现有 `services/execution/dry_run_pipeline.run_multi_period_dry_run`、`crypto_oi_research.build_panel`、pytest(smoke)。

## Global Constraints

- L2 一寸不让:读本地库、无网络、无真单/真钱/真凭证;五个 LIVE 标志由 `SafetyConfig()` 恒 false。
- 驱动器在 `tools/`,不进 `services/`、不进 `research_runner` 白名单;不改流水线核心/门/adapter、不改 carry/trend 驱动器。
- 追加不清库(与 trend 一致);诚实记结论(证伪已过 ≠ 执行现实必过)。

---

### Task CC1: crowd_contra 干跑驱动器 + 真跑

**Files:**
- Create: `tools/dry_run_crowd_contra_multi.py`

**Interfaces:**
- Consumes: `crypto_oi_research.build_panel/_legs/QUANTILE`、`crypto_oi_verify.CROWD_CONTRA`、`alpha_expression.evaluate`、`run_multi_period_dry_run`、`DryRunStore`、`PreTradeRiskGate`/`KillSwitch`/`RiskLimits`/`RiskContext`、`DryRunAdapterEngine`。
- Produces: 一条 `strategy_id="crowd_contra"` 的 `DryRunRun` 追加进 `data/cache/dry_run.db`。

- [ ] **Step 1: 写驱动器**（紧凑平行 carry_multi;差异:OI 面板 + CROWD_CONTRA 信号 + strategy_id）

```python
# tools/dry_run_crowd_contra_multi.py
"""全历史周度滚动干跑驱动器:crowd_contra = cs_rank(neg(global_ls))(反着做拥挤的散户多空仓位)逐周截面多空腿
→ 权重,经真预交易门(fail-closed)+ 干跑适配器(纸面、绝不真发)滚动跑完整历史,资金费现金流单独累计(持永续
的真实现金流),产出 raw 与 vol-target 两条净值曲线,追加一条 DryRunRun 到 data/cache/dry_run.db(与 carry/trend
共存)。墙钟/seed 仅此边界;读本地库、无网络、无真单/真钱/真凭证。crowd_contra 已过 8 项证伪(4/5 机器检验),
这是它从未经过的执行现实检验——证伪已过 ≠ 执行现实必过。实盘 = 同接口换真券商适配器 + 人工批准 = 非目标。
"""
from __future__ import annotations

import os

from services.research import alpha_expression as ax
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.execution.dry_run_pipeline import run_multi_period_dry_run
from services.execution.dry_run_store import DryRunStore
from tools.crypto_oi_research import build_panel, _legs, QUANTILE
from tools.crypto_oi_verify import CROWD_CONTRA

_OUT = os.path.join("data", "cache", "dry_run.db")
_AUM = 1_000_000.0
_COST_BPS = 10.0            # 平 10bps:大币偏保守,对上证伪里 net@10bps 扛过那条证据
_WEEK_MS = 7 * 24 * 3600 * 1000


def main() -> None:
    panel, instruments = build_panel()
    insts = panel.instruments
    close = panel.matrices["close"]
    fmat = panel.matrices["funding"]
    glob = panel.matrices["global_ls"]
    ev = panel.event_times
    sig = ax.evaluate(CROWD_CONTRA, panel)

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

    def cash_flow_fn(held, prices, period):
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
            strategy=StrategyApproval(strategy_id="crowd_contra", approved=True, approval_id="research-dry-run"),
            now=period, max_staleness=10 ** 15)

    run = run_multi_period_dry_run(
        periods=periods, weights_by_period=weights_by_period, prices_by_period=prices_by_period, gate=gate,
        ctx_factory=ctx_factory, adapter=DryRunAdapterEngine(), strategy_id="crowd_contra", aum=_AUM,
        cost_bps=_COST_BPS, cash_flow_fn=cash_flow_fn,
        vol_target={"target_ann": 0.10, "lookback": 8, "lev_cap": 3.0, "ppy": 52})

    DryRunStore(_OUT, create=not os.path.exists(_OUT)).save_run(run)   # append alongside carry + trend
    p = run.pnl
    print(f"=== CROWD_CONTRA 周度滚动干跑 · OI 大币 {len(instruments)} · {p['n_periods']} 周 · 成本 {_COST_BPS:.0f}bps ===", flush=True)
    print(f"DryRunRun {run.run_id}: 累计意图 {run.n_intents} · 门批准 {run.n_approved} · 门拒绝 {run.n_rejected}", flush=True)
    print(f"  总收益 {p['total_return']*100:.1f}% · 夏普(周→年) {p['sharpe']} · 最大回撤 {p['max_drawdown']*100:.0f}% "
          f"· 累计资金费 ${p['total_funding']:,.0f} · 对账残差 {p['residual']}", flush=True)
    print(f"  raw 曲线 {len(run.detail['equity_curve'])} 点 · vol-target 曲线 {len(run.detail['equity_curve_vt'])} 点 "
          f"-> {_OUT}", flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 真跑(先跑齐 carry→trend 再跑 crowd_contra,三条共存)**

Run:
```bash
PYTHONPATH=. python tools/dry_run_carry_multi.py
PYTHONPATH=. python tools/dry_run_trend_multi.py
PYTHONPATH=. python tools/dry_run_crowd_contra_multi.py
```
Expected: crowd_contra 打印 `总收益 … · 夏普 … · 最大回撤 … · 对账残差` **接近 0**(恒等式成立);门批准数 > 0。
记下数字(收益/夏普/回撤/资金费)供文档。

- [ ] **Step 3: 断言对账恒等式 + 三条共存(smoke)**

Run:
```bash
PYTHONPATH=. python -c "from services.execution.dry_run_store import DryRunStore; \
s=DryRunStore('data/cache/dry_run.db'); rs=s.list_runs() if hasattr(s,'list_runs') else None; print(rs)"
```
（若 `DryRunStore` 无 `list_runs`,改用其现有列举 API——实现期先 Read `dry_run_store.py` 确认方法名;目标:确认 dry_run.db 内含 carry/trend/crowd_contra 三条 run。）
Expected: 三个 strategy_id 都在;crowd_contra run 的 `residual` ≈ 0。

- [ ] **Step 4: Commit**

```bash
git add tools/dry_run_crowd_contra_multi.py
git commit -m "research(dry-run): crowd_contra 走同一策略无关干跑流水线(第三条策略;截面拥挤反向 OI 因子的执行现实检验)"
```

---

### Task CC2: 全门绿 + 诚实记结论

**Files:**
- Modify: `CLAUDE.md`、`docs/PROJECT_STATE.md`、记忆 `crowd-contra-and-ic-panel.md`

- [ ] **Step 1: 全门绿**

Run: `PYTHONPATH=. python scripts/validate.py`
Expected: `VALIDATE PASSED (fail-closed gate green).`（驱动器在 gate 外,不该影响 services 无网络不变量;若治理扫描器报 `services/execution` 不 import `tools/` 之类,确认驱动器没被反向依赖。)

- [ ] **Step 2: 诚实记结论**

- `CLAUDE.md`:在干跑流水线条目「策略无关已验证」处补第三条驱动器 `tools/dry_run_crowd_contra_multi.py` + crowd_contra 执行现实数字 + 诚实定性(证伪已过 ≠ 执行现实必过;数字如实)。
- `docs/PROJECT_STATE.md`:crypto 研究状态补 crowd_contra 干跑结论。
- 记忆 `crowd-contra-and-ic-panel.md`:把「stalled before paper-track」更新为「已走干跑流水线,执行现实结论 = <真实数字>」。

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/PROJECT_STATE.md
git commit -m "docs: crowd_contra 干跑流水线执行现实结论记入活文档(证伪已过≠执行现实必过)"
```

---

## 自审(Self-Review)

- **Spec coverage:** spec §2-§4 ↔ CC1(驱动器+真跑),§4 诚实定性 ↔ CC2(记结论);非目标(paper-track/流水线改动)不在任务内。
- **Placeholder scan:** CC1 完整代码;CC2 依真跑数字填(Step 2 真跑先拿到数字再写文档,非占位)。
- **Type consistency:** `build_panel()->(panel,instruments)`、`_legs(sig_row,q)`、`run_multi_period_dry_run(...)` 参数与 carry_multi 一致;`strategy_id="crowd_contra"` 全程一致。
- **读优先红线:** 实现期先 Read `dry_run_store.py` 确认列举 API 名(Step 3),再写 smoke。
