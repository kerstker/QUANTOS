# Phase 7 —— Phase 14 信任边界 + Phase 3A 小资金实盘前验收门(诚实裁定)

> 2026-07-07 · 主审直接调查(分类器暂不可用,亲 Read mandate_store/signing/RISK_REGISTER)· IMPLEMENTATION_ROADMAP.md Phase 7
> 核心:Phase 7 是**真钱前最后的人工门**,roadmap 自己写明"含大量人工签署环节,**不可由代码/AI 自行完成**"。
> 四项里三项本质是人工签署 / 真实密码学密钥 / 真实部署基建;唯一 agent 可做的纯加法诚实交付 = 一份 Go/No-Go 清单。

## 四项裁定

| 子项 | 裁定 | 依据 |
|---|---|---|
| 1 出进程签名 + RBAC(P0-2) | HUMAN_GATED / 需部署基建 | `signing.py` 是**拒绝式接缝**(NullSigningAuthority.sign() 恒 raise、verify_signed_ref 恒 False,即便伪造 is_out_of_process=True 也拒),设计明说"不建真跨进程签名器、真出进程权威=post-L2";边界测试已在 |
| 2 mandate 密码学完整性(P0-1) | HUMAN_GATED | `mandate_store.py` 现是纯 JSON+字段+有限数校验(无签名);**单进程内做签名校验是 theater**——受信代码持同一把 key,真防御只能出进程(signing.py 自己的推理)。不建假实现 |
| 3 进程/部署隔离(Phase 14) | 需部署基建 | 独立进程/容器/主机 + `key/` 文件系统 ACL,单进程代码挡不住受信代码绕过;agent 路径 containment 已测试;真隔离 = 部署环境 |
| 4 Phase 3A Go/No-Go 人工评审 | 100% 人工,但可做诚实辅助 | 决定永远是真人的;**清单文档**(列门槛+状态+签字位,不下结论不设数字)是零风险纯加法辅助 |

## 已落地(唯一 agent 可做的纯加法)

`docs/PHASE_3A_GO_NO_GO_CHECKLIST.md`——逐条列出放行门槛(五旗帜结构性边界、mandate 逐字段签署、验证准入、
firm 上限、本轮新建默认惰性机制的待签数字、≥8 周前向证据、P0/P1 逐项收口、端到端 --submit 演练、进程隔离),
每个需真人拍板的项留显式"人工签此"字段,**不下任何 Go/No-Go 结论、不设任何风险数字**。与 Phase 5 的
`tools/gonogo_report.py`(自动聚合证据数字)互补:报告给证据,清单给门槛与签字。**⚠ 通过清单 ≠ 解锁实盘**——
五旗帜 pin + readiness=L2 仍结构性挡死,真正切实盘是独立的多步人工部署动作。

## 诚实边界总结

Phase 7 的诚实答案与前几个 Phase 根本不同:**它的全部实质都在 agent 的安全边界之外**——真人签署、真实密钥、
真实部署隔离。自主授权明确只覆盖 fail-closed 加固,**不覆盖绕过代码强制的人工门**。所以本 Phase agent 只做了
一件事:把人工评审需要的**门槛清单**整理成 tracked 文档(零风险、不决定、不设数),其余全部诚实标注为需真人 +
需部署基建。**这标志着系统性路线图里 agent 能做的部分收口**——Phases 0–6 的 fail-closed 加固 + 承重接线 +
诚实机制全部落地过门禁;Phase 7 的真钱放行,按设计,交还给人。

**留人工(不可由 agent 完成)**:mandate 逐字段签署、验证准入签署、待签风险数字、真出进程签名器 + RBAC(Phase 14)、
真进程/部署隔离、≥8 周证据达标核验、端到端演练、最终 Go/No-Go 决定。
