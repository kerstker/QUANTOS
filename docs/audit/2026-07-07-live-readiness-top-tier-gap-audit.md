# QUANTOS 最严审查:对标顶级 AI 量化的全维度差距与实盘路线图

> 审计日期:2026-07-07 · 审计方法:10 维并行对抗审计代理 + 主审综合 + 两分支门禁实测
> 视角:**不再以 L2 研究平台的宽容标准打分——一切按「这套系统要把真钱放上去」评**
> (用户 2026-07-04 决策:经 Phase 3A 人工门,小资金 $3k–5k 加密实盘,Binance 优先;
> 前向证据时钟 2026-07-02 起跑,需 ≥8 周 → 最早 2026-08-27 前后成熟)

---

## 0. 审计基准(主审亲自核实的硬事实)

| 事实 | 证据 |
|---|---|
| 磁盘检出 = `master`,领先 `origin/master` 5 提交(全为研究/文档:Barra-lite spec/plan、risk-parity 更正) | `git status -sb`、`git log` 2026-07-07 实测 |
| 实盘线 = `codex/firm-aggregation-and-followups`,**= master + 152 提交、领先 0 落后**(严格超集);最新提交:reduce-only 豁免、ADV/vol 门激活、拥挤 z-gate(z*=3.0 用户签)、保证金地板 2.0 + 异常告警 15% 哨兵、Phase 3A Go/No-Go 清单 | `git merge-base` = master HEAD;`git rev-list --count` 152/0 |
| 另有未合并 topic 分支:`codex/{barra-lite-diagnostics,brinson-attribution,capital-allocator,crowding-gate,impact-calibration}`、`claude/*` ×3、dependabot ×8 | `git branch -a` |
| master 门禁:**1176 tests 全绿**(governance-scanner PASS + 全套件 PASS) | `python scripts/validate.py` 2026-07-07 实测,退出码 0 |
| codex 分支门禁(全新 worktree 检出):**2377 tests 中 1 失败、7 跳过;governance-scanner PASS;live 执行栈套件 687 全绿** | 同上,worktree 实测,退出码 1 |
| 唯一失败 = `tests/research/test_research_runner.py::test_live_log_captured_from_stdout_and_git_sha`:`_git_sha()` 以纯文件读 `.git/HEAD`(治理禁 subprocess),在 git worktree(`.git` 为文件)、packed-refs、非根 cwd 三种真实场景下**静默返回 None → 研究运行的代码溯源丢失** | `services/research/research_runner/runner.py:20-29` 实读 |

**含义(先行结论,待各维度报告并入后细化):**
1. 「系统是哪棵树」当前答不上来——磁盘上跑的是 master(旧 live/,缺 152 个提交里的最新风险门与修复),而"用户以为的系统"在 codex 分支。变更管理是走向实盘的第一风险,先于任何单点代码缺陷。
2. 代码溯源(code_version)在合法 git 布局下会静默丢——审计链的可复现承诺有一条真实裂缝。

---

## 0.1 【审计过程中发现的活跃故障,已 100% 核实,超出常规发现分级】

**排程任务当前正在按计划失败,不是推演,是过去至少 2 天的真实故障:**

1. `scripts/register_schedules.ps1` 经 `git log --oneline --all -- scripts/register_schedules.ps1` 核实**从未存在于 master 分支**(`git show master:scripts/register_schedules.ps1` → `fatal: path does not exist`),只存在于 codex 实盘分支历史(`f144cd9`/`84264b4`/`a21048c`/`47e7750`/`d75f406` 等提交)。
2. `schtasks /query` 实测:**5 个 QUANTOS 任务已注册且 Enabled**(`QUANTOS_forward_track`、`QUANTOS_live_track_binance`、`QUANTOS_live_track_bybit_demo`、`QUANTOS_ops_api`、`QUANTOS_risk_sentinel`)——与 CLAUDE.md/memory 里"用户尚未注册"的认知**不符**,已过期。
3. 每个任务的 `Task To Run` 都是 `cmd /c cd /d H:\code\QUANTOS && python -m tools.xxx`——**硬编码磁盘路径,不锁定 git 分支/commit**。
4. `H:\code\QUANTOS`(磁盘当前检出)的 `live/` 目录只剩 4 个子目录残片,`tools/live_*.py` **glob 零匹配、一个文件都不存在**(master 分支从未拥有过这些文件——它们全部是 codex 分支 152 个提交里新增的)。
5. 逐任务 `schtasks /query /v` 实测结果(2026-07-08 现场查证):

   | 任务 | Task To Run | Last Run Time | Last Result |
   |---|---|---|---|
   | `QUANTOS_live_track_binance`(测试网实盘 `--submit`) | `tools.live_track_advance --submit` | 2026-07-06 12:34 | **-2147020576(失败)** |
   | `QUANTOS_forward_track`(8 周证据钟) | `tools.forward_track_advance` | 2026-07-06 12:34 | **-2147020576(失败)** |
   | `QUANTOS_risk_sentinel`(风控哨兵) | `tools.live_risk_sentinel --act` | **2026-07-08 17:01(今天)** | **1(失败)**,18:01 还会再跑再败 |
   | `QUANTOS_ops_api` | `uvicorn tools.live_ops_api:app` | 2026-07-08 11:54(今天) | **1(失败)** |

**因果链(已验证,非推测)**:排程任务在磁盘检出 codex 分支时被注册(彼时代码齐全,大概率成功过,`data/cache/live_*.db`/`live_ledger.binance.db` 等真实历史产物证明确曾正常运行);此后磁盘被切回 master(用于本分支合法的 Barra-lite 研究文档工作,即本会话开场看到的 `e0841a4` 等提交);schtasks 认的是路径不是分支,遂从切回那一刻起**每次调度必然以 ModuleNotFoundError 类错误静默失败**,无任何人被告警。

**后果分级**:
- 客观上是"fail-safe"的失败模式(代码根本跑不起来,不会误发测试网订单)——不是资金风险。
- 但**风控哨兵(risk_sentinel)与 8 周前向证据钟(forward_track)两条安全关键自动化,实际已经空转至少 2 天**,且无监控/告警覆盖这一失败本身(参见前端/平台维度报告核实的"watchdog 未覆盖此故障"发现)。
- 这是本次全部发现里**唯一一条"正在发生、非假设性"的问题**,理应排在所有 P0/P1 分级发现之前处理。

**已修复(2026-07-08,用户拍板后执行,且已现场验证生效)**:排查确认 `codex/firm-aggregation-and-followups` 是 `master` 当前 HEAD 的严格线性后代(经由仓库真正主分支 `main` 共享的 51 个提交,再加 102 个 live/ 相关提交)——`git merge codex/firm-aggregation-and-followups` 在 master 上是**保证无冲突的快进合并**,已执行:`master` HEAD 从 `e0841a4` 快进到 `c6dc7d2`,322 个文件更新。合并后现场核实 `tools/live_*.py` 全部存在、`live/` 树完整。
**精确故障窗口(`git reflog` 逐条核实,修正此前"至少 2 天"的粗估)**:磁盘从 `codex/firm-aggregation-and-followups` 切到 `master` 的精确时刻是 **2026-07-07 20:32:30**;fast-forward 修复落地是 **2026-07-08 17:35:30**——**真实代码缺失窗口 = 21 小时 3 分钟**,不是笼统的"至少 2 天"(2026-07-06 那次失败是窗口开始前的独立小插曲,当时磁盘还在 codex 分支,失败原因待查,不属于本次分支缺失故障)。
**修复已现场验证生效(非预测)**:`QUANTOS_risk_sentinel` 下一次自然触发(2026-07-08 18:01:01)`LastTaskResult=0`(成功),`risk_sentinel_status.json` 被新鲜写入,binance 权益从故障前 \$5013.23 变为 \$5059.72(真实数据变动,证明端到端链路真的重新跑通,不是文件时间戳巧合)。
**catch-up 机制核实**:`forward_track_advance.py` 有显式设计的 catch-up(docstring 原文"每次运行幂等,错过可自动补"),基于"距上次决策已用时长"判断,不按日历日期回填,数据不会永久丢失只是决策时点顺延;下一条决策 due 时间点(≈07-08 20:00)在故障修复(17:35)之后才到期,**这 21 小时窗口对证据钟本身零实质影响**。`live_track_advance.py` 没有等价的 due 门,但本质是"每次都对齐最新信号再平衡",迟跑只是仓位滞后,同样非数据丢失。真正的损失是这 21 小时里 `risk_sentinel`(安全监控)与 `ops_api`(交易台可见性)完全空转——客观 fail-safe(无真钱无误单),但是一次真实监控盲区。
注:合并顺带发现 Barra-lite 的 spec/plan 文档(`4c7a10b`/`e0841a4`,2026-07-01)与已落地实现(`c48e210`,2026-07-02,`services/portfolio/barra_lite.py`)**代码签名不兼容**(代码版 `zscore(dict)`+百分位 winsorize+缺失值静默置 0;计划版 `zscore(Sequence)`+σ-winsorize+缺失值应 raise)。**两个独立子代理分别核查了这一发现,结论不同,以下取证据更严谨的一方为准并说明分歧**:一方仅凭 master 分支指针停留位置推断"两条线互不可见的重复劳动";另一方用 `git merge-base --is-ancestor e0841a4 c48e210` **直接验证 e0841a4 是 c48e210 的唯一父提交**——三个提交 spec→plan→实现是**同一条线性链、同一作者、间隔约 14 小时**,此前"两条独立线"的框架是 master 指针尚未快进到 c48e210 造成的**分支指针滞后错觉**,不是真实开发史;`docs/PROJECT_STATE.md` 里 c48e210 新增的文字包含"58 symbols/5 factors、carry total_var 0.6544"这类只有真跑过才写得出的具体数字,证明实现方确实知道这条工作、是**选择重新组织代码**(独立重写,非抄模板),不是两拨人各干各的。**结论:不是重复劳动,是同一条工作链的连续产出,文档没有回头补一行"已被取代"的指针**——但两个子代理一致同意的危险点仍然成立:两份 07-01 文档里写着可执行指令+完整源码,若未来任何 agent 被指向执行,会用不兼容签名覆盖已上线、已被 `tools/rebuild_snapshots.py` 依赖的实现。建议:在两份文档顶部各加一行"已被 `c48e210`(2026-07-02)以独立实现方式落地,本文档仅存设计动机与未来扩展参考价值"标注;文档里仍开放的条目(console 面板、拥挤门接组合构建等)拆成独立新 spec 跟踪,不要复活这 900 行本身去施工。工作量:XS(几分钟)。

---

## 1. 总判语

**综合评分 ≈2.2/5(9 维度均值,最低 AI 自治 1.5,最高安全 3.0)——这是"要走向实盘"这把最严标尺下的分数,不是"L2 研究平台"那把宽容标尺下的分数(后者项目自己近期审计给到 82/100)。两个分数都不算错,量的是不同的问题。**

三句话总结这次审计:

1. **没有一处是"假装能用"**——审计过程中反复验证:pre-trade gate、mandate 分级授权、kill-switch latch+持久化、幂等下单、DSR/PBO/CPCV 统计纪律、AST 沙箱防注入、PIT 过滤逻辑、诚实标注文化(`UNCALIBRATED_PRINCIPLED`/`SYNTHETIC_DETERMINISTIC` 贯穿全仓库)——这些核心机制经代码级核实**是真的**,不是演示或占位符伪装成完工。这也是为什么全审计只找到 4-5 个真 P0,而不是几十个。
2. **但差距高度集中在同一种模式:"建好了,没打开"**——见 §3。数据质量门、VaR/CVaR/HHI、Barra-lite、死人开关、告警通道、kill-switch 仪表盘读的对象、alpha 晋级流水线——每一个都是"代码正确、测试通过、但从未被真正接上决策路径或从未被启用"。这比"压根没写"更容易被误判为"已经做了",也是本次审计价值最大的一类发现。
3. **卡首笔实盘的主要不是技术能力,是尚未走完的人工签署链+运维盲区**——8 周前向证据钟本身在轨(硬故障已修复并现场验证生效);真正阻塞的是约 20 项必须由用户亲手完成的动作(mandate 重签、验证准入声明、ACL 收紧、告警通道打开、Go/No-Go 最终签字),外加告警/看门狗从未真正启用这个监控盲区本身。

**审计过程本身的一个插曲值得写进结论**:本次审计发现并**当场修复**了一个真实、活跃的生产故障——排程任务因磁盘分支切换静默失败 21 小时(风控哨兵、交易台可见性全程空转),用户拍板后 fast-forward 合并、现场验证恢复。这次事故本身就是 §3 跨维度模式的一个缩影:不是代码错了,是"这套系统现在跑在哪"这件事没有被机器盯住,而负责发现这类问题的死人开关又恰好也没被注册。

## 2. 记分卡(0–5,顶级=5)

| 维度 | 得分 | 判语 |
|---|---|---|
| 数据层 | 2.0/5 | 架构讲究但未落地、已知 bug 未修、零实时运维 |
| 研究/Alpha | 2.0/5 | 统计诚实扎实,回测-执行换算/晋级流水线/搜索吞吐仍是验证概念级 |
| 风控 | 2.5/5 | 熔断门禁扎实,VaR/强平/托管仍是空白孤岛 |
| 执行/实盘栈 | 2.5/5 | 安全闩锁扎实自省诚实,可观测性/执行基建两头缺 |
| 平台/API/基础设施 | 2/5 | 地基扎实,开关未拨:告警、备份、看门狗全离线 |
| 前端/运维可视 | 2/5 | 数据基本是真的,但看错 kill-switch、按不动开关、叫不醒人 |
| 安全 | 3/5 | 护栏扎实,密钥权限校验和目录 ACL 是唯二真敞口 |
| 流程/治理/变更管理 | 2/5 | 骨架诚实扎实,证据钟静默停摆、文件追不上代码 |
| AI/智能自治 | 1.5/5 | 全审计最低分:写代码的 AI 扎实,跑研究的 AI 是玩具级演示 |
| **综合(9 维度均值)** | **≈2.2/5** | 及格线之下,但没有一处是"假装能用";差距集中在"建好没打开" |
| 实盘路径就绪度 | 2.5/5(过程性) | 硬故障已修复验证;卡点是约 20 项未走完的人工签署,非技术能力 |

## 3. 跨维度结构性差距

以下每条都被 **≥2 个互相不知道对方存在的独立审计代理**,从各自的维度分别撞见——这种独立复现是比单一维度深挖更强的信号,说明不是局部代码质量问题,而是系统性模式。

### 3.1 "建好了,没打开"——本次审计最大的单一主题,贯穿全部 9 个维度

| 子系统 | 建好的是什么(真实存在、测试通过) | 没打开的是什么 | 独立发现于 |
|---|---|---|---|
| 数据质量门 | `quality.py::count_out_of_order`/`missing_sessions` 逻辑正确 | 全部 crypto/FX/宏观抓取器 `strict=False`;乱序检测因输入预排序数学上不可能失败 | §4.1 |
| VaR/CVaR/HHI | `research_risk/domain/risk_modules.py` 真实历史分位数计算,非占位符 | `live/`、`pre_trade/`、`execution/` 零消费方;23 项 CHECK_REGISTRY 里没有一项 | §4.4 |
| Barra-lite 因子风险 | 结构化对角协方差+残差方差,诚实标"lite" | 生产驱动器零调用方(除批量重跑编排);集中度 54% 发现不在任何签字清单 | §4.3、§4.4 |
| 死人开关 watchdog | 代码逻辑正确,容忍窗口设计合理(虽然对本次故障量级仍太粗) | 从未被注册进 schtasks;即便注册,依赖被监控模块导致相关性故障 | §4.5、§4.6、§4.7、§4.9 |
| 告警通道 alert_sink | Telegram push 代码正确 | 环境变量从未配置;真实 ESCALATION 事件安静写进本地文件无人看 | §4.7、§4.9 |
| kill-switch 持久化 | `kill_switch_store.py` 跨进程持久化+防竞态,live/ 侧 3 个入口全部正确加载 | console_api 两处安全看板端点构造全新未持久化对象,读的是假数据 | §4.9(与 §4.4/§4.8 的"研究侧默认未闩锁"是同一模式的不同表现) |
| Alpha 晋级生命周期 | 26 模块/185 测试,状态机链路干净 | 服务的是一次性演示面板;真正上生产的 carry/crowd_contra 完全绕开它,是裸 Python 常量 | §4.8 |
| 多方人工批准 | `verify_multi_party_grant` fail-closed 逻辑正确、哈希链自洽 | 单人可自构造 3 个 `Actor` 走完全程;`promotion_api` 自己文档承认"AI agent 可用" | §4.5、§4.8、§4.9 |
| ADV/流动性+波动率门 | fail-closed 判断逻辑正确 | 阈值(100%/500%)宽到数学上不咬合;数据喂入本身直到审计前一天才修复 | §4.1、§4.4(已在报告内互相更正一次) |
| 撮合精度模型 | `live/execution/filters.py` 真实取交易所 step/tick/min-notional | `dry_run_pipeline.py` 零使用,回测 Sharpe 对此是乐观的 | §4.1 |

**这条模式的含义**:如果要用一句话向不看代码的人解释这次审计,应该是——"这套系统的问题不是能力不够,是很多正确的能力从没被真正接上开关"。修复成本因此普遍偏低(多数是"加一行调用"级别),这也是为什么本次审计给出的具体修补工作量以 XS/S 为主。

### 3.2 "自报字符串 ≈ 身份认证"——安全、治理、AI 自治、前端四个维度独立复现同一漏洞形状

`Actor.is_human`(字段比较)、`promotion_api` 的 `X-Operator` 请求头(`.strip()` 后原样铸造)、多方批准的"不同真人"(`actor_id` 字符串去重)、git `Co-Authored-By`(纯文本约定)——全部是"调用方自己说了算"。**唯一结构性做到防伪的是 `signing.py`**(不存在任何 `return True` 路径)。这不是四个独立漏洞,是同一个设计取舍(L2 阶段的"仅防笨蛋、不防蓄意",CLAUDE.md 明确承认)在四个不同接口上的重复体现——根治需要 Phase 14 出进程签名基建,已知非当前目标;但**当前对外措辞在多处暗示比实际更强的保证**("三方独立人工批准"),这一点在放行更大资金或多人协作前必须改口径,成本很低(文档措辞)。

### 3.3 排程/调度是单点故障,且负责发现"排程挂了"的机制本身也依赖排程

本次审计过程中现场撞见并修复的真实故障(§0.1)是这个模式最直接的例证:磁盘分支切换→ 排程任务静默失败 21 小时 → 负责发现这类故障的 watchdog 本身未注册 → 靠人工审计才发现。四个维度(§4.5/§4.6/§4.7/§4.9)独立指出同一根因(schtasks 硬编码磁盘路径不锁分支/commit、零输出重定向)和同一后果(没有自愈、没有告警)。**这类故障的下一次发生大概率不会是"分支切换"这个具体触发器,而是任何导致磁盘代码与预期不符的操作**——根治不是"记住这次教训",是给排程任务加启动时自检(commit 校验+日志重定向+watchdog 独立于被监控模块)。

### 3.4 研究口径与执行口径不是同一把尺子

研究搜索用 3bps 成本模型,干跑执行侧硬编码 8.5-10bps,Almgren-Chriss 冲击模型只接入 1 个驱动器;"carry gross 1.44→净 VT 0.65"这类反复引用的"衰减"数字不可被任何命令复现(管线外手算);PIT `knowledge_time` 对批量历史数据是 `event_time` 字节级复制(声称的"何时可知"保护对回填数据不生效);live 执行链路零手续费/资金费建模,反而是"纸面"干跑记账比"真钱"live 记账更精确。**这四条独立指向同一件事**:项目引以为傲的"诚实不自欺"文化,在"研究结论到执行现实的换算"这一段还没有被同等严格地执行——这也是为什么支撑 crowd_contra 该不该加仓的证据链,比文档叙事暗示的更脆弱。

### 3.5 文档滞后于代码,且滞后方向不可预测(有时文档过于乐观,有时过于悲观)

RISK_REGISTER.md 在**同一份文件的不同条目上**两个方向都错过:P1-2 顶部日志称已修复、详述条目仍标未修复(文档滞后=过于乐观);P1-6 顶部日志称已接线、详述条目仍写"只诊断不阻断"但代码其实已是真实 BLOCK(文档滞后=过于悲观)。Phase 3A 清单发布 3 小时内被自己的后续提交超越。`docs/phase3a/` 15 个死文件(建仓当天写的传统期货 broker 流程)与真实清单并存,足以误导签字人。Barra-lite 一度被误判为"重复劳动",后经更严谨的 git 血缘核实是"文档没回头补一行指针"。**这不是某一份文档写得差,是没有机制让文档随代码状态自动过期或自动刷新**——签字人如果只读摘要而不核对最新 git log/代码,两个方向的误判都会发生。

### 3.6 没有真正独立于开发者本人的复核层

`master` 分支 605 个提交,只有 2 个 merge commit,其中能辨认的人工 PR 复核只有 1 次(落在 `main` 而非 `master`,且是 2026-07-03 那一次)——此后包括本次审计过程中用户拍板、由本代理直接执行的分支合并本身,同样是"不经 PR 的直接 git merge"。`CODEOWNERS` 文件内容扎实但其强制力依赖的 GitHub 服务端设置本地无法核实。治理扫描器结构性不覆盖 `live/`(33 个文件里约 12% 有结构性边界测试)。喂进 evaluator 的 `leakage_ok`/`pbo` 证据是裸布尔值,没有哈希绑定到具体计算,理论上任何调用方(含未来的 bug)可以直接传 `True`。**这些共同构成"信任链在哪一环真正闭合"的问题**:核心风控/资金路径的机制是真实的,但"这些机制本身有没有被独立于写它的人复核过"这件事,目前答案基本是否定的——这与 8 周证据钟并列,是唯二不能靠 agent 自己修补、必须用户亲自介入的类别。

## 4. 各维度详细发现(P0/P1/P2 分级)

### 4.1 数据层 —— 2.0/5

**[P1] F1 · PIT 的 knowledge_time 对全部批量历史数据是 event_time 的字节级复制品,回填数据零真实"何时可知"保护**
证据 [已核实]:直接 SQL 查询 8 个核心行情库 `COUNT(*) WHERE knowledge_time != event_time`:crypto_binance.db(471,137 行)、crypto_daily.db(15,036)、crypto_funding.db(232,118)、crypto_history.db(44,981)、crypto_smallcap.db(36,491)、crypto_volume.db(1,337)、fx.db(264,165)——**divergence 恒为 0**。唯一例外 crypto.db(30 分钟快照)有真实滞后,证明团队具备正确实现能力,只是没用在主力数据集上。`services/data/pit_store.py`/`quantos/contracts/temporal.py` 的过滤/反前视校验本身是真的,但只保证"不能声称比事件更早知道",不保证"声称的知道时刻是真的"。
顶级做法:vendor 交付时间与 event_time 分离追踪、revision/vintage 独立记录(项目自己的 `alfred_fetch.py` 用真实 `realtime_start` 做对了)。
差距后果:任何"PIT-safe 历史 Sharpe/IC"结论目前不能验证其对 backfill 污染的免疫力。
修补:新增量抓取写真实 knowledge_time;历史 backfill 部分标 `provenance=BACKFILL`。工作量:中。

**[P1] F2 · survivorship-free universe 机制建好测好,但零生产调用方——已发布结论全部基于静态当前币种列表**
证据 [已核实]:`services/data/universe.py`/`universe_store.py` 代码正确;但 `services/research/panel_builder.py:31-52` `universe` 参数默认 `None`,判空即跳过过滤;全仓库 `PITUniverse(`/`SqliteUniverseStore` 只命中 `tests/**`,零生产驱动器构造过 universe。
差距后果:crowd_contra 现用 14 只大市值币历史上鲜有下架,对这一具体策略现实影响有限;但作为通用方法论是能力而非实践。
修补:`panel_builder` 改为必须显式传 universe;对现用 14 币做一次性人工历史成分核验。工作量:中。

**[P1] F3 · crypto_daily.db 跨币种 volume 单位不一致——2026-06-25 最新数据仍复现,项目自审计标 ACTIVE、未修**
证据 [已核实]:直接查询最新两日 EOS volume≈$23k vs BTC≈$43B(五到六个数量级差),`crypto_history.db` 同样复现。`docs/audit/RISK_REGISTER.md:147-150` P2-2 状态 ACTIVE:"已诚实标注 UNRELIABLE_VOLUME_UNITS 并 spawn 修复,但 bug 数据仍在库里"——**修的是探测器,没修数据**。
差距后果:依赖该库算 ADV/容量的结论($1-72M 容量带)对非头部币不可信;$3-5k 规模本身不需要容量结论,但后续放量决策若引用会被误导。
修补:重拉中小市值币 volume 并统一单位,或让消费端强制排除未过检的标的。工作量:小-中。

> **2026-07-13 后续根因更正**:本段“数据库混单位”的判断被生产链取证推翻。CoinGecko
> `total_volumes` 与 CryptoCompare `volumeto` 均为 quote/USD volume；真正 bug 是
> `scenario_tail_research` 对 quote volume 再乘 close，才生成 BTC `$Q` vs EOS `$K` 的假极差。
> 已增加 `VolumeUnit/VolumeSemantics` 强契约、修复双乘并重算；原始发现保留作为审计历史，不再作为 ACTIVE 缺口。

**[P1] F4 · 数据质量门在接入路径 computed-but-not-enforced,乱序检测结构性恒为 0**
证据 [已核实]:`services/data/market_provider.py:45` `strict` 默认 `False`;唯一显式 `strict=True` 调用方是不在 crypto 实盘路径的 `yahoo_equity_fetch.py`。`RISK_REGISTER.md:142-145` P2-1:`file_ingest.py:66` 用已排序序列喂乱序检测,数学上不可能失败;`missing_sessions` 未传参恒为 0。
差距后果:脏/乱序/单位错数据理论上可无声进入前向证据时钟或 live 决策面板;live 与研究复用同一套抓取模块,弱点两条路径共享。
修补:对喂 live 刷新与前向证据钟的抓取器统一开 `strict=True`;修复乱序检测顺序问题。工作量:小(RISK_REGISTER 已给验收标准)。

**[P1] F5 · live 侧 liquidity/volatility 风控引擎吃的是硬编码占位符,不是真实行情——"实时数据喂风控"这一环实际不存在**
证据 [已核实]:`live/tools/live_track_advance.py:234-267` 给 RiskContext 喂 `est_daily_volume=1e12`、`instrument_vol=0.8` 常量占位符,`gate_limits` 又设上限宽松,导致 `check_liquidity`/`check_volatility` 数学上永不触发(项目原话)。真实 bid/ask(`live/exchange/binance_futures.py::book_ticker()`)只用于 LIMIT 单定价锚点,从未接入风控引擎。
差距后果:$3-5k 规模下主流币真实冲击/流动性问题概率本身低,现状是"运气好没暴露",不是"设计上安全"。
修补:项目已有修复方向(真实 ADV/vol 源接入),机制骨架已在。工作量:中。

**[P1] F10 · 回测/干跑撮合未建模真实交易所量化(tick/lot/min-notional),小资金规模放大了这个通常该是 P2 的问题**
证据 [已核实]:`live/execution/filters.py:58-105` 真实取 Binance exchangeInfo 精度并应用;`services/execution/dry_run_pipeline.py` 全文检索 `step_size`/`tick_size`/`min_notional` 零命中。
差距后果:$3-5k÷14 标的≈单标的仓位数百美元,BTC step size(约 $60)可占单笔仓位两位数百分比——本审计明确把它从"通常 P2"上调到"当前规模下 P1"。
修补:把 `live/execution/filters.py` 的量化函数抽成公共工具给 `dry_run_pipeline` 复用。工作量:小-中。

**[P2] F6 · 抓取工程"研究级摄入+生产级存储+业余级网络层",且一处并发限速是死代码**
证据 [已核实]:仅 `fred_fetch.py`/`alfred_fetch.py` 有真 fail-closed 校验;`binance_klines/funding_fetch.py` 无退避重试。`binance_oi_metrics_fetch.py:184` 的 `sleep` 参数只在 `max_workers<=1` 分支生效,默认走 12 线程并发分支(`:212-213`)不受限速节流;429/超时/真实 404 全部折叠成"当天数据缺失",无告警。
修补:把 `live/exchange/retry.py` 的退避逻辑抽成公共工具接入抓取器。工作量:小。

**[P2] F7 · 多 vendor"冗余"名不副实,三源互不往来、从无核对**
证据 [已核实]:Binance/CoinGecko/CryptoCompare 三源数据落进互不知道彼此的 sqlite 文件,无交叉 diff 脚本。唯一核价是 `live/execution/price_check.py::flag_divergent_instruments`,服务下单安全,不覆盖历史研究库真实性。
修补:写轻量周度 reconciliation 脚本比对三源同标的同日收盘价。工作量:小。

**[P2] F8 · 无自动化调度/24x7 运维,研究库普遍陈旧 11-13 天且无人告警;监控名单里有陈旧 868 天的"僵尸标的"**
证据 [已核实]:全仓库唯一真实定时任务是 `register_schedules.ps1` 给 `live_track_advance --submit` 注册的每周任务。审计当日(07-07)直查:crypto_binance/daily/history/smallcap.db、fx.db 最新数据停在 2026-06-25~26(11-13 天未更新),crypto_funding.db 因被 live 任务捎带只停 1 天——"新鲜"与"陈旧"完全取决于是否被 live 任务捎带。`live_refresh_status.json` 显示监控的 115 个标的中最陈旧一条 `worst_days: 868`。
修补:加一个每日/每周计划任务串起现有 fetcher;清理监控名单僵尸标的。工作量:小。

**[P2] F9 · 未文档化的 90 只美股数据集(与"无股票"既定认知矛盾)+ 三个抓取器共享"删库重建、非原子"模式**
证据 [已核实]:`equity_daily.db`(54MB,198,448 行,~90 只美股,数据到 2026-06-30)与 `tools/yahoo_equity_fetch.py` 对应,已诚实自文档化(RAW 未复权、幸存者偏差标注),但未出现在既定认知里——**文档漂移,非数据本身的错**。该文件 `os.remove(_DB)` 后逐 symbol 重灌,非原子;`fred_fetch.py`/`alfred_fetch.py` 共享同款模式,且 macro_fred.db 真的被 regime 分析引用。
修补:三个 rebuild 型抓取器统一改"写临时文件+验证行数+原子 rename";更新 CLAUDE.md 补入该数据集。工作量:小。

**数据层最被高估**:"real, tested PIT store / survivorship-free universe"这句话本身——单元测试层面为真,但在支撑 crowd_contra/carry 结论的实际数据路径上几乎是摆设(F1/F2)。"能力存在"与"能力在用"之间的落差。

**数据层最被低估**:live/ 与研究复用**同一套**抓取代码(非契约兼容,是字面同一 import)——消除了量化行业最经典的"回测一套解析、实盘另一套,悄悄不一致"的整类 P0 风险,文档里完全没强调。代价是共享 fate:F1/F3/F6 同时影响两侧。

**数据层 0 个 P0 的诚实说明**:$3-5k 规模 + Phase 3A 人工门/mandate 硬顶/kill-switch/新鲜度门/CoinGecko 软核价构成纵深防御,数据层本身没有一条能绕过外层防护直接致损的**当前可触发**路径,与项目 RISK_REGISTER 独立吻合。但六个 P1 共同指向:支撑"该不该信任 crowd_contra"的证据链比 CLAUDE.md 叙事暗示的更脆弱——应在加仓/放行下一笔资金前逐一闭环。



### 4.2 安全 —— 3/5

**[P0] 密钥权限无机器校验——withdrawal-disabled / IP allowlist 只是人工约定,代码从不验证**
证据 [已核实]:全 `<WT>` 树 grep `apiRestrictions|ipRestrict|enableWithdrawals` → 0 命中。`live/exchange/binance_futures.py:78-88`、`bybit_futures.py:107-118` 只校验 `base_url` 白名单 + `SafetyConfig.live_trading_armed`,从未对 `api_key`/`api_secret` 的实际权限做检查。项目自己 2026-07-04 的审查文档已把这条标 P0("**实现为零**"),3 天后(WT HEAD 2026-07-06)代码仍无动作。`docs/runbooks/live-trading-runbook.md` §3 通篇讲位置/轮换/泄露处置,完全没讲验证权限。
差距后果:人工在交易所网站创建 key 时若漏勾"禁用提现"或忘了绑 IP,系统不会有任何提示——这正是"泄漏=直接丢钱"的临界开关。
修补:新增 `tools/verify_key_permissions.py` 探测守卫,接入 `live_track_advance.py` 的 `--submit` 前置链,探测失败/不确定即拒绝。工作量:S(2-4 小时)。**性价比最高的一条。**

**[P0] key/ 目录 ACL 过宽——非 Administrator 主体也有读/改权限**
证据 [已核实]:`Get-Acl H:\code\QUANTOS\key` 显示 `NT AUTHORITY\Authenticated Users` 有 Modify、`BUILTIN\Users` 有 ReadAndExecute,外加 3 个无法解析的孤儿 SID——继承自上级目录的默认 ACL,从未为存放交易所密钥收紧过。`key/` 里躺着 10 个文件含 `live_mandate.json`(当前生效限额)。
差距后果:今天机器只有 Administrator 一个账户,可利用性低;但零成本纵深防御——一旦出现第二个本地账户(远程协作者/被安装服务/恶意植入),不需要提权就能读走全部密钥或悄悄改大 mandate 限额不留痕。
修补:`icacls key /inheritance:r /grant:r Administrators:F SYSTEM:F`。工作量:XS(几分钟,零代码改动)。**建议主网 key 落地前第一件事。**

**[P1] SECURITY.md 声称的"pre-commit 钩子"第二道防线不存在(文档失实)**
证据 [已核实]:`SECURITY.md:25-26` 声称"本地 pre-commit 钩子还会再拦一道";实测 `.pre-commit-config.yaml` 不存在,`.git/hooks/pre-commit` 不存在(只有从未启用的 `.sample`)。`.gitignore` 这层是真实有效的(全历史核查零密钥文件进过 git)。
修补:删掉失实声明,或真装一个(~1 小时)。

**[P1] 每日灾备明文、未加密**
证据 [已核实]:`tools/live_backup.py` 用 `shutil.copy2` 明文复制 `live_track*.db`(完整交易台账)、`live_mandate*.json`(真实限额)到 `data/backup/`,建议异地盘存放。`register_schedules.ps1:54-55` 确认每日 03:00 自动跑,零加密。
差距后果:不含密钥本身,但暴露真实限额与交易历史;若目标是可移动介质或被云同步覆盖,暴露面比想象中大。
修补:加密步骤(zip+密码或 age)。工作量:S(半天)。

**[P1] 审计哈希链无任何外部锚点——对同权限攻击者/失控自动化零取证力**
证据 [已核实,经二次核对]:`entry_hash` 链本地重算比对,`seal()`/`verify_against()` 机制实现正确,但生产代码从未把 commitment 写到本地 sqlite 之外任何地方;`console_api/main.py:601` 调 `build_snapshot` 甚至没传 `audit_commitment=`,退化到弱校验分支。`approval_store.py:19-22` 代码注释自己承认能写 sqlite 的攻击者可同时改 head_hash。`tests/monitoring/test_audit_log.py` 演示伪造整链替代后 `chain_valid()` 返回 True。`signing.py`/`NullSigningAuthority` 本身诚实正确(拒绝式设计,测试覆盖伪造场景)。
差距后果:不影响拦截坏订单能力(gate/mandate/kill-switch 真实有效),影响的是事后取证——真出事时无法自证链条没被动过手脚。是代码注释已诚实承认的已知限制,非被藏起来。
修补:复用现有 Telegram 通道每日推 head_hash 摘要,廉价但真实的外部锚点。工作量:M(2-3 天)。

**[P1] mandate 文件(实盘限额唯一权威)无签名完整性校验**
证据 [已核实]:`mandate_store.py::load_mandate` 只做结构+NaN/Infinity 校验,不验证来源真实性;`verify_signed_ref` 全仓库调用点只在测试文件里,从未接入 live/ 执行路径。且该文件正躺在 ACL 过宽的 key/ 目录里(见上)。
修补:最低成本加人工计算 checksum 字段 + 变更审计;完整方案等 Phase 14(已知非目标)。工作量:S-M。

**[P1] 供应链:3 个包无版本约束 + 13 条 dependabot 分支零 triage,CI 无漏洞扫描**
证据 [已核实]:`pyproject.toml:31-32`(aiokafka、prometheus_client)、`requirements-dev.txt:4`(pytest-asyncio)完全无版本约束;0 个 hash-pin。npm 侧健康(真实完整 lockfileVersion 3,345 条 integrity 哈希)。`git branch -r` 显示 13 条 `origin/dependabot/*` 分支(7-12 天),全部未合并;CI 无 pip-audit/npm audit/SBOM 步骤。
修补:锁 3 个版本(10 分钟)+ 建立每周扫一眼 dependabot 队列的习惯。工作量:XS。

**[P2] 密钥静态存储明文,无 DPAPI/Vault 加密**
证据 [已核实代码路径]:`live_track_advance.py:389-402` env 优先,否则明文 `json.load` 读 key 文件。适配器非 dataclass,默认 repr 不含实例属性(验证过不会被异常/日志误打印泄漏)。
差距后果:"同机 Administrator 跑一切"威胁模型下 DPAPI 收益有限(同一受损上下文一样能解密);真正防的是密钥目录被整体复制到别处。工作量:L,ROI 低于两条 P0,可排后。

**[P2] 无 SIEM/HSM/红队**——单机单人体量完全不需要,列出仅为对标完整性。

**15-agent 安全审查(2026-07-05)closeout 抽查:3/3 verified**——CRITICAL kill-switch 竞态修复、HIGH mandate NaN/Infinity 双层修复、HIGH `VITE_OPS_TOKEN` 明文编译进前端 bundle 改 token 文件鉴权,三条全部代码核实属实。但该审查范围是本机进程间鉴权/竞态/前端泄漏,完全没覆盖上面两条 P0(exchange 侧密钥权限、密钥目录 ACL)——"清零"是真的,清的不是最要命的地方。

**安全层最被高估**:①"15-agent 审查已清零 HIGH/MEDIUM"——真实但没碰最贵的两间屋子;② SECURITY.md"两层防护"——一层真一层空气;③ hash-chained 审计日志——工程质量好,但对"同机 Administrator"威胁模型取证价值接近零;④ `docs/phase3a/` 的"Security Officer/IP Whitelist Process"模板——全是空白占位符。

**安全层最被低估**:① `878a751`/`4327645` 真实对抗式复审(mandate NaN 绕过用 $60M PoC 验证过、kill-switch 竞态)——单人项目自己找出并在真金前修掉,纪律稀有;② mainnet 适配器结构性拒绝——四布尔全默认 False,双 venue 对称,零非测试路径能点亮;③ `.gitignore` 纪律经得住全历史查验;④ 三处本机鉴权 token 从一次真实教训后一致推广到同类代码。

### 4.3 研究/Alpha 生产层 —— 2.0/5

**[P0] 回测→执行的"衰减"旗舰数字不是系统计算出来的,而是人工算出来写进文档的**
证据 [已核实,sqlite 直读]:`dry_run.db` 6 行 `detail` JSON 里 `equity_curve_vt` 均是裸 list,旁边**没有** `sharpe_vt` 字段——vol-target 净值曲线存在,但其 Sharpe 从未被管线计算或落盘。`dry_run_pipeline.py:291-307` 的 `_ann_sharpe` 只对 raw returns 调用;前端 `DryRunPipeline.tsx` 只画 VT 曲线图,不显示 VT Sharpe 数字。项目记忆反复引用的"carry gross 1.44→净 VT 0.65""crowd_contra 1.21→1.38"这类"可部署口径"数字,是有人把序列导出后在管线外手算、写进 `docs/PROJECT_STATE.md` 的——**不能被任何一条命令复现**。
同时成本口径有 **4 套互不校准的实现**:研究/自动搜索用 `costs.py::CostModel`(3bps,impact 默认关闭);carry/crowd_contra"旗舰结论"来自手写内联循环(自定义分层滑点);干跑侧每个策略驱动器各自硬编码不同 flat bps(8.5/9.0/10.0);Almgren-Chriss 冲击模型只接入 1 个驱动器。研究侧 3bps 比执行侧 9-10bps 便宜 2-3 倍,"回测 Sharpe"和"干跑 Sharpe"从成本量级上就不是同一把尺子。全仓库最真实的撮合引擎 `event_engine.py`(参与率封顶+整手+融资成本)零生产驱动器调用,只被自己的单测调用。全仓库无"研究→执行衰减"指标模块。
差距后果:任何"这条边执行现实下还剩多少"的结论目前不可审计、不可复现——包括即将支持首笔实盘签字的那个数字。这正是项目自己"诚实不自欺"文化(大量 `UNCALIBRATED_PRINCIPLED` 标注)在这一处没被同等严格执行。
修补:S(应急,打标签"人工计算、非管线输出");M(根治 3-6 天,`sharpe_vt` 做成一等持久化字段,四条成本路径收敛到 2 条)。

**[P1] Alpha 正式晋级流水线(catalog/lifecycle,26 模块/185 测试)迄今 0 个真实候选走完过**
证据 [已核实]:`experiment_registry.db` 的 `alpha_state` 表 60 行全部停在 `TESTING`,0 条 VALIDATED/PAPER_TRADING;`trials` 表 60 行 `first_seen_ts` 全部同一毫秒时间戳(一次性批跑,非持续运营)。真正走向 dry-run/实盘的 carry、crowd_contra 是硬编码 Python 常量,完全绕开 AlphaCatalog/AlphaLifecycleManager,直接进 dry-run 流水线。
差距后果:这套精心构建的晋级状态机对实际正在走向实盘的两个候选从未真正把过关。注意:不等于"没有安全把关"(pre-trade gate/kill-switch/paper-track 这几层真实且被两条边实际走过),缺的是目录/生命周期这一层的治理完整性,对未来第 3、4 个候选能否被同一套流程可信把关,目前没有先例。
修补:S(1-2 天,诚实标注为"研究员手动种子常量,有意绕过 catalog")或 M(1-2 周,补注册进 catalog)。

**[P1] 自动搜索的规模是"验证概念",不是"已耗尽假设空间"——"0 条新正交边"容易被过度解读**
证据 [已核实]:`crypto_factory_loop.json`:24 币宇宙(已有 336 币数据却只用 24),`rounds:2, dry_limit:2`,单轮理论上限 ≈288 次基因组求值,2 轮 ≤576 次——这是全部自动挖掘历史,且只跑过一次。`services/intelligence/**` 全文 grep `Pool|multiprocessing|concurrent.futures|asyncio.gather` 0 命中——纯单线程顺序循环,尽管每次求值是无副作用纯函数,并行化只需几行代码。gp_tree/cs_genome 的"进化搜索"不含随机数(SHA256 哈希索引确定性选点,测试钉死不能 import random)——可复现性正确取舍,代价是确定性局部游走,没有"多次独立重启逃出局部最优"手段。补充证据:`crypto_alpha_search_snapshot.json`(115 币、261 候选)5-fold walk-forward `mean_is_sharpe=1.14→mean_oos_sharpe=-0.45`,同一候选不同纪律下从"看起来很好"到"灾难性"——证明方法链条没有说谎,但也说明挖掘量级很小、一次性、非机构规模。
差距后果:回答"数据瓶颈 vs 方法瓶颈 vs 投入瓶颈"——**主要是投入/规模瓶颈**,其次是数据(零 L2/订单簿),方法本身没问题。
修补:S(数小时到 1 天,套 `ProcessPoolExecutor`,纯函数天然可并行——**本次审计发现的性价比最高单项修补**);M(几天,24 币子集扩到 336 币全量+周期性重跑调度)。

**[P1] crowd_contra 的"强分散"叙事和它的因子集中度风险,视角不同、都是真的,但只有一个被看见**
证据 [已核实]:`barra_lite_snapshot.json`:`crowd_contra` book 的 `factor_contributions.crowding=0.7562`,占 `total_risk=1.3969` 的 **54%**;`crowding=-0.8696` 是最大暴露项。与既有"corr(carry,crowd_contra)=-0.04,强分散"的收益流相关性结论不矛盾但视角不同——收益相关性低不代表风险因子暴露分散,crowd_contra 几乎是纯粹的"crowding 因子"押注。`barra_lite.py` 诚实自标注"Diagnostic only",目前没有任何自动化流程会看到这个风险;且默认因子协方差矩阵是对角阵(假设因子间协方差为零)、残差方差是启发式公式——"lite"名副其实,非完整 Barra。
差距后果:对 $3-5k 单条而言目前是理论关注;若后续放大仓位或用"分散"故事支撑加仓决策而没人看过这份报告,就是决策依据不完整。
修补:S(1 天,纳入人工签字前检查清单,不需要接自动门禁)。

**[P1] 小额执行的力学从未被验证过(即便大概率没事)**
证据 [已核实]:`services/execution` 全树搜 `min_notional|lot_size|round_lot`,唯一命中是死代码 `event_engine.py` 里的通用参数(默认 1,不生效)。carry 24 腿等权(约 $167/腿)、crowd_contra 8 腿等权(约 $500/腿)——数值上远高于 Binance 常见最小名义值($5-20),大概率没问题,但代码库里没有任何地方明确算过这笔账。
差距后果:RE-1 花大量工程校准 $1-72M 容量区间带,却没人花 10 分钟验证 $3-5k 这个真正要发生的规模下整手取整会不会吃掉收益或让权重失真——"过度工程 vs 反而缺"对比最清楚的一例。
修补:S(<1 天,对照真实 stepSize/minNotional 手算一遍)。

**[P2] 核心组合数学被独立重复实现(walk-forward/embargo ×4、成本模型 ×4、inverse-vol ×2)**
证据 [已核实]:purge/embargo 4 套并行实现,其中 `validation/pipeline.py::run_research_validation`(看似端到端入口)全仓库零调用方,只被自己测试覆盖;生产门禁实际用的是 `alpha/walk_forward.py` 独立重写版本。`inverse_vol_weights` 独立存在两份,输入契约和退化情形处理都不同。
差距后果:生产口径本身是对的,风险在未来维护——迟早在某次修复里只改一份、留下静默分歧。
修补:M(3-5 天,收敛到单一实现)。

**[P2] 零 GPU/深度学习/分布式计算/特征库/另类数据基础设施** —— 全部运行依赖仅 `fastapi/numpy/scikit-learn` 级别,全仓库无 torch/tensorflow/ray/dask。对当前"只有 2 条活边、ML stacking 已诚实证伪无边"的现状不是限制因素——多花算力解决不了"没有订单簿数据""只有 2 个正交信号源"的问题。**不建议现在投入。**

**[P2] 频率天花板:零日内执行调度、零资金费预测、零周内择时** —— 对 $3-5k 体量基本无关紧要(几百美元订单不需要智能拆单)。上规模才会开始咬人。**不建议现在投入。**

**[P2] 两处正确性小 bug**:`factor_engine/domain/model.py:365-367` 的 `compute_ic` 用 `if ic else None` 误判真值,`ic==0.0` 合法零相关值会被错误当无效处理(轻微偏置聚合 IC 均值方向上偏乐观);`research_runner/runner.py:20-29` 的 `_git_sha()` 在 git worktree 布局下静默失败返回 None(与 §0 发现独立吻合),导致研究运行代码溯源丢失。修补:S(各 <1 天)。

**研究/Alpha 层最被高估**:"回测-执行衰减已被诚实量化"的印象(carry 1.44→0.65 等)——VT 数字是管线外手算的,不可复现,且混用了成本口径/整手/vol-target 杠杆三个变量。次席:AlphaCatalog/Lifecycle 这套治理骨架,0 个真实候选走完过。

**研究/Alpha 层最被低估**:确定性优先的工程文化本身——GP/进化搜索无 RNG/wall-clock、ML stacking 锁 BLAS 线程环境变量求逐位可复现、AST 编译器用真实攻击载荷测沙箱逃逸。这类"任何结论都不是 RNG/未来函数产物"的底线保证,是后面一切统计结论有意义的前提,却最容易被忽略,因为它不产生吸引眼球的 Sharpe 数字。

### 4.4 风控层 —— 2.5/5

分项参照(非简单平均):KillSwitch 生命周期工程质量≈4/5(接近顶级);pre-trade 22/23 项 fail-closed 检查的结构≈3.5/5;但 VaR/强平价/组合流动性/venue 托管风险四项顶级标配在 live 路径上完全缺席或形同虚设,每项单独看接近 0/5,把总分显著拉低。**本维度未发现真正 P0**(与 RISK_REGISTER"当前无 ACTIVE P0"结论一致),全部是 P1(8 周证据期内/加仓前应处理)与 P2(顶级对标,$3-5k 下大多可明确不做)。

**live/ 执行路径全部风控门枚举(共 22 项,逐条 file:line 核实)**。关键分类:
- **真 BLOCK 且 fail-closed**:拥挤度绝对 z-score(z*=3.0,用户签)、ADV/流动性门与波动率门(见下方 R8 更正)、保证金地板(2.0,用户签,经 arm KillSwitch)、mandate 单笔/账户级/逐标的 cap(用户签)、firm 级跨所硬门(gross 20000/net 10000/单名 0.20,用户签,三文件交叉确认真实生效)、验证证明门(当前 `key/` 下未见签署文件,crowd_contra 会被拦)、通用 PreTradeRiskGate 引擎组。
- **仅 WARN,永不硬停**:拥挤度 rank 加权均值(阈值 UNCALIBRATED 占位)、盘中权益跳变异常(0.15,用户签,设计上绝不 arm/减仓)、多策略共享 mandate cap 提示。
- **非门,纯诊断**:firm 跨账户聚合、死人开关 watchdog、live-paper 对账 parity。
- **未接入**:`sleeve_sizing`(跨策略风险平衡,默认 off)。

**[P1-R1] 两套互不协调的仓位限额体系,只有一套真正经人类审阅**
证据 [已核实]:`live_track_advance.py:764,840-841` 把 gate 实际生效上限算成 `gross=aum×2`、`net=aum×0.3`、`single_order=aum×0.5`、`daily_loss=aum×0.1`——这组乘数在代码里**没有任何"用户签"或"未校准占位"标注**。同时 `key/live_mandate*.json` 是真正逐字段人工签署的独立限额体系(`max_gross_notional=10,000` 等)。两套体系并行生效,代码/文档任何地方都没说清楚"哪一层最终收口",按典型 deploy_aum 反推,net 敞口实际可能是 gate 的 `aum×0.3` 更紧,而非"最终由已签 mandate 收口"这个直觉预期。
差距后果:不是无上限暴露(两套都 fail-closed 且有界),而是实际生效的上限可能不是那个被慎重签署的数字;未来签一份更大 mandate 时,这组从未被审视过的 gate 侧乘数不会自动跟着重新评估。
修补:S(半天),写只读小工具打印"给定当前 aum,两套体系实际生效数字分别是多少、哪个更紧",作为签字材料。

**[P1-R2] VaR/CVaR/HHI 已建好测过,但在一个平行宇宙里——零 live 消费,本轮修补成本最低性价比最高的一项**
证据 [已核实]:`services/risk/research_risk/domain/risk_modules.py:68-84` 是真实历史 5% 分位 VaR/CVaR(非占位符),`:128-139` 是真实 HHI;`capital_allocator.py` 甚至有 Rockafellar-Uryasev CVaR 惩罚项(默认关闭)。但对 `research_risk`/`capital_allocator` 的消费方检索,`live/`、`pre_trade/`、`execution/` 全部零命中;`checks.py` 的 23 项 `CHECK_REGISTRY` 里没有 VaR/CVaR/HHI 任何一项。
差距后果:crowd_contra 上真钱前,没有任何自动化会算出它的 VaR/CVaR/集中度给签字人看——即使这些数字可以立刻算出来。
修补:S-M(1-2 天),把 `evaluate_dry_run_risk` 的 VaR/CVaR/HHI 三引擎接进 `live_risk_sentinel.py`/`gonogo_report` 做**只读展示**(不做成 BLOCK 门,避免仓促上门禁)。

**[P1-R3] 保证金地板是账户级粗代理,交易所已给出的真实强平价被读到又丢弃**
证据 [已核实]:`_MIN_MARGIN_RATIO=2.0` 判断输入是交易所返回的**账户级聚合**字段,不区分具体仓位/分层维持保证金率。全仓库 `liquidation_price` 关键词零命中。Bybit V5 `position/list` 响应自带 `liqPrice` 字段,但 `live_snapshot.py::bybit_position_rows` 只提取部分字段,**`liqPrice` 被直接丢弃,连展示都没留**。
差距后果:$3-5k、跨市场保证金模式下实际风险有限[推断,未确认当前 cross/isolated],但这是"运气好模式凑巧对了",非设计验证过。
修补:S(数小时),先把 Bybit 已白送的 `liqPrice` 接进展示层(零建模成本);Binance 侧调 `/fapi/v2/positionRisk` 的 `liquidationPrice` 字段。

**[P1-R4] Venue 资金托管集中度——FTX 式尾部风险,连诊断都没有**
证据 [已核实]:`firm_aggregation.py`/`firm_gate.py` 限额语义只有 gross/net/单名跨所权重三样,`custody|counterparty` 全仓库零命中。系统没有任何地方回答"多少钱躺在 Binance、多少躺在 Bybit"。
修补:XS-S(几小时),`firm_aggregation` 已按 venue 拆分持仓,只需加"该 venue 名义资金/总资金"诊断阈值。

**[P1-R5] Barra-lite 拥挤集中度(54.14%)发现完全没人看,不在任何签字清单里**
证据 [已核实]:`barra_lite_snapshot.json` 精确计算 `crowding/total_risk=54.14%`——crowd_contra 风险几乎是纯粹的"crowding 因子"押注,不是多因子分散。`PHASE_3A_GO_NO_GO_CHECKLIST.md`/`live-trading-runbook.md` 搜 barra/集中度/crowding 均零匹配。
差距后果:签字人若只看"corr(carry,crowd_contra)=-0.04,强分散"这类收益流结论,会误判 crowd_contra 本身是分散的;是决策依据不完整,非代码缺陷。
修补:XS(1 小时),不需要写代码,清单 C 段加一行要求签字人跑一次 `barra_lite_diagnostics.py` 并过目 `factor_contributions`。

**[P1-R6] 策略层限额结构性缺失——4 层限额(instrument/strategy/account/firm)目前只立起 3.5 层**
证据 [已核实]:`check_strategy_approved` 只是二值判断非风险预算;真正该承载策略层限额的 mandate per-strategy 子额度被 `c96f285` 有意搁置。instrument 层真实、account 层最完整(9/23 项)、firm 层三文件交叉确认真实 BLOCK,唯独 strategy 层没有风险预算意义上的硬门。目前因只有一条策略在跑不显现,加第二条策略上真钱那一刻这颗雷才会响。
修补:M(2-3 天,RISK_REGISTER P1-2 已写明验收标准)。

**[P1-R7] 文档卫生系统性滞后——RISK_REGISTER.md 至少 3 个独立矛盾实例,不是偶然笔误**
证据 [已核实,3 个独立来源]:①P1-2 顶部日志称已修复,详述条目仍标 LATENT(用户已知);②新发现 P1-6 同样模式——顶部称已接线,详述仍写"只诊断不阻断",但代码三处交叉验证(`firm_gate.py`定义+`live_risk_sentinel.py`写状态+`live_track_advance.py`读状态拦停)确认**代码侧已是真实 BLOCK 硬门,详述条目文本过时**(方向相反的误判:这条其实比文档说的更好);③`live-trading-runbook.md:106-108` 描述的是 2026-07-05 跨进程持久化修复**之前**的行为("每轮新建 KillSwitch 不会被上轮闩锁挡住"),当前代码 `live_track_advance.py:747` 确实会加载持久化状态,方向上安全(文档比代码更悲观)但仍需修正。
修补:XS(1 小时),把 3 处过时正文改成与顶部日志/实际代码一致。

**[更正] ADV 参与率/波动率门:数据喂入已于合并前一天(`198b1db`,07-07 19:58)真正修复,阈值本身仍是未校准占位**
证据 [已核实,更正此前"两个引擎从未真正工作过"的表述]:阈值 `max_participation_rate=1.0`/`max_instrument_vol=5.0` 本身没变,仍宽到不太可能触发。**但喂给检查的数据已经修复**:此前无论订单大小恒喂 `1e12`/`0.8` 占位符(数学上不可能触发);现在 `live_track_advance.py:327-332` 真实按标的传入 `adv_units`/`inst_vol`,**新增仓位缺真实数据时会真的 fail-closed 拒绝**(减仓单豁免不受影响)。commit message 自述"testnet 预演 n_rejected_by_gate 5→0"。即:"数据是假的所以永不触发"这个更严重的问题已不成立,剩下的是"阈值本身还没签,签之前宽到几乎不咬合"这个较轻问题。清单 B4 段仍列为未签空白项,与代码现状一致。
修补:S,把 B4 的两个阈值数字签掉即可,机制已经真实可用。

**[P2] StressTestEngine"编造乘数"问题——现状确认已实质修复,保留诚实降级路径**(正面发现)
证据 [已核实]:`StressTestEngine.evaluate()` 现在双路径:传 `timestamps` 走 `_evaluate_replay()` 用真实历史危机窗口(LUNA/FTX/银行业),标 `REAL_HISTORICAL_REPLAY`;不传则退到旧硬编码倍数,但诚实标 `SYNTHETIC_MULTIPLIER_UNCALIBRATED`,不再冒充真实情景。`dry_run_crowd_contra_multi.py:147-148` 确认生产调用方传了真实 timestamps,走修复后路径。

**[P2] 零 VaR 回测框架**——Kupiec POF/Basel traffic-light 全仓库零命中,依赖 R2 先完成,$3-5k 单策略规模优先级明显低于 R2。

**[P2] 组合层"出清天数"完全不存在**——$3-5k÷14 标的仓位规模远低于任何真实流动性约束,**当前规模下确实不重要**,不建议投入。

**[P2] 隔夜/周末/交易所维护窗口 gap risk 完全空白**——加密 7x24 弱化了这个维度的必要性,$3-5k 下明确不重要,不建议投入。

**[P2] 无独立风控组织**——结构性超出代码修复范围。QUANTOS 实际做的是"协议层替代":任何 actor 皆可 arm kill-switch、复位须 3 个不同真人走完整审批链、firm 硬门需用户亲签——单人体量下对"独立性"精神的合理代偿,非缺陷,不建议追加组织架构投入。

**[P2] Barra-lite 是诚实的"lite",非造假,但差距值得摆出来对标**——因子协方差矩阵默认对角阵,生产驱动器调用时未传 `factor_cov=`,当前快照 100% 走对角默认分支;残差方差是收缩系数硬编码而非真实 OLS 回归残差。docstring 诚实声明"diagnostic only"。$3-5k、2 条边规模下合理,不建议现在投入建满血版。

**硬编码风险参数清单摘要**(完整清单约 70+ 处,按来源分四类):①用户签(注释明确)7 处,含保证金地板 2.0、权益跳变 0.15、firm 三限额、拥挤 z*=3.0;②工程占位/未校准(自标注)约 24 处,含 ADV/vol 阈值、拥挤门 threshold/warn、频率护栏、死人开关容忍窗、Almgren-Chriss 冲击系数;③**无出处说明(体量最大,约 45 处,恰恰是真正决定 live 敞口大小的一类)**,含 gate 侧 `aum×2`/`aum×0.3`/`aum×0.5`/`aum×0.1` 全部乘数、`pre_trade/config.py` 默认值、`research_risk/domain/models.py` 的 VaR/CVaR 阈值字段;④mandate JSON(用户运行时手填,非代码硬编码)——`key/live_mandate.json`/`live_mandate_bybit_demo.json` 均含 2026-07-05 会话授权+8周到期重签的 `_note`,`live_mandate_bybit.json`(正式非demo)与 `live_validation_attestations.json` 均不存在。**这正是 R1 发现的核心**:真正"用户签"集中在 6-7 个数字上,体量最大的"无出处"一类恰恰是真正决定敞口大小的 aum 乘数组合。

**风控层最被高估**:"检查存在"不等于"检查会触发"——"22/23 项 fail-closed"听起来面面俱到,但流动性/波动率两项直到合并前一天才第一次吃到真实数据,阈值本身仍占位;"Barra-lite 结构化风险诊断"名字听着机构级,实际是对角协方差+启发式残差的 lite 版且零消费方——都不是造假,但名字和检查数量容易让不点进代码看的签字人高估实际严谨度。

**风控层最被低估**:KillSwitch 工程质量本身——latch+跨进程 JSON 持久化+"只加不减"写入语义专门修复了并发竞态(2026-07-05 CRITICAL 修复,逐行核实)+3 个真实生产入口全部正确加载持久化状态+复位走三方真人 CLI 完整审批链,完成度接近顶级但因不产生吸睛数字而显得平淡。同样被低估:`live_risk_sentinel.py` 每小时真实无人值守自主减仓能力(越限时真实调用 `emergency.py` 提交 reduce-only 平仓单,不只是告警)——很多机构级系统也只做到告警,QUANTOS 已做到检测+自动执行降险闭环,"诊断非门禁"这类保守措辞容易让人误以为系统里没有任何自动化真正动过仓位。

### 4.5 流程/治理层 —— 2/5

骨架诚实扎实(治理扫描器机制真实、self-admitted 局限随处可见),但证据钟静默停摆、文件追不上代码——这是本轮拖低分数的主因,不是治理设计本身有问题。

**[P0] 8 周前向证据钟已静默停摆,根因未修,机制不补跑漏周**
证据 [已核实,sqlite 直读+schtasks 交叉核对]:`paper_track.db` 全部历史只有**一条**决策记录(`decided_ts=2026-07-02`),之后再未写入。三个每周任务(`QUANTOS_forward_track`/`QUANTOS_live_track_binance`/`QUANTOS_live_track_bybit_demo`)在唯一一次该跑的周一(2026-07-06)**同一秒(12:34:07)、同一错误码全部失败**——不是三个独立故障,是一个根因的三次复现(与 §0.1 同源)。[推断,配置与失败/成功时间戳高度吻合]三个任务 Logon Mode 均为 **Interactive only**——唯一在"非交互时段"触发的周一 09:00/09:10/09:20 全部落空。`forward_track_advance.py` 的"catch-up"只是滚动 due-check,**不是日历回填**;数据质量门遇到脏/陈旧数据选择跳过本周留 pending,**漏掉的证据周不会被追认,是永久性缺口**。
差距后果:8 周证据钟公信力现在存疑——不知道过去 6 天已稳定积累多少"真"证据。若下周一同一根因再次失败且无人发现,证据钟可能无限期空转无人知晓。
修补:(a)5 个任务 Logon Mode 改为 "Run whether user is logged on or not"(~30 分钟);(b)立刻手工补跑一次 `forward_track_advance`;(c)`/tr` 加输出重定向;(d)加"距上次成功运行超过 N 天"独立健康检查接入告警。

**[P0] key/ 目录 ACL 世界可写——mandate 完整性唯一实际防线不存在**(与安全层、平台层独立三次复现)
证据 [已核实,现场重新执行 Get-Acl 独立复核]:`Authenticated Users`=Modify、`Users`=ReadAndExecute。**新增关键证据**:`RISK_REGISTER.md` P0-1 验收标准明确要求"测试证明 agent 环境无 key/ 写路径",本次专门 grep `tests/governance/` **未找到任何检查该 ACL 的测试**——这条声称的补偿控制不是"弱",是**不存在**。
修补:`icacls key /inheritance:r /grant:r Administrators:F SYSTEM:F`(几分钟);之后加一个轻量测试固化这个不变量(半天)。

**[P0] Phase 3A 清单发布 3 小时内即被自己的后续提交超越,现无法反映真实签署状态**
证据 [已核实,git log 精确到秒]:`PHASE_3A_GO_NO_GO_CHECKLIST.md` 只有一次提交(`bb9a701`,16:58:43)。同一天接下来 3 小时内又提交 5 次,恰好覆盖清单列出的 B4 待签项:ADV/波动门(`198b1db` 19:58 激活)、权益异常告警+margin 地板(`76a8666` 17:36,"user-signed")、拥挤门 z*=3.0(`11e6037` 18:01,"user-signed")。**且这些"签署"数字直接硬编码进被 git 追踪的源码文件,不是独立签名文件**(不像 B1 的 `key/live_mandate.json`)——"签署"约等于"聊天里报一个数字,提交时被写进 .py 文件",commit 作者统一是 "Trae Assistant",事后无法仅凭 git 区分"这是一次风险数字签署"还是"普通代码修改"。清单文件本身从未被回来勾选,今天任何人打开这份 .md 独立阅读会得出方向性错误的结论。
修补:短期(1 小时)手工刷新清单勾上已发生的 5 处激活;中期(1-2 天)把数字类签署迁出 tracked 源码,改成独立签名文件运行时加载。

**[P0] main(GitHub 默认分支)落后本地 244 文件/近 27000 行——但修复 PR 已存在,零冲突零评审,只是没人点合并**
证据 [已核实]:`git diff --stat master origin/main` = 244 files,整条 live/ 栈、Phase 3A 清单本身在 `origin/main` 上全部不存在。**但** `gh pr view` 确认 PR#16(head=codex 分支,base=main)已存在、`mergeable: MERGEABLE`、零评审、2026-07-03 开至今 5 天未处理——分支收敛的基础设施已就位,只差一次真人复核+点击合并,且核实 origin/main 无任何独有代码(唯一多出的提交是它自己历史上合并 PR#15 产生的 merge commit,两个父提交都已是当前 master 祖先)。`origin/master` 本身也落后本地 157 个提交,从未推送。
差距后果:任何按 GitHub 默认分支做尽调/接手的人,看到的 main 是一个月前、缺整条实盘栈的系统——可信度/连续性风险,非数据丢失风险。
修补:复核合并 PR#16 → 决定 master/main/codex 分支三个名字最终留哪个当唯一主干(建议只留 main)。半天到 1 天。

**[P1] 多方人工批准 = 可自证剧场,已被系统诚实自曝但清单未强制拍板**
证据 [已核实]:`approval.py::_require_human` 只检查 `actor.is_human` 布尔位,"必须是不同的人"是纯字符串比较,无身份认证背书——单人可自己构造 3 个 `Actor(actor_id="a"/"b"/"c", is_human=True)` 走完全流程。**系统自己在两处写清楚了**这个局限(approval.py 文件头注释、`RISK_REGISTER.md` P1-9),不是被掩盖的剧场,但至今没有转化成解决方案。**更重要**:这套机制不只挂在 dormant 的 promotion_api 上——**kill-switch 的 `reset_with_grant` 走同一条 quorum 逻辑**,真出险后"恢复交易"这一步理论上同样可被单人绕过。
差距后果:当前无资金后果(L2 下 APPROVED 不解锁 LIVE);但若 Phase 3A 放行后依赖这套机制做真实决策(尤其 kill-switch 复位),这道安全网在 solo 场景下形同虚设。
修补:决策题——真找 2 个人做只读复核+异步同意,或设计 solo-operator 替代控制(强制冷静期+只读第三方通知+事后不可逆留痕),写进 Phase 3A 清单。

**[P1] CHANGELOG.md 冻结在 422 个提交之前,不含整条实盘栈**——最后一次修改是记录"落地了机构级流程(含 CHANGELOG 本身)"这件事,随后 422 个提交(含全部风险最高的变更)完全不存在于变更日志。修补:S,批量回填关键节点后重新养成习惯。

**[P1] "模型风险管理"页面被合规文档引用为已具备能力,实际在当前代码里不存在**
证据 [已核实]:`COMPLIANCE_AND_PROCESS.md` 称"ModelRiskReview 页(SR 11-7 风格四维评分)"已具备,但 `git show master:apps/.../ModelRiskReview.tsx` 返回 path does not exist——该文件在"IA rebuild 42→30 页"重构中被移除。另一份更早的宏大蓝图文档(`QUANTOS_TOP_TIER_AI_QUANT_DEVELOPMENT_PROCESS.md`)描述的约 30 份必需文档同样一份都不存在——两份文档从未互相更新对齐。真实情况:这一层目前完全靠 RISK_REGISTER 手写文档+人工判断撑着。
修补:S,更正两份文档失实表述为 PLANNED 或删除引用。

**[P2] RISK_REGISTER.md 内部前后矛盾+落后于最新已激活机制**——顶部日志称 P1-2 已修复,下方详述条目仍原样写未修复;P1-5 详述仍描述 ADV/vol 阈值"永不触发",但文档最后提交早于 ADV/vol 真正激活的提交 7 小时。修补:S,通读同步或把状态字段独立成表格首列。

**[P2] 运维手册覆盖对账/限流/密钥泄露扎实,但缺交易所完全宕机、主网前置差异清单、升级联系人**——`live-trading-runbook.md` 质量总体不错(§2.4 对账 drift 硬停明确从 2026-07-04 真实事件反推写成),但无彻底宕机场景独立步骤、无主网上线差异清单(IP 白名单/禁用提现需人工去交易所网页确认,机器测不出来)、无升级联系人树。修补:S(应急步骤+速查框)+M(主网前置清单,1 天)。

**[P2] 分支收敛欠费:13 条 dependabot 12 天零 triage+8 条完全过时分支未清理**——5 条 `codex/*`+3 条 `claude/*` 分支逐一核实全部"ahead of master: 0",已被完全吸收,删除零风险(2 条挂着本地 worktree 需先 remove)。13 条 dependabot 全部对应 OPEN PR、12 天零处理,含 4 个 major 版本升级不建议无脑合并。修补:XS(删分支)+半天到 1 天(dependabot triage)。

**[P2] GitHub 分支保护当前技术上不可用;git 提交人统一为 "Trae Assistant" 弱化审计粒度**——`gh api .../branches/main/protection` 返回 403(私有仓库+免费版无法启用,文档未提及此依赖关系撞墙)。全仓库 git user 统一 "Trae Assistant",无论背后哪个 AI 助手会话驱动,git 层面"作者"粒度无法区分。修补:XS,评估升级 GitHub Pro(几美元/月)+ commit 作者名改真实人名。

**流程治理层最被高估**:"Phase 3A 清单在追踪签署状态"和"8 周证据钟在稳定积累"这两个**印象**本身——前者 3 小时内即过期从未回补,后者自 07-05 起真实静默停摆。两者共同病根:**产出文档/证据的机制本身缺少"机制健康度"这一层监控**——系统很擅长诚实记录"边"的结论,却没有同等力度盯着"记录边结论的流水线自己有没有在正常转"。

**流程治理层最被低估**:项目自己的**主动自曝文化**——绝大多数发现不是从零挖出的秘密,而是 `approval.py` 文件头注释、`RISK_REGISTER.md` 的 LATENT 标注自己写在那里的,本次审计更多是核实这些自曝当前是否仍成立。这比"审计员挖出隐藏问题"的项目健康得多:真正的风险不是被掩盖的谎言,而是"写下来了但没人定期回去检查是否还准"的操作纪律缺口——能靠流程修,不用推倒重来。同样被低估的是 PR#16 这个已存在、MERGEABLE、只差一次点击的分支收敛方案。

**治理扫描器机制与盲区**(既有子任务成果,已核实)——四类清单:`FORBIDDEN_IMPORTS`(ctp/vnpy/quickfix 等)、`FORBIDDEN_CREDENTIALS`、`FORBIDDEN_APIS`、`_LIVE_FLAGS` 武装检测。`SCAN_SUBDIRS=["quantos","services"]`——扫描器源码全文无一处提及"live",排除是"未列入"非显式注释。匹配机制:imports 走 AST;credentials/APIs/flags 全是正则对文件全文字符串匹配。3 个理论绕过成立:动态导入、字符串拼接、变量间接赋值。live/ 补偿控制:18 个 `test_*boundary*.py` 多数点状检查,真正全树 walk 的只有 2 个,**不含 SafetyConfig 武装检查**——若 live/ 出现 `SafetyConfig(live_trading_enabled=True)`,无任何机器检查会抓到[推断]。真正"实例化并断言 raise"的运行时验证仅约 4/33 文件(12%)。`_git_sha()` 静默失败当场自证(worktree 场景下 `.git` 是文件非目录,裸 except 吞掉异常返回 None,与 §0 发现独立吻合)。

### 4.6 执行/实盘栈 —— 2.5/5

目录测绘:`exchange/{base,binance_futures,bybit_futures,retry}.py`=venue 接缝;`execution/{state_io,pricing,filters,slicer}.py`=定价/量化/切单;`execution/{reconciliation,parity,position_ledger,day_pnl,tca,price_check,market_stats,anomaly}.py`=记账/对账/成交质量;`execution/{emergency,risk_monitor,watchdog_check,evidence_escalation,crowding_score,gate_context}.py`=风控/告警;`governance/{mandate,mandate_store,review_queue,validation_attestation,kill_switch_store}.py`=人工授权四道门。风控/幂等/对账主链接近机构水准,但逐字对照顶级执行基础设施(WebSocket/主动限频/真算法执行/smart routing/STP/可观测性)几乎每项缺席——2.5 分是两者的诚实折中。

**[P0] 可观测性/日志/告警链几乎全空白——本次实盘自动化真实静默失败 ≥2 天,唯一发现方式是人工手敲 schtasks**
证据 [已核实,与 §0.1 同一事故的独立复现+关键修正]:即便 `QUANTOS_watchdog` 当时已注册,`watchdog_check.py`/`tools/watchdog.py:25-26` 的容忍窗口是**周度任务 9 天、哨兵 3 小时**——这次 2 天故障窗口远小于 9 天阈值,**注册了也不会对这次故障出声**,它真正设计要抓的是"停摆一周以上"。更深一层:`tools/watchdog.py:18` 自己 `from tools.live_track_advance import _STRATEGIES`——监控者与被监控者共享同一个"缺失 live/ 树"故障根因,若当时也被注册,它自己也会同一时刻崩溃、不写状态文件,且**没有"元 watchdog"发现 watchdog 本身失踪**。`register_schedules.ps1` 全部 `/tr` 零输出重定向,原始 traceback 从未被任何机制捕获保留。
修补:①注册剩余 6 个任务(含 watchdog/backup);②`/tr` 加输出重定向;③watchdog 容忍窗口按任务类型分级收紧;④watchdog 自身依赖最小化(不 import 被监控模块业务逻辑),消除相关性故障。工作量:XS→S→S→M。

**[P1] `live_track_advance.py` 无 catch-up 机制(与 `forward_track_advance.py` 不对称)——本次故障已导致两所测试网真实错过一次调仓**
证据 [已核实,sqlite 精确核对]:`forward_track_advance.py` 是状态相关的 due-check(锚定上次决策数据时间戳,非墙钟),经核实这次故障对它零实际损失。但 `live_track_advance.py::_latest_weights_and_prices()` 无条件取最新面板行,不比较"距上次决策过了几天",完全靠外部触发节奏。直接查库:`live_track.db`(Binance)最后一笔成交 **2026-07-06T01:06:40 UTC**,`live_track_bybit_demo.db` 止于 07-05T07:20,两所**自故障起到今天零新增记录**——证实"该周原本要发生的调仓真的没发生"。$3-5k/周频调仓下金额影响可忽略,但这是没有自愈能力的结构性设计,会在未来任何调度故障时重演且无告警。
修补:S-M(1-2 天),复用 `forward_track_advance.py` 的 due-check 模式。

**[P1] 核心下单决策路径(读仓位→算目标增量→提交)无跨进程锁**
证据 [已核实]:`live_ops_api.py:81` 的 `threading.Lock()` 只防同进程内并发,不能防"人工点击运行"与"schtasks 定时触发"两个独立 OS 进程同时跑。全树无 pidfile/flock/DB advisory lock。已知的 day_state/peak_equity/kill_switch 竞态修复未覆盖这条核心路径。未观测到已发生,下一层对账 hard-stop/mandate 上限会事后拦截,但是没有事前防护,只有事后止损。
修补:S(半天),入口加跨进程文件锁/sqlite 咨询锁。

**[P1] 治理扫描器结构性不覆盖 live/ 全树**(与 §4.5 独立复现)——33 个文件里只约 4 个有结构性边界测试,其余 29 个只靠常规 pytest。当前代码验证过 0 违规,但未来任何编辑若引入扫描器本该拦的模式,无自动化会拦下来。修补:S,把 live/ 加进 SCAN_SUBDIRS。

**[P1] 手续费在 live 执行链路零建模;资金费未与交易所历史 true-up——归因精度缺口,非风控盲区**
证据 [已核实]:live/ 全树搜 `fee|commission|maker|taker` 零命中,`funding` 在真实下单路径零命中。但风控判断是准的(`equity` 直接取自交易所真实账户 `totalEquity`,资金费/手续费本来就会实际扣减钱包余额)。`gonogo_report.py:59-64` 注释自认"execution_cost_estimate 是近似,非精确账实拆分——本阶段刻意不引入"。$3-5k 下金额不重要(几美分到几美元/周),但**同一项目里"纸面"干跑记账比"真钱"live 记账更精确**这个反差值得注意——支持 Go/No-Go 决策的数字目前无法回答"这周赚的钱有多少是资金费、多少是价格方向对了"。
修补:M(2-3 天),从交易所成交回报/资金费历史接口拉真实字段独立记账。

**[P2] 顶级执行工程对照表(逐项标注 $3-5k 下是否重要)**:算法执行(TWAP/POV,slicer.py 只做数量切分非真 TWAP,生产从未启用)不重要;实时 TCA(真实生产链路,喂 Go/No-Go 报告)部分重要;smart order routing(0 命中,双所有意隔离非选路)不重要;self-trade prevention(未设置但两交易所原生支持,零成本)基本不重要但建议顺手做;多 venue failover(有意设计双所独立不互为备份)不重要。

**[P2] LIMIT 单基础设施建好测过(GTC/GTX、marketable-limit 定价、超时撤单回查、部分成交入账,含一次真实联调修复的 Bybit 状态词归一化 bug),但生产 100% 是 MARKET 单**——已注册排程任务未带 `--order-type` 参数。$3-5k 下用 MARKET 合理,但若未来汇报把 LIMIT 能力当"已在用于降冲击成本"不成立,建议文档标注"已测试可用、当前未启用"避免认知漂移。

**[P2] 纯 REST 轮询、被动指数退避(无 jitter)、recvWindow 仅进程启动同步一次**——三项在当前每周一次调仓节奏下都不重要,规模化到日内/高频才会咬人,不建议现在投入。

**执行栈最被高估**:register_schedules.ps1 注释"死人开关静默失败会大声告警"的设计承诺——不仅未注册,即便注册 9 天容忍窗口也抓不住这次量级的故障,文档语气给出的保护感比代码实际能提供的高一大截。同类高估:`forward_track_advance.py` 的 catch-up 能力容易被泛化理解成"整个 live 调度体系都有这个安全网",实测 `live_track_advance.py` 恰恰没有。

**执行栈最被低估**:幂等+崩溃恢复+对账硬停三件套被真实验证过闭环,不是纸面设计——`client_order_id` 碰撞修复三层调用链核实确实生效;`resolve_unconfirmed_orders` 每次启动最先执行;对账 hard-stop 在 2026-07-04 真实联调中**实测抓到过一笔交易所侧遗留的 0.002 BTC 测试仓位**并正确拒绝新单。这类不产生亮眼 Sharpe 数字的底层管道工程,恰恰是区分"业余脚本"和"能拿真钱跑"的分水岭。

**[关键,离首笔实盘最近的现存硬闸]** `key/live_validation_attestations.json` **当前不存在**——`validation_attestation.py::require_validation_attestation` fail-closed 逻辑意味着 **crowd_contra 对任何 `--submit` 都会被拒绝**(`no_validation_attestation:crowd_contra`),与 `PHASE_3A_GO_NO_GO_CHECKLIST.md` B2 项完全对应。这是当前唯一结构性挡住真提交的闸,不是"证据不够"而是"少签一份准入声明"。

**[关键,证据钟到期日可能需要修正]** [推断] 观察到的首两次决策间隔可能因"OI 滞后回看+纯周一触发"耦合拉到接近 11 天而非 7 天——若此模式持续,8 周证据门的实际达标日期可能比之前估计的 2026-08-27 更晚。建议以 `forward_track_status.json`/每日 `gonogo_report.json` 的**实际累计决策数**为准,不要单纯按日历倒算。

### 4.7 平台/API/基础设施 —— 2/5

**[P0] 告警出口从未真正配置——`alert_sink.py` 设计正确但对外哑火,包括一条真实 ESCALATION 事件**
证据 [已核实]:`tools/alert_sink.py:19-30` 需要 `QUANTOS_TG_BOT_TOKEN`+`QUANTOS_TG_CHAT_ID`;实测三级环境变量均未设置。`data/cache/alerts.jsonl` 里有一条 2026-07-07T11:30 UTC 记录,内容是 `ESCALATION: live-paper parity gap 7.1% of AUM ($358) exceeds 5% threshold`——代码自己判定为"升级"级别,实际只安静写进本地文件。全仓库唯一设计的出站通道是 Telegram;邮件/短信/Slack 均无集成。
差距后果:不是能力缺口,是执行遗漏,且直接放大了所有其它 fail-closed 控制的价值(gate 拦住坏订单后,人仍需被告知才能处置)。
修补:注册 Telegram bot,设两个环境变量,验证收到测试消息。工作量:XS(<30 分钟)。**本次审计 ROI 最高的一条,没有之一。**

**[P0] 唯一的外部看门狗从未被排程,且设计上覆盖不到常驻服务**
证据 [已核实]:`register_schedules.ps1:52-53` 定义了 `QUANTOS_watchdog`(HOURLY),但不在已注册的 5 个任务之列;`watchdog_status.json` 的 `ran_at`≈2026-07-05T11:49:46 UTC 是全仓库唯一一条运行记录,**早于**已知的两天故障窗口——故障发生全程,watchdog 一次都没跑。即便补跑,`tools/watchdog.py:56-69` 只认"会写状态 JSON 的周期任务"的新鲜度,`live_ops_api`(ONLOGON 常驻 uvicorn)不写状态文件也无端口/进程存活探测——它本次真实失败结构性不在 watchdog 检查范围内。
修补:补注册 `QUANTOS_watchdog`;给 `live_ops_api` 加 `/health` 端点 + HTTP 探测。工作量:XS+S(半天)。

**[P1] schtasks 架构性脆弱:硬编码磁盘路径+不锁分支/commit+零输出重定向——不是一次事故,是可被任何操作重现的设计缺陷**
证据 [已核实]:5 个已注册任务的 `Task To Run` 均硬编码磁盘路径,不校验 checkout 分支/commit;全部 11 条 `/tr` 定义无一条含输出重定向,Windows 计划任务默认丢弃 stdout/stderr 只留退出码。二者叠加:任何人在这台机器上做任何无关的 `git checkout`/`worktree` 操作,都会**静默**复现"排程任务全部失败"且失败原因永久不可考——本次能查明纯属"这次恰好好排查",非机制保证。
修补:①每条 `/tr` 追加日志重定向(1-2 小时);②任务入口加 commit 校验,不符预期直接 exit+告警。工作量:S。

**[P1] 灾备"3-2-1"两条都不满足:每日备份任务未注册+备份目标与生产同一物理盘**
证据 [已核实]:`data/backup/` 下只有 2 个"改动前手动存档"目录,非按日累积产物;`QUANTOS_backup`(DAILY 03:00)未在已注册 5 个任务之列。`live_backup.py` docstring 明写"真正灾备应指向不同物理磁盘",但 `main()` 默认 `dest_root` 仍在 H 盘,调度层从未真正用过异地能力。
差距后果:资金托管在交易所而非本地(本地丢的是账本/mandate 状态/审计连续性),H 盘整体故障时账本/mandate 状态一起丢失,重建环境可能在无历史记忆下重新签出敞口。
修补:补注册 `QUANTOS_backup`;`--dest` 指向第二块盘或云同步目录。工作量:XS(10 分钟)。

**[P1] key/ 目录暴露面比既有认知更大:确认存在会读取它的非 Administrator 账户,且 3 个 API 令牌也放在同一目录**
证据 [已核实]:`Get-LocalUser` 实测本机 7 个本地账户、3 个 Enabled——`Administrator`(唯一 Administrators 组成员)、`CodexSandboxOffline`、`CodexSandboxOnline`;`Get-LocalGroupMember -Group Users` 证实后两者是 `BUILTIN\Users` 组成员——而安全维度发现的 ACL 问题原文本身就是对 `BUILTIN\Users` 开了 ReadAndExecute。即:安全维度"一旦出现第二个账户"的假设条件**此刻已经满足**,不是未来才触发。`research_runner`/`promotion_api`/`live_ops_api` 三个服务的操作令牌文件也放在同一目录,ACL 问题实际暴露不止交易所密钥。
修补:与安全维度 ACL 修复合并为同一条命令;顺带确认两个 Codex 沙盒账户是否需要保持 Enabled。工作量:XS。**建议与安全维度同款修复一起、尽快做。**

**[P1] 四个 API 服务零远程可达性——操作员离开这台机器后看不到仓位、按不了 kill-switch**
证据 [已核实]:console_api、live_ops_api 硬编码绑定 `127.0.0.1`;research_runner/promotion_api 同样只本机可达。runbook 全文搜"远程/VPN/手机"零命中;仓库内搜 ngrok/tailscale/frp/nginx 均零命中。四服务鉴权本身扎实(`secrets.compare_digest` 常量时间校验+独立高熵令牌+CORS 严格限定+全部 loopback-only)——问题是压根出不了这台机器的网卡。
差距后果:今天唯一远程干预路径是通用 Windows 远程桌面(是否对公网开放不在本次范围,建议单独核实)。与"排程静默失败两天无人知晓"故障互为表里:即便当场发现异常,不回到机器前也处理不了。
修补:给 `live_ops_api` 加一个只读 Telegram bot(查仓位/权益摘要);kill-switch **触发**建议仍留本机自动判定,不建议开放远程触发以免新增攻击面。工作量:S(1 天)。**$3-5k 规模下少数真正值得补的可用性缺口。**

**[P1] 生产排程任务实跑 Python 3.13.9,与全部钉版本声明(3.12)不一致,该组合从未被 CI/治理扫描器验证过**
证据 [已核实]:`H:\miniconda\python.exe --version` 实测 3.13.9,已注册排程任务(含真实提交测试网订单的 `live_track_binance --submit`)全部用这个解释器跑;而 `.python-version`=3.12、CI 用 3.12,本机找不到任何 3.12 解释器,CI 从未在 3.13 上跑过一次。
修补:CI matrix 加 3.13,或把 pin 改成 3.13 并跑一次全套件确认无回归。工作量:S(半天)。

**[P2] 无数据恢复演练脚本**(容易与已有的熔断演练 `KILL_SWITCH_DRILL.md` 混淆,后者演练的是熔断链路不是数据恢复)。修补:S。

**[P2] 全部 13 个 SQLite 库均为 rollback-journal(非 WAL),3 个真实进程共享同一账本文件**——但 Python sqlite3 隐式默认 `timeout=5.0` 已部分兜底,实际碰撞窗口小,是少数"看起来该 P1、证据支持降 P2"的项。修补:加 `PRAGMA journal_mode=WAL`。工作量:XS。

**[P2] `prometheus_client` 是"愿望式依赖"**——声明了但四个真实 API 服务均无 `/metrics` 端点,唯一 import 在已隔离的非功能模块里。$3-5k 规模下 Prometheus+Grafana 属于过度工程,不建议现在补,如实记录以防误读。

**[P2] 供应链/CI 尾部项,诚实评估当前不值得投入**:无 lock 文件但依赖面本身很浅(纯 stdlib+8 个直接依赖,均精确 `==`);CI 不阻挡 push,但当前工作方式本就是直接 push 非 PR 流程,单人项目上这项投入 ROI 低;TLS/限速当前不需要(流量不出网卡),但若未来任何服务改非 loopback 绑定,须同批补上——建议写进 runbook 检查清单,不用现在动代码。

**平台层最被高估**:`alert_sink.py`(本地 jsonl+可选 Telegram)和 `watchdog.py` 死人开关给人"这套系统有监控"的第一印象,但实际运行时间趋近于零——**代码质量和运行状态之间的落差,比"压根没写监控代码"更危险**,因为前者会让人误以为已覆盖,后者至少不会制造虚假安全感。

**平台层最被低估**:三个 mutating API 的令牌鉴权工程质量相当扎实(常量时间比较+独立高熵令牌+CORS 严格限定+清一色 loopback-only),是刻意的"零暴露面"设计取舍;`live_backup.py` 本身已支持 `--dest` 换盘参数——难写的代码早就在了,缺的只是调度层一次参数配置。

### 4.8 AI/智能自治层 —— 1.5/5(全审计最低分)

**四象限速览**(顶级 AI 量化的 AI 用在信号/执行/风控/研究助手四格,QUANTOS 每格差距):

| 象限 | 顶级做法 | QUANTOS 现状 | $3-5k 下值得先补吗 |
|---|---|---|---|
| 信号/Alpha 生成 | 大规模学习模型+海量并行候选搜索(10⁴-10⁶+/campaign) | 经典 UCB1/MCTS/GP,单线程,历史累计约数百次评估;在途两条边(carry/crowd_contra)来自人工/dev-LLM 手写诊断脚本,非搜索引擎产物;唯一学习型方法(GBM+Ridge stacker)已诚实证伪弃用 | **不值得**——项目自己证据已说明换更强模型不是当前瓶颈 |
| 执行 | 常含学习型冲击预测/拆单路由 | 零 AI,纯规则 | 不需要 |
| 风控 | 行业本身以规则+统计为主 | 纯规则 fail-closed | 不是 AI 该补的地方 |
| 研究助手(开发期) | 渐成常态 | **全仓库实际最强的一格**:605 提交里 544 条(92%)挂具体 Claude 型号署名 | 差距小,但**复核机制**差距大 |

**一句话诚实结论**:QUANTOS 唯一称得上扎实的 AI,是**写代码的 AI**(dev-time LLM),不是**跑研究的 AI**(runtime bandit/MCTS/GP)——后者算法教科书级正确但规模玩具级、从未真正驱动过一次实际交易决策;前者高产但几乎没有独立于自身的复核环节。"AI 量化研究平台"准确说法应是"由 AI 编程助手大量协助搭建的、内含经典启发式搜索工具的量化研究/安全工程平台"。

**[P0] 自治晋级闭环从未处理过一个真实候选——"两个宇宙"从不握手**
证据 [已核实]:`services/alpha/{catalog,lifecycle,evaluator,promotion,retirement,maintenance}.py`(26+模块/185+测试)链路本身干净,但服务对象是 `crypto_research_offline.py::regenerate()` 每次整库删除重建的一次性演示面板(`experiment_registry.db` 的 `alpha_state` 60 行全 `TESTING`、同一毫秒时间戳)。实际走向 dry-run/实盘的 carry、crowd_contra 是裸 Python 常量,流向 `forward_track_advance.py`/`dry_run_*.py`/`live_track_advance.py`——这些文件 grep `services.alpha.{catalog,lifecycle,promotion,registry}` **零命中**。发现侧同样断裂:`crypto_factory_loop.py` 自己的产物写明 `"containment": "research; drives no lifecycle"`。退役侧代码真实且正确接线到"停止加仓"开关,但 `_seed_manager()` 每轮重建全新内存 catalog,`alpha_maintenance_status.json` **磁盘上不存在**——sweep 从未真正跑过一次。
修补:把 carry/crowd_contra 注册进 AlphaCatalog(`promotion.py::gather_promotion_evidence` 已正确读取真实 paper-track 数据,只差 `catalog.register()` 这一步)。工作量 M(几天到 1-2 周)。若短期不做,至少去掉文档里"这套 catalog 治理着实际交易决策"的暗示,工作量 S。

**[P0] "非人到不了 APPROVED" 在多个层级只是自报字符串,不是身份认证——4 个独立层级发现同一模式**
证据 [已核实,交叉验证]:`quantos/contracts/alpha.py:21-38` `Actor` 是无签名 frozen pydantic 模型,任何 importer 可构造 `Actor(kind=ActorKind.HUMAN)`;`catalog.py:132-140` `_set_state` 两行字典写入零运行时校验;`guards.py::guard_to_approved` 靠字段比较+可伪造 `ApprovalRecord`;`approval.py::verify_multi_party_grant` 是最深 chokepoint 但自己承认"不同"=不同字符串非认证真人。**最突出**:`services/governance/promotion_api/main.py:131-136,249`(本人直接核实)——`_operator()` 只对 `X-Operator` 请求头 `.strip()` 后原样铸造 `Actor(kind=HUMAN)`;模块自己 docstring 写明持有 gov token 的任何本机进程——**"包括 AI agent"**——可用三个自报标签走完三方批准,"零代码变更、零 code-review 痕迹"。**这是代码库自己诚实公开承认的攻击面**。同一模式在开发溯源层再现:git `Co-Authored-By` 是纯文本约定,无签名验证。
差距后果:CLAUDE.md 的威胁模型声明("单进程内挡不住受信代码")是准确自我认知,六个文件 docstring 独立重复确认是承重设计意图。真正结构性做到防伪的**只有 `signing.py` 一处**(`verify_signed_ref` 不存在任何 return True 路径,伪造权威对象也撞 `raise SigningUnavailable`,绕过需改函数本身而非构造对象)——其余全靠约定。
修补:L2 阶段现状合理(promotion_api 默认不启动、mandate 未接入 live 决策路径);放行真钱前须把 Phase 14 出进程签名接进 mandate 变更(已知已文档化前置项,非本轮新发现),工作量 L。本轮新增值是把这个模式在 4 层的重复点破,便于一次性根治而非逐点打补丁。

**[P0] 实际开发分支(master)自 07-03 唯一一次人工复审后,再无可验证的人工复审动作**
证据 [已核实]:全仓库 605 提交只有 2 个 merge commit,其中只有 1 个是可辨认的人工 GitHub PR 合并(唯一人类账号 kerst_ker,落在 `main` 分支);另一个是 AI 身份直接执行的 `git merge`(非 PR)。此后 `master` 独立推进 100+ 提交(含最近 5 天几乎全部 live/ 风控/治理改动)**没有第二个可辨识的人工复审动作**——**包括本次会话自己把 master 快进合并到 c6dc7d2 这一步,同样是"用户拍板、代理直接执行 git merge",不经 PR**。`.github/CODEOWNERS` 存在且内容扎实(`@kerstker` 拥有 `services/risk/`、`live/governance/`、`quantos/contracts/` 等安全关键路径),但其强制力依赖 GitHub 服务端"Require review from Code Owners"设置——**这一设置不在仓库文件里,本地无法核实是否开启**。
差距后果:"AI 写的安全代码谁复核"的诚实答案是——自 2026-07-03 起没有任何可验证的人类复核动作,包括决定这套系统能否碰真钱的绝大部分风控/治理代码。不代表代码是错的(测试覆盖扎实),但"人工运维介入"(如用户本次发现并拍板修复排程分支问题)不能替代结构化代码复审,两者不是一回事。
修补:确认/开启 `master` 分支的 GitHub"Require review from Code Owners"保护。工作量 XS(几分钟,纯 GitHub 设置,零代码改动)。**本次全审计性价比最高的一条,没有之一**(与 §4.7 的 Telegram 告警并列第一梯队)。

**[P1] 运行时零 LLM——"AI 量化"招牌名不副实(诚实但需要正名)**
证据 [已核实]:`services/intelligence/**`/`services/research/**`/`live/**`/`tools/**` 全文 grep LLM 相关关键词除一处论文引用注释外零真实命中;无任何 LLM SDK 依赖声明;LLM API 域名全仓库零命中。运行时"智能"清单(UCB1 bandit/guided search/evolutionary search/MCTS/GP 表达式树)全部是前 LLM 时代经典启发式算法,教科书级正确实现,但没有一个是学习型/LLM 模型。唯一学习型方法已诚实证伪且零处被 live/ 引用。
修补:S(文档),拆分表述为"研究平台由 AI 编程助手大量协助构建" vs "运行时使用经典自动化搜索,不含学习型/LLM 信号生成"。长期投入学习型信号不建议现在做——项目自己的 stacker 证伪结论已说明当前数据/信号类瓶颈未解决前这笔投入大概率复现同一结论。

**[P1] evidence=/leakage_ok 对"缺失"fail-closed,对"伪造"零防御,且从未红队验证过文档承认的核心绕过**
证据 [已核实]:`evaluator.py:77-87` 的 `leakage_ok`/`pbo`/`regime_profitable_frac` 是裸类型化关键字参数,该文件不 import `detect_leakage`,唯一逻辑是"None→REQUIRES_HUMAN_REVIEW"——只捕获缺失,对伪造(直接传 `leakage_ok=True`)零拦截。真实检测器存在且被搜索循环调用,但输出是未绑定裸 `Optional[bool]`,没有哈希/签名绑定到具体计算,一旦返回和手写 `True` 无区别。`validation/gate.py` 自己 docstring 承认"调用方必须自行保证 provenance"。范围限制:伪造 evidence 最多推到 PAPER_TRADING/人工复核,到不了 APPROVED。红队现状:`tests/governance/` 24 文件里 6 个纯运行时对抗(含 `test_approval_store_tamper.py` 8 种攻击场景全部 fail-closed,真实高质量);但**全仓库没有一个测试直接拿伪造 HUMAN Actor 调 `catalog._set_state`**,把 CLAUDE.md 点名的绕过实际演练并记录结果——是"已承认且推迟",不是"已演练"。
修补:(a)给 evidence 加最小完整性绑定(携带计算输入哈希的不可变对象),工作量 M(2-3天);(b)补一个显式红队测试固定当前防线真实范围,工作量 S(1天)。

**[P1] 自治系统从不自评决策质量——无 regret、无校验、无学习曲线**
证据 [已核实]:全仓库 grep regret/oracle/calibration/brier/learning_curve 等关键词零命中。`UCBSearch` 只保留最终快照不追踪逐轮悔憾。`degradation_monitor`/`maintenance.py` 的 `_LIVE_STATES` 只监控已晋级 alpha,回答"这条边现在还好吗"而非"过去的通过/拒绝判断准不准"——**被拒绝的候选从不进入 paper-track**,"gate 说 FAIL 的候选如果真上了会不会其实是好的"这个问题现有代码结构下根本无法回答。
修补:(a)`UCBSearch.update()` 加逐轮悔憾记录,工作量 S(数小时);(b)被拒绝候选做小比例影子 paper-track,工作量 M(几天)。

**[P1] 挖掘规模玩具级+零并行化——历史累计约数百次评估,单线程**(与研究/Alpha 层 §4.3 独立复现同一发现)
证据 [已核实]:全文 grep 并行化关键词零命中(唯一命中是研究控制台的任务级队列,非搜索循环内部并行)。`crypto_factory_loop.json` 最近一次真实运行两轮后零新发现即提前终止;`experiment_registry.db` 60 行评估全部同一毫秒时间戳。
差距后果:"0 条新正交边"更可能反映采样不足而非假设空间真被穷尽。
修补:S(数小时到 1 天),纯函数天然可并行,包一层 `ProcessPoolExecutor` 即可——两轮独立审计都认定的高性价比修补项。

**[P2] Kill-switch 在研究/干跑侧默认路径纯内存、未闩锁——"重启进程"原则上是绕过(当前无害,不碰真钱)**——`live/governance/kill_switch_store.py` 的持久化路径本身扎实,但研究/干跑侧 `pre_trade/gate.py`/`dry_run_carry.py` 默认构造全新未持久化开关。没有结构性机制强制走强 reset 路径,只有工具层约定。修补:把持久化模式提升为默认行为。工作量 S-M。

**AI 自治层最被高估**:①"26 模块/185 测试的 alpha 晋级生命周期"——听起来驱动实际交易决策,实际从未处理过一个真实候选;②"非人 actor 结构性到不了 APPROVED"——在具体路径上是真的,但作为安全承诺被广泛引用时容易过度泛化,包括一个模块自己文档里就写明"AI agent 可用"的绕过;③"AI 量化"品牌本身——运行时零 LLM;④Bandit/MCTS/GP 的算法复杂度——项目自己的证据已说明当前阶段决定成败的不是搜索算法多精巧,是有没有边、数据够不够干净。

**AI 自治层最被低估**:①dev-time LLM 的工程与研究纪律——算法教科书级正确、统计纪律扎实、诚实标注文化贯穿全仓库、fail-closed 几乎默认姿势,近 2000 个测试,工程纪律高于很多有资金的团队;②提交级 AI 溯源完整度——605 提交 92% 挂具体型号署名,安全关键子集覆盖率 100%,比大多数纯人类团队的可追溯性更细(但这个信号本身是自陈述、未加密验证,与上面身份认证问题同一模式);③`live/` 侧安全工程扎实度——mainnet 适配器结构性拒绝、`_VENUES` 表压根没有 mainnet 条目;④用户本人的运维介入——本会话开场就是用户发现排程指向缺代码分支并当场拍板修复,真实及时,但不能替代结构化代码复审。

**方法论附注**:[已核实] rtk hook 会静默截断特定管道模式(`git log | wc -l` 返回 50,`rtk proxy` 或重定向到文件后返回真实值 591)——任何经 rtk 直接管道计数的审计结论都应视为不可信,需用 `rtk proxy`/文件重定向/专用工具复核。已计入 CLAUDE.md 既有的"rtk pytest gotcha"同类教训。

### 4.9 前端/运维可视化 —— 2/5

数据基本是真的,但看错 kill-switch、按不动开关、叫不醒人——三个窄而深的坑,不是地基问题(前端工程纪律本身真实过硬:CI 跑 typecheck+build+test、`tsconfig strict:true`、零 TODO/FIXME)。审计视角:不是"研究平台前端好不好看",是"真钱在场上时操作员靠这个前端能不能活下来"。

**[P0-1] 两个标榜"REAL"的安全看板,kill-switch 状态结构性读错,不是过期而是恒错**
证据 [已核实,亲自读码]:`console_api/main.py:596-608`(喂 `/monitoring-drift`)与 `:4031-4042`(喂 `/safety-primitives`,Risk.tsx 消费,标 `REAL_SAFETY_PRIMITIVES`)都写 `KillSwitch()` 不传参数——`__init__(self, state=None)` 永远拿到全新"从未触发过"的默认状态。真正跨进程持久化的实盘状态在 `live/governance/kill_switch_store.py::load_kill_switch_state`,而 `console_api/main.py` 全文件**零次**引用它。已确认 console_api 与 `live/execution/risk_monitor.py` 引用的是**同一个** `KillSwitch` 类,不是两套无关代码。`_real_monitoring_snapshot` 自己 docstring 写"replacing a purely hardcoded health view"——开发者本意是展示真实状态,接线时忘了传 `state=`,是回归性 bug,非故意的诚实合成标注。
差距后果:操作员打开标着"REAL_SAFETY_PRIMITIVES"的 Risk 页,即便实盘 kill-switch 已真实跳闸,该页面大概率仍显示"未触发/健康"——**全份审计里最危险的单一发现之一**:不是数据旧,是看的根本是另一个假对象。
修补:2 处调用点各加 3 行(加载 `load_kill_switch_state`)。工作量:小(<1 小时),**必须先修,是本报告唯一"今天就能改完"的 P0**。

**[P0-2] ~30/34 个 console 页面挂载后永不重新拉取数据;CommandCenter 的"安全"卡片把错误永久伪装成"加载中"**
证据 [已核实]:全 `src/` grep 轮询/心跳相关关键词——除 `LiveDesk.tsx`(8s)、`ResearchRuns.tsx`/`RunDetail.tsx`(条件轮询)外零命中。`Risk.tsx`/`CommandCenter.tsx`/`LiveTrading.tsx` 均只挂载时 fetch 一次。`useConsoleSnapshot.ts` 首次失败行为正确,**但这条防线只在"发生过一次 fetch"时生效**——挂载后端后台挂掉,`apiState` 永远停在 `'connected'`。更严重:`CommandCenter.tsx` 未导入 `ProvenanceRibbon`,其 `SafetyTile`(**就是展示 kill-switch 状态的卡片**)判断逻辑是 `payload===null?"载入中…":...`——**从不检查 apiState**,后端挂掉后永久显示"loading…"而非错误,比冻结在旧数字上更危险,因为它主动误导("再等等"而非"这里已经死了")。
差距后果:用户明确点名的"最危险场景"(后端断了、前端静默保持旧画面)在 34 个页面里约 30 个是真实存在的,非假设性风险。
修补:通用(中,1-2 天)给 `useConsoleSnapshot` 加默认心跳(30-60s)+ `visibilitychange`/`focus` 触发重新校验;`CommandCenter.SafetyTile` 单独小修(<1 小时)。

**[P0-3] 告警到人只有一条可选、失败静默吞掉、无人验证是否真的在跑的 Telegram 软通道——很可能半夜爆仓没人知道**
证据 [已核实]:全仓库搜 smtp/email/webhook/slack/sms/twilio/pagerduty 零命中(与 §4.7 平台层独立复现同一发现)。`alert_sink.py::_telegram_push` 任何异常被静默吞掉,无重试无备用通道,docstring 自认"push 只是通知,`alerts.jsonl` 才是权威记录"——真正的记录从不主动找人。触发链条也非自动:`risk_monitor.py::respond_to_breach` 只做闩锁+审计写入,**不调用 alert_sink**;告警能发出去纯粹因为调用方各自额外拼字符串调 `emit()`。而这些驱动器能跑,依赖 schtasks 计划任务(与 §4.5/§4.6/§4.7 三维度独立复现的排程问题同源)——**没有任何代码验证计划任务真的注册、真的还在跑**。前端侧无 toast 库、零 Notification API,任何前端提示要求浏览器标签页开着。
差距后果:若 Telegram 未配置、计划任务失效、或 push 恰好因网络问题失败——当前系统没有任何东西会让操作员知道。$3-5k 绝对金额不大,但"完全没人知道出事了"本身不可接受,与金额大小无关。
修补:中(2-3 天),给 push 加失败重试+失败信号;补第二通道;给计划任务加自检心跳。

**[P1-1] 实盘操作面板(LiveDesk+live_ops_api)没有任何撤单/平仓/kill-switch 复位入口——100% 走 CLI,且这是机器强制的设计,不是遗漏**
证据 [已核实]:`live_ops_api.py` 全部 5 个端点(`/dashboard`、`POST /run`、`/demo-funds`、`/pending-review`、`/pending-review/{id}/decide`)。治理测试 `test_ops_api_never_places_orders_directly` **机器钉死**源码中不得出现 `.place_order(`/`.cancel_order(`/`submit_live_batch`/`submit_emergency_flatten`。唯一"操作"是 `POST /run` 以固定 argv 子进程跑与定时任务完全相同的 `live_track_advance`,不是针对某笔单撤单或一键平仓。
差距后果与严重度校准:$3-5k、单人、操作员本人有终端权限场景下,"没有 UI 按钮只有 CLI"本身不是不可接受——同一人 10 秒内能敲完命令。真正风险不在"没按钮",在 P0-1(看不准状态)和 P0-3(收不到通知)——**这两个先修好,P1 而非 P0 是合理定级**。加仓/多操作员阶段须重新评估。
修补:若要做,给 `live/execution/emergency.py` 已有的独立"只减仓"通道包一个端点,复用同样固定 argv+治理测试模式。工作量:中(2-3 天)。

**[P1-2] "三个不同真人"的多方批准,目前等价于"一个人敲一条 CLI 命令、传三个字符串参数"——全系统无真实身份认证**(与 §4.5/§4.8 独立复现同一模式)
证据 [已核实]:`approval.py::_require_human` 只检查 `actor.is_human` 布尔字段,而 `Actor` 由调用方自己构造。唯一调用 kill-switch 复位的入口 `live_kill_switch_reset.py` docstring 自认"三步在同一次调用里连续执行……不是三个人分别登录操作"。浏览器唯一能点出多方审批全流程的页面 `PromotionQueue.tsx`(与实盘无关),对接 `promotion_api`(默认不启动),鉴权是单个共享 token+自由文本字段,页面自己有个不可折叠的常驻警告条承认"任何持有 token 的本地进程——明确包括 AI agent——都能自报三个身份走完全程"。`console_api/main.py:3505-3517` 自己的架构自省端点承认"需要真实 auth provider"。
差距后果与校准:$3-5k 单人场景下"没有真正的三个人"某种程度是必然的;问题不在做不到三人,在**代码/文档对外宣称"多方哈希链批准"制造了审计严谨性假象**,一旦多人协作或合规展示,假象会被戳穿。当前实际风险是"审计记录里的身份字段不可信",非直接导致资金损失。
修补:短期(几小时,纯文档/UI 文案)去掉"三方独立人工批准"措辞,如实写"当前=单操作员自证三步";长期真身份认证是 Phase 14 范畴,工作量大,文档已承认不重复列。

**[P1-3] LiveTrading.tsx(TCA/对账页)反映周度驱动器快照,挂载后不再刷新,只有绝对时间戳无过期提示**——比 P0-2 温和(持仓/余额/PnL 核心真相在 LiveDesk 是真实时的),但执行质量分析可能是一周前结果却无从判断。修补:小(<1天),`stamp()` 包一层年龄计算,超期变色+提示。

**[P1-4] 风险门拒绝/异常无任何聚合或连续告警逻辑**——`rejected_decisions` 字段在唯一真实调用点被硬编码为 0(与 P0-1 同一段代码)。差距后果:门本身 fail-closed 非资金直接风险,而是"策略因 mandate 配置错误被连续拒绝、操作员毫不知情"的虚假安全感风险——以为有敞口保护,实际策略静默停摆。修补:中(1-2天),加"过去 N 次运行连续 M 次拒绝"计数器。

**[P1-5] 全前端零 React Error Boundary——渲染异常=白屏**——属于"失败得明显"(不会被误认正常),但白屏时 operator 连别的信息都拿不到。修补:小(<1天),加顶层 ErrorBoundary。

**[P1-6] "谁监督监督者":watchdog/风险哨兵本身停跑,没有任何东西会发现**(与 §4.6/§4.7 独立复现)——`watchdog.py` 自己承认它本身也要被调度、不会自触发,而没有代码验证计划任务确实注册、确实还活着。修补:中(1-2天),任何仍在运行的进程定期检查其它任务最近运行时间戳,超期自报。

**[P2] 次要差距(不影响 $3-5k 阶段生死)**:CLAUDE.md"37 页"实际 34 个(文档过时);mandate 剩余额度未计算展示,要操作员自己心算;"挂单"实为账本最近订单历史非交易所当前挂单查询(LIMIT 未启用故当前后果小);4 个页面因遗漏 prop 即便真实连接也显示"合成数据"警告(方向安全但标签不准);`incident_ledger.py` 真实已测试但生产路径零调用点;无 websocket 是明确设计选择("weekly strategy 不需要"),诚实地说不是差距。

**前端最被高估**:34 个研究可视化页面的规模精致度,以及"危险按钮经治理 CLI 子进程"给人的"前端能操作实盘"印象——拆开看能点出的操作只有一个,触发和定时任务完全相同的周度再平衡驱动器,不是撤单/平仓/任何应急杠杆。这批页面对研究阶段价值高,对"真钱在场时操作员能不能活下来"贡献接近零。

**前端最被低估**:`LiveDesk.tsx` 背后 `_venue_dashboard` 对持仓/余额/未实现盈亏是**真正实时**(每 8 秒直接查交易所,非读快照/缓存);`test_ops_api_never_places_orders_directly` 这类治理测试机器钉死"绝不能直接下单/撤单",是非常克制正确的"白名单固定 argv 子进程"模式,比很多真生产系统"随手加一个下单 API"更安全;`assertConsoleSnapshot` 的 fail-closed-on-first-load 设计是本报告里少数"直接对标顶级也不丢分"的部分。真正的窟窿不在"数据是不是真的"(基本是真的),在"变假之后前端会不会说"、"kill-switch 那一格读的是哪个对象"、"出事之后有没有人会知道"——三个窄而深的坑。

## 5. 实盘路线图

（各维度"最被高估/最被低估"已并入 §4 对应子章节末尾，不再单列 §5 高估/低估清单）

### 全维度最高性价比修补清单(9 个维度各自提名的"今天就能做完"的项,合并去重)

在读完整份有序阻塞清单前,先看这 8 项——**总耗时不到 2 天,覆盖了本次审计里近一半 P0 的实际风险敞口**:

| # | 做什么 | 耗时 | 解决哪个 P0/P1 | 来源维度 |
|---|---|---|---|---|
| 1 | 修 kill-switch 仪表盘读错对象的 bug(2 处各加 3 行,加载 `load_kill_switch_state`) | <1 小时 | 前端两个"REAL"安全看板显示假状态 | §4.9 |
| 2 | 配置 `QUANTOS_TG_BOT_TOKEN`/`QUANTOS_TG_CHAT_ID`,验证收到测试消息 | <30 分钟 | 告警链路从未真正打通(已有一条真实 ESCALATION 被安静吞掉) | §4.7、§4.9 |
| 3 | `icacls key /inheritance:r /grant:r Administrators:F SYSTEM:F` | 几分钟 | key/ 目录 ACL 过宽,且已确认机器上有 2 个真实非 Administrator 账户 | §4.2、§4.5、§4.7(三处独立提名) |
| 4 | 补注册 `QUANTOS_watchdog`(死人开关本身) | 10 分钟 | 本次真实故障 21 小时无人发现的直接原因 | §4.5、§4.6、§4.7 |
| 5 | 确认/开启 GitHub `master` 分支"Require review from Code Owners" | 几分钟 | 自 07-03 起无可验证人类复核(含本次会话自己的分支合并) | §4.8 |
| 6 | 把 VaR/CVaR/HHI 三引擎接进 `gonogo_report`/`live_risk_sentinel` 做只读展示(代码已存在,只差接线) | 1-2 天 | crowd_contra 上真钱前无人能看到它的 VaR/集中度 | §4.4 |
| 7 | 给 `evolutionary_search`/GP 循环套一层 `ProcessPoolExecutor` | 数小时 | 自动挖掘历史累计仅数百次评估、单线程,"0 条新边"结论证据量不足 | §4.3、§4.8(两处独立提名同一修补) |
| 8 | 签 `key/live_validation_attestations.json`(crowd_contra) | 视人工判断 | 当前唯一正在拦截真实提交的硬闸(非代码工作,是签字) | §4.6、§0.1 |

### 就绪度总判

**能否在 8 周门成熟时上线:技术上可以,但不会自动发生。** 8 周证据钟本身在轨(2026-07-02 08:00 起跑,权威到期日 2026-08-27,建议按下方 buffer 预算到 09-01 前后);排程故障已修复且现场验证成功。但从"证据钟到期"到"第一笔实盘 $"之间还有约 20 项**必须由真人亲手完成**的动作(签署、开户、改 ACL),目前完成度低,且都不在 agent 可代劳范围内。三个最大拦路虎:
1. **人工签署链尚未走完,且比想象中长**——`crowd_contra` 的策略级验证准入声明(`key/live_validation_attestations.json`)至今未签,**当前就正卡住真实提交**(`live_track_status.json` alerts 原文可查);主网侧 mandate 需整条重签(per-venue,testnet 签署不能带到主网)。
2. **安全维度两个 P0 在主网 key 落地前必须堵上**:交易所 API key 权限代码从未校验、`key/` 目录 ACL 过宽——这两条都是"钱还没放上去时成本最低、放上去后成本最高"的典型项,建议排在主网 key 生成**之前**。
3. **监控/治理完整性缺口本身比这次具体故障更值得警惕**——11 个设计中的排程任务只注册了 5 个,**其中缺失的 `QUANTOS_watchdog` 恰恰是死人开关,本该在故障发生后 1 小时内大声告警,但它自己从未被注册**。即便本次修复生效,下一次某环节悄悄失败仍然无人第一时间知道,除非再做一次人工审计。

**一句话结论**:排程/代码层面的硬故障已修复并现场验证生效;真正决定何时能上线的,是用户自己那条尚未走完的签署链条,以及是否愿意先把监控盲区补上再谈主网。

### 有序阻塞清单(从今天到首笔实盘 $)

| # | 做什么 | 谁做 | 依赖 | 预计耗时 | 现状证据 |
|---|---|---|---|---|---|
| 1 | 验证排程修复生效(不要盲信 LastTaskResult 文字,`0x800710E0` 这类失败码 Windows 会显示"操作成功完成"的误导性文本;改看 `data/cache/*_status.json` 的 mtime 是否越过 07-08 17:35 向前走) | 用户亲手 | 无 | 10 分钟 | [已核实]18:01 `risk_sentinel` 现场验证成功,权益数据真实变动 |
| 2 | 手动补跑一次 `python -m tools.forward_track_advance`,收窄证据钟节奏漂移 | 用户亲手 | #1 | 5 分钟 | 幂等、手工补跑安全(runbook 明文) |
| 3 | 手动跑一次 `QUANTOS_ops_api`(仅登录触发,无固定 NextRunTime,机器不重新登录可能长期不再触发) | 用户亲手 | 无 | 5 分钟 | [已核实]LogonTrigger 无固定下次触发时间 |
| 4 | 排查 `live_risk_sentinel` 的 bybit_demo 腿时钟偏移(retCode 10002,请求时间戳与服务器差 6.05s,超 5000ms recv_window;binance 腿同轮正常) | 代码(agent 可做) | 无 | 数小时 | [已核实,现场验证时新发现的独立小问题] |
| 5 | `key/` 目录 ACL 收紧(`icacls key /inheritance:r /grant:r Administrators:F SYSTEM:F`) | 用户亲手 | 无 | 几分钟 | 安全+平台+治理三维度独立复现,且已确认机器上有 2 个真实非 Administrator 账户 |
| 6 | 建 `tools/verify_key_permissions.py`,接入 `--submit` 前置链 | 代码(agent 可做,需 review) | 无 | 2-4 小时 | 全仓库 0 处校验 apiRestrictions/ipRestrict/enableWithdrawals;本次审计 ROI 最高单项之一 |
| 7 | 标注废弃 `docs/phase3a/` 全套 15 文件(3 处物理位置,2026-06-23 建仓当天写的传统 CTP/FIX broker 上线流程,与加密现实无关,冻结"67% 已批准"15 天未动)+ 两份 07-01 Barra-lite spec/plan(见 §0.1)——防止误导签字人/误执行 | 代码(agent 可做) | 无 | 1 小时 | 真正维护、代码对齐的清单是 `docs/PHASE_3A_GO_NO_GO_CHECKLIST.md`(唯一提交 bb9a701) |
| 8 | **补注册剩余 6 个设计排程,`QUANTOS_watchdog` 优先**(其次 backup/gonogo_report/alpha_maintenance/factory_loop/ic_panel_refresh) | **用户亲手**(运行注册脚本=签署常驻自动化授权) | 无 | 10 分钟 | 全部 11 条命令已写好,只有 5 条被真正跑过;watchdog 缺席是本次故障"21 小时才被人工发现"的直接原因 |
| 9 | 每日灾备加密(`live_backup.py` 当前明文)+ `--dest` 指向第二块盘 | 代码 | 无 | 半天 | 明文复制交易台账+mandate,且备份目标与生产同一物理盘 |
| 10 | 回测→执行 VT Sharpe(carry 1.44→0.65 等旗舰数字)补齐管线字段或标注"人工计算、非管线输出" | 代码 | 无 | S 应急标注/M 根治 3-6 天 | `dry_run.db` 无 `sharpe_vt` 字段,数字不可被任何命令复现 |
| 11 | B1·逐 venue mandate 字段复核签署(testnet 已签) | 用户亲手 | 无 | 视复核深度 | `key/live_mandate*.json` 已有真实内容,复核=确认数字仍是真实想要的 |
| 12 | **B2·签 `key/live_validation_attestations.json`(crowd_contra)** | 用户亲手 | 无 | 依验证判断而定 | **当前正挡住真实提交**,是离首笔实盘最近的现存硬闸 |
| 13 | B3·firm 硬门 3 个数字(gross 20000/net 10000/单名 20%)复核是否适配当前 AUM | 用户亲手 | 无 | 短 | 已签待复核 |
| 14 | B4·剩余待签数字:participation/vol 真实阈值(机制已激活但阈值仍是工程占位 1.0/5.0,数学上不咬合)、维护降级自动减仓目标、拥挤门口径三选一、rate-limit 精确数字 | 用户亲手+研究配合 | 无 | 视项而定 | `198b1db` 只换了喂门的数据来源,没改阈值本身——B4 未因"已激活"算完成 |
| 15 | D 段·端到端 `--submit` 自动提交演练(testnet,不涉及真钱) | 用户亲手 | #12 | 半天 | runbook 已有 preview→submit 检查清单 |
| 16 | 8 周前向证据继续积累 | 时间驱动 | #2、#8 | ~7 周(到 08-27,预算到 09-01 更稳妥) | 见 §0.1 时钟实况 |
| 17 | 真实申请 Binance/Bybit 主网 API key(禁提现+绑IP) | 用户亲手,外部动作 | 建议先做 #6 | 视交易所流程 | 当前 key/ 只有 testnet/demo 两把,无主网 key 痕迹 |
| 18 | 新签一份主网 scope 的 mandate(per-venue,testnet 签署不能带到主网) | 用户亲手 | #17 | 短 | — |
| 19 | P0-1/P0-2 核签:接受 Phase 14 前处于 LATENT,或做最低成本缓解(mandate checksum 字段、agent 环境结构性去掉 key/ 写权限) | 用户亲手核签 | 无 | 需先想清楚,非纯执行项 | 清单原文:"agent 不可自行完成" |
| 20 | F 段·最终 Go/No-Go 人工签字 + 起步资金数字 | 用户亲手 | 以上全部 | — | 清单设计:任一项不过→No-Go |
| 21 | (非阻塞卫生项)`main` 落后 `master` 已因本次合并大幅收窄;8 条 topic 分支(codex/barra-lite-diagnostics 等 5 条+claude/* 3 条)已全部被 master 完全吸收(相对 master 0 个独有提交),可删除;13 条 dependabot 分支 12 天零 triage | 用户/代码 | 无,随时可做 | 半天到 1 天 | 分支层面无任何阻塞实盘的未合并工作,只是清理卫生问题 |

### Phase 3A 清单逐项状态(以 `docs/PHASE_3A_GO_NO_GO_CHECKLIST.md` 唯一真实、代码对齐版本为准;`docs/phase3a/*` 全套判定废弃模板,不采信)

| 段 | 内容 | 现状 |
|---|---|---|
| A·结构性安全边界 | 5 旗帜恒 false、readiness=L2、AI 到不了 APPROVED | [已核实,持续满足]未被本次合并触碰 |
| B1·mandate 逐字段签署 | testnet/bybit_demo 已签 | 主网需重签(#17-18) |
| B2·策略级验证准入 | crowd_contra **未签**,当前正拦真实提交 | **进行中的硬阻塞**(#12) |
| B3·firm 硬门上限 | 已签,待复核适配当前 AUM | 待复核(#13) |
| B4·Phase 4/5 新机制数字 | 拥挤度 z*=3.0/异常告警 15%/保证金地板 2.0 已签;ADV/vol 机制已激活但阈值仍是工程占位 | 部分完成(#14) |
| C·≥8 周前向证据 | 起跑 2026-07-02,当前 6 天,0 条已兑现周期(结构性正常,未到首次兑现点) | 时间驱动中(#16) |
| D·P0/P1 收口签字 | P1 系列多数已收口;P0-1/P0-2 显式"agent 不可自行完成" | 需人工核签(#19) |
| E·进程/部署隔离 | 仅设计文档+拒绝式 stub,无真实实现 | Phase 14,post-L2,非当前目标 |
| F·最终 Go/No-Go | 未签 | 依赖以上全部(#20) |

### 文档一致性补充发现(独立于各维度报告)

- **`docs/QUANTOS_COMPREHENSIVE_AUDIT_2026-07.md`(未跟踪,82/100 L2 视角审计)审查的是不完整快照**——自称 `services/` 139 个 `.py` 文件,实测现为 214 个(少算 54%);全文 0 次提及 live/、Binance、Bybit、crowding、mandate、testnet。大概率生成于磁盘处于"缺 live 代码的 master"状态时。其 82/100 分和列出的发现对它审查的那个旧快照大概率准确,但**对"能不能走向实盘"这个问题完全没有证据覆盖,不能作为当前 readiness 的权威结论**。
- **CLAUDE.md/memory 里的"gate-green N tests"系列数字需要重新拿一次权威值**——各文档口径互不对齐(1047/1173/1280/2129/2094/1176/2377 等),建议合并后跑一次 `scripts/validate.py` 更新回 CLAUDE.md,并确认 §0 提到的 `_git_sha()` worktree 场景失败是否已处理。

### 顶级对照:ORR(运行就绪评审)逐维度差距

| ORR 维度 | 顶级实践期望 | QUANTOS 现状 | 差距 |
|---|---|---|---|
| 变更管理/发布控制 | 部署即代码,无"哪个分支在跑"的模糊 | **本次事故根因本身**:排程硬编码磁盘路径不锁分支/commit | 大——建议给 live 排程加启动时分支/commit 自检,非预期即拒绝启动 |
| 监控与告警 | 关键任务失败分钟级自动感知 | 死人开关设计了但从未注册,本次靠人工审计发现 | 大——#8 是最高杠杆单项修补 |
| 事故响应/On-call | 明确 runbook+责任人 | runbook 已相当完整(§2 故障 playbook) | 小——缺的是监控触发这一环 |
| 回滚能力 | 一键回退已知良好状态 | Kill-switch/reduce-only 紧急通道扎实;但"代码回滚"本身缺乏保护 | 中 |
| 容量规划 | 已知规模下资源/限额上限 | RE-1 容量带工程已做,但 B4 的 ADV/vol 阈值在 $3-5k 规模下仍数学不咬合 | 中——#14 |
| 安全评审 | 密钥最小权限、定期轮换、访问审计 | 两个 P0 未闭环;15-agent 安全审查"清零 HIGH/MEDIUM"但没覆盖这两间屋子 | 大,但修复成本都很低(XS/S) |
| 数据完整性 | 结论可复现、有 provenance | PIT knowledge_time 对批量历史数据是字节级复制;VT Sharpe 不可复现 | 中——不影响 L2 安全边界,但影响证据可审计性 |
| 文档与 Runbook | 单一事实来源 | 15 份死 Phase3A 文档+2 份 Barra-lite 幽灵计划同时存在 | 大——#7 |
| 演练/Game Day | 定期演练故障场景 | 无迹象曾主动演练过"分支切换后排程会怎样"这类场景 | 中——建议 #8 之后做一次人为断开重连演练 |
| 签署链/责任人 | RACI 清晰 | 清单设计干净(每项留了签署人+日期),问题是尚未真正走完 | 待启动 |

**总体**:QUANTOS 在"代码/机制"维度的 ORR 成熟度明显高于"流程/监控/文档卫生"维度——排程故障不是因为某个 gate 写错了,而是因为"这套系统当前跑在哪个分支"这件事本身没有被机器盯住,监控盲区(watchdog 未注册)又让"没被盯住"多晒了将近一天才被发现。补齐 #8(watchdog)是单项杠杆最高的修复。

## 6. 不可逾越的安全边界(重申)

- 5 个 LIVE 标志(`LIVE_TRADING_ENABLED` 等)恒 false,任何阶段的"迈进"都经代码升级 + 多步人工批准,不经旗标翻转;
- mandate 数字 100% 用户签署,代码/AI 不得代拟;
- 密钥不进 git/日志;AI 代理不触碰实盘下单与凭据;
- 治理扫描器与 `tests/governance/` 的机器强制不变量不得弱化。
