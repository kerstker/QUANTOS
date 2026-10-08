# 软件 V1 发行治理阻断收尾

范围沿用[首次软件发行的五项收工条件](../plans/2026-10-08-software-first-release-v1.md)。
真实样本、盈利策略、独立 OOS/签发、Windows 审计和现场运行不是本版软件发行前置。

## 原失败及最小修正

基线 `55f0bfe09e5b6b8f0f0db43d43207077a071516a` 的原完整软件门后端实际终结为
11535 PASS、4 FAIL、19 SKIP，5849.28 秒。完整门继续执行后续阶段；本版不能据此
制作通过准入的发行包。原 3600 秒超时和改用原 CLI 可配置 14400 秒执行时限的记录
见[发行阻断记录](2026-10-09-software-v1-release-blockers.md)；生产性能预算不变。

四项失败对应两个原因：

- `test_services_research_does_not_import_risk`：原件读租约直接导入风险域函数。
  原件报告改经既有 `services.execution.research_risk_replay` 离线组合端口调用原函数。
  执行端口显式重导出同一 `review_gold_account`；计算、输入、风险政策和租约守卫保持。
  原域边界测试、允许边和扫描规则未改。
- 三项 alpha 试验历史快照／跨平台 checkout 检查：发行源码缺少原有
  `evidence/alpha_trial_ledger_snapshot.db`。恢复 Main 已追踪的原始公开快照字节，
  不重新生成历史、不改变快照 seal 或要求文件。大小 110592 字节，SHA256
  `270009147e553904ca6486d843988ad7eaeb73d06d49f58e1ad933dabd04f359`。

重新生成原服务依赖图：20 域、52 边、零环。没有风险阈值、真实权限或依赖版本变化。

## 实际增量验证

验证使用既定 Python 3.12/aarch64 环境与原锁文件。所有日志保留于
`research_outputs/first-software-release-v1-20261009/governance-closure/`。

- 修正前四项原失败均已复现：`original-four-regressions-red-isolated.log`；最初错误
  工作目录的失败运行另行保留于 `original-four-regressions-red.log`。
- 修正后原四项全部通过：4 PASS，3.13 秒，`original-four-regressions-green.log`。
- 原域边界、快照 checkout、Gold 风险回放、执行风险端口、Gold 风险政策和执行风险
  审查六模块：496 PASS，24.44 秒，`affected-boundary-and-risk-tests.log`。
  原四项包含其中，不把重叠计算为新增测试。
- 原实际 Gold 回测导出、测试签名消费和冷登记库恢复场景：1 PASS，29.84 秒，
  `original-signed-consumer-and-cold-registry.log`。这是隔离软件场景，不建立真实签发权限。

以上只是增量证据。修正版仍须独立冻结唯一提交，实际完成同版 ARM64 双冷构建、
原扫描／离线冒烟、原 `scripts/validate.py` 完整门以及隔离根安装和浏览器操作，
之后才能发行。本记录不将基线失败、专项通过或旧版构建折算为新版完整通过。

## 原登记历史的发行关联

55f 完整门的后续 tools 阶段另有五项原不变登记检查失败：发行快照有原登记文件
和原 Git 对象，但其独立源码导出历史不继承这些登记提交。Main 原
`913325cc17db40dd4ffe3698d1f0702747b69f8c` 实际继承全部登记历史。
四项治理修正后的 `4f0004f65256bc01760c28358eafd933e6b01ced` 原五项同样复现失败。

通过真实 Git 合并连接这两条既有历史，合并采用已评审软件树，原提交、文件、
登记条件和祖先校验不改，没有 replace/graft。合并提交
`d6cdf3353cf84c2c3bc4048de0dd97e6486ce552` 的两个父提交分别为 4f0004f6 和
913325cc；合并前后完整源码树均为 `7dd40ed0af7b6f7147ebedf5ae6fc7ee0740c921`。
这是发行 provenance 修正，不使原失败研究获得经济权限。

原五项复验 5 PASS，0.79 秒：
`original-immutable-artifact-regressions-green-02.log`。首次普通合并因两条独立历史被
Git 拒绝，和该合并实际成立前误命名 green 的五项失败运行均保留；明确允许合并
独立历史后才取得上述真实通过。4f 的在建发行轮仅中断自有 Docker build CLI，
原失败／清理和退出留存，不运行一个已知必然失败的完整验收副本。

## 既有采集端到端合同的版本更新

55f 后续 tools 阶段还发现原
`test_whole_native_source_reaches_original_queue_review_and_cold_recovery[True]`
仍要求早期 `QUANTOS_PROJECT_RESEARCH_FACTS_V8`。53ed 同样实际复现：1 FAIL，
7.50 秒，`original-forward-collection-contract-red.log`。原生产选择逻辑对此种
采集输入明确返回 `QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V10` 和相应
`QUANTOS_PROJECT_RESEARCH_FACTS_V13`；它不使用新净前向／冻结验证输入的 V18。

仅将这条原断言迁移到精确 V13，并增加精确 V10 风险审查断言；原政策／哈希、
缺样本覆盖、未完整风险就绪、零经济与提交权限、单次执行和完整冷恢复断言保留。
整个原采集模块 18 PASS，15.78 秒：`original-forward-collection-contract-module-green.log`。
没有生产软件、输入窗口、样本量、策略或风险门变化。

53ed 同版两次实际 ARM64 冷构建、Trivy 运行镜像零已知漏洞和无网络只读冒烟
全部通过，保留于 `attempt-08-history-release/`。该版本已启动 ALL 时才完成上述
原失败定位；仅中断它自有的 QA 子进程，原日志／退出／源码与构建结果保留。
不能据此声明完整软件门或安装通过；新版需完成同版验收。

## 原完整门终态和容量结果固定值迁移

55f 原 ALL 完整终结：32 阶段中 30 PASS、2 FAIL、零 TIMEOUT；两个失败阶段为
后端（11535 PASS/4 FAIL/19 SKIP）和 live/trading/tools（9440 PASS/7 FAIL/52 SKIP）。
整轮约 11072 秒，原输入、断言和生产预算保持。其余前端、构建、治理及证据阶段
全部通过。原结果完整保留于 `attempt-06-complete-execution/software-gate/`。

最后一个失败为原 `test_native_manifest_material_helper_is_same_original_identity`
固定了容量流量接线前的结果哈希。修正前再次真实复现失败。
分别从容量增量 1294f0ac 的真实父版 81cd6b12 和当前版执行同一小型原回放，
结果哈希分别为 `3f0a3561e4490fe663440683864e29289a86e75df5c2f38dfbbaa13523e95fe3`
与 `d519cdb5df58ff770391600c65a908a0d0e50cf067b87c9dbb3bdcc514ce03d0`。
run_id、现金对账、持仓状态和全部账本事件逐项完全相同。完整 payload 仅多三项：
引擎容量政策、决策容量流量、执行容量流量；对应原已评审增量，生产计算未改。
原件和差异见 `before-reviewed-capacity-flow-golden.json`、
`current-reviewed-capacity-flow-golden.json`、`native-manifest-result-hash-diff.json`。

将原固定结果哈希迁移到当前容量合同，固定 run_id／manifest 原身份比较保持，
并增补精确政策、两个时点的容量金额 `10` 和订单数量 `0.1` 断言。原登记模块和
完整 V2 引擎合同合计 103 PASS，17.66 秒：
`original-native-manifest-and-ledger-contracts-green.log`。没有删除或弱化原风险、
篡改拒绝、现金和冷恢复断言，也不把本版虚构成原旧结果。

至此原完整门的 11 个失败均已定位并经相关回归关闭；仍须最终候选实际完成
同版完整门、构建和安装验证，不能将 30 个旧版阶段或专项通过合成为新版 PASS。

不切换现场服务、不修改保护状态、不安装真实信任、不读私钥或交易凭据、不下单。
