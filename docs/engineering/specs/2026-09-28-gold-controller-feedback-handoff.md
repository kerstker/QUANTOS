# 黄金研究身份登记与控制器反馈调用

## 变更

原黄金 setup spec 可显式携带完整 `research_identity`。仅黄金非 portfolio 允许，原身份摘要/时间校验保持；身份包含在原 spec 摘要及操作员批准中。assemble 在批准时间重验后，将同一个 gold-research 策略标识绑定到原 profile、attestation、issuer policy 和初始 task。默认无身份功能测试保持原路径，不自动授予经济准入。

setup CLI 新增 `--research-identity-sha256`，只读取 `/etc/quantos/demo-observation/research-identities/<sha>.json` 的 root-owned 固定文件；不接受任意研究目录路径。核对完整文件内容摘要和身份后才进入原提案/TTY确认流程。没有实际创建受保护身份文件、批准或安装。

原 functional controller 对带身份且 `DEMO_CYCLE_COMPLETED`、现金核对为真的周期调用 worker feedback；worker 仍重建原证据并验证签名策略归属。控制器启动固定 release 下的交付模块子进程，以周期/文件/反馈摘要调用上一增量的独立配置、永久降权交付。调用中没有凭证参数，也不从 executor 返回的任意 path 读取内容。

交付结果保存在原周期 feedback-results 及结果中的 research_feedback 字段。交付失败不改写已完成交易的状态、不重新执行任务，并返回退出码2；默认无身份周期行为不变。

## 验证与限制

研究身份装配、控制器反馈助手新增回归先因缺少功能失败，修复后原 setup/gold setup/controller/successor 四组57 passed；Ruff E9/F821 通过。测试覆盖批准后换身份拒绝、未来身份、固定文件路径/摘要、完整材料原安装校验、对账后调用顺序及交付失败不增加订单。组件进程和系统身份均使用隔离测试替身；未实际启动 root 交付或操作账户。

运行身份文件/反馈 publication profile 尚未登记、未部署；失败交付的只读重试入口待补，自动下一 campaign 的审定预算配置仍未接齐。恢复专用入口和后继登记也须核对黄金身份适用性。未完整 canonical、未实际 Demo 全闭环或持续验收，不标 F4/F5 完成。
