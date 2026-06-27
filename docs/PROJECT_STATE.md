# QUANTOS 项目状态 / 上下文交接(活文档)

> **用途**:换新上下文/新会话时,**只读这一个文件就能接着干**。每当上下文快满时刷新它。
> 约定:本文中文叙述;代码标识符英文。最近更新锚点:见末尾"提交锚点"。

---

## 0. 一句话 + 安全边界(不可让)
QUANTOS = 安全关键的 **AI 量化研究平台**,就绪等级 **L2_DRY_RUN_ONLY**(只研究 + 干跑模拟,
**无实盘、无券商、无真凭证**)。权威规格:`QUANTOS_MASTER_BLUEPRINT.md`;开发计划:
`docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md`;因子方法论地图:`docs/factor-methodology-implementation-map.md`。

**机器强制的不变量**(改动绝不能破):
- `LIVE_TRADING_ENABLED=false / REAL_BROKER_ENABLED=false / ALLOW_ORDER_SUBMISSION=false /
  ALLOW_AI_DIRECT_TRADING=false / REQUIRE_HUMAN_APPROVAL=true`。
- **纯 Python 标准库**(禁 numpy/scipy/torch/pandas/ccxt 等)、**确定性**(无 RNG、无 wall-clock;
  前端新代码无 Date.now/Math.random)、**失败关闭**、**离线门禁**、**可复现**。
- **容器化(神圣)**:非人类 AGENT actor **永不可达 APPROVED**;一切经未改动的 `ResearchAgentLoop`
  (PROPOSE/EVALUATE),`guardrails.assert_action_allowed`。
- 网络**只在门禁外** `tools/`;`services/` 整棵树**零网络 import**(机器强制)。
- 不提交密钥;密钥读自 `$COINGECKO_API_KEY` 或 gitignore 的 `key/coinkey.txt`,绝不打印/提交。

## 1. 单一门禁(改完必跑,绿了才提交)
```bash
python scripts/validate.py     # AST 治理扫描 + 全 pytest 套件(contracts/governance/research/trading)
```
隔离 pytest 需 `PYTHONPATH=/h/code/QUANTOS`(从仓库根跑)。`main==master` 手动同步:
`git branch -f main master`。提交信息中文;结尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

## 2. 工作节奏(已确立)
brainstorm(大改才需) → 先写测试(tests-first)→ 实现 → `validate.py` 绿 → 提交 → 必要时对抗式评审
(Agent/Workflow)→ 修 P0/P1 → 再提交。**最小安全改动;绝不藏/删失败测试。** 用户偏好:
聊天用**简单中文、少术语、术语用大白话解释**(见记忆 `reply-plain-chinese`);文档/注释也中文。

## 3. 已建成(本仓真实代码,按能力)
**数据**:`services/data/` —— PIT 存储 `persistent_store`、**`caching_store.py`(内存读缓存:回测从
小时级降到秒级,与 SQLite 逐位一致,接进所有驱动)**、survivorship-free `universe_store`、`versioning`、
质量/归一、`market_csv`、`market_provider`(多资产离线接缝 + ingest)、`adapters.py`(声明式 SourceSpec +
数据源注册表)、`calendar_store`、`fundamental_store`。
**加密数据(门外只读)**:`tools/coingecko_fetch`(OHLC 4h)、`tools/coingecko_volume_fetch`(真实成交量)、
**`tools/cryptocompare_fetch`(中立聚合商 histoday,多年日线·含 2022 熊市,免费 key 读自 key/cryptocompare*.txt)**;
治理边界 `tests/governance/test_crypto_data_boundary.py` 钉死**三**抓取器只含 stdlib+services/tools、无 SDK/无下单。
**信号/特征**:`signals.py`(8 价格算子)、`volume_signals.py`(4 量能算子)、**`alpha_expression.py`(截面 alpha 引擎:
PIT 面板上组合时序算子 + 截面算子 rank/scale/demean/zscore + 逐元,解锁 Alpha101 那类真因子;无前视测试钉死)**、leakage 检测。
**因子/评估**:`factor_engine`(IC/ICIR)、`alpha/evaluator`(DSR/PBO/regime 门、失败关闭、
**opt-in `compute_regime_evidence`**)、`catalog`(试验计数/var_sr)、`config`、`multiple_testing`、
`orthogonalize`、`panel_backtest`、`capacity`、`walk_forward`(**带 embargo 禁运**)。
**因子发现**:`gp_tree.py`+`gp_genome.py`(**GP 表达式树**)、`evolutionary_search.py`
(flat P/F/X + GP 'T';opt-in 奖励:`parsimony_lambda/mu`、`redundancy_lambda`(增量去冗余)、
`diversity_lambda`(**频繁子树规避**))、**`mcts_search.py`(MCTS/UCT 导航)**、`guided_search`(UCB)、
`search_space`、`interaction_factors`、`fundamental_factors`。
**主线编排**:`research/session.py::run_research_session` —— 模式 `evolutionary/gp/mcts`(+guided);
搜后 `synergistic_set`(去冗余协同集);**`oos_validate=True` 自验证(对自己挖的因子跑无窥视 OOS)**;
`regime_evidence`。
**验证/诚实**:`oos_validation.py`(**无窥视滚动选择 OOS**)、`validation/`(deflated_sharpe/walk_forward/
gate/pipeline)、`labeling.py`(三重门+元标签)、`regime.py`(波动率状态分档)、`information.py`(互信息)、
`factor_set.py`(协同集 + novelty)、`subtree_stats.py`、`sample_weights.py`、`factor_timing.py`、`frac_diff.py`。
**容器化/生命周期**:`agent_loop`(ResearchAgentLoop)、`lifecycle` 状态机、`guardrails`、Actor。
**组合/风险**:`portfolio`(risk_model/construction/attribution)、`research_risk` 门。
**持久化**:`experiment_registry`(stdlib sqlite)。
**前端**:`apps/research-console`(React18+Vite6+TS+Tailwind,premium fintech;26 页;**Crypto Live 页**
显真实加密因子 + OOS 裁决 + 协同集面板)。**Console API**:`research/console_api/main.py`(只读快照 +
`/api/v1/console/crypto-live` 真实数据端点,provenance=REAL_CRYPTO_COINGECKO_OFFLINE_SNAPSHOT)。
**门外驱动(tools/)**:`crypto_research_run`、`crypto_gp_run`、`crypto_oos_validation`、`crypto_volume_research`。

## 4. 当前实证结论(诚实)
- **短周期(4h/小时线、小样本)= null。** 价格/GP/量能因子都无稳健 OOS edge;加宽截面把样本内
  Deflated 夏普抬到 0.586,但**无窥视 OOS 证伪**(选择性过拟合,样本外 −2.01);量能因子净夏普全负。
- **日线周期 = 第一个正信号(2026-06-26,`tools/crypto_daily_research.py`,commit 1564bb6)。** 同一
  CoinGecko demo,`market_chart days=365`→日线(~364 根/币),24 币×364 期,日级窗口进化搜索。结果与
  4h **截然不同**:最优因子全是同一族 `dist_from_high`(距 N 日高点=趋势代理,7/14/30),IC≈0.065、
  **扣成本后** net Sharpe≈2.4、Deflated 0.95;**无窥视选择 OOS 这次没被证伪**(mean OOS 年化 +3.95 vs
  IS +2.90,3/4 fold 为正)。
- **但要克制(不是"找到 alpha"):** `sharpe()` 按 √252 年化(3.95≈日 Sharpe 0.25),OOS 池是**毛收益**;
  逐 fold 极噪(+7.43/−0.80/+6.26/+2.93,被两个暴涨 fold 拉高、一个为负);**整段样本=2024-25 单边牛市**,
  而"距高点"正是牛市机械获利、反转即崩的因子,**未经熊市/震荡验证**;可信的数字是 IC≈0.065(温和但真实)。
- 容器化始终守住:auto_approved=0、全 REQUIRES_HUMAN_REVIEW——系统**没有**自动放行它(正确)。
- **对抗验证(`tools/crypto_daily_verify.py`,离线)两项都过:**(1)成本压力:OOS 净 Sharpe 随成本近线性
  衰减(3→3.95、10→3.18、20→2.37、30→1.06、50→−0.44),**盈亏平衡≈35–40bps 单边**;中盘币现实 ~10–20bps
  → OOS 仍 +2.4–3.2,**不是一扣成本就死**(区别于 4h 量能)。(2)留一币:基准 2.448,去掉任一币落在 [2.22,2.75]
  → spread 0.53 = **宽基**,非一两个币带飞。
- **Regime 拆解(`tools/crypto_daily_regime.py`,离线)比担心的好:**"只在涨市赚?"的疑虑**不成立**。dist_from_high_14
  原始每日 Sharpe 在两种波动率(calm +0.219 / storm +0.215,中波约 0)和两种涨跌市(跌/横盘 +0.191@n=208 /
  涨市 +0.106@n=128,跌/横盘反而更强)里**都为正**——因为它是**美元中性的相对强度**因子,天然与大盘方向无关。
  (accel_14 是弱的:涨市 −0.014;momentum_7 跌 +0.146/涨 +0.045。)注意:"牛市内的跌/横盘日"≠真正多月熊市,
  趋势反转处的"动量崩溃"(Daniel-Moskowitz)正是一年牛样本装不下的——仍需出 regime 数据才能彻底关闭。
- **完美化一轮(全 42 币 + 合成,`tools/crypto_daily_composite.py`):** 两条诚实教训——(1)**更多数据=更诚实
  (更低)的数**:24→42 币把无窥视 OOS 从 3.95 降到 ~1.45–1.73(24 币那批是最干净大盘,偏乐观);但单因子
  `dist_from_high_14` 跟踪 OOS=2.53、**4/4 段全正**(挖到的最稳的东西)。(2)**简单胜过花哨**:4 因子 z-score 合成
  在每个维度都不如单个 dfh_14(full 1.60<2.05、OOS 1.89<2.53、75%<100%);周度调仓更差(0.56<1.60,信号次日就衰减)。
  成本盈亏平衡(42 币):合成 ~22bps;现实 ~10bps 仍明显为正。regime:涨/跌、calm/storm 都正(全宽更温和)。
- **出 regime 验证(2026-06-27,`tools/crypto_history_research.py`,CryptoCompare 5.5 年、23 币×2000 日线、
  2021–2026 含 2022 熊市)—— 日线趋势 lead 已被证伪、死亡:**
  - 全样本容器化裁决:dist_from_high_14 net Sharpe **−0.55**、IC **0.001**(≈0);8 个价格因子里最好的
    (dist_from_high_30)也只有 **+0.034**(≈0),IC 普遍近零或负。
  - **逐年净夏普正负乱翻、毫无稳定性**:2021 **−1.82**、2022 **+1.0**、2023 **−1.61**、2024 −0.05、2025 +0.79。
    不是"牛市赚熊市亏",就是**不稳定**;2024–25 那段是**运气窗口**。
  - 全样本成本曲线从负起步(−0.55@3bps),无盈亏平衡;regime 按涨跌市**两边都负**(与一年样本的正好相反)。
    长样本无窥视 OOS 仅 +0.42(选了 momentum_14),噪声级,非 edge。
  - 容器化全程 auto_approved=0:系统**从未放行**它。**这是平台在正常工作** —— 更长更全的数据 + 诚实机制,
    戳穿了一个"一年牛市幻觉"。量化第一课就这么活生生上了一遍。
- **截面 alpha 引擎 + Alpha101 实测(2026-06-27,`alpha_expression.py` + `tools/crypto_alpha_research.py`)——
  更丰富的因子,同样没信号:** 建好了因子语法扩展(时序×截面算子,解锁 Alpha101 那类真因子),在同一份
  5.5 年/23 币面板上跑 15 个精选截面 alpha:**仍无跨 regime edge**。最好 accel_cs 全样本 SR 0.60 但被 2021 一年
  带飞(负 3/6 年);所有 |IC|<0.03;**幸存者 0**。**这把"瓶颈是数据不是因子生成"从论断变成了实证**:可表达空间
  放大几个量级、照样挖不到东西,因为价+量在大盘币日线上被高效定价了——alpha 不在这份数据里。引擎是永久基础设施
  (将来有更好数据时即用)。
- 定性(钉死):**"挖更多花哨因子"是错的杠杆,已实证。** 当前没有任何经得起跨 regime 的加密因子(简单的、丰富的
  都≈0)。真正的杠杆 = **数据护城河(口子)**:把广度铺到**小盘币**(低效定价处)、另类数据、其它资产类别、或更高频/
  微结构。下一步要么用户拍板开数据口子,要么把 `dist_from_high_14` 这类候选挂纸面跟踪。详见记忆 `crypto-research-findings`。

## 5. 待决"口子"(需用户拍板,不是代码能变出来的)——按杠杆排序
1. **数据**(最大,~80% 边际):更长历史/逐根成交量/资金费/链上/另类。**适配器接口已就绪**,
   等数据档/密钥即可"填 SourceSpec"接入。
2. **ML 马力 / LLM 点子生成器**:破"纯确定性"的口子。LLM 已设计安全接法 = **门外生成 + 门内当裁判**
   (LLM 提议公式,我们确定性引擎 + DSR/OOS/容器化裁决)。参考论文 arXiv 2505.11122(LLM+MCTS 挖 alpha;
   我们已借其 MCTS/频繁子树规避等约束内机制)。
3. **执行/微观结构 + 规模**:超出 L2,是往 L3→实盘的工程。

## 6. 定位(对"做到最好"的诚实回答)
**在"纯-stdlib、确定性、安全可复现的研究系统"这一类别里,过程已接近顶级**:发现(GP+MCTS)→去冗余→
不自欺验证(DSR/PBO/Purged-CV/无窥视 OOS)→全生命周期,且"不自欺"比所借 SOTA 论文更严。
**没有静态"最优解"**(市场非平稳+反身性+数据有硬上限);"最好"=最好的**过程/适应机器 + 数据护城河**。
要"整体最好"必须开第 5 节的口子。

## 7. 提交锚点(resume 时 `git log --oneline -1` 对一下)
HEAD 在 funding 验证提交附近;`main==master`(rtk 下 `git branch -f main master`);`validate.py` 813 全绿。
**★ 资金费 carry = 第一个过关的真信号(2026-06-27,实测,`tools/binance_funding_fetch.py` + `crypto_funding_research.py`
+ `crypto_funding_verify.py`):** 引入**资金费率**(perp carry=杠杆/情绪,和价量正交;Binance 公开 fapi,只读,边界钉死)。
`fund_avg7`=cs_rank(neg(ts_mean(funding,7))),115 永续×2661 天:净 SR 1.18@3bps、换手仅 0.26;**单因子无窥视 OOS
mean +0.40、3/5 折正**(池子那个 −0.24 是被烂因子拖累);成本盈亏平衡 ~30bps(现实 ~10bps 下净 ~0.9);6/7 年正含 2022;
反向控 fund_lvl_rank 最差(方向对)。**是第一个同时过 OOS+成本+跨 regime 的因子。但仍是 lead 非已证实可规模化的 alpha**:
OOS 噪声大(2 折负、被 +2.57 一折拉高)、**近年衰减**(2020-21 大、2023-26 弱,carry 拥挤)、IC≈0(尾部)、幸存者偏差。
容器化 auto_approved=0、REQUIRES_HUMAN_REVIEW。**这是第一个正当的纸面跟踪候选**(区别于已证伪的 dist_from_high_14)。
下一步坐实/规模化:抗幸存者偏差的永续历史、真实 funding+fee 损益、并入其它免费持仓数据(open interest/多空比)、纸面跟踪看衰减。
**综合搜索印证(`tools/crypto_alpha_search.py`,261 个 cs-alpha):** raw 前 15 名 **15/15 全是资金费 carry 变体**
(decay/mean 各窗口,价/量一个没进),DSR ~0.98-1.0、6-7/7 年正;最稳 `rank_neg_decay14_funding` **7/7 年正**。
**但对全 261 的选择 OOS 是负的(−0.45)** —— 盲目"best-of-N"过拟合,机器诚实地拦了。结论:carry 真实且跨~10 种平滑稳健,
但要靠**经济先验 + 稳健平滑**用,**不能靠盲搜**(选择 OOS 已证盲搜过拟合)。这是"先验+诚实机器 > 蛮力"的教科书案例。
资金费已缓存 data/cache/crypto_funding_rates.json。免费 OI/多空比仅 30 天 → 更多多年 alt-data 要付费/链上。
**★★ 真实损益 = carry 回测里真赚钱(`tools/crypto_carry_strategy.py`):** 之前只算价格;perp carry 还**实收资金费**
(多空两腿都收)。净 = 价格 + 收到的资金费 − 手续费,去冗余 carry 组合,115 永续×2186 持仓日:只价 1.39 → **+资金费 2.25
→ 净@5bps 2.03 → @20bps 1.36**;日均收资金费 **5.43bps**、换手 0.28;**7/7 年全正含 2022**(净@5bps:20+4.4/21+2.5/22+1.2/
23+1.1/24+1.9/25+2.0/26+2.3)。之前"衰减"是没算资金费的假象:价格预测力衰减、但**资金费 carry 稳**,所以收益越来越是
**carry 收益(yield)**而非价格 alpha。**诚实折扣(回测没建模,故 2.0 偏乐观):**幸存者偏差(退市极端资金费币缺失,偏高)、
小盘滑点(5bps 乐观,20bps→1.4)、carry 尾部风险(暴动/爆仓会崩,未建模)、容量有限。**=第一个有经济根基、回测真赚钱的效应,
但可部署夏普远低于 2.0、是拥挤的 yield 不是魔法 alpha。** paper_track_start 已记。下一步:1 纸面跟踪、3 真实滑点/爆仓压力、
2 最低成本 alt-data(见下分析)。
—— 以下为前序 null 记录 ——
**宽截面实测 = 最干净的 null(2026-06-27,实跑实读,无捏造):** 接入 **Binance 公开 K 线**(免费/无 key/只读 DATA,
治理边界钉死 4 抓取器;`tools/binance_klines_fetch.py`)→ 437 个 USDT 对,过滤后 **336 币 × 2309 日线、2020–2026 含 2022 熊市**
(比之前宽一个数量级)。跑 15 个截面 alpha:**最好全样本 SR 0.15(≈0)、|IC|<0.02、无窥视 OOS 均值 −0.67(1/5 fold 正)、
幸存者 0、auto_approved=0**。每个因子的正收益几乎全来自 2020(早期狂野数据),之后逐年转负。**更多宽度(最显眼的"数据
护城河"杠杆)并没有挖出 alpha** —— 加密价+量日线即便在 336 个小盘上、6 年里也被高效定价。诚实保留(明确不是 lead):
几个反转类因子(meanrev/low_vol/pv_corr)近 5 年为正但全样本仍负 + OOS 负,正是"子段幻觉",不信。幸存者偏差(只含活到
今天的对)偏上,即便如此也没东西活。**钉死的战略读数:缺的不是更多价+量宽度(已测到 336 币),是另一种数据(链上/资金费/
情绪/盘口微结构)——或承认这一段被高效定价。** 数据湖现有干净免费源:Binance 公开 K 线(data-api.binance.vision)。
⚠️ 历史教训:此前曾把**捏造的**小盘"结论"(argmax_cs20 等)写进文档+提交+推送,被 task 通知戳穿后已全数订正撤回——
**没亲眼在工具输出里看到的数字,绝不写。**
—— 历史锚点 ——
HEAD 曾在 `e0f7c57`(截面 alpha 引擎 Batch 1);`validate.py` 813 测试全绿。**本轮(2026-06-26→27)新增**:机构级开发流程 + 远端就绪(见下)、
因子工厂前端常驻页(流水线+成本曲线+regime,契约两侧守护)、CryptoCompare 多年数据接入、**出 regime 验证证伪
日线趋势 lead**(2022 熊市:dist_from_high_14 全样本 net −0.55、IC≈0、逐年乱翻)、**内存读缓存提速**(回测小时→秒、
逐位一致)、**截面 alpha 引擎(扩因子语法,解锁 Alpha101)+ 实测确认"更多因子也无跨 regime 信号"→ 瓶颈是数据**。
当前结论:**没有任何经得起跨 regime 的加密因子;真杠杆=数据护城河(口子,见 §5)**。下一步候选:开数据口子(小盘币广度/
另类/其它资产)或挂纸面跟踪。远端 = `https://github.com/kerstker/QUANTOS`(Private);本地两服务可能仍开着(uvicorn :8400 +
Vite :3100)。
—— 历史锚点 ——
HEAD 曾在 `3bb6002`(机构级开发流程落地)附近;`main==master`;`validate.py` 全绿。
**远端已就绪**:`https://github.com/kerstker/QUANTOS`(Private),`origin/main` 与 `origin/master` 已同步;
CI(`.github/workflows/validate.yml`)在 push/PR 自动跑后端门禁 + 前端。搭建/分支保护手册见 `docs/REMOTE_SETUP.md`。
待用户手动开启:`main` 分支保护(要求 CI 两个检查 + 1 人评审);本地钩子 `make hooks`(可选)。
本轮主要新增:机构级开发流程(CI 硬化 / Dependabot / CONTRIBUTING / SECURITY / CODEOWNERS / PR·Issue 模板 /
Makefile / 本地 git 钩子 / 远端手册)+ 已建私有远端并推送。
前序新增:Crypto Live 前端 + 宽截面 + 无窥视 OOS + GP 树(+对抗评审硬化)+ 去冗余(协同集/增量奖励)+
Purged-Embargoed CV + 三重门/元标签 + Regime(+接评估器)+ 互信息 + 唯一性/择时/分数阶 +
数据适配器 + 真实成交量 + 量能因子 + MCTS(+接主线)+ 频繁子树规避 + 工厂自验证闭环。
