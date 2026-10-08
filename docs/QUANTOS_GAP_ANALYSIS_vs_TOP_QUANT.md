# QUANTOS 诚实差距报告

> **2026-07-01 更新脚注:** 基线之后已推进数处——P0-3(安全门→运行时 chokepoint + 审计落盘)**基本闭合**(干跑流水线);
> P0-1(ML 层)**部分闭合**(`services/research/ml` sklearn stacking,诚实结论无 edge、瓶颈=数据/信号类);维度 8(治理/资本
> 路径)**大幅推进**(轨道3:券商接缝 + 多方人工批准 + Phase 14 进程隔离设计)。**「现实主义」缺口(下 §5 第 4 条)部分闭合**:
> `services/research/realism/`(Almgren-Chriss 冲击 + 容量衰减带,应用到 carry/crowd_contra)已建——**首跑即抓到自欺:
> 参与率上限把真容量绑在 carry ~$2M / crowd_contra ~$7M,冲击-only 会误报 $B(~300× 高估)。** **RE-2(情景重演 + 尾部)
> 也已建**:carry/crowd 崩盘 beta 极低(下尾依赖 0.13/0.0)、crowd 在 LUNA/FTX 崩盘里反赚——尾部是截面价差击穿非市场 beta;
> 真尾部内生(carry 最差 19% 在 2020-11 牛市);尾偏肥(Hill ~2.4–2.7)。**2026-07-13 更正**:原所谓
> `crypto_daily.db` 混单位实际是 quote USD volume 被消费端二次乘价，已以显式 volume semantics 修复并重算。
> **「现实主义」缺口从"未做"推进到"冲击/容量/情景/尾部四件已建、诚实见效"**。
> **数据缺口:宏观已接**——股票免费源本环境被墙(不绕过 bot-wall),转 **FRED 宏观**:`tools/fred_fetch.py` 抓 6 条市场型序列
> (39271 行)填进 `fundamental_store`,`macro_regime.py` 把 carry/crowd 边按宏观 regime 分桶。**关键发现:两条边 regime 画像相反
> 互补——carry 爱 risk-on,crowd_contra 爱 risk-off(高VIX/倒挂曲线),crowd 在 carry 走弱处走强 = 强分散**。仍敞着:**股票 +
> 基本面**(免费源被墙 → BYO-CSV 或 ALFRED)、经济发布类(需 ALFRED vintage);realism 剩 Copula 全拟合 + trend 容量。
>
> 生成:2026-06-29,多代理审查(8 维并行只读测绘 → 每维对标顶级 AI 量化算差距 → 综合)。
> 对标对象:顶级 AI 量化(文艺复兴 / Two Sigma / Citadel / Jane Street + 现代 ML 量化基金)。
> 评分基准:**作为「研究 / 干跑平台」**打分,不是按「能赚钱的实盘基金」打。两套基准差距甚大,下文反复区分。
> 配套:现状图见 [`CLAUDE.md`](../CLAUDE.md) §「What actually exists」+ [`docs/PROJECT_STATE.md`](PROJECT_STATE.md);研究结论见记忆 `crypto-research-findings` / `fx-research-findings`。

---

## 1. 一句话总判

**QUANTOS 是一个方法学诚实、安全工程扎实、可复现的中端研究 / 干跑平台——它把「不自欺」这件事做到了接近顶配,但它不是、也从未声称是一个能赚钱的实盘系统。** 它自己的诚实研究结论已经证明:加密日线 + 可交易 FX 截面趋势都**没有 OOS edge**,唯一站得住的可部署边是资金费 carry(诚实 Sharpe ~1.2)。

对标顶级实盘基金的差距**本质不在「引擎对不对」**——它的 factor / backtest / validation / portfolio / risk 引擎是真的、有 ~1047 个测试;**差距在三件事的数量级**:(1) 输入端的**数据护城河**(只有免费公开的 crypto 现货 + ECB FX 日频参考价,缺一切厂商授权 / tick / 另类数据);(2) 建模端**完全没有 ML/DL/LLM/RL 层**(信号空间被锁死在固定词表的标量表达式);(3) 执行 / 资本 / 实时风控这一整层按 L2 安全边界**故意不做**。前两条是真能力缺口、可补;第三条是安全边界、不该补。

---

## 2. 两条轴分开讲

### (A) 作为研究平台,它真有什么强项(真实、测过、非脚手架)

- **诚实的关卡(抗过拟合验证栈)——最大的真实资产。** CPCV / Deflated-Sharpe / PBO / no-peek 选择 OOS / purged-embargo / fail-closed gate / 表达式级泄漏检测,全部真实有测试,且**已被它自己用来证伪自己的样本内信号**(crypto/FX 趋势 OOS 全负)。这种「能抓海市蜃楼、并诚实地报告无 edge」的纪律,反而领先于很多商业回测器和中位数 ML 团队。
- **真引擎,不是 PPT。** IC/ICIR/Sharpe 数学、硬化 AST 因子编译器、单标的 + 事件驱动回测(精确 PnL 对账恒等式)、约束优化器(投影梯度 + 完整 fail-closed 可行性校验)、因子风险模型 Σ=BFB'+diag(Δ)、真实重对账 PnL 归因——都有数学、有测试。
- **安全工程 / 容器化治理。** fail-closed 设计、人类专属 kill-switch 复位、不可越权的部署就绪 pin、AST 治理扫描器、机器强制的双层威胁模型,契约层(SafetyConfig/RiskDecision/Actor)严谨且**诚实标注了自身边界**(明说 hash-chain 是内存内、需 out-of-band seal)。
- **自主研究 OS 的雏形。** 护栏化的非人类 ResearchAgentLoop + 进化 / GP / MCTS / bandit 搜索 + 多重检验诚实计数(每个 distinct genome 一个 trial)+ run_registry(sqlite 真源)+ 守卫的 research_runner(唯一可变面、治理钉死为研究专用)。containment 是该平台最成熟、最值得保留的资产。
- **数据架构正确性(非数据量)。** bitemporal as-of(读取层读不出 lookahead)、survivorship-free interval universe、内容哈希版本化、声明式多源 adapter、fail-closed 摄入——范式对,这是教科书级底座。

### (B) 对标顶级,它缺什么(本质)

- **数据护城河差数量级。** 顶级的数据是几千万到上亿美元年预算、厂商授权(CRSP/Compustat/TAQ/Bloomberg)、tick/L2 订单簿、PB 级,加上卫星 / 刷卡 / AIS / 新闻 NLP 等真正产生 edge 的另类数据。QUANTOS 只有免费日频 / 4h 的小样本沙箱。**这是「关卡能抓海市蜃楼但能交易的 edge 几乎没有」的根因——不是引擎不行,是原料不够。**
- **零 ML/DL/LLM/RL。** 所有非线性来自 8 价格 + 4 量算子的 GP 树组合,无法任意函数逼近;无神经网络 / Transformer / 图网络端到端学习;无 LLM 驱动的开放式假设生成;无 RL 执行 / 做市。现代量化 edge 的主体(学习型信号 + 另类数据特征抽取)整块缺失。
- **保真度与规模。** 回测停在 bar 级 next-open 撮合(高估可成交性);市场冲击是默认关闭的 sqrt stub、未用真实成交校准;压力测试只是固定倍数缩放;单机 max_workers=2、无分布式 / GPU、纯合成 / 离线快照数据。
- **执行 / 资本 / 实时风控整层不存在(L2 边界)。** 无券商连接、无订单簿微结构、无延迟建模、无 SOR、无真实资金路径、无毫秒级 intra-day 风控、无监管报送。这是**故意的安全停点**。

---

## 3. 八维评分表

| # | 维度 | 分(0–10) | 一句站位 |
|---|------|:---:|------|
| 1 | 数据护城河 | **4.5** | 架构正确性研究级合格(bitemporal PIT / survivorship-free / 版本化),但数据**实质**只是免费公开小样本——范式对,缺料。 |
| 2 | Alpha / 信号生成 | **6.5** | 真实的截面 DSL + GP/genome 搜索 + de Prado 标注 + 严格验证;但**零 ML/DL**、数据薄、自证无可交易 edge——优秀的单人古典因子实验室,远低于工业 ML alpha 工厂。 |
| 3 | 回测与验证 | **7.5** | 验证方法学(CPCV/DSR/PBO/no-peek/泄漏检测)接近顶配且已用来证伪自己;扣分在撮合保真度(bar 级)、数据规模、单机算力。 |
| 4 | 组合构建与风险 | **7** | 约束优化器 / 因子风险模型 / 9 引擎研究风控门 + 23 项预交易门皆真且 fail-closed;缺结构化因子库、冲击成本、真实压力情景、拥挤诊断。 |
| 5 | 执行与微结构 | **3** | 诚实、保守的 bar 级研究回测器;队列位置仅启发式、冲击未校准、无最优执行算法;大部分实盘能力按 L2 故意不做。 |
| 6 | 基础设施 / MLOps / 规模 | **5.5** | 单机研究平台的工程纪律意外扎实(确定性可复现 / 不可变实验存储 / 读穿缓存 / CI 门),但纯合成数据 + max_workers=2,与工业规模差 3–4 个数量级。 |
| 7 | AI / 智能体能力 | **5.5** | 确定性符号搜索 agent + 机器强制护栏 + 诚实多重检验,在窄轴上扎实;但无 LLM / DL / RL / 在线学习 / 分布式——研究能力整块维度缺失。 |
| 8 | 治理 / 安全 / 资本路径 | **7** | 安全治理脚手架真材实料(fail-closed / 人类专属 kill-switch / 部署 pin / 治理扫描器);但 pre-trade 门是「死代码」未接运行时链路,审计日志仅内存,无真实身份层。 |

> 提醒:若改按「能产生可交易 alpha 的实盘基金」标准,数据维只配 2–3 分、执行维仍 3 分、其它维也要下调。上表是研究平台基准下的公允分。

---

## 4. 差距分类

### (i) L2 设计内故意不做 —— 补 = 破安全边界,**不该补**

- 实盘交易 / 真券商连接 / 订单提交 / AI 直接交易(services/gateway 空壳,无 CTP/FIX SDK,扫描器机器强制)
- 真实资金路径:prime broker / 抵押品保证金 / 清算结算 / 资金调拨 / T+0 对账(账户仅 PaperAccount 模拟资本)
- SOR / 场所选择 / maker-taker rebate / colocation / tick-to-trade 延迟栈
- 毫秒级实时 intra-day 风控引擎、实盘三段(pre/at/post-trade)风控接 OMS/PB
- 实时低延迟行情直连 + live 推理 / 影子部署 + 真实成交反馈闭环(TCA 回灌)
- 监管报送(MiFID II / CAT / Reg SCI)、做市 / 反博弈
- 部署 / 进程物理隔离(规划在 Phase 14)

**这些绝不应记为「落后于顶级基金」——它们是安全边界本身。**

### (ii) 不破 L2 也能补的真研究缺口 —— **这是真正可行动的清单**(按优先级)

| 优先级 | 缺口 | 类型 |
|:---:|------|------|
| P0 | **接 ML/DL 研究层**(sklearn/xgboost/torch 作为只读研究特征变换 + 学习型信号),打破固定词表标量表达式的天花板 | RESEARCH_CAPABILITY |
| P0 | **接更多免费 / 学术离线数据**(Yahoo/Stooq 股票日频、FRED 宏观、Tiingo、学术 CRSP/Compustat 试用、Kaggle 另类数据集);填充已建但从未填的 fundamental store | DATA |
| P0 | **把 pre-trade 门 / kill-switch / 研究风控门接入运行时 chokepoint**(加端点 + 集成路径),让安全逻辑从「死代码」变成可证明生效;审计日志加 sqlite 持久化后端 | RESEARCH_CAPABILITY |
| P1 | **学习型高维信号组合层**(stacking / meta-model / 贝叶斯 blending),让弱信号在广度聚合下变可用——这是「弱信号赚钱」的机制 | RESEARCH_CAPABILITY |
| P1 | **现实主义成本 / 压力 / 容量建模**:学术冲击律(Almgren-Chriss / propagator)、历史情景重演(2008/2020)、Copula 尾部、真实容量衰减曲线——用历史数据离线做,不必上实盘 | RESEARCH_CAPABILITY |
| P1 | **撮合保真度升级**:用历史 tick/L2(若可得)做 LOB 重放 + 离线 TCA 近似 + 合成 LOB 模拟器,替换 queue_fraction 启发式 | RESEARCH_CAPABILITY |
| P2 | **种子管理 + 环境锁定**(GP 算子的 numpy seed、Docker/Nix),补齐跨机器比特一致;加性能回归基线 + 数据漂移检测 | RESEARCH_CAPABILITY |
| P2 | **结构化风险模型**(行业 / 风格 / 宏观因子库)、Brinson-Fachler 多层归因、因子拥挤分数 / 头寸相似度诊断 | RESEARCH_CAPABILITY |
| P2 | 统一跨资产时间标度、自动退市检测、并购 / 分拆 reconciliation、用日历驱动 walk-forward 周期 | RESEARCH_CAPABILITY |
| P3 | 全局多重检验试验预算账本;RL 作为研究 / 仿真能力(执行 / 组合);LLM 驱动的假设生成(人在回路审批) | RESEARCH_CAPABILITY |

### (iii) 需要外部资源(数据 / 算力 / 资本 / 团队)才能补 —— 钱与组织问题,非代码缺陷

- **厂商授权数据合同**(Bloomberg/Refinitiv/CRSP/Compustat/TAQ)+ 数据采购 / 合规 / entitlement 团队 + 年度数据预算(顶格几千万至上亿美元)
- **真正量级的另类数据**(卫星 / 刷卡 / AIS / 网页抓取 / 招聘)的采购、清洗与回测可用性评估团队
- **分布式 / GPU 集群**(Ray/Dask/Spark/k8s),支撑亿级候选挖掘与大规模 ML 训练
- **专职研究 + 基建 / SRE 团队**(数百研究员、万核算力、24×7 on-call、混沌工程)

---

## 5. 顶 5 个「不上实盘也能做、最该做」的下一步

1. **接 ML 研究层(P0,撬动最大)。** 把 sklearn/xgboost(先于 torch)作为只读研究特征变换接入,做学习型信号 + 高维信号组合。当前「自证无 edge」的一个核心外因是信号空间被锁死在固定词表标量表达式里——这是扩大「可发现 edge 类型」最高杠杆的一步,且完全在 L2 内。

2. **喂更多离线数据(P0,与第 1 步互补)。** 接 Yahoo/Stooq/FRED/Tiingo + 学术数据集,填充 fundamental store。再强的 ML 层在当前薄数据上也会饿死;数据面太窄正是 OOS 无 edge 的部分外因。只读、PIT、不下单,不破 L2。

3. **把安全门接进运行时 chokepoint + 审计落盘(P0,治理可信度最关键)。** 现在 pre-trade 门 / kill-switch / 研究风控门都是「契约 + 测试齐全的死代码」,没有端点会触发评估。加集成路径让它们在真实请求流中可证明生效,审计日志加 sqlite 后端形成跨会话长存档。这是把「单元测试证明正确」升级为「运行时证明强制」。

4. **补现实主义的成本 / 冲击 / 压力 / 容量建模(P1)。** 用学术冲击律 + 历史情景重演替换固定倍数缩放和默认关闭的 sqrt stub。**这条特别重要**:组合 / 风险层若现实约束太松,会和已知的「关卡抓海市蜃楼」叠加,让无 OOS edge 的信号在组合层看起来可部署——补现实主义优先于追求 Barra 级因子库广度。

5. **可复现性 + 漂移工程补强(P2,巩固已有强项)。** GP 算子 pin seed、Docker/Nix 环境锁定、性能回归基线、数据漂移检测。把「纪律极好的笔记本」往「可重算的研究底座」推一步。

---

## 6. 诚实结语

**QUANTOS 本质是:** 一个安全可证、方法学诚实、可复现的研究 / 干跑平台,它最稀缺的品质不是「找到了 alpha」,而是**「敢于、并且有能力证伪自己的 alpha」**——这在量化研究里是真正值钱的工程与文化资产。它的引擎是真的,关卡是真的,安全脚手架是真的,自主研究 OS 的雏形是真的。

**它不是:** 一个能赚钱的实盘基金,也不是接近顶级机构数据 / 算力 / 建模规模的系统。它目前唯一站得住的可部署边是资金费 carry(~1.2);加密日线和可交易 FX 截面趋势都**没有 OOS edge**。横幅上的 **L2_DRY_RUN_ONLY** 是诚实且正确的。

把它当成「一个纪律极好的个人研究者的实验室」,而不是「一座印钞工厂」——这个定位既不贬低它真实的工程价值,也不掩盖它与顶级之间数量级的数据 / 建模差距。**最该做的事,是在不碰安全边界的前提下,补「料」(数据)和「脑」(ML/学习型信号)——而不是继续在窄词表里加搜索算法。**
