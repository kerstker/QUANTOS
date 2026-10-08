# Demo 第三批：只读订单查询、身份绑定与恢复差异

2026-09-11。承接用户“继续”，开发起点 `ab12318c0`，分支 `codex/demo-integration-20260911`。
本批开发了操作员只读查询入口和前后端差异诊断，并修正既有 Binance 适配器的未知订单语义；未部署、未读取实际凭证、未发起私有查询或下单。

## 交付结果

| 内容 | 本批行为 | 证据边界 |
|---|---|---|
| 固定查询计划 | 从第二批不可变规格与回执日志生成；绑定完整标的、客户端/场所订单号、方向、数量、价格、提交时点、账户引用、规格与日志末端 | 查询最多10个已绑定订单；不是任意账户扫描；账户引用仍是声明 |
| 独立采集入口 | `tools.binance_demo_order_query.py` 默认零网络、零凭证读取；显式账户读取模式才做公共时钟 GET 与已绑定订单 GET | 不注册 AI/Runner，不创建用户流、订单、撤单、重试授权或现金流水 |
| 有限请求 | 固定 Demo 主机、仅2种 GET 路径、单响应16KB、单连接10秒、整批子进程35秒；失败后剩余订单记为跳过；不重试 | 整批超时记录未知，不能声称超时前各请求结果已经保留 |
| 逐次留存 | 内容寻址 JSON 保存每次输出，原子更新 latest 摘要指针；只读消费验证指针、契约与内容 hash；重复保存不覆盖历史 | 没有独立签名；成功响应只保存白名单字段与原文摘要，不保存签名 URL/请求头；超时子进程输出不进入报告 |
| 精确比较 | 对比回执与查询的累计数量、状态、时间与完整身份；识别漏收成交、缺失状态回执、数量倒退、终态冲突、旧观察 | 查询累计值不会生成虚构的逐笔成交，也不进入 V2 计费 |
| 复用状态规则 | 将原有快照可达性检查移到 `quantos/contracts/order.py`，既有 OMS 和新诊断共用；状态图未改变 | 新诊断没有修改 live OMS、command store 或恢复状态库的权限 |
| 修复旧适配器 | `BinanceFuturesAdapter.get_order` 遇到 -2013 改为 `ORDER_NOT_FOUND_UNCONFIRMED`，既有恢复流程保持 INDETERMINATE | 不再把此结果当确认不存在；撤单接口 None 的既有回查语义保留 |
| Console/Studio | Demo API 新增 `order_recovery`；展示原始回执与查询两列、精确数量差、未确认原因和来源；提供手机直达入口 | 成功只表示订单观察相符；始终 `new_command_allowed=false`、`source_verified=false`，实际流/现金对账仍未知 |

新增 `query_binding_hash` 将固定动作、订单绑定和账户引用绑定到回执报告；查询计划使用相同合同计算，防止数量相同但订单或账户引用不同的快照被读成匹配。
回执报告增加最后订单事件时间；任何源自未来、过期、错规格或旧日志末端的查询都不能显示当前匹配。已知日志更新时要求重新查询，不复用旧成功。
报告仍保持 `TECHNICAL_VALIDATION_ONLY`，不会因此产生经济准入、自动重试或真实费用结论。

## 接口依据与语义修正

官方 Query Order 文档说明：部分无成交的取消/过期订单超过3天，以及超过90天的订单，可能不会再被查到。因此，“未找到”不足以证明订单从未存在；本批据此保留未知，而不把它用于重试依据。[Binance Query Order](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade#query-order-user_data)

实际代码固定使用 Demo REST 地址；查询为 `/fapi/v1/order`，按完整 symbol 与 origClientOrderId 查询，再核对返回的场所订单号及固定条件。本轮核查日期2026-09-11；没有借官方文档声称实际私有接口已读通。[Binance General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info)

## 操作与恢复

本机开发示例以下默认**不请求网络**，只读取离线日志并记录“未发起账户查询”：

```bash
/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905/venv/bin/python -m tools.binance_demo_order_query \
  --journal /home/kerstker/quantos-dev-output/demo-order-recovery-20260911/state/data/cache/binance_demo_execution.db \
  --output-dir /home/kerstker/quantos-dev-output/demo-order-recovery-20260911/default-cli-captures
```

操作员在独立环境已配置 Testnet 环境变量后，显式追加 `--account-read` 才使用这些凭证进行只读查询。不要将密钥放在参数、网页或输入文件中。当前开发未执行此模式；验证使用注入的假传输和假凭证。
退出码0代表所有条目已找到或明确未请求；2代表存在未知/跳过；3代表输入、采集或保存失败。默认模式退出0不代表连接成功。
Console 当前状态根的读取位置固定为 `data/cache/binance_demo_order_queries/latest.json` 与同目录内容文件；不能用其他空间的日志或查询覆盖。没有快照不创建目录，损坏明确显示。
每次采集分别保存；查看历史可按输出摘要定位原文件。CLI 不根据结果更新第二批回执日志；缺失逐笔回执仍需从实际来源补齐。

## 验证记录

先复现缺失模块、Console 消费、页面状态；还复现了“相同数量但不同客户端/场所订单或账户引用”误匹配，并补强绑定。旧适配器 -2013 的两条回归先失败：直接查询没有异常、恢复状态错误地成为 CONFIRMED_ABSENT；修复后通过。查询数量倒退与终态冲突的主原因优先级也在开发中修正，失败保留。

- 主相关集 **233通过/1跳过**：回执与查询、Console契约、既有OMS/订单事件/恢复/启动和依赖图。
- 最后查询补充集 **24通过**：完整身份错配、限流后停止、超时、只读历史等。
- 旧适配器/实际恢复模块补充集 **93通过**：Binance、恢复门、协调器、Testnet入口及MFT测试。以上集合有重叠，不相加。
- 前端全量 **377通过**，类型/ESLint通过；最后直达入口与最终源码通过构建。
- 临时真实 Console → 浏览器 GET：缺少0.6 BTC回执 → 追加合成回执后数量状态一致 → 未找到保持未知 → 过期隐藏比较。桌面、390px手机、暗色均无页面溢出或WCAG 2 A/AA扫描违规；手机直达、表格键盘横滚、来源展开及刷新通过，0页面异常、0 HTTP写请求、0浏览器直连场所请求。
- 默认CLI实际执行：1个固定订单、0个找到，输出“未请求”，没有网络或凭证读取。回执原文件未被比较程序改写。

输出 `/home/kerstker/quantos-dev-output/demo-order-recovery-20260911/`：`targeted-tests.xml`、`query-final-tests.xml`、`adapter-recovery-tests.xml`、`browser-check.json`、四场景捕获/截图、`frontend-dist/` 及检查日志。全部浏览器数据明确标为合成；真实账户与私有流未验收。
仓库卫生、治理和服务依赖检查属于本批增量证据；未运行完整 `scripts/validate.py` 或双冷 ARM64 发布构建。最近完整软件验收仍属于 `3f65322c5` / `244dd6fbe`，不转移到本批。

## 后续顺序

1. 本批完成订单快照读取软件、差异诊断和旧恢复语义补修。仍需真实 Demo 私有来源、用户流断线与补拉、实际撤单竞争、固定命令/风险结果到订单的来源绑定，以及生产 OMS 投影收敛；当前只复用了其状态规则。
2. 补齐逐笔成交后，接同账户/窗口的持仓、钱包、标记、资金费和外部流水，才能核实唯一账本现金。累计订单查询不能代替该步骤。
3. 上述真实运行证据再接既有 MFT read model，随后推进有限实际场所校准；不将诊断一致填成 MFT 全部就绪。
4. P3/P4/P6 保持冻结观察。独立提交权威、人工交易门、部署与经济准入条件没有改变；本批没有新的盈利结论。
