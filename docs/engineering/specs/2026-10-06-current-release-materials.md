# 当前版本交付材料准备

承接[当前完整验收](2026-10-06-final-validation-fixture-storage.md)、
[Windows 核验交接](2026-10-06-independent-review-windows-onboarding.md)及
[既定闭环范围](../plans/2026-09-28-ai-factory-closed-loop.md)。本批准备本地
可审阅材料，不更改应用验收 Source、既定完整交付条件或部署授权。

## 当前锁的离线依赖

584145d59cb8cab3996ccb69286010f6ae783347 的 ARM64 锁摘要仍为
9ce054a1fba251557de66816eb3ba13d4d370b000d8af453b67004207a0e960b。
76 个固定 wheel、104388427 字节逐项校验，通过原严格离线依赖消费者；
离线锁摘要为 cb2fbdfa4473d699412545164ce52ec561b66513122c17dc62656489b88df75e。
外部目录 gold-current-release-materials-20261006 的 offline-assets-result.json
状态为 CURRENT_LOCK_OFFLINE_ASSETS_VERIFIED_NOT_INSTALLED。

74 个文件复用既有相同摘要资产；当前锁指定的 PyJWT 2.15.0 与 urllib3
2.8.0 从原固定官方 URL 获取。首次 urllib TLS EOF 失败及部分文件保留，
没有减弱证书检查。后继仅用 IPv4、关闭 curl 配置文件且保持 HTTPS 证书
校验，两个下载均 HTTP 200、摘要完全匹配，再经原 copy_verified 消费。
原缓存、应用环境和锁不修改，没有安装依赖。

## 安装辅助器版本复核

最初选用干净 b79466f1a4da16350906017e94ece5a323da4085 安装辅助器。打包
预检拒绝旧失败完整门与在途当前门，但随后复核既有现场记录，发现该版仍
有已知权限缺陷：组合驱动直接 source.install 时继承 077 umask；前端公开
父目录也可能因该掩码变成 0700。它不能作为本次安装工具。

原等待任务 session 41747/PID 2620406 已定向停止，终态 143；停止前没有
通过打包准入、没有生成 source/frontend/combined 交付目录。原脚本、绑定、
预检、停止收据保留，主验收进程不受影响。首次后继准备因精确差异前提
不符而在克隆前拒绝，也保留收据；读取实际文件差异后再继续。

独立克隆 b794，仅复制 Main 已有的两个权限修复及两个回归，原安装模板
摘要 c6246bac627c6962b3cd748d2b9f8817cbfed416ba03a3dc468dabd49d2bcd46
保持。组合安装暂用 022，并在 finally 恢复掩码；私有暂存保持 0700。
前端公开父目录在原路径／所有者检查后明确 0755，私有子目录不改。
原 b794 配现有回归先复现 3 FAIL；既有修复后两模块 10 PASS/0.28 秒，
driver 0.465511 秒。再以 584 当前应用的原证据及前端消费者导入修复辅助器，
同样 10 PASS/0.14 秒，driver 0.314853 秒。测试使用临时目录和安装替身，
没有执行实际 root 安装。

原 pre-commit 以明确 Python 3.12 PATH 实际通过，治理零违规；本地冻结
35eeafbb00fd6165c29acabda38537797edcac15，codex/reviewed-install-permissions-20261006，
五条路径（两辅助器、两测试、一记录）。它是装配辅助器提交，不是应用
完整验收或真实安装证明。Main 源码及原 b794 不修改。

## 等待完整门后的装配

新外部目录 gold-current-release-preparation-20261006；驱动完整 Ruff 通过。
实际旧 FAIL 报告仍被原消费者拒绝，当前在途门也被拒绝，原固定源码消费者
和修复辅助器绑定已核对。session 85021 已启动，只等待原 outer 2581184/
37197401 及协调 2566658/37116495；自己的 PID/start_ticks 单独保存。

只在当前完整 pipeline、ARM64、canonical ALL 的 32 项全部通过、留存通过、
outer 源码不变且退出 0 后装配；缺收据或 FAIL 均停止，不自动重跑验收。
冻结应用 checkout 保持 584；在新克隆只保留原 v4 allowlist 生成证据，
运行原 hook 后作本地证据提交，原 source anchor 保持 584。再用原导出工具、
当前 76 个锁定资产和原安装模板生成源码包并校验临时暂存；前端重建绑定
同一 artifact commit，以原运行合同／真实本地 API 合同校验，最后仅调用
修复辅助器 prepare 生成组合安装材料。

截至本轮 2026-10-06 21:30，完整门仍为第一次冷构建，在下载原固定版本
构建依赖；尚无完整门、交付包或真实安装终态。新任务仅排队，不能称
MATCHING_SOURCE_FRONTEND_AND_INSTALL_BYTES_PREPARED。installed、services_changed、
trust_installed、submission_enabled 均 false。Windows 原 CheckOnly 已获用户
成功输出，公开报告内容尚待交回，详见核验交接记录。

完整交付仍须当前同版完整门与匹配材料、长期因果输入及独立来源／结果／
时间和预锁 OOS、现场全 writer 同 cut 现金恢复、获准 Demo 现金反馈及再
研究的原五秒链与实际 24 小时条件。固定范围保持，目标 ACTIVE；不从材料
准备推导部署、信任安装、经济准入或下单许可。

后续[三项研究服务现场预镜像与操作装配](2026-10-06-current-service-materials.md)
已只读核对实际 ff 服务和八份配置，冻结包含既有权限／精确 Git 修复的
操作辅助器，19 项原工具及 19 项当前消费者检查通过。另一任务仅等待本批
匹配材料成功，再生成默认只读操作包；没有安装或切换，不能计作真实交付。

原完整门于21:48冷构建超时，85021/73566拒绝后退出1，无交付目录。后继
[公开下载路径修复](2026-10-06-cold-build-download-route.md)有实际同包吞吐与
原签名／solver 证据，22:24启动不改源码／锁／预算的584同版完整门52333；
本批新绑定材料22844、服务47517仍待整门，原失败和原冻结辅助器保持。
