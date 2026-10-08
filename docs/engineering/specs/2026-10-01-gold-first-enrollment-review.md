# XAUUSDT Demo 首次登记的保护分区审查

接续[停机历史校验](2026-10-01-demo-market-partition-stop.md)，父源码
`bb4b565af1366a30ce9623534440fdd4695c59e4`。原 successor 仅续接已登记市场，
旧 BTC 登记不能通过 `--symbol XAUUSDT` 变成黄金权限；原拒绝保持。
本增量提供独立只读审查，补齐实际首次登记前材料的读取交接。

## 实际保护源与原账本

`first_gold_enrollment_review` 沿原 executor 私有角色、immutable source、
两角色镜像及最后 activation 的读取器核验已安装分区。registry 必须仍是
原已过期 legacy registry，目标历史必须不存在，完整旧 state 必须等于原
切分点；不接收 caller context，也不把旧 BTC context 改名成黄金 context。

沿同一 V2 replayer 复核原 context 与损失/峰值/容量等风险事实，再用当前黄金
完整账户观察核对全账户空仓、非目标 collateral 和封存 USDT 金额。保留 USDC
原账本及其单位，不折算、不创建黄金账本或 pilot 基线。报告明确目标 context
为 None，黄金尚未登记，所有批准/下单/经济权威标记 false。

## 原 worker 与操作员交接

原 context 操作新增显式 `first_gold_enrollment_origin_sha256` payload，只读
固定 XAUUSDT 目录下的哈希文件。worker 持原 preparation lease，前后核验
原生停机历史、source/account、registry、观察及完整 state 未变，继续受原
65000响应与65536管道预算约束。诊断结果沿原 append-only results 留存。

操作员入口新增 `--review-first-gold-enrollment`，仅在原 BTC setup、无 pending
登记时读取保护分区并取得新黄金观察；沿原 Runtime 验证 source/cycle。
审查不能与 enroll、run-cycle、symbol、市场迁移、组合或研究身份变更并用。
输入仍需现有 root/source 门；不执行批准确认、签发、密钥、预算或服务操作。

接收端同时携带和重验原候选摘要、完整 binding、旧 state/context 身份及旧
风险数值，保留原 observed/book/mark/funding 的最早时效界限。过期、错钱包、
错来源、错 registry、改写旧风险或夹带权威标记均拒绝。报告仅为审查材料，
不能安装成 issuer policy、mandate 或初始资金记录。

## 验证与剩余范围

最初新夹具漏导入 book，14项 fixture error 留存；修正后在原 bb4 源码
独立复现14项缺接口失败/11.92秒。纯边界14通过/10.26秒。首次联合103通过/
1失败为 CLI 夹具误用 registry 文件名，改为原 authority 路径名；随后六组
105通过/89.18秒。审阅追加旧钱包摘要改写反例，先1失败/2.40秒，再补齐对
原候选与旧风险值的交叉绑定。各次日志均保留，最终结果另记录。
最终六组 **108项通过，101.88秒**，含新审查、原分区运行、原 worker 管道、
successor 和停机拒绝边界；包含最终完整材料的真实子进程管道验证。

新的真实本地子进程管道已使用原协议、SQLite、receipt/OMS 与 V2 重建；
角色/属主/已接受源码为隔离替身，没有 sudo、真实账户采集、凭据或订单。
这不证明现场来源认证、黄金独立登记或共享预算已经完成。实际首次登记安装、
新独立批准/策略预算、24小时 pilot、监控未知事件、旧格式/跨版本恢复、
最终完整验收、现场升级/回滚、资金反馈再研究和多资产验收仍待关闭。
开发回退须先核对本增量原字节备份与没有后续修改；不回退运行账本或实验历史。
