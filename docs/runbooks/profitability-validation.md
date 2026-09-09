# 盈利验证研究操作

本流程不部署、不下单、不改变经济准入。先读[统一路线](../engineering/plans/2026-09-09-profitability-validation.md)。

## 工作台

新代码验收并另行部署后，在 AI 研究工厂选择已完成的冻结实验，打开「成本决策」→「运行盈利验证」。
该动作需要既有研究令牌与操作人，不新增审批层。系统依次运行 P0、P1、P2，执行一次 P3 公开采集；
对应输入存在时运行 P3 独立研究与 P4/P5；执行轨迹可独立于 P0 运行。自然前向继续使用既有观察流程，已登记的 P6 成本账户随观察推进。
本轮没有切换现有常驻服务，所以现有线上页面不会自动获得本次功能。

同一冻结实验保留一份独立研究记录。失败或中断目录不被重跑覆盖；完整回执可重复读取。
需要复核中断时，使用新的隔离输出目录并说明继承的注册身份，不重新声称外层未见，也不扩大搜索空间。

## 离线命令

使用 DGX 已验证 Python 3.12；下方 `python` 代表该解释器。输入为只读冻结实验/行情目录，输出必须为独立的新目录。

```bash
python -m tools.profitability_study --experiment <冻结实验目录> --input <已验证行情目录> --output <新P0目录>
python -m tools.profitability_position_study --p0 <P0目录> --input <已验证行情目录> --output <新P1目录>
python -m tools.profitability_timing_study --p0 <P0目录> --p1 <P1目录> --input <已验证行情目录> --output <新P2目录>
python -m tools.profitability_market_capture --output <新P3目录>
python -m tools.profitability_information_study --p0 <P0目录> --information <联合面板JSON> --input <已验证行情目录> --output <新P4目录>
python -m tools.profitability_trajectory_import --input <轨迹JSON> --output <新P5目录>
```

P3 使用现有网络环境；每个公开请求有 45 秒总限时，最多三个并发。失败原样保存。
不修改全局代理、模型服务或系统定时器。2026-09-09 实测直连失败、已有代理成功，不能假定未来网络始终可用。

## 联合信息与轨迹格式

Runner 在当前状态根的 `data/cache/profitability_inputs/<冻结实验编号>/` 查找可选 `information.json` 和 `trajectory.json`。
这里不放任何交易密钥。完整研究输出位于 `data/cache/profitability/<编号>/p0` 至 `p5`，Console 仅读取摘要。

`information.json` 根对象仅含 `quote` 与 `observations`。目前成本后评估使用 BTCUSDC 唯一账本，
所以此驱动只接受 `quote: "USDC"`；USDT 可做同步输入验证，不能冒用 USDC 的账户回放。
每个已冻结价格面板时点必须有一行，不补零、不前填：

| 字段 | 口径 |
|---|---|
| slot_ms / available_ms | 毫秒；实际可用时点不得晚于信息时点 |
| flow | 主动买卖差 / 总成交量，无量纲，范围 [-1, 1] |
| funding / funding_event_ms | 已结算有符号费率及结算时点，不能用未来预估费率替代 |
| funding_interval_ms | 1、4 或 8 小时；特征统一转换为四小时口径 |
| oi_change | 同合约 OI 的相对变化，不赋予看多方向 |
| oi_event_ms / oi_previous_ms | OI 两端时点，前者严格较晚，均必须已经可用 |
| source_hash | 原始来源回执 SHA256；声明摘要本身不构成独立来源认证 |

`trajectory.json` 根对象仅含 `observed` 与 `simulated`。两者具有相同 `action_hash`、`symbol` 和固定订单意图。
各自含 `orders` 数组，每个订单含 `id`、正数十进制字符串 `quantity`、`side`（BUY/SELL）、`events`。
事件含毫秒 `at_ms`、`state`、累计成交十进制字符串 `filled`；允许状态为 SENT、ACK、PARTIAL、CANCEL_SENT、CANCELLED、FILLED、REJECTED。
数量不得倒退或超过原订单；输入应先规范化为订单生命周期，终态后的独立撤单请求响应不能伪装为新成交。
诊断比较固定动作下的数量、时间与状态，不以回测利润选择校准参数。导入结果始终保留来源未独立验证的限制。

## 读结果

- P0：参考毛收益是相同实际模拟成交数量下的解释，不是无成本重算；真实场所费用为未知。
- P1：选择只用内层。停止原因包括无内层增量、外层无成交、成本情景不稳定；停止不是删除记录。
- P2：毛收益差包含错失行情和库存变化；单独逐笔价格改善没有证据时仍为未知。
- P3/P4：输入通过不代表有信息增量，组内关联也不等同因果识别；需要成本后对照和自然前向。
- P5/P6：导入轨迹不自证真实场所，预测观察不等于账户净收益。

### P3 独立成本后对照

可选输入 `data/cache/profitability_inputs/<attempt>/basis.json`，独立结果 `profitability/<attempt>/p3_study`。
JSON 根为 `quote`（当前账本驱动仅 USDC）和 `observations`。
每条观察包含 `slot_ms`、64 位 `source_hash`、`previous`、`current`；后两者各包含 spot/perpetual/index 三行：
`market, base, quote, event_ms, available_ms, price`。同组事件时间完全一致，两组均不超过决策前五分钟、可用时间不晚于决策，previous 必须早于 current。
USDT 数据可以单独检查，但不能借用 USDC 账本给出成本后结果。
调用现有信息研究入口时加 `--stage P3`；默认 P4 保持兼容。受限工作流发现 basis.json 后自动运行独立对照，未提供时不会生成占位实验。

### 固定动作回放（Python 研究接口）

`services.research.profitability_fixed_replay.run_fixed_actions` 接收冻结的 `(timestamp, OrderIntent|CancelOrder)` 序列、`cancel_latency_ms`（0–60000）以及原 `run_linear_perpetual_simulation` 的场所规则、资金费、成本、风险、容量、帧和来源参数。
不接受可变策略；策略摘要包含动作身份和延迟。输出中的 `trajectory` 可与外部规范化回执组成 `observed/simulated` 输入，交由现有轨迹导入器比较。
这是离线研究接口，尚未增加前端仿真参数编辑器；标准成本和风险要求全部沿用。撤单拒绝保存在 `cancel_responses`，不伪装为订单终结后的新成交。


## 新版输入与账户操作

「执行与数据」支持 basis、information、trajectory、calibration 四种 JSON。使用既有操作名和研究令牌，内容上限 4 MB，每类最多三个版本；相同内容重复请求不占新版本。旧失败不会被覆盖，队列固定到提交时版本。basis/information 等待 P0，trajectory/calibration 可独立执行。只允许研究资料，不上传凭证。
calibration 根字段为 observed、capture、actions、calibration_cutoff_ms。capture 沿用公开 V2 回放格式（BTCUSDT、3–60 个真实接收的快照），actions 为有序的 ORDER/CANCEL 客户端动作；金额使用十进制字符串。三组延迟固定，不提供无限参数编辑器。规范化轨迹导入不构成来源认证。

「前向账户」→「登记 / 更新前向账户」使用下一 UTC 自然日 00:05 至第十四天的固定窗口。先确保对应冻结模型已有自然时间预测观察；登记账户不会启动或补造预测。首个完整日度前缀需再等待最后预测的四小时标签成熟，最早在起点之后 27 小时具备技术回放条件。断档、失败与未成熟均可见，后续修改源码需另立研究，不让旧窗口静默换引擎。

隔离研究命令（不会安装常驻服务；来源可为已有预测观察的只读状态根）：

```bash
python -m tools.profitability_forward_account --state-root <隔离研究输出根> --source-root <已有预测状态根> --attempt 7 --register --once
python -m tools.profitability_forward_account --state-root <同一隔离研究输出根> --source-root <同一预测状态根> --attempt 7 --interval 60
```

第二条持续至预登记窗口成熟或出现不可恢复缺口/预算耗尽；中断后重复同一命令恢复。每次更新输出简短状态，完整证据在 profitability/<attempt>/p6。真实场所费用仍未知；这是冻结参考目标的模拟账户，不是实际下单盈亏。

含盈利证据的新检查点恢复返回 layout=CACHE_ROOT_V2，隔离目标下为 ai_factory/、profitability/、profitability_inputs/。旧包返回 FACTORY_ROOT_V1，目标下直接为 factory.db 等。调用者必须按返回布局解释，不能将两者混用。生产恢复与异机迁移不包含在本开发操作内。
