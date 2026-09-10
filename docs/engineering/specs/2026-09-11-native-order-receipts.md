# 币安原始订单回执：离线校准与费用诊断

2026-09-11。在账户条件修正源码 `c8165ec12` 的隔离检出中继续开发 P5。
该源码的完整验收独立运行；本增量没有继承其验收结果，没有部署或改变经济准入。

## 具体交付

将离线 `ORDER_TRADE_UPDATE` 原文、摘要及本机接收时间接入已有不可变输入、
三组前缀校准、冻结选择、后段比较和 Studio。研究仍按数量路径、时间及状态误差选择，
手续费在选择之后单独诊断，不进入校准目标，不向模拟账本重复扣费。

仅支持 BTCUSDC / BTCUSDT、单向普通 LIMIT / GTX 固定订单。固定动作、币种、
数量、限价、reduce-only、客户订单编号和场所订单编号必须一致。
条件单、对冲模式、改单、强平、ADL、其他事件和未知字段明确拒绝；不猜测尚未支持的语义。
自成交保护到期保留原始 `EXPIRED_IN_MATCH`，规范化数量路径使用到期状态。

保留四类时钟：消息 E、事务 T、订单 T 和本机接收时间。只有实际接收时间用于分段。
同一声明流的唯一事件不能倒序；原文重复或相同成交 ID/完整订单事实重复不会再次计费，
冲突成交 ID 则拒绝。每次成交的增量数量必须与累计数量之差相等。

手续费保留消息中的有符号金额及币种，maker/taker 角色逐笔留存；不从费率反推已付金额。
USDC、USDT、BNB 分开，缺失金额或币种不补零，也不凭空折算。
只有声明窗口一致、无声明缺口、全部固定订单有回执且费用币种完整时才计算
“报告费用 − 模拟费用”。这仍包含成交数量差异，不代表单独的费率改善或盈利改善。
原始消息、来源声明和自报覆盖范围不证明真实来源，`actual_paid_fees` 始终未知。

## 导入合同与交互

原 P5 输入中的 `tape`、`actions`、`account` 沿用已有合同；`calibration` 可选择：

| 字段 | 内容 |
|---|---|
| `schema` | `PASSIVE_BINANCE_ORDER_RECEIPTS_V1` |
| `cutoff_ms` | 校准段截止点，位于固定盘口共同窗口内 |
| `raw_observed.schema` | `BINANCE_USDM_ORDER_RECEIPTS_V1` |
| `raw_observed.symbol` | 与盘口和固定动作一致的合约 |
| `raw_observed.account_ref_sha256` | 匿名账户摘要；有冻结账户条件时必须相同 |
| `raw_observed.action_hash` | 现有 `ai_factory_store.digest(actions)` 的动作摘要 |
| `raw_observed.provenance` | `SYNTHETIC_TEST_FIXTURE` 或 `IMPORTED_UNVERIFIED` |
| `raw_observed.coverage` | `start_ms`、`end_ms`、显式 `gaps`；不超过150秒 |
| `raw_observed.bindings` | 每项为 `order_id`、`venue_order_id`、`client_order_id` |
| `raw_observed.messages` | 按本机接收顺序，包含 `received_at_ms`、`raw_utf8`、`response_sha256` |

`bindings.venue_order_id` 必须用十进制字符串；原生编号在 `raw_utf8` 中原样保留，
结果中的订单/成交编号也使用字符串，避免浏览器对大于 `2^53−1` 的整数改值。
所有数量、价格与费用为精确十进制字符串；原文摘要为 UTF-8 字节的 SHA-256。
不接受凭证、listenKey、请求头、签名地址、任意网络端点或嵌套的未知字段。
原有规范化 `PASSIVE_RECEIPT_CALIBRATION_V1` 输入保持兼容。

Studio 沿用“研究数据导入 → P5 原始盘口与队列 → 登记输入并运行”，新增格式说明。
结果显示消息/重复/成交/缺失数量、逐币种费用、三情景费用表及柱状图；展开后查看
成交编号、数量、角色、四种时间与来源摘要。精确表格可横向滚动，页面不能横向溢出。
完整诊断写入不可覆盖的 `native-observations.json`；前端最多展示最近50笔成交。

## 预注册软件与接线验证

- 固定合成普通订单，不新增模型调用、候选或参数范围；既有三组校准和三组完整回放全部登记。
- 部分成交、撤单竞争、STP到期、重复包、重复成交冲突、累计量与成交量不一致。
- 四类时钟、截止点后接收、声明缺口、不同窗口和未收到回执的订单。
- 缺失费用、其他币种、负报告费用，以及大整数编号的端到端精确保留。
- 只改报告费用时，冻结延迟、数量比较和模拟账本结果保持相同。
- 原始输入不回写；费用只在原模拟账本扣一次；Console合同与桌面/手机显示一致。

## 当前开发检查

最初7条正向路径失败、19条既有拒绝通过；接线后原始/规范化回执38项通过。
工作流起初仍直接读取 `observed`，两项真实接线失败保留，改为先规范化后进入既有流程。
合成辅助夹具只产生一笔成交，首轮错误预期两笔的测试失败已按实际夹具修正，
不把测试预期错误算作原系统缺陷。大整数编号回归先失败后修正为字符串合同。
相关后端129通过、1跳过、1项既有Starlette告警；新增边界38项通过。

前端新增组件在实现前导入失败，之后相关5项通过；类型与实际ESLint通过。
并行全量首次355通过、旧兼容界面异步标题查找1项失败，原始日志保留，
使用canonical既有单worker方式复核，不修改该测试或放宽断言。
单worker全量 **356通过 / 60文件**，140.04秒；构建5.20秒通过。
依赖图20域、53边、零环，新增研究域到既有JSON完整性工具的治理域引用；治理扫描通过。
实际界面、完整源码验收及最终结果继续记录。

日志目录：`/home/kerstker/quantos-dev-output/account-terms-20260911`。
本增量只交付离线软件路径；没有真实私有回执，仍不能验收场所或盈利。

## 核对的官方依据

2026-09-11核对 [币安 USD-M 用户数据流](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams)：
事件和状态枚举、E/T次序、配置变化及流过期含义。当前渲染页的嵌套schema未完全展开。
[币安 STP 官方说明](https://www.binance.com/en-BH/support/faq/detail/0941126f6413485b9a3df964a9aa2306)
提供USD-M订单事件示例，核实本次普通订单子集使用的字段及 `EXPIRED_IN_MATCH`。
该示例不是完整当前schema或本账户证据；未因此接入COIN-M、期权或全部条件单。
