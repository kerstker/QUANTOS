# 36 家机构与系统对标：QUANTOS 全生命周期开发基线

2026-09-08。起点源码 5e61466e787ed03dd1941c9fde022558f5e73afb。
本次新建明确的 36 对象清单：20 家机构、16 个量化或支撑系统；不是声称审计了 36 家私有机构。
国内相关开源生态是技术关联分类，不表示微软等主体的注册地。采用原始公开资料的相关段落，
未运行商业产品、未取得私有策略、未核验业绩。天演正文无法解析，仅有官方搜索摘要；
明汯等动态站点内容有限。信息不足时不给具体算法背书。

## 目标

让一次有市场、数据、风险与资源边界的配置，驱动来源研究、机制假设、特征/模型实验、
成本后评价、冻结观察、模型挑战与退役建议；最终接回原有 Paper/场所/实盘工序。
每个状态都有来源、版本、失败原因及可操作的下一步；没有信号时继续观察或停止消耗预算。

## 对标矩阵

| # | 对象 | 类型 | 可核实信息 | 择优采用 | 限制 / 不采用 | 工作包 |
|---|---|---|---|---|---|---|
| 1 | [幻方量化](https://www.high-flyer.cn/fund/) | 中国机构 | AI 训练平台与持续数据积累 | 数据与计算预算分离，追踪训练成本 | 不据官网规模推断本项目算力需求 | R1 |
| 2 | [九坤投资](https://www.ubiquant.com/website/about) | 中国机构 | 技术驱动研究，多类量化策略 | 机制家族与风险敞口分别登记 | 公开介绍不披露内部策略实现 | R1 |
| 3 | [明汯投资](https://www.mhfunds.com/) | 中国机构 | 公开机器学习研究入口 | 线性与非线性模型使用相同验证合同 | 主页仅提供有限方法信息 | R2 |
| 4 | [灵均投资](https://lingjuninvest.com/) | 中国机构 | 多因子组合与对冲产品说明 | 信号、组合、对冲分层 | 股票组合方法不能直接外推单 BTC | R5 |
| 5 | [衍复投资](https://yanfuinvestments.com/join) | 中国机构 | 收益风险容量权衡的研究说明 | 容量与风险预算成为候选属性 | 相关公开段落，非内部系统审计 | R5 |
| 6 | [诚奇基金](https://www.cqfunds.com/intro/) | 中国机构 | 量化投研与技术团队介绍 | 记录研究与实施职责及可追溯产物 | 信息不足，不据此增加具体算法 | R0 |
| 7 | [宽德投资](https://www.wizardquant.com/en) | 中国机构 | 特征工程、组合框架、市场冲击和训练部署 | 把成本建模接入预测与执行反馈 | 自述能力，未独立测试 | R4 |
| 8 | [天演资本](https://www.tianyancapital.com/) | 中国机构 | 数理研究与技术解决方案 | 研究与工程统一任务身份 | 正文解析为空，仅官方搜索摘要 | R0 |
| 9 | [Two Sigma](https://www.twosigma.com/about-us/) | 国际机构 | 科学研究与数据技术平台 | 来源、数据、实验、结果的统一谱系 | 只采用公开方法，不采用规模宣传 | R1 |
| 10 | [Jane Street](https://www.janestreet.com/join-jane-street/machine-learning/) | 国际机构 | 模型、训练推理基础设施与交易结合 | 训练和推理合同一致，单列运行表现 | 招聘/团队公开说明，不是系统规格 | R2 |
| 11 | [AQR](https://www.aqr.com/Insights/Research/Working-Paper/Machine-Learning-and-the-Implementable-Efficient-Frontier) | 国际机构 | 交易成本影响可实施组合 | 成本后评价与资本约束联合比较 | 论文市场与本项目不同 | R5 |
| 12 | [Man AHL / Man Group](https://www.man.com/insights/intro-machine-learning) | 国际机构 | 机器学习在投资中的应用 | 简单基线与复杂模型同口径比较 | 应用说明，不构成本项目盈利证据 | R2 |
| 13 | [D. E. Shaw](https://www.deshaw.com/what-we-do) | 国际机构 | 系统化与多元研究方法 | 对不同机制单独建模与评价 | 公开业务介绍，内部机制未知 | R1 |
| 14 | [Citadel GQS](https://www.citadel.com/what-we-do/global-quantitative-strategies/) | 国际机构 | 量化研究与技术平台 | 数据到策略实现用相同身份追踪 | 不推断专有信号与执行参数 | R0 |
| 15 | [G-Research](https://www.gresearch.com/) | 国际机构 | 量化研究与机器学习 | 研究工具与运行工程可复用 | 原子路径失效，改核主页 | R2 |
| 16 | [Winton](https://www.winton.com/news/experiment-and-observation-in-quantitative-investment) | 国际机构 | 区分实验研究与观测研究 | 历史实验不冒充自然前向证据 | 采用方法讨论，不迁移业绩 | R3 |
| 17 | [WorldQuant](https://www.worldquant.com/vi/ideas/the-power-of-exponential-thinking/) | 国际机构 | 组织化 Alpha 生产过程 | 完整尝试账本、机制去重与组合增量 | 产量不等于独立信号数量 | R1 |
| 18 | [Qube Research & Technologies](https://www.qube-rt.com/) | 国际机构 | 数据、研究、执行平台工业化 | 持续调度与恢复，避免手工搬运 | 公开平台介绍 | R3 |
| 19 | [XTX Markets](https://www.xtxmarkets.com/) | 国际机构 | 机器学习预测用于交易与流动性 | 预测与实际执行分别测量 | 不复制高频硬件规模与订单行为 | R4 |
| 20 | [CFM](https://www.cfm.com/cfm-our-approach/) | 国际机构 | 执行研究、价格冲击与组合反馈 | 成交归因反馈成本校准 | 公开方法不等于本项目成交证明 | R4 |
| 21 | [Qlib](https://github.com/microsoft/qlib) | 国内相关开源生态 | 数据、模型、研究工作流 | 统一特征标签训练与评估合同 | 不整库替换唯一永续账本 | R2 |
| 22 | [RD-Agent](https://github.com/microsoft/RD-Agent) | 国内相关开源生态 | 研究开发反馈迭代与因子/模型优化 | 来源绑定提案与失败记忆 | 不直接运行任意生成代码 | R1 |
| 23 | [VeighNa / vn.py](https://github.com/vnpy/vnpy) | 国内相关开源生态 | 事件驱动交易框架 | 策略意图与场所适配分离 | 不导入 broker SDK 或混用账本 | R4 |
| 24 | [RQAlpha](https://github.com/ricequant/rqalpha) | 国内相关开源生态 | 可扩展回测框架 | 模块接口与场所语义分别验收 | 股票执行语义不能直接用于永续 | R4 |
| 25 | [Build Alpha](https://www.buildalpha.com/llm-orchestrator/) | 国际系统 | LLM 编排搜索并消费反馈 | 有预算工具调用、任务轨迹与复盘 | 商业功能自述，未运行产品 | R1 |
| 26 | [QuantConnect LEAN](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview) | 国际系统 | Alpha、组合、风险、执行分层 | 端到端明确接口与状态 | 借鉴架构，不重复引入账户引擎 | R4 |
| 27 | [NautilusTrader](https://nautilustrader.io/docs/latest/concepts/reconciliation/) | 国际系统 | 执行状态与外部回执对账 | 事件幂等、重启恢复、差异队列 | 只有真实回执才能验收场所一致性 | R4 |
| 28 | [pysystemtrade](https://github.com/pst-group/pysystemtrade) | 国际系统 | 系统化规则与生产相关结构 | 保留简单可解释基线 | 期货配置需独立场所适配 | R2 |
| 29 | [VectorBT](https://vectorbt.dev/) | 国际系统 | 向量化研究与交互诊断 | 快速筛选与严格事件回放分工 | 快速统计不替代权威账户仿真 | R2 |
| 30 | [Backtrader](https://www.backtrader.com/docu/) | 国际系统 | 策略、数据和分析器组合 | 接口级回放比较与可解释诊断 | 不据默认成交模型认定真实执行 | R4 |
| 31 | [MLflow](https://mlflow.org/docs/latest/ml/model-registry/) | 通用 ML 系统 | 模型版本与别名管理 | 冻结版本、挑战者与回滚历史 | 版本切换不等于经济晋级 | R3 |
| 32 | [Feast](https://docs.feast.dev/getting-started/concepts/point-in-time-joins) | 通用 ML 系统 | 按历史时点连接特征 | 事件、可用时间和决策截止分开 | 不能只按事件时间过滤 | R2 |
| 33 | [Dagster](https://docs.dagster.io/guides/automate/declarative-automation) | 数据编排系统 | 按数据依赖驱动自动化 | 数据就绪、冷却与预算共同触发任务 | 借鉴条件语义，暂不新增常驻依赖 | R3 |
| 34 | [Optuna](https://optuna.readthedocs.io/en/stable/tutorial/10_key_features/003_efficient_optimization_algorithms.html) | 优化系统 | 采样与剪枝分别控制搜索 | 失败计费、早停和搜索预算 | 剪枝只能读取内层信息 | R1 |
| 35 | [FinRL](https://github.com/AI4Finance-Foundation/FinRL) | 研究系统 | 金融强化学习研究框架 | 保留仿真研究扩展接口 | 推迟真实资金在线探索 | R6 |
| 36 | [Hummingbot](https://hummingbot.org/strategies/v2-strategies/) | 交易系统 | 控制器与订单执行器分离 | 有限任务执行与生命周期对账 | 不接通未经验证的交易权限 | R4 |

## 择优后的六个决策

1. 来源、假设和失败记忆进入结构化研究合同，而不是让聊天文本直接成为代码。
2. 扩展可组合、可解释的特征与模型规格；保持旧冻结模型和唯一 Decimal 账本。
3. 调度以数据就绪、预算、冷却和已有任务状态为条件；未知状态不制造重复实验。
4. 预测评估、技术回放、实际账户、独立经济证据分别展示；回放不得冒充成交反馈。
5. 新模型先成为挑战者；已有观察记录不改写，退役/替换保存理由和版本。
6. 前端围绕来源→计划→实验→模型→观察→执行→组合→运维组织，异常可直接定位到任务。

这些是本项目基于资料的工程选择，不是声称各家机构具有相同的内部架构。
不照搬 Nautilus 的推断成交为真实成交：QUANTOS 仍区分实际回执与推断事件。
不照搬 MLflow 的可变别名来静默替换冻结模型，不引入 16 套运行依赖或第二套账本。

## X 与学术补充

本轮访问 https://x.com/buildalpha 仍失败，检索未提供可核验帖子正文；采用其官方
[LLM 编排文章](https://www.buildalpha.com/llm-orchestrator/)作为商业方法说明。
[HangukQuant 作者文章](https://www.research.hangukquant.com/p/big-improvements-to-the-market-maker)
公开摘要提供模拟/交易复用逻辑的方向，付费部分未读，不称已完成 X 全时间线研究。
[RD-Agent-Quant](https://arxiv.org/abs/2505.15155)、
[Quant 4.0](https://arxiv.org/abs/2301.04020)与 AQR 上述研究分别支持研究迭代、知识驱动、
成本后评价的研究方向；其摘要与公开方法不是 BTC 收益证明。

## 如何验收

每一工作包绑定代码、行为测试、API 合同、前端动作及实际运行回执。软件完成与
自然样本/独立权威/场所真实回执分别标记，禁止用绿色测试替代后者。
详细顺序见 [开发路线](../engineering/plans/2026-09-08-alpha-factory-lifecycle.md)。
来源目录见 [sources.json](../research/quant-benchmark-20260908/sources.json)。

