# 2026-07-13 同刻跨源价格证据收口

状态：P2-6 当前价路径代码闭合；信号 cadence 与生产微观结构校准仍开放。

## 问题

旧驱动器把数日前的策略决策价与 CoinGecko 当前价比较。为避免正常市场波动造成假阳性，
它在决策价超过 3 天时完全跳过跨源核价，而 pre-trade 新鲜度门允许 7 天，形成 3–7 天空窗。

## 已实现

- Binance/Bybit `book_ticker` 在 provider 响应带合法时间时返回
  `{bid, ask, as_of_ms}`；时间缺失时保留旧 bid/ask 形状供 LIMIT anchor 使用。
- `fetch_simple_price_observations` 请求 CoinGecko `include_last_updated_at=true`，只返回同时具有
  正有限价格和 provider source time 的观测；旧 `fetch_simple_prices` API 与请求形状不变。
- `flag_noncontemporaneous_instruments` 纯函数逐标的验证盘口结构、源时间、最大年龄、跨源时间偏差
  和 venue midpoint 对独立源的价格偏差。
- 驱动器先构造真实 venue 并采集本轮盘口，再采集独立源；`decided_ts` 不参与是否核价。
- 任一证据缺失/损坏/陈旧/错时/偏离均加入既有 `frozen` 集合，从 target/current 两侧排除，
  因此已有仓位保持原样。独立源整体失败等价于全部目标缺观测，全部冻结而非静默继续。

## 当前工程基线

- 最大 midpoint 偏差：5%。
- 最大单源年龄：5 分钟。
- 最大跨源时间偏差：5 分钟。

这些值只标记为 `UNCALIBRATED` L2 工程基线，不是生产损失分布或 venue 微观结构校准结果。

## 验收证据

- P2-6 聚焦测试：157 passed。
- Ruff：相关代码/测试全部通过。
- `git diff --check`：通过。
- 拥挤校准证据重新生成并 `--check`：234 周，signed z=3.0，21 个阻断周。
- 最终统一门禁：主套件 2113 passed / 1 skipped；live/trading/tools 901 passed；
  前端 68 tests + typecheck/build；治理扫描 247 文件零违规。

## 剩余工作

1. 当前 `_MAX_STALENESS_MS=7 天` 与周频信号一致；只有在用户确认新的调仓 cadence 后才能收紧。
2. 在 testnet/demo 留存每轮 venue/CoinGecko 原始观测、采集延迟与判定，形成可复算校准集。
3. 用留存分布校准 age/skew/divergence，区分现货聚合价与永续盘口的正常 basis。
4. 逐标的 REST 不是原子快照；后续评估批量 websocket/聚合行情快照，但不得因此引入 live 权限。

本项不改变 `L2_DRY_RUN_ONLY`，不启用 mainnet、真实券商、订单提交或 AI 交易权限。
