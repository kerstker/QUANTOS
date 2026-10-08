# 保留全执行历史的市场分区候选

本增量续接[迁移核验](2026-10-01-demo-market-transition-review.md)，实现原执行
进程生成候选、完整旧前缀检查和独立 collateral 的原账本重建接口。没有
安装市场迁移，也没有改变现有执行准入；原跨市场续期仍拒绝。

## 原进程生成候选

本机操作员可在已接受的完整源码版本使用原工具的
`--review-market-transition XAUUSDT --prepare-market-partition`。原受保护
BTC 登记、无 pending、expired registry、controller/cycle 租约和源码
核验继续保持。此入口保存原账户观察，之后仅向原 executor 的 context
操作传两份 retained origin 的固定文件摘要，不传路径、权益或新许可。

executor 检查身份、源码、私有管道和原目录 custody；核验仅 BTC cycle 可
调用。在固定 BTCUSDC/XAUUSDT 目录加载原哈希文件，持原 preparation
租约读取全 dispatch/recovery/OMS/wire，调用原 `load_context` 重建旧账本
并做两份账户观察核对。原 stop 文件通过原 quiescent export 检查，保留
字节摘要；不能把摘要称为已认证 stop 链。状态、stop、原观察、registry
在结束前重读比较，候选在原 append-only results 中留存。输出仍是未安装
候选，最大响应65000字节，继续沿原65536字节管道限制。

操作员侧核对原 Runtime 的 source/cycle 身份和候选的源码、registry、两
份 origin 摘要。没有生成 Ed25519 key、签名、mandate、profile、policy、
执行任务或订单，没有服务启停。agent 不运行实际受保护入口。

## 分区内容和原账本复用

`live.execution.demo_market_partition.prepare` 在原 canonical replay 和
双观察核验后封存：完整旧状态摘要、旧 reservation/wire 数量、全旧 tail、
旧 canonical context 摘要、原 registry/stop 声明、来源版本和独立 USDT
观察金额。源状态须全 BTC、全部已终结；沿原32批/40000 wire有界预算。
候选的 installed、approval、runtime_authorization、source/stop verified、
submission/economic 标志保持 false。

`partition_state` 先核对全局 reservation/wire 哈希链及所有 OMS/recovery
身份、品种和整数时间，再按封存的实际前缀重建整个旧状态，摘要必须完全
相同。随后只接受同账户、封存之后的 XAU 追加记录；旧 wire 后追加、丢失
历史、修改旧恢复/OMS、混用 client ID、未决记录均拒绝。输入状态来自原
已核验 store snapshot；这个纯投影不会认证签名或赋予权限。

`rebuild_contexts` 分别调用同一个原 canonical replayer，旧账本按封存
时点重建且 context 摘要须一致。已有黄金历史必须以原观察 USDT 钱包为
初始 collateral，差异拒绝。无黄金历史返回 target=null，不能凭过滤后的
空列表制造初始账本。两种 collateral、费用/权益与风险单位分别保留，
没有折算或合成总权益。

## 尚待完成

候选尚无独立保护安装入口；新 registry/profile/mandate/shared-budget
登记、停机链复核、旧配置字节留存、最后发布及中断恢复仍需实现。
`load_context` 和原请求/发送前门目前没有接入已安装分区，因此候选不能
让黄金执行通过。后续必须让每次原提案/签发/发送重新验证已安装绑定和
完整历史，兼顾 target 无历史、已有持仓、跨日及恢复；不能仅过滤旧数据。
持续 pilot 和真实成交/现金反馈仍未验收。

## 证据和限度

新分区接口22项先缺模块失败，worker接口8项先缺方法失败；初次联合为
1失败/61通过，缺文件异常已转为有界拒绝。另两项真实语义回归复现 bool
OMS 时间和错品种 recovery 未被拒绝，修复后五组70通过。增加 worker
main 的实际候选留存/拒绝接线3通过；原子进程隔离检查加入context操作。
Ruff、导入图、hygiene和真实提交 hook 另行留存。

旧前缀、receipt/OMS/recovery、V2 replay、租约、保留原件及状态不变使用
真实本地实现，身份/custody/源和账户传输仍为隔离夹具。目标追加投影为
元数据夹具；target canonical 调用接线有替身，不是新黄金已签批次的
完整混合历史验收。没有实际凭据、现场运行、迁移安装或策略研发。

完整发布验收须覆盖这个冻结版本。尚未开始的旧中间版本验收可保留记录
后合并到最新整合版本；已经运行的原完整门继续，不删除或隐藏失败。
