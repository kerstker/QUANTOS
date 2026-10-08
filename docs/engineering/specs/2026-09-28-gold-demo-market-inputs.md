# XAUUSDT Demo 公开风险输入

原 Demo 成交量/分钟线工具只允许 BTCUSDC、BTCUSDT。新增明确的
DEMO_GOLD_MARKET_INPUTS_V1 与 DEMO_GOLD_MARKET_INPUTS_VIEW_V1，通过原采集器、
Decimal 波动率计算、双时钟包络、观察存储和时效校验接入 XAUUSDT。
BTC 的 V1 格式和默认解释不变，黄金数据不能改标签冒充原 BTC 协议。

仍然只有固定 Demo 域名上的 time、ticker/24hr、klines GET。没有账户调用、
凭证读取、下单或独立签发能力；原订单 V1 黄金范围拒绝保留。
source_verified、submission_enabled、economic_authority 均为 false。
本增量只提供量与波动观测，不证明合约乘数、账户费用/保证金档位或执行资格。

## 验证

三个新增回归先失败，随后接线；黄金、旧 BTC 采集、Demo 请求输入、签名、观察存储和
执行来源六组 **105 passed**。随后补充“公开观察不扩展旧订单协议”回归，黄金专项
**4 passed**。Ruff（既有代码E9/F821）、治理与仓库卫生通过，未做canonical全量验收。

实际公开采集保留两次独立记录，未改原时间规则或覆盖失败：

- 首次目录 gold-demo-market-inputs-20260928/1790583219674821835：四份响应齐全，
  首个探针 RTT=6114ms，超过5000ms；返回 INPUTS_UNRESOLVED。
  两探针中点偏移分别788ms和426ms，没有依据把此失败归为服务器时钟失准。
- 新观测目录 gold-demo-market-inputs-20260928/1790583387116204361：CAPTURED，
  在完成时评估为 OBSERVED，61根闭合分钟线、60个收益间隔，基础币24小时成交量
  29436.36、小时已实现波动率0.002249286245317625。
  原文件 e5c90bfaf3474855338f316c5379549d5a974c966fec755aa5b264520ac1848b.json。

以上是历史采集结果；超过30秒后仍应返回STALE，不能当作后续下单的当前输入。
时钟包络仅为兼容性观察，source_verified=false，不宣称因果认证。

## 后续交接

黄金 Demo 行情输入已接通。黄金账户观察、合约规则语义、执行意图、原独立签发范围
和资金反馈仍需继续接线与验收。没有扩大任何已安装签发政策，没有部署或下单；
这不是黄金 Demo 端到端通过，也不是完整工厂闭环完成。
