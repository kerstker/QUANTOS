# 论文方法与 QUANTOS 差距审查

日期：2026-07-17  
对照对象：[量化交易高价值论文与自动化方法研究报告.docx](I:/qqFILE/量化交易高价值论文与自动化方法研究报告.docx)  
代码基线：隔离分支 `codex/l2-forward-evidence`，Task 1–3 收口提交 `d1cb6b3`  
安全基线：`L2_DRY_RUN_ONLY`，不允许实盘、券商、真实凭据或 AI 直接下单。

## 1. 阅读范围与总判断

已读取 DOCX 的全部 OpenXML 正文和表格：159 个正文段落、7 张表、66 行论文/方法记录。报告的生产级共识可以压缩为：

1. 可解释因子、残差/统计套利、事件信号是 alpha 骨架；
2. ElasticNet/树模型/深度模型是逐层增强，不是替代验证；
3. 目标波动率、相关性、换手和回撤约束属于一等模块；
4. Almgren–Chriss、VWAP/TWAP、OFI/盘口/库存控制组成执行层；
5. PIT 数据、purged/embargo、多窗口 OOS、成本、White/SPA、多重检验、PBO、paper 和 drift 监控缺一不可；
6. RL 先用于执行/做市/库存，不应直接承担端到端 alpha 和下单。

结论：QUANTOS 已经是一个高强度的安全研究与 dry-run 平台，但还不是论文报告所描述的“全方法栈顶级 AI 量化系统”。当前最主要的差距不是再加一个模型，而是：

- 没有真实、连续、可审计的前向证据时钟；
- 数据宇宙仍以 crypto/FX/宏观为主，缺 PIT 基本面、新闻和 LOB 数据层；
- 只有第一层确定性 ML stacking，缺 IPCA/RP-PCA/autoencoder/Deep Momentum/DeepLOB 等可复现研究模块；
- 缺事件/NLP 与微结构/TCA 生产研究链；
- 研究试验全局账本、White/SPA/发表偏差和外部信任/灾备仍未闭环。

## 2. 分层对照

| 论文要求 | 当前实现证据 | 状态 | 真实缺口 | 优先级 |
|---|---|---|---|---|
| 因子/统计套利骨架：价值、质量、动量、残差、配对、去因子化 | `services/research/factor_engine`、alpha factory/DSL/GP、IC 面板、carry/crowd/trend 研究与 dry-run | 部分满足 | 尚无 FF5/q-factor/IPCA 风险底盘、系统化行业/主题中性化和论文式残差配对研究；当前有效边集中在 crypto 因子 | P1 |
| 高维因子与特征压缩：IPCA、RP-PCA、autoencoder、特征稳定性 | `services/research/ml/{feature_panel,stacker,walk_forward_stack}.py`，Ridge/GBM 确定性 stacking | 部分满足 | 没有 IPCA/RP-PCA/autoencoder；没有跨年份/跨 regime 的特征稳定性筛选层；当前 ML 首跑结论是没有可确认 tradable edge | P1 |
| 深度时序：LSTM/Transformer、Deep Momentum Networks | 无对应生产研究模块；当前是 numpy/scikit-learn 的确定性第一层 | 缺失 | 缺多资产时序训练、成本正则、仓位一体化和模型漂移评估 | P2 |
| LOB/DeepLOB/分位数微结构 | 目前没有 LOB、OFI、盘口深度、队列位置或 tick replay 数据链 | 缺失 | 需要独立高频数据许可、事件回放、延迟/队列仿真；不应在当前日/周频系统中伪造 | P2 |
| 事件/NLP：情绪、novelty、surprise、stale-news、LLM 研究增强 | 没有新闻/公告摄取、去重、文本特征、模型版本锁定或事件执行链 | 缺失 | 需要 PIT 新闻时间、来源质量、文本 schema、成本/延迟与事件窗口验证 | P1 |
| RL 用于执行/做市/库存 | 没有 RL 环境或策略 | 有意延后 | 不是当前 L2 缺陷；论文自身建议先做 alpha→目标库存→RL 执行，必须先有真实 replay/simulator 和隔离执行权限 | P3 |
| 风险底盘：FF5/q-factor/IPCA、目标波动率、相关性、回撤、换手 | 有约束 optimizer、PSD factor risk、risk parity、target-vol、attribution、scenario/tail/macro 诊断 | 强部分满足 | 风险模型仍不是论文式可解释因子底盘；部分 regime/容量结果仍是诊断而非门禁 | P1 |
| 执行：Almgren–Chriss、VWAP/TWAP、动态 spread/OFI、库存控制 | 有 Almgren–Chriss 冲击、容量曲线、dry-run adapters、intent/pre-trade gate、paper fills | 部分满足 | 没有执行曲线/分段调度、LOB/TCA、成交概率、队列位置和真实 post-trade attribution；真实 broker 按 L2 有意禁用 | P2 |
| 验证：PIT、purged/embargo、OOS、成本、DSR/PBO、多重检验 | 有 PIT/quality/leakage、Deflated-Sharpe、CPCV、purged walk-forward、OOS selection、BH-FDR、成本/容量/尾部诊断 | 强部分满足 | 缺全局 trial ledger、White Reality Check/SPA、发表偏差证据、研究自由度的统一审计；当前 data admission 仍可能 BLOCK | P0/P1 |
| Forward paper 与 drift | Task 1–3 已完成准入引用、append-only decision/realization/event、原子 paper period、legacy 隔离和严格数值校验 | 结构完成、证据未完成 | 当前真实连续 forward weeks 仍为 0；Task 4–7 尚需把 domain、官方 driver、evidence、Go/No-Go/console 接通；不能把历史回测算成 8 周 | P0 |
| 数据基础：PIT 基本面、公司行动、新闻、LOB、多市场制度 | 有 PIT store、质量/版本/存活宇宙、crypto/FX/ECB/FRED 方向、准入 authority | 部分满足 | 没有报告推荐的股票/ETF/期货基本面与公告层；数据质量 admission 不是 PASS 时不得启动前向时钟 | P0/P1 |
| 生产运维：drift、审计、身份、灾备、供应链 | 有哈希链 audit/event、degradation monitor、strong identity M2 基线、multi-party approval、process-isolation 设计 | 部分满足 | 外部 IdP/mTLS 运营、restore history、HA/跨主机、自动封存、独立告警 SLA、供应链基线仍缺 | P1（平台） |
| 可复现性与安全边界 | 约 1000+ backend tests、frontend contract tests、governance scanner、L2 五开关、无 broker/credential import | 强 | full gate 仍有 container/supply-chain/model-risk/generated evidence 稳定化工作；当前 Task 4–8 仍未完成 | P0 |

## 3. 论文结论与当前系统的错位

### 已经明显领先或不应被当作缺口的部分

- 论文强调的验证、成本、容量、尾部和前向诚实性，在 QUANTOS 中被做成了比一般论文原型更强的 fail-closed 运行边界。
- 真实 broker、live order submission、AI 直接交易没有实现是正确的 L2 安全约束，不是“少接一个 SDK”。
- RL 目前没有上线与论文建议一致：先完成可解释 alpha、风险和执行仿真，再考虑局部 RL。
- QUANTOS 的多方批准、KillSwitch、哈希链事件、强身份和治理扫描超出了论文报告的研究方法范围，是生产安全平台的额外要求。

### 目前最影响“顶级 AI 量化”判断的部分

- 没有真实 forward evidence：研究结果还不能证明自然到达的 OOS 周期、漂移和准入连续性。
- 论文推荐的股票/ETF/期货因子底盘与当前 crypto/FX 研究宇宙不一致；这不是简单换 ticker，而是数据、制度、基本面发布时间和成本模型都要重建。
- ML 只有确定性 stacking，尚未形成“线性基线 → GBDT → 表示学习/时序模型”的可比较实验矩阵。
- 没有新闻/公告事件层，也没有 LOB/OFI/队列回放；因此无法覆盖论文中事件交易和高频执行两大方法簇。
- 多重检验已有 BH-FDR/DSR/PBO 等强组件，但没有把每次搜索、试验数、候选淘汰、研究者自由度统一写入不可变全局账本；这仍是顶级可信度红线。

## 4. 缺口优先级与落地顺序

### P0：先完成当前可信度闭环

1. Task 4：让 `realize_due` 只消费 attested pending，并只调用原子 `commit_realization`；
2. Task 5：官方 driver 每次重新打开 admission authority，BLOCK 时不写/不兑现，分别统计 attested/legacy；
3. Task 6：生成确定性 forward evidence baseline，真实周数只来自 v2 realization；
4. Task 7：Go/No-Go 与 console 消费同一 evidence，缺失/过期/链损坏显示 BLOCKED/STALE；
5. Task 8：刷新模型风险、容器复现、供应链和全量门禁证据。

### P1：让论文的中低频方法真正可研究

1. 建立 PIT 股票/ETF/期货基础面与制度数据 admission；
2. 实现 FF5/q-factor 与 IPCA/RP-PCA 风险/因子底盘，接入残差 alpha、行业/主题中性化和风险归因；
3. 建立全局 trial ledger，并补 White/SPA、发表偏差/研究自由度证据；
4. 建立新闻/公告事件 schema、stale/novelty/surprise 特征和事件窗口 OOS 评估；
5. 完成跨市场制度层：停牌、涨跌停、主连、夜盘、合约换月和本地交易日历。

### P2：执行和高频研究扩展

1. 在取得合规 LOB 数据后实现 OFI、spread、深度、队列位置和事件回放；
2. 在 Almgren–Chriss 之上增加 VWAP/TWAP/分段调度、成交概率、TCA 和 post-trade attribution；
3. 只有在 replay、延迟、库存和风险门禁可信后，才加入 DeepLOB 或执行局部模型。

### P3：深度与 RL

- 先做可复现的 Deep Momentum/LSTM/Transformer 研究驱动器，所有模型固定版本、seed、数据 snapshot、成本正则和 OOS；
- 再做 RL execution/market-making sandbox；RL 永远不能直接越过 risk gate 或 live approval。

## 5. 当前轮结论

Task 1–3 解决的是“前向证据能否成为可信事实”的存储与原子性底座，不等于已经拥有前向业绩。论文对照确认下一条最有价值的路线不是立即接 RL/LOB，而是先完成 P0 的 Task 4–8，并同步推进 P1 的数据 admission、因子底盘与全局试验账本。

审查时必须继续保留：

- `LIVE_TRADING_ENABLED=false`
- `REAL_BROKER_ENABLED=false`
- `ALLOW_ORDER_SUBMISSION=false`
- `ALLOW_AI_DIRECT_TRADING=false`
- `REQUIRE_HUMAN_APPROVAL=true`

本报告是方法差距审查，不将论文中的历史收益数字当作 QUANTOS 的可交易承诺，也不把论文结论直接当作晋级或下单证据。

