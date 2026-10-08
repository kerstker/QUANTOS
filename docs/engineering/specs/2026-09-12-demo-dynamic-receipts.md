# Demo 固定批次到回执与现金的动态绑定

2026-09-12。承接[执行计划 D2/D3](../plans/2026-09-12-demo-execution-completion.md)
及[现金投影工作台](2026-09-12-demo-cash-projection.md)。前一源码检查点为
`ba9f9cd2f34fd9c6d3d05284adba75b5d6604652`。本批尚未部署，也没有新增真实普通订单。

## 已接通的代码路径

原提交前提案与一次性预留 → 同一 SQLite 视图的发送/查询事实 → 持续私有流的
验证快照 → 新增的不可变订单绑定 → 原 Demo 回执日志 → 原 Decimal V2 → 配套现金报告。

`live/execution/demo_receipt_binding.py` 从原提案、预留、发送标记核对确切 client ID。
venue order ID 只能来自原 ID 的 ACK/查询或同命名空间私有回执；编号以十进制字符串
保留，不经浮点。发送结果未知时可用后续私有回执找回绑定，不再次发送。

ACK 本身不生成成交、费用或订单终态。不同来源的编号冲突、缺发送标记、同币种
手工订单、窗口缺口、错误选项或时钟均保留未解决。其他币种消息保留排除数量和摘要，
不混入当前币种账户。旧提案、预留、OMS、恢复记录和私有流均不被此导入改写。

绑定完整时，新增 manifest 使用原始发送时间、固定选项、实际起点捕获与规则，
导入原 `DemoExecutionJournal`。输出日志按 manifest 摘要命名，重启/重复导入不重复
计费；后续私有观察产生新绑定证据，不重贴旧输入身份。回执归一化仍采用现有共同
毫秒时钟约定，不能用估算偏移伪造延迟；实际时钟接线仍须独立验证。

## 账户规则与现金

复用原 `frozen_account_terms.parse_margin_table`，没有另写保证金计算。
配置杠杆与每档最大杠杆共同决定初始保证金率，沿用原 1e-24 向上取整约定；有限
档位上界与风险尾部估计政策完整保留到 Demo 输入解析器。调整系数、网格不兼容、
配置杠杆无有效档位等条件拒绝，不使用默认档位替换。

起点明确使用 `EXPLICIT_DEMO_QUOTE_QUANTUM_1E8_V1` 模型假设；它不是从显示精度
推断的真实结算规则。当前起点尚未保留符号配置的 maxNotionalValue，因此该额度
仍为未验证。容量使用冻结风险输入中的日度基础币成交量乘参考价格，明确为模型
名义金额口径，不能冒充已读取的实际报价币成交总额。这些限制不获得提交权威。

现金窗口可与位于期初采样保护区内的空仓起点绑定：起点金额必须相同，回执从起点
之后开始，所有起点前/同时刻的报告资金流水拒绝再次计入；期末和其余窗口检查保留。
成交佣金、资金费、外部资金与期末钱包仍只经过原唯一账本。

币安文档列出的到期原因 `er` 作为元数据保留，新增有界字符串格式兼容，既有整数
输入保持原样；不据此推导费用或盈利。原生 V2 可携带该字段，V1 历史不原地改解释。
参见[官方 USDⓈ-M 用户数据流](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams)。

## 操作入口

`tools/demo_reserved_receipts.py` 只读提交数据库和私有流，不读取凭证、调用网络、
发送订单或修改执行状态。参数要求原 prepared 记录、原两个数据库、固定时间窗、
明确源码提交和独立输出目录；可选择同一窗口现金输入。示例中的文件须换成实际
原记录，不能使用测试材料作为真实签发配置：

```bash
python -m tools.demo_reserved_receipts \
  --prepared /path/to/prepared.json \
  --dispatch /path/to/dispatch.sqlite3 \
  --stream /path/to/private-stream.sqlite3 \
  --start-ms START_MS --end-ms END_MS \
  --source-commit FULL_REVIEWED_COMMIT \
  --cash-window /path/to/cash-window.json \
  --output-dir /path/to/isolated-output
```

绑定、原始现金、导入报告与最终视图分别按内容摘要留存，`latest.json` 只是派生指针。
返回 0 仅表示所选技术投影可计算；返回 2 表示条件未齐，返回 3 表示输入/存储失败。
无效输入不泄漏内容，不覆盖已有结果。写入输出不等于发布驻留服务。

## 验证与剩余项

联合回归 **423 通过 / 1 跳过**，涵盖独立提交、预留/一次发送/查询、动态原 ID
绑定、只读数据库、唯一账本、原生/持续回执、账户档位、现金、CLI、Console 合同。
相关日志位于 `/home/kerstker/quantos-dev-output/demo-dispatch-20260912/`，
`dynamic-receipt-final-related.log` 为本次联合结果。初始红测试与测试输入/缩进错误
保留，旧测试断言没有删除。服务图 20 域 / 54 边 / 0 环，图证据随本源码更新。

已保存的真实 BTCUSDC 起点离线导入得到配置杠杆 20、10 档规则，规范化往返完全
相同，零新增网络或订单；`actual-origin-contract-projection.json` 保留原输入来源。
它是已有实际材料的技术投影，不是新鲜下单起点。联合链路测试使用隔离合成订单、
签名和回执，不能标为实际 Demo 工况通过。

前端现金工作台仍沿用前一检查点的 399 项验证与实际 HTTP 九组检查；本批未新增
前端形状。新源码的完整 28 项、ARM64 与运行发布尚未完成。

独立实际签发配置、受保护执行进程驻留、后续持仓/减仓批次、撤单、真正逐单的
OMS/查询收敛、场所时钟/私有覆盖及实际 D4 仍需完成。原账户旧成本、恢复指标与
自然前向盈利不能由这些合成回归替代。人工门、五个安全标志和经济准入未改变。
