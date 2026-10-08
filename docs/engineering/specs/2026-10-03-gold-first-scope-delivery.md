# 首次黄金 Scope 冷启动与原签发历史连续性

状态：同版联合检查已通过，正在冻结；本版完整验收与现场交付尚未完成。父源码为
`dee3d59a1973f446b8873133e54dc9a1a70ad364`；独立工作目录为
`/home/kerstker/quantos-dev-output/gold-first-scope-delivery-20261002-mdqdbpk1/source`。
延续[未来模板与原预算连续性](2026-10-02-gold-future-budget-continuity.md)及
[完整工厂交付目标](../plans/2026-09-28-ai-factory-closed-loop.md)。

## 原问题与本次行为

旧 Scope 入口要求先安装同版黄金功能权限，随后等待旧 key 到期。
已安装 Scope 后的联调不能证明旧 BTC 安装可直接进入首次非执行 Scope。
本次增加独立 `FIRST_GOLD_SCOPE_ONLY` 请求、人工确认与签名域；沿原分区
复验、原安装器、固定配置和 CAS 完成首次安装，任务列表为空，不先生成
一小时黄金功能权限或功能交易任务。

原 BTC spec/result/profile/policy/registry、原 source、全部签发记录与编号
索引保留。新 source 单列，不把旧来源改标为新版。原 key/policy 到期要求、
新鲜观察、24 小时窗口、300 秒安装余量、停止历史和独立黄金预算保持。
issuer 锚覆盖原库完整前缀，包含已签与尚未签名的 RESERVED；原生
`IssuerStore.snapshot()` 先验证链、签名、消耗及 claims，不能用空库替代。

新 Scope 使用 `DEMO_GOLD_STRATEGY_BUDGET_SCOPE_V2`，旧 V1 常量与签名域
保持。公开祖先材料绑定完整请求与确认；即使重新签名，也不能在保持原确认
材料时改变额度、数量、期限或组身份。正常追加发行历史允许，原前缀必须相同。

## 安装与恢复

`tools.gold_first_scope_install` 的独立入口准备原 spec、确认、旧 registry、
完整组装输入、同一临时 key 和停止状态，再持久化完整 intent。恢复沿原 seed、
原 key、原起止窗口完成缺失写入，复用原 profile/registry/active pointer 的安装
事务；旧安装原件在新鲜复核前后再次检查。已有完成阶段的指针回退、结果篡改、
孤立或缺失 intent、旧原件变更保持拒绝；未知安装状态不报告为未安装。

仅安装 Scope 不签发订单，不登记或启动 pilot，不启动服务。正常执行仍需原
期初登记、独立预算行政操作、委托签名、REGISTER/ENABLE、逐信号证明及全部
现有风险与执行边界。

## 逐笔批准的历史约束

并行审阅复现：首次 Scope 转成旧 exact BUDGET 后会丢失原 issuer 锚。
把测试中的同一路径换成合法空发行库，原风险及签名仍能成功；该红例保留。

新 `DEMO_GOLD_STRATEGY_BUDGET_POLICY_V2` 必须携带完整 `original_scope`。
只允许沿原声明增加精确决策身份与真实 constraints 摘要；后续 exact 批准保留
该签名原件。当前和历史检查分别验证 Scope 签名，过期历史仍可只读复核。
原 issuer 在同连接的预留事务、每条 RESERVED 重放和实际签名前核验原前缀；
空库、剪切原 unsigned 前缀或自洽重建的新局部链不能签发。签名中断不退款。
Scope 派生 exact 再迁入未来模板时，原行政证书及历史重放继续核同一前缀。

固定 policy/registry 仍是受信部署配置。本合同防止正常消费者丢失约束，
不声称能阻止有权替换整个 root 配置、密钥或受信代码的管理员；该面仍受
原代码审查、治理扫描与角色隔离约束。未增加可选文件缺失时放行的分支。

## 当前验证与证据位置

全部原始红绿日志、精确前像与 agent 交接在上述目录的父目录：
`probe/`、`a-contract/`、`b-original-history/`、`c-install/`、
`root-caller/`、`b-first-scope-exact-consumers/`、`c-exact-ancestry/`。
最终同版联合检查覆盖 39 个测试文件，647 项通过，耗时 1383.85 秒；
`joint-exit.json` 返回 0，前后全部 Python 源码清单一致。原测试和 driver
进程均已结束。清单摘要为
`ba42a4ffb7bc842b2ea7c102c7fcf5f21912f0ae9ad82e5d0844b137676b8f55`，
不累加重叠局部测试数，也不把本次联合检查称为 canonical 全量通过。

已取得原生首次安装及进程中断恢复、完整原签发前缀、真实预热材料绑定、
未来模板到模拟发送、逐笔 exact 空库拒绝与中断预算保持等局部证据。
真实旧 `4673` 二进制产物由新版读取后，全部原文件、目录和 SQLite 附属文件
保持；该证据证明读取兼容，不代替将这些产物实际安装后运行的现场验收。

测试的操作员、sudo/TTY、UID/属主、已接受 source、账户与网络均为明确隔离
输入，密钥仅为测试临时生成。行情缓存由原 FreshEpisode 生成；合成时钟
保证下一收盘落在声明捕获窗口内，但不证明真实网络耗时或完整五秒链。

源码冻结流程还须实际 hook 与本版独立完整验收。现场安装/恢复、
既有活组跨版本交接、获准 24 小时成交—费用/资金费—现金—再研究、独立
经济来源和多资产逐项验收仍在完整交付目标内。机构异机设施保持延期，
策略盈利研发单列，不将局部绿色或工程验收当作整厂完成。
