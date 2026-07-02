# QUANTOS 对标顶级 AI 量化 · 差距 + 修补路线图

> **生成:** 2026-07-01,多代理并行对标(8 维;4 维由工作流深度对标 + web 检索,4 维由 2026-06-29 基线报告 + 本会话更新补齐)。
> **对标对象:** 顶级 AI 量化(RenTech / Two Sigma / Citadel / HRT / Jane Street + 现代 ML 基金)。
> **打分基准:** 作为「研究/干跑平台」(非「能赚钱的实盘基金」)——两套基准差数量级,下文区分。
> **配套:** [`QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md`](QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md)(基线)、[`QUANTOS_SYSTEM_AUDIT_REPORT.md`](../QUANTOS_SYSTEM_AUDIT_REPORT.md)(L2 审计)、[`PROJECT_STATE.md`](PROJECT_STATE.md)。
> **本文用途:** 这是**活的开发路线**——`§3 L2-safe 修补路线`按 impact/effort 排好序,从 rank #1 起逐项开发。

## 1. 一句话总判

QUANTOS 是**方法学诚实、安全工程扎实、可复现的中端研究/干跑平台**,后端引擎基本建齐(1173 测试全绿、刚过 L2 系统审计 0 P0/P1)。它把「不自欺」做到接近顶配——**最强资产不是「找到了 alpha」,而是「敢于且有能力证伪自己的 alpha」**。对标顶级的差距**本质在三件事的数量级**:①**数据**(只有免费日频 crypto+FX+FRED,缺 tick/L2/厂商授权/另类数据);②**建模**(固定词表 DSL+GP,缺学习型信号/DL/LLM/RL);③**执行保真**(bar 级撮合,缺 tick/LOB/最优执行——**这条按 L2 安全边界故意不做**)。①②是真能力缺口可补;③在 L2 内不该补。

## 2. 八维评分表(对标顶级)

| # | 维度 | 分 | 来源 | 一句站位 |
|---|------|:--:|:--:|------|
| 1 | 数据护城河 | **4.5** | 基线+本会话 | 架构正确(bitemporal PIT/survivorship-free/版本化),数据实质=免费小样本;FRED 宏观刚接。范式对,缺料。 |
| 2 | Alpha/信号生成 | **6.5** | 基线+本会话 | 截面 DSL+GP+de Prado+IC 面板真;两条可部署边(carry/crowd);ML 层诚实=当前数据无 edge。缺学习型信号/LLM。 |
| 3 | 回测与验证 | **7.5** | 基线 | **最强维**:CPCV/DSR/PBO/no-peek OOS/泄漏检测接近顶配,已用来证伪自己。扣分在 tick 保真、算力、多重检验预算账本。 |
| 4 | 组合构建与风险 | **7.0** | 工作流 | 约束优化器/因子风险模型/9 引擎门/tail 引擎全真;已补风险平衡执行 book + Barra-lite read-only 诊断第一版;**缺**:完整板块/风格/宏观因子库、多层归因、拥挤诊断落地。 |
| 5 | 执行与微结构 | **4.2** | 工作流 | bar 级 next-open 高估可成交性;现实主义引擎(冲击+容量+尾部)已把 300× 高估抓出、给诚实区间;缺 tick/LOB/最优执行(L2 故意不做)。 |
| 6 | 基建/MLOps/规模 | **5.5** | 基线 | 单机工程纪律意外扎实(确定性/不可变实验存储/CI 门);缺分布式/GPU、种子钉死、环境锁定、特征商店/模型注册形式化。 |
| 7 | AI/智能体 | **5.2** | 工作流 | 护栏化非人 ResearchAgentLoop + 进化/GP/MCTS/bandit + 诚实多重检验;缺 LLM 假设生成/Transformer/RL/在线学习。信号发现瓶颈=智能+数据。 |
| 8 | 治理/安全/资本路径 | **6.2** | 工作流 | 多方哈希链批准/kill-switch/审计链/RBAC 骨架/签名接缝全真;缺 intra-day 实时风控、事件响应自动化、监管报送脚手架(多为 post-L2)。 |

## 3. L2-safe 修补路线(纳入开发路线,按 impact/effort 排序)

> 分类:`L2_SAFE_NOW`=不破 L2 现在就能做;`NEEDS_EXTERNAL`=需数据/算力/团队;`POST_L2`=安全边界外不该补(见 §4/§5)。本节仅列 `L2_SAFE_NOW`。

| Rank | 项 | 维 | 优先/工作量 | 依赖(全已存在) | 为什么 |
|:--:|------|:--:|:--:|------|------|
| **1 ✓** | **风险平衡多策略统一执行 book**(已做,`f...` risk-parity + rolling 逆波动核) | 4 | P0 / M | `book_combine.py`、`dry_run_pipeline`、carry+crowd 回测 | **已完成——诚实结论:分散不敌执行现实。** 三种方式(naive-merge VT 1.20 / risk-parity-merge VT 1.14 / portfolio-of-books 配置 VT 1.21)**均不敌 crowd_contra 单条 VT 1.38**。根因:1.95/8% 是 gross 理想化(carry gross~1.44),净-成本+整手+vol-target 下 carry 仅~0.58、拉不动 crowd;净曲线相关 +0.04(非 gross −0.04)。执行流水线抓到理想-vs-真的差。**deployable 结论:crowd_contra 单条。** 后续排序据此调整:carry 作为独立可部署边价值下调,重心转数据广度(Rank 11)+ 结构化因子库(Rank 2)。 |
| **2 ✓** | **Barra-lite read-only 诊断第一版**(已做,`services/portfolio/barra_lite.py` + `tools/barra_lite_diagnostics.py`) | 4 | P1 / M | `fundamental_store`(已填 FRED)、carry/crowd 本地缓存、`FactorRiskModel` | **已完成第一版 read-only 结构化风险诊断层。** 5 个结构化因子(`carry/crowding/liquidity/market_beta/volatility`)把现有 carry、crowd_contra、carry_crowd_equal 三本书映射到同一 `FactorRiskModel`,输出 `data/cache/barra_lite_snapshot.json`(gitignored,可复现)。首跑:58 symbols / 5 factors;carry total_var 0.6544、crowd_contra 1.3969、carry_crowd_equal 0.5733。诚实边界:诊断-only,不等同完整机构级 Barra 因子库,不改权重/闸门/晋级/干跑/券商路径。下一步可在 Rank #5 做 Brinson-Fachler 多层归因。 |
| 3 | 拥挤门接进组合构建(fail-closed 超阈值) | 4 | P1 / S | `services/alpha/crowding.py`、预交易门 | 便宜、防尾部同步清算风险 |
| 4 | 多 book 资本配置(Kelly/bandit 跨策略 AUM 再分配) | 4/8 | P1 / M | `run_registry`(每策略 Sharpe/var_sr)、优化器 | #1 之上,把「哪条边分多少钱」系统化 |
| 5 | Brinson-Fachler 多层归因(板块/因子/残差) | 4 | P2 / M | #2 因子库、`construction.py` 归因 | 让组合决策可解释(哪层驱动收益/风险) |
| 6 | 冲击模型校准(文献默认 + 敏感性,RE-1 收尾) | 5 | P1 / M | `realism/impact.py`、`dry_run_pipeline` PaperFill | 收紧容量估计(本会话已做参与率×执行铺开敏感性,再补文献系数校准) |
| 7 | 尾部感知组合构建(CVaR 优化) | 4 | P2 / M | `tail_risk.py`、约束优化器 | min-variance 忽略尾部;CVaR 最小化标准且代码量小 |
| 8 | 动态相关漂移检测(滚动相关+regime 断点告警) | 4 | P2 / M | `macro_regime.py`、协方差 | 静态收缩协方差错过 regime 切换(常态 0.3→压力 0.9) |
| 9 | 全局多重检验预算账本(跨会话 trial 计数) | 3 | P2 / M | `experiment_registry`、DSR n_trials | 把「一共试了多少次」变成全局账,防跨会话选择偏差 |
| 10 | 可复现硬化(GP/numpy 种子钉死 + Docker/Nix 环境锁 + 性能回归基线) | 6 | P2 / S | 现有确定性核 | 把「纪律极好的笔记本」推向「可重算底座」 |
| 11 | 股票+基本面(BYO-CSV 摄入 + 价值/动量/低波因子过关卡) | 1 | P1 / M | `market_csv.py`、`fundamental_factors.py`(全已存在) | 唯一真数据广度扩张;需你提供 CSV(免费源本环境被墙) |
| 12 | crowd_contra 送前向 paper-track | 2 | P2 / S | `paper_trading/store` | 已过 8 项证伪 + 干跑,前向跟踪是独立决策 |
| 13 | 数据漂移检测(分布/regime 漂移告警) | 3/6 | P2 / M | `macro_regime`、data quality | 生产化监控雏形 |

## 4. 需外部资源(钱与组织,非代码缺陷)

- 厂商授权数据(Bloomberg/Refinitiv/CRSP/Compustat/TAQ)+ 采购/合规/entitlement 团队 + 年数据预算(顶格千万至上亿美元)。
- 真正量级的另类数据(卫星/刷卡/AIS/网页抓取/招聘)采购与清洗团队。
- 分布式/GPU 集群(Ray/Dask/Spark/k8s)支撑亿级候选挖掘 + 大规模 ML 训练。
- 专职研究 + 基建/SRE 团队(24×7 on-call、混沌工程)。
- tick/L2 订单簿历史数据(解锁 LOB 重放 + 真实执行校准)。

## 5. 安全边界内故意不做(post-L2,补 = 破边界,不该补)

实盘交易/真券商连接/订单提交/AI 直接交易;真实资金路径(PB/抵押/清算/结算);SOR/场所选择/最优执行 RL/colocation/tick-to-trade;毫秒级 intra-day 实时风控接 OMS/PB;真实成交 TCA 闭环;监管报送(MiFID II/CAT/Reg SCI);做市/反博弈;部署/进程物理隔离(Phase 14 设计已备,实现 post-L2)。**这些是安全边界本身,绝不记为「落后于顶级」。**

## 6. 立刻开发:Rank #1

**风险平衡多策略统一执行 book。** 三方(工作流综合器 + 组合维代理 + 本项目既有分析)一致把它排第 1。它把本会话最强的研究结论(carry/crowd 宏观 regime 相反、corr −0.04、收益流组合 Sharpe 1.95/回撤 8%)从「收益流相加」升级成**一个真的、风险平衡的、净-成本的执行 book**——回答上次留下的开放线索(naive 权重 blend 是 carry-dominated、只到 VT 1.20)。全复用现有已测组件,零新模型/数据/外部依赖,直接闭合 §2 维度 4 的最高杠杆缺口。开发按 brainstorm→spec→plan→build,全程 L2 一寸不让。

---
*本路线图随开发推进更新;完成一项即在 Rank 行标注 ✓ 并附提交哈希。*
