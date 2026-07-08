# LIMIT 限价单执行能力(战线②)——设计与落地记录

日期:2026-07-06 · readiness 不变:**L2_DRY_RUN_ONLY**(测试网/Demo 零真实资本)
状态:**已落地(默认不启用)**——`live/` + `tools/` + `tests/` 全量改动,`services/`/`quantos/`
零接触;经对抗式批判(`NEEDS_CHANGES` → 8 条 `required_changes` 全部处理,见 §8 对照表)。

## 0. 核心裁定(一句话)

**默认路径字节不变,LIMIT 显式 CLI 开启。** 所有新字段带默认值
(`order_type="MARKET"`、`limit_price=None`、`post_only=False`,`submit_live_batch` 新参数
`limit_pricing=None`);不传时两所适配器发出的 HTTP 请求形状、`client_order_id` 哈希、驱动器
默认行为与改动前逐字节相同(回归锁测试钉死)。本设计的最小可用版是**可成交限价
(marketable limit)= 市价单加价格上限**,不是做市/吃返佣;紧急减仓通道(`emergency.py`)
恒 MARKET,治理测试源码级钉死绝不构造 LIMIT。

## 1. 契约扩展(live/contracts.py)

`LiveOrderRequest` 加三个默认字段:`order_type: str = "MARKET"`、
`limit_price: Optional[float] = None`、`post_only: bool = False`。`__post_init__` 构造期
fail-closed 校验:

- `order_type` 必须是 `"MARKET"`/`"LIMIT"` 之一;
- `LIMIT` 必须带 finite ∧ >0 的 `limit_price`;
- `MARKET` 不得携带 `limit_price`/`post_only`(纵深防御第一层——非法组合根本造不出来)。

## 2. 定价模块(live/execution/pricing.py,纯函数、无网络/墙钟)

```python
@dataclass(frozen=True)
class LimitPricing:
    band_pct: float = 0.002                    # 工程默认 20bps,非业务风险数字
    post_only: bool = False
    anchor_max_deviation_pct: float = 0.05      # 工程占位容忍度,非业务风险容忍度
```

- `round_price_to_tick(price, tick_size, side)`:BUY 向下取整、SELL 向上取整,±1e-9 epsilon
  吸收浮点噪声。
- `resolve_anchor(book, *, decision_price, max_deviation_pct)`:**批判必须修改①的核心落点**
  ——不只防 `book_ticker` 网络失败,还防"成功但退化"的响应(交叉盘 ask<bid、零价/负价、
  与决策价偏离超容忍度的"天价 mid")。任何一项不过 -> 回退决策价锚(不是直接判失败:决策价
  本身仍是可信备用锚);决策价也不可用 -> `(None, "no_anchor")`。三个用例(交叉盘/零价/
  天价 mid)+ 容忍度边界均有测试(`tests/live/test_pricing.py`)。
- `limit_price_for(side, *, anchor, band_pct, tick_size)`:`anchor×(1±band)` 后按方向取整。
- `to_limit_orders(adjusted, *, anchors, filters, pricing, strategy_id, decided_ts)`:MARKET
  请求 -> LIMIT 请求,逐笔 fail-soft——anchor 缺失/定价失败的腿并入 `dropped`
  (`reason="no_limit_anchor"`),**绝不静默回退 MARKET**(用户显式要了价格上限,悄悄摘掉
  它是欺骗);成功的腿重算 `client_order_id`(限价参与订单身份,见 §4)。

anchor 来源顺序(装配在 `live/pipeline.py`,pricing 本身不碰网络):
① `venue.book_ticker()` 的 `(bid+ask)/2`(首选);② 网络失败/结构退化/偏离过大 -> 决策价
(fail-soft + 告警审计 `LIVE_LIMIT_ANCHOR_FALLBACK`)。

## 3. 适配器(两所)

`place_order` 的 `params`/`body` 构造从硬编码 `"MARKET"`/`"Market"` 改为按
`order.order_type` 分支;`order_type=="LIMIT"` 时追加 `price`(与决策价/成交量同款十进制
格式化,绝不科学计数法)+ `timeInForce`(Binance `GTX`/`GTC`,Bybit `PostOnly`/`GTC`)。
**MARKET 分支产出的请求形状与改动前逐键相等**(`test_*_market_request_shape_byte_identical`
钉死)。价格已在 `pricing.py` 按 tick 取整,适配器只格式化不再取整。

新增只读 `book_ticker(instrument_id) -> Optional[dict]`(不签名,`{"bid": float, "ask":
float}`;解析失败/非 2xx/非 JSON -> `None`,fail-soft)。`VenueAdapter` Protocol 同步加入这个
必需成员——**批判必须修改**:`tests/live/test_venue_base.py::_FakeVenue` 与
`tools/live_track_advance.py::PreviewVenue` 都同步补上(前者是 `isinstance` 协议一致性测试的
主体,后者是只读方法透传真实 venue 的既有惯例)。`live/pipeline.py` 侧仍用
`getattr(venue, "book_ticker", None)` 探测(与 `cancel_order` 的优雅降级同款),旧
fake/包装器不炸。

## 4. client_order_id 幂等语义(live/execution/filters.py)

订单身份 = 决策 + 方向 + 数量 (+ 限价)。`client_order_id(..., limit_price: Optional[float] =
None)`:`limit_price=None`(默认)时输出与改动前**逐字节相同**(golden 测试钉死);带限价时
`ident` 追加 `"|L{limit_price:.10f}"`——同一决策同数量但不同限价是不同订单(改价重发不能被
交易所当重试去重),同限价重试仍幂等。

## 5. 流水线(live/pipeline.py)

`submit_live_batch(..., limit_pricing: Optional[LimitPricing] = None, limit_max_confirm_polls:
int = 30)`。`limit_pricing=None` 时定价段整体不执行,`adjusted` 保持
`apply_exchange_filters` 产出的 MARKET 请求——回归锁测试
`test_pipeline_market_only_path_unchanged` 钉死。

定价段插入在过滤器取整之后、mandate 评估之前:逐标的调 `venue.book_ticker`(异常 fail-soft
为 `None`)组 anchors -> `resolve_anchor` -> `to_limit_orders` 替换 `adjusted`;定价丢弃并入
`dropped_by_filters`,锚回退记 `LIVE_LIMIT_ANCHOR_FALLBACK` 审计(`metadata={"source":
...}`)。mandate 评估看到的是最终 LIMIT 单集合。

**轮询窗口(批判必须修改③,核心修正)**:MARKET **继续尊重调用方传入的
`max_confirm_polls`**(默认 5,可配)——不是"MARKET 恒走 DEFAULT 常量"的静默回归;LIMIT 用
独立的 `limit_max_confirm_polls`(默认 30)。逐单按 `o.order_type` 选轮询窗口:

```python
polls_for_order = limit_max_confirm_polls if o.order_type == "LIMIT" else max_confirm_polls
```

超时撤单(`_cancel_and_requery`)、部分成交入账、崩溃恢复(`resolve_unconfirmed_orders`)、
切单中止(slicer)全部结构零改动——这些函数只认 `VenueOrderResult.status/filled_qty`,不知道
`order_type` 这回事;`tests/live/test_pipeline_limit.py` 用 LIMIT 专属 fake venue 重新走一遍
这些路径(挂单永不成交超时撤单、撤单前部分成交、撤单赛跑全部成交、轮询过程渐进成交只记一次
账、切单子单继承 LIMIT 属性、崩溃恢复正确处置遗留 LIMIT 挂单),证明既有逻辑对 LIMIT 同样
成立,不是靠巧合。

**preview + LIMIT 短路轮询(批判必须修改④,单独成节)**:`PreviewVenue.place_order` 恒返回
`status="PREVIEW_NOT_SUBMITTED"`(非真实终态标记),`get_order`/`cancel_order` 恒 `None`。
若不处理,LIMIT 下每腿会空转 `limit_max_confirm_polls`(默认 30)次真实
`poll_sleep`、随后 `_cancel_and_requery` 因 `cancel_order`/`get_order` 恒 `None` 而产出误导性
的 `LIVE_ORDER_TIMEOUT_CANCEL` 审计 + 把这笔单计成"零成交撤单"——`limit_fill_stats` 会被
纯预览批次污染成假数据。修复采用批判给出的方案(a):把 `"PREVIEW_NOT_SUBMITTED"` 加进
`_TERMINAL_ORDER_STATUSES`——这个字符串只在 preview 路径出现,真实 venue 永远不会返回它,
对 submit 模式(真实 MARKET/LIMIT)零影响;`_confirm_fill` 的终态检查在循环最前面(`if
result.status in _TERMINAL_ORDER_STATUSES: return result, True`),因此这一改动**零轮询、
零撤单调用、零假审计**。顺带修复了同一缺陷此前也存在于 MARKET 预览路径的事实(此前 MARKET
预览也会空转 `max_confirm_polls` 次 sleep + 产生误导性超时撤单审计)——这不是本战线引入的新
覆盖面,是顺带修复的既有缺口,测试 `test_preview_market_no_poll_burn_same_fix_applies` 记录
在案。

**审计**:`LIVE_ORDER_SUBMITTED`/`LIVE_ORDER_TIMEOUT_CANCEL` 的 metadata 增加
`order_type`(+ LIMIT 时 `limit_price`),运维能区分"预期内的限价撤单"与"市价单异常超时"。

## 6. 账本与 TCA(live/execution/position_ledger.py)

**双轨迁移(批判必须修改⑦):** `_SCHEMA`(新库/preview 一次性草稿账本 `create=True` 路径)
直接给 `live_orders` 加 `order_type TEXT`/`limit_price REAL` 两个 nullable 列;
`_migrate_order_type_columns`(镜像 `_migrate_strategy_id` 的惰性 ALTER 范式,每次打开都跑,
`create`/非 `create` 路径都跑)给存量真实测试网账本 `ALTER TABLE ADD COLUMN`——duplicate
column 按"已被并发迁移完成"吞掉(镜像既有并发防御)。**两列刻意不设 NOT NULL/DEFAULT**
(与 `strategy_id` 的强制回填不同):存量行读回 `NULL` 是诚实的("当时只有 MARKET,这个
字段当时根本不存在"),不是拿 `'MARKET'` 去猜一个从未记录过的历史事实;新写入永远由
Python API 显式传值(`record_submission(..., order_type="MARKET", limit_price=None)` 默认)。
迁移测试同时覆盖"旧库打开写入"与"新建库"两条路径(`tests/live/test_position_ledger.py`)。

**存量真实测试网账本(`data/cache/live_track*.db`,双所)在真实首跑前需先备份**——ALTER 是
加列级安全操作(不重写任何存量行、不碰任何已有值),但这是真实交易数据,遵循既有灾备惯例
(见 §9 真实联调步骤)。

**TCA 基准维持决策价**(不改成限价):TCA 度量的是"决策→成交"的全程执行成本(延迟+冲击),
换成限价基准会把不利选择(adverse selection)藏起来、让 LIMIT 看起来免费。诚实边界:未成交
的限价单不产生 TCA 记录 = 选择偏差,LIMIT 的滑点均值必须与成交率一起读。

**`limit_fill_stats` 数据源(批判必须修改⑥):** `VenueOrderResult` 不带请求量(venue 语义,
不该为诊断统计扩展它)——`tools/live_track_advance.py::compute_limit_fill_stats(ledger,
client_order_ids)` 从账本 `live_orders`(`quantity`=请求量,`order_type` 列)join
`live_fills`(`quantity`=实际成交量)按 `client_order_id` 关联,算出 `{n_limit, n_filled,
n_canceled_zero_fill, n_partial, fill_rate_qty_weighted}`——ledger 是唯一同时知道"请求了
多少"和"实际成交了多少"的地方。只统计本轮提交的订单(`client_order_ids` 范围内)且
`order_type=="LIMIT"` 的行,不污染历史/其它批次的统计。

## 7. mandate 名义额口径(live/governance/mandate.py)

**批判必须修改⑤,零成本保守修正,选定方案(2026-07-06 对抗式复审后二次更正):** LIMIT 单的
名义额评估 = **两侧统一 `max(决策价, 限价)`**,不按方向分支。

原设计文本的论证是错的(已改正):偏差源不是 `band`(数十 bps),而是**锚**(实时盘口 mid)
相对决策价的漂移——决策价可能滞后 1–3 天,是百分点级而非 bps 级。限价单的限价是**事前
已知**的价格上限/下限,往"名义额更大"的方向保守评估零成本;§2 的锚容忍度门已经兜住了锚本身
离谱失控的情形,这里是纵深防御第二层。

**二次更正(落地实现曾经写成 `BUY: max` / `SELL: min`,已被对抗式复审 CONFIRMED 推翻):**
名义额 = `quantity × eval_price` 恒为无符号的"经济敞口大小"(方向由 `side` 单独表达,与
`eval_price` 的取值无关),"往名义额更大的方向保守评估"这个目标因此与买卖方向无关——两侧都
应该取两个候选价里更大的那个。`SELL` 分支写成 `min` 是方向性 bug:当市场在决策之后**上涨**
(限价随实时盘口走高、高于滞后的决策价)时,`min()` 会捡回那个更小、更过时的决策价,把真实
更大的 SELL 经济敞口算小,让本该升人工的超限订单被误判 AUTO——三个独立审查维度(LIMIT 安全/
跨战线安全边界/测试诚实性)各自独立复现了同一个反例(SELL BTC 0.013、决策价 60000、限价
65000:决策价评估 $780 未超 $800 的 per-instrument cap,真实/限价评估 $845 超限)并全票确认。
已修复为两侧统一 `max`;`tests/live/test_mandate.py` 的
`test_limit_sell_notional_higher_limit_price_escalates_via_per_instrument_cap` 钉死这个
反例不再回归。

**未知 `order_type` 恒升人工**(同一 commit,fail-closed 纵深防御,只收紧不放宽):真实
构造路径已被 `LiveOrderRequest.__post_init__` 挡死,这里防的是"万一有别的构造路径/
duck-typed 调用方"。`LIMIT` 订单若 `limit_price` 缺失/非法(同样理论上不可达),同样恒升
人工而不是静默退化成按决策价评估——一个本该显眼的异常状态不该被无声吞掉。

mandate 治理面不动:`mandate_store` 必填字段清单不变(已签署 mandate 全部继续有效),
`evaluate_mandate` 只加纵深防御分支。

## 8. 开关与驱动器(tools/live_track_advance.py)

`resolve_limit_pricing(argv) -> (order_type_str, Optional[LimitPricing])`:`--order-type
{market,limit}`,默认 `"market"` -> `limit_pricing=None`(驱动器默认行为一个字节不变)。
`PreviewVenue.book_ticker` 透传真实 venue(同 `exchange_filters`/`account`/`positions`)。

`--order-type limit` 时 `status` 快照新增 `order_type`/`limit_fill_stats` 字段(默认路径不带
这两个键);锚回退告警(`LIVE_LIMIT_ANCHOR_FALLBACK` 审计事件)一并计入 `alerts`,运维不必
单独去翻审计库。紧急减仓分支不接收该 flag(恒 MARKET)。

## 9. 落地顺序(实际执行,TDD)

① contracts 校验(15 tests)→② filters(client_order_id golden + 限价维度,10 tests 新增)
→③ pricing.py(29 tests)→④ VenueAdapter Protocol + 两所 adapter LIMIT 参数/book_ticker
(binance 9 tests / bybit 9 tests / venue_base 2 tests 新增)→⑤ position_ledger 双轨迁移
(7 tests)→⑥ mandate LIMIT 名义额 + 未知 order_type(9 tests)→⑦ pipeline.py 集成(18 tests,
新文件 `test_pipeline_limit.py`)→⑧ 驱动器 flag + 统计(8 tests)→⑨ 治理测试增补
(emergency 恒 MARKET,1 test)→⑩ 本设计文档 + runbook 增补 → `scripts/validate.py` 全绿后
真实测试网 preview 联调(`--order-type limit` 不 `--submit`)。

## 10. 真实测试网 preview 联调(2026-07-06)

`python -m tools.live_track_advance --venue binance --order-type limit --no-refresh`(不带
`--submit`,preview 模式对着真实 Binance 测试网跑):

- `venue.book_ticker()`/`exchange_filters()`/`account()`/`positions()` 全部透传真实 venue
  (定价基于真实市况);
- `place_order` 只记录"本来会提交的 LIMIT 参数"(price/timeInForce),从未真实挂单;
- LIMIT 路径在 preview 下正确短路:零真实轮询燃烧、零假 `LIVE_ORDER_TIMEOUT_CANCEL` 审计、
  `would_submit` 里能看到真实盘口锚定出的限价。

真实**挂单/撤单**联调(`--submit`)按"确认后才有真实副作用"惯例,需用户单独放行——不随本
设计自动执行。

## 11. 治理影响

扫描器零影响:全部改动在 `live/` + `tools/` + `tests/`(通用扫描器 `SCAN_SUBDIRS` 只覆盖
`quantos`+`services`,维持 `live/` 刻意不在其中的既有架构事实)。`live/` 自己的边界测试
(`tests/governance/test_live_execution_boundary.py`)既有各项继续成立(`book_ticker` 的网络
调用仍只在两个 adapter 文件内;无新 SDK;无提幣/划转字面量;mainnet 构造仍结构性拒绝——
LIMIT 不新增任何构造路径);新增 1 条:`test_governance_emergency_channel_never_limit`
源码级钉死紧急通道绝不构造 `order_type=`/`LIMIT`。五个 `SafetyConfig` 旗帜、`console_api`
GET-only、`research_runner` 白名单均零接触。

## 12. Open questions(留给用户拍板)

1. **LIMIT 开启方式**:本设计选了 CLI flag(`--order-type limit`)+ mandate 不动(理由:
   限价只收紧价格风险、不扩大授权)。若认为"执行方式变更"也属于需要签字的业务决定,应改为
   mandate 增加可选 `execution` 段并让 CLI 与 mandate 双确认——治理哲学问题,代码不该替你选。
2. **`band_pct`(默认 20bps)与挂单时长(默认 30 次 × 1s ≈ 30 秒后撤)** 是工程占位数字:
   真实使用前需你确认或改数(滑点上限 vs 成交率的偏好)。
3. **`anchor_max_deviation_pct`(默认 5%)** 同样是工程占位容忍度,非业务风险数字。
4. **撤单后残量的后续策略**:本设计维持"留给下周期自然修正"(周度节奏诚实可接受);
   "撤单后立即市价追补残量"(chase)会重新暴露滑点、需要新的幂等 ID 语义,是否要做需拍板。
5. **`post_only`(做市模式)**:只做字段与参数映射预留、不接驱动器——是否值得在小资金阶段
   追 maker 费率(测试网手续费不真实,证据只能来自主网)由你决定。
6. **真实测试网 LIMIT 联调(真实挂单+真实撤单)**:preview 已跑通(见 §10),真实副作用需
   你单独放行。

## 13. 与设计文档的差异(诚实记录)

- 设计原文的 mandate 名义额论证("band≤ 数十 bps 属工程噪声"辩护"决策价口径")是错的,已在
  §7 改正并改用批判建议的保守修正方案。
- 设计原文声称"preview 模式完全兼容"在批判前不成立(preview+LIMIT 会空转轮询/产生假审计);
  §5 记录了实际采用的修复(方案 a:`PREVIEW_NOT_SUBMITTED` 纳入终态集合)及其对 MARKET 预览
  路径的顺带修复。
- `files_to_modify`/`test_plan` 按批判补全:`live/governance/mandate.py`、
  `tests/live/test_mandate.py`、`tests/live/test_venue_base.py`、
  `tests/live/test_position_ledger.py` 均已在落地清单内(设计原文遗漏)。
- 治理边界测试实际函数数与设计原文口径不同(以 `tests/governance/
  test_live_execution_boundary.py` 现状为准,不是设计原文里的旧计数)。
