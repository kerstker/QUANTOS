# 盈利验证研究操作

本流程不部署、不下单、不改变经济准入。先读[统一路线](../engineering/plans/2026-09-09-profitability-validation.md)。

## 独立策略持仓组合

主研究完成后，在 Studio「压力与组合」运行独立策略组合；「完整过程」中的独立策略
三折比较依赖原三折冻结材料。原计划全部研究组均参与，显示每折实际尝试、失败、
预算、成本后增量和回撤。手续费/滑点可以在三项已登记情景间切换，不能在线改搜索范围。
每折为独立账户窗口，图上的三折增量不是连续账户收益。

「前向账户」有独立策略自己的启用、推进与停止入口。启用冻结模型及逐组件预测合同，
在下一自然窗口比较现金、组合、单策略基线。尚无预测或标签显示等待；停止不会补写旧预测。
「生成 / 更新研究结论」分别引用原预测组合、新持仓组合和三折证据；旧结论版本保留。

研究 Runner 对应 `sleeves`、`sleeve_compare`、`sleeve_observe`、`sleeve_refresh`、
`sleeve_stop`，均只接受已有任务编号。新流水线自动编排；旧已完成流水线保持原记录，
可以通过上述入口补做增量。若模型源码、环境或冻结分区变化，三折缓存复用会拒绝；
不得删除原比较或失败记录以重新搜索。详见[实现合同](../engineering/specs/2026-09-10-independent-strategy-sleeves.md)。

## P5 被动账户输入

在经过核实的开发 Python 3.12 环境中，从仓库根目录执行一次有限公开采集，例如：

```bash
python -m tools.passive_account_capture --symbol BTCUSDC --duration 15 \
  --initial-collateral 1000 --fee-rate 0.0002 --max-order-notional 100 \
  --output /home/kerstker/quantos-dev-output/my-passive-capture-001
```

输出目录必须不存在。示例费率和抵押是显式模拟假设，不是账户真实费率；按实际研究规格
预先冻结参数。先取得规则、已完成三十日容量及资金费日程，再开启深度/聚合成交/标记价
原始窗口；前后规则、接收时间和完整原文保留。失败和断流不自动重写为成功。

`input.json` 初始 `actions: []`，只证明输入可读，不创建订单或实验。固定动作需来自
预注册研究或实际轨迹，保留发送时间；复制到一个新的输入文件后，在工作台选择
「P5 原始盘口与队列」登记该文件，或使用 `python -m tools.passive_execution_study`
的 `--input` / `--output` 离线入口。不要回写原采集文件。

可选账户结构为 `account: {environment, initial_collateral, fee_rate, max_order_notional}`；
金额为字符串，订单可加布尔 `reduce_only`。普通成交量必须有 `nq`，旧文件缺失时不能
把 `q` 总量当作普通队列量。公开环境与动作共同受 4 MB、200 动作预算限制。
三个延迟情景全部保留，成本表和权益/回撤来自同一账本，未提供账户的输入仍显示未知。
跨资金费结算、跨日或缺少因果规则证据时需新规格和对应证据，不能静默补成本。

详见[时间合同、模拟假设与实测](../engineering/specs/2026-09-10-passive-canonical-account.md)。

## 工作台

新代码验收并另行部署后，在 AI 研究工厂选择已完成的冻结实验，打开「成本决策」→「运行盈利验证」。
该动作需要既有研究令牌与操作人，不新增审批层。系统依次运行 P0、P1、P2，执行一次 P3 公开采集；
对应输入存在时运行 P3 独立研究与 P4/P5；执行轨迹可独立于 P0 运行。自然前向继续使用既有观察流程，已登记的 P6 成本账户随观察推进。
本轮没有切换现有常驻服务，所以现有线上页面不会自动获得本次功能。

## P3/P4 自然时间采集

盈利研究完成 P0 后会自动登记独立信息窗口。窗口从下一整点开始，先预热一次 OI，
再积累固定 504 小时；两种计价独立处理，缺口不回填。小时工作者会推进所有已登记窗口（最多八个），
标签成熟后只运行一次有限研究，不自动重搜失败窗口。
前端“执行与数据”显示采集和实验状态；没有新版本工作者时不能仅凭登记认定持续运行。
该页的“登记自然信息窗口 / 更新信息窗口”使用独立的 `profitability_information` 研究任务，
只要求既有 P0 已对账，不重跑 P1/P2，不改变旧 P6 的冻结源码或计划；沿用同一认证和操作记录。

隔离研究可使用有限 CLI（Python 指验证过的开发解释器；状态根须有该编号已对账的 P0）：

```bash
python -m tools.profitability_causal_workflow --state-root /absolute/isolated/research-state --attempt 4 --register
```

`--once` 只执行一次；重启时省略 `--register` 读取同一计划。达到自然成熟时点后进程退出。
原始公共响应、失败、固定面板、标签、成本账户和每次尝试均留在
`data/cache/profitability/<attempt>/causal/`。源码或环境变化后拒绝继续原计划，应保留原窗口，
不得修改摘要绕过检查。此 CLI 不安装服务、发布程序或接触交易账户。
方法与界限见[实现记录](../engineering/specs/2026-09-09-causal-information-workflow.md)。

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

「执行与数据」支持 basis、information、trajectory、calibration、passive 五种 JSON。使用既有操作名和研究令牌，内容上限 4 MB，每类最多三个版本；相同内容重复请求不占新版本。旧失败不会被覆盖，队列固定到提交时版本。basis/information 等待 P0，trajectory/calibration/passive 可独立执行。只允许研究资料，不上传凭证。
calibration 根字段为 observed、capture、actions、calibration_cutoff_ms。capture 沿用公开 V2 回放格式（BTCUSDT、3–60 个真实接收的快照），actions 为有序的 ORDER/CANCEL 客户端动作；金额使用十进制字符串。三组延迟固定，不提供无限参数编辑器。规范化轨迹导入不构成来源认证。

「前向账户」→「登记 / 更新前向账户」使用下一 UTC 自然日 00:05 至第十四天的固定窗口。先确保对应冻结模型已有自然时间预测观察；登记账户不会启动或补造预测。首个完整日度前缀需再等待最后预测的四小时标签成熟，最早在起点之后 27 小时具备技术回放条件。断档、失败与未成熟均可见，后续修改源码需另立研究，不让旧窗口静默换引擎。

隔离研究命令（不会安装常驻服务；来源可为已有预测观察的只读状态根）：

```bash
python -m tools.profitability_forward_account --state-root <隔离研究输出根> --source-root <已有预测状态根> --attempt 7 --register --once
python -m tools.profitability_forward_account --state-root <同一隔离研究输出根> --source-root <同一预测状态根> --attempt 7 --interval 60
```

第二条持续至预登记窗口成熟或出现不可恢复缺口/预算耗尽；中断后重复同一命令恢复。每次更新输出简短状态，完整证据在 profitability/<attempt>/p6。真实场所费用仍未知；这是冻结参考目标的模拟账户，不是实际下单盈亏。

含盈利证据的新检查点恢复返回 layout=CACHE_ROOT_V2，隔离目标下为 ai_factory/、profitability/、profitability_inputs/。旧包返回 FACTORY_ROOT_V1，目标下直接为 factory.db 等。调用者必须按返回布局解释，不能将两者混用。生产恢复与异机迁移不包含在本开发操作内。

资金费覆盖检查允许最多一秒的场所结算时钟偏移；仅用于辨别相邻事件覆盖，原始事件毫秒时间完整进入账本。不填补缺失资金费，明显超过八小时的缺口仍拒绝。当前规则仍属技术约束，不构成独立场所规则认证。

## 组合与 QWEN 研究协作

以下入口属于 2026-09-10 增量，须使用包含本增量的 Console、Runner 与前端。
本次仅开发与隔离验收，现有常驻服务未切换。

1. 在「AI 研究工厂」创建实验时选择 AI 编排、固定来源与候选预算，并选择「AI 研究协议」。
   默认单次提案；三角色为同一 QWEN 的提案、反证、综合，最终只提交一个受限候选。
   每次提案总限时 60 秒，单次调用最多 30 秒、400 输出 Token；超时和中断消耗预算。
   主研究预算为 B 时，配置 AI 的完整三折四组对照另有 12B 次候选尝试；
   无 AI 时三折两组另有 6B 次。完整对照始终计算两种 AI 协议，
   选择单次主研究不表示整个流水线只有一次模型调用。组合权重与固定压力案例另行登记。
2. 主研究完成后，流水线自动执行有限组合研究。已有冻结实验可在「组合内层选择与整体回放」
   启动该阶段。最多三个组件、16 个预先固定的权重方案；权重仅由内层决定。
   查看同一账户的组合与单模型净值、费用/滑点对照、回撤、成交数和全部权重尝试。
3. 「研究结论与来源」显示原始结果字段和引用，以及内层搜索、外层只读解释的过程记录。
   「生成 / 更新研究结论」使用已有研究队列，保存不可变版本；没有真实费用就保持未知。
   过程对照中可查看各折、固定/随机/单次/三角色的结果及实际调用开销。
4. 流水线可登记「冻结组合 · 配对前向观察」，也可在该面板启用、推进或停止观察。
   组合与内层单模型基线使用相同信息时点；成本账户同时比较现金、组合、单模型和三组成本。
   登记不会安装常驻服务；没有对应工作者持续推进时不能宣称持续运行。
   标签未成熟、数据缺口和失败均保留，停止观察不删除旧预测。
5. 任务中断后通过原任务恢复，先核对源码、计划、输入和已有回执。已完成调用和试验复用，
   未完成预留仍消耗预算，不重置时限或覆盖失败。若身份不匹配，先保留原始记录并处理原因。

本轮实际三折四组结果见[QWEN 对照报告](../reports/2026-09-10-qwen-research-protocol-comparison.md)。
当前三角色方向未证明稳定成本后增量，已停止扩搜；零交易的现金结果不作为主动盈利证据。
下一轮只有在新的信息与预注册规格准备好后开展，不根据已看过的外层结果扩大旧搜索。

## 原始逐笔采集与队列诊断

使用 DGX 手册中的隔离 Python。以下是一次性公开采集，不安装或切换常驻服务：

```bash
python -m tools.passive_market_capture --symbol BTCUSDC --duration-seconds 30 --output <全新采集目录>
python -m tools.passive_market_capture --symbol BTCUSDT --duration-seconds 30 --output <另一全新采集目录>
python -m tools.passive_execution_study --input <固定队列输入.json> --output <全新研究目录>
```

采集为 0.01–120 秒、最多 20,000 消息/4 MB 原始文本；超出预算、断号或连接失败会留存失败。
目录不可重复。中断日志在 `raw-events.jsonl`，正常原始材料为 `raw-tape.json`，输入质量为 `result.json`。
不要用五秒聚合快照替代原始材料。

队列输入是 `{"tape": <raw-tape.json 的内容>, "actions": [...]}`。
空动作只校验输入。非空动作最多 200 条，按客户端发送时间排序：
ORDER 包含 timestamp、kind、order_id、side（BUY/SELL）、quantity、price，可选 expires_ms；
CANCEL 只含 timestamp、kind、order_id。时间使用 Unix 毫秒，价格和数量使用正十进制字符串。
所有动作必须处于共同可用窗口，不把离线准备的动作声称为发生结果之前的预测。

在包含本增量的 Studio「执行与数据」选择「P5 原始盘口与队列」登记同一输入即可进入既有队列。
可查看 0/25/100 ms 三组诊断、盘口、接收延迟、订单剩余量和时间轨迹。
可选 `account` 已接通唯一模拟成本账本，见[统一账户合同](../engineering/specs/2026-09-10-passive-canonical-account.md)。
当前仍没有真实费用、个别实际队列位置或真实回执校准结论。

## 双时钟被动回执导入

在同一被动输入上添加 `calibration`，必须同时包含 `account` 和至少一条固定 ORDER。
使用相同的研究输入入口和离线命令，无须新增模型、代理或交易凭证。

```json
{
  "schema": "PASSIVE_RECEIPT_CALIBRATION_V1",
  "cutoff_ms": 1789000000100,
  "observed": {
    "action_hash": "与完整固定 actions 一致的 SHA256",
    "symbol": "BTCUSDC",
    "coverage": {"start_ms": 1789000000000, "end_ms": 1789000000200, "gaps": []},
    "provenance": {"kind": "IMPORTED_PRIVATE_RECEIPTS", "declared_source_sha256": "原始来源声明 SHA256"},
    "orders": [{
      "order_id": "与 ORDER 相同的编号", "side": "BUY", "quantity": "0.001", "limit_price": "100000",
      "events": [{"receipt_id": "唯一回执编号", "venue_ms": 1789000000020,
        "received_ms": 1789000000030, "state": "ACK", "cumulative_filled": "0"}]
    }]
  }
}
```

此片段是 `calibration` 结构说明，摘要占位文字不能直接运行。真实摘要为 64 位小写十六进制；
动作摘要使用 `services.research.ai_factory_store.digest(actions)`。订单价格、数量、方向
必须与动作完全对应，时间落在同一原始输入的共同可用窗口。数量是累计成交量，
不要把逐笔新增量直接填入；不得根据场所时间推算不存在的实际接收时间。

测试材料必须声明 `SYNTHETIC_TEST_FIXTURE`。来源摘要声明不会自动认证原始回执。
对未观测区间列出有序、不重叠的 `gaps`，不要用空白区间表示零成交。
ACK、PARTIALLY_FILLED、FILLED、REJECTED、CANCELLED、EXPIRED、CANCEL_REJECTED
均支持；乱序、重复或缺时钟的原始材料应先按可验证依据规范化，无法确定时保持未知。

系统先运行三组前缀，冻结选择后再运行三组完整窗口，共六次尝试。
查看 `calibration/selection.json`、`fit-*.comparison.json` 和 `heldout-*.comparison.json`；
界面摘要只显示前 20 个订单与最近路径点，完整文件保留全部记录。
后段继续原账户与订单，不能称为新的自然 OOS。零误差、选出参数或界面绿色均不意味着
真实场所校准验收。详见[方法、限制与验证](../engineering/specs/2026-09-10-passive-receipt-calibration.md)。

## 公开订单边界与当前冻结结果

含新订单边界增量的 Studio 会在同一 P5 工作区显示公开报文中的挂单价格乘数、
未完成订单上限和市价保护幅度。参数来自已登记输入的 `exchangeInfo` 原始回执，
不手工用网页当前值覆盖旧冻结材料。整组缺失时显示未知，部分缺失时拒绝编译。
限价边界在订单到达时检查，部分成交仍占用订单名额，撤单到达后释放。
这些规则只覆盖声明的模拟订单；真实外部挂单和场所行为仍待回执核验。
见[具体支持范围](../engineering/specs/2026-09-10-public-order-constraints.md)。

独立策略修正源码 `e9ca09b51` 的 286 次冻结比较、逐笔复核、1,330 文件恢复和真实前端
检查已完成，但没有稳定净增量；[实际报告](../reports/2026-09-10-independent-sleeves-comparison.md)
保留全部结果及构建后公开镜像仓库 401 的失败。合并代码继续完整验收，当前说明不是服务发布记录。

## 本批已验收版本与开发检查

运行源码 `35062eb67703e3469f069bf936e6081fcf38997f`、证据提交 `d1055d75f8233f0b7d150f582fa8f818541f887e`，完整 **28/28 PASS**。
后端 7,731 通过/19 跳过，执行栈 3,442 通过/2 跳过，前端 350 通过；类型、ESLint 与构建通过。
两次 ARM64 冷构建镜像相同，离线扫描、断网只读启动及干净检出的源码/材料绑定通过。
文档收尾提交与已验收运行源码分别记录；本批没有部署或切换常驻服务。

上节“合并代码继续完整验收”是前一检查点。旧已用历史、原始模型与回执保留原源码，
恢复不重复模型调用或已完成试验。新功能须在另行授权切换服务后才出现在原入口。

增加或修改 Runner 任务种类时，相关专项验证包含现有精确清单与诊断用途合同：

```bash
python -m pytest -q tests/governance/test_research_runner_boundary.py
```

这里的 `python` 指本机核实的 Python 3.12 开发解释器；这是快速开发检查，
不替代完整 `scripts/validate.py`，也不新增普通研究审批或交易权限。
