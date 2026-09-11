# Binance Demo 接入第一批：连接、账户读取接口与工作台

2026-09-11。承接用户“按照顺序来开发”，先推进执行工程；本批是开发增量，未部署、未下单，未完成整条 Demo 执行闭环。
分支 `codex/demo-integration-20260911`，开发起点 `3b85403732ff7b902d6fd5149d9500949edff280`。
最近完整软件验收仍属于 `3f65322c5` / `244dd6fbe`，不能转移到本批源码。

## 本批交付

| 交付 | 实现 | 证据边界 |
|---|---|---|
| 当前 Demo 地址 | `quantos/contracts/binance_demo.py` 统一 REST 地址；Binance 适配器使用该地址，拒绝旧地址及未知地址；公共 GET 不再携带 API key | Mainnet 构造条件不变；没有重接 V1 账本 |
| 提交入口提前检查 | `tools/testnet_execution_service.py` 在创建数据库、读取凭证、构造适配器前报告现有普通提交权威阻断；CLI 返回 `BLOCKED` 与稳定原因码，退出码 2 | 提前报告已有阻断，不新增审批流程，不让布尔开关替代权威 |
| 独立只读采集 | `tools/binance_demo_readiness.py` 默认两次公共 GET：time、exchangeInfo；检查 BTCUSDT/BTCUSDC、报价/保证金、永续状态及有限数量/价格规则 | 不代表覆盖全部交易过滤器；不写账本、候选或盈利材料 |
| 操作员账户读取接口 | 显式 `--account-read` 才读取两项 Testnet 环境变量并签名 GET `/fapi/v3/account`；保存白名单账户字段，Decimal 文本不经 float，不合并 USDT/USDC | 本轮仅假凭证和模拟传输测试；实际账户读取未验证；不把 `canTrade` 当作 QUANTOS 准入 |
| 有界运行和失败可见 | 固定主机/路径 GET、拒绝重定向、响应上限 2 MB、socket 10 秒、整个采集子进程 35 秒；无自动重试；公共子进程不继承交易凭证 | DNS/多地址连接也受父进程总时限约束；超时写失败观察；不是持续服务 |
| 前后端接线 | `GET /api/v1/console/binance-demo` 读取当前状态根的快照；Studio 新入口 `/studio/demo` | Console 不采集、不接触凭证、不提供交易按钮；每 15 秒刷新只读快照 |
| 时效与人机交互 | 120 秒时效；缺失、未来、损坏、失败、过期与上次成功分别显示；步骤、时钟、规则、资产、持仓、下一步工作区，桌面/手机/暗色 | 账户、订单流、账本对账分别显示未知；资产与持仓是场所观察，不是已对账的唯一账本 |

快照 SHA-256 用于标识读取的文件内容，不是独立来源签名或经济权威。界面 `REAL` 仅表示读取了符合契约且新鲜的观察；`authoritative=false`、`submission_enabled=false` 始终保留。
新采集程序未注册到 AI/Runner 工作流，浏览器也不接收密钥。现有自动研究、冻结自然窗口和常驻运行版本保持原样。

## 预先限定的验证范围

- 成功的公共采集限两次 GET，目标仅 BTCUSDT/BTCUSDC；失败即记录，没有循环扩搜。
- 显式账户模式最多再执行一次账户 GET；没有订单创建、撤单、listen-key、转账或配置修改请求。
- 单次样本用于判断连接、时钟和响应契约；不计算收益、不增加 Alpha 试验、不声称盈利改善。
- 后续订单与恢复测试先绑定冻结目标、唯一指令身份、来源版本和风险预算；真实 Demo 回执与主网回执分开登记。
- 继续条件：连接/账户响应可解析，失败和过期能正确传到前端；任何一项不满足则停在该步骤，不显示可交易。

## 验证记录

开发过程先复现旧地址、公共携钥、提交前副作用及缺失接口/页面；随后补实现。
扩展检查曾暴露导航计数、服务依赖图漂移；均按新增接口同步，未放宽依赖图政策。
真实浏览器联调中保留了临时 CORS 配置遗漏、采集子进程 30 秒外部超时、Axe 测试上下文和手机滚动区不可键盘聚焦的问题；后两项分别修正验证脚本与产品代码。网络总时限问题另有先失败再修复的回归。

本批最终相关检查结果见任务输出目录：
`/home/kerstker/quantos-dev-output/demo-integration-20260911/`。
包括 `targeted-final-tests.xml`、`browser-check.json`、三种视图截图及 `frontend-dist-final/`。
最终相关后端 **303 通过 / 1 跳过**，前端全量 **368 通过**；类型、ESLint、构建通过；仓库卫生专项 2 通过。
实际独立 Console → 浏览器检查覆盖桌面、390px 手机、暗色、刷新与往返导航，三种视图均无页面溢出和 WCAG 2 A/AA 扫描违规；手机表格键盘横向滚动通过；0 页面脚本异常、0 写请求、0 浏览器直连场所请求。
这些是增量验证；本批未运行完整 `scripts/validate.py`、ARM64 双冷构建或发布验收。

实际公共采集已成功读取两个 Demo 合约，曾读到：BTCUSDT 数量步长 `0.0001`、最小名义金额 `50 USDT`；BTCUSDC 数量步长 `0.001`、最小名义金额 `100 USDC`。
这些是当次场所返回值，会变化，不能替代冻结执行规则。实际私有账户、订单、成交与费用仍未验收；公共网络也曾发生超时，不能据成功快照声称连续可用。

## 独立开发环境复现

以下为本机路径。公共检查无需密钥，输出写入明确的开发目录：

```bash
/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905/venv/bin/python -m tools.binance_demo_readiness \
  --output /home/kerstker/quantos-dev-output/demo-integration-20260911/state/data/cache/binance_demo_readiness.json
```

Console 在对应 `QUANTOS_STATE_ROOT` 下只读该文件。输出必须是该状态根的 `data/cache/binance_demo_readiness.json`，不会自动复制到现有运行空间。
开发浏览器验证使用临时回环端口 16840 / 16911 和专属 CORS 来源；验证结束停止临时进程，未安装常驻服务。

账户模式仅供操作员在其独立环境已配置 `BINANCE_TESTNET_API_KEY`、`BINANCE_TESTNET_API_SECRET` 后使用，在同一命令追加 `--account-read`。凭证不作为命令行参数、不落入快照、不交给 AI。现有只读检查不能判断测试网真实订单能否提交。

## 后续顺序

1. 本批完成连接与接入诊断软件；下一批继续冻结目标 → 风险 → 指令 → Demo 回执 → 恢复 → 唯一账本 → MFT 前端的闭环。复用既有 command store、order recovery、pipeline 和 read model；补版本绑定和逐单证据，不建立第二本账。普通提交当前仍被独立权威缺口阻断，真实账户条件也未验证。
2. 执行链路可验证后，准备有限小额真实成交校准规格，明确范围、资金预算、期限和停止条件。实际执行沿用现有人工交易门，不因本批开发而授权。
3. P3/P4 与 P6 按各自冻结版本继续自然观察，成熟结果决定研究方向；不改变旧尝试、预测或外层样本身份。

本批落在统一路线 R4/P5 与 R6 接入观察部分；不是新增盈利结论，也没有将整个 P5 标为完成。

## 官方接口依据

- [Binance USDⓈ-M General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info)：当前 Demo REST/WebSocket 地址和请求规则，2026-09-11 核查。
- [Binance Account REST API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account)：V3 账户与资产/持仓字段，2026-09-11 核查。这里只实现账户 GET；WebSocket 尚未联调。
