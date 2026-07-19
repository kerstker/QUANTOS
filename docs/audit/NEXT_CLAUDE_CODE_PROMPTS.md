# 后续 Claude Code 分阶段执行提示词(NEXT_CLAUDE_CODE_PROMPTS)

> 审计日期:2026-07-06 · 用法:每次只交给 Claude Code 一个提示词,完成、过门禁、提交后再进下一个。
> 每个提示词都内置同一组**安全约束**(下方"通用约束"),对应 [`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md) 的一个 Phase。

## 通用约束(每个提示词都必须遵守,复制进每次对话)

```
铁律(不可协商):
1. 不绕过、不弱化任何风控门 / kill-switch / mandate / 治理边界。
2. 不创建真实交易动作;不 arm mainnet;不翻任何 LIVE 旗帜;不写真实账户接口;不 import 券商/CTP/FIX SDK。
3. 不删除、不隐藏、不弱化任何现有测试或审计;失败测试必须修复而非删除。
4. 不引入不可控自动交易;任何自动动作只能是"更 fail-closed / 只减不加敞口"的方向。
5. tests-first(TDD):先写复现问题的失败测试,再实现;最小安全变更。
6. 每步跑 `python scripts/validate.py`(治理扫描器 + 全部 pytest)必须全绿才提交;当前基线 1564 passed。
7. 所有结论/改动附 file:line 证据;所有 prose 文档用中文,代码标识符用英文。
8. 若某项改动需要真实风险数字(mandate 限额、firm 敞口上限、阈值校准),只建机制 + fail-closed 缺省,
   数字留给用户签署,绝不代填。
9. 遵守 services/ 不 import live/ 的单向边界;live/ 不进治理扫描器 SCAN_SUBDIRS(有专属边界测试)。
```

---

## 提示词 1 — Phase 1:P1 LATENT 工程收口(全 fail-closed 加法)

```
读 docs/audit/RISK_REGISTER.md 与 GAP_MATRIX.md。按优先级逐项收口以下 P1/P2 缺口,每项 TDD + 独立提交 +
过全量门禁全绿。全部是"只让系统更严"的 fail-closed 加法或真 bug 修复,不放松任何风控:

1. [P1-2] live/governance/mandate.py::evaluate_mandate 增加账户级敞口聚合:gross/net 校验改为对
   (账户现有全量敞口 + 本批 delta) 投影;多策略共享未分块 mandate 时硬降 NEEDS_HUMAN_REVIEW
   (不再只告警)。测试:两策略各贴 60% gross,第二批必须升人工。
2. [P1-3] live/execution/filters.py::client_order_id 加 per-batch 单调序号/nonce,消除
   同(strategy,decided_ts,inst,side,quantity)碰撞;同批重试仍幂等。测试:两笔同数量逻辑不同的增量
   得到不同 client_order_id;记账不再静默 INSERT OR IGNORE 吞没(考虑撞主键 raise)。
3. [P2-1] services/data/file_ingest.py:66 修乱序死检查:排序前用原始顺序跑 count_out_of_order;
   传真实 expected_days 让缺口检查生效。测试:乱序 event_time 序列 build_report 报 out_of_order>0。
4. [P1-1] tools/live_track_advance.py preflight 加 fail-closed 前置:每个将提交的 strategy_id 必须有
   在案 PASS ValidationResult(或可审计人工 override 记录),否则即便 --submit + 已签 mandate 也拒绝并落审计。
5. [P1-4] live/pipeline.py:place_order 之前先写 pending 意图账本行,让 resolve_unconfirmed_orders 看得见
   place-后-未落库的孤儿订单。测试:place 与 confirm 之间注入进程终止,下次崩溃恢复能发现该订单。
6. [P1-5] tools/live_track_advance.py::make_ctx_factory 从 LiveLedger 回填 recent_order_fingerprints/
   recent_timestamps;新增 cancel-rate 检查引擎。测试:窗口内重复 intent 返回 DUPLICATE_ORDER;限频/撤单率拒。
7. [P3-1] tests/governance/test_crypto_data_boundary.py:_NETWORK_ROOTS 加 aiokafka/kafka/redis/pymongo
   (显式排除被隔离的 services/trading/pre_trade_risk/main.py)。测试:services/ 隔离文件外新增 aiokafka 会失败。
8. [P2-3] live/execution/risk_monitor.py / live_risk_state.json 峰值权益持久化加合理性上界校验,
   防虚高峰值触发非必要 drawdown 全平。测试:注入异常峰值时全平路径要求人工确认或不在单轮执行。

约束:见"通用约束"。不碰 mandate 真实数字、不 arm mainnet、不改五旗帜。
```

---

## 提示词 2 — Phase 2:Data Quality Gate + Feature/Alpha 持久化

```
读 docs/audit/RISK_REGISTER.md 的 P1-8/P2-2/P3-4。TDD 完成:
1. 喂承重决策(前向证据钟、可部署 book)的单标的抓取器统一开 strict=True;前向兑现前加一道质量门,
   不过 fail-closed 留 pending 并告警。先验证开 strict 不破坏现有数据刷新(分抓取器灰度)。
2. [2026-07-13 已完成] volume 根因是 quote USD volume 在 scenario 容量端二次乘价；已用显式
   `VolumeUnit/VolumeSemantics` + quote-notional helper 修复，极差检查更正为 dispersion guard。
3. AlphaAssetRegistry 落 sqlite 持久化;在进化/cs_genome/gp 育种处调用 make_child_asset 记
   parent_ids/mutation_op,落持久注册表。测试:AlphaAsset 磁盘往返一致;育种子代带非空谱系。

约束:见"通用约束"。不改任何前视/PIT 语义;质量门只能更严不能更松。
```

---

## 提示词 3 — Phase 3:验证门作 live 前置 + 组合优化器接实盘 + firm 硬门

```
读 docs/audit/RISK_REGISTER.md 的 P1-1/P1-6/P2-7 与 TARGET_ARCHITECTURE.md 第五节。TDD 完成:
1. firm 级敞口硬门:services/portfolio/firm_aggregation.py 结果接入组合级硬门(firm 净/毛/单 symbol
   跨账户集中上限),越限升人工或按比例减仓。阈值只建机制 + fail-closed 缺省,真实数字留用户签。
2. 把 min_variance_weights/risk_parity_weights + 毛杠杆/换手/相关性约束接入 live sizing
   (mandate 之前的账户级组合层);保留"裸信号 + mandate"可回退路径。先在干跑充分验证。
3. 拥挤门阈值(crowding_gate.py)做敏感性扫描并文档化校准依据,契约测试锁定。

约束:见"通用约束"。firm 硬门/拥挤门阈值是风险数字,只建机制不代填。
```

---

## 提示词 4 — Phase 4:OMS/EMS 订单完整性 + Pre-Trade 引擎全承重

```
读 docs/audit/RISK_REGISTER.md 的 P1-5/P2-3/P2-6。TDD 完成(全部 testnet/demo 验证,不碰 mainnet):
1. 接入真实逐标的 ADV/已实现波动喂 MarketContext,gate_limits 的 max_participation_rate/
   max_instrument_vol 设校准值,让 check_liquidity/check_volatility 在 live 承重。
2. OMS/EMS 强化:主备连接/断线重连/交易日切换;撤单/改单完整;Bybit 真实联调收口(等 key)。
3. 同点跨源核价已于 2026-07-13 完成：venue midpoint/CoinGecko 均用源时间戳，缺失或陈旧冻结且无 decided_ts 跳过。剩余只在用户确认 cadence 后收紧 7 天实盘新鲜度门，并积累 testnet/demo 观测校准 5%/5 分钟工程阈值。
4. 紧急减仓对抗式属性/网格测试:证明任意(持仓,订单)输入下 submit_emergency_flatten 只减不增敞口。

约束:见"通用约束"。触碰订单执行路径——每步过全量门禁 + testnet 联调,绝不在 mainnet 验证。
```

---

## 提示词 5 — Phase 5:Portfolio 生命周期 + Post-Trade 对账 + 退休调度

```
读 docs/audit/RISK_REGISTER.md 的 P1-7 与 GAP_MATRIX.md 维度 10。TDD 完成:
1. 调度器周期调用 services/alpha/maintenance.py::run_maintenance + 自动从 forward paper-track/live 账本
   算 live-vs-backtest 指标喂入;退休/降权信号联动 live 仓位收缩(只减不加)。
2. 实时盘中监控:把周/小时级对账升级为近实时持仓/权益/保证金/回撤监控 + 异常检测。
3. 自动日终报告 + PnL/成本归因(复用 gonogo_report 风格)。

约束:见"通用约束"。自动 de-risk 只能"只减不加",全平动作保留人工确认选项。
```

---

## 提示词 6 — Phase 6:事件总线 + 可观测性 + 容器化 + 解耦

```
读 docs/audit/RISK_REGISTER.md 的 P2-4/P2-5/P3-3。TDD/渐进完成:
1. 用 quantos/contracts/events.py::Event 实现持久事件总线,关键事件(pretrade approved/rejected、
   kill-switch、alpha promoted、order state、deployment)带 correlation/causation ID,支持回放。
2. 引入 import-linter 钉死域间无环(禁回指 research 反向边),进 scripts/validate.py;research 拆
   backtest/validation/api 子包;console_api 按 router 拆 <800 行。
3. Docker + staging/prod 环境隔离 + 配置管理 + 监控告警基建。
4. 删空 DDD 脚手架目录或标 PLANNED。

约束:见"通用约束"。大范围结构调整分模块渐进,每步过全量门禁;不改任何安全语义。
```

---

## 提示词 7 — Phase 7:Phase 14 信任边界(真钱前最后结构性前置)

```
读 docs/PHASE_14_PROCESS_ISOLATION.md、services/governance/signing.py、docs/audit/RISK_REGISTER.md 的 P0-1/P0-2。
这是通往真钱的最后结构性前置,涉及大量人工签署环节——凡需人工决定的一律停下来交给用户,不代替。TDD 完成:
1. promotion_api 的 OIDC+mTLS+MFA+RBAC 与浏览器 PKCE S256 M2 身份边界已落地；继续部署外部 IdP/TLS、密钥轮换及身份生命周期，
   实现真 SigningAuthority(独立进程签发/验证 SignedDecisionRef)，把 verify_signed_ref 接进 live 执行
   路径与 kill-switch reset。
2. mandate 密码学完整性:加载校验 detached 签名;agent 环境结构性无 key/ 写权限。
3. 进程/部署隔离:mutating 决策与研究/agent 进程隔离。
测试:进程内伪造 is_out_of_process=True 的权威仍验不过;篡改/无签名 mandate 被拒。

⛔ 不做:arm mainnet、翻 LIVE 旗帜、签署真实 mandate 数字、开启无人值守真钱 --submit——这些是
Phase 3A 人工 Go/No-Go 评审的内容,必须由真人逐字段签署,AI/代码绝不代替。完成本提示词后,把系统状态、
剩余人工签署项、Go/No-Go 检查清单交给用户。

约束:见"通用约束"。这是最高危 Phase——任何触及真钱授权的动作都停下来等人工。
```

---

## 使用顺序与检查点

1. 提示词 1(Phase 1)可**立即执行**——全 fail-closed 加法 + 真 bug 修复,不依赖任何人工决定。
2. 提示词 2-6 是工程主体,大多可并行,**不解锁真钱**。
3. 提示词 7(Phase 7)含大量人工签署,**不可由代码/AI 自行完成**;它的产物是"真钱前的最后一份人工检查清单"。
4. 每个提示词完成后:过 `python scripts/validate.py` 全绿 → 提交 → 更新 `docs/audit/` 对应风险条目状态 → 再进下一个。
