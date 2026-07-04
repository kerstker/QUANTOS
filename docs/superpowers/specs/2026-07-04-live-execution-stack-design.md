# Stage 1 设计:live/ 树 + Binance testnet 执行栈

> **背景:** Stage 0(真前向 paper-track)已 2026-07-04 落地(commit `e6ff3c8`),8 周证据钟已起走。
> 本文是 Stage 1——把执行栈接到**真交易所(先测试网,假钱零风险)**,为 Go/No-Go 后的真钱小资金
> 铺路。决策记录:`docs/2026-07-04-live-readiness-review-and-plan.md`。
> **诚实标注(2026-07-04 更新):** 首版写就时,Binance 签名请求格式/响应字段名依训练知识假设,
> 尚未验证。**同日已完成真实测试网联调**:`exchange_filters`/`account`/`positions`/
> `get_order`(不存在)四个只读调用 100% 对上真实响应;一次真实 `place_order`(BUY 0.002 BTC)
> 也成功,但**恰恰这次真实调用证伪了一个隐藏假设**——见 §7。

## 0. 2026-07-04 对抗式审查:发现 + 修复

真实测试网联调后,用 ultracode workflow 对 Stage 1 全部代码跑了一次 5 维度(数值缩放/fail-closed
边界/幂等重试/量化机构运维视角/真实响应假设)对抗式审查(每条发现经 3 个独立复核者反驳,≥2 票
"真实"才算确认)。24 条发现,15 条确认为真实缺陷。**已修复(本次提交内):**

- **[CRITICAL]** `submit_live_batch` 曾经只信 `place_order` 的**同步** ACK 来判断是否成交——
  真实联调证伪了这个假设(BUY 0.002 BTC 成功,但 ACK 回 `status=NEW, filled_qty=0.0`)。修复:
  提交后轮询 `venue.get_order()` 直到终态(`FILLED/CANCELED/REJECTED/EXPIRED`)或耗尽
  `max_confirm_polls`,用**确认后**的状态记账;耗尽仍未确认则诚实记录最后已知状态(不伪造
  FILLED,也不假装 0 仓位)。`poll_sleep` 由调用方注入(默认 no-op),真实驱动器必须传入真实
  `lambda: time.sleep(...)`——墙钟仍只在调用方边界,`live/pipeline.py` 本身不 import time。
- **[HIGH]** `place_order` 抛异常不代表订单没有抵达交易所(响应可能在真正送达前就丢了)。修复:
  异常后先用 `get_order` 回查权威状态,能找到就当恢复成功处理(审计记 
  `LIVE_ORDER_RECOVERED_AFTER_SUBMIT_ERROR`);两边都查不到才是真失败。
- **[HIGH]** 记账(ledger/audit)失败不再让一笔真实成功的订单被误记成"提交失败"——venue 侧
  确认成功后,记账失败单独记进新增的 `LiveBatchResult.ledger_write_errors`,订单仍计入
  `submitted`(否则调用方可能误判"没下单"而重复下单)。
- **[MEDIUM]** `venue.exchange_filters()` 调用补上 try/except,失败时 fail-closed 返回
  `NEEDS_HUMAN_REVIEW` + 审计记录,而不是让异常直接崩溃传播。
- **[MEDIUM]** `BinanceFuturesAdapter.positions()` 修复 Hedge Mode(双向持仓)下同一标的
  LONG/SHORT 两条记录互相覆盖、静默丢失一侧净敞口的问题——改成按标的累加净数量,entry_price
  取绝对数量更大那条腿的入场价(没有单一"入场价"的良定义)。
- **[LOW]** HTTP 响应体解析补上 JSON 异常兜底,非 JSON body(网关故障页等)统一成清晰的
  `RuntimeError` 而不是未处理的 `json.JSONDecodeError`。

**记录在案、暂不修复(不是本轮范围,留给接线真实驱动器/调度器时处理):**

- 单元测试的 fake venue(`tests/live/test_pipeline.py::_FakeVenue`)已改为**模拟真实异步行为**
  (NEW ACK → 轮询后 FILLED),不再是旧版本"同步即终态"的错误假设——这正是旧假设能在
  "71 tests 全绿"下溜走的根本原因,现在测试本身就会抓住任何回归。
- `RiskLimits` 里"裸数量"类阈值(`fat_finger_qty`/`default_max_position_qty`/参与率检查)若
  真实接线,必须按同一个 `UNIT_SCALE` 折算,否则真实小额仓位可能被误判超限、或运维为"消除莫名
  拒绝"而随手放宽阈值削弱风控——本文件测试固定用刻意放到极大的 `RiskLimits` 规避,不代表已解决,
  真实驱动器接线时需要显式处理。
- `live/execution/reconciliation.py::reconcile_positions` 目前**没有任何生产调用方**——没有
  类似 `tools/forward_track_advance.py` 的调度驱动器会去调用它,drift 目前不会被任何代码路径
  发现。这不是这几个文件本身的 bug,而是"生产驱动器/调度器"这一层根本还不存在(Stage 1 只建了
  执行栈的各个部件,不含调度)。
- API 限流(429/418)感知/退避/熔断、时钟偏移校正(`recvWindow` 硬编码 5000ms)、崩溃恢复(启动时
  核对上一批次状态未知的 `client_order_id`)、确认送达的告警通道——这些是"能安全无人值守定时运行"
  需要的运维硬化,在真正写出调度驱动器之前都不适用,留作那时的工作范围。
- "1000X 前缀"合约(如 1000SHIBUSDT)命名不匹配问题——当前 14 个大币标的不含此类合约,暂不影响
  crowd_contra 上线,但会在宇宙扩展时炸掉,已记录。

## 1. 与现有安全边界的关系(核心设计决策)

### 1.1 SafetyConfig 五旗不挪用、不稀释
`live_trading_enabled`/`real_broker_enabled`/`allow_order_submission`/`allow_ai_direct_trading`/
`require_human_approval` 在 blueprint §1.2 里指的是**真钱/真券商**。测试网是交易所官方提供的
沙盒(零真实资本、独立账户体系),**不属于这五旗治理的对象**——用不真钱这件事本身就是安全边界,
不需要也不应该借用"real_broker_enabled"这类字面意思是"真钱"的旗子去描述它,否则未来读代码
的人会误以为"这五旗全 False 时系统绝对不可能对外发任何请求",而实际上测试网请求在流动。
**诚实的做法是把测试网许可与真钱许可完全分开建模**,而不是把测试网塞进真钱的旗子体系里
造成语义混淆。

### 1.2 mainnet 与 `LiveBrokerAdapter` 同款结构性拒绝
交易所客户端(`BinanceFuturesAdapter`)**自身**做一层结构性拒绝,与 `services/trading/
broker_adapter.py::LiveBrokerAdapter` 对称:
- 只允许 `base_url ∈ {TESTNET_BASE, MAINNET_BASE}`,其它值直接拒;
- 用 `MAINNET_BASE` 构造时,若 `not safety_config.live_trading_armed`(L2 默认恒 False)→
  `raise SafetyViolation`——即便被强凑非默认 `SafetyConfig`,`assert_research_safe()` 风格的
  多重检查仍然只在 Phase 3A Go/No-Go 之后才可能为真(那需要人工修改 SafetyConfig 的构造参数,
  这本身就是本文档不覆盖的、post-Stage-1-gate 的人工操作)。
- 用 `TESTNET_BASE` 构造:**不检查 SafetyConfig**——测试网许可来自"是否提供了测试网 API key"
  这个自然前提(没 key 就连不上),不是这五旗的管辖范围(见 §1.1)。

### 1.3 "AI 不自主交易"在测试网语境下的落地:分级 mandate,而非自由执行
即使是假钱,也不该由 AI 在运行时凭空决定"现在提交这批订单"。所以引入一个**人工签署的 mandate**
(硬编码限额 + 允许清单 + 人工签署人 id + 过期时间),作为 `PreTradeRiskGate` 批准之后的**第二层**:
- gate 批准的意图,再过 mandate 分级:限额内 → `AUTO`(机械执行,零人工决策空间,像预先设好的
  止损单);超限/mandate 缺失或过期/任何评估异常 → `NEEDS_HUMAN_REVIEW`(**不提交**,留存待批)。
- mandate **fail-closed 到底**:没有有效 mandate 文件时,一切意图恒 `NEEDS_HUMAN_REVIEW`——
  AI 不能凭空发明一个"看起来合理"的限额来自我授权。
- 本阶段(Stage 1)只建能力 + 单元测试(注入假 HTTP transport,零真网络);**真正对着用户的
  测试网账户跑一次的"联调烟测"是后续显式步骤**,由用户提供测试网 key 后触发,不在本次自动完成
  的工作范围内——这是对"AI 不自主交易"精神的延伸,不因为是假钱就放松。

## 2. 目录结构

```
live/
  __init__.py
  contracts.py            — VenueOrderResult / VenuePosition / ExchangeFilters(值类型)
  exchange/
    __init__.py
    base.py                — VenueAdapter Protocol(venue-agnostic,Bybit/Hyperliquid 后续实现同协议)
    binance_futures.py      — BinanceFuturesAdapter(签名 REST,纯 stdlib)
  execution/
    __init__.py
    filters.py               — 交易所过滤器(步长/最小名义额)+ 确定性 client_order_id
    position_ledger.py       — LiveLedger(SQLite append-only 本地仓位账本)
    reconciliation.py        — reconcile_positions(纯函数,只报告 drift 不自动修正)
  governance/
    __init__.py
    mandate.py                — Mandate 值类型 + evaluate_mandate(fail-closed 分级)
  pipeline.py                  — submit_live_batch:决策→intents→gate→过滤器→mandate→venue→ledger→审计
```

## 3. 组件设计

### 3.1 `live/contracts.py`
**两级下单类型,刻意不合并**(实现中发现的真实数量级问题,非过度设计):既有
`OrderIntent.quantity: int`(`quantos.contracts.trading`)是为研究管线的整数手数假设设计的——
直接复用能拿到 `PreTradeRiskGate` 的 9 引擎 fail-closed 风控,但小资金真实分数仓位(如 0.015
BTC)不能被迫凑整数手数(3k-5k 美元组合里 BTC 若按整币计价会整仓错配几个数量级)。所以:
- **风控门货币**:复用 `OrderIntent`(int)不重造——`live/pipeline.py` 用缩放整数喂 gate;
- **venue 提交货币**:新增 `LiveOrderRequest(instrument_id, side, quantity: float,
  client_order_id)`——`apply_exchange_filters` 之后的产物,一路浮点,不再回整数手数。

新增 venue-agnostic 值类型(frozen dataclass,与 `RiskLimits` 一致的轻量风格):
- `VenueOrderResult(venue_order_id, client_order_id, instrument_id, status, filled_qty, avg_price, raw)`
- `VenuePosition(instrument_id, quantity, entry_price)`
- `ExchangeFilters(instrument_id, step_size, min_qty, min_notional, tick_size)`
- `LiveOrderRequest(instrument_id, side, quantity, client_order_id)`——见上,恒 MARKET(类型本身
  排除 LIMIT 的歧义,不需要运行时检查)。

### 3.2 `live/exchange/base.py` — `VenueAdapter` Protocol
```python
class VenueAdapter(Protocol):
    venue_mode: str            # "testnet" | "mainnet"
    def place_order(self, order: LiveOrderRequest) -> VenueOrderResult: ...
    def get_order(self, instrument_id, client_order_id) -> Optional[VenueOrderResult]: ...
    def account(self) -> dict: ...                     # raw venue balances(诊断用,不承诺形状)
    def positions(self) -> dict[str, VenuePosition]: ...  # 规范化仓位(reconcile_positions 的输入)
    def exchange_filters(self) -> dict[str, ExchangeFilters]: ...
```
`positions()` 是 venue-agnostic 的规范化仓位读取——每个 venue 自己知道怎么把原始账户 JSON
折算成 `{instrument_id: VenuePosition}`(如 Binance 把 `"BTCUSDT"` 折成不带后缀的 `"BTC"`,
与仓库既有 ticker 惯例一致)。`account()` 保留原始形状作诊断逃生舱(如可用余额),不用于对账。
`Bybit`/`Hyperliquid` 后续实现同一协议(用户已拍板"venue 可插拔",本阶段只做 Binance)。

### 3.3 `live/exchange/binance_futures.py`
纯 stdlib(`urllib.request`/`hmac`/`hashlib`/`json`/`time`)。可注入 `http_request` 回调
(生产默认 urllib 实现;测试注入假 transport,记录调用、返回罐头 JSON——零真网络)。

签名机制(USDT-M 合约,公开文档规格):查询串按插入顺序拼接(不需重排)、`timestamp`(ms)必带、
`HMAC-SHA256(secret, query_string)` 十六进制签名追加为 `&signature=`,请求头 `X-MBX-APIKEY`。

四个方法,不多不少:
- `exchange_info() -> dict`:`GET /fapi/v1/exchangeInfo`(公开,不签名)——solve 步长/最小名义额;
- `account() -> dict`:`GET /fapi/v2/account`(签名);
- `place_order(intent, *, client_order_id) -> VenueOrderResult`:`POST /fapi/v1/order`(签名,
  `type=MARKET`);
- `get_order(instrument_id, client_order_id) -> Optional[VenueOrderResult]`:`GET /fapi/v1/order`
  (签名)。

**绝不实现**:提幣(`withdraw`)、划转(`transfer`)、杠杆/保证金模式切换——不是本阶段范围,
且治理测试钉死这些调用字面量不得出现。

### 3.4 `live/execution/filters.py`
- `client_order_id(strategy_id, decided_ts, instrument_id) -> str`:确定性哈希(镜像
  `dry_run_pipeline._det_id` 的模式),截断进 Binance 36 字符限制——同一决策重试产生同一 ID,
  交易所天然去重,不会重复下单。
- `round_to_step(qty, step_size) -> float`;`apply_exchange_filters(intents, filters) ->
  (adjusted, dropped)`:步长取整、低于最小名义额或取整后为零 → fail-soft 丢进 `dropped`(附原因),
  绝不因单个过滤失败中断整批。

### 3.5 `live/execution/position_ledger.py` + `reconciliation.py`
`LiveLedger`(SQLite,镜像 `ForwardLedger`/`PaperTrackStore` 的 append-only 模式):
- `live_orders(client_order_id PK, instrument_id, side, quantity, order_type, submitted_at,
  venue_order_id, status)`;
- `live_fills(fill_id PK, client_order_id, instrument_id, quantity, price, filled_at)`;
- `position(instrument_id) -> int`(折叠成交);`all_positions() -> dict`。

`reconcile_positions(ledger_positions, venue_positions, *, tolerance=0) -> dict`:纯函数,
只报告 `{"drift": {...}, "in_sync": bool, "max_abs_drift": float}`——**不自动修正**,drift
是留给人/ops 流程处理的信号,不是本层的决策权。

### 3.6 `live/governance/mandate.py`
```python
@dataclass(frozen=True)
class Mandate:
    mandate_id: str
    venue: str                              # 必须与实际 venue_mode 匹配,否则 fail-closed
    allowed_instruments: frozenset[str]      # 空集 = fail-closed 拒绝一切(同 RiskLimits 惯例)
    max_single_order_notional: float
    max_gross_notional: float
    max_net_notional: float
    per_instrument_max_notional: tuple[tuple[str, float], ...] = ()
    approved_by: tuple[str, ...] = ()        # 签署 mandate 的人工 actor_id(留痕,非本阶段强制人数)
    issued_at: int = 0
    expires_at: Optional[int] = None
```
`evaluate_mandate(intents, prices, mandate, *, account_state, now) -> MandateDecision`:
`MandateDecision(tier: Literal["AUTO","NEEDS_HUMAN_REVIEW"], reasons: tuple[str,...])`。
fail-closed 规则(任一触发 → `NEEDS_HUMAN_REVIEW`,绝不默认 `AUTO`):mandate 为 `None`;
`now > expires_at`;`venue != mandate.venue`;任一 instrument 不在允许清单;单笔/总 gross/
总 net 名义额超限;评估过程任何异常(外层 try/except 兜底,镜像 `PreTradeRiskGate.evaluate`
的"异常即拒"哲学,只是这里"拒"= 升级人工而非直接否决——两者都不是自动执行)。

**与 `PreTradeRiskGate` 的关系:互补,不重叠。** gate 是风控**权限**(第7层"风险与策略分离"
原则的风险侧);mandate 是"这批已获风控许可的意图,机械执行需要多少人工在场"的**分级**——
只有 gate 已批准的意图才会进入 mandate 评估,mandate 从不越过 gate 直接放行任何东西。

### 3.7 `live/pipeline.py`
```python
def submit_live_batch(*, intents, prices, gate, ctx_factory, venue, mandate, ledger,
                      audit, account_state, now) -> LiveBatchResult
```
流程(全部复用既有组件,零重造风控逻辑):
1. `services.execution.dry_run_pipeline.screen_intents(intents, gate=gate, ctx_factory=ctx_factory)`
   ——原样复用,gate/kill-switch 是唯一的风险权限来源;
2. 已批准意图 → `apply_exchange_filters`(3.4)→ 过滤后意图;
3. `evaluate_mandate`(3.6)→ 若 `NEEDS_HUMAN_REVIEW`:写入待批记录(`live_pending_approval` 表
   或简单返回给调用方),**不提交任何订单**,函数在此返回;
4. 若 `AUTO`:逐笔 `venue.place_order(intent, client_order_id=...)` → `ledger.record_submission`
   → `audit.append(...)`(镜像 `run_dry_run_pipeline` 的审计模式,`GlobalAuditLog` 原样复用)。
5. 任何 venue 调用异常:fail-soft 记录该笔失败,不影响其它笔,不重试(重试是运维步骤,非本函数职责)。

## 4. 治理边界(新增,不弱化任何既有不变量)

`tests/governance/test_live_execution_boundary.py`(镜像 `test_broker_adapter_boundary.py`/
`test_signing_boundary.py` 的 AST 检查风格,应用到整棵 `live/` 树):
1. 仅 `live/exchange/binance_futures.py` 可含网络 import(`urllib`等);`live/` 其余文件一律不许;
2. `live/` 全树无交易所/券商 SDK import(`_FORBIDDEN_SDK`,同 crypto_data_boundary 那份清单);
3. `live/` 全树源码不含提幣/划转调用字面量(`_FORBIDDEN_TRADE_CALLS` 的 withdraw/transfer 子集;
   注意 `place_order`/`submit_order` 本身是本树的**正当职责**,不在禁止清单里——这与 `tools/`
   抓取器的边界不同,那边连下单都不许,这边下单是设计意图,提幣/划转才是红线);
4. `BinanceFuturesAdapter(base_url=MAINNET_BASE, safety_config=RESEARCH_SAFE_DEFAULTS)` 必须
   `raise SafetyViolation`(镜像 `test_live_broker_adapter_uninstantiable_at_l2`);
5. 源码无硬编码 API key/secret 字面量(镜像 `test_signing_has_no_embedded_key_material` 的正则);
6. `services/` 树任何文件不得 import `live/`(单向依赖:`live/` 依赖 `services/`,反向禁止——
   保持研究核心不知道执行栈存在,执行栈是消费者不是被消费者);
7. `evaluate_mandate` 在 mandate=None / 过期 / venue 不匹配时,对任意非空 intents 恒返回
   `NEEDS_HUMAN_REVIEW`(fail-closed 冒烟,不是完整分支覆盖——完整覆盖在 `tests/live/` 单元测试)。

## 5. 测试策略(先行)

`tests/live/test_binance_futures_adapter.py`:注入假 `http_request`,验证签名串构造、
testnet/mainnet 构造分支、四个方法的请求/解析往返、无 key 时的失败模式。
`tests/live/test_filters.py`:步长取整、最小名义额丢弃、client_order_id 确定性+长度。
`tests/live/test_position_ledger.py`:提交/成交记录、position 折叠、幂等。
`tests/live/test_reconciliation.py`:drift 计算、in_sync 判定。
`tests/live/test_mandate.py`:AUTO/NEEDS_HUMAN_REVIEW 各分支、fail-closed 默认。
`tests/live/test_pipeline.py`:端到端(fake venue),验证 AUTO 提交、NEEDS_REVIEW 不提交、
gate 拒绝的意图不到 mandate 这一层。

## 6. 非目标(本阶段明确不做)

真实网络联调(等用户测试网 key);Bybit/Hyperliquid 具体实现(协议已留口子);撤单/改单;
杠杆/保证金模式配置;真钱路径的任何代码分支(mainnet 恒结构性拒绝);console 页面(读接口后补);
mandate 文件的具体数字签署(那是用户在 Go/No-Go 前的动作,不是本阶段代码要填的默认值)。

## 7. 2026-07-04 补:调度驱动器落地(对抗式审查发现 #7/#11 的答案)

`live/governance/mandate_store.py::load_mandate`(mandate 文件加载器,fail-closed,数字由用户
自己签署)+ `live/execution/reconciliation.py::preflight_check`(drift 超容忍度硬停)+
`tools/live_track_advance.py`(周度驱动器,与 Stage 0 前向跟踪同一条 crowd_contra 信号路径)——
这是 `submit_live_batch`/`reconcile_positions` 第一次有真实生产调用方。

**默认 preview 模式**(唯读方法透传真实 venue,`place_order` 只记录不真提交);`--submit` 才可能
真正下单,但没有用户签署的 `key/live_mandate.json` 时 `evaluate_mandate` 仍 fail-closed 到
`NEEDS_HUMAN_REVIEW`,零提交——两层保护(模式 + mandate)独立生效。

**gate 限额的正确解法(回答审查发现 #1):** 不是把 `RiskLimits` 的裸数量阈值(`fat_finger_qty`/
`default_max_position_qty`)也乘 `UNIT_SCALE`(容易算错、也容易被"消除莫名拒绝"的冲动放宽)——
而是认识到这些**不感知价格**的阈值对多资产加密货币 book 本来就不是正确的工具(不同币单价差
好几个数量级,统一的"最大数量"没有意义),把它们设成有效无限,把真正的保护完全交给**天然价格
感知、与 `UNIT_SCALE` 无关**的名义额类检查(`max_order_notional`/`max_gross_exposure`/
`max_net_exposure`/`daily_loss_limit`)。

**真实联调验证:** 用真实测试网账户跑了一次 preview(`--no-refresh`),对账前置检查在**真实数据**
上正确拦下了一次真实 drift(本地账本是新建的空账本,交易所侧还留着更早手动烟测下的 0.002 BTC
仓位)——手动核对补记后(把那笔已知的真实成交写回账本,不是新动作)重跑,全链路正常跑通:
真实 AUM(`$4993.81`,交易所侧可用余额)、真实 gate 评估、mandate 缺失 → 正确 fail-closed 到
`NEEDS_HUMAN_REVIEW`、零提交。

**观察到但未深究(不是本次范围):** `tools/live_track_advance.py::_latest_weights_and_prices`
(与 Stage 0 `forward_track_advance.py` 同一条 `build_panel`/`CROWD_CONTRA`/`_legs` 调用链)
在几次相邻的独立进程调用之间,产出的信号权重从"1 个标的"变成"空字典"——`decided_ts`(panel 最新
event_time)保持不变,复跑三次确认非 hash-seed 随机性(每次都是空)。根因未查(不影响本次交付
的正确性:无论生成几笔意图,没有 mandate 签署就是零提交,driver 两种情况下都安全);这是
**既有** crowd_contra 信号计算链的特性,不是本次新增代码引入的问题,值得独立排查但不阻塞
Stage 1 交付。

## 8. 非目标(本次仍不做)

API 限流/退避/熔断;时钟偏移校正;崩溃恢复(核对上一批次状态未知的 client_order_id);
确认送达的告警通道;定时任务本身的注册(用户人工执行,同 Stage 0 的 schtasks 模式)。
仓位/回撤监控的**减仓下单**部分——见 §9,故意留了一个未接线的架构决定点。

## 9. 2026-07-04 补:仓位/回撤监控(分两级,只做了安全的那一级)

`live/execution/risk_monitor.py`,分两级(与 `mandate.py` 分级授权同一哲学——安全响应可以
自动,新的下单触发必须由人另外签署才激活):

**① `evaluate_risk_state` + `respond_to_breach`(已接线、全自动、零新风险):** 纯函数算真实
权益相对历史峰值的回撤 + 真实总/净敞口,越限即 `arm` 既有 `KillSwitch`(non-human SYSTEM
actor,`services/risk/pre_trade/kill_switch.py` 本来就允许**任何** actor 触发——halt 只会让
系统更保守,不引入任何新风险)。fail-closed:某标的缺价导致无法估值本身就算一次越限(不是
静默跳过)。已接进 `tools/live_track_advance.py`(对账通过后、下单前的第二道前置检查,同
对账硬停的结构)。峰值权益持久化在 `data/cache/live_risk_state.json`(首次运行以当前权益
为起点)。真实联调:阈值默认工程保守值(回撤 20%、gross ≤2×AUM、net ≤0.3×AUM),对着测试网
真实账户跑通,当前 0.002 BTC 测试仓位远在阈值内,零误报。

**② `compute_flatten_orders`(纯计算,故意没有接自动提交):** 给定当前仓位 + 目标 gross
上限,算出等比例缩减各腿的 REDUCE-ONLY 数量(每条腿只朝 0 缩放,绝不反手、绝不新增仓位)——
已测试(15 tests),返回形状与 `apply_exchange_filters` 的 `desired` 参数一致,可以直接喂给
既有交易所过滤器。**没有接自动提交的原因是一个真实的架构冲突,不是偷懒:**
`services/risk/pre_trade/checks.py::check_kill_switch` 一旦 `KillSwitch` 被触发,会挡下
**所有**意图,包括 reduce-only——也就是说,①越限触发 kill switch 之后,如果想让②的减仓单
经过同一个 `PreTradeRiskGate` 送出去,会被自己刚触发的同一个 kill switch 挡下,越是紧急、
越送不出减仓单。要解决这个矛盾,只有两条路,都是需要人工明确拍板的架构决定,不是代码层面
可以自己悄悄选一个的:
- (a) 修改共用的 `check_kill_switch`,让它认识"这是 reduce-only、只减不增"这个语义、豁免它
  ——但 `services/risk/pre_trade/` 是研究/纸面/实盘所有路径共用的核心风控引擎,改它的通用
  检查逻辑影响面很大,需要谨慎评审;
- (b) 给"紧急减仓"开一条不经过共用 `PreTradeRiskGate` 的独立安全路径(自己的最小化检查:
  只减不增、有目标上限、有人工签署的授权)——架构上更干净,但是一个新的、平行于主执行路径
  的组件,同样需要独立设计。

在这个决定之前,`compute_flatten_orders` 保持"测试好、能用、没上线"的状态——真实世界里,
①已经把系统halt 了,人工介入后可以用这个函数手算该减多少仓、再用既有的
`submit_live_batch`(mandate 里临时松绑 kill switch 状态或走人工审批的多方批准流程)执行,
不是完全没有退路,只是不自动。

## 10. 2026-07-04 补:Bybit venue adapter(venue 可插拔的第二个实现)

`live/exchange/bybit_futures.py`,V5 统一账户(USDT 永续,`linear` category),满足同一
`VenueAdapter` Protocol。与 `binance_futures.py` 底层协议的真实差异(2026-07-04 研究 Bybit V5
公开文档得出,**尚未对真测试网响应验证**——首次真实联调等用户提供 Bybit 测试网 API key):

1. **签名在 header,不在 query/body**:`sign_str = timestamp + api_key + recv_window + query_or_body`,
   HMAC-SHA256 十六进制放进 `X-BAPI-SIGN` header(Binance 是把签名串追加进 query string)。
2. **统一响应信封** `{retCode, retMsg, result, time}`,`retCode==0` 才是成功——`_unwrap()` 统一校验,
   非 2xx HTTP 或非零 retCode 都 fail-closed 成 `RuntimeError`。
3. **Hedge Mode 用 `positionIdx` 字段**(0=one-way/1=多头腿/2=空头腿),不是 Binance 那样在
   `positions` 数组里出现两条独立记录区分。本系统假设 one-way(下单固定 `positionIdx=0`),但
   `positions()` 依然按标的**累加净数量**做防御——镜像 2026-07-04 对抗式审查发现 #13(Binance
   hedge-mode 覆盖 bug)的同一修复姿态,防止账户意外处于 hedge mode 时静默丢失一侧敞口。
4. **订单查询分两个端点**:`/v5/order/realtime`(未结/部分成交)与 `/v5/order/history`(已终态)——
   不像 Binance 一个 `/fapi/v1/order` 通吃。`get_order` 先查 realtime、找不到再查 history,两边都空
   才是真正"不存在"。这个假设**未经真实验证**,首次联调需要重点检查。
5. **一开始就走轮询确认**:`place_order` 的响应本身不带成交状态字段(`status="NEW"` 是占位),
   `submit_live_batch` 现有的"提交后轮询 `get_order` 直到终态"逻辑天然适用——这次没有重蹈
   Binance 最初"同步 ACK = 最终成交"的覆辙,直接把上次审查的教训用在了新 adapter 的设计起点上。

`live/execution/filters.py`/`live/pipeline.py`/`live/governance/mandate.py` 零改动即可搭配
`BybitFuturesAdapter`(它们只依赖 `VenueAdapter` Protocol,不知道具体是哪家交易所)。治理边界测试
(`tests/governance/test_live_execution_boundary.py`)已扩展为对两个 venue adapter 文件同时检查
(mainnet 结构性拒绝、无 SDK、无提幣/划转字面量)。**22 个新单元测试 + 2 个 Protocol 一致性测试,
全绿**。

## 11. 2026-07-04 补:Hyperliquid ——需要人工拍板的依赖决定(不建假实现)

研究 Hyperliquid API(鏈上永续 DEX)公开文档发现一个**架构性质根本不同**的事实:Hyperliquid
**没有 API key/secret**,下单等"L1 动作"需要用**以太坊私钥**签名——先把 action 用 **msgpack**
序列化、算 connection_id(**Keccak-256** 哈希,不是 Python stdlib `hashlib` 的标准 SHA-3,两者输出
不同)构造出一个 phantom agent,再用 **secp256k1 ECDSA** 对它签名。这需要 `msgpack` +
Keccak-256 库 + secp256k1 签名库(如 `eth_account`/`eth_keys`/`coincurve`)——**全部都不是 Python
stdlib**,与 `live/exchange/{binance,bybit}_futures.py`"纯 stdlib、零第三方依赖"的既有纪律直接冲突。

**为什么不现在就建:**
- 手刻 Keccak-256 + secp256k1 签名(避免引入依赖)工程量不小、容易出安全性错误(签名算法实现
  bug 可能导致私钥泄露或产生看似有效实则错误的签名)——**不建议**;
- 引入 `eth_account` 这类依赖,意味着**这个进程需要持有一把以太坊私钥**——这与 QUANTOS 现有
  "研究进程不碰真实密钥材料"的安全姿态(参见 `services/governance/signing.py` 的出进程签名权威
  设计)性质上更接近,需要认真评估密钥怎么存、谁能访问、要不要走类似的隔离设计,而不是简单地
  `pip install eth_account` 完事;
- 建一个「看起来能跑但签名是假的/占位的」半成品 adapter,比不建更危险——它会让人误以为
  Hyperliquid 已经就绪,实际上一旦真的调用签名会失败或(更糟)产生错误但被接受的签名。

**结论:** `live/exchange/hyperliquid.py` 暂不实现。若要推进,需要人工先决定:(a) 是否批准引入
`eth_account`(或等价库)作为新依赖;(b) 私钥材料如何存储/隔离(是否要等 Phase 14 出进程签名权威
落地后才接入 Hyperliquid,而不是在研究进程里直接持有以太坊私钥)。`VenueAdapter` Protocol 已经
是 venue-agnostic 的,`live/execution/filters.py`/`live/pipeline.py`/`live/governance/mandate.py`
零改动即可支持 Hyperliquid——真正的空缺只在 `live/exchange/hyperliquid.py` 这一个文件,决定做出后
随时可以补。

## 12. 2026-07-04 补:限流退避 + 时钟偏移校正(运维硬化)

回答 2026-07-04 对抗式审查记录在案的两个运维缺口:

**限流感知退避** `live/exchange/retry.py::call_with_backoff`——纯函数骨架(`sleep` 可注入,
测试零真延迟),两个 venue adapter 各自定义自己的 `is_rate_limited` 判定:Binance 是 HTTP
429/418;Bybit 是 HTTP 429 或业务错误码 `retCode==10006`("Too many visits!")。指数退避
(`base_delay * 2^attempt`),耗尽仍限流 → `RateLimitedError`(区别于普通 `RuntimeError`,
调用方/未来驱动器可以据此识别"这是限流,该退避/告警",而不是当成随机的提交错误)。**签名
每次重试都重新计算**(含 timestamp)——退避可能跨越数秒,复用旧签名有落在 `recvWindow`/
`recv_window` 之外的风险。

**时钟偏移校正** `sync_clock()`(两个 adapter 都有,同款语义):拉取交易所公开的服务器时间
端点(Binance `/fapi/v1/time`,Bybit `/v5/market/time`),计算本机时钟相对服务器的偏移(ms),
之后所有签名请求的 timestamp 都加上这个偏移。Fail-soft(查询失败保留旧偏移,默认 0,不打断
流程)——不调用 `sync_clock()` 则行为与硬化前完全一致(向后兼容,现有测试/调用方零改动)。
真实驱动器接线时应该在每轮开始前调用一次 `sync_clock()`。

两处硬化都是纯粹的"运维稳健性"改进,不改变任何 fail-closed 安全语义——限流退避耗尽依然是
`raise`(不会静默放行),时钟校正失败依然回落到未校正状态(不会假装校正成功)。**live/ 全套测试
从 149 增至 166(Binance +6/Bybit +6/retry 独立模块 +5),全绿。**
