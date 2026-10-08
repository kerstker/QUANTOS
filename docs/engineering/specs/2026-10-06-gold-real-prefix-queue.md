# 真实有限片段进入原研究队列与冷恢复

状态：真实公共原件的有限技术交接已完成，整个 F1–F5 未完成。
依据：[固定交付范围](../plans/2026-09-28-ai-factory-closed-loop.md)。
本文记录观察结果，不改变策略、准入、授权、发布或运行服务。

## 源码与输入

所有生产函数来自月观察原只读源码
`b3cbfce3a9f9801e8d81dfccc4f8c4517cb94c2a`，Python源码摘要
`96a229efae768b6ecb6b07be2e3ef2046d0bbd4d5a674da52269d18711cd223e`。
使用已核验 Python 3.12.3/aarch64 和原 ARM64 哈希锁。
当前发布候选 dbc22390 的独立 ARM64/完整检查另行进行；本证据不能
代替该版本发布检查。月观察进程未重启、未换源、未修改原件。

原 `gold_chunked_source.from_session` 从当前配对观察中读取第一个已
封存报价段和同水位 Bar 原件，再沿原 native prefix 冻结全部字节。
原输入 source_id 为
`0cc36c0d611149d709b678a170d447e80586ba69ec03f0c26768a0976ff239a5`，
一个原件分段、6523439 blob 字节。原报表核对122帧与两根自然收盘，
报价可用时间1791221359882–1791222302251，跨度942369ms。
收盘实际接收时间为1791221400202及1791222300724。

数量0.1、初始抵押1000、费率0.00045沿用此前有限技术检查的明确
配置；费率为假设，未读取账户费率。没有为产生交易修改信号参数。
原 warmup 的 historical_pit_proven=false、未签名来源、规则/数量映射
及保证金模拟假设等限制保持，不将回顾性登记写成前向预登记。

## 原交接与实际结果

1. 原 `registered_gold_chunked_job.prepare_source_run` 创建真实本地
   实验登记、三候选原 trial 和输入字节副本。副本与冻结原件完全
   相同；actor 为 AUTOMATED_RESEARCH，无人工批准或候选晋级。
2. 原 `JobQueue` 执行 whitelisted `registered_gold_chunked`，任务
   `run_d617b13bf99e865d55bb5d58d350ae67` 为 done，技术 verdict 为
   TECHNICAL_VALIDATION_ONLY。channel/trend/reversion 均零订单、
   零成交和零持仓；三账户及现金基准残差均0。零成交不构成收益、
   真实成交成本、容量或 Demo 现金闭环证据。
3. 原 `ProjectResearchEvidenceReader` 完整读取实际 project/registry，
   原输入 material=COMPLETE、part_count=1、attempt_count=3。
4. 在全新进程中通过原 recover kind恢复，初次 metrics 完全一致，
   117份 native 原档案 SHA、三条试验历史和项目语义cut完全保持。
   补充冷进程 trace 确实观察到队列、native source及原 _native_run
   各调用1次，而生产 execute/_execute/compare_source入口均0次。
   没有替换生产函数或用测试替身使恢复通过。

原 invocation 为
`c1c84c82ad96b54840356b1a1890d10a295da82e22e84b69b72a75578ac27cc1`。
项目 cut SHA 为
`74dd3fef0a7fcfe11219a2e47fa23e5b4a6623dccd9c4ae581a41c6635878de9`。
首次完整交接9.259325秒；首次冷恢复6.616440秒；补充消费者 trace 的
新冷进程7.411011秒。每次3121 Python前后保持。

## 原预算与未通过状态

本有限任务先声明原件64MB、准备128MB、历史256MB、完整结果16MB、
结果索引256MB及4096字节chunk；均由原合同核验，不在失败后加大。
研究范围为 RETROSPECTIVE_FINITE_PUBLIC_PREFIX_TECHNICAL_ONLY。

原消费者实际输出：

| 检查 | 当前状态 |
|---|---|
| OOS | NOT_INDEPENDENTLY_LOCKED |
| 统计审查 | CHUNKED_EVIDENCE_ADAPTER_PENDING |
| 风险独立重放 | NOT_INDEPENDENTLY_REPLAYED |
| 独立核验 | INDEPENDENT_REVIEW_REQUIRED |
| 候选准入 | NOT_APPROVED |

economic_authority、promotion_approved、submission_enabled始终false。
没有30天有效输入、来源权威、Demo或整厂完成证明。后续继续既定
完整发布、长期经济输入与独立核验、现场同cut现金恢复以及获准
Demo反馈四组；盈利策略另开阶段。

## 实际审查入口复核

原真实队列执行 `registered_review_request`，任务
`run_7ecf572e4a7e8755795b5edce092f42f` 明确终结为 error，原错误为
`REGISTERED_RESEARCH_REVIEW_PRODUCER_SOURCE`。实际计划是
QUANTOS_REGISTERED_GOLD_CHUNKED_RUN_V3，生产者为
REGISTERED_GOLD_CHUNKED_RUNNER_V1，没有 forward_design。
它是回顾性技术任务，不能作为已预登记的完整前向审查任务。
没有更改原计划、生产者身份或历史，也未补写声明使它通过。

[原审查入口](../../../tools/registered_research_review_job.py)已实现
完整前向计划的 unsigned request 导出；
[原统计适配器](../../../services/validation/project_research_statistical_evidence.py)
已支持 Gold V2/V3 声明并使用全部原报告复算。当前任务镜像中的
CHUNKED_EVIDENCE_ADAPTER_PENDING 不能概括为整个后端缺少统计实现。
本任务既无这些声明，也未进入统计审查或独立签名核验。

被动 trace 实际观察 prepare_review_request 1次，研究执行、native run、
compare_source均0次；117份原档案、三个trial、项目语义cut和3121份
Python保持。外部诊断125.721130秒结束，其断言遗漏原错误码前缀导致
driver退出1，原件及失败保留。随后独立读取原队列行与原证据完成核验，
0.592814秒，不重新执行请求；确认原拒绝、全部原件与cut保持。
这证明实际拒绝边界，不是独立审查、统计或候选准入通过。

后续完整审查必须使用事前冻结、与实际对应输入绑定的方案和完整
试验历史；独立结果与来源信任仍须由受控审查方提供。本地代理未
签名、安装trust或制造人工批准。策略表现不合格时应真实淘汰，
不能为完成系统验收改成盈利或准入通过。

## 原件位置

开发证据目录：
`gold-project-closure-20261003-3pr5mrly/gold-real-prefix-queue-20261006`。
保留外部 driver、进程起点、冻结输入、原登记/队列DB、native archive、
初始与恢复项目cut、全文件摘要、完整报告事实与各进程终态。
审查拒绝另保留 review-readiness.py/json 与
verify-review-readiness.py、review-readiness-verification.json。
原采集仍在 `gold-quote-continuation-20261006/monthly-observation`。
未部署、切宿主服务、写保护状态、安装实际trust、读取凭据或发单。
