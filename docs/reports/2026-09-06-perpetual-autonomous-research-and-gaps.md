# BTCUSDC 自动研究结果与系统缺口复核

日期：2026-09-06。状态：TECHNICAL_VALIDATION_ONLY；无 Paper/实盘准入。

## 本次真实执行结果

通过 QUANTOS `JobQueue → perpetual_alpha → TrialLedger → canonical Decimal ledger → RunRegistry → technical-replay` 完成计算。原 A/B/C 未过筛后，系统自动生成固定语法的八个新假设。78 项情景包括11个候选和2个基准，各有3组滑点、带/不带150累计停机；没有挑选成本最乐观的一组作为成绩。

评估：2025-01-01 00:05 至2026-08-01 00:05 UTC；初始1500 USDC，25%目标绝对名义敞口，单边taker4bp。下表为单边5bp滑点且带累计停机的中心情景。

| 候选 / 基准 | 净收益 USDC | 2025 | 2026截至8月1日 | 分钟回撤 | 成交次数 | HAC alpha t |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| cash | 0.00 | 0.00 | 0.00 | 0.00% | 0 | — |
| passive25 | -112.64 | -20.36 | -92.27 | 14.98% | 28 | — |
| A | -83.47 | -52.52 | -30.95 | 7.62% | 78 | -0.65 |
| B | -81.93 | -54.13 | -27.80 | 8.96% | 17 | -1.17 |
| C | -56.94 | 0.00 | -56.94 | 4.50% | 8 | -1.73 |
| h_fade_close_return_3 | -150.84 | -61.23 | -89.62 | 12.06% | 135 | -1.10 |
| h_fade_close_return_14 | -150.05 | -64.17 | -85.88 | 11.96% | 83 | -1.02 |
| h_trend_close_return_3 | -150.42 | -70.74 | -79.68 | 13.00% | 192 | -1.02 |
| h_fade_settled_funding_3 | 33.23 | -32.99 | 66.22 | 12.19% | 87 | -0.31 |
| h_trend_settled_funding_14 | -150.83 | -83.03 | -67.79 | 12.87% | 21 | -0.95 |
| h_fade_settled_funding_14 | 145.73 | 78.86 | 66.86 | 9.25% | 41 | 0.62 |
| h_trend_settled_funding_3 | -118.00 | -24.20 | -93.80 | 15.69% | 65 | -0.33 |
| h_trend_close_return_14 | 84.88 | -6.79 | 91.67 | 11.18% | 116 | 0.39 |

11 个候选均未通过预注册进一步研究筛选。较强的14日资金费反向，在2/5/10bp滑点下分别净赚146.96/145.73/142.42 USDC，两个年份均为正；但HAC alpha t仅0.62，剔除最佳5个交易日后中心情景净收益为-3.73。因此保留为未确认研究假设，不能说已发现可实盘Alpha。3日资金费反向与14日价格趋势虽全期盈利，仍有年度、回撤或集中度问题。

Ridge只有25次有效周训练，首次可激活在2026年2月；2025没有足够730个自身合约成熟样本。没有借用现货/USDT训练数据来制造更长记录。

## 数据与独立复核

交易价/mark全档案对齐1,354,287分钟。整个评估窗完整；风险与回撤只观察分钟开盘及资金费事件，不证明分内止损成交。150是触发阈值，退出损失可以越过150。暖机部分有4个不完整交易日，未填值。月档案缺少2026-06-29整日mark，使用币安独立日档案补足1440行，官方SHA256：`04c72c0b6bc27cae56bd26fa801ef0043ba7d9c5670043b794156ba9fec375c2`。未插值，未跨市场补值。归档日期可追溯，但不证明当年逐条发布可用时间。

共保留两个预检失败（合约CSV表头、mark覆盖缺口）、修复前78次与反手风控修复后78次；156次真实行情情景均在任务TrialLedger保留。每轮独立有理数核算78条路径、45,084个日权益/持仓检查，并逐笔复核4bp费用与实际资金费，全部通过。两轮经济输出逐字段相同；新回归测试确认反手后绝对数量下降仍须检查新方向风险。

最终计算代码：`ccc3dba41e37edd65aa950e1c0a82872a8fa7a7e`。
最终运行：`run_998b025d60feb3ddda98c9bfbdb41760`。
结果哈希：`0e73c30e19bf2b05a04888c6894334d63cf6cef4c805a5a690cb56f795383382`。

所有输入、逐项输出、trial DB、queue DB与验证日志位于：
`/home/kerstker/quantos-dev-output/autonomous-perpetual-20260906/`。

复现命令（在项目根目录，使用锁定解释器；下一次attempt会记录为新的试验）：
```bash
PYTHONPATH=. OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python /home/kerstker/quantos-dev-output/autonomous-perpetual-20260906/run_queue.py 5
PYTHONPATH=. python /home/kerstker/quantos-dev-output/autonomous-perpetual-20260906/independent_reconcile.py 4
```

运行4为已完成的不可变记录。该启动脚本只绑定独立开发目录，不是生产部署或任意HTTP路径参数。归档抓取可使用现有 `tools.binance_spot_archive_capture.capture(..., market="usdm", kind="klines"/"markPriceKlines")`；正式RunKind默认读取工作目录下 `data/cache/perpetual_alpha/input` 中按同样规范冻结的输入，不在计算过程中暗中下载或补价。

## 当前系统距离最小实盘闭环的缺口

本轮完成的是 BTCUSDC 永续的有限自动研究与技术验证，不代表全自动 Alpha 平台全部验收，更不代表发现可实盘盈利策略。

| 环节 | 已验证的实际状态 | 剩余工作及验收边界 |
| --- | --- | --- |
| 数据清洗 | 官方 trade/mark 分钟归档经 SHA/CRC 校验；修复合约 CSV 表头、毫秒与现货微秒区分；缺失 mark 日由官方独立日归档补齐 | 当前规则历史情景；历史场所规则、发布可用时间、真实盘口/队列、保证金档位仍不齐全。评估窗要求分钟完整，暖机缺失日不填值 |
| 信号生成 | A/B/C 以及 8 个固定语法新假设，经下一日 00:05 执行代理、实际资金费进入唯一 Decimal 永续账本 | 新因子必须有真实历史可用时间；更多数据源和因子不能凭最近收益追加搜索 |
| AI 假设 | 复用 HypothesisSpace/UCBSearch 自动提议、反馈和保存搜索状态；不是本次真实 LLM 推理 | 项目已有 Hypothesis/SqliteHypothesisStore 和受控模型 runtime；本路径尚未贯通 LLM 提议→规范假设存储→同一证据准入。外部 provider 的 connectivity probe 不能当作假设生成验收 |
| 回测与试验记录 | 原候选失败后自动启动新候选；全部成本/风险情景先登记，再计算；结果及源代码、数据、规格身份绑定；无任意代码/路径请求参数 | 技术自洽与独立数值复核不等于独立经济权威；正式 DSR/PBO、污染历史的全试验总体绑定、真正冻结前向尚未完成 |
| Paper | 保留现有隔离边界；没有绕过晋级或写入新 paper 成交 | `is_authoritative_backtest_record` 仍因外部签名 verifier 未接入而拒绝所有记录；需完成受审查的独立权威接入与 V2 前向执行衔接。不能把同一历史再次回放改名 Paper |
| USDC 执行/恢复 | 离线账本已支持 USDC，既有恢复/对账专项通过 | `live/exchange/binance_futures.py` 仍多处拼接 USDT、过滤非 USDT；须在同一适配器迁移显式合约身份并完成部分成交、超时、重连、重启对账测试。当前研究支持不能冒充真实适配完成 |
| 风控 | 本次离线模型计入浮盈亏/费用/资金费的150累计预算、次分钟退出、永久停止新风险；新增反手仓位风险回归 | 实际执行需要持久、跨重启、跨日的同一150预算，接 canonical kill-switch；已有日损益和回撤乘数不等于该预算。正常权限、校验、回滚、告警不能删 |
| 真实净值 | 技术账本由独立 Fraction 方法按成交、资金费和 mark 逐日复核 | `tools/investor_snapshot.py` 明示实际费用/资金费 UNKNOWN，venue 外部现金流默认 None；必须从真实成交和资金流水补齐，入金不能算盈利 |
| 实际验证与发布 | L2 隔离及人工门保留；未部署或调用私有交易接口 | `require_live_submission_authority` 仍无独立签名权威；正常提交连 Testnet 公共入口也会被阻断。需真实发布/扫描/烟测证据、运行告警及冻结前向；异机备份按用户要求延期 |

开发顺序应收敛到：先把同一技术账本接入真实前向数据和规范的假设/试验记录，再补独立权威、USDC 执行恢复、实际费用与资金流水、持久150预算及发布验证。策略是否通过仍由证据决定，不能保证开发完成就一定产生 Alpha。

## 本批安全边界与维护性

本批新增研究功能，代码提交 ccc3dba41 为 +680 / -17 行（含规格、测试和展示）。复用现有归档校验、信号/Ridge数学、假设搜索、TrialLedger、JobQueue/RunRegistry、技术结果路由以及唯一 Decimal 永续账本；没有复制资金/持仓/PnL账本，没有新建平行页面。原 USDT、现货和旧试验按用户要求保留各自市场语义。没有改现有实盘权限、风险审批或旧结果隔离，没有 `.bak` 文件、新 CSS 覆盖层或隐藏旧入口。新研究行为和反手风控修复属于明确功能开发，不能描述为全项目行为完全不变的减法重构。

本次队列数据库、试验数据库、行情和日志仅写入 `/home/kerstker/quantos-dev-output/autonomous-perpetual-20260906/`（canonical 验收另写工作树自身的证据与开发缓存）；没有变更 `/srv/quantos/state`、驻留服务、凭证或真实交易。

另补资金费冻结文件必须覆盖固定评估窗与14日信号暖机；真实输入预检再次通过。新增2个覆盖边界回归、原队列/账本和导入图检查合计36通过；动态导入白名单未增加或放宽，导入图20域、49边、零循环。相关修复提交：`d652eb4f7`。合入主工作树后同样36通过；供应链3项失败再次复现，日志 `remaining-release-failures.log`。

## 验证命令与结果

解释器固定为 `/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905/venv/bin/python`，Node 为 `/home/kerstker/.nvm/versions/node/v24.16.0/bin/node`。以下 `python` 指该锁定解释器。

- 修改前：后端执行/风控/既有研究专项516通过；前端lint、typecheck、build通过，262测试通过。日志 `baseline*.log`。
- `python -m pytest tests/research/test_perpetual_alpha.py tests/research/test_perpetual_alpha_runner.py tests/tools/test_public_spot_alpha_research.py tests/tools/test_binance_spot_archive_capture.py tests/governance/test_research_runner_boundary.py tests/research/test_research_runner_auth.py tests/research/test_console_api_contract.py tests/governance/test_crypto_data_boundary.py -q`：89通过、1跳过；`final-targeted.log`。
- 权威、对账、回撤、投资者快照、控制台及假设存储专项：217通过、1跳过；`system-audit-tests.log`。此为离线测试，不是实际下单证据。
- `python scripts/validate.py` 在干净代码提交 `ccc3dba41` 完整执行：**FAILED**。后端7152通过、19跳过、4失败；执行栈3388通过、2跳过；前端typecheck/eslint/build通过、263测试通过；Phase13自身26通过。日志 `full-validation-clean.log`，机器记录 `evidence/validation_report.json`。最初在未提交工作树调用曾被干净源码约束拒绝，日志也保留。
- 全量4个后端失败：导入图精确行号回归1项（本轮已修复）；供应链证据3项（容器证据未绑定当前源码等）。全量阶段另外显示ARM64容器扫描、可复现构建、控制台冒烟、供应链和top-tier证据未通过。不能把旧镜像报告改写为当前提交的验收证据。
- 尾修复：`python -m pytest tests/research/test_perpetual_alpha.py tests/research/test_perpetual_alpha_runner.py tests/governance/test_service_import_graph.py tests/governance/test_research_runner_boundary.py -q`，36通过；`preflight-fix-tests.log`。导入图生成和真实资金费/行情窗口预检通过。源码修复在独立工作树验证后快进合入，避免修改全量测试正在运行的源码。
- `npm run lint`、`npm run typecheck`、`npm run build`、`npm test -- --run`：本批最终前端均通过，263测试；`final-ui-*.log`。
- 两轮 `independent_reconcile.py`：各78路径、45084净值检查通过。`final-verification.json` 还确认真实registry技术投影13行及两轮经济结果一致。
- 代码提交前都显式执行 `sh scripts/git-hooks/pre-commit`，仓库整洁、治理扫描、密钥防呆均通过；未使用 `--no-verify`。

完整门禁记录保持失败状态。全量运行后只有已验证的导入定位/证据刷新和资金费窗口预检加固，未宣称最终HEAD全量绿灯。既有版本、费用模型、全部试验和门禁记录保留；没有部署、实盘授权或自动晋级。
