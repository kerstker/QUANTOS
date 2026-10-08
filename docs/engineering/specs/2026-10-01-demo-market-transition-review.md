# 过期 BTC Demo 到黄金的迁移核验

本增量提供核验前置条件，不安装迁移，也不构造黄金授权。原 BTCUSDC 使用
USDC；XAUUSDT 使用 USDT。二者不能以换交易对、复制余额或重置执行存储来
续期。`demo_successor_setup` 的同市场限制继续保持。

## 已实现的入口

`tools.demo_successor_setup --source-commit <已接受的完整提交号>
--review-market-transition XAUUSDT` 只供原受保护的本机操作员流程使用。它复用
原源码/注册表检查、controller/cycle 租约、executor context 及原账户观察
边界。账户观察仍会按原工具保存证据；核验不生成批准、私钥、签名、任务，
不安装配置、不启服务，不替换或清空任何执行存储。不应由研究代理运行此
受保护入口。

核验要求原登记为 BTC、无 pending 安装、旧公钥窗口全部到期、原 context
已有当前 UTC 日的核对。核验模式拒绝补建 day checkpoint；管理员须先完成
原跨日现金核对。所有 enroll/run-cycle、portfolio/research 和 symbol 参数
与此模式互斥，参数错误在受保护读取之前拒绝。

纯函数 `live.execution.demo_market_transition_review.review` 先调用原
`validate_origin`，核对旧 USDC 观察与已重建的 canonical wallet。随后要求
同账户、同 capture 源码的后续黄金观察满足原 freshness、全部订单类别、
账户模式及场所规则检查。两份完整账户观察须均空仓，全部 collateral
自由/平坦且金额一致；其他 collateral 的持仓或无法解释的金额不能忽略。

结果保留旧 context 摘要、原初始权益、canonical wallet、日损失、峰值和
turnover/capacity 消耗；另列观察到的 USDT 钱包。没有跨币折算，也没有
合成总权益。`NEW_GOLD_ENROLLMENT_REQUIRED` 是下一交接缺口，所有
approval/submission/migration/economic 标志保持 false，source 和 atomic
snapshot 仍未认证。此 JSON 不能被当作 issuer policy、pilot baseline、
新账本或已批准迁移。

## 后续完整交接要求

1. 原追加执行存储继续完整保留。现有 `load_context` 要求所有 reservation
   同 symbol，不能只过滤 BTC 后宣称黄金无历史；需要受审查的市场分区
   binding，包含整个旧存储前缀、已完成核对及旧停机/审计材料的身份。
2. 黄金初始 collateral 必须来自启动时原实际 USDT 观察，旧 USDC 的亏损
   和权益另保留，不能将两种单位相加或把旧 USDC 基准称作 USDT 基准。
   继续复用唯一 V2 现金流算法，各市场前缀和容量的单位显式保留。
3. 新黄金 mandate、profile、issuer/shared-budget policy 由独立本机
   操作员另行批准。旧三单/一小时功能模板不能自动扩大为24小时，旧过期
   BTC 批准不能被复用。新的 profile/risk 仍须原执行门验证。
4. staged 安装必须保留原配置字节和停机状态，发布顺序、中断 UNKNOWN、
   恢复和跨市场旧任务拒绝须有原执行链测试；之后才是持续 pilot 和真实
   成交/资金核对。当前尚未实现这项安装，不允许据本核验启动黄金执行。

## 验证

新增15项检查先因缺入口失败。第一次核验与原续期共28项通过；加入原
操作员接线和实际本地回执重建后，六组72项通过。最初集成夹具缺临时父
目录导致1失败/71通过，记录保留，修复夹具后72通过。账户、批准与传输
仍是隔离合成材料；原 SQLite receipt/OMS/recovery 重建及状态不变检查
使用真实本地实现。Ruff、导入图和实际提交 hook 另留存。

这个增量须由自身冻结版本完成发布验收；旧版本正在运行的完整门不覆盖
它。没有现场迁移、部署、凭据读取、订单或策略研发。
