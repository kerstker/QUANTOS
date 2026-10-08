# 价格设计与实验反馈整合版本

## 完整验收和交付终态

固定1048c3761ef0f4d3b324aa323b5f1897fbd39506的原canonical已退出0，
32项全通过，失败/错误/超时0，21跳过保留。后端9602通过/19跳过，
执行侧5546通过/2跳过，前端90文件500测试通过，类型检查、lint和生产构建通过。
证据产物提交a14fde4c1318a892e22aee5918233cfe34fb8ac8，源码锚仍为1048。

`acceptance-retry-1/acceptance-exit.json`为CANONICAL_ACCEPTANCE_AND_RETENTION_PASS；
原source delivery退出0、bundle-verification为SOURCE_ONLY_DELIVERY_VERIFIED，
独立克隆完成。79份载荷和仅安装脚本摘要已再次读取核实。
匹配前端FRONTEND_DELIVERY_VERIFIED（106文件），三服务drop-in草案摘要已核实。
服务方案摘要b89988075f8e3c0319dd05905624091dd78aaaa16223551c0d850135e5a34409。

输出根为`/home/kerstker/quantos-dev-output/factory-price-feedback-20260929-vplv2g3p/acceptance-retry-1`。
所有等待者均已退出，不能继续报告运行中或重启验收。尚未安装、尚未切换服务；
源码安装器还不安装前端，统一安装与实际切换/恢复仍待完成。
后续3ff策略定义/前瞻观察和主目录新增策略草稿不在此验收内。
工程PASS不代表策略有效、独立经济准入、真实Demo或整厂闭环。

基于a247ed85201988b8b1a2b17bf13ff9b361e13b59，在独立目录
`/home/kerstker/quantos-dev-output/factory-price-feedback-20260929-vplv2g3p/source`
整合后续研究功能，分支codex/factory-price-feedback-20260929。原主工作区保留，
原a247真实双批及完整验收源码不变。

本版本包含：崩溃恢复测试时间修正；原报告信号原因进入研究记忆；
AI采集前价格区域及退出配方；同组前批实验反馈进入下一批价格设计；
原页面/API/队列/输入装配/账本交接和恢复。没有改变订单权限、成本或账户状态。

独立目录十二组研究/采集/恢复/反馈验证90项通过；依赖图、存储边界和完整console合同
108项通过、1跳过。前端两文件10项通过，typecheck通过。Starlette弃用警告保留。
原依赖图重新生成，20域/52边/无环。固定提交前执行实际pre-commit，不绕过hook。

这些是整合检查，不是canonical ALL或部署验收。已有真实单轮价格设计、
单次反馈设计及旧版本连续研究证据保留各自源码身份，不能改称本固定版本的运行证据。
后续在固定源码上验证两批新公开采集、真实模型设计、原成本实验及下一批反馈，
保留真实一小时冷却，失败也应如实留存。完整发行验收和实际服务交付尚未完成。

完整目标仍包括独立验证与淘汰、组合风控、真实黄金Demo、获准执行、成交资金对账及复盘。
当前生成能力限于一个吸收策略族，NO_SIGNAL及参数变化均不是盈利或准入证据。

## 固定提交与运行

独立提交1048c3761ef0f4d3b324aa323b5f1897fbd39506，32文件，实际pre-commit通过，
提交后工作树干净。完整canonical未启动，不能继承a247的在途结论。
真实双批在独立research-source副本启动，会话64745，驱动run-research.py；
新模式AI_DESIGNED_ZONE_V1，真实本地Qwen、两批新公开输入、每批一个实验及一小时冷却。
只有原两批完成且第二批设计/研究记忆绑定第一批诊断时才报告通过。未部署。

首批实况：run_d8aff63d334400b8284561a05be5f18d已error，原错误
INVALID_OR_STALE_WINDOW_TIME；未创建研究结果。原组仍RUNNING，PID3063864存活，
等待原冷却后第二批。不能据固定源码整合检查通过称连续采集或本版真实闭环成功。

## 完整验收首试与网络路径复核

原输出目录的完整验收99808已退出1：图/存储预检通过，尚未构建即在Trivy数据库下载
遇到CloudFront IPv6连接重置。原源码未改，项目全量测试未开始。
原源码打包依绑定终态拒绝ACCEPTANCE_NOT_COMPLETE，未生成安装产物。
旧日志、stderr和acceptance-exit.json保留。

继承现有代理的独立数据库预检仍EOF失败；没有当作成功或盲目覆盖重跑。
IPv4到原CDN的TLS可连接后，使用临时本机CONNECT隧道进行完整数据库下载预检，
原_prepare_scanner_database成功，摘要
sha256:c5bde5866f95ca2280ac769fd88d61a14f5833787e245c304e09d65d5c5c6b62。
探针记录位于scanner-ipv4-probe-3leejsot/summary.json。

临时隧道仅允许实际观察到的public.ecr.aws和d5l0dvt14r5h8.cloudfront.net的443端口，
仅在下载子进程作用域内设置原支持的代理变量；不读取TLS内容，不关闭证书验证，
不改宿主代理配置、扫描器版本、数据库摘要或证据校验器。
辅助器ipv4_scanner_transport.py摘要保存在新输出目录transport-binding.json。
原构建和离线扫描不经过此隧道；数据库下载完成后关闭该临时监听器并恢复进程环境。

## 同一固定源码的新验收输出

新独立目录acceptance-retry-1保持1048c3761源码，并已重新完成扫描数据库预检、
基础镜像及原生构建器预检，进入COLD_BUILD_A。验收会话76064，PID3079708/start_ticks317712301。
源码打包会话81559/PID3080529/start_ticks317714537绑定该验收进程，
前端打包47502/PID3081003/start_ticks317716336绑定该源码打包进程。
只有原完整canonical ALL和证据留存通过才导出同版本源码与前端包。
旧失败目录不覆盖，research-source中的真实采集保持1048不变。
当前仍是验收在途，不是完整通过或安装；服务切换材料生成器已准备但未执行。

## 交付准备自动续步

在acceptance-retry-1新增外部驱动finish-service-preparation.py，不修改冻结源码。
会话51543绑定实际存活的前端打包父进程3081003/start_ticks317716336，
等待其终态成功后调用原prepare-next-service-delivery.py；生成器、原服务配置摘要及
前端驱动的文件摘要已固定。原生成器再次校验同版本源码/前端证据和现场配置，
只生成升级配置与回滚原件的审查材料，不安装、刷新或切换服务。
驱动语法检查通过，实际启动返回WAITING_FOR_BOUND_FRONTEND_DELIVERY。
尚无准备成功结果；具体进程绑定保存在候选candidate.json，失败保留独立终态。
该续步减少打包后的人工接力，不代表部署或黄金交易闭环完成。

后续验收实况：76064原驱动确认arm64-driver退出0，双冷构建、扫描及断网启动
证据已保留，archive=00995d7bc1945138458de0e5fc8b99eb3f530439a2376a9e7fcc42c5a09d0e48。
canonical子进程3114350已实际启动并确认存活，当前仍RUNNING；候选元数据已更新。
这不是canonical通过，也未触发安装或服务切换。之后在开发目录新增的策略定义
导出和前瞻观察绑定不属于该固定1048版本，不能归入本次验收。

三项研究用户服务交接前的只读复核保存在
`/home/kerstker/quantos-dev-output/factory-cutover-preflight-20260929-z3yw2slu/summary.json`。
web/console/runner均active/running、NeedDaemonReload=no，单元路径及内容摘要与
原preimages一致；当前15842/health声明live_disallowed=true，但尚无gold_factory_research
任务类型。选定research-runtime-244dd6fbe状态根的原runs表为空，没有观察到排队任务。
这只是非原子快照，实际切换时须重新验证根、队列和配置，不能证明已经排空全部进程。
没有读取EnvironmentFile、凭证或账户记录，也未安装或切换任何服务。

## 固定1048真实双批终态

原64745驱动实际退出0，组为EXHAUSTED，摘要FRESH_TWO_BATCH_INCOMPLETE。
首批保留INVALID_OR_STALE_WINDOW_TIME失败；真实一小时冷却后后台自行创建第二批。
第二批模型收到前批1527ms边界延迟诊断，价格区域由-10/+50bps改为-20/+20bps，
时效上限仍1500ms；不能据参数变化声称可用性改进。

第二批采集run_3c9ed13e7f0eb480e4395a1f4f845ad5与研究
run_ecaf5abb4483eb9d208ef5ae90b8ff46均done，实际trial
c5730979a1cf1c6e9275da6bd08f1b1aebb61315721b0ee1832bcf229c7aadab，候选0、NO_SIGNAL。
终态摘要写入时自动核验仍running；退出后直接查询原注册表确认核验已done，
latest-review.json绑定同trial，状态INDEPENDENT_AUTHORITY_UNCONFIGURED，没有遗留运行任务。
post-exit-recheck.json另存于actual-cn038529，不改原终态摘要。

固定源码HEAD/干净工作树、两份设计收据、第二批冻结采集记忆和完成试验的input/report
摘要已复核。首批没有完成研究，所以第二批没有前批实验记忆；本次不能证明
“完成实验→下一批价格设计”的真实连续闭环。只证明首批失败后继续新采集/研究和明确预算终止。
未部署、没有订单、没有盈利或独立经济准入结论。

## 完整验收后端阶段通过

固定1048的原canonical首个pytest阶段已实际结束：9602 passed、19 skipped、9 warnings，
耗时2366.07秒，阶段PASS。此前a247/a232的崩溃恢复时钟失败在本固定版本未再出现。
原验收已进入live/trading/tools执行栈阶段；这不是整个canonical完成。
源码/前端自动打包及三服务升级审查材料仍等待原完整验收终态，未部署。
