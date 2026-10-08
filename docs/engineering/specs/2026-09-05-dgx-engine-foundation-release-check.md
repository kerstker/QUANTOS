# DGX 发布验收修正记录

日期：2026-09-05。沿用第三批技术回放合同，结果仍为
`TECHNICAL_VALIDATION_ONLY`。异机恢复按用户决定延期，生产服务未切换。

## 实际构建发现与修正

提交 `ec8e275535e5226743d71e4abbbc467511a1c758` 的两个独立 ARM64 冷构建
均成功，OCI manifest 同为
`sha256:0a38f8e501bb9d5df965d9cc34a1f14a4785e866e72ff4c8778896730161f3af`，
但当日 Trivy 扫描拒绝发布。不能将构建一致性当作漏洞检查通过。

随后对同一固定基础镜像独立扫描，发现 `libuuid 2.42-r0` 含新披露的
HIGH/MEDIUM 漏洞。已有 OpenSSL 修正与最终镜像移除 pip 的做法不能消除
libuuid 的问题。当时 Alpine v3.24 的 ARM64 官方包已提供
[libuuid 2.42.3-r0](https://pkgs.alpinelinux.org/package/v3.24/main/aarch64/libuuid)。
截至本次重建，仓库索引已将该修订滚动为 `2.42.3-r1`；运行层改为显式固定
当前修订版本，并保持零已知漏洞策略和严重度门槛不变。

独立诊断容器只应用 libuuid/OpenSSL 补丁并移除 pip，使用同一固定 Trivy
扫描器与当日数据库，实际结果为零已知漏洞。这只证明基础层修正，仍需完整
应用镜像的双冷构建与实际复扫。诊断容器和镜像已清理，没有修改常驻服务。

原捕获器在策略失败后清理临时文件，仅留下通用错误，丢失具体发现。本次增加
只创建的 `rejected-audit-<sha256>.json`：保留失败审计及原始扫描报告，放在任务
输出根，**不写入通过的 evidence bundle**；仍立即失败并执行既有资源清理。
相同报告可重读，冲突文件/符号链接拒绝覆盖。

全量回归另外发现 `technical-replay` 已存在于 API 契约，却漏登记在 console
组合边界的精确 GET 路由集合中。补齐这一项，保持精确枚举和只读方法断言。

## 已执行验证

任务输出根：`/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905`。

- 首次双冷构建与拒绝：`/tmp/quantos-arm64-capture-ec8e27553.log`；构建原始
  进度另在 `/tmp/quantos-arm64-build-a-progress.log` 和 `-b-progress.log`。
- 原始/补丁基础层扫描：`r1-scan-diagnostic/base-report.json` 与
  `r1-scan-diagnostic/patched-base-report.json`，后者零发现。首轮完整运行镜像
  的失败扫描被旧捕获器清理，不能据此声明已保存其具体漏洞清单。
- 失败后留存、固定补丁和路由边界回归：先失败日志
  `/tmp/quantos-scan-retention-before.log`、`/tmp/quantos-libuuid-before.log`；
  修正后 `/tmp/quantos-r1-boundaries-after.log`，79 passed。
- 第三批整合 621 passed / 1 skipped，真实 HTTP 补充 46 passed / 1 skipped；
  前端 257 tests、typecheck、eslint、build 通过。
- 独立 canary 的真实研究队列、只读 HTTP、运行合同、进程身份和 L2 边界均通过，
  回放结果 hash 为
  `dab7ab40c1c00a13713305946d6f11a993c3dacaf852abc135e7c4a7ef629bbd`，
  对账残差为零；记录在 `canary-ec8e27553/`。该 hash 属于上述中间提交。
- 构建期间 60 次健康探测零错误、两路公开行情流全程 healthy，接口 P95
  208.85ms；这是五分钟观测，不是冻结运行 SLO 的完整压力验收。

首轮大范围回归主组为 7110 passed / 19 skipped / 20 failed。其中一个是上述
路由枚举遗漏，三个是旧供应链证据，其他失败涉及修复期间工作树不再干净的
来源绑定。完整日志保留在 `/tmp/quantos-full-regression-ueh7yt6g/validation.log`。
这轮不是最终干净提交的 canonical PASS；最终须重新执行完整门禁，不能删掉
这些失败或仅用专项通过替代。最终独立输出计划存于任务根 `r1-final/`，
canonical 结果以仓库 `evidence/validation_report.json` 的实际状态为准。

## 运维边界

只读 systemd/journal 元数据确认 watchdog 汇总为 46 PASS、2 BREACH、2 PARTIAL、
3 UNAVAILABLE。BREACH 来自盘口归档刷新和备份；备份实际原因仍为
`RESTORE_RTO_EXCEEDED`，现行 300 秒目标不放宽。元数据在 `ops-readonly/`。
未配置外部封存/heartbeat，A 股前向为 PARTIAL，CFTC 首次触发尚未到达。
没有读取盲化候选收益。生产数据库不可由当前开发用户读取，尚不能完成真实
规模恢复耗时分解、RTO 修复与回滚演练；异机部分按用户决定延期。

因此即使最终离线发布检查通过，仍不能声称 M2 已验收，更不能切换经济权威、
晋级或实盘门禁。最终源码、构建/扫描、完整验证和运行版本须分开核对。
