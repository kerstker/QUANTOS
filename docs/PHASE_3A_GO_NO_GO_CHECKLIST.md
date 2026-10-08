# Phase 3A 小资金实盘 Go/No-Go 人工评审清单

> Phase 7 item 4 的诚实交付。**本清单不下任何 Go/No-Go 结论、不设任何风险数字**——它只把门槛逐条列出、
> 标注每项当前机制状态,并为每个需要真人拍板的项留一个显式"人工签此"字段。结论 100% 是人工评审的事。
>
> 配套证据:`python -m tools.gonogo_report` 自动聚合前向证据 + 逐 venue PnL/成本归因 + 事故台账
> (`data/cache/gonogo_report.json` + `docs/reports/gonogo-<date>.md`;console `/api/v1/console/gonogo`)。
> 本清单列**门槛与签字**,那份报告给**证据数字**,两者合起来是人工评审的完整材料。
>
> ⚠ 通过本清单 ≠ 解锁实盘。即便全部签字,五旗帜结构性 pin + readiness=L2 仍挡死真钱——真正切到实盘是
> 一个独立的、需多步人工操作的部署动作,不在本清单的自动化范围。

## A. 结构性安全边界(必须保持,非签字项)

- [ ] 五旗帜保持默认:`LIVE_TRADING_ENABLED=false`、`REAL_BROKER_ENABLED=false`、
      `ALLOW_ORDER_SUBMISSION=false`、`ALLOW_AI_DIRECT_TRADING=false`、`REQUIRE_HUMAN_APPROVAL=true`
      —— 由代码 + 治理扫描器机器强制;**放行日之前不得翻动**。
- [ ] readiness 仍为 `L2_DRY_RUN_ONLY`,直到本清单全签 + 独立部署动作。
- [ ] AI/非人 actor 结构性到不了 APPROVED / reset kill-switch / 触实盘(`ResearchAgentLoop` 硬化路径,
      已测试)——放行不改变这一点。

## B. 逐字段人工签署项(真人签,agent 绝不代填)

### B1 · mandate 逐字段签署(P0-1 的业务侧)
`key/live_mandate.json`(每 venue 各一份;loader `live/governance/mandate_store.py` fail-closed,缺文件/
坏字段/非有限数字都硬拒,**不代填任何默认数字**)。逐字段人工核签:
- [ ] `max_single_order_notional` = ______  · 签署人 ____________ · 日期 ______
- [ ] `max_gross_notional` = ______ · `max_net_notional` = ______ · 签署人 ____________ · 日期 ______
- [ ] `per_instrument_max_notional`(逐标的)已核 · `allowed_instruments` 白名单已核 · `exchange`/`venue` 匹配
- [ ] `expires_at` 已设(过期即 fail-closed 升人工)· `approved_by` 记真实签署人

### B2 · 策略级验证准入(P1-1,已接线为 live 前置)
`key/live_validation_attestations.json`(策略级 PASS 验证或可审计人工 override;preview 只告警、
`--submit` fail-closed 跳批)。模板:`python -m tools.create_validation_attestation_template`。
- [ ] `crowd_contra` 已签(**当前真提交被此门挡住,直到签署**)· 签署人 ____________ · 日期 ______

### B3 · firm 硬门上限(P1-6,已接线,数字已签待复核)
`tools/live_risk_sentinel.py` 内 gross 20000 / net 10000 / 单名 20%(RISK_REGISTER 记为已签)。
- [ ] 三个上限数字复核确认适配当前 AUM 规模 · 复核人 ____________ · 日期 ______

### B4 · 本轮新建、默认惰性、待签才生效的机制数字(Phase 4/5)
这些机制已 fail-closed 建好、**默认关闭 = 生产行为字节等价**,签一个数才启用:
- [ ] `max_participation_rate` / `max_instrument_vol`(Phase 4:ADV/波动门,现结构性不绑定)= ______ / ______
- [ ] 权益异常跳变阈值 `_EQUITY_JUMP_ALERT_PCT` / margin 地板 `min_margin_ratio`(Phase 5 item 2)= ______ / ______
- [ ] 维护降级→自动**减仓**目标数字 + RETIRED→全平人工确认路径(Phase 5 item 1,现只"停止加仓"不减仓)
- [ ] 拥挤门口径决定(WARN-only / 短腿 / 签阈值,三选一)· rate-limit 精确数字
- [ ] 维护 sweep 的 backtest 参照数据 `data/cache/alpha_backtest_refs.json`(研究/人填,agent 不代填)

## C. 前向证据(人工读，非签字数字)
- [ ] ≥8 周真前向 paper-track 证据已积累(证据钟 2026-07-02 起走;`gonogo_report` 逐 alpha 列已积累周数/
      距门槛/paper Sharpe·IC)· 达标日期 ______
- [ ] live-paper 对账无系统性缺口(parity gap 趋势正常)· TCA 加权滑点未持续高于回测假设

## D. P0/P1 收口签字(逐项核 RISK_REGISTER)
- [ ] **P0-1** mandate 密码学完整性 = **Phase 14 + 人工**:真 detached 签名校验 + agent 环境结构性无
      `key/` 写权限 + 文件系统权限收紧。**诚实边界**:单进程内的签名校验是 theater(受信代码持同一把 key),
      真防御只能出进程(见 `services/governance/signing.py` 与 `docs/PHASE_14_PROCESS_ISOLATION.md`)。
      → 需真人 + 真部署环境,**agent 不可自行完成**。核签人 ____________
- [ ] **P0-2** 出进程签名 + 强身份 M4 = **Phase 14 + 部署基建**:promotion_api 的 OIDC 非对称验签、
      MFA、mTLS token binding、动作 RBAC 及浏览器 PKCE S256 已在 M2 承重并拒绝旧自报身份；仍需
      真外部 IdP/TLS、密钥托管轮换、身份生命周期及 AI workload 排除证据，并实现独立
      `SigningAuthority`、把 `verify_signed_ref` 接进执行路径。
      → 需真人 + 真部署环境,**agent 不可自行完成**。核签人 ______
- [ ] **P1-1..P1-10** 逐项核 `docs/audit/RISK_REGISTER.md`:P1-1/2/3/4/5/6/8/9/10 已收口(见 git log 与
      RISK_REGISTER 状态列);**P1-7**(维护调度 + 自动 de-risk)机制已上、自动减仓数字待签(见 B4)。
- [ ] 端到端 `--submit` 自动提交路径演练(preview→--submit 检查清单,见 `docs/runbooks/live-trading-runbook.md`)
      · 演练人 ____________ · 日期 ______

## E. 进程/部署隔离(Phase 14,需真实部署环境)
- [ ] mutating 决策进程与研究/agent 进程隔离(独立进程/容器/主机;`key/` 文件系统 ACL 收紧)——
      单进程代码挡不住受信代码绕过,真隔离是部署环境的事(容器安全不变量已有测试 +
      `deploy/console.Dockerfile` 已有同主机双冷构建、只读冒烟和 Trivy 证据；但这仍不等于进程/主机隔离，
      见 `evidence/reproducibility/container_build_latest.json`)。

## F. 最终 Go/No-Go 决定(纯人工)
- [ ] A–E 全部核完、B/D 需签项全签、C/前向证据达标、无未收口 P0/P1。
- [ ] **Go / No-Go 决定**(圈一个):______ · 决定人 ____________ · 日期 ______ · 起步资金 $______

> 任一项不过 → **No-Go**。上线初期先禁用无人值守 `--submit`,人工每周执行,稳定后再议自动。
> 本清单由代码/AI 维护"门槛与状态",**决定与签字永远是真人的事**。
