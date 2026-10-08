# Gold 原始输入跨代码版本复算

状态：DEVELOPMENT_VALIDATION_ONLY。承接[原生长输入预算](2026-10-05-gold-registered-long-budget.md)
与[完整门验证域覆盖](2026-10-06-canonical-validation-coverage.md)，固定
[F1–F5交付范围](../plans/2026-09-28-ai-factory-closed-loop.md)保持。

## 问题和实现

原 NativeSource 要求当前代码身份等于封存 manifest 的 release_identity。
长时间采集期间升级代码后，原数据包无法直接进入新版原生注册、账本和冷恢复。
原严格前向入口继续拒绝这种身份变化；复算通过新的可信离线准备入口进行。

`prepare_source_reanalysis` 显式接收原始数据包的代码身份，逐项核对原 manifest、
全部 blob、路径集合、字节预算和物理读取切面。原始输入不重写；新 input/plan
分别保留 collection_source_identity 和当前执行 source_identity，形成新的
BACKTEST、输入摘要、原生试验和注册记录。这个身份 pin 只是代码来源元数据，
不是独立来源核验，也不证明采集时可知性。

新增输入版本 QUANTOS_GOLD_NATIVE_REANALYSIS_INPUT_V1/V2 和计划版本
QUANTOS_REGISTERED_GOLD_REANALYSIS_RUN_V1/V2；V2绑定原显式 replay_budget。
新计划与新输入必须成对匹配，不能换成旧计划或标记 FORWARD；拒绝事后添加
forward_design。队列仍只接受 invocation SHA，不能运输路径、来源 pin 或回调。
当前执行代码在整个读取/执行切面内必须保持；原始数据包与原日志代码身份
必须一致，原资金费、初始现金、费用、组合限制与执行预算继续由同一账本处理。

新结果沿用原注册→队列→分段账本→完整报告→项目读者→冷恢复路径。
项目读者只对有界嵌套 source_binding 元数据进行展开，保持全部原始输入、
报告摘要与物理切面的核对。没有把长历史展平成一个内存对象。

## 验证记录

新增案例最初6 FAIL/2 PASS；补齐执行交接后的首轮2 FAIL/6 PASS，真实暴露
项目读者浅层 dict 不能序列化嵌套 lazy source_binding。两轮原失败记录保留。
修正有界元数据展开并补充原资金费现金流案例后9 PASS/20.67秒，driver
20.852786040秒。包含错误 pin、前向重标、预算不足、当前源码变化、原 blob
同字节重写、非选中原件缺失拒绝，以及注册、全部账户和无重算冷恢复。
资金费夹具实际 funding=-0.01、ending_equity=999.95、residual=0。

原1001分段完整案例保留全部断言，并参数化增加新版代码复算分支；没有缩小
输入、预算或读取范围。新增分支1 PASS/345.54秒，driver345.757623636秒，
四引擎全部1001环境片段、完整项目读者与冷恢复通过。3135 Python运行前后
摘要2bccd5ed保持。夹具代码身份与时间仍为合成测试，不是实际独立证明。

六组治理/研究边界40 PASS/1 warning/4.53秒，driver4.706724583秒，3135 Python保持。
七组原source、原注册、长预算短案例、完整原件评审/人口、原签名消费和新复算
兼容检查60 PASS/2 deselected/459.24秒；两个1001案例在这批显式排除，新版
分支已如上单独完整执行，原同版分支保留供最终完整门。3135 Python摘要
2bccd5ed前后保持；各批有交叠，不累计成完整门。
首次治理命令引用不存在的测试路径，退出4、未执行测试；原记录保留，正确命令
另行运行。全改动E9/F821及新增测试完整Ruff通过；十个既有文件17项原样式
诊断无新增。全仓E9/F821通过；原服务图20域52边、零环保持。新文档链接与
原仓库卫生通过。

原件位于 gold-project-closure-20261003-3pr5mrly/gold-source-reanalysis-20261006。
精确DGX基线 /tmp/quantos-dgx-development-bs8oq2tf/baseline.json 仅环境核对，未--test。
原6b完整发布验收仍在自己的冻结源码上运行，不能证明本增量通过完整门。

## 尚未完成的交付条件

本增量目前不接 Gold evaluation_core、锁定OOS或独立签名请求；结果仍为
TECHNICAL_VALIDATION_ONLY，经济准入、晋级与下单关闭。Gold OOS消费接线、
真正预封存/访问记录、独立来源/结果/时间、真实成本风险及长期数据仍待完成。
已有30天公开观察声明不事后改成 OOS；完整窗口结束只是数据节点。

Windows11 x64签发工具待PC公开CheckOnly报告，实际trust未安装。
现场全部writer同cut现金恢复、获准Demo→现金反馈→再研究、五秒链及实际24小时
仍是原收尾条件。目标ACTIVE，本批PROGRESS；没有部署、切服务、写保护状态、
安装真实trust、读取实际私钥/交易凭据、下单或改变经济准入。

## 冻结与开发工作区整合

代码候选已冻结为 bf46be0e769c67bfd2587d672152459accd77dd4，基于6b，十二路径。
实际sh原pre-commit（644、不可执行）以验证Python PATH运行，790文件零违规；
没有跳过钩子。精确DGX基线 /tmp/quantos-dgx-development-_a0zsb69/baseline.json
核对该完整提交、干净源码、ARM64/Python/Node/锁定依赖，未--test。

十个替换Python父像及1530个未修改运行Python与Main相同，备份后只整合十二路径。
Main专项39 PASS/2 deselected/72.51秒，driver72.731440581秒，8002 Python前后保持；
原长案例不在此短批，新增长分支的完整证据见上。Main HEAD913325cc/index95b9d9f1
保持，未暂存或提交，其他Python内容保持。候选原Source及提交仍干净，不改冻结版。

原四段真实公共输入正在新私有workspace、bf46当前版本下新登记并复算；
session55663/child2140896，尚无终态。原数据包身份与真正原公共采集器身份分列；
不会继承旧盈利结果，完整长期输入、独立控制、OOS、真实成本风险仍未证实。
本批对执行入口的观察计数有profiling开销，不作为性能对比。
原6b完整验收冷构建A已PASS、B在途；bf46自身完整发行尚未启动。

## 真实原件复算和新进程冷恢复（2026-10-06 16:59）

bf46当前代码实际重新登记并运行已封存四段真实公共输入，完整原队列、
四个原账户和项目读者通过。原生execute计数1；另起进程cold recovery计数0。
原结果 c3c8ee3dcdde56f03f2b7e3859a614d21253316654011331c575fd9f3be4320f
与项目cut 2b5e8153166178ac1df9cfcf5c2f319fad4db8f1cb48fefd4839d81bf19553e7
均保持，四账户residual=0。新原生文件字节、inode、mtime及mode保持，全部旧
数据包、原生记录、registry和历史结果未变。原数据包代码edfd、原公共采集器bf1
与当前执行bf46分别留存，未重写旧manifest或继承旧结果。

执行485.451147243秒（driver486.310088665），新进程恢复167.600349875秒
（driver168.413108649），均含profile计数开销，不作为性能比较。两个driver
均退出0，3135 Python摘要2bccd5ed前后保持，bf46源码/HEAD仍冻结干净。
原件为real-four-reanalysis-result.json、real-four-reanalysis-cold.json及各driver
日志/状态。本证明只覆盖原四段，不代表30天完整输入、因果可知性、锁定OOS、
独立来源/结果/时间、成本校准或完整风险准入；经济/晋级/下单仍关闭。
