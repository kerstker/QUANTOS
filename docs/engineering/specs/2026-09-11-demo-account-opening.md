# Demo 账户起点与已有持仓增量

2026-09-11。开发起点 `1950808c6cc277c37305849b60ab33d3b6d35c23`，分支
`codex/demo-integration-20260911`。用户已授权 Demo 凭证安装与自动联调，并要求按依赖顺序继续。
本批完成账户起点采集、V2 带仓位启动及只读前端接线；不把既有账户浮盈亏当成本次策略收益。

## 固定检查规格与实际结果

先检查现有提交链，再补准确账户起点这一前置依赖。单次最多 9 次 GET：
公共时间 → 起始账户模式 → 起始余额/数量 → 起始合约配置 → 持仓价格/保证金 →
全账户普通挂单 → 结束合约配置 → 结束余额/数量 → 结束账户模式。
无自动重试，无交易、撤单、调杠杆、转账或订阅写入。

| 尝试 | 耗时 | 结果 |
| --- | --- | --- |
| 原解析器 | 4.646 秒 | `RESPONSE_INVALID`；保留已完成的两个账户读取和失败记录 |
| 修复中文合约名称后 | 8.026 秒 | 8 项私有读取完整；四组前后数值一致 |
| 最终真实 HTTP/前端复核 | 13.080 秒 | 8 项私有读取完整；四组前后数值一致 |

首次失败后另做两次单独的只读合约配置字段核查，只输出字段类型及无效的公开合约名称；
发现 Demo 列表有 740 条配置，包括中文名称。补回归后允许受限汉字和原英文数字名称，
继续拒绝控制符、空白和分隔路径；没有扩大可执行合约清单。

两次完整采集均观察到 **14 个非零持仓、0 个普通挂单**；BTCUSDT 为 **0.0097 BTC**，
单向持仓、单资产保证金，BTCUSDT 全仓且配置杠杆为 20。只是场所既有配置，不代表本系统选择或调整杠杆。
钱包/全仓钱包、三份数量观察、账户模式、全合约保证金/杠杆配置分别核对一致。
首份完整记录的 14 个仓位中，11 个按显示均价估算的浮盈亏与场所值存在细微差额；全部保留。
已有仓位、订单和配置未被修改。

## 实现

- `quantos/contracts/binance_demo_account.py`：限制条数、精确十进制字符串、重复身份、
  读序、接收时间和固定 Demo 环境。未知字段在采集时剔除；订单编号转为精确字符串。
- `tools/binance_demo_account_capture.py`：默认零网络/零凭证读取；显式操作员模式复用
  已安装的受限凭证文件。固定官方 Demo 主机、GET 路径和禁止重定向，公共请求不携凭证。
  单响应 2MB，socket 10 秒、子进程 110 秒总限；受限子环境，不保存签名、请求头或密钥。
- `services/research/demo_account_baseline.py`：完整、变化、失败、未请求、过期分别表示；
  内容摘要文件保留各次尝试，latest 原子替换，读取核验摘要/大小/路径。
  每种资产分别核对钱包 + 浮盈亏 − 保证金余额，不将 USDT/USDC 合并。
  仓位注明 `PREEXISTING_ACCOUNT_POSITION`，开仓均价×数量仅为估算成本。
- `services/execution/perpetual_ledger_v2.py`：新增不可变 `OpeningLedgerState` / `OpeningPosition`。
  明确总钱包含逐仓资金，逐仓分配只扣一次；精确历史 entry_notional、合约规则和来源摘要由调用方提供。
  初始化不制造成交/费用/容量消耗；截止时间含边界，拒绝同一或更早时点的后续事件。
  初始权益 = 初始总钱包 + 起点浮盈亏；本段价格盈亏扣除起点浮盈亏。
  部分平仓、资金费及原有风险/清算继续使用同一现金账本，旧空仓状态形状不变。
  旧仓位低于开仓初始保证金时仍支持符合既有条件的减仓，不因此允许新增风险。
- Console 新增 `account_baseline`，TypeScript 契约、API 解析器和 Console 契约测试同步。
  Studio 展示四组核对、合约筛选、开仓均价、标记价格、保证金模式、浮盈亏差额、
  分币种余额、已有挂单及采集时间/摘要；未知、失败及 120 秒后历史状态明确可见。
  页面刷新只读快照，不启动采集或接收凭证。

## 证据的实际边界

**REST 前后相同不等于原子账户快照。** 采集期间的往返变化、未覆盖的账户流水、条件/算法单和私有流缺口
不能由相同数字排除。当前仅查询 `/fapi/v1/openOrders` 普通挂单；并未声明覆盖独立算法订单服务。

`atomic_snapshot_verified=false`、`new_strategy_pnl=null`、`ledger_reconciliation=UNKNOWN`，
`submission_enabled=false`、`authoritative=false`。源码摘要和内容摘要用于复核本地材料，
不是独立受控的账户来源认证、部署身份或下单权威。

实际采集**尚未自动导入现金账本**：显示均价可能有精度差，尚不能直接当作精确历史成本基础；
全账户每个已有合约的适用数量网格、手续费、保证金档位和私有资金流水也需补齐。
`OpeningLedgerState` 已通过精确输入的工程验证；既有 Demo 回执日志的空仓约束仍保留，
没有把14个真实旧仓位转换成虚构成交或直接声明对账通过。

普通提交入口的独立权威集成仍缺失。用户的 Demo 测试授权已经明确，此处是尚未完成的工程依赖，
不是要求用户重复同意；没有绕过该入口、构造 HUMAN、重置 kill-switch 或更改主网准入。

## 验证

先复现缺失账本/采集/Console/前端字段，再实现；真实中文合约解析错误补专项回归。
保留最初测试夹具字段错误、类型检查失败和全部失败采集，不删除失败历史。

- 后端扩展：**856 通过、1 跳过**；包含全部 `tests/execution`、`tests/risk`、Demo工具/日志/
  查询恢复、Console API契约及相应执行/服务依赖边界。
- 提交入口、人工限额提案、Console/Runner边界及Git hook补充：**49 通过**。
- 前端全量：**380 通过**；类型、ESLint、生产构建通过。
- 真实只读 Console → 构建前端：桌面、390px 手机、暗色；合约筛选、无匹配状态、
  键盘表格访问、分币种详情与刷新无写入。过期记录与新采集分别通过；均零页面错误、页面横向溢出、WCAG 2 A/AA扫描违规、浏览器写请求或直连场所请求。
- 本批为增量技术验证；没有运行新的完整 `scripts/validate.py` 28 项或 ARM64 发布构建，
  不继承历史源码的完整验收结果，没有部署或切换正式服务。

最终补充明确界面普通挂单的覆盖范围；构建、16项页面回归及三种浏览器视图再次通过。
仓库卫生、治理扫描、服务依赖图和实际 pre-commit hook通过；真实凭证字节扫描未发现输出或暂存内容泄露。
临时 Console16840/前端16911已停止并核实端口关闭；没有切换常驻服务。

本机输出：`/home/kerstker/quantos-dev-output/demo-opening-account-20260911/`。
真实采集按各自源码摘要保存；`real-capture-attempts.json` 列出三次结果。

复现命令仅含文件路径：

```bash
/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905/venv/bin/python \
  -m tools.binance_demo_account_capture --operator-read \
  --credential-file /home/kerstker/QUANTOS-backtest-runtime-integration/key/binance_demo/credentials.json \
  --output-dir /home/kerstker/quantos-dev-output/demo-opening-account-20260911/state/data/cache/binance_demo_account_captures
```

退出0表示本次完整采集且四组观察一致，不表示交易/盈利通过；失败、未请求或变化返回2。
每次运行有界退出，不是持续采集服务。

## 后续依赖顺序

1. 固定本次订单的账户/合约/策略归属，核实精确起点成本、实际合约规则与流水完整性，
   把带仓位启动接入现有回执日志；不能直接用估算均价填平差额。
2. 将既有独立提交校验接到受控验证来源和真实人工限额，绑定部署、账户起点、有限订单计划、
   过期及一次性使用；保留研究/AI与凭证、交易执行隔离。
3. 有限 Demo 订单 → 私有回执 → 漏收补拉 → OMS 收敛 → 同一现金账本对账，验证恢复不重复下单/扣费。
4. 再完成 Demo 持续运行与新源码完整发布验收。Demo 工程通过仍不代替真实费用/场所校准或自然盈利证据。

## 官方接口依据

- [Binance Demo General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info)：固定Demo主机。
- [Binance Account REST](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account)：
  V3逐资产余额、账户模式、合约配置；不同保证金模式下不能混用全局美元与逐币种值。
- [Binance Trade REST](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)：
  V3持仓信息及普通挂单；持仓/挂单响应不是完整私有流水。
