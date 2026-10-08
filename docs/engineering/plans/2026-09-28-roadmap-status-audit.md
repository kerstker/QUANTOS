# 2026-09-28 路线图状态复核

本记录按用户要求检查路线图，区分实现、固定版本验收与现场运行。没有重跑全系统验收、重启服务或操作账户；没有总完成率，也不将历史DONE视为当前完成。现场观察截至本日约12:40 CST。

## 适用范围

- [总计划](../../QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md)保留阶段0–14的架构范围，历史百分比不适用。
- [DGX引擎路线](2026-09-05-dgx-engine-foundation-roadmap-review.md)保留M0–M4及其验收要求；工程检查通过不自动证明M2持续运行或M4经济证据完成。
- [研究优先决定](../specs/2026-09-20-dgx-research-first.md)是当前优先级：单市场研究、真实成本、冻结前向、受控Demo。机构异机恢复等延期，不能记PASS。
- [平台补齐计划](2026-09-23-platform-completion.md)是当前R0–R5实施记录；本次以其后续增量纠正较早表格的过期状态。

## 当前交付与缺口

| 路线 | 可支持的结论 | 未满足的完成条件 |
|---|---|---|
| R0 工程与运行 | 源码217102cea的canonical.json为ALL、32阶段PASS；feedf1a10已由用户安装。新GetUnit竞态修复4d8261ade专项77项通过，完整验收仍在ARM64构建 | 新修复完整验收与部署、真实容量归档、失败发布服务恢复；不能继承上一版PASS |
| R1 Demo闭环 | 执行/授权/恢复组件与历史有限账户核对已有记录；用户已确认账户、独立执行和签发配置 | 当前有效授权、有限会话实际订单/成交/费用/现金核对；无快照不等于用户未配置。BTC证据不能替代XAU验收 |
| R2 研究有效性 | RF2十二案例、六维评估及V2技术账本已有交付记录 | 独立锁定OOS、真实成本校准、足够的策略证据与独立经济准入；不使用已失效V1收益 |
| R3 自然前向 | 本次用原固定采集身份核验：5/14窗已发布，缺口0，identity_matches=true；两个timer正常 | 剩余9窗及10月8日08:10 CST成熟评估。发布信号不是盈利证明；旧P6失败保留 |
| R4 控制台与账户 | 黄金图表、历史逐笔关联、三类账户稳定币页面已有交付记录；资金刷新timer当前active | 黄金monitor当前failed/退出78；存量摘要STOPPED、history_complete=false、reconciliation=REQUIRED。连续采集、期初成本及完整净盈亏尚未完成；本次未做新浏览器验收 |
| R5 工程债 | 18条research→alpha例外已由后续增量消除，锁文件、Alpine哈希安装、Ruff和局部mypy已有证据 | 全仓严格类型与长期运行可靠性仍未完成；不再把18条例外列为待办 |
| 黄金自动交易 | 只读接入及研究/执行边界存在 | XAU专属规则、完整复盘、合格策略、受控Demo闭环及明确实盘授权；主网未开放 |
| 其他资产/机构设施 | 原多资产范围仍保留 | Kraken等实际账户/资产输入及专属验收未证明完成；机构设施延期，不阻塞当前单市场研究 |

## 当前现场证据

- `/var/tmp/quantos-maintenance-result-p9lxkvzf.json`：操作5e7aa225，MAINTENANCE_INCOMPLETE、NOT_RUN、services_restored=true。这是本次维护恢复成功，不表示原失败服务全部健康。
- `/var/tmp/quantos-operator-diagnostic-u38om08j/summary.json`：GetUnit查询CalledProcessError。现场复现inactive timer卸载竞态；原诊断未记录单元名，不武断认定原错误来自该timer。
- `/home/kerstker/quantos-dev-output/maintenance-ipv4-release-20260928/canonical.json`：217102cea固定版本完整验收，21项测试跳过不计通过。
- `/home/kerstker/quantos-dev-output/maintenance-unit-query-20260928/roadmap-rf3-check.json`：本次原发布回执与采集身份核验。
- systemd只读查询：黄金monitor为failed/78，钱包刷新及RF3两个timer为active。timer正常本身不证明数据刷新成功。

## 下一步与验收出口

1. 恢复黄金持续监控：先定位退出78原因，保留历史缺口；恢复后证明新事件持续入档、重连补录及前端时效。
2. 收口在途维护修复：完整验收后按原管理员入口执行；只有实际归档结果、恢复结果和新发布数据一起成立，才关闭容量恢复任务。
3. 按原授权和账户范围完成XAU受控Demo会话，逐单核对成交、费用、现金及未知结果恢复；不越过独立签发门。
4. 研究与自然观察继续按原协议推进；补齐OOS和成本证据，10月8日核验成熟结果，不先写成功。

结论：工程基础已有显著增量，端到端产品闭环未完成。当前最紧迫的是连续监控和现场可靠性，而不是继续扩张模块数量。
