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
**数据**:`services/data/` —— PIT 存储 `persistent_store`、survivorship-free `universe_store`、
`versioning`、质量/归一、`market_csv`(BYO 日线)、`market_provider`(多资产离线接缝 + ingest)、
**`adapters.py`(声明式 SourceSpec + 数据源注册表)**、`calendar_store`、`fundamental_store`。
**加密数据(门外只读)**:`tools/coingecko_fetch`(OHLC 4h/30min,无量)、
**`tools/coingecko_volume_fetch`(market_chart 真实 24h 成交量,时间戳对齐到整点网格)**;
治理边界 `tests/governance/test_crypto_data_boundary.py` 钉死两抓取器只含 stdlib+services/tools、
无 SDK/无下单。
**信号/特征**:`signals.py`(8 价格算子)、**`volume_signals.py`(4 量能/流动性算子)**、leakage 检测。
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
- 定性:**第一个扛过成本 + 扛过 OOS 选择的日线趋势候选**,但**regime 依赖、未出样本外确认**。当 lead,不当结论。
  缺的决定性测试是**出 regime**(熊/震荡样本——数据口子:demo 日线封顶 365 天≈一年牛)。详见记忆 `crypto-research-findings`。

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
HEAD 在 `3bb6002`(机构级开发流程落地)附近;`main==master`;`validate.py` 全绿。
**远端已就绪**:`https://github.com/kerstker/QUANTOS`(Private),`origin/main` 与 `origin/master` 已同步;
CI(`.github/workflows/validate.yml`)在 push/PR 自动跑后端门禁 + 前端。搭建/分支保护手册见 `docs/REMOTE_SETUP.md`。
待用户手动开启:`main` 分支保护(要求 CI 两个检查 + 1 人评审);本地钩子 `make hooks`(可选)。
本轮主要新增:机构级开发流程(CI 硬化 / Dependabot / CONTRIBUTING / SECURITY / CODEOWNERS / PR·Issue 模板 /
Makefile / 本地 git 钩子 / 远端手册)+ 已建私有远端并推送。
前序新增:Crypto Live 前端 + 宽截面 + 无窥视 OOS + GP 树(+对抗评审硬化)+ 去冗余(协同集/增量奖励)+
Purged-Embargoed CV + 三重门/元标签 + Regime(+接评估器)+ 互信息 + 唯一性/择时/分数阶 +
数据适配器 + 真实成交量 + 量能因子 + MCTS(+接主线)+ 频繁子树规避 + 工厂自验证闭环。
