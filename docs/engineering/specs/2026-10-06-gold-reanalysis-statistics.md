# Gold BACKTEST 原统计评审与签名消费接线

本增量基于干净 `021c44fa430894cc366f955e5eb459910ac930a5`，沿用
[执行前评价绑定](2026-10-06-gold-reanalysis-evaluation-core.md)。关闭既定 F1–F5
研究证据消费链的接线缺口，不新增策略、阈值或收尾条件。旧通用 Technical
统计序列方法仍拒绝 Gold 输入；新完整项目评审走 Gold 原账户消费者。

## 结果与边界

新 `QUANTOS_GOLD_REANALYSIS_STATISTICAL_POLICY_V1` 绑定原 Core 的完整政策
SHA、当前 consumer、原封存 manifest、输入、训练/验证网格、候选原定义、
原基准账户声明与登记时刻。共用原政策校验内容，旧公开 Technical 校验入口
的 schema/方法不放宽。Gold 完整评审只接受原 ROBUST profile，不能以 PRESCREEN
降低 DSR/PBO 标准；旧 Technical profile 保持。可信离线准备先保留原政策文件，再创建原 native plan；
已有 Core plan 缺失原政策时，禁止事后补政策。queue 不运输政策或来源 pin。

完整项目评审沿原 Gold 报告、分块读者、原资金费收据和唯一现金账本读取原
候选账户及无下单基准账户，调用原 DSR/CPCV/CSCV 与经济比较计算，没有新
P&L 计算。基准账户不是新试验，全部真实登记尝试仍计入 `n_trials`。其他历史
保留在完整 cut/报告原件中；缺少可比较的全局方差时，DSR 明确为 INSUFFICIENT，
不显示局部矩阵结果为全局显著性。样本、选择、原政策、完整 cohort、报告
内容及实际信息区间都重新核对。

有 locked OOS 声明时，统计使用执行前冻结的连续 train/validation 网格。
原完整现金和风险记录仍是原执行范围，不能伪造样本末端清仓。信息区间至少
覆盖 Core 声明及原完整 quote episode/资金费收据可知时间，不以一个统计间隔
代替整段依赖。当前短样本仍缺 30 天与有效 CPCV/PBO 条件；不减 embargo、
持续时长、DSR/PBO 阈值或原计算预算来取得 PASS。

新 BACKTEST cohort/report 显式版本化。原统计证据准备/重算、完整 V3 来源
材料导出、受控 trust 的签名消费和冷数据库复读接收新版本。新增生产者
`REGISTERED_GOLD_REANALYSIS_RUNNER_V1` 仍需实际 trust 明确允许，不安装或
替换用户 trust。真实独立签名、时间与 OOS 访问证明仍未完成；自动生成的
测试签名不能替代它们，资格、经济准入、晋级及下单保持关闭。

控制台 TypeScript 增加显式报告版本；对应 API 合同案例读取真正导出的完整
公开 packet，保留 snapshot/只读/非权威标记、完整计数与缺证据状态。没有
服务启动、部署、宿主服务切换或保护状态写入。

## 实际验证过程

新原队列/政策案例实际 16 FAIL，18.81 秒、driver19.011314398 秒；旧可信准备
拒绝 `statistical_policy` 参数。初步实现后 16 PASS，25.29 秒、driver
25.476949648 秒。新增混合旧历史、真实源码导出/合成签名消费及冷库复读后，
17 PASS/1 FAIL，34.44 秒、driver34.689110059 秒；消费器的原生产者白名单
遗漏新生产者。单独定位 1 FAIL/17 deselected，5.50 秒、driver5.698513799 秒，
导出/源码身份断言实际通过，失败在消费器名单。

补齐名单及资金费收据信息区间后的原无资金费案例暴露 `max` 空展开错误，
6 FAIL/12 PASS，30.70 秒、driver30.904566699 秒；保留失败后修正空收据处理。
随后 18 PASS，54.71 秒、driver54.957170108 秒，3138 Python 摘要 `3abbb97e`
前后保持。实际来源导出、原签名函数消费、冷库一次 evaluation/audit、零
search_evaluations 和原 native 文件物理不变均通过；签名键仍为合成测试键。

之后新增实际 episode 不能由短 Core 缩小的反例，以及控制台完整公开 packet
合同案例。八组兼容批实际 99 PASS/1 FAIL/116 deselected，883.08 秒、driver
883.664671315 秒；3138 Python 摘要 c7fd0ec3 前后保持。唯一失败为新增 Console
断言误以为仅有一个 snapshot ID；原 middleware 合并请求和原数据集两个标识。
保留生产行为，修正为断言完整双标识、顶层 null、质量标识与原 packet 一致。

新增有效 PRESCREEN 政策不能进入 Gold 完整评审的反例，实际 1 FAIL/19 deselected，
1.90 秒、driver2.097101423 秒；原接受该较低门槛。新 Gold 校验显式拒绝后，
Gold 全部案例、原 Technical 政策和新 Console 合同共 52 PASS/116 deselected，
64.39 秒、driver64.782767477 秒，3138 Python 摘要 dd6131dd 前后保持。原失败、
日志、XML 与源码前后摘要保留，各批不能累加或混称同版完整发行门。随后
仅移除新增测试未使用的 import；完整 Ruff、全 E9/F821、九原文件父/当前
108 条样式诊断无新增、原图20域52边零环、卫生/diff/新链接均通过。

前端精确锁 `73c957e79856ce33264e204374a4017f60c64012c1db52b64443b92d34cfde6c`
与冻结原发行相同；复制已安装依赖到本轮专用 RAM，不改原依赖/锁，不升级。
实际 `npm run typecheck` 和原 `npm run build`（仅 outDir 到本轮 RAM）退出0。
Python/依赖仍使用原隔离3.12/aarch64环境。磁盘新增构建余量预检不足，拒绝
磁盘依赖复制；原预留要求不改，转而保存已终结纯合成兼容夹具的完整字节和
九字段路径元数据后紧凑存储。该存储不是现场恢复资格；未触原公开采集、
真实业务/实验记录、运行中验收、发行 Source 或原报告/失败日志。

紧凑存储已终结的原 `source-reanalysis-compatibility` 合成夹具：18973 文件、
750086989 原字节、795201536 原分配字节；九字段路径元数据与字节先完整
RAM/DISK 双验证，再逐批持久化 intent、复读原摘要/物理字段后移除松散副本。
archive SHA 为 `1e1b1c94ef1f0447879f73aae537fcad21fc99515aa124b0b93e648857de6ea3`，
index SHA 为 `8353c2cbb393559f593fdf874797c6a9ea717fff4eadb90abb79ec80c521ebda`。
前两次预检因无关 /proc cwd 权限不可查及瞬时退出 PID 终结，未保存/移除原
夹具；不提升权限，回到原终态/本轮进程核对，并保留失败。最终 exit0，
全部 Source、原报告/日志及非夹具记录保持，不授予现场恢复资格。

## 完整交付仍待证明

最终同版完整发行门，长期因果输入、独立来源/结果/时间、实际成本风险与锁定
OOS，现场全部 writer 同 cut 现金恢复，以及获准 Demo→现金反馈→再研究及
原四类结果仍按既定四组验收，五秒链和实际24小时不变。Windows 实际 CheckOnly
报 KEY_CUSTODY_UNEXPECTED_PRINCIPAL：用户交回 ACL 元数据，目录符合要求，
私钥文件额外显式允许 Administrators（S-1-5-32-544）完全控制；owner 仍为用户。
已准备仅备份 ACL、移除该条授权并执行原自检的命令，未读密钥内容、改原
签发器、重建密钥或在 PC 执行。PC 修复/自检通过结果待回，实际 trust 未安装。
原 `6b227509db9bc0734255cc2dd7cebd6eedb26b46` 同版完整验收仍在运行，ARM64
已通过，canonical 后端阶段实际 TIMEOUT（原3600秒），后续阶段继续，不能
借作本增量全门成绩，也不能记完整验收 PASS。目标 ACTIVE，
本批 PROGRESS；未触实际私钥/交易凭据、下单或升级经济准入。


## 冻结、整合与最终定向证据

独立提交 `cbf78facb68d8ac40f497ea2a201f1fefea9de58`，父021c44fa，仅13路径。
新文件源权限先保存原元数据，仅本轮新 contract/test/记录收紧；Main权限不改。
原hook0644实际以sh和验证Python PATH执行，791扫描文件零违规，无跳过。
精确DGX基线 `/tmp/quantos-dgx-development-7gk1rrti/baseline.json`，仅环境，
没有--test。最终3138 Python摘要 `b99e142eab6451c24b98344ec87cf62bb861db2e02e2f8068ac810323995ec07`。

冻结新HEAD的实际来源导出/合成签名消费/冷库案例 1 PASS/19 deselected，
24.90秒、driver25.139742177秒；原runner/多资产治理与新Console合同21 PASS，
30.58秒、driver30.924401269秒。两批同摘要前后保持；不累加为全门，
合成签名不是实际独立核验。前端typecheck/build原通过如上。

1531未改运行Python与Main相同，九现有Python及TS父像精确核对和备份后，
只整合13路径。Main8003→8005，仅九改二新，原HEAD913325cc/index95b9d9f1、
其他Python保持，不暂存/提交。Main四组实际70 PASS/2 FAIL/116 deselected，
64.74秒、driver65.068744730秒，8005 Python摘要d8fb27ce前后保持。两个失败
均为实际导出读取Main旧源码的RESEARCH_EVIDENCE_FILE_CUSTODY；只读实查
1400应用Python及锁，1327现有文件不满足原权限条件（首个quantos/__init__.py
为0664）。冻结Source同两条消费者链通过；不chmod Main、不放宽读者、不
跳过失败或把Main测试记绿。源码权限诊断、原失败日志/XML及所有读集合留存。

本批独立源码可审阅，当前脏工作区不能作为受控发行来源。完整发行仍待最终
同版验收；原6b后端TIMEOUT及后续在途保持，Windows修复结果/真实独立核验、
锁定OOS和原现场闭环仍待证明。目标ACTIVE，本轮PROGRESS。
