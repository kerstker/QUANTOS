# 黄金数量与清算单位：公开来源核对

2026-09-28从官方合同目录逐级定位并下载原 PDF，原字节、提取文本和请求/接收时间回执保存在本机 `quantos-dev-output/gold-rule-sources-20260928/`，未读取账户或凭证。

## 依据与推导

[Gold Perps 合同规格](https://www.binance.com/en/about-legal/contractspecs-goldperpetual) 当前链接的2026-01-29 PDF 定义 XAUUSDT 的标的为一金衡盎司黄金价格指数、USDT 报价与结算，交易数量使用 XAU。规格把 Contract Size 的定义交给清算程序，不能仅凭 exchangeInfo 的 baseAsset 推断完整单位语义。

[官方清算程序](https://www.binance.com/en/about-legal/clearing-procedures) 当前 PDF 的4.13(a)、85.1、86.1分别定义 USDS-M 的数量为标的资产数量，仓位价值使用数量与标记价格，浮动盈亏使用数量、方向及价差。4.12所述 Contract Multiplier 是 COIN-M 的术语。因此，在本系统的线性账本中，将以 XAU 为单位的 API 数量映射为乘数1是这些公开规则的工程推导，不是声称官方给出一个名为 multiplier 的 XAU API 字段。

原文摘要：gold-spec SHA-256 `e6cdd2a9d1f65380634a191c60ccee8f78dc1657c21809f5206fd553db3fe014`；clearing-procedures SHA-256 `b86bd5d3c3f9812f0f1f6441a38709fc368c13452c22d1355e869d2bd8b4e948`。通过官方网站当前链接发现，两个下载原字节哈希均已核对；不执行 PDF 内容。

## 实现边界

这解决数量映射缺少公开依据的问题，不授予经济权威、真实账户适用性或 Demo 等价性。未改变 gold_contract_profile 的 PUBLIC_FILTERS_ONLY 语义，其 contract_multiplier 仍为 null；后续完整规则装配应额外绑定这些来源，不能把仅过滤器输入升级成已核验执行 profile。

接收时间晚于历史样本，不能直接倒填过去的规则可用时间。账户费率、保证金档位、实际清算费用、历史规则区间、Demo 适用性和真实成交核对仍未完成。当前 BTC public replay 使用明确的模拟保证金和费率，也不能转标为黄金实际规则。
