# QUANTOS 实盘就绪审查 + 小资金实验开发计划

> **生成:** 2026-07-04。**触发:** 用户明确指令——把系统从 `L2_DRY_RUN_ONLY` 推进到
> **真实交易所、小资金实验**(L3 路径),按顶级 AI 量化机构的实盘标准审查缺口并开发补齐。
> **审查方法:** 实测(非念文档)——`scripts/validate.py` 全绿(1412 测试 + 治理扫描),
> 逐一核验接缝代码(`broker_adapter.py`/`approval.py`/`signing.py`)、数据管道(`tools/` 抓取器)、
> paper_track.db / dry_run.db 实际内容。
> **性质:** 这是 blueprint 从第一天就预留的那条 **Phase 3A 人工门控通道**的启动文件,
> 不是对安全边界的破坏——见 §4 红线处理。

---

## 1. 一句话总判

**研究侧已达标,交易侧为零。** 作为研究/验证平台,QUANTOS 的方法学诚实(CPCV/DSR/无窥视 OOS/
现实主义引擎)接近顶配;但按顶级机构「让真钱上线」的标准衡量,**七大实盘必备件里有五件完全缺失、
两件只有接缝**。最长的瓶颈不是代码——是**前向证据**(顶级机构不会让一条只有历史回测的边上真钱,
不管回测多诚实)。

**当前唯一可部署的边:** `crowd_contra`(拥挤持仓反向 OI 因子,14 大币永续,周度调仓,
vol-target 年化夏普 ~1.38 / 回撤 ~12% / IC t 值 4.32,过 4/5 项证伪 + 干跑执行现实检验)。
周度调仓 + 大币永续 + 小资金,意味着实盘技术栈可以很薄(不需要 tick/LOB/低延迟),这是可行性的关键。

---

## 2. 对标顶级机构实盘标准:七大件逐项审查

| # | 实盘必备件 | 顶级标准 | QUANTOS 现状(实测) | 缺口等级 |
|---|---|---|---|:--:|
| 1 | **前向跟踪记录** | 数周到数月的 paper/incubation 前向证据,live-vs-paper 偏差持续度量 | **缺失**。`paper_track.db` 里 12 条全是老合成时代小时因子(`h_zscore_3` 等,合成纪元时间戳);`crowd_contra` 从未入前向跟踪 | **P0·最长瓶颈** |
| 2 | **生产级数据管道** | 定时摄入、新鲜度监控、失败告警、断点续传 | 抓取器齐全但全是**一次性研究工具**(`binance_funding_fetch`/`binance_oi_metrics_fetch`/`binance_klines_fetch`),无调度、无新鲜度检查、无告警 | **P0** |
| 3 | **真实执行栈** | 订单管理(幂等/重试/部分成交/最小名义额与步长过滤)、成交回报对账、仓位跟踪 | **结构性拒绝的空壳**(`LiveBrokerAdapter` 构造即 raise——这是 L2 下的正确设计,但意味着从零建)。乾跑撮合引擎的订单/回报契约(`DryRunCommand`/`NormalizedReport`)可直接复用 | **P0** |
| 4 | **密钥管理与进程隔离** | 密钥不进代码库、最小权限(只交易、禁提币)、IP 白名单、执行进程与研究进程隔离(Phase 14 三信任域) | 设计文档完备(`PHASE_14_PROCESS_ISOLATION.md`),**实现为零**(`NullSigningAuthority` 有意拒签)。repo 已有密钥纪律约定(env / gitignored `key/`) | **P0** |
| 5 | **实时风控与 kill-switch 接线** | 实盘仓位/回撤/敞口监控,越限自动 flatten 或熔断,预交易门在**每笔实盘订单**前执行 | 预交易门 + 闩锁 kill-switch **真实且有测试**,但只接了乾跑流水线;无实盘仓位监控、无告警通道(无 Telegram/邮件/任何 push) | **P0** |
| 6 | **运维与对账** | 调度器、监控面板、事故台账、每日对账(交易所仓位 vs 本地账本)、runbook | 审计链/事故台账/健康检查**已建有测试**;调度器、实盘对账、告警、runbook 缺失 | **P1** |
| 7 | **治理落地** | Go/No-Go 证据评审、多方批准、书面授权与限额 | 状态机真实(`approval.py` 三真人哈希链)、Phase 3A 文档骨架在,但**证据追踪器为空、状态 NO_GO**;多方批准假设 3 个真人(需按实际人数诚实适配) | **P1** |

**顶级机构有、但本实验刻意不需要的**(按规模与频率裁剪,不是落后):tick/LOB 执行、SOR、
colocation、做市、毫秒级风控、监管报送。周度调仓 + 14 个高流动性永续 + 小资金,
市价单/简单限价单足够,执行落差(implementation shortfall)照常度量即可。

---

## 3. 分阶段开发计划(每阶段末尾是一道人工门)

### Stage 0 — 立即启动前向证据钟(纯 L2,零红线触碰)✅ 2026-07-04 完成
最长的等待是日历时间,所以**第一件事是让钟开始走**——已落地并验证:
1. ✅ 真前向 paper-track:`services/research/paper_trading/forward.py`(决策先落库、结果后兑现的
   PIT 台账;无前视 min_gap、覆盖率 fail-closed、决策不可改写;10 测试)。**首条决策已落库:
   decided_ts=2026-07-02(数据时间)、recorded_at=2026-07-04(墙钟),crowd_contra +
   carry_oi_majors(对照)各 14 腿 ±1/14;同公式历史参照(gross+funding,周度单位)
   crowd 3.66(231 周)/carry 3.14(312 周)。首次兑现预计 2026-07-09 数据到达后。**
2. ✅ 数据管道生产化:`tools/live_data_refresh.py`(三源增量刷新,冪等/fail-soft/staleness 三维
   口径——退市币停更不误报;首轮实测 115/115 零失败,prices/funding/oi 新鲜度 1/1/2 天)。
3. ✅ 告警通道:`tools/alert_sink.py`(本地 alerts.jsonl 台账 + 可选 Telegram,密钥只走 env)。
4. 周度驱动:`tools/forward_track_advance.py`(幂等 catch-up;错过周自动补)。
   调度注册(人工步骤,用户执行):
   `schtasks /Create /SC WEEKLY /D MON /TN QUANTOS_ForwardTrack /ST 09:00 /TR "cmd /c cd /d H:\code\QUANTOS && python -m tools.forward_track_advance >> data\cache\forward_track_run.log 2>&1"`

**门 0→1:** 无(纯研究侧)。**8 周证据钟已于 2026-07-02(数据时间)起走,最早 Go/No-Go 评审
≈ 2026-08 下旬(≥8 个已兑现前向周)。**

### Stage 1 — 实盘执行栈(先测试网,结构上碰不到真钱)✅ 代码+测试完成 2026-07-04(真联调未做)
按 Phase 14 三信任域的精神建**独立 `live/` 树**,研究核心 `services/` 一行不改、治理门保持全绿。
用户已拍板批准模式 = **分级授权**(书面 mandate 硬限额内自动执行,越限/新策略/kill-switch 复位
升级人工审查)——不是"人批准每一次调仓",下面按已拍板的模式记录:

1. ✅ `live/exchange/binance_futures.py`:纯 stdlib(urllib + hmac-SHA256 签名)USDT-M 永续 REST 客户端
   ——**绝不引入 ccxt/交易所 SDK**。mainnet 构造在 L2 默认下结构性 `raise SafetyViolation`(与
   `LiveBrokerAdapter` 对称);testnet 不受五个安全 flag 管辖(见 spec §1.1,那治理真钱/真券商)。
2. ✅ `live/execution/{filters,position_ledger,reconciliation}.py`:目标权重 → 订单批(交易所步长/
   最小名义额过滤、冪等 client_order_id)→ append-only 本地仓位账本(SQLite)→ 对账纯函数(只报告
   drift,不自动修正)。
3. ✅ **分级授权,非"每笔人批"**:`live/governance/mandate.py` —— 已获 `PreTradeRiskGate` 批准的订单,
   再按人工签署的 mandate(单笔/总 gross/总 net/逐标的限额)分级:限额内 `AUTO` 机械执行(**AI 仍不
   自主决策**——执行的是人预先书面授权的确定性策略,类似预设止损单),超限/mandate 缺失或过期/
   venue 不匹配/任何评估异常 → `NEEDS_HUMAN_REVIEW`(留存、不提交,fail-closed 到底绝不默认 AUTO)。
4. ✅ `live/pipeline.py::submit_live_batch`:复用既有 `PreTradeRiskGate`+`KillSwitch`+
   `GlobalAuditLog`(零重造风控/审计逻辑)——每笔订单先过门,kill-switch latched 则全批被拒。
5. ✅ `live/` 树的**专属治理测试**(`tests/governance/test_live_execution_boundary.py`):密钥只读自
   env、无提幣/划转字面量、无自动批准路径(mandate fail-closed 冒烟)、订单端点只允许出现在
   `live/exchange/binance_futures.py` 单一文件、`services/` 单向不得 import `live/`。
   **重要:`live/` 故意不进通用治理扫描器**(它的 `FORBIDDEN_APIS` categorically 禁 `place_order`
   ——研究/纸面树里对,但那正是 `live/` 的正当职责),等价保护由这份专属测试给。
6. **未做(不在本次自动执行范围内)**:真实测试网联调(响应字段名依文档假设,未对真响应验证)、
   仓位/回撤实时监控进程 + reduce-only flatten、mandate 文件的具体数字签署、Bybit/Hyperliquid
   具体实现(协议已留口子)、撤单/改单。
共 71 tests(63 单元 + 8 治理边界)全绿,含端到端缩放往返 integration 测试。设计文档:
`docs/superpowers/specs/2026-07-04-live-execution-stack-design.md`。

**门 1→2(Go/No-Go 评审,人工):** 前向 paper-track ≥8 周为正且与回测特征一致(见 Stage 0,证据钟
已起走)+ testnet 真实联调连续 ≥2 次周度调仓全对账(残差≈0)+ 证据包填入 `docs/phase3a/` 追踪器 +
书面签署 mandate(限额+允许清单+签署人)。

### Stage 2 — 真钱小资金(受限 mandate)
1. 真实 API 密钥(**只交易权限、禁提币、IP 白名单**),密钥只进 env / gitignored `key/`。
2. 初始资金 $3k–5k(用户已拍板,14 币 L/S,每腿名义额需舒适高于交易所最小名义额;$2k 以下部分腿会被
   最小额卡掉,组合失真)。杠杆 1x 起步(vol-target 需要时再议)。
3. 硬限额写死在实盘配置:单日最大亏损、总回撤 kill-switch 线、单腿最大名义额、总敞口上限。
4. 每周:人批准调仓;每日:自动对账(交易所仓位 vs 本地账本)+ 心跳告警;
   持续度量 **live vs paper 偏差**(这是顶级机构衡量执行质量的核心指标)。

### Stage 3 — 实验评估(8–12 周后)
live-vs-paper 偏差、真实成交成本 vs 假设 10bps、资金费现金流对账、事故台账复盘 →
决定加资金 / 维持 / 退役。

---

## 4. 红线的诚实处理(修订什么、绝不动什么)

**绝不动(永久红线,继续机器强制):**
- 研究核心 `services/`+`quantos/` 零券商 SDK、零网络 import、五个安全 flag 代码默认值永不改;
- 密钥永不进代码库/日志/文档;
- **AI 永不自主下单**——实盘架构以「人批准每个订单批」实现,比 flag 更强;
- 永不藏/删失败测试、永不弱化风险门、永不伪造数字。

**经人工批准修订(本文件即决策记录的起点):**
- 「永不实盘」在 ROADMAP §5 的绝对表述,按 blueprint 原意收敛为「实盘唯经 Phase 3A 人工门」
  ——本计划就是走这道门,不是绕过;
- 治理扫描器**扩展**(不是弱化)以覆盖新 `live/` 树:那里允许订单端点存在,但受自己更严的规则
  (密钥来源、无提币、无自动批准、单一文件边界);
- 多方批准的「3 个不同真人」按实际人数诚实适配(独立操作者:1 人 + 24h 冷静期二次确认;
  有合伙人则维持多人)。

---

## 5. 用户已拍板的决策(2026-07-04)

1. **交易所**:Binance + Bybit + Hyperliquid,Binance 先行(数据/信号/成本假设全部基于 Binance
   fapi,有 testnet;venue 可插拔,`live/exchange/base.py::VenueAdapter` 协议已留口子)。需用户完成
   开户/KYC/期货权限,API 密钥由用户创建并只给只交易权限。
2. **批准模式**:**分级授权**——书面 mandate 硬限额内自动执行,越限/新策略/kill-switch 复位升级
   人工审查(`live/governance/mandate.py`,已实现)。
3. **真钱前的前向证据门**:**≥8 周**(机构标准)。Stage 0 证据钟已于 2026-07-02(数据时间)起走,
   最早 Go/No-Go ≈ 2026-08 下旬;Stage 1/2 开发照常并行,这个门只影响真钱开闸时间。
4. **实验资金量级**:**$3k–5k**(推荐起步),杠杆 1x 起步。

---
*本文档随各阶段推进更新;每道门的通过证据记录在 `docs/phase3a/` 追踪器。*
