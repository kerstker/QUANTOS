# 最终同版验收：终态合成夹具存储与重新执行

承接[First-Scope 测试修正](2026-10-06-first-scope-fixture-source-coherence.md)
和[既定完整闭环范围](../plans/2026-09-28-ai-factory-closed-loop.md)。
本批没有生产代码变更，不修改原验收阶段、时限、磁盘预留或交付定义。

## 已核实的结果

原 6b227509 完整门已经失败终结。原 outer-exit 为 returncode=1，
12384.264587 秒，3135 Python 前后保持；其 30 PASS / 1 FAIL / 1 TIMEOUT
不追改，也不借作当前 584145d5 的验收成绩。

2026-10-06 本轮开始磁盘空闲 417276186624 字节，低于原启动余量
442515849216 字节：412451078144 字节原输入预留，加 28 GiB 测试余量。
只选择已终结的离线合成测试展开目录，真实公共输入与在途采集不在处理范围。

第一批原 6b canonical-tmp 已完成存储整理：1013932 份单链接常规文件，
原字节 15889947584、原分配字节 18098028544。所有文件体及原路径的九字段
元数据先进入 PAX/zstd 和 SQLite 清单；内存归档、持久化磁盘归档各独立复读
一次。逐文件再次核对内容及身份，先 fsync 删除意向，再移除展开副本并
fsync 受影响目录。原 Source、其 evidence、顶层原报告与日志摘要保持；
非归档叶与所有目录保留，未扩大任何权限。完成时空闲 429099483136 字节。

磁盘归档 SHA-256：
`d11268d7dc34d8b8b082f1f4cb5a2f8c3eed5c12608ee4fed7f433d44f07b129`。
路径清单 SHA-256：
`1509e461d23c5bccfc751355a4de4a31eb4f5cd9290dd4ac7802799ad2165b25`。
这是测试夹具存储，restore_authority=false，不是业务现金恢复验收。

第二批选择原 438f04c2 同版完整门的 canonical-tmp。其原 outer-exit 与
canonical-exit 均 returncode=1，7613.405648 秒，原 Python 前后保持。
原协调、阶段进程和进程组已消失，并核对当前用户命令行无该夹具路径引用；
不声称已检查无权读取的其他用户文件描述符。第二批 child 2566736、
start_ticks=37116757 已 returncode=0，657.932060 秒，完成 1148805 份文件，
原字节 17470485165；原 Source/evidence/日志/失败报告保持。完成空闲
440887365632 字节，仍不足原启动余量。

因此按同一算法处理已终结的 First-Scope 合成夹具。child 2578416、
start_ticks=37182550 已 returncode=0，148.357175 秒，完成 321656 份文件，
原字节 6968080923；原 332 PASS 报告与冻结 584 Source 保持，未借作全门。
三批共 2484393 份常规单链接文件、40328513672 原字节与路径元数据，
非归档叶和目录保持。三批均 restore_authority=false。

## 当前版本验收准备

外部目录 `gold-project-closure-20261003-3pr5mrly/`
`gold-release-acceptance-20261006-current-closure/source`，从已审阅本地分支
独立克隆，HEAD 为 584145d59cb8cab3996ccb69286010f6ae783347。
3138 份应用 Python 摘要为
`4333b6db911778219a5788f41f6a27e99bfe117ccfd992631d12be5f61f4a9e4`，
与原冻结 Source 完全相同。原 Main HEAD/index 不变。

首次依赖准备因 node_modules 目录链接未被目录型 ignore 排除而拒绝。
原失败留存在 preparation-first-failure.json；只把本轮新建链接换为独立
依赖目录副本，原依赖不改。package.json、package-lock.json 和依赖内部
lock 与原完整门相同，Git 工作树恢复干净；没有修改 ignore 或源码守卫。

精确 DGX 基线 `/tmp/quantos-dgx-development-tac2qdp3/baseline.json` 通过：
aarch64、Python 3.12.3、Node v24.16.0、ARM64 锁摘要 9ce054a1；
这是环境检查，tests_returncode=None。IPv4 构建主机重新通过实际 DNS 核对。
七份原外部验收辅助器只替换明确版本绑定，AST 规范化比对与原流程相同。

外部串行驱动 session 22775 完成上述三批后，实际启动检查记录空闲
443963072512 字节，大于原 442515849216 字节余量；当前源码、干净状态、
原锁和精确环境均保持。原 ARM64 双冷构建、扫描、只读冒烟、canonical ALL
和证据留存流程已启动。原 3600 秒阶段预算保持，失败记录并停止，不自动
重启，不复用旧版本 PASS。

2026-10-06 20:47 实查协调 PID 2581196/start_ticks=37197418、ARM64 driver
2581269/37197719、capture 2581281/37197722 同原进程在途。源码保管预检
1395 文件/145 目录 read cut 通过，服务依赖图和持久化预检也均退出 0。
ARM64/canonical 整门尚无终态，不能称完整 PASS。辅助器 E9/F821、Main
卫生/diff/新增记录链接通过；本批没有变更生产 Python。
Windows ACL 修复/CheckOnly 仍待实际 PC 输出，trust
未安装。公开采集 14309、58478 同原 handle 在途；真实独立来源/结果/时间、
锁定 OOS 与访问记录、现场全 writer 同 cut 现金恢复、获准 Demo 现金反馈
及再研究的实际五秒链/24 小时条件仍未完成。完整四组保持，目标 ACTIVE。
