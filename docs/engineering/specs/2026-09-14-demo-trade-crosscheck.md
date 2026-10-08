# Demo 逐单三方观察核对

2026-09-14。承接[历史回执恢复](2026-09-14-demo-historical-recovery.md)。

## 实现范围

`services/research/demo_trade_crosscheck.py` 在离线环境下，按明确的合约、场所订单编号，
将 REST 成交、留存私有成交消息、COMMISSION 流水逐笔对应。交易编号保留十进制字符串，
比较方向、持仓侧、数量、价格、成交时间、maker/taker、费用币种/金额及报告已实现盈亏。
重复私有投递不重复计数；缺少 REST/私有成交、同编号冲突、终态累计数量不符、重复费用
流水、错误币种、未来本地观察或损坏摘要分别保持缺失、冲突或拒绝。

`tools/demo_trade_crosscheck.py` 只读打开已有 statement 和私有流；使用原 150 秒有界导出，
保留原窗口 GAP、之前的 GAP、原流账户引用和完整本地哈希链检查结果。独占创建新的诊断
文件，不覆盖输入或旧结果。没有网络、凭证、下单、签发、OMS 投影或现金更新入口。

本工具的 OBSERVATIONS_MATCHED 只表示所给观察一致。原 statement V1 没有账户身份绑定，
所以 account_binding 恒为 UNKNOWN；latest_order_query 为 NOT_CHECKED。缺口存在时即使
所有已见成交一致，也返回 MATCHED_WITH_STREAM_GAPS，不能用它清除原恢复阻塞。

## 本轮真实记录

使用原受保护私有流与此前独立保存的 REST statement：

| 订单 | 性质 | 数量 | 手续费 USDC | 逐笔费用差额 | 结果 |
| --- | --- | --- | --- | --- | --- |
| 1203137495 | 系统开仓 | 0.002 | 0.07012169 | 0 | MATCHED_WITH_STREAM_GAPS |
| 1203150289 | 人工平仓 | 0.002 | 0.06996294 | 0 | MATCHED_WITH_STREAM_GAPS |

两单的已见成交字段均对应；两个局部窗口都含原拒收消息缺口，均原样保留。
读取前后原流已有记录前缀摘要相同。本检查没有修改原订单、恢复、OMS 或现金账本。

材料目录：`/home/kerstker/quantos-dev-output/demo-historical-recovery-20260914`。
`actual-opening-crosscheck.json` 与 `actual-manual-close-crosscheck.json` 是独立诊断，
不替代原 functional-cycle 结果，不将人工平仓改标为自动减仓。

## 检查和未完成项

新增回归在实现前因模块不存在失败；实现首轮 20 通过、1 个重复费用夹具排序错误失败，
修正夹具后，逐笔核对/离线工具/原 statement/原私有流联合 65 通过；输出文件独占权限
修改后工具 5 项通过（包含在前述范围）。新增模块触发来源图指纹变化，已重新生成，
图检查通过，仍为 20 域、53 边、0 环。失败日志全部保留在同一材料目录。

本增量在 a79d619ca 完整验收开始后开发，**不在该完整验收范围内，尚未安装或接前端**。
它提供下一步恢复所需的明确差异证据，尚需受保护同账户 REST 来源绑定、最新原编号查询
核对、账户缺口的独立恢复语义及人工外部平仓记录，再进入原唯一账本的正式恢复。
不得直接把此诊断的 MATCHED 状态当作投影许可或自动闭环完成。

## 固定执行账户来源采集增量（开发验证）

新增 `tools/demo_executor_statement.py`：从原受保护执行账户引用出发，由原 Demo 传输
适配器校验凭证与账户摘要相符，再执行原六类 GET。只允许 BTCUSDT/BTCUSDC、最多一天的
明确历史窗口；保留原 statement、其摘要、固定账户引用、采集源码摘要和时间范围。
父进程核对账户、窗口、源码、内容摘要和起止时间，115 秒子进程上限，失败单独留存，
不额外重跑整次网络采集。原 statement 工具仅增加内部读取器/网络适配器注入，默认离线
行为和原命令行不变。

正式留存入口使用既有 `origin-capture.lock`，与账户起点采集互斥；结果位于已纳入备份的
`receipt-reconciliation/statements/`，没有新增备份范围外的锁。该入口不改 OMS、现金、
原缺口、停机状态、签发政策或订单。摘要/账户身份匹配仍不等于独立签名来源或投影许可。

新增测试先因模块不存在失败；固定账户采集、原流水/起点、逐笔比较与备份联合 94 通过。
此增量也不在 a79 完整验收中，尚未作为驻留服务安装。实际账户采集及后续恢复另记结果。

### 固定账户实际只读核验

冻结提交 `dcbd515c59f68a553b32283eb5f9767b0072ad2a` 的源码归档经摘要校验后，在 root 所有的
隔离诊断目录运行原执行 UID 1001 的 capture-worker。没有调用正式留存入口，没有修改
服务、受保护结果目录、原 dispatch、OMS 或现金；输出仅保存到本任务材料目录。这是
已做相关测试的临时只读诊断，不能称为该源码已完成完整验收或正式安装。

实际窗口 `1789382380000`–`1789384850000`，六次 GET 全部取得结果：2 笔 BTCUSDC 成交、
15 条账户级收入流水，无采集缺口。窗口内其他交易对的流水没有计入 BTCUSDC 逐单费用。
采集账户引用与原执行私有流相同，statement 摘要
`0bd64602c56e29ab4c7f82d04fd15cc2c1ff92d17e653381dc76a554c4df510a`。
原 dispatch 读取前后快照相同。

新实采再与两个原私有窗口比较：系统开仓、人工平仓均为 MATCHED_WITH_STREAM_GAPS，
逐笔已见字段一致、费用差额零，原 GAP 均保留。原始 statement V1 比较器自身的
account_binding 仍为 UNKNOWN；隔离诊断的外层材料另记录固定执行采集及账户引用相同，
没有通过改写旧比较结果或原日志声称来源已被独立签名。材料为
`actual-fixed-account-statement.json`、`actual-fixed-account-comparison.json`、
`fixed-statement-source.json`，均在前述任务输出目录。

同时核对了币安[当前用户数据流文档](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams)：
账户更新是增量事件，轻量成交推送提供的字段少于完整订单推送。文档不能证明本轮拒收消息
具体是什么；本批没有据此猜测或放宽解析规则，精确拒收诊断仍等待实际消息证据。

### 历史兼容和费用页面

补充回归发现：结构校验不应要求旧记录的内部采集源码摘要等于当前程序版本。先复现
1 失败/15 通过，再将当前版本校验保留在新采集父进程边界；旧记录保留其原摘要，结构
校验不升级其来源权力。相关固定账户/原流水/起点/逐笔比较最终联合63通过，范围与前述
94重叠；新的内部采集源码不匹配仍由父进程拒绝。真实实采仍对应上文冻结 dcbd 源码，
没有将其冒充后续修改的实测。

已使用现行验收版本的原 `save_statement` 将本轮 BTCUSDC 窗口发布到研究侧原流水缓存。
此前18合约/84天的 statement 文件逐字保留，旧 latest 指针另存 `statement-previous-pointer.json`。
当前页面聚焦本轮窗口；这不是整个账户的全部历史。没有修改观察时间来伪造新鲜度。
实际 Console API 已返回2笔BTCUSDC成交、总手续费0.14008463 USDC、费用差额零；
现金对账仍UNKNOWN、策略P&L仍null，窗口外/其他交易对范围仍按现有界面提示。
发布记录为 `statement-publication.json`，本次只更新已有数据缓存，没有重启服务或改变
Console/TypeScript 合同。逐笔交叉检查器自身尚未接入页面或替代原账本。
