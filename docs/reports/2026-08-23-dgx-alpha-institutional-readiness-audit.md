# DGX 部署、证据与 Alpha 机构就绪审计（2026-08-23）

## 结论

当前系统是 **L2_DRY_RUN_ONLY**，不是机构实盘盈利就绪。主网、真实券商、订单提交、
AI 直接交易仍被结构性关闭，且本次整改不改变这些边界。

截至审计时，唯一 Alpha 生产工作流共有 48 个活动候选，执行 paper 合格数为 0：

- HISTORICAL_OOS：10 FAIL，38 NOT_AVAILABLE；
- SIGNAL_SHADOW：43 RUNNING，5 NOT_AVAILABLE；
- RESEARCH_RISK：37 PENDING，11 NOT_AVAILABLE；
- EXECUTION_PAPER：48 NOT_AVAILABLE；
- LIFECYCLE：9 RUNNING，1 BLOCKED，38 NOT_AVAILABLE。

所以“做到机构实盘盈利级别”当前只能作为毕业目标，不能作为已达到的事实或收益承诺。
正确路径是先恢复可重复的生产证据链，再让 OOS、盲化前向、研究风控、执行 paper 和人工
资本门逐层给出结论。

## DGX 部署实况

- 平台：NVIDIA DGX Spark，Ubuntu 24.04，ARM64，约 121 GiB 统一内存；
- QUANTOS 发布目录为只读 commit 绑定发布，状态位于 `/srv/quantos/state`；
- console API、前端和公共流服务可启动，五个非协商 L2 安全开关保持正确；
- 重启后最初 6 个 QUANTOS 单元失败。公共 Binance/Bybit 接口经本机代理验证可达，
  其中 4 个网络 worker 随后自动恢复；根因是 QUANTOS 早于用户态代理监听器启动，
  不是交易所中断；
- `paper_multivenue_sor` 仍因可信游标链全审未完成而 fail-closed；
- 共享主机上的 vLLM/Aetos 容器不属于本次 QUANTOS 变更范围，未被重启或修改。

## 已确认的生产故障

### 1. 游标链每次重启从创世点扫描

`binance_realtime_stream.db` 约 53 GiB。旧流服务每次重启都以 cursor checkpoint `0/""`
启动隔离子进程全审，单次持续数十分钟，并在审计期间阻塞多场所 paper SOR。

整改：由独立 feature batch 哈希链持久化可信游标锚点；日常重启只验证锚点后的后缀；
首次和过期仍从创世点全审。常驻流在 24 小时证明到期前主动启动周期全审，并由 feature
worker 对全审状态、精确数据库锚点和新增后缀进行复核后再写入独立权威。

### 2. Feature batch 身份未绑定完整证明

旧 `batch_id` 只绑定 source digest 和版本，但同一 source digest 的周期全审会产生新的
`full_verified_at_ms`。结果是 worker 扫描 23–35 分钟后，以
`batch_id reused with different content` 失败，定时器随后重新从头扫描。

整改：batch v3 改为对“除 batch_id 外的完整 canonical payload”做内容寻址。同内容重跑
仍幂等；同数据但新的全审证明生成新的合法批次，杜绝身份碰撞。

### 3. 实时 Alpha 的持久 OOS 证据未被生产工作流消费

`realtime_alpha_registry.db` 已有 40 个候选、9,756 条防篡改 EVALUATED 事件，但生产工作流
只读取当前一轮 `realtime_alpha_discovery.json`。候选滚出临时快照后，历史门错误退化为
NOT_AVAILABLE。

整改：家族注册表新增同一 SQLite read snapshot 下的身份+评估冻结视图；生产工作流在当前
快照缺行时，从该哈希链权威回放最新评估，并把 authority hash、event seq、record hash
绑定进 gate content hash。该回放不会把探索评估升级成正式 PASS：DGX 当前 38 个最新评估
均为 EXPLORATORY_ONLY，因此修复后的诚实状态是 PENDING，而非 PASS。

### 4. 未来时间戳异常帧会终止常驻流并取消全审

DGX 首次运行修复后的全量游标审计超过 39 分钟后，Binance WebSocket 出现一条超过
30 秒硬上限的未来事件时间帧。旧路径先把帧写进内存快照或交给游标存储，随后才在快照
构造阶段报错；异常逸出常驻 flush loop，进程退出，并取消尚未完成的全量哈希链审计。
主机 NTP 与 Binance 公共服务器时间差约 0.8 秒，因此不是 DGX 时钟失步。

第一轮整改只校验外层 `E`。现场复验进一步发现，bookTicker 在 `E` 缺失时会合法回退到
交易时间 `T`，该有效时间仍可绕过前置门；同时 `asyncio.to_thread(subprocess.run)` 在
取消后不能终止子进程，主服务会一直等待全审线程到超时，状态文件因此停止刷新而
systemd 仍显示 active。

最终整改：在 `StreamState.handle` 的任何内存、计数器或持久游标变更之前，统一校验帧的
有效事件时间（常规 `E`，bookTicker 缺失时为 `T`）；超过既有 30 秒上限的帧由消费循环
隔离并计入拒绝数。快照构造阶段的二次防御保持不变，阈值没有放宽。全审改为原生
asyncio 子进程，取消或超时时必须 kill + reap；一次性创世全审按现场超过 60 分钟的规模
把硬上限调整为 90 分钟，正常重启仍只验可信 checkpoint 后缀。回归测试锁定异常帧不得
改变 latest、event count、last received 或游标事件链，并锁定取消必须回收审计子进程。

## 收益与证据判断

- 旧 100 万 paper 轨：carry 约 -3.28%，crowd_contra 约 -4.90%，top_contra 约 +4.48%；
- 1 万隔离 paper 同方向，但 `paper_ready=false`；
- 三个 canonical 静态 Alpha 的正式历史评估全部 FAIL，DSR 远低于 0.60，且有效试验数为
  2,717；
- 正式前向样本只有 2–7 期。crowd_contra 的 3 期累计收益为正，但样本过小，不能据此
  推断可持续盈利；
- 历史 backtest、工程 shadow、旧 paper 和正式 promotion evidence 是不同证据层，禁止
  混成一个“已盈利”结论。

## 机构级毕业条件

保持 L2，直到同时满足：

1. DGX 调度、备份、外部心跳、事件 seal、artifact admission seal 连续稳定；
2. 候选身份、试验分母、OOS、PBO/DSR、泄漏、regime、容量和成本证据可重放；
3. 盲化前向达到预注册的独立观察数和 regime 覆盖，不按收益提前揭盲；
4. 研究风控九引擎与组合/执行承重证据 READY；
5. 正式 execution paper 经过真实盘口、TCA、滑点、容量、对账和衰减监控；
6. 冠军/挑战者只能按冻结多指标规则替换，不能只看最终净 PnL；
7. 任何真钱阶段仍需独立人工身份、多方批准、mandate、kill-switch 和小额毕业流程。

本报告不构成投资建议，也不承诺未来盈利。
