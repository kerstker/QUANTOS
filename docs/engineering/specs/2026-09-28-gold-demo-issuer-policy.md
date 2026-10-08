# 黄金独立签发策略与持久化预算

## 实现

新增 DEMO_GOLD_ISSUER_POLICY_V1，字段沿用原有限签发策略，symbol严格为
XAUUSDT；旧DEMO_ISSUER_POLICY_V1仍仅接受BTCUSDT/BTCUSDC。
策略版本、原批准绑定及范围仍纳入策略摘要和原预留身份。

原 review_submission 按已验证的策略选择精确批次协议，重算黄金风险快照，
核对账户/原观察/规则/mandate/validation/策略批准及原订单上限。
原 IssuerStore 按同一策略协议检查订单摘要，生成对应黄金签名信封与正文；
重启时重查签名、预留、预算和原账户client ID约束。没有增加签发存储或
改变部署公钥登记与进程身份。

## 隔离联调

新增八项测试，合法签发与预算用例在修改前因ISSUER_POLICY_SCOPE失败。
修改后原签发器使用临时测试Ed25519密钥，经过完整原风险重放和预算预留，
生成黄金签名；执行端原prepare核验后写入SQLite意图，原execute经最终复核
取得一次模拟ACK。OMS没有因ACK产生填充。

回归覆盖旧BTC策略不能用于黄金、黄金策略不能改成BTC、精确上限低1e-18、
风险结果被改写、账户观察错配、过期策略、重复签发、签发预算耗尽和只读重启。
六组联合87 passed：gold_demo_issuer、demo_issuer（live/tools）、
gold_demo_preparation、gold_demo_submission_verification、gold_demo_dispatch。
新测试完整Ruff、两处实现E9/F821通过；治理698文件零违规。

使用同进程隔离fixture，网络、真实凭证读取和部署custody均替换为测试接口；
临时测试策略和批准只存在测试目录。没有改动已安装policy、公钥登记、
操作员批准或实际账户，未启动真正独立服务，未真实签发或下单。

## 剩余范围

纯签发/执行组件联通不等于研究候选已具经济资格。
黄金运行期请求profile/session、撤单/未成交恢复协议、黄金信号到受控执行
意图，以及系统成交到原资金账本反馈还需完成。
未部署、未完整canonical或真实Demo端到端验收。
