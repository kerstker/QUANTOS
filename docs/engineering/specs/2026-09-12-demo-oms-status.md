# Demo 订单恢复状态与工作台

2026-09-12。承接[OMS 恢复](2026-09-12-demo-oms-resolution.md)和
[Demo 执行计划](../plans/2026-09-12-demo-execution-completion.md) D3。

受保护恢复入口把输入未齐、配对后等待投影、投影中断和整个批次恢复完成分别保存为
不可变显示记录。完成状态绑定实际投影结果、计划摘要、原预留和提案身份；缺行、
不同批次、数量冲突、未来时间和虚假的现金认证均拒绝。中断可能已有部分事件落盘，
完成数量保留未知，恢复时仍由原 OMS 事件去重，不从显示文件驱动订单恢复。

Console 增加 `oms_status`，对应 TypeScript 合同及 API 测试同步。Studio 展示固定
批次订单数、配对数、已恢复数、原编号、场所编号、终态及精确成交数量；逐单详情
默认折叠，手机按字段纵向排列。来源未认证、现金核对和经济准入保留各自状态。
刷新失败时保留历史记录，不将其描述为当前账户状态。

执行侧可用 `tools.demo_oms_status_publish` 把经过结构和内容摘要验证的显示副本
发布到研究缓存 `data/cache/binance_demo_oms_status`。来源文件只读，目标目录必须
独立；损坏、重叠目录和倒退时间不替换已保存视图。该工具没有密钥、网络、下单或
恢复入口。研究缓存中的显示数据也不能用于执行权威，实际受保护发布服务尚未安装。

```bash
python -m tools.demo_oms_status_publish \
  --source-dir /var/lib/quantos-demo-executor/receipt-reconciliation/views \
  --output-dir <research-state>/data/cache/binance_demo_oms_status
```

以上命令由有相应读取权限的执行侧发布者运行；研究 Console 只读副本。本次没有向
运行状态写入合成数据，没有发送普通订单，也没有安装独立签发配置。

验证材料位于 `/home/kerstker/quantos-dev-output/demo-receipt-binding-20260912/`：

- 后端相关 **102 通过 / 1 跳过**，`oms-status-final.log`。覆盖真实保护入口中的
  合成回执、原查询、事件写入中断、再次恢复、原输入不变及重复运行不增成交。
- 前端全量 **406 通过**，`oms-ui-all.log`；类型检查通过。首次 ESLint 因拒绝测试
  使用超精度数字字面量失败，保留 `oms-ui-eslint.log`；改用显式字符串转 Number
  构造非法输入后通过，未放宽产品编号合同。
- 四种合成恢复状态使用原 Console HTTP 处理器，桌面/手机/暗色共十二组核验；
  首次截图发现手机表格过密，修正纵向布局并再做浏览器核验。这里的成功仅覆盖
  隔离软件输入，不能当作真实 Demo 成交、现金闭合或盈利证据。

本源码仍需新的完整验收。实际运行仍为 `95238a31d` / 源码 `2e1d8b9d1`。
独立签发和驻留执行、持仓后续批次/撤单、时钟合同、D4 真实工况、全规模恢复指标
及冻结自然研究验收继续推进；没有由这一显示增量宣称第 1–4 项整体完成。
