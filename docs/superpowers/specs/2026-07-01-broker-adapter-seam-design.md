# broker adapter 接缝 —— 设计 spec

> 状态:设计已与用户确认(2026-07-01,方案 A)。下一步:writing-plans。
> 约定:中文叙述,代码标识符英文。安全边界见 §6,**不可让,最高优先级**。
> 由来:轨道3(通往实盘的安全脚手架)的**子项目 1**。轨道3 = 3 个独立子项目(各自一套 spec→plan→实现):
> (1) **broker adapter 接缝(本 spec)** →(2)多步人工批准状态机 →(3)Phase 14 进程隔离设计。按重要程度/
> 依赖顺序,接缝先做(是另两块的地基)。

## 0. 最高约束(L2_DRY_RUN_ONLY,一寸不让)

本轨**只做设计 + 安全脚手架**,绝不真上实盘、绝不引入真券商连接、绝不弱化任何门。具体到本子项目:
- 五个 LIVE 标志(`live_trading_enabled/real_broker_enabled/allow_order_submission/allow_ai_direct_trading/
  require_human_approval`)不动、值不变(前四恒 false、末一恒 true)。
- **绝不 import 任何券商/CTP/FIX/交易所 SDK,绝不 import 网络库**(`services/` 树零网络 import 机器强制)。
- 绝不写任何能真发单/真凭证/真连接的代码路径。
- 新增的 `LiveBrokerAdapter` 是**结构性拒绝的空壳**(构造即 raise),不是能用的实现。

## 1. 目标(一句话)

把"实盘 = 换适配器 + 人工批准"里的**"换适配器"**从口号变成一个**正式、可测、L2 合规的接缝**:一个抽象
`BrokerAdapter` 接口(Protocol),测过的干跑引擎经薄包装 conform,再加一个**在 L2 下连构造都不行**的
`LiveBrokerAdapter` 占位——从而"换适配器"有了确切的接口契约,而那个接口的唯一可用实现仍是干跑。

## 2. 已确认决策(方案 A)

- 薄 `Protocol` 接缝 + 干跑实现 conform(**零改动**测过的 `DryRunAdapterEngine`)+ 结构性拒绝的 live 桩。
- 纯后端,无 console、无新数据(YAGNI;接缝上不上 console 是后续,不在本子项目)。
- 接缝**载荷承重**:`assert_dry_run_adapter` 守卫接进 `dry_run_pipeline.simulate_fills` 开头,运行时只允许
  干跑适配器过流水线(不是纯装饰)。
- 不选方案 B(重写干跑引擎、涟漪进流水线+测试、动最敏感代码)、不选方案 C(完整券商生命周期抽象、过度工程
  一个只有干跑一种实现的接缝)。

## 3. 现有真零件(复用,已探明接口)

- `services/trading/dry_run_adapters/domain/engine.py::DryRunAdapterEngine`:`process(cmd: DryRunCommand,
  existing_ids=None) -> DryRunAdapterRun`;`simulate_fill(rid: str) -> NormalizedReport | None`。无券商 SDK,
  `TestNoExecution` 钉死绝不真发。**本子项目零改动它。**
- `services/trading/dry_run_adapters/domain/models.py`:`DryRunCommand`(含 `dry_run=True`/
  `live_trading_allowed=False` 字段)、`DryRunAdapterRun`(含 `run_id`、`rejection_reasons`、`dry_run=True`)、
  `NormalizedReport`(含 `fill_price`/`fill_quantity`/`dry_run=True`)。
- `quantos/contracts/config.py`:`SafetyConfig`(5 flags,`assert_research_safe()`,`live_trading_armed`
  property = 仅当 live_trading_enabled ∧ real_broker_enabled ∧ allow_order_submission ∧ ¬allow_ai_direct_trading
  才 True)、`SafetyViolation(RuntimeError)`、`RESEARCH_SAFE_DEFAULTS = SafetyConfig()`(全安全默认)。
- `services/execution/dry_run_pipeline.py::simulate_fills(approved, prices, *, adapter, run_id, timestamp,
  cost_bps=0.0)`:当前 duck-typed 调 `adapter.process`/`adapter.simulate_fill`。**本子项目在其开头插一行守卫。**
- 治理:`tests/governance/test_crypto_data_boundary.py`(`services/` 无网络 import + fetcher 无 SDK)。

## 4. 组件与接口(新文件 `services/trading/broker_adapter.py`)

### 4.1 `BrokerAdapter`(Protocol,runtime_checkable)
```python
@runtime_checkable
class BrokerAdapter(Protocol):
    is_dry_run: bool
    def submit_order(self, cmd: DryRunCommand) -> DryRunAdapterRun: ...
    def fetch_fill(self, run_id: str) -> Optional[NormalizedReport]: ...
```
中性方法名定义"换适配器"的接缝。类型指向现有 `DryRunCommand`/`DryRunAdapterRun`/`NormalizedReport`——它们
就是当前流经流水线的规范订单/回报契约(`DryRun*` 前缀是历史名;真适配器消费同一套。重命名这些契约是**本
子项目非目标**)。

### 4.2 `DryRunBrokerAdapter`
```python
class DryRunBrokerAdapter:
    is_dry_run = True
    def __init__(self, engine: DryRunAdapterEngine | None = None):
        self._engine = engine or DryRunAdapterEngine()
    def submit_order(self, cmd): return self._engine.process(cmd)
    def fetch_fill(self, run_id): return self._engine.simulate_fill(run_id)
```
薄包装现有引擎,**零改动它**。满足 `BrokerAdapter`。

### 4.3 `LiveBrokerAdapter`(结构性拒绝的占位)
```python
class LiveBrokerAdapter:
    is_dry_run = False
    def __init__(self, safety_config: SafetyConfig):
        # 唯一构造前提:live 全armed —— L2 下 RESEARCH_SAFE_DEFAULTS 恒 False,故永远 raise。
        if not safety_config.live_trading_armed:
            raise SafetyViolation("LiveBrokerAdapter cannot be constructed at L2_DRY_RUN_ONLY "
                                  "(live_trading_armed is False); this is the intentional seam-refusal.")
        raise SafetyViolation("LiveBrokerAdapter is a non-functional seam placeholder — a real "
                              "broker implementation is post-L2, human-gated, and out of scope.")
    def submit_order(self, cmd): raise SafetyViolation("live order submission is disabled (L2)")
    def fetch_fill(self, run_id): raise SafetyViolation("live fill fetch is disabled (L2)")
```
**绝不 import 券商/网络 SDK。** 即便有人硬凑一个 live-armed SafetyConfig,第二个 raise 也挡死构造——它没有
任何真实现。这是"换适配器"那个有形、但结构上无法交易的目标槽位。

### 4.4 `assert_dry_run_adapter(adapter) -> None`(fail-closed 守卫)
```python
def assert_dry_run_adapter(adapter) -> None:
    if isinstance(adapter, LiveBrokerAdapter):        # 实际上到不了(构造已 raise),防御性
        raise SafetyViolation("live adapter is not permitted in the dry-run pipeline (L2)")
    if isinstance(adapter, (DryRunAdapterEngine, DryRunBrokerAdapter)):
        return
    if getattr(adapter, "is_dry_run", False) is True:  # 显式自证干跑的第三方也放行
        return
    raise SafetyViolation(f"unknown adapter {type(adapter).__name__} is not a proven dry-run adapter (fail-closed)")
```
接进 `dry_run_pipeline.simulate_fills` **第一行**:运行时只让证明为干跑的适配器过流水线,未知一律拒(fail-closed)。
诚实标注:这是运行时防误用的 chokepoint,**不是**对受信内进程代码的信任边界(那是子项目3 的进程隔离面)。

## 5. 数据流(不变,只多一道守卫)
干跑流水线的 `simulate_fills(approved, prices, *, adapter, ...)` → **`assert_dry_run_adapter(adapter)`(新,
fail-closed)** → `adapter.process/simulate_fill`(原样,经守卫确认是干跑)。其余流水线一字不改。

## 6. 容器化 / 安全边界(机器强制,不可让)

- `broker_adapter.py`:**无券商/CTP/FIX SDK import、无网络 import**(`services/` 无网络 import 机器强制 + 新
  治理测试显式钉)。
- `LiveBrokerAdapter` 在 L2 **无法构造**(`live_trading_armed` 恒 False → 第一 raise;即便绕过也第二 raise);
  **无任何真发单/真凭证/真连接代码**。
- 接缝是**加法**:不改五个 LIVE 标志、不改 `DryRunAdapterEngine`、不弱化 `PreTradeRiskGate`/`ExecutionBoundary`/
  `KillSwitch`;`dry_run_pipeline` 仍 fail-closed(且多一道守卫,更严)。
- `broker_adapter.py` 不进 `research_runner`(治理边界);核心确定性(无 RNG/墙钟)。
- 真适配器实现、人工批准、进程隔离 = 显式非目标(分别是子项目2/3 + post-L2)。

## 7. 测试(TDD,门绿)

- `tests/trading/test_broker_adapter.py`:
  - `DryRunBrokerAdapter` 满足 `BrokerAdapter`(`isinstance(a, BrokerAdapter)` 为真;`submit_order`/`fetch_fill`
    委托到引擎、返回值与直接调引擎一致)。
  - `LiveBrokerAdapter(RESEARCH_SAFE_DEFAULTS)` 构造 `raises SafetyViolation`(L2 拒绝);即便传 live-armed
    SafetyConfig 也 `raises SafetyViolation`(占位无实现);其方法(若绕过构造能拿到实例是不可能的,故用
    `LiveBrokerAdapter.submit_order` 的 unbound/monkeypatch 侧写或直接断言方法体 raise)——**简化:只测两条
    构造路径都 raise 即可**(方法不可达因为构造已死)。
  - `assert_dry_run_adapter`:放行 `DryRunAdapterEngine()`、`DryRunBrokerAdapter()`、`is_dry_run=True` 的鸭子;
    拒绝未知对象(`object()`)`raises SafetyViolation`。
- `tests/execution/test_dry_run_pipeline.py`(已存在):`simulate_fills` 传未知/非干跑适配器 → `raises
  SafetyViolation`(守卫生效);传 `DryRunAdapterEngine()`/`DryRunBrokerAdapter()` → 原样跑通(不回归)。
- 治理:`tests/governance/`(可扩已有 boundary 测试或新增):`broker_adapter.py` import 根 ⊆ stdlib+内部、
  无 `_FORBIDDEN_SDK`、无网络根;`LiveBrokerAdapter` 在 `RESEARCH_SAFE_DEFAULTS` 下构造 raise(机器钉死"L2 下
  live 适配器不可实例化")。
- `scripts/validate.py` 全绿。

## 8. 非目标(YAGNI)

- 不写任何真券商/CTP/FIX 实现;不 import 任何 SDK/网络;`LiveBrokerAdapter` 永远是拒绝桩。
- 不重命名 `DryRun*` 契约(接口直接引用它们)。
- 不接 console、不加新数据、不改前端。
- 不改五个 LIVE 标志、不动 `DryRunAdapterEngine`/门/kill-switch。
- 人工批准状态机(子项目2)、Phase 14 进程隔离(子项目3)、真适配器(post-L2)= 本子项目外。
