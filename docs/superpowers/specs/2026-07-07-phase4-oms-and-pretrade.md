# Phase 4 —— OMS/EMS 订单完整性 + Pre-Trade 引擎承重(诚实裁定 + 落地)

> 2026-07-07 · ultracode 5-agent 并行调查(均亲自 Read/亲跑)+ 主审 TDD 落地 · IMPLEMENTATION_ROADMAP.md Phase 4
> 核心:Phase 4 大部分**已被既有结构性机制满足或需用户签风险数字/cadence 决定**;能本轮干净做完的是紧急通道
> 对抗式属性测试(item 4)与 ADV/波动承重**机制**(item 1),其余诚实标注后延。

## 子项4 · 紧急减仓对抗式属性/网格测试(P2-3)—— ✅ CLEAN_TDD 已完成

紧急通道是唯一绕过共用 gate/kill-switch 的下单路径,其"只减敞口"不变量此前只零散单点测。新增穷尽属性/网格
测试(`tests/live/test_emergency.py`):仓位(多/空/零)× 方向 × 数量(含超仓)× fail-open 过滤器分支 × 混合批,
断言**每笔发出的 venue 订单 reduce_only=True 且结构上不增加 |仓位|**(方向相反、qty ≤ |pos|、绝不穿越零点),
非法(同向/超仓/无仓)一律不发。纯测试零源码改动,65 新参数化用例全绿。

## 子项1 · live 风控引擎接真实 ADV/波动(P1-5 深化)—— 机制 ✅,激活待用户签阈值

**问题**:`make_ctx_factory` 用占位符 `est_daily_volume=1e12` / `instrument_vol=0.8`,使 `check_liquidity` /
`check_volatility` 在 live **结构性永不触发**(死引擎)。

**已落地(机制 + 接口,fail-closed,零回归)**:
- `live/execution/market_stats.py`:纯函数 `average_daily_volume_units`(trailing 窗中位数 base 量 × UNIT_SCALE,
  缩放对齐 gate 的缩放整数 quantity)+ `realized_volatility`(对数收益 pstdev × sqrt(annualize))。**fail-closed**:
  缺/非有限/窗口不足 → None(gate 据此拒该标的,绝不像 dry-run 那样 fail-open 回退大数)。6 tests。
- `make_ctx_factory` 加 `adv_units` / `inst_vol` 参:**None(默认)→ 保留占位符(向后兼容零回归)**;传 dict →
  真实值,缺键 → None → check_liquidity/check_volatility fail-closed 拒。3 passthrough tests。

**激活(driver 穿线 + 签阈值)= 就绪下一步,本轮未做,两个原因诚实**:
1. 驱动器把真实 ADV/vol 喂进去需从 `_latest_weights_and_prices` 的 panel/决策行算出,但它经 `_STRATEGIES`
   注册表返回 4-tuple——穿线需注册表契约 4→5 tuple 的审慎重构,不宜在此仓促动安全关键驱动器。
2. verdict = **NEEDS_USER_SIGNED_NUMBER**:`max_participation_rate`(现 1.0=有效不绑定)/ `max_instrument_vol`
   (现 5.0)是风险数字须用户签(firm-gate 式)。$5–10k AUM 下真实参与率 ~1e-7,liquidity 门在小资金下本不
   实际绑定(RE-1:carry ~$2M/crowd ~$7M 才绑)——接线诚实价值 = 把门从"结构性失效"升为"真实且承重 + 缺数据
   fail-closed 拒",而非"今天主动拒单"。

## 子项2 · OMS/EMS 强化 —— 大部分 ALREADY_DONE / 需 cadence 决定 / post-L2

地基事实:live/ 是**无状态周度 REST 轮询**(每请求全新 urllib,每轮重建 adapter),非长连接——"断线重连/主备"
在本模型里大部分**不适用**;"不丢单/不重单"由另一套机制承重。逐项:
- **撤单** = ✅ 已做(两所 cancel_order + 竞态回查 + 超时撤单 + 崩溃恢复撤孤儿)。
- **改单 amend** = 投机:Protocol/两所无 amend;MARKET 即时成交无意义,只挂单 LIMIT 才需,而 LIMIT 默认关,
  现走 cancel+replace(功能替代)。不建(或建默认不接标 testnet-pending)。
- **断线重连** = "不丢单/不重单"已被**确定性 client_order_id + pre-journal PENDING_NEW + resolve_unconfirmed
  每轮回查 + preflight drift 硬停**结构性满足(非靠重连子系统)。唯一干净小缺口(低价值):瞬时网络错误
  (URLError/超时)在幂等 GET 读路径不重试→一次抖动整轮丢(fail-safe 不下单),但崩溃恢复网已把它降级为
  "整轮丢+下轮恢复"= 优雅降级非正确性修复。**绝不盲重试 place_order**(会双发,回查语义必须保留)。长连接
  user-data-stream 重连只在 1H/盘中 cadence 才需 → 待 cadence 决定。
- **主备连接** = 需 cadence + mainnet(testnet 只一台主机,无备可切,机制建出也无法真实验证)→ post-L2。
- **交易日切换** = ✅ 已做/不适用(crypto 24/7 无收盘;唯一"日切"=日内亏损 UTC 基线重置已做)。

## 子项3 · 新鲜度门收紧 + 同点跨源核价(P2-6)—— CLEAN_TDD,留专门 follow-up

裁定:**staleness 收紧**绑 1H cadence 决定(周度下 7 天是刻意宽容,1H 已推迟)。**同点跨源核价**可干净 TDD:
现跨源核价拿滞后决策价(OI 归档 1~3 天)去比 CoinGecko"当前"价,决策价越陈旧越要靠"当前一致"补偿容忍度;
修 = 用 `venue.book_ticker` 取**当前**盘口 mid(同点)比第二源,复用既有 `flag_divergent_instruments` /
`resolve_anchor` / `book_ticker`(fail-soft)。这是一次编排改动,留专门 follow-up(触碰核价路径,须 testnet
preview 验证)。

## 诚实边界总结

Phase 4 的诚实答案:订单完整性的硬骨头(确定性 ID、pre-journal、崩溃恢复、撤单、preflight)**上几轮已做**;
本轮把紧急通道不变量**穷尽证明**、把死掉的 ADV/波动引擎升为**真实且承重的 fail-closed 机制**;其余(改单、
主备、长连接重连、staleness 收紧)诚实标注为 cadence 决定 / post-L2 / 需签数字,不硬塞造假承重。
