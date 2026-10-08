# Gold 维护窗口在停写后固定数据切片

状态：增量实现与隔离联合验证完成，父提交 `cb72988f5316567987ecc8e73a3a819541a0d0df`；自身完整发布门尚待完成。

## 问题与行为

完整角色归档原先要求在停止服务前传入包含全部研究文件身份的
`VerifiedResearchLayout`。研究服务关机时即使只将 registry 原字节写回，
文件时间也会变化，原窗口随即以 `ORIGINAL_CHANGED` 拒绝正常归档。
原 public 快照和 cut 时间同样早于停止服务，不能代表停止后的切片。

现有完整维护入口增加 `InstalledResearchScope`：停写前只核对原保护配置、
固定研究根的 inode、所有者、模式和父链，不打开可变研究文件。
完整布局参数仍可转换成此范围；新调用方也可直接捕获范围。
Root 的实际、有效和保存 UID/GID 必须全部为零，调用方字典不能代替句柄。

维护窗口复用原独占锁、服务清单和启动抑制。暂停服务后，按原固定角色
根和全部研究 UID 完成两次相同的进程观察，再实际重读原 public 文件，
以新 public 时间和同一窗口摘要绑定 cut；此后捕获完整研究布局，再次
观察范围并复核原件，才持久固定布局摘要。全部 native worker、原文件
读取租约、退出检查、角色档案和恢复连接继续使用同一停止后上下文。

窗口使用明确的新 scope schema；旧 Gold 窗口及其恢复仍按原合同校验。
新窗口的 RUNNING 中断清理通过原配置和根、窗口身份及新的完整范围
观察核对，在进程退出后只记录 `FAILED_CLEANED`。原布局、public、cut
摘要保持，即使失败后原 registry 又变化也不会把新数据重标成旧档案。
清理不报告 worker 退出证明或归档成功，服务恢复仍调用原窗口入口。

## 验证与限制

- 真实原文件改写、虚拟服务停机的原反例先失败；修后停止时间与新 public/
  cut 一致，原数据字节保持。两类 RUNNING 清理反例也先失败后通过。
- Root/保存身份、调用方字典和根 inode 替换边界三项通过；正例的 Root
  身份只在单元夹具中模拟，生产检查未放宽。
- 六文件隔离回归 103 PASS / 4 SKIP，34.43 秒，driver 34.884409 秒；
  3117 Python 前后保持。四项跳过需要实际内核和 Root，尚未计为通过。
- 实际内核五项通过，driver 678.108789 秒，3117 Python 前后保持；覆盖
  原 UID 停机改写后直接 scope 捕获、全部五 worker、活 UID 阻断、末端
  失败清理，以及租约释放后残留进程和旧数据变化的恢复。运行使用独立
  无网络、只读源码 ARM64 容器中的真实 Root、原角色 UID、setpriv 和
  文件租约；服务管理器、cgroup 和 accepted-release 路径仍为明确夹具，
  不能代替现场验收。
- 七个变更 Python 的完整 Ruff、全仓 E9/F821、服务图（20 域、52 边、
  零环）与仓库卫生通过。冻结提交、实际 hook、精确 DGX 环境基线和
  选择整合的最终记录保存于下述证据目录；环境基线不是完整发布门。

空进程观察不证明持续 writer 排除；整个 writer barrier、全部角色业务
连接、现金恢复和现场 Demo 仍待完成。`writer_barrier_verified`、
`whole_set_verified`、`cross_role_business_join_verified`、经济权威和
下单权限保留 false。该增量关闭停写顺序缺口，不增加 F1–F5 的交付范围。

完整发布门另用自己的固定提交验收；专项通过不能代替 canonical ALL。
未部署、切换运行服务、写保护状态、安装真实 trust 或读取凭据/下单。

本机证据目录：`gold-project-closure-20261003-3pr5mrly/`
`gold-post-pause-layout-20261005`，保留所有失败、命令、JUnit、进程身份与
Python 前后摘要。
