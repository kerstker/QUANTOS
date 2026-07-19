# broker adapter 接缝 实现计划

> **Execution:** Follow the repository's task-by-task TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"实盘 = 换适配器 + 人工批准"里的「换适配器」从口号变成一个正式、可测、L2 合规的接缝:一个抽象 `BrokerAdapter` 接口、测过的干跑引擎经薄包装 conform、一个在 L2 下连构造都不行的 `LiveBrokerAdapter` 占位,外加一道 fail-closed 守卫接进干跑流水线让接缝载荷承重。

**Architecture:** 单一新文件 `services/trading/broker_adapter.py` 放 `BrokerAdapter`(Protocol)+ `DryRunBrokerAdapter`(薄包装现有 `DryRunAdapterEngine`,零改动它)+ `LiveBrokerAdapter`(结构性拒绝的空壳)+ `assert_dry_run_adapter` 守卫。守卫接进 `services/execution/dry_run_pipeline.py::simulate_fills` 第一行。纯加法:不改引擎/门/kill-switch/五个 LIVE 标志。

**Tech Stack:** Python 标准库(`typing.Protocol`/`runtime_checkable`)+ 复用 `quantos.contracts.config.SafetyConfig`/`SafetyViolation` + `services/trading/dry_run_adapters` 现有契约。无第三方依赖。

## Global Constraints

- **L2_DRY_RUN_ONLY,最高优先级,一寸不让**:五个 LIVE 标志(`live_trading_enabled/real_broker_enabled/allow_order_submission/allow_ai_direct_trading/require_human_approval`)不动、值不变。
- `broker_adapter.py` **绝不 import 券商/CTP/FIX/交易所 SDK,绝不 import 网络库**(`ccxt/binance/coinbase/kraken/ib_insync/alpaca/fix/quickfix/ctp/vnpy` 等;`requests/urllib/socket/httpx/aiohttp/websocket` 等)。
- **绝不写任何能真发单/真凭证/真连接的代码路径**;`LiveBrokerAdapter` 永远是构造即 raise 的空壳,无真实现。
- 接缝是**加法**:零改动 `DryRunAdapterEngine`,不弱化 `PreTradeRiskGate`/`ExecutionBoundary`/`KillSwitch`;不重命名 `DryRun*` 契约;不接 console;`broker_adapter.py` 不进 `research_runner`;核心无 RNG/墙钟。
- 单一门禁 `python scripts/validate.py` 必须绿;隔离 pytest 用 `PYTHONPATH=. python -m pytest ...` 从仓库根跑。提交中文,结尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

## File Structure

- `services/trading/broker_adapter.py` (Create) — `BrokerAdapter` Protocol + `DryRunBrokerAdapter` + `LiveBrokerAdapter` + `assert_dry_run_adapter`。
- `tests/trading/test_broker_adapter.py` (Create) — 接缝单元测试。
- `tests/trading/__init__.py` (Create if missing)。
- `services/execution/dry_run_pipeline.py` (Modify) — `simulate_fills` 开头插一行 `assert_dry_run_adapter(adapter)`。
- `tests/execution/test_dry_run_pipeline.py` (Modify) — 守卫的正/负回归。
- `tests/governance/test_broker_adapter_boundary.py` (Create) — 无 SDK import + L2 下 live 不可构造 机器钉死。

## 现有真签名(本会话已探明,实现期先 Read 复核)

- `services/trading/dry_run_adapters/domain/engine.py::DryRunAdapterEngine`:`__init__(self)`(无参);`process(self, cmd: DryRunCommand, existing_ids: List[str] = None) -> DryRunAdapterRun`;`simulate_fill(self, rid: str) -> Optional[NormalizedReport]`。
- `services/trading/dry_run_adapters/domain/models.py`:`DryRunCommand`(dataclass,全字段有默认;含 `command_id`/`adapter_type="DRY_RUN_CTP"`/`instrument_id`/`side`/`quantity`/`dry_run=True`/`live_trading_allowed=False`…);`DryRunAdapterRun`(有 `run_id` 默认 uuid4、`rejection_reasons: List[str]`、`dry_run=True`);`NormalizedReport`(有 `status`/`fill_price`/`fill_quantity`/`dry_run=True`)。
- `quantos/contracts/config.py`:`SafetyConfig`(pydantic frozen;`live_trading_armed` property = `live_trading_enabled ∧ real_broker_enabled ∧ allow_order_submission ∧ ¬allow_ai_direct_trading`);`SafetyViolation(RuntimeError)`;`RESEARCH_SAFE_DEFAULTS = SafetyConfig()`(全默认 → `live_trading_armed` False)。构造 live-armed:`SafetyConfig(live_trading_enabled=True, real_broker_enabled=True, allow_order_submission=True)`(`allow_ai_direct_trading` 默认 False → `live_trading_armed` True)。
- `services/execution/dry_run_pipeline.py::simulate_fills(approved: Sequence, prices: Dict[str, float], *, adapter, run_id: str, timestamp: int, cost_bps: float = 0.0) -> dict`(当前 duck-typed 调 `adapter.process`/`adapter.simulate_fill`)。
- `tests/governance/test_crypto_data_boundary.py`:`_import_roots(path) -> set`(AST 取 import 根)、`_FORBIDDEN_SDK`(集合)、`_NETWORK_ROOTS`(集合)、`test_services_tree_has_no_network_imports`(**已覆盖** 全 `services/` 无网络 import,含新文件)。

> 每个 Task 第一步先 `Read` 它要调的现有模块源,核对字段/签名再落代码。

---

### Task 1: `broker_adapter.py`（接口 + 干跑包装 + live 拒绝桩 + 守卫）

**Files:**
- Create: `services/trading/broker_adapter.py`
- Create: `tests/trading/__init__.py`(空)、`tests/trading/test_broker_adapter.py`

**Interfaces:**
- Consumes: `DryRunAdapterEngine`/`DryRunCommand`/`DryRunAdapterRun`/`NormalizedReport`(dry_run_adapters)、`SafetyConfig`/`SafetyViolation`/`RESEARCH_SAFE_DEFAULTS`(contracts.config)。
- Produces: `BrokerAdapter`(runtime_checkable Protocol:`is_dry_run: bool`、`submit_order(cmd)->DryRunAdapterRun`、`fetch_fill(run_id)->Optional[NormalizedReport]`);`DryRunBrokerAdapter(engine=None)`(`is_dry_run=True`);`LiveBrokerAdapter(safety_config)`(构造即 raise);`assert_dry_run_adapter(adapter)->None`。

- [ ] **Step 1: 写失败测试**

```python
# tests/trading/test_broker_adapter.py
import pytest

from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS, SafetyConfig, SafetyViolation
from services.trading.broker_adapter import (BrokerAdapter, DryRunBrokerAdapter, LiveBrokerAdapter,
                                             assert_dry_run_adapter)
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.trading.dry_run_adapters.domain.models import DryRunCommand


def _cmd():
    return DryRunCommand(command_id="c1", adapter_type="DRY_RUN_GENERIC", instrument_id="BTC",
                         side="BUY", order_type="MARKET", quantity=1, price=100.0,
                         risk_approval_ref="r", pre_trade_risk_ref="r", oms_state_ref="VALIDATED")


def test_dry_run_broker_adapter_satisfies_protocol():
    a = DryRunBrokerAdapter()
    assert isinstance(a, BrokerAdapter)          # runtime_checkable structural match
    assert a.is_dry_run is True


def test_dry_run_broker_adapter_delegates_to_engine():
    engine = DryRunAdapterEngine()
    a = DryRunBrokerAdapter(engine)
    run = a.submit_order(_cmd())                  # delegates to engine.process
    assert not run.rejection_reasons and run.dry_run is True
    rep = a.fetch_fill(run.run_id)                # delegates to engine.simulate_fill
    assert rep is not None and rep.dry_run is True and rep.live_trading_allowed is False


def test_live_broker_adapter_refuses_construction_at_l2():
    with pytest.raises(SafetyViolation):
        LiveBrokerAdapter(RESEARCH_SAFE_DEFAULTS)     # live_trading_armed is False -> raise


def test_live_broker_adapter_refuses_even_when_armed():
    armed = SafetyConfig(live_trading_enabled=True, real_broker_enabled=True, allow_order_submission=True)
    assert armed.live_trading_armed is True
    with pytest.raises(SafetyViolation):
        LiveBrokerAdapter(armed)                       # no real implementation -> still raise


def test_live_broker_adapter_methods_are_fail_closed():
    # construction always raises, so bypass __init__ to prove the method bodies also refuse
    inst = object.__new__(LiveBrokerAdapter)
    assert inst.is_dry_run is False
    with pytest.raises(SafetyViolation):
        inst.submit_order(_cmd())
    with pytest.raises(SafetyViolation):
        inst.fetch_fill("x")


def test_assert_dry_run_adapter_allows_dry_run():
    assert_dry_run_adapter(DryRunAdapterEngine())     # whitelisted engine
    assert_dry_run_adapter(DryRunBrokerAdapter())     # whitelisted wrapper

    class _DuckDryRun:
        is_dry_run = True
    assert_dry_run_adapter(_DuckDryRun())             # self-attested dry-run duck


def test_assert_dry_run_adapter_rejects_unknown_fail_closed():
    with pytest.raises(SafetyViolation):
        assert_dry_run_adapter(object())              # unknown -> fail-closed
```

- [ ] **Step 2: 跑确认 FAIL** — `PYTHONPATH=. python -m pytest tests/trading/test_broker_adapter.py -q` → FAIL(模块不存在)

- [ ] **Step 3: 实现**

```python
# services/trading/broker_adapter.py
"""Broker adapter 接缝(轨道3 子项目1)—— L2_DRY_RUN_ONLY,一寸不让。

把"实盘 = 换适配器 + 人工批准"里的「换适配器」变成一个正式接缝:BrokerAdapter Protocol 定义执行适配器
的契约,测过的干跑引擎经 DryRunBrokerAdapter 薄包装 conform(零改动引擎),LiveBrokerAdapter 是接缝的
live 槽位——但它在 L2 下**结构上无法构造**(live_trading_armed 恒 False → 构造即 raise),且**没有任何真
实现**(即便强凑 armed 配置也第二次 raise)。绝不 import 任何券商/网络 SDK。assert_dry_run_adapter 是 fail-
closed 守卫,接进干跑流水线让接缝载荷承重(运行时只放行证明为干跑的适配器)。

诚实边界:守卫是运行时防误用的 chokepoint,NOT 对受信内进程代码的信任边界——那个 build-time 面(受信代码
可绕过)是轨道3 子项目3(进程隔离)的范畴。真券商适配器实现 = post-L2、人工门控 = 显式非目标。
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from quantos.contracts.config import SafetyConfig, SafetyViolation
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.trading.dry_run_adapters.domain.models import (DryRunAdapterRun, DryRunCommand,
                                                             NormalizedReport)


@runtime_checkable
class BrokerAdapter(Protocol):
    """执行适配器接缝。`DryRun*` 前缀是历史名——它们就是当前流经流水线的规范订单/回报契约,真适配器
    消费同一套(重命名它们是本子项目非目标)。"""
    is_dry_run: bool

    def submit_order(self, cmd: DryRunCommand) -> DryRunAdapterRun: ...

    def fetch_fill(self, run_id: str) -> Optional[NormalizedReport]: ...


class DryRunBrokerAdapter:
    """薄包装现有 DryRunAdapterEngine(零改动它),满足 BrokerAdapter。绝不真发。"""
    is_dry_run = True

    def __init__(self, engine: Optional[DryRunAdapterEngine] = None) -> None:
        self._engine = engine if engine is not None else DryRunAdapterEngine()

    def submit_order(self, cmd: DryRunCommand) -> DryRunAdapterRun:
        return self._engine.process(cmd)

    def fetch_fill(self, run_id: str) -> Optional[NormalizedReport]:
        return self._engine.simulate_fill(run_id)


class LiveBrokerAdapter:
    """接缝的 live 槽位 —— **结构性拒绝的空壳,无真实现**。L2 下 live_trading_armed 恒 False → 构造即 raise;
    即便强凑 armed 配置,也因无真实现再次 raise。绝不 import 券商/网络 SDK,无任何真发单/真凭证/真连接。
    真实现 = post-L2、人工门控 = 显式非目标。"""
    is_dry_run = False

    def __init__(self, safety_config: SafetyConfig) -> None:
        if not safety_config.live_trading_armed:
            raise SafetyViolation(
                "LiveBrokerAdapter cannot be constructed at L2_DRY_RUN_ONLY "
                "(live_trading_armed is False) — intentional seam refusal.")
        raise SafetyViolation(
            "LiveBrokerAdapter is a non-functional seam placeholder — a real broker "
            "implementation is post-L2, human-gated, and out of scope.")

    def submit_order(self, cmd: DryRunCommand) -> DryRunAdapterRun:
        raise SafetyViolation("live order submission is disabled (L2_DRY_RUN_ONLY)")

    def fetch_fill(self, run_id: str) -> Optional[NormalizedReport]:
        raise SafetyViolation("live fill fetch is disabled (L2_DRY_RUN_ONLY)")


def assert_dry_run_adapter(adapter) -> None:
    """fail-closed 守卫:只放行证明为干跑的适配器,未知一律拒。运行时防误用的 chokepoint(非受信代码信任边界)。"""
    if isinstance(adapter, LiveBrokerAdapter):        # 实际到不了(构造已 raise),防御性
        raise SafetyViolation("live adapter is not permitted in the dry-run pipeline (L2)")
    if isinstance(adapter, (DryRunAdapterEngine, DryRunBrokerAdapter)):
        return
    if getattr(adapter, "is_dry_run", False) is True:
        return
    raise SafetyViolation(
        f"unknown adapter {type(adapter).__name__} is not a proven dry-run adapter (fail-closed)")
```

- [ ] **Step 4: 跑确认 PASS** — `PYTHONPATH=. python -m pytest tests/trading/test_broker_adapter.py -q` → PASS(7 passed)

- [ ] **Step 5: 提交** — `git add services/trading/broker_adapter.py tests/trading/ && git commit -m "feat(trading): broker adapter 接缝——BrokerAdapter Protocol + 干跑包装 + 结构性拒绝的 live 桩 + fail-closed 守卫"`

---

### Task 2: 守卫接进干跑流水线（接缝载荷承重）

**Files:**
- Modify: `services/execution/dry_run_pipeline.py`（`simulate_fills` 开头)
- Modify: `tests/execution/test_dry_run_pipeline.py`

**Interfaces:**
- Consumes: `assert_dry_run_adapter`（Task 1)、现有 `simulate_fills`/`DryRunAdapterEngine`。
- Produces: `simulate_fills` 第一行调 `assert_dry_run_adapter(adapter)`(未知/非干跑适配器 → `SafetyViolation`)。

- [ ] **Step 1: 写失败测试**（追加进 `tests/execution/test_dry_run_pipeline.py`)

```python
# 追加进 tests/execution/test_dry_run_pipeline.py
import pytest as _pytest
from quantos.contracts.config import SafetyViolation
from services.trading.broker_adapter import DryRunBrokerAdapter


def test_simulate_fills_rejects_unknown_adapter_fail_closed():
    intents = build_intents({"BTC": 10}, {}, strategy_id="carry")
    with _pytest.raises(SafetyViolation):
        simulate_fills([(intents[0], "ref-1")], {"BTC": 100.0}, adapter=object(),
                       run_id="r1", timestamp=1000)


def test_simulate_fills_accepts_dry_run_broker_adapter():
    intents = build_intents({"BTC": 10}, {}, strategy_id="carry")
    out = simulate_fills([(intents[0], "ref-1")], {"BTC": 100.0}, adapter=DryRunBrokerAdapter(),
                         run_id="r1", timestamp=1000)
    assert len(out["fills"]) == 1 and out["fills"][0].instrument_id == "BTC"
```
(既有 `test_simulate_fills_produces_paper_fills_no_live` 等仍传 `DryRunAdapterEngine()`,守卫白名单放行 → 不回归。)

- [ ] **Step 2: 跑确认 FAIL** — `PYTHONPATH=. python -m pytest tests/execution/test_dry_run_pipeline.py -k "unknown_adapter or dry_run_broker" -q` → FAIL(第一个:未加守卫故不 raise;第二个:`DryRunBrokerAdapter` 已在 Task1 可用,应能过——若 import 未就绪则一起 FAIL)

- [ ] **Step 3: 实现**（`services/execution/dry_run_pipeline.py`)

顶部 import 区加:
```python
from services.trading.broker_adapter import assert_dry_run_adapter
```
`simulate_fills` 函数体第一行(docstring 之后、循环之前)加:
```python
    assert_dry_run_adapter(adapter)  # fail-closed:运行时只放行证明为干跑的适配器
```

- [ ] **Step 4: 跑确认 PASS** — `PYTHONPATH=. python -m pytest tests/execution/test_dry_run_pipeline.py -q` → PASS(全文件,含既有不回归)

- [ ] **Step 5: 提交** — `git add services/execution/dry_run_pipeline.py tests/execution/test_dry_run_pipeline.py && git commit -m "feat(execution): 干跑流水线接缝守卫——simulate_fills 首行 assert_dry_run_adapter(fail-closed)"`

---

### Task 3: 治理测试 + 全门绿 + 文档

**Files:**
- Create: `tests/governance/test_broker_adapter_boundary.py`
- 验证 + 文档(CLAUDE.md / PROJECT_STATE.md / 记忆)

**Interfaces:**
- Consumes: `tests/governance/test_crypto_data_boundary.py` 的 `_import_roots`/`_FORBIDDEN_SDK`/`_NETWORK_ROOTS` 模式(实现期 Read 复核确切名字后复用或重实现同逻辑)。

- [ ] **Step 1: 写治理测试**

```python
# tests/governance/test_broker_adapter_boundary.py
"""治理:机器钉死 broker adapter 接缝的 L2 安全边界。
1) broker_adapter.py 不 import 任何券商/交易所 SDK、不 import 网络库;
2) LiveBrokerAdapter 在 RESEARCH_SAFE_DEFAULTS(L2 默认)下无法实例化(构造即 raise)。
(services/ 全树无网络 import 已由 test_crypto_data_boundary.py::test_services_tree_has_no_network_imports 覆盖。)"""
import ast
import os

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BROKER = os.path.join(_ROOT, "services", "trading", "broker_adapter.py")

_FORBIDDEN_SDK = {"ccxt", "binance", "coinbase", "cbpro", "krakenex", "kraken", "ib_insync",
                  "ibapi", "alpaca", "alpaca_trade_api", "oandapyV20", "fix", "quickfix", "ctp", "vnpy"}
_NETWORK_ROOTS = {"urllib", "socket", "requests", "httpx", "aiohttp", "websocket", "websockets",
                  "ftplib", "telnetlib", "http"}


def _import_roots(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    roots = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                roots.add(a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            roots.add(n.module.split(".")[0])
    return roots


def test_broker_adapter_imports_no_sdk_or_network():
    roots = _import_roots(_BROKER)
    assert not (roots & _FORBIDDEN_SDK), f"FORBIDDEN broker/exchange SDK in broker_adapter.py: {sorted(roots & _FORBIDDEN_SDK)}"
    assert not (roots & _NETWORK_ROOTS), f"network import in broker_adapter.py: {sorted(roots & _NETWORK_ROOTS)}"


def test_live_broker_adapter_uninstantiable_at_l2():
    from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS, SafetyViolation
    from services.trading.broker_adapter import LiveBrokerAdapter
    assert RESEARCH_SAFE_DEFAULTS.live_trading_armed is False
    with pytest.raises(SafetyViolation):
        LiveBrokerAdapter(RESEARCH_SAFE_DEFAULTS)
```

- [ ] **Step 2: 跑确认 PASS** — `PYTHONPATH=. python -m pytest tests/governance/test_broker_adapter_boundary.py -q` → PASS

- [ ] **Step 3: 全门禁** — `python scripts/validate.py` → `VALIDATE PASSED`(新接缝测试 + 治理 + 既有干跑流水线不回归;`services/` 无网络 import 仍绿)。

- [ ] **Step 4: 文档/记忆** — `CLAUDE.md` 加 `services/trading/broker_adapter.py` 条目(接缝形式化、干跑包装、live 桩结构性拒绝、守卫载荷承重、L2 边界);`docs/PROJECT_STATE.md` 记轨道3 子项目1 落地 + 后续子项目2/3;记忆补一条(轨道3 接缝 + L2 拒绝机制)。

- [ ] **Step 5: 提交** — `git add -A && git commit -m "test(governance)+docs: broker 接缝 L2 边界机器钉死(无SDK/网络、L2 下 live 不可构造)+ 记入活文档"`

---

## Self-Review

**Spec coverage**(逐节对 spec):
- §4.1 `BrokerAdapter` Protocol → Task 1。✓  §4.2 `DryRunBrokerAdapter` → Task 1。✓
- §4.3 `LiveBrokerAdapter`(双 raise + 方法 raise + 无 SDK)→ Task 1(实现)+ Task 3(治理钉死)。✓
- §4.4 `assert_dry_run_adapter` → Task 1(实现 + 单测)。✓
- §5 数据流(守卫接进 simulate_fills)→ Task 2。✓
- §6 安全(无 SDK/网络、L2 无法构造、加法不弱化、不进 research_runner)→ Global Constraints + Task 3 治理。✓
- §7 测试(conformance/委托一致/构造两路 raise/方法 fail-closed/守卫放行拒绝/流水线正负/治理)→ Task 1+2+3。✓
- §8 YAGNI(不重命名契约、不接 console、不动引擎/门/flags)→ 计划遵守。✓

**Placeholder scan:** 无 TBD/TODO。每步给了完整代码。"实现期先 Read 复核"是诚实的接口校验提示。

**Type consistency:** `BrokerAdapter`/`DryRunBrokerAdapter`/`LiveBrokerAdapter`/`assert_dry_run_adapter`(Task1 定义)在 Task2(`simulate_fills` 调 `assert_dry_run_adapter` + 测试 import `DryRunBrokerAdapter`)、Task3(治理 import `LiveBrokerAdapter`)一致消费;`submit_order`/`fetch_fill`/`is_dry_run` 跨 Protocol/两实现/守卫命名一致;`SafetyViolation`/`SafetyConfig.live_trading_armed`/`RESEARCH_SAFE_DEFAULTS` 是既有真符号,跨任务引用一致。✓
