# DGX 研究优先与 Claude Code 开发委派

状态：用户已授权调整交付优先级；本文件记录设计决定，机器评估接线待实现。

## 目标与授权

2026-09-20 用户授权暂缓 TOP22 的机构级要求，优先在单台 DGX 完成：
策略来源检索 → 可复现的成本后回测 → 冻结前向观察 → 有界测试网验证。
当前研究进度以该闭环评价；TOP22 保留为长期能力与历史证据，不再作为研究迭代的总完成率。
本决定覆盖旧路线中要求先完成异机恢复、外部备份或全部机构设施才开展研究的工序。
软件发布、测试网执行、经济证据分别记录，研究评级不隐式签发订单权限。

Codex 负责架构、重要缺陷、策略来源核查、实验设计、集成审阅和验收。
Claude Code / DeepSeek 负责有明确文件范围与验收标准的实现、测试、文档及小修复。
当前终端的 P0–P2 工作流先完成在途修复和验证；不同时在其文件范围启动第二个写入者。
后续每张任务单应包含允许修改路径、输入与输出合同、测试命令和停止条件。
独立任务可以并行；共同接口、账本和评分合同由主审阅者串行集成。

## TOP22 的研究阶段处理

以下为新研究视图的映射，不修改旧分数，也不把延期标成 PASS。
研究视图必须独立验证现行实现，不能将旧 paths_exist 或文本标记检查视为行为通过。

| 原要求 ID | 研究阶段要求 | 延期范围 |
|---|---|---|
| DATA.PIT.CONTRACT | 决策时可知性、迟到修订、样本对齐回归 | 无 |
| DATA.VENDOR.DEPTH | 当前实验输入来源、可用时间、质量可核查 | 商业 tick/盘口合同与 SLA；依赖盘口的策略仍需相应数据 |
| ALPHA.TRIAL.GLOBAL_LEDGER | 全部试验含失败、预算、候选与数据版本留存 | 无 |
| ALPHA.MULTI_ASSET.RESEARCH | 先在明确限定的市场完成闭环 | 全资产覆盖 |
| VALIDATION.ANTIOVERFIT.GATE | 训练内选择、锁定 OOS、多重尝试控制 | 无 |
| VALIDATION.CHALLENGER.MODEL_RISK | 独立复算、简单基准与证伪测试 | 机构评审身份设施；模型无效可得出合格否定结论 |
| PORTFOLIO.CONSTRAINED.OPTIMIZATION | 有限仓位、现金、杠杆、换手及集中度 | 全机构跨 book 配置 |
| PORTFOLIO.COST.CAPACITY.LOAD_BEARING | 成本与容量实际影响可执行持仓 | 超出当前市场的校准 |
| RISK.FAIL_CLOSED.GATES | 非有限值拒绝、损失上限、未知即停 | 无 |
| RISK.RECOVERY.DRILL | 同机中断重启、不重发、不重复计账 | 机构长期 SLO |
| EXECUTION.DRYRUN.RECONCILIATION | 唯一 V2 账本，订单/成交/费用/现金守恒 | 旧 V1 继续隔离 |
| EXECUTION.MULTIVENUE.SOR | 单场所仿真/测试网口径一致 | 多场所智能路由和生产 TCA |
| AI.AGENT.NONHUMAN.BOUNDARY | AI 无自批和真实交易权限 | 无 |
| AI.MODEL.REGISTRY.REDTEAM | 提示/模型/工具/调用身份、越权回归 | 外部服务商与机构级评审设施 |
| GOV.APPROVAL.HASHCHAIN | 实验与测试网授权可追踪 | 无 |
| GOV.IDENTITY.STRONG_AUTH | 本机操作员与研究进程权限隔离 | 外部 OIDC、mTLS、机构多方身份部署 |
| PLATFORM.CI.UNIFIED_GATE | 每批专项测试；里程碑完整验收 | 不要求每个实验重建全部发布制品 |
| PLATFORM.SUPPLY_CHAIN | 固定依赖、适用漏洞检查、源码身份 | 异机可复现构建、外部 WORM 与独立签发基础设施 |
| OPS.BACKUP.ENCRYPTED | 保留原始输入和实验记录，输出可重跑 | 外置加密备份不阻塞单机研究 |
| OPS.RESTORE.DRILL | 同机任务恢复和断点验证 | 异机恢复、30/90 天历史与整机灾备 |
| FRONTEND.ROUTES.SINGLE_SOURCE | 一个可靠研究入口，合同测试 | 非关键页面与视觉完善 |
| FRONTEND.EVIDENCE.SINGLE_SOURCE | 来源、失败、成本、状态和未知值真实展示 | 无 |

延期项在进入对应生产范围前重新评审；保留现存备份与监控，不停止服务或删除记录。
旧矩阵的 PASS 只代表原检查器结论。尤其 PIT、执行和容量须按当前修复版本重新核验。

## 当前验收的六个维度

1. 数据可信：市场范围、数据摘要、时区、available-time、缺口和修订明确。
2. 回测正确：V2 逐笔账本，手续费、资金费、滑点、容量、保证金及平仓规则实际生效。
3. 实验有效：先登记假设、试验预算、训练/验证/锁定 OOS、基准、退出与淘汰规则。
4. 盈利证据：冻结 OOS 成本后净收益为正只是必要条件；同时检查基准差额、回撤、交易数、有效样本、成本压力与跨期稳定性。
5. 前向与测试网：冻结信号先记录后评价；测试网逐单回执与现金核对，独立记录测试网净收益和基于真实公共行情的成本校准结果。
6. 研究效率：记录冷/热回测耗时、峰值内存、数据缓存命中、失败率和恢复耗时；根据基线设性能预算。

工程有效性与策略盈利分开评价。零成交、无持仓或否定一个坏策略可以是正确实验，不能标成盈利策略。
测试网撮合、深度和费率可能不同于真实市场；测试网盈利不等于真钱市场可实现盈利。
所有数值门槛在运行前登记；缺数据、缺成本或缺足够样本为 UNKNOWN/INSUFFICIENT。
改变策略须创建新身份和新评估计划，不能反复查看同一个 OOS 后调参直到通过。
公开论文也只能提供待验证假设；不得用论文宣称的收益代替本地复现。

## 分批委派和完成条件

- RF0（当前）：记录授权、TOP22 映射、团队分工和候选筛选纪律。
- RF1：在现有评分系统增加研究范围视图。输出证据状态与延期原因；不可将延期自动折算为通过，不修改旧矩阵历史。
- RF2：确认当前 P0–P2 修复已验证，接通一个冻结公共数据样本的 V2 回测入口，记录成本、持仓和逐事件对账。
- RF3：接通有预算的候选队列，输出所有候选（含失败）的同口径比较；先选一类简单基准再评估复杂方法。
- RF4：满足预登记条件后生成测试网评估包，复用现有人工授权与限额入口，运行前核查场所和账户范围。

RF1 建议先通过 CLI 输出，随后将同一后端合同接入前端；控制台不能另算分数。
RF2 的真实风险预算不得复用用于历史兼容的 1e7 杠杆/参与率占位 profile。
发布门仍检查证据生成正确性；TOP22 未达机构目标不等于研究实验不能执行。
当前源码并发修改期间，委派必须先报告文件所有权，避免更改正在修复的 validate.py 等共享文件。

## 首批策略来源候选（尚未回测）

- [Deep Momentum Networks](https://arxiv.org/abs/1904.04912)：先复现传统动量与波动率缩放基准，再考虑神经网络。原研究为期货，向加密资产迁移的有效性未知。
- [Machine Learning-Based Bitcoin Trading Under Transaction Costs](https://arxiv.org/abs/2606.00060)：重点验证预测到仓位的成本阈值和换手约束。当前只检索摘要，全文方法、泄漏和费用口径须先核查。
- [AdaptiveTrend](https://arxiv.org/abs/2602.11708)：候选方向为趋势与波动率缩放。其资产选择/滚动评价需要重点检查幸存者偏差与训练内选择。当前只检索摘要，论文收益尚未复现。

已有仓库中被证伪的同类方法先做去重；无新增机制或独立数据不重复扩搜。
本批无新回测、盈利结果、测试网订单或常驻搜寻进程。

## Claude Code 执行说明

官方 [ultracode 文档](https://code.claude.com/docs/en/model-config) 指出它结合 xhigh 与动态工作流；max 环境变量会覆盖该功能。
用户级配置启用 ultracode 并将 effort 调整为 xhigh。DeepSeek 不在 Claude 原生模型能力列表中，命令接受与 API 成功不能单独证明自动工作流确实启用。
使用独立、有时限的 CLI 调用验证委派，不复用或并发恢复正在写文件的交互会话。
大型文件互斥任务使用工作流；小任务单代理即可。每批须由 Codex 审阅实际 diff 与测试证据。

## 本批执行记录

- 独立 CLI 审阅调用成功返回，未开放文件或命令工具。
- 用户级配置已设 ultracode=true、effort=xhigh，JSON 校验通过；DeepSeek 返回 unrecognized_model 提示，未据此声称自动编排已验证。
- 通过 ListAgents/SendMessage 向本机唯一同工作区交互会话 quantos-backtest-runtime-integration-a0 [52ff32] 发送一次协调任务；消息编号 2b4bd463-5bc0-4cfd-847d-e670fdfc92f9。发送方报告 queued，尚无接收确认；不同权限模式可能触发接收端批准，不能将排队称为已开工。
- 初次消息要求先收口 P0–P2，再申报 RF1 文件范围与计划；后续独立委派仅占用三个新文件，未接管交互会话的修复文件。
- 仓库文档卫生检查 PASS，已有跟踪文件的本批 diff 空白检查通过。本次没有运行完整软件验收。

### RF1 独立开发与复核

- Claude Code 会话 `9a97c1c3-6876-4c5b-8db3-f05977008f87` 完成三个新文件：`config/research_first_scope.json`、`tools/research_first_assessment.py`、`tests/tools/test_research_first_assessment.py`。协调消息 `977a72dd-ac44-48f0-b0eb-333b65afc44d` 返回 queued，仍不是接收确认。
- 22 项逐一映射为 7 项研究核心、9 项缩小适用范围、6 项机构要求延期；9 项适配项只延期其扩大范围，不豁免当前实验所需的数据、成本、容量和风险约束。
- CLI 为只读范围投影，不修改历史证据或交易门。六维均为 UNKNOWN，整体 RESEARCH_EVIDENCE_INCOMPLETE：目前没有接入逐次实验的证据合同，不能把该工具当作自动验收器或已完成的前端界面。
- Codex 审阅实现并独立复跑新工具测试及 `test_perpetual_replay_research.py`、`test_btc_factory_replay.py`：35 passed in 6.70s。使用已验证 Python 3.12、禁用字节码和 pytest 缓存；这不是完整验收，也不证明真实策略盈利。
- RF2 只读接口审阅定位到 `tools/perpetual_alpha_research.py::load_data/run_alpha` 和 `services/research/perpetual_alpha.py::replay`；现有输出显式为 TECHNICAL_VALIDATION_ONLY。尚未核验本次真实输入完整性、冻结实验身份或运行新策略。下一步必须先完成这些检查，避免将测试夹具或已反复观察的历史区间冒充独立 OOS。
