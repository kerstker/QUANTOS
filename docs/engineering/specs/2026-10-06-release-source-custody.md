# 完整验收源码权限准备修正

状态：DEVELOPMENT_VALIDATION_ONLY。延续[完整门验证域覆盖](2026-10-06-canonical-validation-coverage.md)，
固定[F1–F5交付范围](../plans/2026-09-28-ai-factory-closed-loop.md)保持。
本批没有修改生产或测试 Python，候选仍为
`6b227509db9bc0734255cc2dd7cebd6eedb26b46`；修正本机验收副本准备。

## 已确认的问题与原记录

前一同版验收已完成 ARM64 原构建、扫描和只读冒烟，canonical 后端随后在
完整报告导出、签名消费与控制台源报告等入口出现 ERROR。不能据此称完整验收通过。
独立干净副本中的原报告导出案例实际复现 `RESEARCH_EVIDENCE_FILE_CUSTODY`：
1 ERROR/5.87秒，driver6.096807426秒，3134 Python源码前后保持。
两个副本的 services/quantos/tools 共1393 Python文件均为0664，受实际当前UID所有、
单链接；创建源码副本时继承组可写权限。之前验收准备漏检了这一既有读者要求。

只在新的独立诊断副本先保存4921路径的权限与文件摘要，再去除组/其他写位；
所有文件字节、UID、链接数、所有者权限及执行位保持。没有更改原冻结验收副本、
Main源码权限、源码读者、安全门或保护运行目录。
原完整报告导出随后1 PASS/9.34秒，driver9.543386028秒；
原读者拒绝边界、budgeted Source签名消费者/冷恢复、streamed history签名消费及
控制台完整导出联合24 PASS/1 warning/298.37秒，driver298.778191519秒。
两次绿例的3134 Python均前后保持；签名案例仍为合成隔离证据，没有真实独立核验。

已确定不能通过的原本机验收先持久化取消意图，再核对PID/start、父链、当前UID、
本轮cwd及Python命令，仅SIGTERM原canonical及三个所属后代。首轮只读检查发现
嵌套pytest/资源跟踪进程，未发信号；完整树复核后才执行取消。原协调器自然报告
canonical -15、INCOMPLETE，外层1/1575.460269783秒，3135 Python保持；
协调器和四个目标均实际消失。原日志、源文件和原ARM64制品保留。
分类为CANCELLED_CONFIRMED_SOURCE_CUSTODY_PREPARATION_ERROR；后端没有完整终态，
execution尚未开始，不归类为自然超时或完整门PASS。

## 同版新验收

新独立源码从同一完整提交以umask022、无硬链接克隆，未改源码字节或应用依赖。
1393运行文件与前版逐字相同；原前端锁SHA73c957e7，离线npm ci 456包退出0。
3135 Python冻结（含Node成员1）；3134应用Python完整摘要仍为
`0ede7dd2d9f091a950a54280dc6e0f96bd8ea43132e46fa2e08477318a28ac3c`。
精确DGX环境基线 `/tmp/quantos-dgx-development-d_pm5x97/baseline.json`，未运行--test。

新协调器先调用原 `_source_files`/`_source` 和拥有读取切面的 `_ProjectReads`，
逐项检查整个源码/锁文件身份及原物理权限后重验；失败则不启动昂贵发布阶段。
原ARM64→canonical ALL→保留顺序、参数及3600秒上限保持。
其余五驱动中四个与前版逐字相同；capture只换实际公开DNS地址，归一AST相同。
新协调器移除单个新增预检语句后的AST与前版相同。TLS主机与原校验保持。
启动free447893086208字节满足原数据预留412451078144+28GiB30064771072，预算未缩。

本轮新验收目录 `gold-release-acceptance-20261006-source-custody`，
session4232/coordinator2064790/start35412710。源码权限、服务图、持久化预检均退出0；
ARM64原SOURCE_PASS后正在扫描/冷构建，canonical ALL尚无终态。
原件位于 `quantos-dev-output/gold-project-closure-20261003-3pr5mrly`，各批分开保留。

Main只新增本记录及项目状态，不暂存/提交；HEAD913325cc/index95b9d9f1保持。
固定长期因果输入、独立来源/结果/时间、真实成本风险、锁定OOS、现场全writer同cut
现金恢复、获准Demo→现金反馈→再研究、五秒链及实际24小时条件仍需完成。
Windows11 x64签发包已交接，待用户PC公开CheckOnly结果，见
[签发接入记录](2026-10-06-independent-review-windows-onboarding.md)。
目标ACTIVE，本轮PROGRESS；未部署、切服务、写保护状态、安装实际trust、
读取实际私钥/交易凭据、下单或改变经济准入。
