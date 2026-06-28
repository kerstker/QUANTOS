# QUANTOS 合规与开发流程对照表

> 给单人全栈开发者的"机构级流程"落地参考。目的:在 QUANTOS(安全关键 AI 量化研究平台)上,用
> **机器强制**代替**多人人工纪律**,让单人开发也能逼近机构合规。2026-06-29。

## 0. 一句话原则

机构合规的命门是**职责分离(Segregation of Duties)**:写代码 ≠ 批准上线 ≠ 碰生产凭证。
单人做不到"多个人",但可以做到**把角色在时间/工具上分开,让机器当不讲情面的"第二个人 + 审计员"**。

## 1. 机构软件流程的骨架(SDLC + 门禁)

| 阶段 | 机构做法 | 关键产物 |
|---|---|---|
| 需求/设计 | 设计先于实现、评审 | 设计文档、ADR(架构决策记录) |
| 编码 | 分支开发、小步提交 | feature 分支、规范化提交 |
| 评审 | PR + 双人复核(四眼原则) | PR 记录、评审意见 |
| 集成 | CI 自动构建/测试/扫描,**fail-closed** | 绿色门禁才可合并 |
| 测试 | 单元→集成→契约→端到端 + 覆盖率门槛 | 测试套件、覆盖率报告 |
| 发布 | 变更审批、灰度、可回滚 | 变更工单、发布说明、CHANGELOG |
| 运行 | 监控、告警、runbook、事后复盘 | 可观测性面板、postmortem |
| 安全/合规 | 依赖扫描、SAST、密钥管理、审计留痕 | 漏洞报告、不可变审计日志 |
| 环境 | dev/staging/prod 隔离,凭证不跨 | 环境矩阵、凭证策略 |

**金融机构特有**:四眼原则、变更审批委员会(CAB)、模型风险管理(SR 11-7)、数据血缘、全程不可变留痕。

## 2. QUANTOS 现状对照(已经在做的机构纪律)

| 机构要素 | QUANTOS 对应 | 位置 |
|---|---|---|
| 自动化策略闸门 / 职责分离 | **governance scanner**——机器拦死越界改动(券商SDK/下单/翻flag) | `apps/research-console/scripts/run_paper_to_live_governance_check.py` |
| CI fail-closed 门禁 | `scripts/validate.py`(治理扫描 + 全测试套件,全绿才算过) | `scripts/validate.py` |
| 防接口漂移 | 契约测试(改 payload 必须同 commit 改 TS 类型 + 契约) | `tests/research/test_console_api_contract.py` |
| 测试金字塔 | 单元 + 契约 + 治理边界 + 前端 smoke;~1000+ 测试 | `tests/`, `services/**/tests/` |
| 测试先行(TDD) | 全程红→绿→提交 | 全仓 |
| 信任边界 + 双人复核要求 | 威胁模型明写"build-time 面由 code review + 治理扫描把关" | `CLAUDE.md` §Threat model |
| 不可变审计留痕 | hash-chained audit log(seal/verify) | `services/monitoring/audit_log.py` |
| 模型风险管理 | ModelRiskReview 页(SR 11-7 风格四维评分) | 前端 + `console_api` |
| 密钥管理 | 从不提交凭证、不引券商 SDK(机器扫描强制) | 治理扫描器 + `tests/governance/` |
| 变更审批 + 职责分离(金融级) | L2_DRY_RUN_ONLY;上 live 需多步人工审批;`auto_approved=0` | `quantos/contracts/config.py` + 生命周期状态机 |
| 关注点分离 | 策略(意图)与风险(许可)分离;broker 在 adapter 后 | `services/execution`, `services/risk` |
| 多重检验诚实 | experiment_registry 跨进程持久试验次数 + var_sr(防假阳性膨胀) | `services/research/experiment_registry/` |

**结论:你不是野路子单人开发——已把机构合规最硬的几条(自动化策略闸门、fail-closed 门禁、职责分离、留痕)用代码固化了。**

## 3. 单人落地步骤(轻量、合规审计第一眼就看的)

按优先级,逐项可独立完成:

1. **主干分支保护 + 强制 PR**:GitHub 设 `main` 必过 CI + 至少 1 review。哪怕只有你,也走 PR 流程留 review 痕(你审你自己,用清单)。
2. **ADR 目录** `docs/adr/NNNN-标题.md`:每个重要架构决策记"背景/选项/决定/后果"——合规要的就是"为什么这么改"可追溯。
3. **CHANGELOG + 语义版本**:每次发布留版本号 + 变更说明。
4. **依赖漏洞扫描 + SAST 接进 CI**:dependabot(已有分支迹象)+ 代码静态安全扫描。
5. **凭证管理 + 环境隔离策略文档**:写明谁能碰什么、research/paper/live 如何隔离(QUANTOS 已隔离)。
6. **事件响应 runbook + postmortem 模板**:`docs/runbooks/`、`docs/postmortems/`。
7. **自检清单(Definition of Done)**:每个 PR 自查——测试绿?契约同步?门禁过?无密钥?provenance 诚实?

## 4. 自检清单(PR Definition of Done,贴在 PR 模板里)

- [ ] `scripts/validate.py` 全绿(治理扫描 + 全测试)
- [ ] 前端 typecheck + test + build 绿
- [ ] 改 payload 形状的,同 commit 改了 TS 类型 + 契约测试
- [ ] 新研究数字:**真在工具输出里观察到**,不是编的
- [ ] 无密钥/凭证入库;无券商 SDK;无 live/flag 翻动
- [ ] provenance 诚实(真=真标记,合成=合成标记,fail-closed 回退)
- [ ] 提交信息说清"做了什么 + 为什么 + 怎么验证的"
