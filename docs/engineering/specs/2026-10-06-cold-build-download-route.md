# 完整验收冷构建超时与公开下载路径修复

承接[当前完整验收](2026-10-06-final-validation-fixture-storage.md)、
[匹配交付材料](2026-10-06-current-release-materials.md)和
[三服务材料](2026-10-06-current-service-materials.md)。应用冻结版本仍为
584145d59cb8cab3996ccb69286010f6ae783347；本批不改变源码、依赖版本、
阶段预算或交付范围。

## 原失败终态

2026-10-06 21:48，原完整验收 session 22775 实际退出 1。outer elapsed
3683.610253 秒，3139 份含 Node 内 Python 前后相同；第一次 ARM64 冷构建
命令在原 3600 秒上限超时。原日志停在固定构建依赖 gfortran 下载，未开始
canonical 阶段，不能称当前应用全量测试通过或失败。原报告、日志和失败
文件摘要保留于 gold-cold-build-download-diagnostic-20261006。

源／前端／组合材料任务 85021 随后以 FULL_PIPELINE_NOT_PASS 退出 1，
服务材料 73566 以 MATCHING_MATERIALS_NOT_COMPLETE 退出 1；各目录未生成
交付包或 operation。拒绝后的原件保持，没有追改失败、延长时限或直接重跑。

## 实际下载诊断

从原固定公开基础镜像，只读、无网络、非 root、无 capability、无宿主挂载
检查实际 aarch64 / Alpine 3.24.1 及两个原 v3.24 仓库。比较同一官方 URL 的
gfortran-15.2.0-r5.apk，保留 HTTPS 主机、证书验证、禁用 curl 配置文件。

原绑定地址 199.232.162.132 在 45 秒内仅接收 1448743 字节，returncode=28，
约 32192 字节/秒；本轮官方 DNS 返回的 146.75.114.132 在 1.351264 秒内
完整接收 17262562 字节，returncode=0，约 12886807 字节/秒。旧部分内容与
完整包前缀逐字节相同，旧件不是完整包，不宣称全字节相等。完整包摘要
e4b93d67c193500b6d615a1bcf145b0d14c0122dfe67863765c86157dd3fd785。
原基础镜像中的 apk verify 实际接受该包，未使用 allow-untrusted。

后续 DNS 又回到旧地址；不声称新地址始终是最新解析。采用原 capture 已
支持的显式 IPv4 路由：地址来自本轮真实官方解析，再由原域名 TLS、原
签名索引／固定包复核。临时容器的独立缓存、公开基础镜像 metadata 副本
及非 root tmpfs 中，原完整四个构建约束经 apk add --simulate 解析 30 包；
recursive fetch 在 90 秒诊断预算内完成 GCC/gfortran 等依赖，gfortran 原
校验 OK，摘要与 curl 完整件一致。没有安装应用依赖或改宿主状态。

缺缓存、只读日志／数据库锁、错误 logfile 参数、复制原锁被拒以及 fetch
版本表达式未使用 solver 的诊断失败均保留；最后使用公开 installed 元数据
和原约束 solver 修正探测。不会把这些辅助器错误计成产品 PASS。临时容器
read-only、cap 全删、no-new-privileges、无宿主挂载，实际私钥与凭据不读。

## 当前同版重跑

独立克隆到 gold-release-acceptance-20261006-current-cdn/source，3138 份应用
Python 摘要仍 4333b6db911778219a5788f41f6a27e99bfe117ccfd992631d12be5f61f4a9e4。
七原辅助器只换公开地址为 Alpine 146.75.114.132、PyPI 151.101.128.223；
恢复地址后的规范化 AST 与原件相同。原 hostname、TLS、包签名／锁、双独立
冷构建、扫描、只读冒烟、canonical ALL 和留存流程保持。

初次准备错误地把 DGX 打印前缀纳入路径，随后最新 DNS 条件也拒绝；两失败
留存，发行 Source 未修改。原实际 DGX 基线 ffvym6pd 通过，Python 3.12.3、
aarch64、Node v24.16.0、原 9ce054a1 锁、干净 584，tests_returncode=None。
后继以本轮签名／TLS／solver 证据验证原受支持的显式路由，不修改应用门禁。

22:24 实际空闲 442846412800 字节通过原 442515849216 启动余量，原数据
预留及 28 GiB 不减。session 52333 启动同版原完整验收；源码保管、服务图、
持久化预检均退出 0。22:31 原冷构建 A 在下载固定 ECR 基础镜像层，仍无
整门终态；Alpine 包下载诊断不能替代基础镜像下载、完整构建或全量测试。

新匹配材料任务 22844 与服务材料任务 47517 仅绑定该次原进程。已有冻结
安装工具 35eeafbb、操作工具 8b4bbbab 不改；外部驱动只换明确输入路径，
规范化 AST 相同，Ruff 和在途拒绝检查通过。实际八份服务预镜像再次核对，
以后任何变化仍拒绝。只有同版整门和匹配材料成功才生成默认只读操作包；
失败停，不自动重跑或安装。

目标 ACTIVE。Windows 公开报告内容、真实独立来源／结果／时间及锁定 OOS、
现场全 writer 现金同 cut 恢复、获准 Demo 反馈再研究的原五秒链和实际 24
小时仍待完成。没有部署、切服务、写保护状态、安装 trust、改变经济或下单
准入，不以当前部分结果证明完整交付。

22:42 新 ARM64 流程实际完成：双独立冷构建 A/B、原离线 Trivy、无网络
只读冒烟和发布证据留存全部通过；arm64-exit / arm64-driver-exit 均退出 0。
原归档摘要 c18fa967219b09c79c4a9d3ce950778e328de7dfecb2bf75e0d7f7f70f4c73e7，
原固定镜像 manifest 56c7092754291e48bc65f2dcdddd98d42280ad63072c10fba92b5f8830714ad9，
config 318c77a39abd383642eccf17e4f7607280870632e4d35bb71103c92df27abf80。
这证明本次同版冷构建超时已在原流程中解决，不是完整系统交付完成。
canonical ALL 已实际启动，PID 2763369/start_ticks=37875527，原阶段 3600 秒；
仍在后端测试阶段，无终态。新匹配和服务材料任务继续原绑定等待，不借
ARM64 的局部结果当作 canonical PASS。
