# 黄金 Demo 账户观察与技术合约映射

## 目的与实现

F2 的账户输入此前只接受 BTCUSDT/BTCUSDC，无法把 XAUUSDT 的账户配置、
费用、保证金阶梯和规则交给原有 Demo 流程。本增量复用原采集器与账户核对，
不建立第二套余额或资金账本。

XAUUSDT 使用 DEMO_GOLD_EXECUTION_ORIGIN_V1，BTC V1–V4 原身份保持。
黄金规则额外保留实际响应 baseAsset；评估要求 XAU、TRADIFI_PERPETUAL、
USDT 报价和保证金、TRADING。缺失基础资产拒绝采集；不匹配的已观察规则保留，
评估为 UNRESOLVED。保留完整 filters、费用、阶梯、前后目标账户配置，
以及原时间边界、账户绑定、普通/条件单、同报价资产仓位和可用资金核对。

原 CLI 可选择 XAUUSDT；公开 GET 仍固定 Demo 域名/路径/参数，公开请求禁止
携带 API headers。默认路径仍不读凭证或联网。未运行真实私有账户捕获。

原技术合约转换器复用 V2 ContractSpec 和保证金表解析，但使用
DEMO_GOLD_ORIGIN_CONTRACT_PROJECTION_V1，显式保留基础资产、场所合约类型
及目标账户上限。乘数 1 是工程映射，contract_multiplier_verified=false；
结算量子仍是声明的模拟政策，不是交易所现金结算已验证。输出非权威且禁止提交。

## 验证

- 新增五项账户测试先以 ORIGIN_SCHEMA 失败；修复后通过。
- 消费者回归发现黄金被标记为旧 DEMO_ORIGIN_CONTRACT_PROJECTION_V1；
  修改前明确失败，修改后保留黄金独立身份。
- 八组联合 80 passed：gold_demo_execution_origin、binance_demo_execution_origin、
  demo_origin_symbol_config、demo_origin_compression、demo_origin_contract、
  demo_executor_origin、demo_pre_submit、gold_demo_market_inputs。
- 新测试完整 Ruff、三个实现文件 E9/F821 通过；治理扫描 698 文件零违规。
- 合成账户含其他报价资产仓位，目标 USDT 钱包 5000；不是用户账户事实。
  所有私有读使用测试传输与 synthetic 凭证，未读取实际密钥。

## 未完成项

未部署、未下单、未运行 canonical 全量验收。本增量允许获得黄金账户观察与
技术映射，不赋予黄金订单、独立签发或候选准入资格。真实账户规则/成本核对、
执行意图及独立签发的黄金交接、成交现金反馈与完整 F2/F3/F5 验收仍未完成。
原 BTC 订单协议继续拒绝 XAUUSDT。

## 受控执行进程接线续增量

后续检查发现 tools/demo_executor_origin.py 仍独立限制两个 BTC 品种，
导致公共/开发采集支持黄金但自动请求准备无法采集。现复用原采集器的精确
SYMBOLS 范围，覆盖父进程、捕获子进程及 CLI；保持 fixed_account、受控
凭证读取接口、网络 opener、进程锁、115秒超时、存储上限和哈希留档。
这是开发源码更新，未执行受控私有采集或变更安装配置。

新增五项回归，其中三项修改前因 PROTECTED_ORIGIN_INPUT/SYMBOL 失败。
覆盖固定账户读取、父进程保留黄金记录、错误账户拒绝、未支持品种在账户读取前
拒绝，以及原 request_prepare.capture_inputs 的黄金组合输入路径。
后者证明另一写入者更新 latest.json 后仍读取本次返回记录，市场输入保留
protected=True。测试中一处错误调用工具层 load 已修正为原模型 load，
并完整重跑相关七组：65 passed，Ruff 检查通过。

剩余执行接线已定位：demo_pre_submit 的账户条款检查及
demo_position_context 的恢复检查仍要求 V4；demo_submission 的订单范围、
demo_issuer_policy 的独立策略范围、旧订单查询协议仍仅支持 BTC；
demo_portfolio_task/source 是 BTCUSDC 小时模型，并非黄金吸收信号模型。
这些消费者必须共同验证黄金语义与原授权身份，不能靠全局增加一个 symbol
使观察自动成为批准。新增采集不修改任何已安装策略、签发策略或信任根。
