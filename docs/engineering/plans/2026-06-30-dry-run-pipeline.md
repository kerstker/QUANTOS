# 端到端「干跑流水线」实现计划

> **Execution:** Follow the repository's task-by-task TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有真零件串成一条连续的、不碰真钱的干跑流水线——信号/权重→意图→预交易门(运行时 chokepoint,fail-closed)→干跑适配器受理(不真发)→纸面成交→对账/PnL→哈希链审计→可复现的 DryRunRun 落库。

**Architecture:** 一个薄确定性编排器 `services/execution/dry_run_pipeline.py` 按序组合现有模块;`ctx_factory(intent)->RiskContext` 注入(把 RiskContext 装配留给驱动器/测试,核心保持干净);append-only `services/execution/dry_run_store.py` 落库;驱动器 `tools/dry_run_carry.py` 跑 carry 端到端。

**Tech Stack:** Python 标准库 + sqlite;复用 `services/execution/intent_builder.build_intents`、`services/risk/pre_trade`(PreTradeRiskGate/KillSwitch/RiskContext/RiskLimits/order_bridge)、`services/trading/dry_run_adapters`(DryRunAdapterEngine)、`services/research/paper_trading/domain`(PaperAccount/PaperFill/reconcile)、`services/monitoring/audit_log.GlobalAuditLog`。

## Global Constraints

- L2_DRY_RUN_ONLY:零下单到真券商、零真钱、零真凭证。五个 LIVE 标志(`SafetyConfig` live_trading_enabled 等)恒 false。
- **门 fail-closed**:仅 `RiskDecision.decision ∈ {RISK_APPROVED, RISK_APPROVED_WITH_LIMITS}` 的意图进下一步;其余/异常→不成交、记原因。
- **kill_switch 先查**:`KillSwitch.is_active()` 为真 → 全意图被拒、零成交。
- 干跑适配器命令 `dry_run=True`、`live_trading_allowed=False`;无券商 SDK、无网络 import(`services/` 无网络 import 仍机器强制)。
- **编排器绝不进 research_runner**(`tests/governance/test_research_runner_boundary.py` 钉死无下单路径)。
- 确定性:`services/execution/` 核心无 RNG、无墙钟;墙钟/seed 只在驱动器边界注入(timestamp、adapter seed)。
- 实盘 = 同接口换真券商适配器 + 人工批准 = 显式非目标。
- 单一门禁 `python scripts/validate.py` 必须绿;隔离 pytest 用 `PYTHONPATH=. python -m pytest ...` 从仓库根跑。提交中文,结尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

## File Structure

- `services/execution/dry_run_store.py` (Create) — `DryRunRun` dataclass + `DryRunStore`(append-only sqlite)。
- `services/execution/dry_run_pipeline.py` (Create) — `screen_intents` / `simulate_fills` / `run_dry_run_pipeline`。
- `tools/dry_run_carry.py` (Create) — carry+vol-target 端到端驱动器。
- `tests/execution/__init__.py` (Create if missing) + `tests/execution/test_dry_run_store.py` / `test_dry_run_pipeline.py` (Create)。

## 现有零件确切签名(实现期照此,勿猜)

- `build_intents(targets: Dict[str,int], current: Dict[str,int], *, strategy_id="") -> list[OrderIntent]`;`OrderIntent`(instrument_id, side, quantity, order_type, limit_price, strategy_id)。
- `PreTradeRiskGate(limits: RiskLimits, kill_switch=None, safety_config=SafetyConfig(...all false...), audit_trail=None)`;`.evaluate(intent, ctx: RiskContext) -> RiskDecision`;`.issue_ref(decision, intent, ctx) -> str`。`RiskDecision`(decision, reasons)。
- `RiskContext`(account: AccountRiskState, market: MarketContext, strategy: StrategyApproval, kill_switch: KillSwitch, safety_config: SafetyConfig, actor, now: int, max_staleness, recent_order_fingerprints, recent_timestamps, plan_turnover)。
- `AccountRiskState`(account_id, cash, equity, day_pnl, peak_equity, positions);`MarketContext`(instrument_id, known, reference_price, market_open, price_as_of, est_daily_volume, instrument_vol);`StrategyApproval`(strategy_id, approved, approval_id);`RiskLimits`(allowed_instruments, allowed_accounts, max_position_qty, max_order_notional, fat_finger_qty, …,有默认);`KillSwitch().is_active()/arm()/reset()`。
- `DryRunAdapterEngine(config: DryRunAdapterConfig).process(cmd: DryRunCommand) -> DryRunAdapterRun`;`.simulate_fill(rid) -> NormalizedReport|None`。`DryRunAdapterConfig`(adapter_type, seed, dry_run, live_trading_allowed, synthetic_latency_ms);`DryRunCommand`(command_id, adapter_type, …, instrument_id, side, order_type, quantity, price, …, dry_run, live_trading_allowed);`NormalizedReport`(…, status, fill_price, fill_quantity, …)。
- `PaperAccount`(account_id, name, initial_capital, cash, positions, …);`PaperFill`(fill_id, order_id, run_id, instrument_id, side, quantity, price, fill_method, timestamp, commission, slippage);`reconcile(fills: List[PaperFill], marks=None, initial_capital=0.0) -> ReconciliationResult`。
- `GlobalAuditLog().append(*, event_id, actor, action, timestamp, target='', metadata=None)`;`.seal() -> AuditCommitment`;`.chain_valid() -> bool`。

> 实现期第一步永远先 `Read` 该零件源,核对字段名/枚举值(尤其 `OrderSide`/`OrderType`/`RiskDecision.decision` 的确切取值、`reconcile` 返回结构),再写。下方代码按上述签名书写;若与源不符以源为准并相应微调。

---

### Task 1: DryRunRun + DryRunStore（落库)

**Files:** Create `services/execution/dry_run_store.py`; Create `tests/execution/__init__.py`, `tests/execution/test_dry_run_store.py`

**Interfaces:**
- Produces: `DryRunRun`(dataclass: run_id:str, strategy_id:str, timestamp:int, n_intents:int, n_approved:int, n_rejected:int, rejected_reasons:list, pnl:dict, audit_root:str, detail:dict);`DryRunStore(path, *, create=False)` 带 `save_run(run)` / `get_run(run_id)->dict|None` / `all_runs()->list[dict]`;异常类 `DryRunStoreError`。

- [ ] **Step 1: 写失败测试**

```python
# tests/execution/test_dry_run_store.py
import pytest
from services.execution.dry_run_store import DryRunStore, DryRunRun, DryRunStoreError


def _run(rid="r1"):
    return DryRunRun(run_id=rid, strategy_id="carry", timestamp=1000, n_intents=5, n_approved=4,
                     n_rejected=1, rejected_reasons=["FAT_FINGER"], pnl={"net": 0.012},
                     audit_root="abc123", detail={"fills": 4})


def test_save_get_all_roundtrip():
    s = DryRunStore(":memory:", create=True)
    s.save_run(_run("r1")); s.save_run(_run("r2"))
    g = s.get_run("r1")
    assert g["strategy_id"] == "carry" and g["n_approved"] == 4 and g["pnl"]["net"] == 0.012
    assert {r["run_id"] for r in s.all_runs()} == {"r1", "r2"}
    assert s.get_run("nope") is None


def test_missing_db_fails_closed():
    with pytest.raises(DryRunStoreError):
        DryRunStore("nonexistent-dryrun.db", create=False)
```

- [ ] **Step 2: 跑确认 FAIL** — `PYTHONPATH=. python -m pytest tests/execution/test_dry_run_store.py -q` → FAIL(无模块)

- [ ] **Step 3: 实现**

```python
# services/execution/dry_run_store.py
"""干跑流水线运行记录的 append-only sqlite 持久层(沿用 paper_track/experiment_registry 模式)。
一次 DryRunRun = 一次端到端干跑的可复现摘要(意图/批准/拒绝原因/PnL/审计根)。纯 stdlib、确定性、fail-closed。"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field


class DryRunStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class DryRunRun:
    run_id: str
    strategy_id: str
    timestamp: int
    n_intents: int
    n_approved: int
    n_rejected: int
    rejected_reasons: list
    pnl: dict
    audit_root: str
    detail: dict = field(default_factory=dict)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS dry_run (
  run_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, timestamp INTEGER NOT NULL,
  n_intents INTEGER, n_approved INTEGER, n_rejected INTEGER,
  rejected_reasons TEXT, pnl TEXT, audit_root TEXT, detail TEXT
);
"""


class DryRunStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        try:
            rows = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise DryRunStoreError(f"dry-run store unreadable: {exc}") from exc
        if "dry_run" not in {r["name"] for r in rows}:
            raise DryRunStoreError("dry-run store missing table 'dry_run'")

    def save_run(self, run: DryRunRun) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO dry_run(run_id, strategy_id, timestamp, n_intents, n_approved, "
            "n_rejected, rejected_reasons, pnl, audit_root, detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run.run_id, run.strategy_id, int(run.timestamp), run.n_intents, run.n_approved, run.n_rejected,
             json.dumps(run.rejected_reasons), json.dumps(run.pnl), run.audit_root, json.dumps(run.detail)))
        self._conn.commit()

    def _row(self, r) -> dict:
        d = dict(r)
        for k in ("rejected_reasons", "pnl", "detail"):
            d[k] = json.loads(d[k]) if d[k] else None
        return d

    def get_run(self, run_id: str):
        r = self._conn.execute("SELECT * FROM dry_run WHERE run_id=?", (run_id,)).fetchone()
        return self._row(r) if r else None

    def all_runs(self) -> list:
        return [self._row(r) for r in self._conn.execute("SELECT * FROM dry_run ORDER BY timestamp")]
```

- [ ] **Step 4: 跑确认 PASS** — `PYTHONPATH=. python -m pytest tests/execution/test_dry_run_store.py -q` → PASS

- [ ] **Step 5: 提交** — `git add services/execution/dry_run_store.py tests/execution/ && git commit -m "feat(dry-run): DryRunRun + append-only DryRunStore"`

---

### Task 2: 权重 → 目标手数 → 意图

**Files:** Create `services/execution/dry_run_pipeline.py`(本任务先放 `plan_target_quantities`);Test: `tests/execution/test_dry_run_pipeline.py`

**Interfaces:**
- Consumes: `build_intents`(现有)。
- Produces: `plan_target_quantities(weights: Dict[str,float], prices: Dict[str,float], aum: float) -> Dict[str,int]`(`round(w*aum/price)`,价≤0/缺价跳过)。

- [ ] **Step 1: 写失败测试**

```python
# tests/execution/test_dry_run_pipeline.py  (本文件后续任务继续追加)
from services.execution.dry_run_pipeline import plan_target_quantities


def test_target_quantities_round_and_skip_bad_price():
    w = {"BTC": 0.5, "ETH": -0.5, "DOGE": 0.1}
    px = {"BTC": 100.0, "ETH": 50.0, "DOGE": 0.0}        # DOGE price 0 -> skipped
    q = plan_target_quantities(w, px, aum=10000.0)
    assert q == {"BTC": 50, "ETH": -100}                 # 0.5*1e4/100=50 ; -0.5*1e4/50=-100 ; DOGE dropped
```

- [ ] **Step 2: 跑确认 FAIL** — `PYTHONPATH=. python -m pytest tests/execution/test_dry_run_pipeline.py -q` → FAIL

- [ ] **Step 3: 实现**

```python
# services/execution/dry_run_pipeline.py
"""端到端干跑流水线编排器(薄、确定性):权重→目标手数→意图→门(运行时 chokepoint,fail-closed)→
干跑适配器受理(不真发)→纸面成交→对账/PnL→哈希链审计→DryRunRun。核心无 RNG/无墙钟(timestamp/seed
由调用方注入);RiskContext 由注入的 ctx_factory 装配(核心不硬编码账户/安全装配)。编排器不进 research_runner。"""
from __future__ import annotations

from typing import Callable, Dict, Sequence


def plan_target_quantities(weights: Dict[str, float], prices: Dict[str, float], aum: float) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for inst, w in weights.items():
        px = prices.get(inst)
        if px is None or px <= 0:
            continue
        q = int(round(w * aum / px))
        if q != 0:
            out[inst] = q
    return out
```

- [ ] **Step 4: 跑确认 PASS** — `PYTHONPATH=. python -m pytest tests/execution/test_dry_run_pipeline.py -q` → PASS

- [ ] **Step 5: 提交** — `git add services/execution/dry_run_pipeline.py tests/execution/test_dry_run_pipeline.py && git commit -m "feat(dry-run): 权重→目标整数手数"`

---

### Task 3: 门 chokepoint —— screen_intents（fail-closed + kill-switch)

**Files:** Modify `services/execution/dry_run_pipeline.py`; Modify `tests/execution/test_dry_run_pipeline.py`

**Interfaces:**
- Consumes: `PreTradeRiskGate.evaluate(intent, ctx)`、`RiskContext` 及子对象(由测试/驱动器装配的 `ctx_factory(intent)->RiskContext`)。
- Produces: `screen_intents(intents: Sequence, *, gate, ctx_factory) -> dict`(键:`approved`(list[(intent, decision_ref)])、`rejected`(list[(intent, reasons)]))。fail-closed:`decision.decision` 不在批准集 / 评估抛异常 → 入 rejected。

- [ ] **Step 1: 写失败测试**(含真实 RiskContext 装配 helper;benign 批准、超限拒绝、kill-switch latched 全拒)

```python
# 追加进 tests/execution/test_dry_run_pipeline.py
from services.execution.dry_run_pipeline import screen_intents
from services.execution.intent_builder import build_intents
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.checks import RiskLimits
from services.risk.pre_trade.context import RiskContext, AccountRiskState, MarketContext, StrategyApproval
from services.risk.pre_trade.kill_switch import KillSwitch


def _ctx_factory(prices, kill_switch, *, strategy_id="carry", account_id="paper", actor=None):
    def make(intent):
        return RiskContext(
            account=AccountRiskState(account_id=account_id, cash=1e7, equity=1e7, day_pnl=0.0,
                                     peak_equity=1e7, positions={}),
            market=MarketContext(instrument_id=intent.instrument_id, known=True,
                                 reference_price=prices.get(intent.instrument_id, 1.0), market_open=True,
                                 price_as_of=1000, est_daily_volume=1e9, instrument_vol=0.5),
            strategy=StrategyApproval(strategy_id=strategy_id, approved=True, approval_id="ok"),
            kill_switch=kill_switch, safety_config=PreTradeRiskGate().__init__.__defaults__[1]
                if False else __import__("quantos.contracts", fromlist=["SafetyConfig"]).SafetyConfig(),
            actor=actor, now=1000, max_staleness=10**9, recent_order_fingerprints=(), recent_timestamps=(),
            plan_turnover=0.0)
    return make


def _gate():
    return PreTradeRiskGate(RiskLimits(allowed_instruments=("BTC", "ETH"), allowed_accounts=("paper",),
                                       max_position_qty=10**9, max_order_notional=1e12, fat_finger_qty=10**9))


def test_benign_intents_approved():
    ks = KillSwitch()
    intents = build_intents({"BTC": 10, "ETH": -5}, {}, strategy_id="carry")
    out = screen_intents(intents, gate=_gate(), ctx_factory=_ctx_factory({"BTC": 100.0, "ETH": 50.0}, ks))
    assert len(out["approved"]) == 2 and not out["rejected"]


def test_kill_switch_latched_rejects_all():
    ks = KillSwitch(); ks.arm("test")
    intents = build_intents({"BTC": 10}, {}, strategy_id="carry")
    out = screen_intents(intents, gate=_gate(), ctx_factory=_ctx_factory({"BTC": 100.0}, ks))
    assert not out["approved"] and len(out["rejected"]) == 1


def test_fat_finger_rejected_fail_closed():
    ks = KillSwitch()
    gate = PreTradeRiskGate(RiskLimits(allowed_instruments=("BTC",), allowed_accounts=("paper",),
                                       max_position_qty=10**9, max_order_notional=1e12, fat_finger_qty=5))
    intents = build_intents({"BTC": 1000}, {}, strategy_id="carry")    # qty 1000 >> fat_finger 5
    out = screen_intents(intents, gate=gate, ctx_factory=_ctx_factory({"BTC": 100.0}, ks))
    assert not out["approved"] and out["rejected"]
```

> 注:`safety_config` 的取法以源为准——实现期 Read `quantos.contracts` 确认 `SafetyConfig()` 默认即全 false,直接 `from quantos.contracts import SafetyConfig; SafetyConfig()`。上方测试的丑写法仅占位,实现时替换为干净 import。`actor` 若 gate 要求非空,Read `RiskContext`/gate 用法后用受控 system actor;若 gate 在 live 关闭时不卡 actor,可传 None。

- [ ] **Step 2: 跑确认 FAIL** — `PYTHONPATH=. python -m pytest tests/execution/test_dry_run_pipeline.py -k screen -q` → FAIL

- [ ] **Step 3: 实现**(追加到 dry_run_pipeline.py)

```python
# 追加到 services/execution/dry_run_pipeline.py
_APPROVED = {"RISK_APPROVED", "RISK_APPROVED_WITH_LIMITS"}


def _approved(decision) -> bool:
    return str(getattr(decision, "decision", decision)).split(".")[-1] in _APPROVED


def screen_intents(intents: Sequence, *, gate, ctx_factory: Callable) -> dict:
    """运行时 chokepoint:逐意图装配 RiskContext → gate.evaluate → fail-closed 分流。任何非批准/异常入 rejected。"""
    approved, rejected = [], []
    for intent in intents:
        try:
            ctx = ctx_factory(intent)
            decision = gate.evaluate(intent, ctx)
            if _approved(decision):
                ref = gate.issue_ref(decision, intent, ctx)
                approved.append((intent, ref))
            else:
                rejected.append((intent, list(getattr(decision, "reasons", ()) or ["REJECTED"])))
        except Exception as exc:                              # fail-closed:评估异常 -> 拒绝,绝不放行
            rejected.append((intent, [f"GATE_ERROR:{type(exc).__name__}"]))
    return {"approved": approved, "rejected": rejected}
```

- [ ] **Step 4: 跑确认 PASS**(实现期把测试里的 safety_config/actor 占位换成源确认后的干净写法)— `PYTHONPATH=. python -m pytest tests/execution/test_dry_run_pipeline.py -k "screen or kill or fat" -q` → PASS

- [ ] **Step 5: 提交** — `git add -A && git commit -m "feat(dry-run): screen_intents——预交易门运行时 chokepoint(fail-closed + kill-switch)"`

---

### Task 4: 干跑受理 + 纸面成交 + 对账

**Files:** Modify `services/execution/dry_run_pipeline.py`; Modify `tests/execution/test_dry_run_pipeline.py`

**Interfaces:**
- Consumes: `DryRunAdapterEngine.process/simulate_fill`、`PaperFill`、`reconcile`(现有)。
- Produces: `simulate_fills(approved: Sequence, prices: Dict[str,float], *, adapter, run_id: str, timestamp: int) -> dict`(键:`fills`(list[PaperFill])、`reports`(list[NormalizedReport]))。`reconcile_run(fills, marks, initial_capital) -> dict`(返回 PnL 摘要 dict)。

- [ ] **Step 1: 写失败测试**

```python
# 追加进 tests/execution/test_dry_run_pipeline.py
from services.execution.dry_run_pipeline import simulate_fills, reconcile_run
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.trading.dry_run_adapters.domain.models import DryRunAdapterConfig, AdapterType


def _adapter():
    return DryRunAdapterEngine(DryRunAdapterConfig(adapter_type=AdapterType.FIX, seed=0, dry_run=True,
                                                   live_trading_allowed=False, synthetic_latency_ms=0))


def test_simulate_fills_produces_paper_fills_no_live():
    intents = build_intents({"BTC": 10}, {}, strategy_id="carry")
    approved = [(intents[0], "ref-1")]
    out = simulate_fills(approved, {"BTC": 100.0}, adapter=_adapter(), run_id="r1", timestamp=1000)
    assert len(out["fills"]) == 1
    f = out["fills"][0]
    assert f.instrument_id == "BTC" and f.quantity == 10 and f.price > 0
    assert all(r.live_trading_allowed is False and r.dry_run is True for r in out["reports"])  # never live


def test_reconcile_run_returns_pnl_summary():
    intents = build_intents({"BTC": 10}, {}, strategy_id="carry")
    out = simulate_fills([(intents[0], "ref-1")], {"BTC": 100.0}, adapter=_adapter(), run_id="r1", timestamp=1000)
    pnl = reconcile_run(out["fills"], {"BTC": 101.0}, initial_capital=1e7)
    assert "net" in pnl or "realized" in pnl or "total_pnl" in pnl     # some PnL field present
```

> 实现期 Read `DryRunCommand` 必填字段 + `simulate_fill`/`NormalizedReport` + `reconcile` 返回结构,据此填 `_to_command` 的全部字段(command_id/idempotency_key/checksum 用确定性派生,如 `f"{run_id}:{i}"` 的 hashlib)与 `reconcile_run` 的摘要键。

- [ ] **Step 2: 跑确认 FAIL** — `PYTHONPATH=. python -m pytest tests/execution/test_dry_run_pipeline.py -k "fills or reconcile" -q` → FAIL

- [ ] **Step 3: 实现**(追加;字段以源为准)

```python
# 追加到 services/execution/dry_run_pipeline.py
import hashlib
from services.research.paper_trading.domain.models import PaperFill, OrderSide, FillMethod
from services.research.paper_trading.domain.reconciliation import reconcile
from services.trading.dry_run_adapters.domain.models import DryRunCommand, AdapterType


def _det_id(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _to_command(intent, ref: str, price: float, *, idx: int, run_id: str) -> DryRunCommand:
    cid = _det_id(run_id, idx, intent.instrument_id)
    return DryRunCommand(
        command_id=cid, adapter_type=AdapterType.FIX, ems_run_id=run_id, parent_order_id=cid,
        child_order_id=cid, instrument_id=intent.instrument_id, side=str(intent.side),
        order_type=str(intent.order_type), quantity=abs(int(intent.quantity)), price=float(price),
        risk_approval_ref=ref, pre_trade_risk_ref=ref, oms_state_ref=cid, ems_state_ref=cid,
        kill_switch_ref="", idempotency_key=cid, checksum=_det_id("ck", cid),
        dry_run=True, live_trading_allowed=False)            # 永远干跑、永不允许实盘


def simulate_fills(approved: "Sequence", prices: Dict[str, float], *, adapter, run_id: str,
                   timestamp: int) -> dict:
    fills, reports = [], []
    for i, (intent, ref) in enumerate(approved):
        px = prices.get(intent.instrument_id)
        if px is None or px <= 0:
            continue
        run = adapter.process(_to_command(intent, ref, px, idx=i, run_id=run_id))
        rep = adapter.simulate_fill(run.run_id if hasattr(run, "run_id") else getattr(run, "id", run))
        if rep is None:
            continue
        reports.append(rep)
        qty = int(intent.quantity)
        side = OrderSide.BUY if qty > 0 else OrderSide.SELL
        fp = float(getattr(rep, "fill_price", None) or px)
        fills.append(PaperFill(fill_id=_det_id("f", run_id, i), order_id=_det_id("o", run_id, i),
                               run_id=run_id, instrument_id=intent.instrument_id, side=side,
                               quantity=abs(qty), price=fp, fill_method=FillMethod.NEXT_OPEN,
                               timestamp=timestamp, commission=0.0, slippage=0.0))
    return {"fills": fills, "reports": reports}


def reconcile_run(fills, marks: Dict[str, float], initial_capital: float) -> dict:
    res = reconcile(fills, marks=marks, initial_capital=initial_capital)
    # 返回结构以 ReconciliationResult 源为准;转成可 JSON 的摘要 dict
    return {k: getattr(res, k) for k in ("total_pnl", "realized_pnl", "unrealized_pnl", "net")
            if hasattr(res, k)} or {"summary": str(res)}
```

> `adapter.process` 的返回(DryRunAdapterRun)其标识字段名以源为准(`run_id`/`id`),`simulate_fill(rid)` 的入参用之;实现期 Read `engine.py` 确认后去掉上面的 `hasattr` 兜底、直接用真字段。`OrderSide`/`FillMethod`/`OrderType` 的确切成员以 `paper_trading/domain/models.py` 为准。

- [ ] **Step 4: 跑确认 PASS** — `PYTHONPATH=. python -m pytest tests/execution/test_dry_run_pipeline.py -k "fills or reconcile" -q` → PASS

- [ ] **Step 5: 提交** — `git add -A && git commit -m "feat(dry-run): 干跑受理(不真发)+ 纸面成交 + 对账"`

---

### Task 5: 编排器 run_dry_run_pipeline + 审计 + DryRunRun + 确定性

**Files:** Modify `services/execution/dry_run_pipeline.py`; Modify `tests/execution/test_dry_run_pipeline.py`

**Interfaces:**
- Consumes: Task2/3/4 函数、`GlobalAuditLog`、`DryRunRun`(Task1)。
- Produces: `run_dry_run_pipeline(*, weights, prices, current_positions, aum, gate, ctx_factory, adapter, strategy_id, timestamp, marks=None, initial_capital=1e7) -> DryRunRun`。

- [ ] **Step 1: 写失败测试**

```python
# 追加进 tests/execution/test_dry_run_pipeline.py
from services.execution.dry_run_pipeline import run_dry_run_pipeline


def _run(ks=None):
    ks = ks or KillSwitch()
    return run_dry_run_pipeline(
        weights={"BTC": 0.5, "ETH": -0.5}, prices={"BTC": 100.0, "ETH": 50.0}, current_positions={},
        aum=1e6, gate=_gate(), ctx_factory=_ctx_factory({"BTC": 100.0, "ETH": 50.0}, ks),
        adapter=_adapter(), strategy_id="carry", timestamp=1000, marks={"BTC": 101.0, "ETH": 49.0})


def test_end_to_end_benign_produces_run():
    r = _run()
    assert r.strategy_id == "carry" and r.n_intents == 2 and r.n_approved == 2 and r.n_rejected == 0
    assert r.audit_root and isinstance(r.pnl, dict)


def test_kill_switch_latched_zero_fills():
    ks = KillSwitch(); ks.arm("halt")
    r = _run(ks)
    assert r.n_approved == 0 and r.detail.get("n_fills", 0) == 0    # latched -> nothing fills


def test_deterministic_same_input_same_run():
    a, b = _run(), _run()
    assert (a.n_intents, a.n_approved, a.n_rejected, a.audit_root, a.pnl) == \
           (b.n_intents, b.n_approved, b.n_rejected, b.audit_root, b.pnl)
```

- [ ] **Step 2: 跑确认 FAIL** — `PYTHONPATH=. python -m pytest tests/execution/test_dry_run_pipeline.py -k "end_to_end or latched or deterministic" -q` → FAIL

- [ ] **Step 3: 实现**(追加)

```python
# 追加到 services/execution/dry_run_pipeline.py
from services.monitoring.audit_log import GlobalAuditLog
from services.execution.dry_run_store import DryRunRun


def run_dry_run_pipeline(*, weights, prices, current_positions, aum, gate, ctx_factory, adapter,
                         strategy_id: str, timestamp: int, marks=None, initial_capital: float = 1e7) -> DryRunRun:
    from services.execution.intent_builder import build_intents
    run_id = _det_id(strategy_id, timestamp, tuple(sorted(weights)))
    audit = GlobalAuditLog()
    audit.append(event_id=_det_id("a0", run_id), actor=f"dry_run:{strategy_id}", action="PIPELINE_START",
                 timestamp=timestamp, target=run_id, metadata={"aum": aum, "n_weights": len(weights)})

    targets = plan_target_quantities(weights, prices, aum)
    intents = build_intents(targets, current_positions, strategy_id=strategy_id)
    screened = screen_intents(intents, gate=gate, ctx_factory=ctx_factory)
    for intent, reasons in screened["rejected"]:
        audit.append(event_id=_det_id("rej", run_id, intent.instrument_id), actor=f"dry_run:{strategy_id}",
                     action="INTENT_REJECTED", timestamp=timestamp, target=intent.instrument_id,
                     metadata={"reasons": reasons})

    filled = simulate_fills(screened["approved"], prices, adapter=adapter, run_id=run_id, timestamp=timestamp)
    for f in filled["fills"]:
        audit.append(event_id=_det_id("fill", run_id, f.instrument_id), actor=f"dry_run:{strategy_id}",
                     action="DRY_RUN_FILL", timestamp=timestamp, target=f.instrument_id,
                     metadata={"qty": f.quantity, "price": f.price})
    pnl = reconcile_run(filled["fills"], marks or prices, initial_capital)
    commit = audit.seal()
    audit_root = getattr(commit, "root_hash", None) or getattr(commit, "head_hash", None) or str(commit)

    return DryRunRun(
        run_id=run_id, strategy_id=strategy_id, timestamp=timestamp, n_intents=len(intents),
        n_approved=len(screened["approved"]), n_rejected=len(screened["rejected"]),
        rejected_reasons=[rr for _, rs in screened["rejected"] for rr in rs], pnl=pnl, audit_root=str(audit_root),
        detail={"n_fills": len(filled["fills"]), "targets": targets})
```

> `audit.seal()` 的承诺字段名(root_hash/head_hash)以 `audit_log.py` 源为准,实现期去兜底。

- [ ] **Step 4: 跑确认 PASS** — `PYTHONPATH=. python -m pytest tests/execution/test_dry_run_pipeline.py -q` → PASS（全文件)

- [ ] **Step 5: 提交** — `git add -A && git commit -m "feat(dry-run): run_dry_run_pipeline 编排器——端到端 + 哈希链审计 + 确定性 DryRunRun"`

---

### Task 6: 驱动器 tools/dry_run_carry.py（carry 端到端真跑)

**Files:** Create `tools/dry_run_carry.py`

**Interfaces:**
- Consumes: Task1/5、carry 信号(COMPOSITE/vol-target)、`crypto_funding.db` 价格、`DryRunStore`。

- [ ] **Step 1: 实现**

```python
# tools/dry_run_carry.py
"""端到端干跑驱动器:把 carry 组合(近端一期 vol-target 权重)从信号一路跑到纸面成交+对账+审计,落库一次
DryRunRun。墙钟/seed 只在此边界。读本地库、无网络、无真单/真钱。装配真实 RiskContext(LIVE 全 false、门 fail-closed)。"""
from __future__ import annotations

import os
import sys
import time

from quantos.contracts import SafetyConfig
from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research import alpha_expression as ax
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.checks import RiskLimits
from services.risk.pre_trade.context import RiskContext, AccountRiskState, MarketContext, StrategyApproval
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.trading.dry_run_adapters.domain.models import DryRunAdapterConfig, AdapterType
from services.execution.dry_run_pipeline import run_dry_run_pipeline
from services.execution.dry_run_store import DryRunStore
from tools.crypto_carry_strategy import COMPOSITE, QUANTILE, _legs

_DB = os.path.join("data", "cache", "crypto_funding.db")
_OUT = os.path.join("data", "cache", "dry_run.db")


def main() -> None:
    import json
    instruments = sorted(json.load(open(os.path.join("data", "cache", "crypto_funding_snapshot.json"),
                                         encoding="utf-8"))["instruments"])
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    funding = {tk: {int(k): v for k, v in d.items()} for tk, d in
               json.load(open(os.path.join("data", "cache", "crypto_funding_rates.json"), encoding="utf-8")).items()}
    ets = sorted({b.event_time for inst in instruments for b in store.fetch_bars(inst, -10**18, 10**18)})
    panel = ax.build_field_panel(store, instruments, ets, ("close", "volume"))
    panel.matrices["funding"] = [[funding.get(i, {}).get(t) for i in panel.instruments] for t in panel.event_times]
    sig = ax.evaluate(COMPOSITE, panel)
    last = sig[-1]
    w = _legs(last, QUANTILE)                                # 最近一期的目标多空权重(按 instrument index)
    weights = {panel.instruments[i]: wt for i, wt in w.items()}
    prices = {panel.instruments[i]: panel.matrices["close"][-1][i] for i in w
              if panel.matrices["close"][-1][i]}
    allowed = tuple(weights)
    now = int(time.time() * 1000)                            # 墙钟仅此边界

    def ctx_factory(intent):
        return RiskContext(
            account=AccountRiskState(account_id="paper", cash=1e7, equity=1e7, day_pnl=0.0, peak_equity=1e7,
                                     positions={}),
            market=MarketContext(instrument_id=intent.instrument_id, known=True,
                                 reference_price=prices.get(intent.instrument_id, 1.0), market_open=True,
                                 price_as_of=now, est_daily_volume=1e9, instrument_vol=0.8),
            strategy=StrategyApproval(strategy_id="carry", approved=True, approval_id="research-dry-run"),
            kill_switch=ks, safety_config=SafetyConfig(), actor=None, now=now, max_staleness=10**12,
            recent_order_fingerprints=(), recent_timestamps=(), plan_turnover=0.0)

    ks = KillSwitch()
    gate = PreTradeRiskGate(RiskLimits(allowed_instruments=allowed, allowed_accounts=("paper",),
                                       max_position_qty=10**9, max_order_notional=1e12, fat_finger_qty=10**9))
    adapter = DryRunAdapterEngine(DryRunAdapterConfig(adapter_type=AdapterType.FIX, seed=0, dry_run=True,
                                                      live_trading_allowed=False, synthetic_latency_ms=0))
    run = run_dry_run_pipeline(weights=weights, prices=prices, current_positions={}, aum=1e6, gate=gate,
                               ctx_factory=ctx_factory, adapter=adapter, strategy_id="carry", timestamp=now,
                               marks=prices, initial_capital=1e7)
    if os.path.exists(_OUT):
        os.remove(_OUT)
    DryRunStore(_OUT, create=True).save_run(run)
    print(f"DryRunRun {run.run_id}: 意图 {run.n_intents} 批准 {run.n_approved} 拒绝 {run.n_rejected} "
          f"成交 {run.detail.get('n_fills')} | PnL {run.pnl} | 审计根 {run.audit_root[:12]} -> {_OUT}", flush=True)
    if run.rejected_reasons:
        print("  拒绝原因:", run.rejected_reasons[:10], flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: import 检查 + 真跑** — `PYTHONPATH=. python -c "import tools.dry_run_carry"`(OK);`PYTHONPATH=. python -m tools.dry_run_carry`(打印一次 DryRunRun;`data/cache/dry_run.db` 生成)。若门把全部意图拒了,Read 拒绝原因 + gate 源,修 RiskContext 装配(常见:actor/strategy approved/market known),直至 benign 意图获批。
- [ ] **Step 3: 提交** — `git add tools/dry_run_carry.py && git commit -m "research(dry-run): carry 端到端干跑驱动器——信号→门→纸面成交→对账→落库"`

---

### Task 7: 全门绿 + 治理 + 收尾

**Files:** 无(验证 + 记忆/状态)

- [ ] **Step 1: 全门禁** — `python scripts/validate.py` → `VALIDATE PASSED`(含新 execution 测试 + 治理:`services/` 无网络 import 仍绿、`test_research_runner_boundary` 仍绿)。
- [ ] **Step 2: 治理点检** — grep `services/execution/dry_run_pipeline.py` 与 `dry_run_store.py`:无 `import requests/urllib/socket/aiohttp`、无券商 SDK、无 `research_runner` 引用;`DryRunCommand` 恒 `dry_run=True, live_trading_allowed=False`。
- [ ] **Step 3: 记忆 + 现状图** — `CLAUDE.md`/`docs/PROJECT_STATE.md` 记 `services/execution/dry_run_pipeline` 端到端干跑流水线落地;干跑 carry 一次运行结果记入活文档。
- [ ] **Step 4: 提交** — `git add -A && git commit -m "docs(state): 端到端干跑流水线落地 + carry 干跑首跑记入活文档"`

---

## Self-Review

**Spec coverage**(逐节对 spec):
- §4.1 run_dry_run_pipeline → Task 5(+2/3/4 拆出 plan_target_quantities/screen_intents/simulate_fills/reconcile_run)。✓
- §4.2 DryRunStore → Task 1。✓  §4.3 驱动器 → Task 6。✓
- §5 数据流 → Task 5 编排顺序。✓
- §6 安全(LIVE false、门 fail-closed、kill-switch 先查、不真发、不进 research_runner、确定性)→ Task 3(fail-closed+kill-switch 测试)、Task 4(dry_run/live_trading_allowed=False 测试)、Task 5(确定性测试)、Task 7(治理点检)、Global Constraints。✓
- §7 测试 → 各 Task TDD + Task 7 门绿。✓  §8 YAGNI(不接 console/实盘)→ 计划未含。✓

**Placeholder scan:** 无 TBD/TODO。多处"以源为准"注记是**诚实的接口校验提示**(字段名/枚举/返回结构需 Read 现有模块确认),非占位符——每步都给了按已探签名书写的完整代码;实现者照写、若与源微差则按注记微调。Task 3 测试里 safety_config 的丑占位已显式标注"实现期替换为干净 import"。

**Type consistency:** `DryRunRun` 字段(Task1)在 Task5 装配一致;`screen_intents`→`{approved,rejected}`(Task3)被 Task5 按键消费一致;`simulate_fills`→`{fills,reports}`、`reconcile_run`→pnl dict(Task4)被 Task5 一致消费;`plan_target_quantities`(Task2)→`build_intents` targets 一致;`_det_id`/`_approved` 跨任务复用同名。✓

> 实现期红线:每个 Task 第一步先 `Read` 它要调的现有模块源,核对字段/枚举/返回,再落代码——本计划代码按已探签名书写,但 `OrderSide/OrderType/FillMethod` 成员、`reconcile` 返回结构、`audit.seal()` 承诺字段、`DryRunAdapterRun` 标识字段这些细节以源为准。
