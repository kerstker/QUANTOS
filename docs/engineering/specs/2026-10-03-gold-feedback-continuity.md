# 黄金持续反馈、原现金期初与终端观察

状态：2026-10-03 47 路径（含治理）的组合验证已结束，2,519 通过、13 失败，
耗时 780.22 秒，全部 Python 源码前后摘要一致。12 项治理失败来自未提交源码
的证据绑定拒绝及旧 Phase13 产物；1 项旧容量夹具未接新账户期初，在首轮进入
固定 registry 读取后拒绝。容量夹具现已接回原生账户期初，四组相关回归
51 通过 / 57.17 秒；原组合后仅这一 Python 测试文件改变。原失败日志保留，
不能把局部修正追记成原组合通过。当前准备冻结提交，自身完整验收仍待执行。
独立源码目录为 `/home/kerstker/quantos-dev-output/gold-feedback-retry-20261003-er2zswb6/source`，
父提交 `13209c1aef9d97318e320cca417e1ba712719cff`。
沿用[完整工厂交付范围](../plans/2026-09-28-ai-factory-closed-loop.md)和
[研究优先决定](2026-09-20-dgx-research-first.md)。策略研发单列。

## 实际缺口

持续 bar 控制器曾在每个 tick 重新启动普通观察。已完成现金反馈若发布暂时失败，
重启不能直接找到原完成结果并继续原回执；简单再观察可能产生同一周期的第二条
SETTLED。正常交付后已轮转的周期，到期时又可能先进入新行情和期初采集，尚未
执行原停止观察就被原窗口门拒绝。

真实 AUTO 快速成交还暴露了现金期初错误：旧 reported path 使用签前账户观察
作为期初，实际入场回执仅晚 13ms，无法满足原账本要求的 5000ms 安全间隔。
修复保留原现金算法和回执时间，在公共信号之前捕获并留存完整账户期初。

另一个出口是本次未交易但保留旧 BTC 分区历史：原周期已正确报告停止和
空仓，supervisor 却将全账户非空历史作为本次资金反馈义务。该失败已用原分区、
原四执行状态和原登记复现；不能通过删除旧历史或泛化为“空仓即完成”修复。

## 已实现的开发路径

- 原 controller/executor 双根留存按完整原窗口、源码、账户、runtime、claim、
  决策和回执发现当前或上一周期的唯一完成结果。原普通半帧、歧义、失配及截断
  继续拒绝；已轮转周期必须有原 claim，不能用重建的自洽空库重置历史。
- 一个有限 pilot invocation 共用延迟初始化的 feedback owner。发布前释放留存锁，
  发布后重读原件，只向同一 claim 附加已核验回执。已完成的上一周期只读确认。
- 原固定子进程只有在完成输入核验后发生明确的目标存储错误，才输出严格绑定的
  retryable receipt。未知异常、超时、损坏原件和错身份不伪装成可重试成功。
  immutable 发布使用不覆盖目标的 rename；未知临时文件保留。
- 新预信号期初有原目录下的持久 intent、完整账户捕获引用和原状态/分区/登记绑定。
  先等原 5000ms 间隔，再开始公共信号；退出后的 closing 也遵守原现金间隔。
  缺失或中断不补造期初，不改回执，不续期或新增额度。
- 新 reported artifact 使用 V2，增加原 opening 与 runtime 摘要；发布器必须核对
  外部 cycle。旧 V1 字段与摘要保持。研究消费者仅保留公件技术上下文，不能据此
  声称独立验证了私有账户来源或具有经济准入资格。
- rotation 在持久分配前后核验原窗口与连续时钟；遇到期不创建下一周期许可，
  已消耗的分配保留。controller 已接专用 terminal observer 的路由和严格完成状态，
  原 ACTIVE 决策保持原字节。专用 executor 的 CURRENT/PREVIOUS 到期收尾已通过
  原生组合；唯一原 closure intent 和新非 SETTLED 观察承担收尾，不另开策略。
  已识别 closure intent 丢失可能造成二次收尾的失败，修复及原外部反例复测通过。
- 已交付现金结果再经过无信号轮转后，终端发现仍读取完整原 rotation 中最后一份
  SETTLED，再核双根原 claim/result/delivery。当前待发布结果优先；历史结果不重发，
  ACTIVE 周期继续普通观察，仅实际到期或停止进入专用收尾。
- 无成交收尾使用独立请求、原登记窗口和完整执行历史核验。旧 BTC 分区保留，
  不伪称全账户历史为空，不生成现金反馈。只有已核验的 IDLE 原件可完成；两层
  控制端分别复核。执行侧确认本次执行状态发生变化时，返回精确绑定的只读
  NOT_APPLICABLE，才继续原退出观察；未知、损坏或错身份结果不会进入该分支。
- 已持久 STOP 且与同 claim 的 origin 精确匹配时，可按原停止的真实时点复核
  30 秒新鲜门并补齐原 decision/result，不追加 GET、claim、时间或第二条 SETTLED。
  这不覆盖尚未写入 STOP 的过期 origin。
- 同一 feedback owner 在首次 worker 建立留存、或完整结果落盘后丢失回包时，
  冷审实际 executor 历史，再沿原镜像机制补齐 controller 已有原结果；不新增执行
  claim。双根历史丢失而固定 closure 痕迹仍在时拒绝伪装首轮。

## 当前验证及其边界

所有以下日志位于独立源码上一级目录。它们是局部证据，不能合并计数成一次
最终同版全量验收；各阶段源文件及 fixture 边界由相应 handoff 记录。

| 证据 | 实际结果 |
|---|---|
| `a-native-feedback/final-handoff.json` | 12 文件阶段冻结，14 组 101 PASS；真实 AUTO 原签发/风险/回执/OMS/现金/第三帧留存 |
| `v2-consumer-handoff/handoff.json` | 3 文件阶段冻结，7 组 123 PASS；实际独立 CLI、原生 AUTO artifact 与研究消费 |
| `b-readers/handoff.json` | reader 阶段冻结，101 PASS；唯一已轮转 claim 缺失的真红已修；不修改原 store |
| `legacy-handoff/handoff-final.json` | 原 legacy 发现与兼容 101 PASS；controller 原 result 与 executor 原 decision 的不同来源保持 |
| `root-native-auto-second.log` | 1 PASS / 10.24s；原现金结果→实际发布子进程 ENOSPC→同 claim 成功→单次轮转→冷 owner 不重复发布 |
| `root-closure-status-green.log` | 55 PASS / 4.51s；CURRENT/PREVIOUS 路由、原窗口和完成状态；专用 worker 在这些路由测试中明确隔离 |
| `root-native-closure-first.log` | 2 PASS / 15.72s；原 AUTO 现金→controller→真实 fork/私管道→原 worker/账户/停止/留存→tick/supervisor；当前及上一周期，两次调用始终 4 帧、唯一 SETTLED |
| `root-legacy-closure-first.log` | 84 PASS / 1.41s；原 legacy 读者仅豁免经过专用 proof 核验的一个收尾尾记录；这些 reader 用例明确隔离 closure proof，真实 nonBAR 组合待补 |
| `a-native-feedback/exit-audit/idle-helper-handoff.json` | 无成交纯核验 helper 阶段与相关原登记/恢复共 78 PASS；后续完整管道证据另列 |
| `root-terminal-native-first-green.log` | 4 PASS / 21.72s；CURRENT、PREVIOUS、原 NO_ENTRY 后再次轮转的历史结果、真实 nonBAR 原现金，均经 controller→私管道→原 worker；冷重启不追加 claim |
| `closure-handoff/final-handoff.json` | 执行侧三文件冻结；53 PASS / 162.68s；SETTLED、legacy、idle、原 STOP 历史恢复、期限与 NOT_APPLICABLE 边界 |
| `root-control-joint.log` | 15 测试文件 256 PASS / 40.78s；含 empty、旧 BTC、legacy 无成交私管道与冷重启；执行侧/reader 分阶段更新中，不能称最终同版全门 |
| `root-idle-handoff.log` | 9 PASS；精确 NOT_APPLICABLE 的来源、周期、请求、窗口、原因和严格 JSON 类型；传输为明确隔离边界 |
| `b-readers/terminal-idle-handoff.json` | reader 两文件冻结；128 PASS / 128.97s，同 owner 完整结果/首次 claim 恢复与双根丢失拒绝；六份源码依赖前后摘要一致 |
| `root-final-joint.log` / `root-final-joint-exit.json` | 47 路径实际终态：2,519 PASS / 13 FAIL / 780.22s；全部 Python 源码前后一致。12 项治理证据失败和 1 项容量夹具失败全部保留；不是 canonical 验收 |
| `a-native-feedback/capacity-opening-compatibility/native-final-joint.log` | 修正后四组 51 PASS / 57.17s；96 次原生期初与只读重开，原登记和执行状态不变；仅账户传输/宿主身份、行情及原长现金窗口为隔离边界 |

原红日志与失败记录全部保留。账户/网络、OS 身份、源码接受和操作员仍有明确的
合成边界；上述原生算法和真实子进程验证不等于现场账户运行。

最终 50 个变更 Python 文件 E9/F821/F811 静态检查通过，服务图检查通过。
治理失败不得删除或降级：源码冻结后由原 ARM64 → canonical ALL 流程生成并
核验该提交的供应链、Phase13 和评级证据。未通过该流程前，没有本批完整 PASS。

## 容量与时效

8,730 帧十秒采样样本通过原逐帧 durable API 写入双根，实际写入约 1054 秒；
87,300 帧一秒样本为离线批量构造的完整原生格式和 audit 历史，只证明 reader
负载能力，不证明一天的逐帧写入吞吐。最终 reader a71c83e6 对原数据只读复测：
两者冷开约 5.204s / 51.296s，热发现约 68.76ms / 645.53ms，指定结果读取约
8.99ms / 9.32ms（2 PASS / 58.00s）。它们是单 runtime、无 closure intent 的
负载，不覆盖最坏 98 份大 rotation 或 pending closure 完整 prefix 的额外成本。
操作系统页缓存未清空；不把这些数字当作完整五秒订单链验收。

原账户新鲜门为 30 秒。当前 closure 已留存 origin、尚无匹配的持久 STOP 时中断，
若实际重启和冷审超过该期限，会拒绝续做，同一 claim 不重新 GET。这是尚未
关闭的恢复限制。匹配的原 STOP 支持按其真实时间补写，不能代替无 STOP 的恢复。
固定语义时钟下的 fork 测试不证明任意墙钟延迟恢复；原 900 秒收尾预算也不能
替代账户新鲜门。不得把此路径标成完整故障恢复通过。

预信号完整 origin 仍使用原 1000 份/64MiB 共享捕获池，并计入原 observation
存储预算；每 episode 一次，不是每 tick 一次。既有用量和大记录可能提前触发
原上限。原账户采集最多 115 秒，发生在订单时效窗外；未旁路这些预算。

## 仍需完成

1. 无持久 STOP 的过期账户观察恢复，需要独立、有界、保留原失败记录的协议。
   本包不静默重采同 claim，也不把已 STOP 的历史补写当作该问题的解决。
2. 最终组合复核必须同时覆盖已成交与无成交、历史轮转、旧 BTC/legacy、跨 UTC、
   有限期限、原账户与账本不变，以及各写入点和同 owner/冷启动恢复。
3. 最终组合冻结、必要联合检查、实际 hook、自己的 ARM64、canonical ALL 与留存。
4. 原现场安装/升级/恢复、完整五秒链及获准的 24 小时成交—现金—再研究验收，
   独立经济来源和多资产专项。机构异机设施继续延期，不计 PASS。

本增量未部署、切换驻留服务、修改保护运行状态、读取真实凭据或下单。
readiness 仍为 L2_DRY_RUN_ONLY。父首装版本 e6fb746 的 32/32 PASS
不替代父 13209c1a 反馈升级或当前组合的完整验收。
