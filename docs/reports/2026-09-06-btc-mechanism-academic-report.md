# BTC 永续合约可交易优势的公开证据与信息增量检验

**Public Evidence and Incremental Predictability in BTC Perpetual Futures**  
QUANTOS 研究工作论文 · 2026-09-06 · 探索性研究，未经同行评审  
分类：金融计量、市场微观结构、加密衍生品。状态：`EXPLORATORY_PREDICTION_ONLY`。

## 摘要

本文将公开研究拆分为经济机制、信息要求与可证伪实验，避免将价格形态、模型复杂度或历史最优收益直接当作 Alpha。在 Binance BTCUSDC 永续的冻结行情上，本文检验主动成交量不平衡、已结算资金费能否改善简单价格模型的下一日收益预测。四个固定模型采用月度扩展窗口估计，仅使用已成熟标签；2025-01-01 至 2026-08-01 的 577 个相邻 24 小时收益构成评价样本。

加入主动成交量后，相对价格模型的全期均方误差下降 **0.943%**，2025/2026 分别下降 **1.476%/0.203%**。但全期仍比零收益预测略差，且探索性三重比较校正未通过预设门槛。加入三日已结算资金费没有改善该模型。本文另验证两日官方 OI 历史归档可获取，但没有把部分覆盖冒充全历史可用。两次重放预测逐字节一致；独立重建 2,920 个预测值和 12 组汇总通过。

结论是主动成交量存在值得保留的弱增量线索，而非已经确认的可交易 Alpha。本文不计算账户净收益、不执行交易，不证明 QUANTOS 全系统金融正确性。数据已被项目其他研究查看，因果滚动评价不等于未见外层样本，更不等于真实前向。

## 1. 研究问题与文献边界

研究问题：在 BTCUSDC 永续上，价格之外的交易行为与杠杆信息是否提供可复现的预测增量，并最终足以覆盖执行成本？本文完成其中的信息初筛与数据可行性检验；净收益、独立前向及经济准入不在本次已完成范围。

这是一项定向原始证据核查，不是穷尽性系统综述。检索围绕 cryptocurrency momentum/common factors、crypto carry、order flow imbalance、backtest overfitting 以及 Binance 官方字段。优先作者、研究机构与交易所原始资料；排除无可核实定义的盈利截图、二手推荐与私有机构策略推测。检索日期为 2026-09-06。机器可读来源、阅读范围及访问失败见 [sources.json](../research/btc-mechanisms-20260906/sources.json)。

| 证据 | 本文保留的内容 | 不作的推断 |
| --- | --- | --- |
| Liu 与 Tsyvinski 的历史研究及作者说明 [1] | 动量与关注度提供可复验线索 | 历史现货规律不证明当前 BTCUSDC 永续净盈利 |
| Liu、Tsyvinski、Wu，2019 工作论文 [2] | 截面收益需要市场、规模、动量等风险解释 | 截面排序不能直接当 BTC 单币择时；正收益未必是 Alpha |
| Cont、Kukanov、Stoikov [3] | 最优买卖价处供需与深度关系具有微观结构解释 | 股票同期价格冲击不等于 BTC 下一日可预测收益 |
| Crypto carry 与作者后续说明 [4] | 杠杆需求、市场分割及套利资本约束 | 到期期货基差不等于永续资金费；高资金费不是无条件做空规则 |
| DSR / PBO [5–6] | 必须保存搜索总体，并评价选择过程 | 统计门槛不修复前视错误，也不保证盈利 |

本文对 [1] 阅读作者公开说明；NBER 正文入口访问返回 403，未声称精读全文。[2] 使用 2019-04-15 工作论文，不混称后续期刊定稿。[4] 核查 BIS 概要及 2025-12-22 作者说明，PDF 链接未能读取；不同版本的历史数值不混用。

## 2. 机制研究材料与研究优先级

机器可读 [hypotheses.json](../research/btc-mechanisms-20260906/hypotheses.json) 保存七张材料卡，均非策略批准记录。它们尚未写入运行中的候选目录；本轮四个模型的试验已进入既有 TrialLedger。

1. **主动成交信息增量：先保留。** 主动买卖压力可能持续，也可能已反映在当期价格中。需要检验滞后信息而非同期解释。本轮得到弱增量，仍未达到研究支持门槛。
2. **OI 与价格状态交互：下一批重点。** 同样涨跌在仓位扩张/收缩下可能有不同后续分布。OI 每一份合约同时存在多空，OI 增加不识别净方向，也不能直接称“新增资金看多”。数量变化与名义金额变化必须分开，后者含价格机械影响。该机制尚未实测。
3. **已结算资金费：保留失败证据。** 本轮三日汇总线性形式未提供增量；不能由此否定条件化拥挤、基差或对冲 carry，也不能偷偷改期限直到显著。
4. **去杠杆与成交方向：待数据验收。** OI 收缩、价格和主动交易可以描述状态，不能单独证明清算方向或“机构扫损”。盘口与完整清算信息缺失时只报告代理变量关系。
5. **相对价值与 carry：后续独立范围。** 需同时计入双腿价差、资金支付、融资、保证金与不同步成交风险；1500U 不能同时完整支持两腿的全部资本占用。
6. **ICT/SMC：保留明确规则，拒绝故事代替证据。** 先前订单块缺结构源码，重建规则已失败。本轮未再次搜索订单块参数，不把 FVG 当成真实订单簿缺口。

这里的优先级是下一步研究价值，不是预期收益排名。做市、跨所与期权需要其他数据和执行能力，未纳入四模型实验。

## 3. 数据、经济口径与可用性

### 3.1 本次实际使用的数据

市场为 Binance USD-M BTCUSDC 永续，不使用 BTCUSDT 替换或拼接。复用既有公共归档输入，逐档核对官方 SHA256、CSV 边界、重复和 OHLCV 合法性，共读取 **1,354,289 条 trade 分钟记录**。预热日历为 2024-01-01 至 2026-07-31，4 个非完整日未插值；相关特征窗口缺失则不训练。本轮只预测交易价格，因此不使用 mark 与 trade 的交集；此前账本研究的对齐记录数为 1,354,287，两者用途不同。

评价期完整：2025 年 365 个、2026 年 212 个 24h 标签，共 577 个。资金费来自既有带原响应摘要的 capture，经原校验器检查，使用实际已结算费率。该校验器针对冻结样本要求 8h 结算槽连续；不意味着已经覆盖所有历史结算频率变更。

归档具有来源与内容身份，但缺少每个历史事件当时的真实接收时间。我们假定完整日信息可在次日 00:00 决策时使用，留出 5 分钟计算与执行延迟。这个可用性假设还需前向实测，不能凭归档时间戳升级为生产证据。

### 3.2 主动成交量与 OI 的关键区别

USD-M kline 的 quote asset volume 为总报价币成交额，taker buy quote asset volume 为主动买入报价币成交额 [9]。定义：

`I_t = 2 × Σ taker_buy_quote_volume / Σ quote_volume − 1`。

先聚合成交额再取比值，不平均分钟比率。负数、非有限值、主动买入额大于总额、总额为零均拒绝。它不包含挂单与撤单，**不是 [3] 中的盘口 OFI**。

官方 REST OI 统计只提供近期窗口 [10]，但历史 daily metrics 是另一个入口。此次实际下载并经官方 CHECKSUM 验证：

| 日期 | 行数 | 第一条 / 最后一条 UTC | SHA256 |
| --- | ---: | --- | --- |
| 2025-01-01 | 288 | 00:00 / 23:55 | `f0a18c6986fc94164a23997ab48c29a37430340d4cd2553bf3bc92e295456b87` |
| 2026-07-31 | 288 | 00:00 / 23:55 | `5c7ec3ba911bb5b002566e15656e768d7c197914b5583be9afdc59a8b70988ff` |

字段包含 `sum_open_interest`、`sum_open_interest_value` 及若干多空比率。样本成功不证明中间日期齐全；`create_time` 的事件/发布含义、数量单位、修订与跨日对齐仍需验收。因此 OI 未进入本轮回归。下一步先验收完整时段，不能据两个样本宣称数据问题全部解决。

### 3.3 成本合同

用户报告 maker=0、taker=0.036%=3.6bp，未读取私人账户。按相同近似名义金额，完整吃单开平手续费约 7.2bp，叠加单边 2/5/10bp 不利滑点，门槛分别约 **11.2/17.2/27.2bp**。这只是比较尺度，不包含资金费、保证金、整手、冲击与估计误差；实际开平名义金额可变。若采用实际成交价，价差/滑点不可重复扣除。免费 maker 不代表可成交或没有逆向选择。

## 4. 冻结实验与统计方法

完整规格在首次运行前提交：[冻结实验计划](../engineering/plans/2026-09-06-btc-mechanism-study.md)，代码提交 `5704fc48c7f71152fe7e38fb71e6791cb26bd816`。这是查看本批结果前的内部冻结，并非外部注册平台认证；此前历史已被查看的事实不变。

对完整 UTC 日 t，信息截止日末，决策为 t+1 日 00:00，参考价 P_t 为 t+1 日 00:05 的一分钟开盘价。标签为 `y_t=P_(t+1)/P_t−1`，可用时间是 t+2 日 00:05。决策时刻尚未发生的退出参考价不得参与训练。

价格基线包含 1 日收益、7 日收益和最近 28 个日收益标准差（ddof=1）。四个带截距 OLS 模型为：

| 模型 | 信息集 |
| --- | --- |
| PRICE | r1、r7、v28 |
| PRICE_FLOW | PRICE + 当日主动成交量不平衡 I |
| PRICE_FUNDING | PRICE + 最近三完整日已结算资金费率之和 F |
| PRICE_FLOW_FUNDING | PRICE + I + F |

模型每个 UTC 月首个可用决策重新拟合，扩展训练窗口、至少 180 个四模型共同有效且标签已成熟的样本。标准化只用训练集，零方差尺度设 1；月内参数冻结。无学习率、树深、预测期限或阈值搜索。每模型共保存 25 次拟合，首次 180、最后 909 个训练样本，其中包括评价开始之前的拟合。月度参数与训练标签时间均可查。

评价量：MSE、RMSE，以及 `R²_PRICE=1−MSE_model/MSE_PRICE`、`R²_ZERO=1−MSE_model/mean(y²)`。这里 R² 是预测误差比较，**不是回归拟合 R²，也不是收益率**。

对增量模型记录配对损失差 `d_t=e_PRICE,t²−e_model,t²`，正值表示改善。Bartlett 权重 `1−lag/8`、lag=1…7，用 Newey–West 形式估计均值标准误 [7]；三模型全期双侧正态近似 p 经 Holm 步降校正。年度结果只作描述，不据年度最佳者重新选模型。

**统计限制：** 四模型存在嵌套关系，原始 MSPE 差的正态近似不能当标准有效的嵌套预测发现检验。Clark–West 专门讨论额外参数的估计噪声与调整 [8]。本批冻结的 HAC/ Holm 数值仅为探索诊断，不能将其解释为正式错误率保证；未在查看结果后换检验以获取通过。独立外层数据、适用的嵌套检验或完整训练过程的重采样仍是后续要求。Holm 只覆盖本批三个比较，不能消除整个项目累计搜索污染。

内部研究支持门槛要求：全期与两个年度均改善 MSE，并且正向损失改进的全期校正 p<0.05。该门槛即使通过，也只意味着进一步研究，不是经济准入。

## 5. 实证结果

### 5.1 完整结果表

表中相对改善为百分数，正值更好。评价不是账户回测。

| 模型 | 全期 RMSE（bp） | 全期相对 PRICE 改善 | 2025 改善 | 2026 改善 | 全期相对 ZERO 改善 |
| --- | ---: | ---: | ---: | ---: | ---: |
| PRICE | 231.149 | 0 | 0 | 0 | -1.058% |
| PRICE_FLOW | 230.057 | +0.943% | +1.476% | +0.203% | -0.106% |
| PRICE_FUNDING | 231.287 | -0.119% | -0.181% | -0.034% | -1.179% |
| PRICE_FLOW_FUNDING | 230.187 | +0.831% | +1.307% | +0.170% | -0.219% |

所有模型全期均未战胜零预测。FLOW 对价格模型的改善跨两个年度为正，但幅度在 2026 明显较小。2025 FLOW 对 ZERO 的改善仅约 0.042%，不能由单个年度的微小差异宣称可交易能力。资金费在此固定模型中没有增加预测质量；合并 FLOW/FUNDING 也不优于 FLOW 单独增量。

### 5.2 探索性不确定性

损失差与区间单位为收益平方 ×10⁶，非 bp 收益。

| 增量模型 | 平均损失改善 ×10⁶ | 近似95%区间 ×10⁶ | HAC z | 原始双侧 p | Holm p | 内部门槛 |
| --- | ---: | --- | ---: | ---: | ---: | --- |
| FLOW | +5.036 | [0.203, 9.869] | +2.042 | 0.0411 | 0.0822 | 未通过 |
| FUNDING | -0.637 | [-1.091, -0.182] | -2.745 | 0.0061 | 0.0182 | 未通过，方向为变差 |
| FLOW + FUNDING | +4.438 | [-0.348, 9.223] | +1.818 | 0.0691 | 0.0822 | 未通过 |

小 p 不等于好的策略；FUNDING 的损失差为负。FLOW 的未校正 p<0.05 也不能省略其他试验。以上区间还有第4节的嵌套模型与历史污染限制，不能用来证明“无效机制已被彻底证伪”或“盈利概率”。

### 5.3 与既有 QUANTOS 证据的关系

前一轮订单块重建在单边 3.6bp 手续费及 5bp 滑点下，2x 净亏 140.25 USDC、5x 停机路径净亏 152.81 USDC，见 [订单块报告](2026-09-06-causal-orderblock-results.md)。那是不同规格的技术经济回放，不能把本轮 MSE 数值与其 PnL 混合排序。已失效 V1 账本的历史收益一律不作为本文经济证据。

本文的弱信息增量也不能直接翻译为某个仓位、杠杆或预期收益。需要独立冻结交易映射，再通过同一修复后经济账本、实际资金费和执行约束评价。没有生成买卖指令、账户收益曲线、Sharpe、DSR/PBO 数值或 Paper 成交。

## 6. 可复现性、验证和局限

### 6.1 复现身份

- 源码提交：`5704fc48c7f71152fe7e38fb71e6791cb26bd816`。
- Python 源码摘要：`4ee05e0c78993c5b23802cf17e897ac4e7f6acc9168b22aa3ccc80a5b3c5f2c2`。
- ARM64 锁文件摘要：`5d195596db6896721135bdf8f4b665e247deea83385382f594a3b8232235a436`。
- 输入快照摘要：`974859e62f75b4e421983e80b87742f492b499044e95b02e04a509f9fc89e24b`。
- 首轮结果摘要：`04885bb52360d73d6648625b132a2a0efd59614c35c1fa2928c044b740ac4ced`。
- 环境：DGX aarch64、Python 3.12.3；`OPENBLAS_NUM_THREADS=1`、`OMP_NUM_THREADS=1`。

输出根目录：`/home/kerstker/quantos-dev-output/btc-mechanism-study-20260906`。`results/{1,2}/` 保存逐日 CSV、模型参数、输入身份、全部模型统计；`results/trials.db` 为既有 TrialLedger 格式，四模型两次重放共 **8 条试验、24 个状态事件**。重放不是八个独立候选；第二轮用于复现性，未新增模型或选择规则。

两次预测 CSV 逐字节一致，模型拟合与统计逐字段一致。独立验证器不导入研究驱动，以导出面板和 Moore–Penrose 求解重新估计预测，**2,920 个值**最大绝对差 **1.57×10⁻¹⁷ 以下**；另用 Bartlett 协方差矩阵独立检查 12 组统计。其覆盖数包括预热后的 2024 预测，正式评价是 577×4=2,308 个预测。

### 6.2 验证命令与结果

在项目根、指定虚拟环境运行：

```bash
/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905/venv/bin/python -m pytest \
  tests/tools/test_btc_mechanism_study.py \
  tests/governance/test_research_runner_boundary.py \
  tests/governance/test_service_import_graph.py \
  tests/governance/test_repository_hygiene.py -q
```

结果 **29 passed**；新测试覆盖未来追加不改旧预测、未成熟标签扰动、零方差与缺失、成交额单位合法性、滚动特征因果性及独立 HAC 数值。测试先失败于缺失实现，随后通过，红灯日志保留。仓库 hygiene 与显式 pre-commit 治理扫描通过。复现指令、材料索引与下载步骤见 [复现说明](../research/btc-mechanisms-20260906/README.md)。

此次没有修改经济引擎、风险门、服务或前端，不以这些专项测试替代完整系统验收。完整发布门禁先前仍 FAILED；150阈值边界一致性、实盘持久预算、真实USDC恢复和独立经济权威等未在本研究任务中修复。

### 6.3 不能消除的限制

1. 样本来自单场所单合约，已被历史研究使用；不存在本轮新产生的独立前向证据。
2. 只有日频线性形式。缺少 OI、真实预测资金费快照、同步现货/指数基差与盘口；不能据此否定其他频率、非线性机制或多腿收益。
3. 档案校验保证文件一致，不保证历史可用时点或未被修订；5分钟延迟与月度训练及时完成需要前向验证。
4. 未计算资金费现金流、成本后账户收益与保证金路径；更小预测误差不保证交易盈利。
5. 原始HAC正态近似与本批Holm不是完整嵌套预测检验，也未校正全项目累计研究选择；未制造缺失的正式统计证据。
6. 独立数值复核使用同一导出面板，能够发现模型/统计实现差异，却不能独立证明原始交易所数据真实或特征经济解释正确。

## 7. 结论与后续交付边界

应当整合研究，但保留的是来源、机制、定义、失败条件和全部试验，而非论文里表现最好的曲线。本轮形成十条来源记录、七张机制卡、一个冻结实验、四模型全结果以及可复现材料包。

**近期研究主线应继续考察主动成交信息与 OI 状态的增量，暂不宣称已发现 Alpha。** 下一批优先验收 BTCUSDC 全窗 OI 数量口径、时间含义和缺口，再冻结一个小时级/24h标签的机制实验；具体交互、训练与交易映射必须在新结果之前确定。已结算资金费的本轮失败形式保留，不通过扩大杠杆或反复换窗口挽救。

在任何经济结论前，先关闭会影响其路径的金融/代码边界问题。预测线索通过后，才接唯一经济账本进行相同风险与成本下的基线比较，再积累真正冻结前向。当前结论允许研究继续，不允许自动晋级、Paper 成交或真钱执行。

## 参考文献与数据文档

[1] Liu, Y. & Tsyvinski, A. *Risks and Returns of Cryptocurrency*. [NBER 工作论文入口](https://www.nber.org/papers/w24877)；本次已读[作者说明，2018-09-06](https://cepr.org/voxeu/columns/risks-and-returns-cryptocurrencies)。

[2] Liu, Y., Tsyvinski, A. & Wu, X. *Common Risk Factors in Cryptocurrency*, 2019-04-15 工作论文。[作者机构 PDF](https://economics.yale.edu/sites/default/files/2022-10/LiuTsyvinskiWu2019%20COMMON%20RISK%20FACTORS.pdf)。

[3] Cont, R., Kukanov, A. & Stoikov, S. *The Price Impact of Order Book Events*, arXiv v3。[原文](https://arxiv.org/html/1011.6402v3)。

[4] Schmeling, M., Schrimpf, A. & Todorov, K. *Crypto carry*。[BIS 概要](https://www.bis.org/publications/working-paper-1087-crypto-carry)；[作者后续说明，2025-12-22](https://cepr.org/voxeu/columns/crypto-carry-market-segmentation-and-price-distortions-digital-asset-markets)。

[5] Bailey, D. H. & López de Prado, M. *The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality*。[作者 PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)。

[6] Bailey, D. H., Borwein, J. M., López de Prado, M. & Zhu, Q. J. *The Probability of Backtest Overfitting*, 2015-02 版本。[作者 PDF](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)。

[7] Newey, W. K. & West, K. D. *A Simple, Positive Semi-Definite, Heteroskedasticity and Autocorrelation Consistent Covariance Matrix*。[NBER Technical Working Paper 55](https://www.nber.org/papers/t0055)。

[8] Clark, T. E. & West, K. D. *Approximately Normal Tests for Equal Predictive Accuracy in Nested Models*, 2005-11-15 工作论文。[Kansas City Fed PDF](https://www.kansascityfed.org/documents/5368/pdf-RWP05-05.pdf)。

[9] Binance. *Binance Public Data*, USD-M Klines 与校验说明。[官方仓库](https://github.com/binance/binance-public-data)。

[10] Binance. *USD-M Market Data — Open Interest Statistics*。[官方文档](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)。OI样本原始URL与校验记录见材料包 `oi-feasibility.json`。
