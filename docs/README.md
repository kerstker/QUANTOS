# 文档导航(2026-07-09 整理)

仓库里有 240+ 份 markdown 文档,不是全部同等权威——这份索引回答"我该信哪一份"。

## 先读这几份(当前真正权威的状态)

| 文档 | 是什么 |
|---|---|
| [`CLAUDE.md`](../CLAUDE.md)(根目录) | **唯一的地面真相**。"系统实际有什么"的权威描述,每次重大变更都应同步更新。任何其它文档与它冲突,以它为准。 |
| [`docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md`](QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md) | 当前顶级能力机器评估；成熟度、红线与下一步的唯一计分源。 |
| [`docs/PROJECT_STATE.md`](PROJECT_STATE.md) | 最新工作进度与会话交接，包含最近门禁结果。 |
| [`docs/audit/2026-07-07-live-readiness-top-tier-gap-audit.md`](audit/2026-07-07-live-readiness-top-tier-gap-audit.md) | 最新一轮全维度审计(9 维度独立打分 + 跨维度结构性差距 + 有序阻塞清单)。想知道"现在到底差什么"看这份。 |
| [`docs/audit/RISK_REGISTER.md`](audit/RISK_REGISTER.md) | 活文档,逐条风险 P0/P1/P2 + 修复状态,change 后应同步更新(历史上出现过顶部日志与详述条目不同步的问题,已修复两处,以后改动请同时改两处)。 |
| [`docs/PHASE_3A_GO_NO_GO_CHECKLIST.md`](PHASE_3A_GO_NO_GO_CHECKLIST.md) | 走向首笔实盘的人工签字清单,唯一权威版本(`docs/_archive/phase3a-day1-boilerplate/` 那批同名早期文档已废弃,见下)。 |
| [`SECURITY.md`](../SECURITY.md)(根目录) | 安全不变量 + 密钥处理规范 + git 钩子安装方法。 |
| [`docs/runbooks/live-trading-runbook.md`](runbooks/live-trading-runbook.md) | 实盘运维 playbook:限流/时钟漂移/崩溃恢复/风险越限/密钥轮换等场景处置步骤。 |
| [`README.md`](../README.md)(根目录) | 项目介绍 + 环境搭建。 |
| [`docs/RESEARCH_WORKFLOW.md`](RESEARCH_WORKFLOW.md) | 研究→回测→验证→干跑→(晋级)全流程怎么走,新人从这里开始做研究。 |

## 目录结构

```
docs/
├── audit/          权威:审计报告(RISK_REGISTER 是活文档,其余是时间点快照)
├── phase2a/、phase2f/、phase2h/   历史:对应开发阶段的设计/交付记录
├── research/       研究结论(因子发现、证伪过程等)
├── runbooks/        权威:运维 playbook
├── reports/         自动生成:每日 Go/No-Go 证据报告(schtasks 产出,追加式)
├── engineering/
│   ├── specs/       历史:TDD "先写设计"记录(64 份,按日期命名)
│   └── plans/       历史:TDD "再写实施计划"记录(32 份,按日期命名)
└── _archive/        已废弃/被取代,保留仅供历史参考,不要按其内容行动
    ├── phase3a-day1-boilerplate/   2026-06-23 建仓当天的传统期货 broker 上线脚手架,与实际
    │                               加密货币方向无关,从未更新过——见其 README.md
    └── stale-snapshots/            审阅了不完整代码快照、结论已过时的旧审计文档
```

`docs/engineering/{specs,plans}` 是项目自有的开发记录，不依赖任何外部技能或插件。它保留
spec → plan → TDD 实现的完整链路，同一条工作
在这里能看到从设计意图到落地代码的完整链路),**不是待清理的杂物**——历史上曾有一次因为
master 分支指针滞后,被误判成"两条独立开发线互不知情的重复劳动",后来用 `git merge-base
--is-ancestor` 核实其实是同一条线性提交链,只是文档没有回头补一行"已被取代"的指针。教训:
判断一份 spec/plan 是否仍有效,先看它是否被后续实现取代(检查同名 `git log`),不要只看
文件是否"看起来旧"。

## 已知的文档重叠(未合并,按新旧排列供参考)

根目录 + `docs/` 顶层散落着好几份内容有重叠的路线图/差距分析文档,没有强行合并(合并前需要
逐份精读避免信息丢失,这份索引只负责告诉你哪份新):

| 文档 | 最后改动 |
|---|---|
| `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md` | 2026-07-04(相对最新) |
| `docs/PROJECT_STATE.md` | 2026-07-03(体量最大,77K) |
| `docs/2026-07-03-quantos-architecture-and-roadmap.md` | 2026-07-03 |
| `docs/2026-07-02-quantos-gap-analysis-roadmap.md` | 2026-07-03 |
| `docs/QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md` | 2026-07-01 |
| `docs/ROADMAP.md` | 2026-06-27 |
| `docs/QUANTOS_AUDIT_2026-06.md` | 2026-06-24(早期审计,已被 07-07 版取代,建议归档但保留供对比) |
| `docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md`、`docs/QUANTOS_L2_5_RESEARCH_PLATFORM_HARDENING_BACKLOG.md` | 2026-06-23(最早期) |

**如果只想知道"现在真实差距是什么",不要读这一批——读上面"先读这几份"里的 07-07 审计报告。**
这批文档更适合"想了解某个具体历史决策当时怎么想的"这类溯源场景。

## GitHub

见根目录 [`CONTRIBUTING.md`](../CONTRIBUTING.md)、[`docs/GITHUB_SETUP.md`](GITHUB_SETUP.md)。
分支/PR/依赖更新的当前状态见最新一次项目状态同步(问 CLAUDE.md 或直接 `git log`/`gh pr list`,
这类状态变化太快,不适合写进静态文档)。
