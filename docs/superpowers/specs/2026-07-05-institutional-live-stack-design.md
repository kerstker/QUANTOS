# 机构级实盘执行栈补全设计(2026-07-05)

用户拍板:在 Stage 1(测试网执行栈,178 tests)基础上,把系统补齐到"机构级全功能"。
本轮 9 个工作包,按序实施、逐包 TDD、逐包 commit。用户已确认的三个决定:
**紧急减仓走独立通道**、**Hyperliquid 继续推迟**(依赖决定不变)、**Bybit key 稍后提供**
(真实联调等 key,真实下单前单独确认)。

不变的边界(全程有效):L2 五旗不动;mainnet 结构性不可实例化;mandate 真实数字只能
用户签;`live/` 纯标准库(第三方依赖=人工拍板,本轮无);`live/` 不进通用治理扫描器
`SCAN_SUBDIRS`,等价保护是 `tests/governance/test_live_execution_boundary.py`。

---

## ① OMS 补全:撤单 + 订单超时

**缺口**:`VenueAdapter` 只有 place/get/account/positions/filters——没有撤单;
`_confirm_fill` 轮询打满后订单留在非终态,等下轮崩溃恢复才处理 = 僵尸订单窗口一周。

**设计**:
- `VenueAdapter` 协议加 `cancel_order(instrument_id, client_order_id) -> Optional[VenueOrderResult]`。
  Binance:`DELETE /fapi/v1/order`(origClientOrderId);Bybit:`POST /v5/order/cancel`。
  返回撤单请求被受理后的订单状态;订单不存在返回 None(与 `get_order` 口径一致)。
- 流水线超时策略(`_confirm_fill` 升级):轮询 N 次仍非终态 → `cancel_order` →
  **必须再 `get_order` 回查权威终态**——撤单与成交存在竞态:撤到时可能已部分/全部成交,
  按交易所报的真实 `filled_qty` 记账,绝不假设"撤单=零成交"。撤单本身异常也回查后再定论
  (镜像 place_order 异常回查的既有教训)。
- 回查后仍非终态(交易所处理中):留非终态给崩溃恢复兜底,不假装确认。

**测试(fake venue 必须模拟最不方便的真实行为)**:撤单与成交赛跑(撤单受理但实际已成交)、
部分成交后撤销(filled_qty>0 且 CANCELED)、对已终态订单撤单(交易所报错/None)、撤单调用
本身抛异常后回查、超时→撤单→记账全链路。

## ② 紧急减仓独立通道

**背景**(spec 2026-07-04 §9 留白,用户本轮拍板独立通道):`check_kill_switch` 触发后挡下
所有意图包括 reduce-only——越紧急越送不出去。机构标准:紧急去风险不能被"禁止开新仓"的
开关挡住。

**设计**:新模块 `live/execution/emergency.py`,专用提交路径 `submit_emergency_flatten`:
- **不走**共用 `PreTradeRiskGate`/kill-switch(这是通道存在的意义),但自带结构性窄校验,
  任何一条不满足即拒绝该笔(fail-closed,拒绝也审计):
  1. 方向必须与当前持仓相反(多仓只能卖、空仓只能买);
  2. 数量 ≤ |当前持仓数量|(只能减到零,不能反向开仓);
  3. 订单强制 `reduce_only=True`——`LiveOrderRequest` 加字段,两个适配器都传交易所原生
     reduceOnly 参数(交易所侧第二重保证);
  4. 当前持仓为零的标的直接拒绝。
- 每笔:审计(`GlobalAuditLog`)→ 提交 → 轮询确认终态(复用 `_confirm_fill` 升级版,
  含超时撤单)→ 记账。
- 驱动器接线:`respond_to_breach` arm kill-switch 之后 → `compute_flatten_orders` →
  紧急通道。仅 `--submit` 模式真提交;preview 只记录"本会提交什么"。
- **治理测试钉死性质**:对任意持仓/订单组合,校验函数在结构上只放行严格减少 |敞口| 的
  订单——加仓/反向/超量/零仓标的全被拒。诚实边界:这是运行时结构约束 + 审计,单进程内
  挡不住受信代码绕过(与全库同一威胁模型注脚)。

## ③ TCA 成交质量分析

**设计**:`live/execution/tca.py` + 账本 append-only `tca` 表:
- 每笔确认成交记录:client_order_id、标的、方向、决策价(= `submit_live_batch` 收到的
  `prices`,周度节奏下 mark price 即到达价代理)、实际成交均价、滑点 bps
  (带方向:买贵/卖贱为正=成本)、名义额、时间。
- 纯函数 `tca_summary(ledger)`:均值/中位数/最差滑点、按标的分解、名义额加权累计成本拖累,
  并列出回测成本假设(10bps)对比——"执行现实校验回测假设"闭环的最后一块。
- 流水线在 `_confirm_fill` 确认后写 tca 记录;写失败不影响成交记账(镜像
  ledger_write_errors 的既有处理)。

## ④ live-paper 对账(parity)

**设计**:`live/execution/parity.py` 纯函数:
- 输入:Stage 0 `forward_decisions` 台账(同 decided_ts 的纸面意图腿)+ live 账本同
  decided_ts 的真实订单/成交。
- 输出:按标的对齐的周度报告——纸面意图权重 vs 实际执行名义额、成交率(意图被执行的比例,
  含 mandate 拦截/过滤器拦截/撤单的归因)、执行成本差。
- 驱动器每轮落 parity 快照(jsonl,`data/cache/live_parity.jsonl`),作为 8 周证据的一部分。
- 诚实边界:preview 模式下 live 侧是"本会提交"记录,报告如实标注 preview/submit 口径。

## ⑤ Bybit 驱动器 + mandate 交易所域

**设计**:
- `tools/live_track_advance.py --venue {binance,bybit}`(默认 binance),bybit 读
  `key/bybit_test_key.json`(gitignored,同 Binance 只看 key 名/类型/长度)。
- **mandate 严谨性缺口**:两所 testnet 的 `venue_mode` 都是 `"testnet"`,给 Binance 签的
  mandate 会被 Bybit 误认。修复:adapter 加 `venue_name` 属性(`"binance"`/`"bybit"`),
  mandate 加**必填** `exchange` 字段,`evaluate_mandate` 校验 `mandate.exchange ==
  venue.venue_name`(不匹配 → NEEDS_HUMAN_REVIEW,fail-closed 一致)。模板预填
  `"binance"`(结构性协议匹配字段,同 `venue` 的处理,非风险数字)。
- key 到位后:先只读联调(account/positions/filters/get_order),真实下单前单独向用户确认。

## ⑥ TWAP 切单

**设计**:`live/execution/slicer.py` 纯函数 `plan_slices(request, *, max_child_notional,
n_slices)`:母单按名义额上限或等分切子单,子单 client_order_id 确定性派生
(`{parent}-s{i}`,幂等可恢复);流水线可选 `slicer=` 参数,子单逐个走完整确认流程,
任一子单失败/kill-switch 触发即中止剩余子单(已成交的子单如实记账)。
诚实标注:$3-5k 周度调仓单子极小,现阶段默认不启用(`slicer=None`),是为规模化预留的
已测试机制。

## ⑦ 前端设施

- console_api 扩 live-trading 相关端点:TCA 汇总、parity 报告、紧急减仓历史、订单统计
  (含撤单/超时归因)。read-only、fail-closed 到诚实的"artifact 缺失"标注(CI 无账本)。
- 前端 live 页对应展示;契约测试(`test_console_api_contract.py` + TS 类型)同 commit 更新。
- 新策略研究种类(⑨ 产出)加进 research_runner `RUN_KINDS` 白名单 → 前端可触发。

## ⑧ 运维 runbook

`docs/runbooks/live-trading-runbook.md`(中文):周度周期时序、故障模式处置(限流/时钟
漂移/崩溃恢复/drift 拦截/kill-switch 触发/紧急减仓)、每步如何验证、key 轮换、mandate
签署与更新流程、从 preview 升 --submit 的检查清单。

## ⑨ 更多策略研究

用现有数据(336 币 6 年 klines/funding/OI)找新边,流程沿用既有纪律:
候选(资金费动量/均值回归变体、OI 变化率、截面低波、周度反转)→ IC 面板横向比较
(registry 加行)→ 幸存者过证伪流程 → 最好的送干跑流水线。开放式,做出多少算多少,
负结果照实报(参照 ML stacker 的先例)。

---

## 实施顺序与验收

按 ①→⑨ 逐包:先写失败测试 → 实现 → 包内测试绿 → commit。每包完成后
`tests/live/ + tests/governance/test_live_execution_boundary.py` 全绿;⑦ 完成后
前端 typecheck + 契约测试绿;全部完成后 `scripts/validate.py` 全量门禁绿。
文档同步:本文件 + CLAUDE.md + 记忆文件在收尾 commit 更新,测试数只报实测值。

---

## 实施结果(同日全部落地,9 包 9 commits)

**live/ + 治理边界测试从 178 增至 267;`scripts/validate.py` 全量门禁(1437 python 测试 +
治理扫描器)全绿;前端 typecheck 干净、48/48 测试绿、契约测试绿。** 逐包:

- **①(7581d11)** 两 adapter `cancel_order` + 超时策略(轮询打满 → 撤单 → **回查权威状态**
  记账——撤单与成交赛跑、部分成交后撤销都按交易所真实 filled_qty 入账);崩溃恢复对遗留
  开单也主动撤(防止上轮遗留单稍后成交与本轮叠加双倍仓位)。**顺带钉死既有缺口:Bybit
  原样状态("Filled")与流水线终态集合("FILLED")不匹配,接上 Bybit 驱动器后订单永远
  确认不了——adapter 边界统一归一化。**
- **②(b6d1bef)** 紧急减仓独立通道(用户拍板):`live/execution/emergency.py`,不咨询共用
  gate/kill-switch(治理测试钉死不 import),结构窄校验网格测试证明"只可能减少敞口";
  `LiveOrderRequest.reduce_only` + 两所原生 reduceOnly;`flatten_target_for_breach` 策略
  (回撤全平/敞口减回 90%/纯数据质量问题不自动交易);过滤器在此 fail-open(减仓单
  "不提交"才是危险动作——与加仓路径刻意相反的不对称)。已接驱动器 breach 分支。
- **③(7d238e5)** TCA:账本 `live_tca` 表(惰性建表老库平滑迁移)+ 带方向滑点 bps +
  `tca_summary` 对照回测 10bps 假设;流水线与紧急通道都 fail-soft 记录。
- **④(a44e254)** live-paper 对账:纸面侧用 Stage 0 **记录在案**权重(非重算信号),live 侧
  账本仓位 + 确定性 client_order_id 订单归因;逐周 `live_parity.jsonl`。真实联调:正确抓到
  交易所侧遗留 0.002 BTC 测试仓位(`unexpected: ['BTC']`)。
- **⑤(9c35522)** `--venue bybit` + **mandate 必填 `exchange` 域**(两所 testnet 都叫
  "testnet",不签交易所域会互相误认——unset/mismatch 都 fail-closed 升人工);per-venue
  账本/状态/风控峰值/对账路径 + per-venue 余额解析;模板预填 exchange="binance"。
  Bybit 真实联调等用户 key(`key/bybit_test_key.json` 已建但为空)。
- **⑥(4d38c37)** TWAP 切单:`plan_slices`(等分/名义额上限,确定性子单 ID,步长感知,
  总量绝不超母单);`submit_live_batch(slicer=)` 默认 None 行为不变;子单序列内失败即中止
  剩余(跨母单仍 fail-soft)。诚实定位:为规模化预留,现阶段不启用。
- **⑦(06253c2)** console:驱动器把 TCA/订单统计/venue 落进状态快照(console 只读,
  `services/` 不 import `live/` 边界不破),`/live-trading` 端点 + LiveTrading.tsx 加
  TCA/对账/订单统计/紧急减仓卡片;TS 类型 + 契约测试同 commit。
- **⑧(72df437)** 运维 runbook:`docs/runbooks/live-trading-runbook.md`(周度周期/故障
  处置/key 与 mandate 生命周期/preview→--submit 检查清单)。
- **⑨(b8ce645)** 策略研究一轮,**诚实证伪**:5 个新候选过 IC 面板(注册表各加一行)——
  日频 `reversal_7_wide` t=16.4 / `low_vol_wide` t=15.7 统计巨大;新建
  `tools/wide_factor_gauntlet.py`(周度 L/S 分位 + 换手计费 0/10/25/50bps)一轮打掉:
  reversal 周度毛夏普 0.21、10bps 即负(日频大 IC = 微观结构买卖价差反弹,持有不过夜就
  没了——与 FX +0.70 mirage 同类);low_vol 毛 0.49 净@25bps 0.26,无可交易边;funding
  变体/OI 变化率无信号。**可部署 book 不变(crowd_contra 单条)。** 诚实性核查:
  crowd_contra 日频面板 t=0.66 vs 周度重算 t=2.99(n=232)——慢速持仓因子的持有期口径
  效应,非数据损坏;相对首跑 4.32 有软化(样本延长),仍显著。runner 白名单
  +ic_panel/+wide_gauntlet(前端可触发)。

**遗留(非本轮目标):** Bybit 真实联调(等 key 填入;真实下单前单独确认)、mandate 真实
数字(用户签)、Hyperliquid(依赖决定,维持推迟)、IC 面板 per-factor 持有期口径
(registry 加 horizon 字段,候选改进)。
