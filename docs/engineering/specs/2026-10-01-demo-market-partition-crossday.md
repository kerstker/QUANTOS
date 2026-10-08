# 固定原切分点的跨 UTC 日安装恢复

接续[独立安装恢复](2026-10-01-demo-market-partition-recovery.md)，父源码为
`81777bc91d9cac7c3822b70b2a052ccf8babe49d`。该版恢复会再次生成当前日候选并比较
稳定字段；旧账本的日状态可能改变，因此未完成安装在跨日后不能沿原批准恢复。
本增量将形成新候选与复核已经批准的候选分开，原批准、切分点及初始钱包不变。

## 复核旧日事实与新日观察

`revalidate_pre_activation` 先核对整个旧 reservation/wire/OMS/recovery 前缀，
以原 `recorded_at_ms` 调用同一 V2 replayer，验证原 context 摘要。未完成激活
必须没有目标执行历史，整个 state 摘要必须等于封存的旧 state；不能借恢复
过滤或容纳新订单、续写 wire 或改变旧记录。源码、旧 registry、已过期 key
与原停机摘要均须匹配。

新 BTCUSDC 与 XAUUSDT 观察必须同账户、同捕获来源且按顺序完成，均属于当前
UTC 日。沿原 `assess` 和 transition inventory 核验全账户空仓、各 collateral
自由平坦、两观察一致且等于封存 inventory。USDC 当前钱包必须等于原账本
closing wallet，USDT 必须等于原独立封存金额；没有币种折算、新基线或日风险
重设。旧日 loss、peak、capacity 等事实由原 context 摘要完整保留。

## 原受保护 worker 与安装工具

原 `context` 操作新增明确的 `retained_market_partition` payload 字段，仍只读
固定 hash 路径下的两份执行侧观察。worker 持原 preparation lease、读取原
quiescent stop 字节，并在完成后复查 stores、stop、registry 和观察原件未变。
原新候选操作保持。请求/响应继续受原 65536 字节管道限制，不接受外部路径、
手填现金或新批准。

原 root 操作员入口取得新观察并调用同一 Runtime/worker，核对 source/cycle、
候选、旧 context/state、registry、stop、inventory 和两份实际捕获摘要。
安装后的复核改为固定原意图，不再调用生成候选入口。新确认时间不得早于本次
新观察复核；确认前后以及镜像写出后均重新核验。

每份实际新观察报告在原 stage 的 `pre-activation-revalidations/<摘要>.json`
排他留存；既有同摘要文件须原字节读回。原 V2 intent、原复核、ready result、
issuer/executor 镜像、失败记录均不改写，activation 仍是最后持久提交。

报告的截止时间取原 observed/book/mark 的 30 秒界限和 nextFundingTime 的
最早值。controller 在收到报告及最后激活前都验证这条原截止时间；不把完成
复核的时间当成新的行情有效期。ready 落盘后超时，保留 UNKNOWN、两镜像和
审计，禁止创建 activation。

## 验证与限制

纯复核新增15项先缺接口失败；初次实现遗漏验证器导入，15项失败日志保留，
补齐后15通过。原恢复新增7项先复现跨日拒绝/未消费新报告的失败。首轮联合
111通过/1失败：旧发布后变化用例仍修改重新生成的候选，已改成两镜像出现后
新报告返回改变的 stop 摘要，原 UNKNOWN 断言保留并通过。

真实本地子进程管道初次失败是隔离夹具未提供原 worker area，原日志保留；
补上夹具目录后原 Runtime framing、SQLite、retained 原件和 V2 重建贯通。
身份、已接受源码、root pipe/属主检查仍是虚拟夹具，没有实际 sudo、账户凭据、
HTTP、订单或保护目录操作。没有将夹具等同现场来源认证。

最终九组 **164项通过，138.83秒**，覆盖原分区、运行绑定、首装/恢复、当前报告
损坏、原 worker/操作员/functional 路径和最后提交前超时。没有真实跨 UTC 日
账户采集、实际24小时运行或完整版本 canonical 通过证据。

父链66303795完整门已终结失败：执行侧6274通过/1失败/2跳过，失败的旧
操作员联调夹具没有隔离新增分区路径，读取现场 `/etc` 遇到权限拒绝。该失败
在本副本独立复现；现在三个分区路径均指向临时目录，仍调用真实 resolver、
风险门、签发和执行会话。补充不可读分区及缺本地镜像的 activation 场景，
验证构造请求即拒绝且无预留、签发库或发送材料。操作员与分区运行两组
**48项通过，47.27秒**，与上述164重叠，不相加；生产权限门没有放宽。

同次完整门四项前端失败为新clone未安装锁定依赖，分别缺 tsc/eslint/vitest。
本副本已执行原 `npm ci --offline`，未改依赖或 allowScripts；esbuild 未登记
安装脚本的提示保留。最新固定版本的完整验收须显式完成这项准备，且继续
运行原四项前端门，不能以安装成功代替门通过。旧失败及其报告不改写。

V1 缺材料和物理截断仍拒绝，未经审查的跨源码版本迁移也不在此恢复中自动
处理。停机链来源认证、独立黄金登记/shared-budget批准、监控未知事件、
最终版完整验收、现场安装/升级/回滚、实际成交/现金/再研究和多资产验收
仍待完成。所有报告仍 source_verified/kill_switch_verified/经济权威 false，
不能成为订单权限或经济资格。
