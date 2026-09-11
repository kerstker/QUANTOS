# Demo 持续账户与订单事件接线（2026-09-12）

本增量补齐持续事件输入：操作员采集进程维持 DEMO 私有连接，将实际接收的账户、订单、配置、保证金及成交摘要保存在独立输入日志；Console 和 Studio 展示当前连接、事件数量、双时钟与历史缺口。普通提交权威、实际新订单和账户现金闭合仍需分别完成。

## 实现与边界

- `tools/binance_demo_user_stream.py` 默认离线；显式操作员读取只访问固定 DEMO 的 listenKey POST/PUT 资源和私有 WebSocket。租约不写磁盘，过期消息中的租约脱敏；不删除可能与其他读取进程共用的租约。
- 每次采集最长六小时、十万条消息，最多八次重连；15 秒心跳、25 分钟续期。REST 在可终止子进程中执行，20 秒截止；连接、停止信号和全部等待受会话截止时间约束。实际停止和失败保留，不能无记录重试。
- `services/research/demo_user_stream.py` 只保存输入事实，不是第二本现金账本。金额保持十进制字符串；账户局部更新不推断其余仓位已清零；重复消息保留而不重复计费。成交摘要与订单事件不会分别记账。
- SQLite 单写锁、追加触发器和本地哈希链。外来数据库、损坏或被移除的触发器、不同账户引用均拒绝恢复。重启新增缺口。完整导出在同一读取事务内验证全链，并保留窗口前最近的缺口；导出仍是未认证输入，需要既有固定动作绑定后才可进入技术回放。
- `/api/v1/console/binance-demo` 新增 `user_stream`，对应 TypeScript 契约与接口契约测试同步。只读端检查清单和最近 100 条记录，显示最近 50 条；明确校验范围，不把局部哈希检查当作来源签名或全历史认证。
- Studio 支持全部记录、账户/订单消息、数据缺口筛选，按接收顺序显示场所事件时间和事务时间。订单 ID 以字符串保留，手续费按原币种显示。刷新失败、过期、停止和零消息都有独立状态。

账户引用是当前 DEMO API key 的命名空间哈希，不是交易所提供的稳定账户 ID；更换 key 应建立新日志，由操作员另行确认关联。凭证不进入 Console、Runner 或网页。

## 验证记录

先保留缺模块的失败回归，再实现日志与采集；额外复现了外来数据库误写、移除触发器被修补、导出遗漏窗口前缺口的问题，修订后通过。首轮日志 11 项、采集及日志 19 项、接口/容器/持久化相关 97 通过/1 跳过；后续联合 87 项通过。前端首轮历史连接标签误显示为当前状态被测试发现，修正后 20 项通过。各组有重叠，不累加。

实际 DGX 隔离采集 120 秒：1 次连接、8 次真实心跳、11 条日志记录，按预算正常停止，0 条账户/订单事件、0 个采集缺口。零事件不证明事件交付；观察期没有触发 QUANTOS 新订单。完整新源码验收与长期运行结果将在完成后追加，前一源码的完整 28/28 不覆盖本次增量。

材料目录：`/home/kerstker/quantos-dev-output/demo-user-stream-20260912/`。`actual-stream-120s.json` 与隔离 `state/data/cache/binance_demo_user_stream.db` 保留实际输入；所有 red/green 和失败日志分别留存。

## 运行

使用 DGX 已验证 Python 3.12，明确指定权限受限的 DEMO 凭证与独立日志路径：

```bash
python -m tools.binance_demo_user_stream --operator-read \
  --credential-file /operator/private/demo/credentials.json \
  --journal /isolated/state/data/cache/binance_demo_user_stream.db \
  --seconds 21600
```

该命令只用于操作员事件采集。研究 Runner 不提供此入口。不要将现有账户仓位或资金流水冒充 QUANTOS 新策略盈亏。

## 1–4 尚待闭合

1. 精确历史成本、同一截点的账户现金核对。
2. 独立普通提交验证来源、实际新订单与回执恢复；本增量提供持续输入，不替代这些条件。
3. 新源码完整发布验证、实际运行；既有全规模恢复 932.389 秒仍超过 300 秒，官方深度归档质量失败仍保留，异机恢复搁置。
4. 原 P3/P4、P6 自然窗口继续；模型、计划、旧失败与预算不改写，不以软件测试代替成本后盈利证据。

## 协议依据

[Binance USDⓈ-M 用户数据流](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams) 规定局部账户更新及同类事件的时序语义；账户余额变化字段不等于完整现金利润。
[租约 REST 接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/user-data-streams) 定义 POST/PUT 及有效期。
[事件结构](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/~schemas) 包含租约失效消息，采集时必须脱敏。
