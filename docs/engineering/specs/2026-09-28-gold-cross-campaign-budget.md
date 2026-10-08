# 同一黄金冻结输入的跨 campaign 预算

原预算仅按 campaign_id 统计。同一 input_sha256 改 campaign 编号、input_id 别名或
objective，可以重新获得六个候选和多轮预算。四个失败回归复现：重复预留、跨编号超额、
两个连接同时预留，以及重新计算配置摘要后恢复已消耗候选。

现复用 FactoryStore 的现有轮次记录，在 BEGIN IMMEDIATE 事务中按
XAUUSDT + input_sha256 聚合同库历史：

- 所有 campaign 合计最多六个已预留轮次，失败和取消也计数；未知模型选择不返还预算。
- 同一输入同时至多一个 RESERVED；原拥有者可取回自己的预留，别的 campaign 等待。
- 已知候选跨 campaign 排除，父尝试引用也包括同一输入的先前 campaign。
- 更换编号不重置冷却期；采用前后两个 campaign 间隔的较大值。
- 复核从全组先前记录重建每个 session 的剩余候选，不能仅重算配置摘要就洗掉历史。
- 不同 input_sha256 保持独立；原小时级 campaign 行为不变。

旧记录若已超出此组预算或有重叠预留会明确拒绝复核，不删除、压缩或改写旧试验来通过。

## 验证

新增五项：四项先失败，一项验证不同输入应独立。修复后相关四组25项通过；进一步覆盖
Factory API、工作流、黄金恢复与 Console 合同，八组 **144 passed，1 skipped**。
两个独立 SQLite 连接的并发预留实测仅一个成功。Ruff（既有Store使用E9/F821）、
治理与仓库卫生通过；未运行canonical全量验收，未部署。

只读复核之前真实Qwen调度实验的factory.db，原一个campaign/一个round仍为COMPLETED，
未修改其登记。现场目录仍为 gold-factory-campaign-20260928/1790580969594853813。

## 边界

这是同一数据库、完全相同输入字节身份下的调度预算，不是跨数据库、跨输入修订或
跨数据集的经济研究总预算。改变源码身份、费用假设或数据内容会改变输入摘要；
不能据此声称获得独立样本或重置统计试验次数。独立OOS、全局TrialLedger资格审查、
独立核验方与完整交易闭环仍需继续完成。
