# DGX研究与Binance测试网：当前路线及Claude交付收口

> **后续增量：11项测试隔离失败已修复。** 本轮扩大联合104 passed（31.00秒）；导入契约4 kept、0 broken。仍未完成冻结源码的canonical全量验收。下方首轮11项失败记录保留为历史，不再表示这组专项测试当前失败。

## 本次决策

用户已授权14天无密钥公共观察、选择Binance测试网，并进一步授权复用系统现有测试网接入开展联调。测试网系统测试不等待14天，也不以策略先盈利为前提；前向窗口用于策略评价，与联调并行。授权不等于已有机器批准工件自动有效，不扩大为主网交易，不允许AI读取凭证、代签、复位停机或绕过执行门。

这条当前路线覆盖旧[研究优先决定](../specs/2026-09-20-dgx-research-first.md)中将RF4串行排在前向通过之后的工程测试顺序；候选经济晋级和原冻结实验条件不变。RF3仍使用原14窗口合同，不按结果提前结束或延长到盈利。

## Claude Code状态及所有权

本次通过桌面终端快照核查：交互Claude已输出P0–P2终态总结，显示done并回到输入提示符。PID3393768仍存活，`ps --ppid 3393768`未列出直接子进程。会话保留，不杀进程、不清上下文、不声称已经退出。

Claude声明剩余项为Docker哈希锁、ruff/mypy门禁、签名基线和canonical证据刷新、部分模块解耦；其中frontend子代理曾报Not logged in，不能标为该子代理正常交付。其前端产物由本轮独立测试核对。

RF1/RF2/RF3已有产物由Codex负责集成，不再交给原P0–P2会话重复实现。保留现有暂存/未暂存文件与其他任务改动；本次未提交、推送、部署或刷新签名工件。HEAD为`1aaa0c3c9b2980ba5f4b6648d4a5cc8a6b383593`，不能用该提交代表未提交源码。

## 独立验收与补修

解释器：`/home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python`，实际Python3.12.3。pytest均禁字节码及缓存。

- 首轮17个专项文件477 passed，14.34秒，1条Starlette/httpx弃用警告。覆盖严格合同/哈希、PIT面板与数据身份、多标的滚动、训练内选择、风险有限性、旧权威标志、时钟同步、审计异常、风险provenance及验收假绿。
- 前端`factory-lifecycle-fail-closed.test.ts`通过3项，2.75秒；使用已安装Node v24.16.0，不安装依赖。证明三项守卫行为，不代表全前端验收。
- Codex新增真实子进程回归`test_skipped_required_file_cannot_hide_behind_unrelated_pass`：关键文件skip、另一个文件pass时，旧实现错误返回PASS；修复前实测1 failed。原因是默认xunit2没有testcase.file，原逐文件skip检查读不到路径。
- 最小修复：`scripts/validate.py::_run_check`在自己生成JUnit报告时显式使用xunit1，保留文件身份，不改变原命令身份记录或交易门。回归保留在`tests/governance/test_validate_false_green.py`。
- 扩展联合运行包括上述回归、validate runner、hourly/MFT集成、research pipeline、pre-trade与demo batch：91 passed、11 failed，26.58秒。11项均来自`test_validate_runner.py`的完整验收模拟，在`resolve_canonical_source_anchor`处因`.importlinter`处于脏工作区而拒绝；没有绕过该边界、删除测试或伪称全绿。新增混合skip回归在这次运行中通过。

因此本轮是开发交付收口，不是完整验收通过。后续必须在明确源码所有权及冻结身份后重跑这些失败和canonical全量门；若冻结后仍失败，按实际缺陷处理。

## 按优先级执行的六条路线

| 顺序 | 路线与下一步 | 完成标准 | 依赖/负责人 |
|---|---|---|---|
| R0 当前收口 | 分离P0–P2、RF1–RF3及其他任务改动；确认提交范围，冻结源码；复跑11项失败与完整验收，刷新适用证据 | 同一源码的真实验收报告；失败不隐藏；保留签名工件边界 | Codex集成审阅；Claude限定补修；不直接提交整个脏工作区 |
| R1 测试网工程闭环 | 复用已部署Demo专用通道；核实执行/签发服务、策略意图到现有风险合同的接线；形成有界测试批次 | 人工执行侧完成下单/成交/撤单/持仓/费用/现金核对，未知订单阻断与重启不重发 | 与R2/R3并行；凭证和签发留在受保护执行侧；不使用硬拒绝的通用入口绕门 |
| R2 研究流水线 | 保留RF2失败候选及获利对照；来源去重、假设登记、固定预算、训练内选择、独立OOS、同口径成本压力和失败归档 | 单条可复现研究命令、全部案例与成本归因；盈利或否定均可审计，不保证盈利 | Codex负责实验设计；Claude实现边界明确的候选/测试 |
| R3 真实前向证据 | 核验旧观察的策略/参数/发布时间；有效证据复用；安排RF3登记、每日调度和完整原始回执留存 | 原合同14个窗口，缺失不补造；成熟后V2成本评价，单独报告测试网收益 | 已获观察授权但尚未启动；不作为R1工程联调前置条件 |
| R4 研究控制台 | 将已有六维CLI及RF2只读证据合同接入前端；展示任务、失败、成本、观察、测试网对账与未知项 | 一个入口看真实状态；后端/TypeScript/合同测试同步；延期项不算PASS | 消费R0–R3证据，不自行生成第二套评分 |
| R5 技术债与后续范围 | 依赖锁生成一致性、适用平台生产锁、ruff/mypy渐进门禁、剩余模块边界；再考虑Kraken/多资产 | 各任务独立复现与验收，不夹带大重构；生产范围前补齐相关部署要求 | 不将全机构TOP22、异机灾备、多场所和签名基线刷新变成每次研究的前置条件 |

R1应拆成：只读状态核查 → 离线预览/风险拒绝用例 → 受保护执行侧有界Demo批次 → 原编号恢复/费用现金核对。测试策略功能与评价策略盈利分别记录；真实订单范围/数量/有效期来自经核准的现有风险配置，不继承研究占位限额或账户默认杠杆作为授权。

## 现有接入证据与未决问题

本次前一轮通过本机`http://127.0.0.1:15840/api/v1/console/binance-demo`只读核查：环境BINANCE_USDM_DEMO；历史functional_cycle为MANUAL_ACCOUNT_RECONCILED、fill_count=2、final_quantity=0；新portfolio_cycle与portfolio_cash均NO_SNAPSHOT。总提交状态false，execution_reason为LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE，issuer_status为NO_SNAPSHOT。这是显示证据，不证明受保护签发侧物理缺失，也不能直接作为订单来源。

受保护私有采集服务active；某BTCUSDC起点systemd单元failed与随后已有新账户观察同时存在，不能据单一单元失败断言全部账户链路停止。须按时间、单元及发布来源分别核对。

通用`tools/testnet_execution_service.py::build_runner`调用`require_live_submission_authority`，当前实现硬拒绝。已有独立Demo会话见[专用执行手册](../../runbooks/demo-execution-session.md)，不可混用两条路径或重复发送历史订单。

## 保留的未完成项

- P1-9剩余live/research解耦及精确存量import基线；primitive下沉不代表全部架构修复。
- P2-11 Docker仍未完成哈希锁。Claude在Dockerfile中关于特定平台wheel可用性的解释本轮未独立核验，不能据此宣称方案绝对不可行；后续对实际版本/平台解析做可复现实验。
- P2-12 ruff/mypy配置不等于已入门禁；锁生成一致性待独立复现。全仓风格清理不混入当前安全修复。
- autonomy基线及canonical证据未刷新；不能改签名工件后沿用旧签名。
- 当前补修覆盖混合skip漏检，不宣称已穷尽过滤/收集规则的全部假绿边界。
- 本轮未发出测试网/主网订单，没有新增盈利结论或运行中的RF3调度。

## 11项失败的后续修复与复核

根因：`tests/governance/test_validate_runner.py`中的合成完整验收用例把真实开发工作区交给`resolve_canonical_source_anchor`，因此在未提交修改存在时先行拒绝，未能测试报告刷新、依赖顺序及时间复用等预期行为。这是测试环境耦合，不是允许脏源码生成正式证据的理由。

修复仅在该测试文件：新增`clean_validation_repo`临时Git仓库夹具，11项用例把测试模块的REPO指向真实、干净的临时仓库；仍调用原源码校验，没有mock其成功结果，没有改生产门禁。报告和执行标记在仓库之外，避免测试自身生成报告污染下一轮来源。

新增两项反例覆盖未跟踪源码和已暂存源码：均必须在子检查执行前抛出dirty path，且不得生成成功报告。临时测试仓库里的空提交只是夹具，不是对项目的提交、发布身份或签名证据。

验证顺序：原runner/假绿42项通过；补齐两项反例后，扩大到runner/假绿、hourly/MFT集成、research pipeline、pre-trade及demo batch联合104项全部通过（31.00秒）。实际无缓存运行import-linter，1133文件/4706依赖，4契约通过、0失败；该结论包含`.importlinter`已有精确例外，不代表历史架构债务清零。

后续R0仍须明确整合范围、冻结源码并运行canonical全量门。本次未重写签名工件、提交项目、启动新服务或发出订单。

## 收尾交接与暂停推进

用户最新要求：先收尾之前的工作，再由用户决定要做什么。因此本表是待选路线，不是继续自动执行的任务队列；不启动新委派、RF3调度、服务切换或交易。已有常驻服务不停止、不改动。

最后核对：Claude PID3393768仍在，未发现直接子进程，保留交互会话；`git diff --check`通过。工作区127条状态记录（71条已跟踪变更、56条未跟踪记录，未跟踪记录可能代表目录）包含多个任务，不将其全部算作本轮产物，也不自动暂存、提交或清理。

交付状态为已有开发补修与交接完成，不是项目全部完成：专项104项、导入契约4项已通过；完整canonical验收、提交范围确认、Docker锁、静态门禁、剩余解耦及签名证据仍保留为待决定项。此前失败记录不删除，后续修复结果另行记录。等待用户下一步指示。
