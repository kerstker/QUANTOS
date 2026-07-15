# 端到端「干跑流水线」—— 设计意图(待 brainstorm 细化)

> 2026-06-30 保存的设计意图。下一步:走 brainstorm → spec → plan → 实现。
> 目标:让系统从"一堆研究脚本"变成"一个能端到端跑的系统",且是通往实盘的正确(安全)铺路。

## 一句话

把现有的真零件串成一条**连续的、不碰真钱的干跑流水线**:一个策略能从信号一路跑到成交对账+监控,
作为一个**系统**运行——而非孤立脚本。实盘那一步永远 = 换适配器 + 人工批准,不在本设计范围。

## 流水线(复用现有真零件,补"接线")

```
选信号(如 carry composite + vol-target 权重)
  → 组合构建            services/portfolio (optimizer)
  → 下单意图            services/execution (intent builder)
  → 预交易风控门 + kill-switch【接进运行时链路,不再是死代码】 services/risk/pre_trade
  → 干跑成交适配器(纸面成交、真实成本)                      services/trading/dry_run_adapters
  → PnL 对账            services/research/paper_trading (reconciliation/consistency)
  → 监控/归因 + 哈希链审计                                   services/monitoring
  → 落库成一次可复现的「策略运行」
```

## 为什么这一步对(决策依据)
1. "系统能用":策略端到端作为系统运行,补上用户感觉缺的"一直在研究状态"。
2. 通往实盘的正确脚手架:live = 把 dry-run 适配器换成真券商适配器(同接口)+ 人工解锁,不重写。
3. 补差距报告 #3 P0:预交易门/kill-switch 从"已测死代码"→"运行时可证强制"。
4. 全程 L2 合规:仅干跑适配器、无券商 SDK、无真单/真钱、门 fail-closed、晋级实盘人工专属。

## 待 brainstorm 决定的岔路
- 编排放哪:扩 research_runner(白名单加 dry_run_pipeline 这一 kind)还是新模块?
- "策略运行"如何持久化 + 上 console 页(复用 run_registry 模式?)。
- 预交易门接运行时的 chokepoint 形态(端点触发?编排内调用?)——必须真生效、可证、fail-closed。
- 纸面账户/资金/对账的边界(PaperAccount 模拟资本,绝不接真凭证)。

## 非目标
实盘/真券商/真单/真钱——L2 安全边界,本设计到干跑为止。
