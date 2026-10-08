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

不切换现场服务、不修改保护状态、不安装真实信任、不读私钥或交易凭据、不下单。
