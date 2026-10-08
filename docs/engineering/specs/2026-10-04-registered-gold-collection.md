# 黄金采集程序进入原登记库

承接[原登记研究入口](2026-10-04-registered-research-product-entry.md)和
[原项目独立评审入口](2026-10-04-registered-project-review-product-entry.md)，
继续推进[完整工厂目标 F1–F5](../plans/2026-09-28-ai-factory-closed-loop.md)。
父源码为 `4c03ad1751c971d5cdfb770bf57b1eea08a0200b`。
本记录描述开发增量；完整系统目标保持进行中。

## 原问题与实际交接

原登记入口只消费可信 producer 已准备的运行。黄金 AI／采集程序仍直接
创建独立诊断归档，因而不能提供原项目的完整试验人口。

新黄金程序默认在实际计算前调用原 `prepare_gold/run_gold`。原 AI 提案、
新窗口采集、冻结请求和原 JobQueue 不变；请求 plan 最后发布，并保留
`register_original_trials=true`。反馈实验的有界子进程也进入同一路径。
CLI 的 `--register-original-trials` 明确表达当前默认行为。可信 Python
调用者显式 False 仅保留原独立诊断路径，历史材料不补写、不导入新登记。

三候选与可选第四个共享账户组合使用原 Gold 条件订单、止损及
LinearPerpetualSimulationSession；没有转换成另一种目标仓位策略。
共同项目、原 SqliteExperimentStore、原 trial ledger、provenance 和
invocation/outcome 状态由已有实现持有。全部原尝试在 producer 计算前登记，
失败和未知中断保留。原现金、成本及风险公式不复制。

同一 attempt 的 invocation 由固定域和编号确定；输入变化不能分配另一个
原运行来覆盖历史。发布失败恢复消费同一原报告；报告未落盘的在途调用
保持 UNKNOWN，不再次调用原 producer、不添加试验。既有确定性诊断复算
仍保留，不算新的原登记试验。已失败原运行不变为新成功。
自动恢复资格从完整原项目、原登记、原输入和 durable report 核验，
不从旧镜像的 running.json 推断。

## 兼容结果与只读证明

原受管目录为 `data/cache/registered_research/project/runs/<id>/`，
登记库为 `data/cache/registered_research/registry.db`。
旧黄金结果目录只接收原 input/report/manifest/trials.db 的精确字节副本，
并保留 `registered-native.json` 标记。原试验编号保持，支持的恢复入口
回到原运行；不向原受管 archive 加入旧独立核验请求或另一套账本。

留存投影读取固定相邻的原项目与登记库，通过原 ProjectResearchEvidenceReader
保留完整人口，再以同一物理读集合核对镜像、原计划、原报告及原 ledger。
镜像或原件变化不修复、不借自称登记状态通过。结果 metadata 只取原计划，
原程序留存验证沿同一结果投影；适配器与只读 HTTP 检查精确字段、
原试验数量/编号及固定缺失状态。TypeScript 与结果页同步展示。

## 资格与剩余完整目标

本批是计算前试验登记，不能当作评估核心预登记。原 Gold 是 FORWARD
适配器，现有 V3 技术评估的直接目标仓位策略不能代替它。
输出明确：评估计划 NOT_PREREGISTERED、统计 FORWARD_ADAPTER_UNAVAILABLE、
风险 NOT_INDEPENDENTLY_REPLAYED、锁定 OOS UNKNOWN、候选 NOT_APPROVED。
经济、晋级及下单权限均 false；旧 V1 隔离和默认 authority 关闭保持。

Gold 统计/风险适配、独立锁定 OOS、真实成本风险与候选准入仍须实现；
完整 writer 一致性停写、完整跨角色业务/现金对账、最终同版 ARM64/canonical
全门、获准现场安装/升级/恢复、五秒链和实际 24 小时成交—现金—再研究
继续验收。其他市场单列，机构异机设施延期不记 PASS，盈利策略研发单列。
没有部署、切服务、写受保护状态、安装现场 trust、使用真实凭据或下单。

## 验证记录

原始失败与每版冻结摘要、日志/XML、提交及选择性整合记录留存在
`gold-project-closure-20261003-3pr5mrly/registered-gold-automation-0sc56ore`。
初始 bridge 缺模块、程序缺参数、只读读取限额不一致、采集夹具源码错配、
HTTP 自称资格及结果页缺状态的失败均保留。原报告 durable、outcome 尚未
发布的实际程序回归先失败；默认程序仍未登记的反例也保留。

测试行情、模型 transport、源码身份和时钟为隔离输入；原提案编译、
程序请求、登记事务、原生产者、账本、队列、发布与恢复被实际执行。
没有以夹具完成证明真实市场持续运行或独立经济来源。
初步联合 167 PASS／1 SKIP；随后兼容组 279 PASS／14 FAIL／1 SKIP，
失败为旧简化 source 的程序夹具没有显式选择原诊断路径。两处旧夹具保留
原格式，并新增完整五字段源码身份的程序冷读关系及原件字节不变检查。
第一冻结组合 374 PASS／1 SKIP／231.43 秒。随后仅清理一条新增格式诊断、
明确保留诊断复算的文档字符串，并重生成服务图；最终冻结组合
374 PASS／1 SKIP／231.88 秒，driver 232.520 秒 exit 0，
3044 个 Python 文件运行前后相同。既有 IC panel 快照缺失跳过保持，
重叠与不同冻结检查不累加称为完整门通过。

数据、持久化及开发入口另 17 PASS／1.97 秒，3044 Python 前后相同；
首次将开发入口测试路径误写为 governance 的收集错误日志保持。
前端三文件 15 PASS／1.48 秒、typecheck/build 通过。新增 Python 全 Ruff、
全仓 canonical F821/E9 通过；修改文件 196 条原诊断与父版一致，无新增。
服务图首次 drift 保留，按原生成器更新后 20 域52边、无循环且 current；
文档三链接、diff 空白及仓库卫生通过。

主区初次整合预检核对十五父前像、四新路径不存在，1154 个递归依赖
（包括嵌入 Python 与前端源/配置）字节一致。提交与最终预检、原件备份、
选择性整合及主区验证另留存，主区其他编辑保持。
最近完整门仍为旧 5ae 版 30 PASS／1 FAIL／1 TIMEOUT。
