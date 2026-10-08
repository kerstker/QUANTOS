# Gold BACKTEST 执行前评价绑定

本增量基于干净 `bf46be0e769c67bfd2587d672152459accd77dd4`，延续
[原始输入跨版本复算](2026-10-06-gold-source-reanalysis.md)。属于既定完整交付
F1–F5 的研究证据接线，不增加收尾条件。旧 Core V2 只允许 Gold FORWARD，
跨版本 BACKTEST 因而无法在原 queue 执行前冻结评价计划；完整项目读者还把所有
带 Core 的计划当作 Technical 输入读取。

## 行为与边界

- 新 `QUANTOS_GOLD_REANALYSIS_EVALUATION_CORE_V1` 只允许原 Gold 适配器的
  BACKTEST；原 Core V2 及其 FORWARD/Technical 配对保持。
- 注册计划 V3/V4 分别对应普通及预算化来源。可信离线
  `prepare_source_reanalysis(..., evaluation_core=...)` 绑定当前 consumer、原封存
  manifest、完整输入、登记时刻、完整 cohort、预算与 Core SHA。Core 在 plan、
  registry provenance 与原 PLANNED/RUNNING ledger 之前冻结。queue 参数仍只有
  invocation SHA，不传入可替换的评价声明。
- 原 Gold 来源和项目读者验证数据集绑定；完成的普通回测不能事后追加 Core。
  冷恢复继续读取原结果，不能重新执行。
- 原 facts 投影通过原报告、资金费原件和唯一现金账本读取新 BACKTEST。声明了
  locked OOS 时，评价统计仅消费连续的 train/validation 网格；缺失训练/验证窗、
  中间空隙、重叠及不齐网格在登记前拒绝。没有 OOS 声明时保留原声明 sample 并
  返回缺证据。整次现金对账和风险记录仍对应原完整执行，不伪造截断后的现金。
- 通用统计序列入口尚未支持 Gold BACKTEST。本批明确返回
  `INSUFFICIENT / GOLD_BACKTEST_STATISTICAL_ADAPTER_UNAVAILABLE`，没有曲线、
  选中 simulation hash 或 READY；避免沿用旧 FORWARD 分支及缺少分块 material
  的异常。原 Technical 统计和原 Gold FORWARD 行为保持。

这只实现声明的执行前绑定和指定统计窗，不证明独立登记时间、来源或结果，不
独立认证 OOS 边界/访问历史，也不完成候选选择隔离、完整统计 study/CPCV、
真实成本校准或九引擎风险核验。底层执行仍读取原完整研究输入；排除 OOS 的
统计窗不能替代独立封存或完整选择隔离。OOS 状态只会是 UNKNOWN/INSUFFICIENT，
经济准入、晋级和下单始终关闭。测试中的 OOS 标识及历史摘要都是合成夹具。

## 验证记录

全部测试使用隔离 Python 3.12/aarch64 环境，日志、XML、源树前后摘要留在
`gold-reanalysis-core-20261006`。首次错误 fixture 导入的 13 ERROR 保留；纠正后
实际 13 FAIL 证明旧 API/合同不支持新 Core。首两轮绿化分别暴露测试跨线程
SQLite 连接与实际项目读者错误的 Technical 绑定；均保留原失败，并修复各自
责任点。原 13 项最终通过，随后扩展为 20 项。

新增覆盖原 queue 登记顺序、两存储版本、caller 变更隔离、全部 cohort、统计窗
排除 OOS、原现金 residual 0、原读者/冷恢复与物理原件不变，以及七种身份错配、
事后 Core、访问缺失/已查看、五种窗口错误和旧 Core 版本拒绝。通用统计入口
先因测试传了完整 row 而触发身份字段错误；纠正为原 identity 后，实际两项
FAIL 暴露 `PROJECT_CHUNKED_MATERIAL_REQUIRED`。新明确缺证据分支针对该失败。

修改统计 dispatch 前八组兼容实际 125 PASS / 2 deselected，82.86 秒，driver
83.191015244 秒；六治理组及原统计预声明窗口共 56 PASS / 1 warning，4.59 秒，
driver 4.783890557 秒，3136 Python 摘要 `483d7008` 前后保持。两个原 1001 长案例
未纳入兼容批；父版的新跨代码长分支已单独完整执行，不能作为本版全门成绩。
最终 dispatch 后十组兼容实际 157 PASS / 2 deselected，312.30 秒，driver
312.683191364 秒，3136 Python 摘要 `ec27d94b` 前后保持；包含原通用统计入口
与统计预声明窗口。首次末批误填测试路径退出 4、未执行测试，日志保留。随后
只拆开新增分号，AST 完全相同，最终 20 PASS，driver 26.714015002 秒，
3136 Python 摘要 `6b640400` 前后保持。此前
125 项与 56 项批次使用旧摘要，不能混称同一版本全门。

全仓 E9/F821、新测试完整 Ruff、原服务图 20 域/52 边/零环、卫生、diff 与
本记录链接通过。九份现有生产 Python 父/当前均 59 项已有样式诊断，无新增，
不称现有文件完整 Ruff 零问题。末批源码摘要及 hook、选择整合收据保存在本批
输出目录；Main 不暂存、不提交，用户其它改动不覆盖。

## 交付状态

本批尚不建立真实独立核验资格。Windows x64 签发包的实际 CheckOnly 公件待
用户交回，trust 未安装。父版真实四段复算与独立进程恢复已完成，但不能事后
赋予本批 Core，也不是 30 天完整输入或已锁定 OOS。

原冻结 `6b227509db9bc0734255cc2dd7cebd6eedb26b46` 的同版完整验收继续，ARM64
已通过、canonical 在途；本增量自身全发行门未开始。最终同版完整发布、长期
因果输入与独立来源/结果/时间/真实成本风险/OOS、现场全部 writer 同 cut 现金
恢复和获准 Demo 反馈链仍按既定四组验收，五秒链及实际 24 小时要求保持。
没有部署、切服务、写保护状态、读实际私钥/交易凭据或下单。

## 冻结与整合收据

隔离提交 `021c44fa430894cc366f955e5eb459910ac930a5`，十一条路径；Source保持干净。
最终 20 项 26.51 秒、driver 26.714015002 秒，3136 Python 摘要
`6b6404007eae8ffed8aeea701a2fa25260b4bc21b1d034e598af9f7490e138c3` 保持。
提交准备拒绝新测试/新记录的0664；先留存原元数据/摘要，再只准备这两个新文件
的只读来源权限，原字节/inode保持。实际原hook0644不可执行，以sh与已验证
Python PATH执行，790文件零违规，未跳过。精确DGX环境基线
`/tmp/quantos-dgx-development-zt4fem_5/baseline.json`未--test，仅环境证据。

整合前比对1530未改运行Python和九个替换父像，保存Main原像与preflight后
仅整合本批十一条路径；Main8002→8003，九改一新，其他Python/HEAD/index
保持，不暂存/提交。Main原queue/CoreV2/跨代码来源/统计窗口四组实际92 PASS，
46.12秒、driver46.343410013秒，8003 Python摘要
`f2499d3499332897149e7a4e90ee1975ecd0ef8928f2f53f692439bdd6e1fafc`前后保持。
父bf46真实四段执行/冷恢复不能事后继承本批Core；本批自身全门未开始，
原6b的完整验收继续且不能借为本版PASS。目标ACTIVE、本批PROGRESS。
