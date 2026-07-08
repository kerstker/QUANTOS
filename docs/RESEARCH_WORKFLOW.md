> 目的:回答"一个人从零开始,该怎么用 QUANTOS 做一次研究"。`CLAUDE.md` 回答的是"系统里有
> 什么",这份文档回答"怎么用"。两者冲突时以 `CLAUDE.md` 为准(系统实际情况变化更快)。
>
> 读者假设:你能在仓库根目录跑 Python、装好依赖(见根目录 `README.md`),但没读过任何设计
> 文档。本文只引用**真实存在**的命令/文件路径——每条命令都在写这份文档时核实过其对应脚本
> 确实有 `if __name__ == "__main__"` 入口或确实是可跑的模块。

# 研究/回测平台使用流程

## 0. 一句话总览:研究的全生命周期

QUANTOS 的研究工作从"有一堆历史数据"到"一个信号有没有资格进干跑执行",走的是同一条链:

```
数据(PIT 存储、真实快照)
  → 假设 / 因子生成(手写表达式 或 自动挖掘搜索)
  → 统计验证不自欺关卡(IC 诊断 → DSR/CPCV/purged walk-forward → evaluator 判定)
  → 组合构建(与已知边正交化、风险模型、组合优化)
  → 干跑执行(fail-closed 预交易门 + kill-switch + 纸面成交 + 对账)
  → 【可能】晋级(生命周期状态机,唯一能到 APPROVED 的路径是人工批准)
  → 退役/衰退监控(realized vs backtest 偏离 → DEGRADED/PAUSED/RETIRED)
```

这条链在前端 `apps/research-console` 里对应 7 个导航分组(单一真源见
`apps/research-console/src/app/routes.tsx`):

| 导航分组(中文/英文) | 对应链路阶段 | 代表页面 |
|---|---|---|
| 总览 OVERVIEW | 入口仪表盘 | 新手引导 Getting Started(`/` 和 `/research` 的真实落地页)、指挥中心 Command Center |
| 数据 DATA | 原料 | 数据目录、加密市场、外汇市场 |
| 因子研究 ALPHA RESEARCH | 假设/因子生成 | 研究运行、IC 面板、因子工厂、Alpha 资产库、相关图谱、DSL 检查器、研究结论 |
| 验证 VALIDATION | 不自欺关卡 | 验证门、实验登记、回测战果、计算实验室 |
| 组合与风险 PORTFOLIO & RISK | 组合构建 | 组合构建、种群优化器、风险仪表盘、衰退监控 |
| 执行与跟踪 EXECUTION | 干跑执行 + 前向证据 | 干跑流水线、Carry 就绪、纸面跟踪、实盘路线、交易台、GoNoGo 证据、生命周期 |
| 治理与系统 GOVERNANCE & SYSTEM | 审计/健康/诚实完整度 | 晋级审批、证据链、事件流、系统健康、系统完整度、架构 |

**读这份文档时请始终记住安全边界(详见 §5):QUANTOS 现处于 `L2_DRY_RUN_ONLY`——本文档描述
的整条链路,终点是「干跑」(纸面成交,绝不真发单),不是实盘。任何"晋级"到干跑及以上都要经过
下面 §2.3 的 fail-closed 验证门,状态机本身的结构性设计决定了 AI/自动化到不了人工批准这一步——
但这句话有一个 `CLAUDE.md` 标注的限定条件,见 §5 末尾的 `promotion_api` 注脚,不要只读这一句话
就当作绝对保证。**

---

## 1. 环境准备

```bash
# 1) 后端只读 console API(计算好的研究快照,GET-only,无券商/OMS)
python -m uvicorn services.research.console_api.main:app --host 127.0.0.1 --port 8400

# 2) 前端指向真实后端(默认是 mock 模式,不加这两个环境变量就看不到真数据)
cd apps/research-console
VITE_API_URL=http://127.0.0.1:8400 VITE_MOCK_MODE=false npm run dev
# 打开 http://localhost:3100/research —— 会先落到「新手引导 Getting Started」静态说明页
# (纯说明、不发请求),指挥中心在总览分组的第二个入口

# 3)(可选)如果想从前端页面直接触发研究任务(而不是自己敲命令行),再起 research_runner——
#    它是整个系统里唯一被允许"写"的接口,且被治理测试钉死为研究专用白名单(见 §2.6)
python -m uvicorn services.research.research_runner.main:app --host 127.0.0.1 --port 8420
cd apps/research-console && VITE_API_URL=http://127.0.0.1:8400 \
  VITE_RUNNER_URL=http://127.0.0.1:8420 VITE_MOCK_MODE=false npm run dev
```

不想开前端,直接看命令行/JSON 产物也完全可以——本文下面每一步都给出对应的命令行入口和落盘
文件路径,console 只是"看"的一层。

---

## 2. 从零开始做一次研究:具体步骤

### 2.1 先看有什么数据(不要重新造轮子)

**先看数据目录,而不是先去抓新数据。** 这个项目吃过一次"以为数据广度是瓶颈,结果去查才发现
数据广度早就不小"的教训(见 §3 常见陷阱)。

- 页面:「数据」分组下的**数据目录**(`/research/data-catalog`)、**加密市场**
  (`/research/crypto-live`)、**外汇市场**(`/research/fx-research`)。
- 对应只读端点:`GET /api/v1/console/data-catalog`、`/crypto-live`、`/fx-research`。
- 实际落盘的数据库(`data/cache/` 下,数量级供直觉参考,截至写作时):
  - `crypto_binance.db`(336 个币种、约 6 年 K 线,~116MB)——宽宇宙价格/量能类因子的基础。
  - `crypto_funding.db`(115 个币种,资金费率,~57MB)——carry(资金费套利)类因子的基础。
  - `crypto_daily.db`(18 个币种日线,~4.5MB)——trend(趋势)类因子的基础。
  - `fx.db`(46 种货币,ECB/Frankfurter,约 27 年,~77MB)——外汇截面研究。
  - `macro_fred.db`(FRED 6 条市场型宏观日频序列,~4MB)——宏观 regime 分桶用。
  - `dry_run.db`——每一次干跑跑批的存证(见 §2.4)。
  - `experiment_registry.db`——每次实验评估的不可变制品(见 §2.3)。
- 数据集介绍/研究结论沉淀在**研究结论**页(`/research/findings`,对应
  `GET /api/v1/console/research-findings`)——已经跑过的结论,先看这里,别重复劳动。

### 2.2 生成 / 挖掘候选因子

因子(alpha 候选)有两条路,都是真实、可跑的:

**(a) 手写因子表达式,过 IC 面板做初筛横向对比**

`services/research/ic_panel.py` 提供逐期截面 rank IC 序列 + 聚合统计(mean/ICIR/hit-rate/
t-stat/逐年)。已注册的因子/宇宙在 `tools/ic_panel_registry.py`(命名宇宙比如
`crypto_funding`/`crypto_oi_majors`/`crypto_daily`/`crypto_binance_wide`,命名因子比如
`carry_composite`/`crowd_contra`/`trend_composite`)——**新候选只需在这个注册表里加一行**,不用
重写抓面板/算 IC 的样板代码。跑驱动器:

```bash
python -m tools.ic_panel_research
```

产出 `data/cache/ic_panel_snapshot.json`,console 页**IC 面板**(`/research/ic-panel`,
`GET /api/v1/console/ic-panel`)直接读这份快照渲染表格。**明确边界:IC 面板是初筛诊断,不是
晋级门禁**——它不碰 `AlphaCatalog`/evaluator,不会让任何因子自动往下游走一步。

**(b) 自动化搜索/挖掘(进化算法 + 正交新颖性奖励)**

`services/research/alpha_factory.py::run_alpha_factory` 是真实的跑分/进化引擎(可插拔字段×窗口
基因、按经济收益夏普评分)。一个可直接跑的驱动器例子——`tools/crypto_factory_loop.py`(loop-
until-dry 自主正交挖掘,逐轮用真实的门:与已知边正交、PIT-safe、扛得住 1 天执行滞后 + 10bps
成本才算「发现」):

```bash
python -m tools.crypto_factory_loop
```

产出 `data/cache/crypto_factory_loop.json`。同一引擎还有更朴素的单轮版本
`tools/crypto_factory_orthogonal.py` 和其它挖掘驱动器(`tools/crypto_alpha_search.py`、
`tools/crypto_gp_run.py` 等)——命名模式是 `tools/crypto_*` 或 `tools/*_research.py`,想看某个
具体研究方向已经跑过什么,先用文件名 + 头部 docstring 扫一遍 `tools/` 目录,大概率已经有人跑过。

因子研究页面对应:**因子工厂**(`/research/alpha-factory`)、**Alpha 资产库**
(`/research/alpha-zoo`)、**相关图谱**(`/research/alpha-graph`)、**DSL 检查器**
(`/research/dsl-explorer`,查看因子表达式 AST)。

### 2.3 送进验证关卡:为什么存在、在哪触发

一个因子在样本内看起来好看**不代表**它是真边——`services/research/validation/` 这一组模块就是
专门对付这件事的"不自欺关卡":

- **purged / embargoed walk-forward**(`walk_forward.py`):时间序列交叉验证绝不能让训练集
  "看到"和测试集时间上重叠或紧贴的数据(自相关会造成信息泄漏)。walk-forward 强制测试窗口严格
  在训练窗口之后;purged k-fold 额外在每个测试折两侧挖一个"隔离带"(embargo)。
- **CPCV 组合式清洗交叉验证**(`cpcv.py`):不是给一个标量,而是把时间轴切成 N 组,遍历所有
  C(N,k) 种"留 k 组做测试"的组合,每种组合都在训练组上选最优候选、在测试组上打分——最终得到一个
  **样本外表现的分布**,而不是一个数字。一个脆弱的边在这个分布里会显著左偏/常为负,即便它的全
  样本夏普看起来很漂亮。
- **Deflated Sharpe Ratio(DSR)+ Probability of Backtest Overfitting(PBO)**
  (`deflated_sharpe.py`):量化"多重检验"代价——你试的候选越多,纯凭运气冒出一个高夏普的概率
  就越高。DSR 把"试验次数"和"试验间方差"纳入,把观测夏普打折成一个概率(是否统计显著跑赢
  纯运气);PBO(CSCV)给出"这个被选中的策略其实是过拟合产物"的概率。
- **验证门**(`gate.py::ValidationGate`):把以上证据汇总成一个判决——`PASS` / `WARN` / `FAIL` /
  `REQUIRES_HUMAN_REVIEW`。默认阈值:`min_oos_sharpe=0.5`、`min_deflated_sharpe=0.60`、
  `max_pbo=0.50`、`min_profitable_regime_frac=0.50`;试验次数达到 30 次(或试验数少但 DSR 边缘)
  强制转人工复核。**这个门只是指标仲裁器,不重新跑回测**——它信任上游(`services/alpha/evaluator.py`)
  诚实地算出了 `oos_sharpe`/`deflated_sharpe`/`pbo`/`walk_forward_passed`,泄漏检测证据必须
  独立提供(见 `services/research/leakage_evidence.py`)。

**在哪触发**:不需要手动一步步调用这些模块——`services/alpha/evaluator.py` 把它们串成一条流水
线(PIT 截面 IC 序列 → 净成本回测 → 全 catalog 口径 DSR → `ValidationGate`),`alpha_factory` 的
搜索循环、`services/research/session.py`(可复现研究会话编排器)在挖掘过程中就会喂给它。对应页面
**验证门**(`/research/validation`)、**实验登记**(`/research/experiments`,每次评估的不可变
制品落 `experiment_registry.db`)、**回测战果**(`/research/backtest-results`)。

### 2.3.5 组合构建(诚实说明:引擎已实现,但暂未接入下面 §2.4 的干跑走查路径)

`services/portfolio/construction.py::build_portfolio` 是真实的组合构建引擎:协方差矩阵
(`risk_model.py::covariance_matrix`)→ 收缩(`shrink_covariance`)→ risk-parity 权重
(`optimizer.py::risk_parity_weights`)→ 集中度上限(`cap_weights`)→ 成本感知再平衡
(`cost_aware_rebalance`,与上一期权重比较算换手)→ 输出 `RebalancePlan`。种群优化器
(`services/alpha/population_optimizer.py`,页面**种群优化器** `/research/population-optimizer`)
在这之前先从一堆相关候选里选出真正互补的去冗余子集。

**诚实边界:下面 §2.4 举例的两个真实干跑驱动器(`tools/dry_run_carry_multi.py`、
`tools/dry_run_crowd_contra_multi.py`)都不 import `services/portfolio/construction.py` 或
`optimizer.py`——它们直接从因子 legs(COMPOSITE/QUANTILE)算权重,走的是更简化的单因子权重路径,
不经过这里说的组合构建引擎。** 这个引擎目前没有对应的 `tools/` CLI 驱动器把它接入干跑走查链路
(console 的**组合构建**页 `/research/portfolio`、端点 `/api/v1/console/portfolio` 在只读展示层面
调用了 `risk_parity_weights`,但同样不是 `build_portfolio` 的完整管线,也不写回任何执行 book)。
也就是说,跟着本文档 §2 走一遍不会实际用到 `build_portfolio` 这一步——它是已实现、有单测
(`tests/portfolio/test_construction.py`)覆盖的真实引擎,但目前只能自己在 Python 里手写调用
(`build_portfolio(plan_id, portfolio_id, returns_by_alpha=...)`),没有现成命令行入口。

### 2.4 通过验证后:干跑流水线

一个因子/组合过了验证门,不代表可以直接实盘——下一步是**干跑**(paper,纸面成交,绝不真发单,
但走的是和实盘同一套预交易门 + kill-switch + 对账逻辑)。`services/execution/dry_run_pipeline.py`
的数据流:

```
权重 → 目标整数手数 → build_intents → 逐意图过预交易门(fail-closed) + kill-switch
     → 干跑适配器受理(纸面,绝不真发) → PaperFill → reconcile 对账(恒等式残差应≈0)
     → 哈希链审计 → DryRunRun 落 data/cache/dry_run.db
```

已有的真实驱动器例子(每一条都是"策略无关的执行骨架"跑一条具体策略,可以照着写新的):

```bash
# carry(资金费套利)策略,周度滚动
python -m tools.dry_run_carry_multi

# crowd_contra(OI 持仓拥挤度反向)策略,周度滚动,含资金费现金流
python -m tools.dry_run_crowd_contra_multi
```

跑完会在终端打印一份摘要(总收益、夏普、最大回撤、门批准/拒绝数、对账残差),同时把这次
`DryRunRun` 追加进 `data/cache/dry_run.db`(与之前的策略并列存证,不覆盖历史)。

### 2.5 干跑结果去哪看

- 页面:**干跑流水线**(`/research/dry-run`,对应 `GET /api/v1/console/dry-run`)——单次快照 +
  多期净值曲线(SVG)。
- 相关联的执行现实诊断:**Carry 就绪**(`/research/carry-readiness`)、**纸面跟踪**
  (`/research/paper-trading`,前向 paper-track 持久层,`paper_track.db`)、**生命周期**
  (`/research/lifecycle-monitor`,realized vs backtest 的偏离监控)。

**§0 总览承诺的最后一环——退役/衰退监控(realized vs backtest 偏离 → DEGRADED/PAUSED/
RETIRED)——由 `tools/alpha_maintenance_sweep.py` 驱动**,不是只读页面自己算出来的:

```bash
python -m tools.alpha_maintenance_sweep
```

它读 `paper_track.db` 的前向证据算 live-vs-backtest 健康,经审计的 `AlphaLifecycleManager`
以**非人 actor** 降级/退役衰退的 alpha(只到 `DEGRADED`/`PAUSED`/`RETIRED`,containment 保证
绝不 `APPROVED`),落一份只读快照 `data/cache/alpha_maintenance_status.json`——**生命周期**页
展示的状态就来自这份快照。**诚实边界:这条命令目前不在下面 §2.6 `research_runner` 的
`RUN_KINDS` 白名单里**——前端「研究运行」页的按钮点不到它,只能命令行或定时任务(见
`scripts/register_schedules.ps1`)跑。

### 2.6 (可选)从前端直接触发,而不是敲命令行

**研究运行**页(`/research/runs`)背后是 `services/research/research_runner`——整个系统里**唯一**
被允许"写"的服务,`POST /run {kind, params}` 只接受一个白名单(`RUN_KINDS`,定义在
`services/research/research_runner/run_kinds.py`),比如 `carry_strategy`/`oi_research`/
`combo_book`/`factory_orthogonal`/`factory_loop` 等——每个 kind 对应上面 §2.2/§2.3 的某个研究
工具,没有任何订单/券商/凭证入口(被 `tests/governance/test_research_runner_boundary.py` 机器
钉死)。在页面上点按钮,本质上就是异步跑了一遍你也可以在命令行敲的那条 `python -m tools.xxx`。

---

## 3. 怎么读懂结果

### 3.1 因子层面的指标

- **IC(Information Coefficient)**:某一期,因子值的截面排名与"下一期收益"的截面排名之间的
  相关系数(这里统一用 Spearman rank IC)。直觉:这一期因子排得靠前的资产,接下来收益是不是也
  排得靠前?单期 IC 噪音很大,几乎不能单看。
- **ICIR(IC Information Ratio)= mean(IC) / std(IC)**:把很多期的 IC 序列平均值除以标准差,回答
  "这个因子的预测力是稳定的,还是几期运气堆出来的"。
- **hit-rate**:IC 同号(比如都为正)的期数占比——一个更直观但信息量比 ICIR 少的稳定性度量。
- **t-stat**:`mean_ic / (ic_std / sqrt(n))`,回答"这个 mean_ic 在统计上是不是显著不为零"(注意:
  `services/research/ic_panel.py` 里样本期数 n<10 时全部诚实返回 `None`,不硬凑一个数字)。

### 3.2 组合/策略层面的指标

- **Sharpe(夏普比率)**:单位波动率下的超额收益,回答"这条收益曲线好不好",但**完全不管这个
  数字是不是挑出来的**(下面 DSR/PBO 才管这件事)。
- **Deflated Sharpe Ratio(DSR)**:把"你试了多少次"折算进去后,这个观测夏普是不是显著跑赢
  "纯粹靠运气从这么多次试验里选出一个最高夏普"。是一个 [0,1] 的概率,不是夏普本身的替代品。
- **PBO(Probability of Backtest Overfitting)**:CSCV 方法给出的"这个被选中的策略在真实样本外
  会不会翻车(表现劣于中位数)"的概率估计。
- **walk-forward / CPCV 通过与否**:见 §2.3——不是一个数字,是"样本外表现分布/序列是否站得住"
  的结构性检验。
- **最大回撤(max drawdown)**:历史最高点到之后最低点的最大跌幅,衡量尾部/持续亏损的严重程度,
  夏普看不出这个。

### 3.3 干跑层面的指标

- **门批准 / 拒绝(approved/rejected)数**:预交易门(`PreTradeRiskGate`)对每一笔意图的裁决,
  fail-closed——异常/风险超限直接拒,不放行。
- **对账残差(residual)**:`reconcile` 用平均成本法算出的 PnL 恒等式(已实现 + 未实现 − 成本)
  和用别的口径重算应该几乎相等,残差理应在浮点误差量级(实测常见 ~1e-9 量级)——残差偏大是一个
  账没对上的信号,不该忽略。
- **raw 曲线 vs vol-target(VT)曲线**:raw 是不调杠杆的原始净值曲线;VT 是按目标波动率动态调
  杠杆后的曲线——**VT 才是这个项目里"可部署"口径**,raw 曲线常常回撤大得多(carry 策略的 raw
  回撤能到 77%,VT 后降到约 22%,是同一条策略两种不同的杠杆假设,不是两个策略)。

### 3.4 provenance(数据来源)标记——这个项目"不自欺"文化的核心机制

console API 的每个只读端点都在返回体里带一个 `data_provenance` 字段,前缀 `REAL_` 或
`SYNTHETIC_`,例如:`REAL_IC_PANEL_OFFLINE_SNAPSHOT`(IC 面板)、
`REAL_DRY_RUN_OFFLINE_SNAPSHOT`(干跑)、`REAL_CRYPTO_COINGECKO_OFFLINE_SNAPSHOT`(加密行情)、
`REAL_FX_ECB_OFFLINE_SNAPSHOT`(外汇)、`REAL_REPO_INVENTORY`(系统完整度页,活的仓库自省)——
对应还没有真实引擎/快照支撑的页面,fail-closed 退回 `SYNTHETIC_DETERMINISTIC`(确定性合成数,
**不是随机瞎编**,但也不是真实研究结果)。**看任何一个页面/端点前,先看这个字段**——`REAL_*`
才是"这是从真实数据/真实引擎算出来的";`SYNTHETIC_DETERMINISTIC` 是"这个子系统的真实实现还没
接上,这里显示的是占位形状,不代表任何研究结论"。前端读取快照时还会做运行时校验
(`assertConsoleSnapshot`),一旦端点返回的字段结构漂移,页面会显式报错 + 诚实回退到 mock,而不
是悄悄渲染 `undefined`。

---

## 4. 常见陷阱(项目自己吃过的真实教训)

- **样本内好看 ≠ 真边,无窥视(no-peek)OOS 是唯一裁判。** 加密 4 小时因子曾经样本内 DSR 到
  0.586,看起来像一条真边;换成无窥视的滚动 walk-forward 样本外检验后被证伪——那 0.586 是"挑
  出来的"选择性过拟合,不是真实预测力。类似地,一个跨全量 46 种货币的 FX 趋势因子样本外一度
  +0.70,换成"可交易宇宙关卡"(剔除锚定/历史停用/不可交易货币 + 成本扫描)后在所有成本档位都
  转负(−1.1 到 −1.7)——污染源是不可交易货币,不是真实的趋势边。
- **宽宇宙不等于更好的因子。** 336 币宽宇宙上的动量类因子(`momentum_7_wide`)IC 明显为负
  (约 −0.063),与这个项目"宽宇宙价格类因子历史上多数年份负夏普"的既有结论一致——数据变多不会
  自动把一个不成立的因子变成立。
- **数据广度往往不是真正的瓶颈,现实主义(执行摩擦)才是。** 这个项目在"要不要再多抓数据"上
  拐过一次弯:发现数据广度早就不小(Binance 336 币 6 年、FX 46 种 27 年),真正被搁置的是"已经
  验证过的候选因子该不该往下游走",以及"理想化收益 vs 真实执行现实"的落差。冲击-only 的容量
  估算曾经把可部署容量高估约 300 倍——真正绑定的约束是参与率上限,不是市场冲击系数本身;真实
  校准区间是 carry 约 100–2000 万美元 / crowd_contra 约 360 万–7200 万美元,而不是"看起来很大"的
  单点数字。
- **gross(毛)分散故事,不等于净成本执行后依然成立。** carry + crowd_contra 两条边在收益流层面
  近乎不相关(相关系数 ≈ −0.04),等权/逆波动组合把夏普从单条 1.3–1.4 抬到 1.95、回撤减半——这
  是一个真实、干净的"分散降低风险"故事。但把它们**真的**塞进同一个执行 book(整手、真实门、
  真实成本、vol-target)后,三种统一执行方式都不敌 `crowd_contra` 单条自己跑——根因是 1.95/8% 那
  个漂亮数字是 gross 的,净成本 + vol-target 下 carry 的贡献被压缩到拉不动组合。**结论:一个分散
  故事必须过执行现实这一关才能算数,不能只看收益流层面的相关系数。**
- **现实主义引擎抓出过真实的数据质量 bug。** 在给 trend 策略做容量分析时,`crypto_daily.db` 的
  volume 字段被发现跨币种单位不一致(个别币种和 BTC 相差约 17 个数量级,physically 不可能),
  项目诚实标注 `UNRELIABLE_VOLUME_UNITS`、不报假数字,而是把它列成独立的修复任务——这类"诊断
  引擎顺手抓出数据 bug"的情况值得认真对待,不要因为它不是"你要找的那个问题"就忽略过去。
- **拥挤度/crowding 是一个独立的风险维度,不能只看 IC/Sharpe。** `crowd_contra` 是一个基于持仓
  拥挤度反向操作的因子,天然需要独立的拥挤度监控(`services/portfolio/crowding_gate.py`)——统计
  上显著的边,如果自己在把市场推向拥挤,也可能是自我实现的风险,这也是为什么干跑驱动器里能看到
  独立于预交易门之外的"拥挤门"裁决记录。

---

## 5. 安全边界(重申,非协商)

- 这套流程的终点是**干跑(dry-run)**——纸面成交,预交易门 + kill-switch 全程 fail-closed,
  **绝不向任何券商真实发单**;`services/trading/broker_adapter.py` 的 `LiveBrokerAdapter` 在
  L2 下结构性拒绝构造。
- 从因子候选到干跑之间的每一步(§2.3 验证关卡)都是 fail-closed 的:输入超出合理范围直接判定
  失败,而不是放行;试验次数多或指标边缘会被强制转人工复核,不会静默通过。
- "晋级"指的是 `services/alpha/lifecycle.py` 的状态机(`DRAFT → TESTING → VALIDATED →
  PAPER_TRADING → SHADOW → APPROVED → …`)——其中 `APPROVED` 是**唯一**授权走向实盘的状态,
  且这个状态机的结构性设计决定了**非人类 actor 到不了 APPROVED**(自动化/AI 研究循环最多只能把
  一个 alpha 推到 `SHADOW`)。真正的多方人工批准机制见 `services/governance/approval.py`。
- **限定条件(`CLAUDE.md` 2026-07-06 注脚,与本条同属"非协商安全边界"的一部分,不能只靠状态机
  本身的结构性保证)**:上面这句"非人类 actor 到不了 APPROVED"说的是状态机本身
  (`can_alpha_transition` 校验 `actor.kind == HUMAN`)。但如果启动了 `services/governance/
  promotion_api`(端口 8460,**默认不启动**)、且治理 token(`key/governance_token.txt`)落入了
  自动化/agent 运行环境手中,这个 build-time 结构性保证会被绕过——持有该 token 的任何本机进程
  可以用三个自报的 `X-Operator` 标签走完三方批准并真的把状态推到 `APPROVED`,零代码变更、零
  code-review 痕迹。即便走到 `APPROVED` 也不等于解锁实盘(LIVE 仍被 readiness pin + 五个 LIVE
  旗帜结构性挡死),但「agent 运行环境不得持有 `key/governance_token.txt`」本身就是这条安全边界
  的一部分,不是可有可无的操作细节。
- 本文档只描述研究/回测/干跑范围内的流程。任何关于"实盘怎么做"的内容不在这份文档的范围内,也
  不应该从本文档的任何一步自然推出"这样就能自动实盘"的结论——实盘路径是显式独立的人工门控项目
  (见 `docs/runbooks/live-trading-runbook.md`、`docs/PHASE_3A_GO_NO_GO_CHECKLIST.md`),不是本文
  这条研究链路的自动延伸。

---

## 6. 想看更多细节,去哪找

- **`CLAUDE.md`(仓库根目录)**——"系统实际有什么"的权威地面真相,每个模块最新的诚实结论都在
  这里,任何文档与它冲突以它为准。
- **`docs/README.md`**——240+ 份文档的导航索引,回答"这么多文档该信哪一份"。
- **`docs/audit/` 系列**——多维度独立审计报告(`RISK_REGISTER.md` 是活文档,其余是时间点快照)。
- **`docs/superpowers/specs/` + `docs/superpowers/plans/`**——这个项目"先写设计、再写实施计划、
  再 TDD 实现"的开发范式本身,想看某个具体能力(比如 IC 面板、干跑流水线、组合分散验证)当初为
  什么这么设计,按日期文件名去找对应的 spec/plan。
- **`docs/runbooks/live-trading-runbook.md`**——如果你的问题是"研究通过之后,通往实盘的路径是
  什么",这不是本文档的范围,去看这份 runbook。
