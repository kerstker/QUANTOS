# bf0 同版验收与匹配交付材料

承接[解析与代理增量](2026-10-07-strict-json-and-proxy-transport.md)、
[原材料工具复核](2026-10-06-current-release-materials.md)和
[三服务边界](2026-10-06-current-service-materials.md)。
原完整交付四组与F1–F5保持；本记录不把材料准备当作交付或部署。

## 本次实际 ARM64 与完整门

应用冻结提交bf0df04791eac4baa3d9b114a915ca00c5862a49，3140个非Node Python，
摘要da7df0ae。独立目录gold-release-acceptance-20261007-parser-proxy，
原session85317；当前canonical PID3119119/start39119232已实际核对存活。

本次自身ARM64 driver已exit0：原双冷构建A/B、Trivy、network=none只读smoke
及原证据保留均通过。归档7ff747a7da442d487a150e15250296242652e79c6f570aea7ff514d1f4219bd6，
config6c348c3fe28bc4b1e61b3e94a27d18ea143d652547bfa0b7ffc578eaf34b0de3，
manifest53b7ac8f4847b7e2e7a1d07bf54983fc37cf32d904262add7dafa941eb423d57。
这是此版ARM64证据，原完整ALL32阶段仍运行，原每阶段3600秒上限不减；
没有最终PASS或完整证据留存终态，不能借ARM64或专项通过代替。

## 源码、前端和安装材料绑定

新独立目录gold-parser-proxy-delivery-20261007。沿用原开发辅助器
35eeafbb00fd6165c29acabda38537797edcac15和原打包准入，驱动只换完整应用提交、
对应验收目录和新隔离证据分支名，逆向替换后AST与原驱动相同。
所有辅助器摘要与干净HEAD已核对，旧失败材料／脚本不覆盖。

当前ARM64锁逐字与旧资产锁相同。原严格离线依赖消费者实际重验76个固定wheel、
104388427字节，原offline lock cb2fbdfa与新应用锁9ce054a1保持；没有下载、
更新或安装依赖。当前在途完整门被实际拒绝BOUND_FULL_ACCEPTANCE_STILL_RUNNING。

材料任务已启动session74180／PID3125929/start39149033，绑定本次outer3106294和
coordinator3106281的实际PID/start；失败或终态缺失直接停止，不重跑验收。
只有本次完整pipeline、source unchanged、ARM64、canonical ALL与原留存全部通过，
才在新副本做原allowlist证据提交、原hook、源包校验、同artifact前端重建及组合材料。
准备任务不是源码安装，当前尚无source/frontend/combined成功终态。

## 三项研究服务与默认只读操作包

新独立目录gold-parser-proxy-service-materials-20261007。原操作辅助器
8b4bbbab5ddd03c7846cf91aae6570d3737f044d及五份工具摘要保持，驱动仅换上游目录
和完整应用绑定，逆向替换后AST相同。原现场预镜像与八份公开单位配置原件复制
至此私有输出；随后实际只读systemctl查询确认路径／drop-in集合不变且无待reload。
八原件单链接、所有者、无组／全局写、读取前后身份及摘要全部核对。
三项服务仍active/running、ffac安装工作目录；未读EnvironmentFile或交易凭据。

在本次bf0应用消费者下加载原冻结操作工具，原四模块19 PASS／0.73秒，
driver1.0536秒，3140 Python前后相同。包括源码安装成功／失败的权限与掩码恢复、
私有暂存、前端可达性、精确Git环境、默认只读和损坏绑定拒绝；真实安装与服务
调用均为临时目录／替身，没有实际root安装或服务操作。

上游材料在途被实际拒绝MATCHING_MATERIALS_STILL_RUNNING；后继已启动session45192／
PID3126662/start39152160。只有上游同版匹配材料完整成功，才再次实查现场预镜像／
工作区摘要并生成三份drop-in、operator材料及默认REVIEWED_NOT_APPLIED检查。
原排空保护、状态、工作区、地址和EnvironmentFiles保持；新路径／修改均拒绝。
当前没有operator成功终态或现场operation，没有安装、reload、切服务或开启交易。

外部两驱动完整Ruff通过。Main代码／HEAD／index不变，未暂存或提交Main；
Windows权限诊断仍待实际回贴，修正版尚未启用。长期公共观察89813仍仅为新未来
研究输入；独立来源结果时间、真实成本与九风险、预锁OOS、全writer同cut现金恢复、
获准Demo原五秒链和真实24小时仍未完成。目标ACTIVE，本轮有实际材料与检查进展。
