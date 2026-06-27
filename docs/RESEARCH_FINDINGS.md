# QUANTOS · 诚实研究纪实

> 一份给平台公信力做背书的实录。读这一篇,你会知道这套系统试过什么、证伪了什么、唯一过关的是什么,以及——它怎么在自己出错时把自己纠正回来。
>
> 叙述用中文,代码标识符 / 文件路径 / 命令保持英文。运行细节见 [`docs/PROJECT_STATE.md`](PROJECT_STATE.md),结论备忘见 [`docs/crypto-research-conclusions.md`](crypto-research-conclusions.md)。
>
> **安全前置**:本平台就绪等级 `L2_DRY_RUN_ONLY`——只做研究与 dry-run 模拟,**永不**实盘交易、不连券商、不碰真实凭证。本文所有“夏普”“收益”均为**回测 / 建模数字**,不是实盘业绩,也不是收益承诺。

---

## 0. 为什么写这份纪实

量化研究最大的谎言不是“我找到了 alpha”,而是“我找到了 alpha”被一段牛市运气喂出来、自己却信了。

QUANTOS 这套加密研究链,目的从来不是“挖出一个能赚钱的因子”,而是**搭一台不会自欺的诚实机器**,然后老老实实记录它说了几次“不行”、说了几次“行”。最终的账是:一长串严谨的 **NULL**(无信号),夹着**唯一一个**经得起交叉检验的 lead(资金费 carry,建模净夏普 ~2.1,附一长串折扣),外加**一次被如实标注、随后全数撤回的捏造**。

把这三样都摆出来,才叫公信力。

---

## 1. 方法论:诚实是怎么被机器钉死的

研究结论可信,不靠“我很认真”,靠的是**机器在每一步替你挡掉自欺**。这套机制本身已提交、门禁绿、容器化不变。

### 1.1 多重检验诚实(Multiple-Testing Honesty)

挖得越多,越容易“碰巧”挖到漂亮回测。系统对此有专门防线:

- **Deflated Sharpe / PBO / BH-FDR**——`services/research/validation/deflated_sharpe.py`、`services/alpha/multiple_testing.py`(Benjamini-Hochberg FDR),按你试过的因子总数惩罚夏普。
- **试验次数跨进程持久化**——`services/research/experiment_registry/`(stdlib `sqlite3`),让全目录的 Deflated-Sharpe 试验计数 + 方差**不因进程退出而清零**,多重检验账永远诚实累计。失败关闭:加载损坏 / 未初始化存储会直接抛错,绝不“静默重置成 0 次”。

### 1.2 无窥视 OOS(No-Peek Out-of-Sample)

这是裁决主线。**样本内**多漂亮都不算数,要看**无窥视滚动选择 OOS**——选因子时绝不偷看样本外那段:

- `services/research/oos_validation.py`(无窥视滚动选择 OOS)+ `services/research/validation/walk_forward.py`(purged + embargo 的滚动交叉验证),裁决经 `services/research/validation/pipeline.py`、`gate.py` 汇成 ValidationResult。
- 三重门 / 元标签(`labeling.py`)、regime 条件(`regime.py`)、互信息去冗余(`information.py`),共同把“子段幻觉”挤出去。

后文你会看到:同一个 `dist_from_high` 族,样本内 Deflated 0.95、看着像金子;出 regime 一测,直接死亡。这就是无窥视 OOS 的价值。

### 1.3 容器化(Containment,贯穿全程的安全不变量)

诚实机器跑在一个**失败关闭、确定性、离线**的容器里:

- 纯 stdlib、无 numpy/pandas/scipy/torch、无交易所 / 券商 SDK;`services/` 整棵树**零网络 import**(`tests/governance` 用 AST 钉死),网络只允许出现在门外的 `tools/` 抓取器。
- 治理扫描器(`apps/research-console/scripts/run_paper_to_live_governance_check.py`)+ `tests/governance/` 把数据边界钉死:`tools/` 里 5 个抓取器(coingecko / coingecko_volume / cryptocompare / binance_klines / binance_funding)**只含 stdlib + services/tools、无交易所 SDK、无任何下单 / 划转调用**,且只读公开行情数据。
- **关键不变量**:非人类 actor 全程 `auto_approved=0`。**包括下面那个唯一过关的 carry lead 在内,系统从未自动放行任何因子**——所有候选一律 `REQUIRES_HUMAN_REVIEW`。机器能说“这个看起来不错”,但放行权永远在人手里。
- 全链由单一失败关闭门禁 `python scripts/validate.py` 守(治理扫描 → 全 pytest 套件),CI 离线无密钥跑同一条命令。

> 一句话:**这套平台最大的资产不是任何一个因子,而是“它不自欺”这件事本身。** 下面的时间线,就是这台机器一次次说“不行”、最后谨慎说一次“也许行”的全过程。

---

## 2. 研究历程时间线(逐站诚实记录)

数据从 CoinGecko(4h / 日线 / 真实成交量)→ CryptoCompare(中立聚合商,多年日线含 2022 熊市)→ Binance 公开 K 线(免 key 只读,宽度上一个数量级)→ Binance 公开 `fapi` 资金费率,一路加宽、加久、加另类数据维度。

### 站 1 — 短周期 4h / 小时线 = NULL

价格 / GP / 量能因子,**无稳健 OOS edge**。加宽截面能把**样本内** Deflated Sharpe 抬到 **0.586**,看着像有东西——但**无窥视 OOS 直接证伪**(选择性过拟合,样本外 **−2.01**);量能因子净夏普全负。

第一课:样本内的漂亮数字一文不值,除非它能穿过无窥视 OOS。

驱动:`tools/crypto_research_run.py`。

### 站 2 — 日线趋势 `dist_from_high_14` = 出 regime 被证伪

这是最值得讲的一站,因为它**差一点就骗过了所有人**。

- **样本内极其诱人**(2024–25 牛市,CoinGecko,24 币 × 364 日):最优因子全是“距 N 日高点”族(`dist_from_high`),IC ≈ 0.065、**扣成本后**净夏普 ≈ 2.4、Deflated **0.95**;当时连无窥视选择 OOS 都没把它证伪(mean OOS 年化 +3.95 vs IS +2.90,3/4 fold 正)。看到这组数,任何人都想拍板。
- **加币就开始变诚实**:24 → 42 币,把无窥视 OOS 从 +3.95 拉降到 ~1.45–1.73——说明那 24 币是最干净的大盘子集,偏乐观。
- **出 regime 一测就死**(CryptoCompare,5.5 年、23 币 × 2000 日线、2021–2026 含 2022 熊市):
  - 全样本净夏普 **−0.55**、IC **0.001**(≈ 0);8 个价格因子里最好的 `dist_from_high_30` 也只剩 **+0.034**。
  - **逐年净夏普乱翻、毫无稳定性**:2021 **−1.82** / 2022 **+1.0** / 2023 **−1.61** / 2024 −0.05 / 2025 +0.79。
  - 成本曲线从负起步(−0.55 @ 3bps),无盈亏平衡点;长样本无窥视 OOS 仅 +0.42(噪声级)。
- **结论:2024–25 那段是运气窗口(样本期过拟合)。已证伪、归档、不进纸面跟踪。**

第二课:一个能在牛市跑出净夏普 2.4 的因子,可以是纯运气。**没有出 regime 验证的回测,等于没有结论。**

驱动:`tools/crypto_daily_research.py` / `crypto_daily_verify.py` / `crypto_daily_regime.py` / `crypto_daily_composite.py`、出 regime 证伪 `crypto_history_research.py`。

### 站 3 — 价 + 量,Alpha101 级丰富因子 = 同样 NULL

会不会是因子太简单?于是解锁了截面 alpha 引擎(时序 × 截面算子,能表达 Alpha101 那类真因子,无前视测试钉死),在同一 5.5 年 / 23 币面板上跑 15 个精选截面 alpha:

- **仍无跨 regime edge,幸存者 0。** 最好的 `accel_cs` 全样本 SR 0.60,但被 2021 一年带飞(6 年里负 3 年);所有 |IC| < 0.03。

把可表达的因子空间放大几个量级,照样挖不到。这把“瓶颈是数据、不是因子生成”从一句论断,变成了**实证**。

驱动:`tools/crypto_alpha_research.py`(引擎 `services/research/alpha_expression.py`、`interaction_factors.py`、`gp_tree.py`)。

### 站 4 — 336 币宽截面 = 最干净的 NULL

会不会是币太少?接 Binance 公开 K 线:437 个 USDT 对,过滤后 **336 币 × 2309 日线、2020–2026 含 2022 熊市**(比之前宽一个数量级)。跑 15 个截面 alpha:

- **最好全样本 SR 0.15(≈ 0)、|IC| < 0.02、无窥视 OOS 均值 −0.67(1/5 fold 正)、幸存者 0、`auto_approved=0`。**
- 正收益几乎全来自 2020(早期狂野数据),之后逐年转负。诚实保留(并明确**不是** lead):meanrev / low_vol / pv_corr 近 5 年正、但全样本仍负 + OOS 负 = 子段幻觉,不信。

第三课:**更多价 + 量宽度,并没有挖出 alpha。** 缺的不是币数,是另一种数据。

驱动:`tools/crypto_smallcap_research.py` / `crypto_binance_research.py`。

### 站 5 — 蛮力综合搜索 = 过拟合被诚实机器拦下

`tools/crypto_alpha_search.py`,一次性盲搜 261 个截面 alpha。raw 前 15 名 **15/15 全是资金费 carry 变体**,DSR ~0.98–1.0、6–7/7 年为正——诱人到极点。

**但对全 261 个的选择 OOS 是负的(−0.45)。** 这是典型的 best-of-N 过拟合:挖 261 个,挑最好那个,几乎一定漂亮——而诚实机器把它拦下了。

第四课:**carry 这个东西是真的、跨 ~10 种平滑稳健;但要靠经济先验 + 稳健平滑去用,绝不能靠盲搜。** 先验 + 诚实机器 > 蛮力,这一站给了铁证。

### 站 6 — 资金费 carry = 唯一验证通过的 lead

换了一种数据维度——**资金费率(perp funding rate)**,它代表永续合约的杠杆 / 情绪 carry,与价 / 量正交。这是前面所有 NULL 都缺的那“另一种数据”。详见第 3 节。

---

## 3. 唯一过关的 alpha:资金费 carry(流动永续)

> 再次强调:这是 **lead(线索),不是已证实可规模化的 alpha**;是**有经济根基、回测真赚钱**的效应,但**可部署夏普远低于 2.0**,是一种**拥挤的 yield(收益),不是魔法 alpha**。

数据源:Binance 公开 `fapi` 的 `fundingRate`,免 key、无账户、只读,治理边界钉死(`tools/binance_funding_fetch.py`)。

### 3.1 截面研究

驱动 `tools/crypto_funding_research.py`。`fund_avg7 = cs_rank(neg(ts_mean(funding, 7)))`,**115 永续 × 2661 天**:

- 净 SR **1.18 @ 3bps**,换手仅 **0.26**(低换手=低成本敏感)。
- 单因子无窥视 OOS mean **+0.40**、**3/5 fold 为正**;成本盈亏平衡 ~30bps(现实 ~10bps 下净 ~0.9);**6/7 年为正,含 2022 熊市**。
- **这是第一个同时穿过 OOS + 成本 + 跨 regime 三道关的因子。**

### 3.2 真实损益回测——carry 里真正赚钱的来源

驱动 `tools/crypto_carry_strategy.py`。之前所有站只算价格收益;永续 carry 还会**实收 / 实付资金费**(多空两腿都结)。净收益 = 价格 + 收到的资金费 − 手续费。

de-redundant carry 组合,**115 永续 × 2186 持仓日**:

| 口径 | 净夏普 |
|---|---|
| 只算价格 | 1.39 |
| + 资金费 | 2.25 |
| 净 @ 5bps | **2.03** |
| 净 @ 20bps | 1.36 |

- 日均收资金费 **5.43bps**,换手 **0.28**。
- **7/7 年全正,含 2022 熊市**(净 @ 5bps:'20 +4.4 / '21 +2.5 / '22 +1.2 / '23 +1.1 / '24 +1.9 / '25 +2.0 / '26 +2.3)。
- 一个关键洞察:之前看到的“因子衰减”其实是**没算资金费的假象**——价格预测力确实在衰减,但资金费 carry 稳。换句话说,收益越来越是 **carry 收益(yield)而非价格 alpha**。

### 3.3 流动子集 + 滑点压力测试——~2.1 这个数字的来历

驱动 `tools/crypto_carry_stress.py`。按流动性分层给每个币建模单边滑点(p ≥ 0.75 → 5bps / ≥ 0.5 → 12bps / ≥ 0.25 → 25bps / 否则 50bps),分别跑“全币”与“流动半数”,报净夏普 / 逐年 / maxDD / 最差日。

- **流动永续、扣滑点后建模净夏普 ~2.1(实跑 2.09)、年化 ~47%、7/7 年正含 2022、最大回撤 maxDD ~26%。**
- ⚠️ **必须钉死的定性**:这里的滑点是**建模的、不是实测的**。5bps 是乐观值;把成本提到 20bps,夏普就掉到 1.4。**~2.1 是建模上限,不是可交付业绩。**

### 3.4 carry lead 的全部诚实折扣(缺一不可)

文档反复强调:**这是有经济根基、回测真赚钱的效应,但可部署夏普远低于 2.0,是拥挤的 yield 不是魔法 alpha。**

1. **幸存者偏差**——回测只含活到今天的永续;退市 / 极端资金费币缺失 → 回测偏高。
2. **滑点是建模而非实测**——5bps 乐观;现实小盘滑点会吃掉 carry(carry 本身才 ~5.4bps/天)。
3. **拥挤 / 可能衰减**——carry 是有据可查但拥挤的加密效应;早年(2020–21)大,近年(2023–26)弱(注:此“衰减”在只看价格时被夸大,加入资金费后 carry 本身稳)。
4. **尾部风险**——暴动 / 爆仓会崩,回测**未建模**。
5. **容量有限**。
6. **IC ≈ 0(尾部效应)**——收益来自尾部分布而非线性 IC。
7. **OOS 噪声大**——funding 研究里有 2 折为负,被某一折 +2.57 拉高。

**定性结论(钉死)**:carry 是**第一个正当的纸面跟踪候选**(区别于已证伪、被归档的 `dist_from_high_14`),是 **lead,不是已证实可规模化的 alpha**。`paper_track_start` 已记。下一步坐实 / 规模化:抗幸存者偏差的永续历史、真实 funding + fee 损益、并入其它免费持仓数据(OI / 多空比)、纸面跟踪看是否衰减。

---

## 4. 核心教训

### 4.1 数据种类 > 因子数量

这是整段研究最贵的一课,而且是**实证**出来的,不是拍脑袋:

- 4h → 日线 → Alpha101 级丰富因子 → 336 币宽截面——**沿“价 + 量”这一条线一路加码,全是 NULL。**
- 一旦换到**另一种数据**(资金费率,代表杠杆 / 情绪、与价量正交),立刻出现唯一一个过关的 lead。

→ **“挖更多花哨因子”是错的杠杆,已被实证。真正的杠杆是数据护城河**:小盘 / 低流动性长尾(低效定价处)、另类数据(链上 / 资金费 / 情绪 / 盘口微结构)、其它资产类别、更高 / 微结构频率。这些需要用户拍板成本 / 资源的“口子”。

截面 alpha 引擎 + 快引擎本身**不产 alpha**,但它们是**永久资产**——有了更好数据,把新源“填进 `SourceSpec`”接进来,现成的截面 alpha 立即可跑。

### 4.2 先验 + 诚实机器 > 蛮力

站 5 给了铁证:**盲搜 261 个 alpha**,raw 榜单看着金光闪闪(全是 carry 变体,DSR ~1.0),但选择 OOS 是 **−0.45**——纯过拟合,被诚实机器当场拦下。

同一个 carry 效应,**靠经济先验 + 稳健平滑去用**(站 6),就穿过了 OOS + 成本 + 跨 regime;**靠盲搜去挖**(站 5),就是过拟合。

→ **不是“试得越多越好”,而是“先有经济假设,再让失败关闭的机器替你证伪”。** 这正是容器化诚实机制存在的意义。

---

## 5. ⚠️ 纠错文化:一次被如实标注的捏造

公信力不是“我从不犯错”,而是“我犯错时能不能自己抓出来、并留痕”。这件事必须写进纪实。

`docs/PROJECT_STATE.md` §7 明确记载一处历史造假:

> ⚠️ 历史教训:此前曾把**捏造的**小盘“结论”(`argmax_cs20` 等)写进文档 + 提交 + 推送,被 task 通知戳穿后已**全数订正撤回**——**没亲眼在工具输出里看到的数字,绝不写。**

这意味着:某次曾把未经工具实跑验证的“漂亮小盘结论”当成真结果写进文档、提交、推送;随后被自动 task 通知发现、并**全数撤回订正**。(注:`argmax_cs20` 本身只是 `tools/crypto_alpha_research.py` 里一个合法的因子表达式 `cs_rank(ts_argmax(close, 20))`——被撤回的是**为它编造的结果数字**,不是这个因子名的存在。)

**留痕说明(诚实标注其中的不一致)**:历史提交 `b390b7f` 的标题仍把 `argmax_cs20` 描述为“小盘长尾首个像样候选(扛成本 / 6 年全正,但薄 + 幸存者偏差 = lead 非结论)”。**注意:`b390b7f` 已不是 git 顶部提交**——当前 HEAD 在 `051a32f`(carry 部署压力测试),`b390b7f` 已被其后约 14 个提交(全是资金费 carry 工作)取代。这条旧 commit 标题与 §7 的撤回记录**并不完全一致**(标题里还在正面讲它)。**以权威活文档为准**:`PROJECT_STATE.md` §7 与 `crypto-research-conclusions.md` 将 `argmax_cs20` 归为**已撤回的捏造**,并把唯一正当 lead 定为**资金费 carry**。

把这条不一致也摆出来,而不是悄悄抹平——这本身就是纠错文化的一部分:**活文档优先,孤证(单条历史 commit 标题)服从于交叉一致的权威记录,且不一致本身公开可见。**

新铁律(已内化):**没亲眼在工具输出里看到的数字,绝不写进文档。**

---

## 6. 战略读数(钉死)

- **错的杠杆(已实证)**:继续在“大盘 + 价 + 量 + 日线”里挖更花哨的因子。
- **真的杠杆**:数据护城河——小盘 / 低流动性长尾、另类数据(链上 / 资金费 / 情绪 / 盘口微结构)、其它资产类别、更高 / 微结构频率。这些需要用户开“口子”(拍板成本 / 资源)。
- **永久资产**:截面 alpha 引擎 + 快引擎 + 全套诚实机器。它们不产 alpha,但有更好数据时即插即用,且永远替你挡掉自欺。
- **唯一在跟踪的 lead**:资金费 carry,纸面跟踪中,带全部折扣,**远未到可部署**。

---

## 附:相关文件(绝对路径)

**活文档 / 结论**
- `H:\code\QUANTOS\docs\PROJECT_STATE.md`(§4 当前实证结论、§7 提交锚点与撤回记录)
- `H:\code\QUANTOS\docs\crypto-research-conclusions.md`

**carry lead 驱动(`tools/`,门外只读)**
- `crypto_funding_research.py`、`crypto_carry_strategy.py`、`crypto_carry_stress.py`、`crypto_alpha_search.py`

**NULL 站驱动**
- `crypto_research_run.py`(4h)
- `crypto_daily_research.py` / `crypto_daily_verify.py` / `crypto_daily_regime.py` / `crypto_daily_composite.py`、`crypto_history_research.py`(日线趋势 + 出 regime 证伪)
- `crypto_alpha_research.py`(Alpha101 级)
- `crypto_smallcap_research.py` / `crypto_binance_research.py`(336 币宽截面)

**抓取器(门外只读,治理边界钉死)**
- `coingecko_fetch.py`、`coingecko_volume_fetch.py`、`cryptocompare_fetch.py`、`binance_klines_fetch.py`、`binance_funding_fetch.py`

**诚实机器 / 容器化不变量**
- 安全契约:`H:\code\QUANTOS\quantos\contracts\config.py`
- 治理扫描器:`H:\code\QUANTOS\apps\research-console\scripts\run_paper_to_live_governance_check.py`
- 数据边界测试:`H:\code\QUANTOS\tests\governance\test_crypto_data_boundary.py`
- 单一失败关闭门禁:`H:\code\QUANTOS\scripts\validate.py`
- agent 安全咽喉点:`H:\code\QUANTOS\services\intelligence\agent_loop.py`
- 无窥视 OOS / 多重检验:`H:\code\QUANTOS\services\research\oos_validation.py`、`H:\code\QUANTOS\services\research\validation\`、`H:\code\QUANTOS\services\alpha\multiple_testing.py`、`H:\code\QUANTOS\services\research\experiment_registry\`
