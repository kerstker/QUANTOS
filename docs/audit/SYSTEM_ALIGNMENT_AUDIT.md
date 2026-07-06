# QUANTOS 系统对标审计(SYSTEM_ALIGNMENT_AUDIT)

> 审计日期:2026-07-06
> 审计员视角:顶级 AI 量化交易系统首席架构审计员 + 交易系统工程负责人
> 方法:15 维度多智能体审计(18 个 agent 逐维深读源码 + 8 组 P0 红队逆向追踪 + 主审第一手核实关键安全锚点),
> 全部结论钉到 `file:line` 代码证据,不信文档、只信源码。
> 配套文件:[`GAP_MATRIX.md`](GAP_MATRIX.md)(评分)、[`RISK_REGISTER.md`](RISK_REGISTER.md)(风险)、
> [`TARGET_ARCHITECTURE.md`](TARGET_ARCHITECTURE.md)、[`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md)、
> [`NEXT_CLAUDE_CODE_PROMPTS.md`](NEXT_CLAUDE_CODE_PROMPTS.md)。

---

## 一、一句话判断

> **当前系统更接近于:一个"半生产级 AI 量化研究平台" + "受控 testnet 执行原型",正走在一条刻意人工门控的通往小资金实盘的路上——它不是普通回测平台,也还不是生产级交易系统,更不是顶级全自动 AI 量化系统,但它的"研究 + 安全治理"半区已达近机构级水准。**

理由:
- **研究 + 安全治理半区(9 个维度)= 近机构级 4 分**:真实的截面因子 DSL 引擎、确定性进化/GP 生成器、多重检验纪律(Deflated Sharpe/PBO/CPCV/BH-BHY-FDR)、fail-closed 证据门、10 态哈希链审计生命周期、约束组合优化器(真 PSD 校验)、23 检查 fail-closed 风控门、闩锁跨进程 kill-switch、多方三人批准状态机——这些**不是 mock,是有 1564 测试保护、确定性可复现、机器强制边界的真实工程**。
- **执行 + 运维基建半区(5 个维度)= 工程化原型 3 分**:live/ 执行栈是真打 Binance testnet / Bybit demo 的良好工程原型(确认轮询/撤单/崩溃恢复/双所),但存在 2 个已披露的订单完整性 P1 缺口(client_order_id 同数量碰撞、place-后-未落库孤儿窗口),且无实时盘中监控、无 Docker/容器化、无 staging/prod 隔离基建、无统一事件总线/事件级回放。
- **零真实资本**:交易所仅 testnet/demo,mainnet 适配器在 L2 构造即 `raise SafetyViolation`;五旗帜 + readiness pin 结构性挡死 LIVE。所有"自动化真钱能力"目前都被挡在真钱之外。

---

## 二、当前系统真实成熟度

### 成熟度雷达(15 维度)

```
维度                              分   成熟度
 1 总体架构                        3   ███░░  BASIC_IMPL(无事件总线/服务注册表/循环依赖)
 2 数据层                          4   ████░  ENGINEERED(PIT 生产级;质量门未强制)
 3 Alpha Factory                   4   ████░  ENGINEERED(全库最成熟;lineage 未接线)
 4 回测引擎                        4   ████░  ENGINEERED(PnL 恒等式;无撮合模拟)
 5 Validation Gate                 4   ████░  ENGINEERED(真门;未盖在货币路径)
 6 组合层                          4   ████░  ENGINEERED(真优化器;不在实盘 sizing)
 7 交易边界                        4   ████░  ENGINEERED(最成熟安全维度)
 8 Pre-Trade Risk Gate             4   ████░  ENGINEERED(真 fail-closed;live 引擎被架空)
 9 OMS/EMS/Gateway                 3   ███░░  BASIC_IMPL(testnet 原型;2 个订单完整性 P1)
10 交易中/后风控                   3   ███░░  BASIC_IMPL(周/小时级;无实时盘中)
11 Kill Switch/降级/恢复           4   ████░  ENGINEERED(闩锁跨进程;human-only 复位)
12 审计/回放/事件溯源              3   ███░░  BASIC_IMPL(哈希链;无事件总线/回放)
13 AI Agent/治理                   4   ████░  ENGINEERED(非人不可达 APPROVED)
14 前端/控制台                     4   ████░  ENGINEERED(真只读;危险按钮经治理 CLI)
15 DevOps/MLOps/安全/测试          3   ███░░  BASIC_IMPL(CI+测试强;无 Docker/基建)
                                  ───
均分 ≈ 3.67
```

### 自动化等级判定

| 子系统 | 当前等级 | 说明 |
|---|---|---|
| 研究 | **L2-L3** | 自动因子生成/进化/回测/验证;晋级人工门(非人结构性到不了 APPROVED) |
| 组合 | **L2** | 优化器/风险平衡真实,但只在研究/干跑行使,不在实盘 sizing |
| 执行 | **L0(实质)** | 仅 testnet/demo 零资本;调度器可自动 --submit 但只作用于沙盒 |
| 风控 | **L3** | 强制自动、fail-closed、闩锁 kill-switch;但 live 部分引擎被占位符架空 |
| 治理 | **L2** | 多方人工批准 + 分级授权;放行/扩权/解锁结构性需真人 |

> **用户目标**:L4 执行自动化 + L3 风控边界 + L2 治理审批。
> **当前差距**:研究/风控/治理已到位或接近;**执行自动化的"真钱"那一档尚未开启**(被 L2 挡住,这是对的),且要开启前必须收口一批 LATENT 缺口 + 落地 Phase 14(进程隔离/出进程签名/真身份认证)。

---

## 三、与"顶级全自动 AI 量化系统"的差距

对标你给的十条终极形态,逐条核对现状:

| 目标形态 | 现状 | 差距 |
|---|---|---|
| 1. 自动数据接入/清洗/质量/特征 | PIT/宇宙/复权/日历/特征存储/泄漏检测生产级 | **质量门 computed-not-enforced;无实时/流式;乱序死检查** |
| 2. 自动 Alpha 生成/变异/组合/压缩/去相关/谱系 | DSL + 生成器 + 去冗余/正交/聚类真实 | **lineage 未接线;富 AlphaAsset 无持久层** |
| 3. 自动回测/OOS/walk-forward/过拟合/多重检验 | 全部真实 + fail-closed + 对抗测试 | **未盖在 live 部署路径;冲击/容量未校准** |
| 4. 自动模拟盘/影子实盘/小资金/渐进上线 | 干跑流水线真实;前向证据钟已起走(8 周) | **无资本分档晋级状态机(Small→Full 靠人改 mandate);影子实盘未建** |
| 5. 自动组合优化/风险预算/降权/退休 | 优化器/风险平衡/退休 sweep 真实 | **不在实盘 sizing 路径;退休无调度器、无自动指标喂入** |
| 6. 自动信号/意图/风控拦截/订单执行 | 干跑 + testnet 全链路真实 | **live 部分风控引擎被架空;2 个订单完整性缺口** |
| 7. 自动盘前/盘中/盘后/PnL 归因/日报 | preflight/parity/TCA/GoNoGo 报告真实 | **无实时盘中监控(周/小时级);无自动日报** |
| 8. 自动异常检测/降级/停机/KillSwitch/恢复 | 闩锁 kill-switch + 紧急减仓 + 哨兵真实 | **恢复流程半自动;降级依赖人工带证据** |
| 9. 全链路审计/事件溯源/Replay/可解释 | 哈希链审计 + 不可变实验制品 + 决策解释 | **无统一事件总线;无 correlation/causation ID;无事件级回放** |
| 10. AI 自动研究但不绕过风控/审计/治理 | 非人结构性到不了 APPROVED/reset/live | **Phase 14 前是 build-time 保证(受信代码可绕);promotion_api runtime 面** |

**核心结论**:差距不是"能力造假",而是**三类真实差距**——(1) 已建已测的 engine 未接线到承重路径(验证门、组合优化器、firm 门、维护调度器);(2) live 执行路径的部分引擎被占位符架空 + 2 个订单完整性缺口;(3) 生产运维基建(事件总线、容器化、实时监控、Phase 14 信任边界)整体缺失。**每一类都可工程化收口,且大多是"让已有原语承重"而非从零造。**

---

## 四、每个模块的证据(第一手核实的安全锚点)

主审亲自打开并逐行核实了 5 个最关键的安全锚点(不假手于 agent),结论与 8 组红队一致:

1. **SafetyConfig**(`quantos/contracts/config.py:17-46`):5 旗帜全安全默认,`live_trading_armed` 需全部启用且非 AI,`assert_research_safe()` fail-closed。生产代码无任何处构造 armed 配置(仅测试)。
2. **Pre-Trade Gate**(`services/risk/pre_trade/gate.py:56-92`):真 fail-closed——构造即 `assert_research_safe()`;绑定权威 kill-switch/safety(调用方无法篡改,`:59`);每检查异常→`UNKNOWN_RISK_ERROR`(`:66`);只放行 APPROVED/REJECTED(`:81`);审计写失败→拒绝(`:87`);外层 try/except 保证出错**永不** APPROVED(`:91`)。
3. **KillSwitch**(`services/risk/pre_trade/kill_switch.py:41-104`):`is_active()` 损坏状态 fail-closed 视为触发(`:41`);arm 任何 actor 可(`:51`);reset 仅 HUMAN + 绑定事件的批准/三方 grant(`:69`/`:89`);永不自动复位。跨进程闩锁 `live/governance/kill_switch_store.py:39`(只加不减写盘防竞态)。
4. **下单唯一出口**:`place_order` 全库仅 3 个非测试调用点——`live/pipeline.py:349`(主路径,gate→mandate→仅 AUTO→`get_order` 确认终态)、`live/execution/emergency.py:152`(紧急减仓,`validate_flatten_order` 结构性保证只减敞口 + 强制 `reduce_only`)、`tools/live_testnet_smoke.py`(冒烟)。
5. **紧急通道只减敞口**(`live/execution/emergency.py:47-61`):纯函数保证数量>0、有持仓、方向必与持仓相反、数量≤|持仓|(可减到零、**绝不穿越零点反向开仓**)+ `reduce_only=True`(交易所侧第二重)。

红队对你列的 15 个 P0 问题逐条裁定(详见 [`RISK_REGISTER.md`](RISK_REGISTER.md) 与工作流原始记录):

| P0 问题 | 裁定 | 关键证据 |
|---|---|---|
| Q1 策略绕过风控直接下单 | **MITIGATED** | 仅 2 个 place_order 点,主路径只对 gate-APPROVED∧mandate-AUTO 提交 |
| Q3 无 kill-switch 执行路径 | **MITIGATED** | kill-switch 是 CHECK_REGISTRY #1,横切每笔;紧急通道只减敞口 |
| Q4 风控 fail-open | **MITIGATED** | 5 条评估链全 fail-closed DENY;mandate NaN 绕过已修 |
| Q5 重复下单 | **LATENT/P1** | 同数量 client_order_id 碰撞静默吞记账(P1-3) |
| Q6 订单不可追踪 | **LATENT/P1** | place-后-未落库孤儿窗口(P1-4) |
| Q7 账实不一致 | **MITIGATED** | 不信 ACK、轮询终态、部分成交如实记账、崩溃恢复增量 |
| Q8 实/模混淆 | **MITIGATED** | 三层隔离;残留 testnet vs demo 同所无法区分(LATENT) |
| Q9 mock 误用 | **MITIGATED** | 合成数据与下单路径结构性隔离,console 只读 |
| Q10 密钥泄露 | **MITIGATED** | key/ 三重 gitignore 从未入历史;无硬编码;无日志泄露;URL 脱敏 |
| Q11 断线状态错误 | **MITIGATED** | 回查权威态;残留孤儿订单下周期 preflight 抓(LATENT) |
| Q12 回测不可复现 | **MITIGATED** | 无 RNG、墙钟只在边界、trial-count 持久化 |
| Q13 前视/泄漏 | **MITIGATED** | 双时态契约 + PIT 读 + 泄漏探测 + embargo/purge + 先决策后兑现 |
| Q14 无审批扩权 | **LATENT/P0** | mandate 无签名 JSON + 自动 --submit(P0-1) |
| Q15 kill-switch 无人工复位 | **MITIGATED** | human-only + 多方三人 grant |
| Q2 AI 改生产 | **LATENT/P1** | 直接路径挡死;残留 promotion_api runtime 面 + mandate 可改 |

---

## 五、关键风险(最先报告)

**当前(L2)不存在可被立即利用的 ACTIVE P0。** 最重要的两条 P0 均为 LATENT——被 L2 pin + 五旗帜 + testnet 零资本 + Phase 3A 人工门挡住,**放行真钱当天集体转正**:

- **P0-1 · mandate 完整性**:无签名 JSON,任何有 `key/` 写权限的进程(含 AI agent)可改限额;常驻自动 `--submit` 调度按新限额自动扩仓。放行前必须升级为密码学保护(签名 + Phase 14 出进程验证)。
- **P0-2 · 终极信任边界未实现**:单进程内受信代码可绕过所有 runtime chokepoint(直接凑 HUMAN Actor / 直调 place_order),真防御是 Phase 14 进程隔离/出进程签名,当前只有拒绝式 stub。

9 条 P1 全部 LATENT,是"放行前必须收口"的工程清单(验证门作前置、mandate 账户级聚合、订单完整性 2 缺口、live 风控引擎接线、firm 硬门、维护调度器、数据质量强制、promotion_api 认证)。详见 [`RISK_REGISTER.md`](RISK_REGISTER.md)。

---

## 六、是否适合进入下一阶段

**适合继续在 L2 内推进"让已建原语承重"的工程收口 + 准备 Phase 3A 评审材料;不适合现在接真实资本。**

- ✅ **可以做**:收口全部 P1 LATENT 工程缺口(它们大多是 fail-closed 安全加法,只会让系统更严);继续 8 周前向证据钟;准备 Go/No-Go 材料。
- ✅ **可以做**:所有让系统更 fail-closed、更诚实、更严格的加固——这些不触碰人工门,有 1564 测试兜底。
- ⛔ **现在不该做**:arm mainnet、翻任何 LIVE 旗帜、扩大 mandate 到真实数字、开启无人值守真钱 --submit、绕过任何风控——这些结构性需要真人,且代码本身就挡住 AI/非人执行。
- ⛔ **Phase 3A 放行的硬前置**:P0-1(mandate 签名)、P0-2(Phase 14 信任边界)、P1-1~P1-6(执行/风控/组合收口)、≥8 周前向证据、mandate 逐字段人工签署、自动提交路径端到端演练。

**成熟度结论一句话**:这是一个**诚实、安全、研究能力近机构级**的平台,它最大的价值不在"已经能自动赚钱",而在"它把'不能做的事'用代码结构性挡死了"——这让它成为一个**可以安全地继续自动化下去**的底座。下一步是把已建好的 engine 逐个接到承重路径上,而不是造新东西。
