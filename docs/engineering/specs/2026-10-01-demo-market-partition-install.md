# 独立操作员的受保护市场分区安装

本增量续接[全历史分区候选](2026-10-01-demo-market-partition.md)，增加
`tools.demo_market_partition_install`。默认只复核原 executor 新鲜生成的候选；
显式安装仅供独立本机 sudo/TTY 操作员确认完整候选摘要。没有现场执行本入口。

## 安装与原材料留存

沿用原已接受源码核验、原登记指针与 spec/result 绑定、两份公开 registry、
expired 旧授权及原 executor preparation 租约。操作员持 controller 排他维护
租约，复用原 successor 捕获和 context 交接；不在排他租约内嵌套 shared cycle
租约。研究进程不承担操作员角色，也没有新增签名或下单权限。

确认前后重新捕获两市场账户并核对完整旧状态、canonical context、停机字节、
registry、原完整账户库存摘要和单列 USDT 钱包。后续观察时间和 origin 摘要可变化，
已批准的切分点、旧风险事实及黄金金额不可变化；时钟倒退与跨日拒绝，不能以新观察
重新设定基准。停机字节摘要仍不代表已认证停机链。

第一次可能持久写入为 root 私有的
`/etc/quantos/demo-issuer/market-partition-intent.json`。随后在原 controller 的
`market-partition-installations/<candidate digest>` 留存候选、旧 registry、操作员
确认和最后复核。两份相同记录分别发布到 issuer/executor 配置目录，root 属主、
原角色 gid、0640；issuer 先写，executor 最后写。原排他 write/fsync 入口和受保护
描述符读取用于发布与读回，之后再次核对新鲜原材料。

任一可能持久化后的异常保留 `installed=UNKNOWN`，已有 intent 或任一保护记录拒绝
重放。尽可能留存 failure；即便结果留存失败也不报告成功。CLI 拒绝已有部分安装时
继续报告 UNKNOWN，不把失败变成“尚未安装”。没有覆盖、删除或自动恢复这些记录。
部分安装的独立恢复交接仍待设计与验证。

## 权限与未完成事项

安装记录仅声明分区材料已保护留存，`submission_enabled` 和经济权威仍为 false。
没有新 key、profile、policy、mandate、执行任务、pilot 基准或服务操作，也没有修改
停机/预算/dispatch/recovery/OMS/wire 账本。原候选自身所有权限与验证标志继续 false。

原运行 `load_context`、提案、独立签发及发送前检查尚未消费这份安装记录。
后续须绑定两角色记录摘要、完整旧前缀和原 V2 重建，处理无 target 历史、持仓、跨日
及中断；新黄金首登记和 shared-budget 授权仍待实现。不能将本安装工具计为真实
黄金执行、24 小时运行、现金反馈或整厂交付完成。

## 隔离证据

初始16项因缺模块失败；首次安装和原候选交接联合29项通过。补充回归复现操作员
变化与时钟倒退未拒绝，两项已修正；六组联合117项通过。之后复现 CLI 将部分安装
误报未安装，修正为 UNKNOWN；最后三组57项通过，覆盖共享 controller 租约拒绝及
原本机身份门。Ruff、服务图检查通过（20域/52边/无环）；实际 hook 结果另行留存。

测试使用虚拟操作员/属主和临时目录、合成账户观察，目录租约、排他写入、fsync、
读回、旧 receipt/OMS/recovery 与 V2 重建沿用实际实现。没有真实保护配置安装、
凭据、订单、部署或策略研发。输出在本机
`quantos-dev-output/gold-partition-install-20261001-ggq74tw1`；完整发布须由最终整合
版本自身验收，不能沿用5f39版 PASS。
