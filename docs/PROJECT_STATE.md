# QUANTOS 项目状态 / 上下文交接(活文档)

> **2026-07-20 Rank #12 验证 provenance 已完整收口：** 共享冻结契约
> `CatalogEvidence` 与 `BatchPboEvidence` 现在分别保留 catalog 的精确收益序列/Sharpe 总体/
> 可比较相关矩阵/有效试验数/DSR，以及 CSCV 的精确 block×candidate 矩阵/PBO。只有
> `AlphaCatalog.evidence()` 能从完整试验账本铸造目录证据；无法比较的历史试验按一项一个独立试验
> 保守计数，不会被少量相关序列间接折减。完整候选池必须先冻结并写入 catalog，再产生第一个裁决。
> evaluator、严格验证门和 standalone pipeline 已删除裸 DSR/PBO/trial-count 信任路径；
> `ExperimentRegistry.verify_evaluation` 会独立重放 DSR、PBO 与 exact-float reproducibility hash。
> `VALIDATED`、`PAPER_TRADING` 和最终人工晋级均绑定完整 catalog snapshot，目录收益即使只变化
> `1e-11` 也会使旧证据失效。console 只公开哈希、计数和相关性摘要，不泄露保留收益/矩阵。
> 该增量不增加网络、凭据、券商、下单或 live 能力；机器顶级判定仍为
> **15/22、NOT_TOP_TIER**，未把内部证据硬化冒充外部数据、身份或运营成熟度。
>
> **2026-07-20 外部 AI provider 治理接缝已落地，但 live 凭据未通过：** 新增版本化
> `config/model_provider_registry.json`，默认仍为 `offline_registered`，仅显式选择
> `sushuo_gpt_5_6_sol` 才会访问固定的
> `https://vip.sushuo.biz/v1/chat/completions` / `gpt-5.6-sol`。网络只存在于
> `tools/external_model_provider.py`；`services/`/`quantos/` 继续零网络。外部调用必须先通过注册
> `ModelCard`/`PromptTemplateCard`/`ToolManifest` 和 `GovernedModelRuntime`，唯一动作
> `EVALUATE`，审批、kill-switch、凭据、订单和 live trading 全部禁止。证据设计只保留请求/响应/
> 内容哈希、模型、用量、TLS leaf 指纹和运行时事件链，不保存 key、key 路径、原始 body 或模型正文；
> 独立 verifier 会重算注册、lineage、event/self/source/test hashes 和 7 项 provider 红队。
> 本轮本地 35 项聚焦测试通过；随后用用户指定 KEY 分别通过 Python stdlib HTTPS 和原始 Node
> `fetch` 形式做无业务数据 echo probe，两者均由服务返回 **HTTP 401**，所以没有生成或提交
> `evidence/external_model_provider_baseline.json`，`AI.MODEL.REGISTRY.REDTEAM` 继续诚实保持
> M2/PARTIAL。未来只需更新有效 `NEW_API_KEY` 或仓库外 key 文件并重跑 probe；只有 live 证据通过
> 独立 verifier 后该项才自动升 M3。`VALIDATION.CHALLENGER.MODEL_RISK` 仍另需强外部人类评审身份，
> 不随 provider 接入假升级。

> **2026-07-20 多资产研究证据从伪绿补成 M3 真基线:** `ALPHA.MULTI_ASSET.RESEARCH`
> 不再读取未跟踪的本机 equity cache。现把已版本化的 Frankfurter/ECB FX PIT bundle
> （18 个可交易货币、126,113 行、1999-01-04 至 2026-06-26）正式接入 `fx_tradable`
> IC 宇宙；同一研究核重放 `fx_momentum_120` 与 `fx_low_vol_60` 在 1/5/20 日的非重叠
> 截面 IC。治理侧不 import research/tools，而用独立 stdlib 实现逐行复核 PIT 身份、
> `knowledge_time <= event_time`、bundle/manifest/实现/测试摘要、自摘要及全部统计结果；
> 干净 checkout 无需网络或 `data/cache/fx.db` 即可复算。
>
> **研究结论仍诚实为 null:** momentum mean IC/t-stat 为 h1 `-0.0003/-0.0665`、
> h5 `-0.0050/-0.5233`、h20 `-0.0094/-0.5035`；low-vol 为 h1
> `-0.0049/-1.1136`、h5 `-0.0050/-0.5235`、h20 `-0.0063/-0.3351`。
> 这次关闭的是“可版本化、可重放的非加密研究能力”缺口，不是发现了 FX alpha；证据固定
> `DIAGNOSTIC_ONLY_NO_PROMOTION`，不触发晋级、部署或交易。机器矩阵由 **14/22、1 FAILED**
> 恢复为 **15/22、0 FAILED、5 PARTIAL、2 项外部/人工证据、3 条红线开放**，总 verdict
> 仍为 `NOT_TOP_TIER`。

> **2026-07-19 Pass M 精确家族证据收口 + 供应链实证刷新:** 便携 ALPHA 试验账本的
> exact-v3 家族模式已落地为 33 张家族表 / 401 个 SQL 对象 / 124 个命名绑定 /
> 228 条结构规则 / 9 个受控 UDF；静态实现封印为
> `c1a61a41daa67f58e0d7ede45dff0fe127fc36dba3a8ab3bc4a22657bab98f07`。
> 运行时只允许 17 条精确状态迁移，未列明调用一律拒绝；v2→v3 激活覆盖 405 个故障注入点，
> 13 张继承表非空迁移保持字节/逻辑不变，7 个已填充回滚点在关闭重开后仍精确恢复，33 张新表的
> UPDATE/DELETE 不可变性已做真实 SQLite 验收。C5 已拆为 proposal registration + 唯一 terminal parse
> result，消除二次链与未终结解析歧义；调用方 UDF 残留、伪造 owner token、同连接重入和双连接竞态均
> fail-closed。当前持久激活仍禁止，`CodeManifestEvidence` 只证明哈希绑定，不冒充外部批准或来源证明。

> 同轮修复了模型风险证据“实现已变但复用 artifact version”的真实治理缺口：生成器现在保留历史制品与
> runtime event 前缀，并通过新 release 追加；当前证据为 39 个制品、6 条运行事件、4 个哈希链修订，
> 模型/挑战者仍诚实停在 M2（负 OOS、无外部模型提供方/强审阅身份）。强身份本地基线重算为 M3。
> 供应链已用 Trivy 0.72.0 官方 SHA-256 校验包、2026-07-19 新数据库、两次真实 `--no-cache`
> OCI 构建重做：归档字节一致（SHA-256
> `1e584493f7d68e97dddd8842ef99179b49f01c840b4d387887d0c34a14897ade`），最终运行镜像
> 0 漏洞，基础镜像 5 项中低风险记录，534-component SBOM/基线检查通过；只读、无网络、UID 65532
> L2 冒烟通过。Docker Desktop 与临时扫描/归档文件已回收。

> 2026-07-20 03:00 的已注册 `QUANTOS_backup` 任务在正式运营根完成真实加密备份并返回 0；10 个
> required authority store 均已备份到独立卷、隔离恢复、摘要与 SQLite 完整性复验通过，权威恢复历史
> 追加到 event seq 26。该真实证据已同步进发布工作树，`backup-evidence:current` 恢复 PASS。
> 合并后最终统一门禁实跑 **19/19 阶段通过**：主后端 4227 passed / 5 skipped；live/trading/tools
> 1063 passed；前端 69 tests + typecheck/production build 全通过；治理扫描 351 文件 / 0 违规；漏洞、事件
> schema、服务图、供应链、模型风险历史、拥挤校准、顶级评估、备份新鲜度和风险恢复演练全部通过。

> **2026-07-20 干净 checkout 证据审计更正（本轮补齐前基线）：** 当时机器结论为
> **`NOT_TOP_TIER`：14/22 PASS，1 FAILED，5 PARTIAL，2 项需外部/人工证据，3 条红线开放**。
> 唯一 FAILED 是 `ALPHA.MULTI_ASSET.RESEARCH`：此前仅凭未纳入版本控制的本地
> `data/cache/equity_universe_snapshot.json` 判 PASS，GitHub/Linux 干净 checkout 无法重放；现已改为必须提供
> `evidence/multi_asset_research_baseline.json`（版本化非加密 PIT bundle + 可重放因子结果），禁止本地缓存伪绿。
> 其余未完成项不是代码伪绿：外部模型提供方与独立强身份审阅、真实 OIDC/TLS/密钥与身份生命周期、30 天恢复
> 历史 + 外部 Ed25519/WORM、跨主机可复现及外部签名/收据、持牌数据深度、post-L2 多场所执行。单次
> L2 backup 已新鲜且真实，但不冒充 30 天运营历史或外部检查点；Phase S 与持久部署激活仍开放。

> **2026-07-16 Wave0后P2质量改进:** import-linter依赖治理已引入（4个合约，检测到
> research↔alpha 16处循环依赖，live/隔离与contracts清洁度已验证）；147个空DDD目录
> 已用PLANNED.md标记为架构占位；console_api已验证为按router拆分（7548行分布多视图）；
> L2备份调度代码完整但待积累30天运营证据。GAP_MATRIX维度1已更新反映改进。
> 最新提交：53f3715（docs更新）、45b72e3（import-linter）、b3eb210（DDD标记）。

> **2026-07-15 顶级续审与恢复证据纠偏:** 当前机器权威仍是
> `config/top_tier_requirements.json` + `services/governance/top_tier_assessment.py` +
> 两个生成制品；07-07 手工审计只作历史快照。制品准入已推进到 V4 的双 registry-head
> 事务 CAS，强身份已推进到本地 M3 的应用授权 authority，L2 backup 已推进到 v2 精确
> source contract（10 required + 6 optional，含 approvals/identity/restore-history authority）。续审发现并关闭
> `OPS.RESTORE.DRILL` 假绿：旧 generic live/testnet restore JSON 不再参与顶级计分，单次
> `l2_backup_latest.json` 固定封顶 M3。恢复红线现消费 STARTED/OUTCOME append-only history，
> 动态检查 30 天 cadence/freshness/失败窗口，并要求 exact current-head Ed25519 external checkpoint；
> 默认 trust registry 为空，故不会本地制造 M4。最终完整门禁实跑：backend 2571 passed / 1 skipped，
> 仅 service-graph 与 supply-chain tracked evidence 两项漂移失败；live/trading/tools 986 passed；
> frontend 68 tests + typecheck/build；治理扫描 318 files / 0 violations。
> strong-identity、service graph、supply-chain 与 assessment/matrix 制品仍待获准重生成，禁止沿用旧
> `15/22` 作为当前结论。详见
> `docs/audit/2026-07-15-top-tier-continuation-review.md`。

> **2026-07-15 制品准入 V3 增量:** 软件供应链准入已从调用方自报 policy hash
> 升级为仓库治理的空策略注册表 + 严格 policy v3；稳定 policy 现钉住外部
> receipt authority/key ID/Ed25519 public-key SHA-256，短命动态 receipt 可独立轮换并验签，
> 无需改 policy/registry。新鲜度/撤销/证书有效期/运行时
> verifier/重复 JSON 键/签名载荷空白/时钟回拨均失败关闭。CLI 固定使用
> `data/cache/artifact_admission.db`，policy 决策必须先写入 WAL/FULL、不可更新删除、
> 哈希链 SQLite 权威后才输出；该生产库当前未配置，注册表也无 ACTIVE policy，故真实
> 环境仍为零 `ALLOW`；receipt-authority registry 同样为空。诚实边界不变：尚无真实
> 外部 authority/签名 receipt，source-native checkpoint/revocation proof 未验证，
> 仅支持 Rekor v1 `integratedTime`，无真实有效 bundle 集成夹具，SQLite 非 WORM 且无
> 外部链头封存，也无跨主机复现或独立 admission 部署。详见
> `docs/audit/2026-07-15-artifact-admission-v3-review.md`。最终统一门禁 18/18 全绿：
> 主套件 2420 passed / 1 skipped，live/trading/tools 972 passed，前端 68 tests +
> typecheck/build，治理扫描 313 files / 0 violations；Catalog 12 types/17 definitions，
> 服务图 16 domains/36 edges/0 cycles，供应链 320 components。机器结论保持
> `NOT_TOP_TIER`：15/22 PASS、5 PARTIAL、2 MANUAL、0 FAILED。

> **2026-07-15 event worker assurance 增量:** consumer-group 与 delivery journal 中的新
> worker 不再是无标签字符串，而使用保留内部 ID 并固定标
> `PROCESS_DECLARED_UNAUTHENTICATED`，该值进入既有 coordination/consumer SHA-256 链；
> public status 解码可读名并显式展示 assurance。旧行不重写且标 `LEGACY_UNLABELED`；同名旧活跃
> member 会先追加合法 `LEFT` 再切换，避免双占分区。真实 authority 演练链有效、health PASS，
> external heartbeat 只允许上报固定弱保证枚举，不发送 worker 名。此增量防止把进程名误报成强身份，
> 并不提供外部认证；OIDC/mTLS workload identity、密钥生命周期与 caller-injected lease clock 仍开放。
> 详见 `docs/engineering/specs/2026-07-15-process-declared-worker-assurance.md`。
> 两个 2026-07-15 增量最终统一门禁 18/18 全绿：主套件 2353 passed / 1 skipped，
> live/trading/tools 971 passed，前端 68 tests + typecheck/build，治理扫描 312 files / 0 violations；
> schema Catalog 12 types/17 definitions，服务图 16 domains/36 edges/0 cycles，供应链 320 components。
> 机器结论保持 `NOT_TOP_TIER`：15/22 PASS、5 PARTIAL、2 MANUAL、0 FAILED。

> **2026-07-15 数据质量准入权威增量:** `data_quality_admission.json` 不再只是可覆盖文件。
> 新 `DataQualityAdmissionStore` 将完整 canonical evidence、独立 evidence/decision SHA-256、
> 追加式决策链与 redacted `data.quality.admission.decided` outbox 在同一 SQLite 事务提交；
> publish-before-ack 可幂等恢复，恢复 drain 现覆盖 7 个 owner，console 只显示 ID/verdict/count/hash
> 白名单字段。`forward_track_advance` 已把该权威 verdict 作为记录/兑现的必要条件，authority 故障或
> `BLOCK` 均暂停证据钟；新库也进入 L2 加密备份必需源。真实本地运行仍诚实为
> `quality=BLOCK / lineage=BLOCK`，ID `data-admission-8bb26fcafd7e6672ec8d66ceb6fcfff5`，
> 已 publish/ack/project 且 pending=0。它只关闭本地双写/旁路缺口，不关闭 funding、外部 WORM、
> 强 worker identity、跨主机 HA 或任何 live 红线。详见
> `docs/engineering/specs/2026-07-15-data-quality-admission-authority.md`。

> **2026-07-10 权威覆盖层:** 顶级系统目标、成熟度和缺口不再使用手工完成度百分比。
> 当前机器真源为 `docs/QUANTOS_TOP_TIER_STANDARD.md`、
> `config/top_tier_requirements.json`、`evidence/top_tier_assessment.json` 和
> `docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md`。运行
> `python scripts/assess_top_tier.py --check` 可检测证据漂移；`python scripts/validate.py`
> 已先执行该检查。当前基线: `NOT_TOP_TIER`, 22 项要求中 15 项 PASS、5 项 PARTIAL、
> 0 项 FAILED、2 项需人工/外部证据、2 条红线未关闭。控制台 `/research/system-completeness`
> 已直接渲染同一证据源，不再生成 `completion_score` 或虚构模块。
> `OPS.RESTORE.DRILL` 已于 2026-07-10 用一次性进程内密钥完成真实加密备份与隔离恢复演练:
> 24 个文件逐字节 SHA-256 校验，RTO 773ms / 300000ms，RPO 2ms / 86400000ms；
> 证据在 `evidence/restore_drill_latest.json`，恢复目标从不覆盖活动状态。当前基线升至
> `NOT_TOP_TIER`, 14/22 PASS。
> `PLATFORM.CI.UNIFIED_GATE` 已升级为 M4:同一个 `python scripts/validate.py` 现在按顺序覆盖
> 能力证据漂移、治理扫描、全部后端测试、live 执行栈、前端 typecheck、前端 68 项测试和
> 前端生产构建；任一失败整体非零退出。当前基线升至 `NOT_TOP_TIER`, 15/22 PASS。
> `RISK.RECOVERY.DRILL` 已于 2026-07-12 完成隔离离线重启演练：持久化 kill-switch、
> pre-trade gate 熔断拒绝、SQLite 账本重建、审计链承诺、损坏状态 fail-closed 与审计篡改
> 检测全部通过，最近一次完整门禁 RTO 55ms / 300000ms；证据在
> `evidence/risk_recovery_drill_latest.json`。当前基线升至 `NOT_TOP_TIER`, 16/22 PASS；
> 剩余红线为 `GOV.IDENTITY.STRONG_AUTH` 与 `PLATFORM.SUPPLY_CHAIN`。
> `PLATFORM.SUPPLY_CHAIN` 已于 2026-07-12 建立确定性 CycloneDX SBOM、Python 直接依赖
> 固定、npm integrity 覆盖、GitHub Action 提交 SHA 固定与输入 manifest 摘要校验，平台工程
> 从 M1 升至 M3。真实 npm Registry 审计先发现 5 项（critical 1 / high 1 / moderate 3），已将
> `vitest` 从 2.1.9 升级并精确固定为 4.1.10，复扫为 0；保留证据在
> `evidence/vulnerability/npm_audit_latest.json`，统一门禁首关校验报告、lockfile 与 package manifest
> 一致。Python 初次审计先暴露 `pytest 9.1.1` / `pytest-asyncio 0.23.8` 不可重建冲突，修复后发现
> Starlette/PyJWT/cryptography 共 19 条 advisory；已升级到 FastAPI 0.139.0、Starlette 1.3.1、
> PyJWT 2.13.0、cryptography 49.0.0、pytest-asyncio 1.4.0、httpx2 2.5.0，复扫为 0，证据在
> `evidence/vulnerability/python_audit_latest.json`。三个 GitHub Actions 固定提交已逐一通过 OSV commit
> 查询并保留 0-known 证据。容器侧固定并校验 Trivy 0.72.0 官方归档：原 `python:3.12-slim`
> 扫出 2 critical / 18 high，不能采用；最终改为摘要固定的官方 Python 3.12 Alpine 多阶段构建，
> builder 编译科学计算依赖、runtime 移除 pip 并以 UID 65532 非 root 运行。基础层 5 条 medium/low
> 在最终镜像全部清零，最终 Trivy 结果为 0，且只读根文件系统下正式 API、安全原语与 promotion 组合导入冒烟通过；
> 证据在 `evidence/vulnerability/{actions_osv_latest,container_trivy_latest}.json`。同一固定输入已完成
> 两次 `--no-cache` OCI 构建，BuildKit `v0.26.2` 重写时间戳后两份归档逐字节一致：共同归档 SHA-256
> `d1bdd50a483da77a9124d4f67b4bc8ed589c04b592543007626d6fcdf3dddd9a`，共同 manifest
> `sha256:4fce371aee715f289891b965068e52e7bf7ce6cfc7fc67932f294ff658e134e9`；正式证据与篡改校验在
> `evidence/reproducibility/container_build_latest.json`，且已接入统一门禁。供应链继续补上只验不签的离线 admission
> 边界：固定 `sigstore==4.4.0`，调用方必须提供摘要钉死的 policy 与 trusted root，精确匹配 OIDC issuer/identity，
> 并由 Sigstore 验证 Fulcio 证书链、SCT、Rekor inclusion proof、DSSE 签名及 SLSA subject/builder/buildType/source；
> 任何缺失、漂移或歧义均 `DENY`，且不产生部署/执行/交易权限。诚实边界仍是同主机/同 builder、
> **不是当前源码制品 attestation**，尚无跨主机复现、
> QUANTOS 的真实外部身份签名 bundle、独立 admission 部署或刷新/吊销监控，因此该项仍为 M3/PARTIAL，红线不关闭。
> `AI.MODEL.REGISTRY.REDTEAM` 与 `VALIDATION.CHALLENGER.MODEL_RISK` 已于 2026-07-12
> 从手工不可核验的 M1 升至机器可复算的 M2：新增冻结模型/提示词/工具/评估/红队/挑战者契约、
> append-only SQLite 注册表、真实 `ResearchAgentLoop` 四案边界攻击、注册 prompt/tool 的离线 runtime
> action 边界、敏感输出拒绝与哈希链调用历史，以及基于真实 FX walk-forward 快照的 3 份离线评估。
> 证据在 `evidence/model_risk_baseline.json`。真实结果仍是
> `REFUTED_NO_EDGE`（Ridge -0.4035、GBM -1.1173、单因子 -2.0389），两个挑战者均因绝对
> Sharpe 小于 0 被拒；当前 18 个可交易 FX 标的的 126,113 行 PIT 评估子集已作为确定性 gzip JSONL
> bundle + manifest 纳入版本证据并逐行/哈希验证；现已保留两次真实修订和两条允许调用事件，但没有外部模型提供方、审查身份仅进程角色，
> 因此两项保持 M2/PARTIAL，不虚报 M3。
> `evidence/model_risk_history.json` 已从首条基线追加到第二条真实修订：v1 的 12 个制品与调用事件保持不变，
> v2 真实执行 action override、敏感输出和禁用工具动作三类 runtime 攻击，新增第 2 份红队报告与第 2 条允许调用。
> 同版本改内容、丢弃旧制品、复用调用 ID 改写负载、丢弃/重排旧调用、历史回滚、链篡改或尾部落后当前证据均失败关闭；
> `scripts/validate.py` 首关强制 `model-risk-history:current`。当前为 `revision_count=2 / MULTI_REVISION_HISTORY`，
> `LONGITUDINAL_REGISTRY_HISTORY_NOT_RETAINED` 已按真实证据移除；外部 provider、强审阅身份与负 OOS 结果仍使成熟度停在 M2。
> `GOV.IDENTITY.STRONG_AUTH` 已于 2026-07-12 从共享 `X-Gov-Token` + 自报
> `X-Operator` 的 M1 路径升级为承重的 M2 强身份边界：promotion API 删除本地共享 token，
> 只接受 allowlist 非对称算法签名的 OIDC access token，强制 issuer/audience/时效/MFA、
> RFC 8705 `cnf.x5t#S256` mTLS 证书绑定及读取/申请/复核/批准/执行 RBAC；actor/audit ID
> 只能来自 `issuer|subject`。浏览器已接入 Authorization Code + PKCE S256：Web Crypto 生成
> state/nonce/verifier，10 分钟版本化一次性事务仅存 sessionStorage，access token 仅驻内存，
> 回调先清理并消费事务再换 token。13 个攻击案例与三方 subject 分离证据在
> `evidence/strong_identity_baseline.json`。外部 IdP 运营、可信 TLS 终止器、密钥托管轮换、
> 身份生命周期/撤销/访问复核及 AI 无法人类化的运维证据仍缺，因此该红线保持 M2/PARTIAL，不虚报 M4。
> 本增量完整门禁结果：治理扫描 244 文件零违规；后端 2003 passed / 1 skipped；
> live/trading/tools 844 passed；前端 typecheck、68 tests、production build 全部通过。
> `P1-8/P2-1` 数据质量对抗复核于 2026-07-13 完成代码收口：strict 接入改为写前全批校验 +
> SQLite 原子批写，承重日/1h/1m 驱动器强制 cadence，funding 原始观测与日缓存 fail-closed，
> JSON 缓存原子发布；前向 `record_decision/realize_due` 均要求本轮质量证据，失败 ticker 留 pending，
> `--no-refresh` 不再推进证据钟。随后完成真实四源刷新、永续 mark-price 经济口径修正与
> `evidence/data_quality_admission.json`：固定 24 币前向宇宙中 mark 24/24 PASS、OI 24/24 PASS，
> funding 24/24 BLOCK；全 115 ticker funding 请求均为结构化 `URLError` / `WinError 10060` /
> `TRANSIENT`，没有半份发布或跨 venue 静默替代。perp mark 默认尝试 16 日覆盖，已有两条 pending
> 决策的 14/14 起点/后续定价已补齐；`forward_track_advance --no-refresh` 实跑前后仍各 1 pending、
> 0 realized、0 forward period。随后落地 `services/data/raw_archive.py`(SHA-256 object + immutable
> observation sidecar + parser 计数 + 篡改/schema 校验)，接入 daily/1h/1m/mark/OI/funding 与批量研究入口；
> `raw_data_lineage_backfill` 三轮真实重取最近 8 日，最终 mark 192/192(全 refetch)、OI 192/192
> (112 refetch + 80 verified archive replay，vendor transient 保留 warning)；384 unique objects /
> 1065 observations、0 invalid。evidence 绑定当前三 cache SHA、scope、days 与 archive aggregate。
> funding 0/192 且无 observation，因此 admission schema v2 的
> quality / lineage 双门均 BLOCK，前向驱动器也结构性拒绝缺 lineage 的证据。唯一外部根因仍是 funding
> 源不可达。四源刷新现已具备独立 spawn 进程、source/global 硬 deadline、哈希链历史、覆盖率 SLO 与连续
> 失败告警；两轮真实运行均约 635.5 秒，链完整，但仅积累 2/30 轮且 funding 水位 5 天使 SLO 为
> `BREACH`。历史/spot/1h/1m 旧缓存 backfill、外部 WORM/异地副本仍 open；`crypto_daily.db`
> volume 双乘根因已于 2026-07-13 闭合。详见
> `docs/engineering/plans/2026-07-13-data-quality-enforcement.md`。本增量完整门禁：治理扫描
> 246 文件零违规；后端 2080 passed / 1 skipped；live/trading/tools 856 passed；前端 typecheck、
> 68 tests、production build 全部通过。顶级能力结论不变：`NOT_TOP_TIER`,16/22 PASS。
> `P2-3` 峰值权益状态误全平风险于 2026-07-13 完成无需新阈值的代码收口：新增
> `live/execution/equity_state.py`，把账户级 append-only `live_equity*.jsonl` 最大有效权益作为
> `live_risk_state*.json` 的证据上界；周度驱动器和小时哨兵均已承重。状态损坏、非有限、非正或
> 高于历史时返回 `peak_equity_unknown`，常规执行硬停、kill-switch 闩锁并告警，但纯数据质量
> breach 不进入紧急全平，异常原文件保留取证。现有 Binance/Bybit demo 峰值均与各自全历史最大值
> 精确一致；无历史的未配置 Bybit 状态 fail-closed。未修改 20% 回撤阈值、90% 敞口回撤目标或真实
> 回撤全平语义。剩余边界：本地 JSONL 非 WORM/哈希链，上游异常高权益若同时进入历史仍需 15%
> 跳变告警人工核查，现金流/account identity 调整 high-water mark 与真实回撤确认/延迟策略未实现。P2-3 聚焦
> 243 tests、完整 `tests/live` 764 tests 全绿；最终统一门禁主套件 2102 passed / 1 skipped、
> live/trading/tools 882 passed、前端 68 tests + typecheck/build、治理扫描 246 文件零违规。
> 顶级结论仍为 `NOT_TOP_TIER`,16/22 PASS。
> `P2-7` 拥挤门审计漂移于 2026-07-13 收口：旧风险登记仍称全部阈值 `ACTIVE/UNCALIBRATED`，
> 但代码现实是用户早已签署 crowd_contra `average_warn_only + short-leg z*=3.0`，live 与两条干跑均已承重。
> 本轮把手工 233 周说明升级为 234 周机器证据：确定性冻结 bundle/manifest 绑定四个原始制品哈希，
> `evidence/crowding_gate_calibration.json` 重放真实 gate、绑定实现哈希和三处签署常数，并进入统一门禁。
> `z*=3.0` 阻断 21/234=8.9744%；2.0/2.5/3.0/3.5/4.0 阻断 61/38/21/9/5 周；live/dry
> 阻断周逐项一致。新证据同时纠正旧概括：dry-run rank average 恒 0.500，live extremity-rank average
> 为 0.500–0.655；两者均 WARN-only，BLOCK 权威仍只来自共同 z-score。诚实边界：通用
> 0.60/0.45/0.25/5天默认值仍未获生产微观结构校准，L2 无 order-book/强平标签/生产成交样本。
> 本增量最终统一门禁：主套件 2110 passed / 1 skipped，live/trading/tools 882 passed，前端
> 68 tests + typecheck/build，治理扫描 247 文件零违规；机器评估仍为 `NOT_TOP_TIER`,16/22 PASS。
> `P2-6` 的 3–7 天跨源核价空窗于 2026-07-13 完成当前价路径收口：删除按 `decided_ts`
> 年龄跳过与按日放宽偏差，周度驱动器每轮改取真实 venue `book_ticker` 的 bid/ask/source time，
> 对 CoinGecko `price + last_updated_at` 做 midpoint 偏差、最大年龄与跨源时间偏差检查。缺盘口、
> 交叉盘、无源时间、独立源缺失、陈旧或错时均逐标的冻结；整源异常冻结全部目标，现有仓位不动。
> Binance/Bybit 无时间戳盘口仍可供旧 LIMIT anchor 使用，但不能通过新核价；旧
> `fetch_simple_prices` API 不变。5% 偏差与 5 分钟年龄/错时上限明确为 `UNCALIBRATED` L2 工程基线。
> `_MAX_STALENESS_MS=7 天` 仍服务周频信号，收紧必须先确认 cadence；逐请求 REST 非原子快照且尚无
> 长期观测校准集。P2-6 聚焦 157 tests、Ruff、diff check 全绿；驱动器实现哈希变化后拥挤证据已重生成并
> 重放通过（234 周、21 个 signed-z blocks）。顶级能力结论不因本项改变，仍为
> `NOT_TOP_TIER`,16/22 PASS。最终统一门禁：主套件 2113 passed / 1 skipped，
> live/trading/tools 901 passed，前端 68 tests + typecheck/build，治理扫描 247 文件零违规。
> 同轮机器评估发现供应链基线因 `scripts/validate.py` 摘要漂移短暂落为唯一 `FAILED`；本轮没有只做
> 一次性重生成，而是给 `scripts/generate_supply_chain_evidence.py` 增加确定性 `--check`，同时逐字节校验
> 320 组件 CycloneDX SBOM 与绑定 manifest 的 baseline，当前/篡改测试钉死 fail-closed，并在统一门禁中
> 排在 top-tier assessment 之前。重生成后 `PLATFORM.SUPPLY_CHAIN` 恢复 M3/PARTIAL，矩阵为
> `16 PASS / 0 FAILED / 4 PARTIAL / 2 manual-external`。M4 仍严格开放：跨主机复现、真实外部身份
> Sigstore bundle、独立 admission 部署及 freshness/revocation 证据均未提供，本地 current 门不替代它们。
> `P2-5` 于 2026-07-13 从“总线本体有测试、零真实 producer”推进到关键事件承重：
> `DurableEventBus` 现在以 canonical Event + correlation/causation + 前序摘要形成 SHA-256 追加链，
> 支持旧库迁移、跨连接串行追加、同内容幂等重跑和跨重启因果回放；更新/删除触发器阻断普通篡改，
> 即使绕过触发器修改元数据或删除中间行，全链校验也会拒绝回放和继续发布。干跑单期/多期 pipeline
> 在纸面 fill 前持久化 started + 每意图 pretrade approved/rejected，完成后写 completed；七个正式
> dry-run driver 共用 `data/cache/domain_events.db`。alpha maintenance 的 DEGRADED/PAUSED/RETIRED
> 同样以结构化事件进入该库。两个 service 域实际 import `Event`，聚焦 162 tests + Ruff/diff 全绿。
> 诚实边界：同步调用仍是权威业务路径，总线是证据旁路；kill-switch/live pre-trade/promotion 尚未接，
> 也没有跨进程 consumer、outbox、schema registry、死信或 HA broker，不能称全面事件驱动架构。
> 本增量最终统一门禁：主套件 2129 passed / 1 skipped，live/trading/tools 902 passed，
> 前端 68 tests + typecheck/build，治理扫描 249 文件零违规；机器评估仍为
> `NOT_TOP_TIER`,16/22 PASS。
> `P2-4` 于 2026-07-13 完成第一条真实反向依赖拆除：`services/research/session.py`
> 只保留确定性会话核心与 `ResearchSearchRuntime` 注入契约，不再 import
> `services.intelligence`；正式入口迁到
> `services/intelligence/research_session.py::run_research_session`，由 intelligence
> 固定组装 `ResearchAgentLoop`、guided/evolutionary/MCTS 搜索组件，并拒绝调用方覆盖
> `search_runtime`。仓库内正式工具与端到端测试均已迁移，治理测试逐个 AST 扫描
> `services/research/**/*.py`，禁止该反向边回归。Ruff 全绿，相关 research/intelligence/data/
> governance 套件 752 passed / 1 skipped。诚实边界：这只消掉 research→intelligence；
> research↔alpha/portfolio/risk/execution 等其余环、research 巨型域和 4,934 行
> `console_api/main.py` 仍开放，因此 P2-4 仍是 PARTIAL，不宣称全域无环。
> `P2-4` 第二增量于 2026-07-13 把上述“其余环”从文字债务升级为机器事实：
> `services/governance/service_import_graph.py` 对生产 `services/**/*.py` 建确定性 AST 图，解析
> 绝对/相对/`from services import ...`/字面量动态 import，排除测试目录，任何语法错误失败关闭；
> 两处非字面量动态工具加载按路径/行号/函数显式登记。`config/service_import_policy.json` 必须与
> 当前循环边和动态点完全一致，新增循环、图快照漂移、制品篡改和已拆边的陈旧权限均拒绝。
> `evidence/service_import_graph.json` 已进入 `scripts/validate.py`，并被 320 组件供应链 manifest
> 绑定及 `PLATFORM.CI.UNIFIED_GATE` 机器要求。初始事实为 12 域/26 边/一个 6 域 SCC/14 循环边；
> 随后把共享 `CostModel` 上移到 `quantos/contracts/cost.py`，研究侧兼容重导出，删除唯一的
> portfolio→research 生产依赖。当前为 12 域/25 边/一个 5 域 SCC/12 循环边，portfolio 与
> intelligence 均已退出 SCC。聚焦架构/供应链/顶级评估/成本与组合套件 76 tests 全绿；
> 仍开放的 SCC 是 alpha/execution/governance/research/risk，下一条按图拆 execution→research。
> `P2-4` 第三增量已继续兑现该排序：`PaperFill/OrderSide/FillMethod` 上移
> `quantos/contracts/paper.py`；平均成本对账与 Almgren–Chriss impact/calibration 正本移到
> `services/execution/`，research 原路径仅兼容重导出，类/函数身份与对账恒等式均由测试锁定；
> 正式干跑和 carry driver 改用 execution 正本。execution→research 零命中并由域边界测试钉死。
> 图门先拒绝两条陈旧循环权限，收紧 policy 后当前为 12 域/24 边/一个 4 域 SCC/10 循环边，
> execution 与此前的 intelligence/portfolio 均已退出。新增正本、契约与测试进入供应链 manifest；
> 聚焦 contracts/execution/research/governance/供应链套件 200 tests 全绿。剩余 SCC 为
> alpha/governance/research/risk；下一条高杠杆回边是 research→risk（仅 console 风险诊断消费）。
> `P2-4` 第四增量把 console 风险诊断改为一次性 `RiskConsolePort` 注入：research console
> 未配置时失败关闭且禁止运行时替换，真实 risk/safety 组合上移到 `services/console`，正式入口改为
> `services.console.main:app`。research→risk 零命中，risk 退出 SCC；契约、kill-switch 与 API
> 聚焦 65 tests 全绿。图更新为 13 域/26 边/alpha-governance-research 三域 SCC/6 循环边。
> `P2-4` 第五增量把 mutating promotion HTTP 组合根从 governance 内迁到独立
> `services/promotion_api`；治理域只保留审批、强身份与策略原语，对 alpha/research 零 import。
> OIDC+mTLS+MFA+RBAC、三方审批状态机、审计和全部 L2 安全语义未变。旧 policy 先按预期拒绝
> 4 条陈旧循环权限，再收紧为 alpha↔research 两条。当前机器事实为 **14 域/27 跨域边/
> 唯一 alpha-research 双域 SCC/2 条循环边**；governance、risk、execution、portfolio、intelligence
> 均已退出 SCC。P2-4 仍是 PARTIAL：下一刀是归位 alpha/research 双向消费的 metrics/validation/
> regime、panel/backtest 与 catalog 生命周期 contracts/ports，然后清零最后一个 SCC；约 4,900 行
> console API router 拆分仍在其后。
> 本增量最终统一门禁：主套件 2153 passed / 1 skipped，live/trading/tools 902 passed，前端
> 68 tests + typecheck/build，治理扫描 260 文件零违规；机器评估仍诚实为
> `NOT_TOP_TIER`,16/22 PASS。
> `P2-4` 第六增量清零最后一个循环：性能指标、IC、regime、DSR 与验证门的无状态正本抽到
> `services/validation`，该域对其他 service 域零依赖；research 原路径保留对象身份一致的兼容重导出。
> `factor_set` 归回 alpha，四处 CostModel 改用 `quantos/contracts/cost.py` 正本，alpha 对 research
> 零 import。旧 policy 先按预期拒绝 alpha↔research 两条陈旧权限，再收紧为 `known_cycle_edges=[]`。
> 当前生产 service 图为 **15 域/29 跨域边/0 SCC/0 循环边**，聚焦统计/验证/alpha/治理回归
> 185 tests 全绿。循环依赖半区已经机器闭合；P2-4 仍未整体完成，因为 `services/research` 仍过大且
> `console_api/main.py` 仍约 4,900 行，下一步按只读 router 拆到单模块 <800 行。
> 本增量最终统一门禁：主套件 2157 passed / 1 skipped，live/trading/tools 902 passed，前端
> 68 tests + typecheck/build，治理扫描 268 文件零违规；机器评估仍诚实为
> `NOT_TOP_TIER`,16/22 PASS。
> `P2-4` 第七增量开始拆 console god-module：新增 374 行
> `services/console/api/artifact_snapshots.py`，承接 FX、carry、dry-run、IC、研究发现、
> 1 分钟策略复核、前向/测试网状态与 GoNoGo 共 8 个只读 GET 路由；模块不 import `live/`、`tools/`
> 或 risk，所有缺失/损坏制品继续诚实 fail-closed。FastAPI 0.139 的延迟 router 组合使旧的
> `app.routes` 平铺假设失效，契约守卫改以 OpenAPI 实际 GET paths 为准，payload/provenance/read-only
> 校验一项未放宽。`main.py` **4,875→4,498 行**，新 router <800；架构边界测试锁定 URL、GET-only、
> 行数和实现已移出 composition root。相邻回归 110 tests 全绿，生产图仍为 **15 域/29 边/0 SCC/
> 0 循环边**。最终统一门禁：主套件 2158 passed / 1 skipped，live/trading/tools 902 passed，
> 前端 68 tests + typecheck/build，治理扫描 269 文件零违规。P2-4 仍未关闭：余下 4,498 行继续
> 按生命周期/数据、验证/回测、治理诊断、系统/运行态 router 分批拆到各模块 <800 行。
> `P2-4` 第八增量完成 console god-module 收口：15 个按业务簇划分的只读 router 模块承接全部
> 44 个 OpenAPI paths（含 `/health`），每个模块均 <800 行；`console_api/main.py` 从最初 4,875 行
> 降至 **772 行**，且不再包含任何 route implementation。FastAPI runtime 依赖均由 composition root
> 一次性注入，正式 `services.console.main` 仍提供真实 risk/safety port；治理测试现锁定 URL 集、GET-only、
> 模块与主文件行数、实现移出和 port fail-closed。PIT 裸读白名单随真实 owner 从 `main.py` 精确迁到
> `data_catalog.py`，旧条目同步删除，没有扩大权限。最终统一门禁：主套件 **2174 passed / 1 skipped**，
> live/trading/tools **902 passed**，前端 **68 tests + typecheck/build**，治理扫描 **284 文件零违规**；
> service 图仍为 **15 域/29 边/0 SCC/0 循环边**。机器评估仍诚实为 `NOT_TOP_TIER`：
> **16/22 PASS、4 PARTIAL、2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。P2-4 仍是 PARTIAL：console 半区
> 已闭合，下一步是收窄 oversized `services/research` 的 ports/adapters 与 backtest/validation/api 边界。
> `P2-4` 第九增量关闭第一条 research 数据所有权穿透：新增 research-owned
> `PointInTimeMarketDataPort/VersionedMarketDataPort`，data-owned
> `PITResearchDataAdapter` 保留原 PIT 查询与同一 raw-bar 内容哈希；正式 intelligence 组合层负责适配，
> research session 不再调用 `fetch_bars` 或 import data versioning。console 的真实数据目录枚举上移
> `services/console/data_console.py`，研究侧通过一次性 `DataCatalogRuntime` 消费，未配置失败关闭且不可热替换。
> volume-dispersion 纯检查归入 `quantos/contracts/quality.py`，data 保留同对象兼容导出。由此
> **`services/research` 对 `services.data` 零 import、research→data 边清零**；服务图变为
> **15 域/30 边/0 SCC/0 循环边**。边数 +1 是一条隐藏 research→data 被两条真实组合边
> `intelligence→data`、`console→data` 取代，不是新增反向耦合。最终统一门禁：主套件
> **2184 passed / 1 skipped**，live/trading/tools **902 passed**，前端 **68 tests + typecheck/build**，
> 治理扫描 **290 文件零违规**。机器评估保持 `NOT_TOP_TIER,16/22 PASS`；下一步收窄
> experiment/run/paper registry 的 persistence ports，再继续 backtest/validation/api 子域边界。
> `P2-4` 第十增量完成 registry persistence 隔离：`services/research/ports/persistence.py` 提供
> runtime-checkable `ExperimentStorePort`、`RunStorePort`、`PaperTrackStorePort` 与
> `ForwardLedgerPort`，并承载稳定的 fail-closed persistence errors。ExperimentRegistry、
> RunRegistry、paper advance/health/realize 领域核不再 import concrete store；原本和兑现逻辑混在一起的
> SQLite `ForwardLedger` 已迁到 `paper_trading/forward_store.py`。整个 research 域的 `sqlite3`
> import 精确收敛到四个 adapter owner：experiment/run/paper/forward stores，治理测试禁止回流。
> 旧 `ExperimentStoreError/PaperTrackStoreError` 导入仍与 port 正本对象身份一致；工具、live parity、
> promotion API 和 console 组合调用均已迁移并回归。Crowding calibration 因源码摘要变化曾正确拒绝，
> 重新生成后 policy/summary 逐字段不变（234 周、signed z=3.0、21 个阻断期），未改阈值。
> 最终统一门禁：主套件 **2190 passed / 1 skipped**，live/trading/tools **902 passed**，前端
> **68 tests + typecheck/build**，治理扫描 **292 文件零违规**；服务图仍为
> **15 域/30 边/0 SCC/0 循环边**，机器评估仍为 `NOT_TOP_TIER,16/22 PASS`。
> 下一步分离 backtest engine、validation orchestration 与 API adapters 的内部依赖。
> `P2-4` 第十一增量完成上述内部边界切片。验证流水线不再直连具体
> `backtest_engine.engine`：新增 runtime-checkable `BacktestExecutionPort`、不可变且拒绝空/非有限证据的
> `BacktestOutcome` 与 `DeterministicBacktestAdapter`；端口为必填依赖，返回类型或 OOS equity 长度不合约即
> 失败关闭。walk-forward 与 CPCV 纯正本迁入中立 `services/validation`，research 旧路径仅保留对象身份一致
> 的兼容导出。mutating runner 整体上移独立 `services/research_runner` adapter 域，read-only FastAPI
> router 整体上移 `services/console/api`；不保留会制造回环的旧 shim，`services/research` 生产代码对
> FastAPI/uvicorn/tools/data 均为零 import。15 个 router、44 个 GET-only OpenAPI paths、772 行无 route
> implementation 的 API root 与所有 payload/provenance/fail-closed 语义不变。服务图现为
> **16 域/36 边/0 SCC/0 循环边**；新增域是独立 runner，边数增加来自 console adapter 显式暴露其真实
> 依赖，不是反向环。Python advisory 重新扫描为 0 漏洞，供应链仍为 320 组件。最终统一门禁：主套件
> **2204 passed / 1 skipped**，live/trading/tools **902 passed**，前端 **68 tests + typecheck/build**，
> 治理扫描 **296 文件零违规**；机器评估仍诚实为 `NOT_TOP_TIER`：**16/22 PASS、4 PARTIAL、
> 2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。P2-4 的本轮排定切片已闭合，下一本地优先级转到 P2-5
> consumer/outbox/offset/DLQ；research 的进一步模块化仍是长期开放项，不等于 Phase 14 进程隔离。
> `P2-5` 第二增量完成单机可靠消费基线。`DurableEventBus` 新增独立 SHA-256 追加式 consumer journal：
> 稳定 consumer/worker identity、caller-supplied 时间租约、跨连接排他 claim、租约过期重领、owner/lease
> 校验 ack、持久 offset、失败次数与只存类型+SHA-256 指纹的错误证据；达到上限进入不可变 DLQ 并高声抛错，
> poison event 隔离后后续事件可继续。语义明确为 **at-least-once**，不冒充 exactly-once。
> `DomainEventProjection` 是 WAL/FULL、全链校验、同 seq+ID+内容幂等的真实 read model；7 个 dry-run
> driver 与 maintenance sweep 均推进同一 consumer。console `/events` 首选只读 projection，只公开 5 类
> L2 事件和 payload 字段白名单，缺失/损坏回退 run registry，再无数据才用诚实标注 synthetic。
> dry-run `completed` 进一步采用真 transactional outbox：`DryRunRun` 与 completion event 在同一个
> `dry_run.db BEGIN IMMEDIATE` 事务写入，outbox/dispatch ack 各自为不可变哈希链；dispatcher 先
> `publish_once` 再追加 ack，因此 bus 已提交/ack 前崩溃可安全重放。started/pretrade 仍保持动作前先落事件的
> fail-closed 语义。maintenance JSON 快照跨库 outbox、kill-switch/live pretrade/promotion/deployment
> producers、schema registry、consumer heartbeat/partition 与跨主机 HA 仍开放。拥挤证据因 7 driver 摘要
> 变化先正确拒绝，重生成后仍为 234 周、signed z=3.0、21 个阻断期。最终统一门禁：主套件
> **2225 passed / 1 skipped**，live/trading/tools **905 passed**，前端 **68 tests + typecheck/build**，
> 治理扫描 **297 文件零违规**；服务图仍 **16 域/36 边/0 SCC/0 循环边**，供应链 320 组件，
> 机器评估仍为 `NOT_TOP_TIER,16/22 PASS`。
> `P2-5` 第三增量收口 schema、生产端与跨存储诚实边界。新增不可变
> `EventSchemaRegistry`，对 type/version、payload、source/domain/criticality 与显式 consumer
> compatibility 严格失败关闭；`dry_run.completed` 升至 1.1 并绑定完整 `DryRunRun` SHA-256，1.1
> consumer 仍可读取登记过的 1.0 历史。event bus 旧库迁移改为 `BEGIN IMMEDIATE`，并发首次打开的
> WAL lock 竞态已有回归。除 dry-run outbox 外，human-only promotion 的 `APPROVED` catalog snapshot
> 与 promotion event 现同写 `experiment_registry.db`；maintenance snapshot + transitions 同写新的
> `alpha_maintenance.db` 追加记录，JSON 只作事务后镜像，live consumer 优先权威库且损坏/空库停止新增敞口。
> 三条 dispatcher 均为 publish-then-ack at-least-once，publish/ack 崩溃可幂等恢复。kill-switch arm/reset
> 与 deployment decision 也有严格事件 hook；reset 在清闩前写事件，写失败保持停机。诚实边界仍保留：
> 当时 kill-switch JSON 状态与事件并非同库事务（已由下述第四增量关闭）；deployment event 不是实际 deployment state，真实 live
> pre-trade producer、consumer partition/rebalance、跨主机 broker/复制/HA 均未完成；approval consume、
> catalog/outbox、audit 仍是多库 saga。源码绑定证据已重建并复核：服务图 16 域/36 边/0 SCC，供应链
> 320 components，拥挤证据 234 周/21 blocks，评估仍 `NOT_TOP_TIER,16/22 PASS`。最终统一门禁：
> 主套件 **2250 passed / 1 skipped**，live/trading/tools **908 passed**，前端 **68 tests +
> typecheck/build**，治理扫描 **303 文件零违规**。
> `P2-5` 第四增量关闭 kill-switch 状态/事件原子性缺口。新增 risk-owned、WAL/FULL 的 append-only
> `KillSwitchStateStore`：ARM/RESET transition、完整状态、严格版本事件、correlation/causation 与前序摘要在
> 同一个 `BEGIN IMMEDIATE` 事务提交，transition 与 dispatch ack 各有不可变 SHA-256 链；CAS 拒绝陈旧写入，
> 并发哨兵只保留一个获胜 incident，不能二次 ARM 改写或虚增 `trip_count`。事件必须与 actor、scope、原因指纹、
> 时间和 RESET 的 activation causation 精确一致。`live_track_advance`、`live_risk_sentinel` 与三方 reset CLI
> 均已迁入该权威库；JSON 降为原子兼容镜像，console 在 DB 存在时绝不回退镜像，DB 损坏即显示 TRIPPED。
> 完整 legacy TRIPPED 状态迁移时会同事务重建可验证 activation outbox，信息不足时只 fail-closed、不伪造历史。
> `risk_recovery_drill` 升为 schema v2，重启验证 `kill_switch.db` 哈希、pending ARM outbox、门禁拒绝、有效 JSON+
> 损坏/篡改 DB 故障，实测 RTO 128ms。聚焦回归 180 tests、风险/live/monitoring/关键治理 1009 tests 全绿；
> 服务图仍 16 域/36 边/0 SCC，拥挤证据仍 234 周/21 blocks，供应链仍 320 components。机器评估保持
> `NOT_TOP_TIER`：**16/22 PASS、4 PARTIAL、2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。仍开放：真实 live
> pre-trade producer、deployment 实际状态 outbox、consumer heartbeat/partition/rebalance、跨主机 broker/复制/HA，
> 以及 approval/catalog/audit 多库 saga；单机 SQLite 不冒充跨主机事件平台。最终统一门禁：主套件
> **2265 passed / 1 skipped**，live/trading/tools **916 passed**，前端 **68 tests + typecheck/build**，
> 治理扫描 **305 files / 0 violations**，全部 fail-closed gate 通过。
> `P2-5` 第五增量关闭事件时间单位歧义并把 schema 演进变成正式制品。canonical 最新版本统一为
> Unix epoch microseconds；历史上实际写入 milliseconds 的 kill-switch、alpha promotion、deployment
> v1.0 继续按登记单位解释，新 producer 升至 v1.1 microseconds。projection/console 只依据
> `(event_type,event_version)` 归一化，禁止按数值量级猜测，因此混合历史不会再把旧事件显示到 1970 年附近。
> 新增确定性 `evidence/event_schema_catalog.json`：覆盖 9 个 event types、14 个 schema definitions，逐版本
> 固定 timestamp unit、显式 consumer compatibility、payload JSON Schema + SHA-256，并绑定 12 个实现文件；
> `--check`、`scripts/validate.py` 与 320-component supply-chain manifest 会拒绝缺失、陈旧或篡改制品。
> 受影响 producer/consumer/legacy replay/console projection 聚焦 **172 tests** 与 Ruff 全绿；Catalog/供应链
> 陈旧性检查均通过。机器评估仍诚实为 `NOT_TOP_TIER`：**16/22 PASS、4 PARTIAL、
> 2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。当时下一本地增量是 consumer heartbeat/partition/rebalance、
> deployment state outbox 与更多 data/risk producer；跨主机 broker/复制/HA 仍不得由单机 mock 冒充。
> `P2-5` 第六增量完成诚实的单机 consumer-group 协调。新增
> `services/monitoring/consumer_group.py` 与同库 append-only coordination hash chain：group 的
> partition count/session timeout 创建后不可漂移；heartbeat 维护 membership，活成员按排序做确定性 eager
> rebalance，每次 assignment 变化递增 generation 并 fence 旧 claim/ack。partition key 优先使用
> `correlation_id`，否则使用 `event_id` 的稳定 SHA-256；内部 offset identity 使用保留前缀 + group hash，
> 不与用户 group 名碰撞。partition offset、retry/DLQ 和 publish/projection 仍复用原权威日志并跨重启恢复。
> 正式 `domain-event-projection-v1` 固定 1 partition 保护全序和旧 offset，处理前 heartbeat、正常退出 leave，
> 崩溃才等待 session timeout 接管；时间回拨、旧 generation、非 owner、过期 membership、逆序 ack、配置漂移、
> 双连接竞态、legacy DB 迁移和协调链篡改均失败关闭。聚焦 **234 tests** + Ruff + service graph/Catalog/
> supply-chain checks 全绿，仍是 16 域/36 边/0 SCC、320 components，机器评估保持
> `NOT_TOP_TIER`：**16/22 PASS、4 PARTIAL、2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。
> 诚实边界：这是 caller-clock、进程内 worker identity、单 SQLite authority 的协调，不是跨主机 consensus；
> 协调日志 retention/lag alert、跨主机 broker/复制/HA 仍开放。下一本地项转为 deployment state outbox 与
> 更多 data/risk producer。最终统一门禁：主套件 **2287 passed / 1 skipped**，live/trading/tools
> **916 passed**，前端 **68 tests + typecheck/build**，治理扫描 **306 files / 0 violations**，18 阶段全绿。
> `P2-5` 第七增量把 deployment 从可选同步 event hook 升为 L2 权威 evaluation ledger + 第五条
> transactional outbox。新增 `DeploymentDecisionStore`：evaluation 与 canonical v1.1 event 在同一个
> `BEGIN IMMEDIATE` 写入，evaluation/dispatch 各有 append-only SHA-256 链、UPDATE/DELETE trigger、
> 精确幂等和同 subject/environment 时间单调约束。状态枚举只有 `DENIED` 与
> `ELIGIBLE_NOT_APPLIED`，代码/表约束均不存在 `DEPLOYED`；LIVE 在 L2 只会记录 DENIED。
> 正式 `evaluate_and_record_deployment` 先走纯 gate，再原子落状态/outbox；authority 不可用时原本允许的
> PAPER 决策降为 `DEPLOYMENT_STATE_ERROR` 拒绝。事件 ID 绑定 actor/裁决/原因/版本/时间，同毫秒不同裁决
> 不冲突；grant/approval ID 进入 causation。`tools/deployment_events.py` 只做 publish-then-ack + projection，
> publish 后/ack 前崩溃可安全重放，不接受申请也不执行部署。console `/events` 仅白名单展示安全字段。
> 聚焦 **209 tests** + Ruff + service graph/Catalog/supply-chain checks 全绿，机器结论仍为
> `NOT_TOP_TIER`：**16/22 PASS、4 PARTIAL、2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。
> 诚实边界：本轮关闭的是 deployment evaluation state，不是实际 APPLIED/DEPLOYED 状态；真实部署执行器、
> staging/prod 隔离、live pre-trade/data producer 仍开放。下一本地项转为更多 data/risk producer 与
> consumer lag/retention 告警。最终统一门禁：主套件 **2305 passed / 1 skipped**，live/trading/tools
> **917 passed**，前端 **68 tests + typecheck/build**，治理扫描 **308 files / 0 violations**，18 阶段全绿。
> `P2-5` 第八增量补齐首个真实 data producer、第二类 risk producer 与单机事件健康告警。
> `data.market.refresh.recorded` 现在和防篡改 `RefreshHistoryStore` 的主记录、规范化 source 行在同一
> `BEGIN IMMEDIATE` 提交；第六条 outbox/dispatch 双追加链支持 publish-before-ack 崩溃重放，旧库仅在
> outbox 表完全不存在且原主链可验证时确定性回填，部分迁移或篡改拒绝修复。事件只含 run/outcome/counts/
> duration 与两条 SHA-256，不复制自由文本源错误。`research.risk.dry_run.evaluated` 则与精确
> `DryRunRun.reproducibility_hash` 和 run 内 `REAL_RESEARCH_RISK_SUBSET` 逐字段相等后，和 completed event
> 共用既有 dry-run 事务 outbox；它仍是 4 个诚实输入引擎、5 个显式 skipped 的诊断，不是晋级许可。
> Catalog 升为 **11 types / 16 definitions / 18 implementation bindings**。`DurableEventBus.operational_snapshot`
> 按真实 partition 分配统计 pending（不拿全局 seq 差冒充 lag），按版本 schema 归一化事件年龄，并汇总
> group DLQ、三条 journal 行数和 SQLite allocated bytes。`EventBusHealthPolicy` 输出 PASS/WARN/BREACH；
> console `/events` 与可调度的 `tools/domain_event_health.py` 分别只读展示和复用 alert sink 投递。
> retention 明确为 `APPEND_ONLY_ALERT_ONLY`，不自动 DELETE/VACUUM。自动归档/外部 seal、独立告警 SLA、
> 强 worker identity、真实 live pre-trade、跨主机 broker/复制/HA 仍开放，SQLite 仍不冒充 Kafka。
> 最终统一门禁：主套件 **2321 passed / 1 skipped**，live/trading/tools **920 passed**，前端
> **68 tests + typecheck/build**，治理扫描 **311 files / 0 violations**，18 阶段全绿；机器结论仍为
> `NOT_TOP_TIER`：**16/22 PASS、4 PARTIAL、2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。
> `P2-5` 第九增量补上独立 outbox recovery drain。`tools/domain_event_outbox_drain.py`
> 以固定顺序覆盖 dry-run、experiment/promotion、maintenance、kill-switch、deployment、data-refresh
> 六个 owner store，全部 `create=False`；缺库标 `UNAVAILABLE`，部分存在只报 `PARTIAL`，损坏只保留异常类型且
> 不阻断其他独立 source，event authority 打不开也会原子落 `BREACH` 快照。所有 dispatch 完成后只推进一次
> shared projection；若所有 source 与 event authority 都不存在，不创建空数据库。该工具只搬运 event + 追加 ack，
> 不评估策略、不改 domain state、不 reset kill-switch、不部署、不下单、不清理历史。调度注册与外部告警 SLA 仍是
> 独立运维工作，单机恢复 drain 不冒充 broker/分布式事务/exactly-once。
> 新恢复工具 7 项聚焦测试、跨 outbox/治理 77 项回归与 Ruff/diff 均通过。最终统一门禁：主套件
> **2322 passed / 1 skipped**，live/trading/tools **927 passed**，前端 **68 tests + typecheck/build**，
> 治理扫描 **311 files / 0 violations**，18 阶段全绿；Catalog 仍为 11 types/16 definitions/18 bindings，
> 服务图仍为 16 域/36 边/0 cycles，供应链仍为 320 components。机器结论保持 `NOT_TOP_TIER`：
> **16/22 PASS、4 PARTIAL、2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。
> `P2-5` 第十增量从一次现场安全冲突开始：本机两条 `--submit`、一条 `--act`、ops API 与其
> live watchdog 仍为 Enabled，且 `live/` 路径不消费 canonical `SafetyConfig`。五个任务已可逆禁用并逐项
> 复核 Disabled；`register_schedules.ps1` 重写为 L2-only module allowlist，运行时解析 Python/workspace，
> 用独立 executable/arguments/working-directory + `IgnoreNew` 注册六条 research/paper/maintenance/event-ops/report
> 任务，治理测试禁止 live module/action flag/硬编码路径/shell action。新 `domain_event_ops.py` 每小时固定
> drain→health，任一失败不阻断另一半，只留异常类型，写统一状态/本地告警并以 0/1/2 表达结果。首轮计划任务
> 按时运行并返回 1：projection/health PASS，dry-run/experiment/data-refresh 三 owner store BREACH，另三库
> UNAVAILABLE，未伪装成绿。首次手工探针同时抓到 shared authority 仍用 `create=True` 的缺陷：一个 4 KiB
> 无 `event_log` SQLite 被初始化为空 authority；未猜测回滚用户文件。现存 authority 现先经只读三表 preflight
> 再 `create=False`，drain/health 两条反例钉死缺 schema 文件逐字节不变。独立外部告警 SLA、安全归档/seal、
> scheduler watchdog 与工作的备份仍开放；单独 `QUANTOS_backup` 最近运行失败。
> 聚焦验证为 event/monitoring **79 passed**、governance/supply-chain **76 passed**，Ruff、PowerShell parse、
> Catalog/service graph/supply-chain checks 全绿。最终统一门禁：主套件 **2327 passed / 1 skipped**，
> live/trading/tools **938 passed**，前端 **68 tests + typecheck/build**，治理扫描 **311 files / 0 violations**，
> 18 阶段全绿；Catalog 仍为 11 types/16 definitions/18 bindings，服务图仍为 16 域/36 边/0 cycles，
> 供应链仍为 320 components。机器结论保持 `NOT_TOP_TIER`：**16/22 PASS、4 PARTIAL、
> 2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。
> `P2-5` 第十一增量关闭首轮 scheduler 暴露的三份 legacy owner-store BREACH。新增
> `tools/domain_event_owner_migration.py`，plan/apply 明确分离：拒 WAL/SHM，immutable SQLite + integrity check，
> 对 protected tables 的 schema/有序逐行摘要，SQLite logical backup，同目录 staging 上调用 owner migration，
> target chain + protected digest 双验证，替换前再验源 SHA，最后 atomic replace/失败 rollback；一个坏源不阻断
> 独立好源，异常只留类型。真实维护窗口先禁用 event task，三库均迁移 PASS：dry-run 7 行、experiment
> 2 trials/2 alphas/8 evaluations、refresh 5 history/20 source rows 的 protected digest 前后完全一致，三份本地
> rollback DB 保留。dry-run/experiment 不伪造历史；refresh 依既有 legacy contract 回填 5 条事件，全部
> publish/ack/project，pending=0、DLQ=0。`RefreshHistoryStore(create=False)` 也修为缺 outbox schema 时在 WAL 前
> immutable preflight，失败不再制造 sidecar。计划任务已恢复并现场返回 **2/PARTIAL**：三 migrated owners PASS，
> alpha-maintenance/kill-switch/deployment 三个未启用 authority 仍 UNAVAILABLE，未为空绿而造库。同机 migration
> backup 不冒充 off-host 灾备；失败的 `QUANTOS_backup` 与独立 scheduler watchdog 仍是下一优先级。
> `P2-5` 第十二增量关闭了“失败任务继续静默跑”和“备份源码存在即 PASS”两处运营假绿。旧
> `QUANTOS_backup` 的现场根因是用户级口令只有 11 字符，低于 12 字符 fail-closed 下限，且没有
> `QUANTOS_BACKUP_DEST`；旧任务现已禁用。新 `tools/l2_backup.py` 明确覆盖 10 个必需 L2 authority store，
> 对活动 SQLite 使用 online backup API，逐制品 AES-GCM 加密，生成加密 manifest，原子发布前执行隔离恢复、
> SHA-256 与 SQLite integrity_check；默认只接受 UNC/不同卷目标，key/ 与 broker credentials 不在 allowlist。
> `QUANTOS_scheduler_watchdog` 已每 15 分钟注册并真实运行，不 import 被监控 worker，校验 task state、精确 module
> action、working directory、last result 与 cadence。现场结果为 **PARTIAL/2：6 PASS、1 PARTIAL、1 UNAVAILABLE、
> 0 BREACH**；event ops 的 2 是已知未启用 authority，backup 因配置不完整保持 UNAVAILABLE。成熟度 checker
> 已从 `paths_exist` 升为 operational evidence，当前机器结论因此诚实回落为 `NOT_TOP_TIER`：
> **15/22 PASS、5 PARTIAL、2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。下一步需人工从受控 secret source 配置
> 12+ 字符口令及 off-host/不同卷目标并保留首份证据；本机 watchdog 仍不能发现整机/Task Scheduler 同时失联，
> 外部 heartbeat/WORM retention 继续开放。
> 本增量最终统一门禁 **18/18 阶段全绿**：主套件 **2334 passed / 1 skipped**，live/trading/tools
> **954 passed**，前端 **68 tests + typecheck/build**，治理扫描 **311 files / 0 violations**；Catalog
> **11 types/16 definitions**，服务图 **16 domains/36 edges/0 cycles**，供应链 **320 components**。
> `P2-5` 第十三增量补上带外失联与链截断防护接缝。`EventAuthorityCommitment` 统一承诺 event、
> delivery journal、consumer-group coordination 三条已验证链的 count/max-seq/head，并以 overall SHA-256
> 封装；`extends_authority_commitment` 允许正常追加，但对截断、整体替换、锚点位置 hash/行数漂移失败。
> `tools/domain_event_seal.py` 经 read-only SQLite snapshot 生成 content-addressed、create-once 锚点，已存在
> 同名不同内容时拒绝覆盖；明确标 `PARTIAL_EXTERNAL_WORM_UNVERIFIED`，不删除/VACUUM authority。真实诊断
> 锚定 5 event / 10 delivery / 33 coordination rows，随后 extension verify PASS，临时锚已清理。
> `tools/external_heartbeat.py` 只向 operator-owned HTTPS 端点发送白名单健康摘要，拒绝 userinfo、fragment、
> loopback 与 redirect，endpoint 只留 SHA-256 指纹；当前无 endpoint，手工运行诚实写 `UNAVAILABLE` 且未发网。
> 两项均为配置存在才注册；首次 reconciliation 暴露 `schtasks /query` 在 missing task 上被 PowerShell Stop
> 提前中止，现改原生 `Get-ScheduledTask`/`Disable-ScheduledTask` 并真实重跑成功。watchdog 当前为
> **6 PASS / 1 PARTIAL / 3 UNAVAILABLE / 0 BREACH**：backup、external heartbeat、event seal 均未配置，
> 不能冒充 off-host/WORM/带外 SLA 已完成。
> 本增量首次全门禁发现 `test_source_process_deadline...` 在 Windows 全负载 spawn 冷启动下把健康 worker
> 的 2 秒非目标预算耗尽；隔离复跑通过，生产 hard deadline 逻辑未改。测试把健康/global 预算调到 5 秒，
> 故意 hang 源仍为 200ms，四路并发压力 4/4 通过。最终统一门禁 **18/18 全绿**：主套件
> **2338 passed / 1 skipped**，live/trading/tools **969 passed**，前端 **68 tests + typecheck/build**，
> 治理扫描 **311 files / 0 violations**。机器评估保持诚实：**15/22 PASS、5 PARTIAL、2 MANUAL、0 FAILED**。
>
> `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md` 与旧机构补全计划保留为历史研究输入；
> 新开发排序必须以能力矩阵中的 `classification`、`red_line`、目标成熟度和
> `next_action` 为准，同时始终保持 `L2_DRY_RUN_ONLY`。

> **用途**:换新上下文/新会话时,**只读这一个文件就能接着干**。每当上下文快满时刷新它。
> 约定:本文中文叙述;代码标识符英文。最近更新锚点:见末尾"提交锚点"。

---

## 0. 一句话 + 安全边界(不可让)
QUANTOS = 安全关键的 **AI 量化研究平台**,就绪等级 **L2_DRY_RUN_ONLY**(只研究 + 干跑模拟,
**无实盘、无券商、无真凭证**)。权威规格:`QUANTOS_MASTER_BLUEPRINT.md`;开发计划:
`docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md`;因子方法论地图:`docs/factor-methodology-implementation-map.md`。
**当前活路线(2026-07-10 起):** `docs/QUANTOS_TOP_TIER_STANDARD.md` +
`docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md`;旧计划仅作历史输入。

**机器强制的不变量**(改动绝不能破):
- `LIVE_TRADING_ENABLED=false / REAL_BROKER_ENABLED=false / ALLOW_ORDER_SUBMISSION=false /
  ALLOW_AI_DIRECT_TRADING=false / REQUIRE_HUMAN_APPROVAL=true`。
- **纯 Python 标准库**(禁 numpy/scipy/torch/pandas/ccxt 等)、**确定性**(无 RNG、无 wall-clock;
  前端新代码无 Date.now/Math.random)、**失败关闭**、**离线门禁**、**可复现**。
- **容器化(神圣)**:非人类 AGENT actor **永不可达 APPROVED**;一切经未改动的 `ResearchAgentLoop`
  (PROPOSE/EVALUATE),`guardrails.assert_action_allowed`。
- 网络**只在门禁外** `tools/`;`services/` 整棵树**零网络 import**(机器强制)。
- 不提交密钥;密钥读自 `$COINGECKO_API_KEY` 或 gitignore 的 `key/coinkey.txt`,绝不打印/提交。

## 1. 单一门禁(改完必跑,绿了才提交)
```bash
python scripts/validate.py     # AST 治理扫描 + 全 pytest 套件(contracts/governance/research/trading)
```
隔离 pytest 需 `PYTHONPATH=/h/code/QUANTOS`(从仓库根跑)。`main==master` 手动同步:
`git branch -f main master`。提交信息中文;结尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

## 2. 工作节奏(已确立)
brainstorm(大改才需) → 先写测试(tests-first)→ 实现 → `validate.py` 绿 → 提交 → 必要时对抗式评审
(Agent/Workflow)→ 修 P0/P1 → 再提交。**最小安全改动;绝不藏/删失败测试。** 用户偏好:
聊天用**简单中文、少术语、术语用大白话解释**(见记忆 `reply-plain-chinese`);文档/注释也中文。

## 3. 已建成(本仓真实代码,按能力)
**数据**:`services/data/` —— PIT 存储 `persistent_store`、**`caching_store.py`(内存读缓存:回测从
小时级降到秒级,与 SQLite 逐位一致,接进所有驱动)**、survivorship-free `universe_store`、`versioning`、
质量/归一、`market_csv`、`market_provider`(多资产离线接缝 + ingest)、`adapters.py`(声明式 SourceSpec +
数据源注册表)、`calendar_store`、`fundamental_store`。
**加密数据(门外只读)**:`tools/coingecko_fetch`(OHLC 4h)、`tools/coingecko_volume_fetch`(真实成交量)、
**`tools/cryptocompare_fetch`(中立聚合商 histoday,多年日线·含 2022 熊市,免费 key 读自 key/cryptocompare*.txt)**;
治理边界 `tests/governance/test_crypto_data_boundary.py` 钉死**三**抓取器只含 stdlib+services/tools、无 SDK/无下单。
**信号/特征**:`signals.py`(8 价格算子)、`volume_signals.py`(4 量能算子)、**`alpha_expression.py`(截面 alpha 引擎:
PIT 面板上组合时序算子 + 截面算子 rank/scale/demean/zscore + 逐元,解锁 Alpha101 那类真因子;无前视测试钉死)**、leakage 检测。
**因子/评估**:`factor_engine`(IC/ICIR)、`alpha/evaluator`(DSR/PBO/regime 门、失败关闭、
**opt-in `compute_regime_evidence`**)、`catalog`(试验计数/var_sr)、`config`、`multiple_testing`、
`orthogonalize`、`panel_backtest`、`capacity`、`walk_forward`(**带 embargo 禁运**)。
**因子发现**:`gp_tree.py`+`gp_genome.py`(**GP 表达式树**)、`evolutionary_search.py`
(flat P/F/X + GP 'T';opt-in 奖励:`parsimony_lambda/mu`、`redundancy_lambda`(增量去冗余)、
`diversity_lambda`(**频繁子树规避**))、**`mcts_search.py`(MCTS/UCT 导航)**、`guided_search`(UCB)、
`search_space`、`interaction_factors`、`fundamental_factors`。
**主线编排**:`intelligence/research_session.py::run_research_session`（固定受控搜索 runtime；
`research/session.py` 只保留可注入的确定性核心）——模式 `evolutionary/gp/mcts`(+guided);
搜后 `synergistic_set`(去冗余协同集);**`oos_validate=True` 自验证(对自己挖的因子跑无窥视 OOS)**;
`regime_evidence`。
**验证/诚实**:`oos_validation.py`(**无窥视滚动选择 OOS**)、`validation/`(deflated_sharpe/walk_forward/
gate/pipeline)、`labeling.py`(三重门+元标签)、`regime.py`(波动率状态分档)、`information.py`(互信息)、
`factor_set.py`(协同集 + novelty)、`subtree_stats.py`、`sample_weights.py`、`factor_timing.py`、`frac_diff.py`。
**容器化/生命周期**:`agent_loop`(ResearchAgentLoop)、`lifecycle` 状态机、`guardrails`、Actor。
**组合/风险**:`portfolio`(risk_model/construction/attribution)、`research_risk` 门。
**通往实盘的安全脚手架(轨道3,2026-07-01)**:`services/trading/broker_adapter.py` —— 把「换适配器」形式化成
`BrokerAdapter` Protocol + `DryRunBrokerAdapter`(透明超集包装现有引擎、零改动、流水线 drop-in)+ `LiveBrokerAdapter`
(结构性拒绝:L2 下 `live_trading_armed` 恒 False → 构造即 raise,绝不 import 券商 SDK)+ `assert_dry_run_adapter`
fail-closed 守卫接进 `simulate_fills` 首行。治理 `test_broker_adapter_boundary.py` 钉死无 SDK/网络 + L2 下 live 不可
实例化。子项目2 `services/governance/approval.py` —— 多方人工批准状态机:不可变 `ApprovalRequest` + 纯转换
(`open_request/review/approve/reject/consume`,每步哈希链),硬不变量 `REQUESTED→REVIEWED→APPROVED→CONSUMED` +
提出/复核/批准三个两两不同真人 + 一次性绑 subject;`verify_multi_party_grant` fail-closed;接进 `KillSwitch.
reset_with_grant` 与 `evaluate_deployment(grant=)`(只做加法、旧路径零回归、LIVE 仍挡死)。治理钉死无 SDK/网络 +
非人到不了 APPROVED。子项目3 `services/governance/signing.py` + `docs/PHASE_14_PROCESS_ISOLATION.md` —— 出进程签名
权威接缝(收口):`SignedDecisionRef` + `NullSigningAuthority`(进程内无密钥、`sign()` 即拒)+ `verify_signed_ref`
fail-closed(无出进程权威恒 False,伪造权威也 False);威胁面文档列清所有「受信代码可绕过」面 × 出进程签名怎么堵 +
post-L2 迁移路径。诚实决定:**不接进 L2 执行路径**(强接要么死代码要么堵死干跑)。治理钉死无密钥材料 + 无 SDK/HSM
import。**轨道3 = 3 子项目全 landed:接缝(✓)、多步人工批准状态机(✓)、Phase 14 进程隔离设计(✓)。** 全程 L2 一寸不让。
**执行/干跑(端到端能跑的系统,2026-06-30)**:`risk/pre_trade`(23 检查电池 + latched `KillSwitch`,
**运行时 chokepoint**)、`execution/{intent_builder,dry_run_pipeline,dry_run_store}` —— `run_dry_run_pipeline`
把权重→目标手数→意图→**逐意图过门(fail-closed,latched→全拒)**→干跑适配器纸面成交(**绝不真发**)→
平均成本对账(恒等式残差≈0)→哈希链审计→可复现 `DryRunRun` 落 `data/cache/dry_run.db`。驱动器
`tools/dry_run_carry.py`(carry 最新腿 × vol-target 杠杆端到端;首跑流动 58 币→23 名组合,门批准 23/拒 0、
纸面成交 23、成本 9.9bps×换手=约 −$956、残差 0.0)。`trading/dry_run_adapters` 无券商 SDK。把预交易门从
"已测死代码"变成"运行时可证强制"。**`run_multi_period_dry_run` + `tools/dry_run_carry_multi.py` 升级为周度滚动**:
逐周增量调仓单过门(fail-closed)→纸面成交→`reconcile` 价格 MTM + 资金费现金流(carry 真边)→raw/vol-target
两条净值曲线→多期 `DryRunRun.detail.equity_curve`(首跑:流动 58 币×313 周,门批准 15908/拒 67,总收益 126.7%、
夏普 0.45、回撤 77%、累计资金费 $166k、残差 1e-9)。**已上 console**:`/api/v1/console/dry-run` + 干跑页
`DryRunPipeline.tsx`(单期 + 多期 SVG 净值曲线,nav「干跑流水线」,浏览器验证过)。诚实:周度滚动 carry 多数收益是
L/S 价差(非 beta 中性、回撤大),funding 较小稳定项——执行现实把理想化 carry 的尾巴暴露出来。**美元中性实验**(每期记
`net`/`avg_net_exposure_pct`,console 出 KPI):证伪"回撤=舍入净敞口"假设——$1M 时净敞口已仅 ~1.8% 且 77% 回撤
$1M↔$100M 规模不变,故回撤是 **L/S 截面价差本身**、非净市场敞口/舍入;funding(~16.6% 累计)是骑在波动价差上的
稳定边、无法干净隔离。**实盘 = 同接口换真券商适配器 + 人工批准 = 非目标。**
**持久化**:`experiment_registry`(stdlib sqlite)、**`paper_trading/{store,advance,health}.py` 前向 paper 追踪库
(append-only `data/cache/paper_track.db`,真 train/paper 切分,驱动器 `tools/crypto_paper_track.py`;喂 LifecycleMonitor
建议 / 衰退维护 / Alpha Zoo 时间维。`live_ic` 仍 null——离线源无截面 IC 面板;纸面夏普在 ~24 期尾巴是噪声非 edge)**。
**前端**:`apps/research-console`(React18+Vite6+TS+Tailwind,premium fintech;26 页;**Crypto Live 页**
显真实加密因子 + OOS 裁决 + 协同集面板)。**Console API**:`research/console_api/main.py`(只读快照 +
`/api/v1/console/crypto-live` 真实数据端点,provenance=REAL_CRYPTO_COINGECKO_OFFLINE_SNAPSHOT)。
**门外驱动(tools/)**:`crypto_research_run`、`crypto_gp_run`、`crypto_oos_validation`、`crypto_volume_research`。

## 4. 当前实证结论(诚实)
- **候选第二边 `crowd_contra`(持仓反向,2026-07-01 用截面 IC 面板重新确认)**:反着做拥挤的散户多空
  仓位(14 大币 OI 数据),mean_ic=0.035、**t-stat=4.32(统计显著)**、过 4/5 项证伪检验(滞后1天/10bps
  成本/子集/对 carry 增量存活,唯一没过的是"跨年广泛性"——2020-21 接近零、2022 年后才显著)。此前研究
  自己的结论就是"proceed to paper-track"。**2026-07-01 已把它送进干跑流水线**(`tools/dry_run_crowd_contra_multi.py`,
  第三条策略过策略无关的 `run_multi_period_dry_run`):14 大币 × 232 周 × 10bps × 含资金费——**总收益 +185.6% ·
  夏普(周→年)1.21 · 最大回撤仅 12% · 门批准 3066/拒绝 0 · 对账残差 7e-09**,三条里执行现实表现最好(夏普远高于
  carry 0.45/trend 0.40,回撤远低于 carry 77%/trend 37%)。**诚实边界:这是对已知历史的执行现实回测(整手+门+
  成本+换手,恒等式成立),非前向/实盘;宇宙仅 14 大币;证伪已过≠前向必赚。** 走干跑而非前向 paper-track——缓存上
  "前向 paper-track"只是留尾回放(已被 no-peek OOS 折叠覆盖);是否送前向 paper-track/加仓位仍是独立的下一步。
- **现实主义引擎 RE-1 首跑抓到自欺(2026-07-01,`services/research/realism/`)**:Almgren-Chriss 冲击 + 容量衰减带
  应用到 carry/crowd_contra。**关键发现:参与率上限(10% ADV/期)是绑定约束——真容量 carry ~$2M / crowd_contra ~$7M**;
  只看冲击会误报 $743M–$4.1B / $1.69B–$9.1B(**~300× 高估**)。这正是差距报告警告的「松现实主义 × 关卡抓海市蜃楼 =
  无 edge 信号在组合层看起来可部署」——引擎首跑即抓到,且指出**可部署性受限于*构造*(最小 ADV 名满权重),非仅信号**。
  诚实标 `UNCALIBRATED_PRINCIPLED`(冲击系数未校准 → 区间带)。
- **现实主义引擎 RE-2 情景重演 + 尾部(2026-07-01,`services/research/realism/{scenario,tail_risk}.py`)**:把 carry/crowd
  拉过真实危机(2021-05/LUNA/FTX/2023-03 银行)+ 尾部量化。**诚实发现:① 美元中性预判证实——崩盘 beta 极低(下尾依赖
  carry 0.13 / crowd 0.0),crowd_contra 在 LUNA(+10.6%)/FTX(+6.5%)崩盘里反赚——尾部风险是*截面价差击穿*非市场 beta;
  ② 真尾部内生(carry 最差 19% 在 2020-11 牛市,非崩盘;干跑 77% 是 vol-target 加杠杆口径);③ 尾偏肥 Hill ~2.4–2.7;
  ④ **2026-07-13 更正**:`crypto_daily.db` 是统一 quote USD volume，`$3.7Q/$20k` 来自容量消费端二次乘价；
  已加显式 volume semantics 并修复，trend 容量重算约 `$47.9K`(短样本/未校准)。** RE-2 剩 Copula 全拟合。
- **FRED 宏观数据方向第一增量(2026-07-01,`tools/fred_fetch.py` + `services/research/macro_regime.py`)**:股票免费源本环境被墙
  (Stooq bot-wall / Yahoo 429,不绕过)→ 转 FRED(可达)。抓 6 条市场型日频序列(VIX/DGS10/T10Y2Y/HY-OAS/DXY/DFII10,39271 行)
  填进已建但从未填的 `fundamental_store`(PIT kt=et)。**关键发现:carry 与 crowd_contra 宏观 regime 画像相反、互补**——carry
  爱平静/risk-on(低VIX SR+2.36 vs 高VIX+0.65,正常曲线+2.08 vs 倒挂+0.67),crowd_contra 爱压力/risk-off(高VIX+1.96,倒挂+1.76)。
  crowd 正好在 carry 走弱处走强 = 强分散故事,系统化了 RE-2 的 LUNA/FTX 危机发现。诚实:regime=全样本分位(回顾性非PIT择时)、
  gross 无成本、HY-OAS 覆盖 155/期;股票 + 基本面 + 经济发布类(需 BYO-CSV/ALFRED)= 待办。
- **组合分散验证兑现(2026-07-01,`services/research/book_combine.py`)**:regime 互补 → 组合 carry+crowd 收益流。
  **corr=-0.04(近乎不相关)→ 组合夏普 1.95 > 单条 1.44/1.30,最大回撤 8% = 单条一半,总收益~187% 保住,VIX regime 被抹平。**
  两条正交 crypto 边合成 Sharpe~2/回撤 8% 的强分散。诚实:收益流层面(不同宇宙/规模、231 共同周、gross 无成本 RELATIVE 比较);
  同一执行 book(统一宇宙 + 干跑流水线)= 自然下一步。
- **对标顶级路线图 Rank #1 已做 + 诚实更正(2026-07-01,`tools/dry_run_combo_riskparity_multi.py`)**:风险平衡统一执行
  book(rolling 逆波动 sleeve,PIT)+ portfolio-of-books 配置——**carry+crowd 分散不敌执行现实**:三种方式 VT 1.20/1.14/1.21
  均 < crowd_contra 单条 VT 1.38。1.95/8% 是 gross 理想化(carry gross~1.44),净-成本下 carry 仅~0.58 拉不动 crowd,
  净曲线相关 +0.04。执行流水线抓到理想-vs-真的差。**deployable = crowd_contra 单条。** 见 `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`。
- **对标顶级路线图 Rank #2 已做(2026-07-01,Barra-lite read-only 诊断第一版)**:`services/portfolio/barra_lite.py`
  新增纯函数 read-only 因子风险诊断核(`winsorize/zscore/corr_exposure/build_barra_lite_model/barra_lite_report`),
  复用现有 `FactorRiskModel` + `risk_decomposition`;`tools/barra_lite_diagnostics.py` 从本地缓存构建
  5 因子(`carry/crowding/liquidity/market_beta/volatility`)并输出 gitignored 的 `data/cache/barra_lite_snapshot.json`。
  首跑:58 symbols / 5 factors;carry total_var 0.6544、crowd_contra 1.3969、carry_crowd_equal 0.5733。
  诚实边界:诊断-only,不等同完整机构级 Barra 因子库,不改权重/晋级/闸门/干跑/券商路径;治理测试钉死无网络/券商/订单/凭证 surface。
- **对标顶级路线图 Rank #3 已做(2026-07-02,拥挤门 primitive)**:`services/portfolio/crowding_gate.py`
  新增纯函数 fail-closed crowding gate,输入候选权重 + caller-supplied crowding evidence,输出 `PASS/WARN/BLOCK`。
  score=按绝对权重的拥挤暴露均值;缺失覆盖不足、显式 None/NaN/inf、空 book、非有限权重均 `BLOCK`。
  诚实边界:primitive only,未自动接入 `build_portfolio`/干跑/预交易门/券商/生命周期;后续若要改变组合构建行为需单独 spec。
- **对标顶级路线图 Rank #4 已做(2026-07-02,协方差感知多 book 资本配置 primitive)**:`services/portfolio/capital_allocator.py`
  新增纯函数 fail-closed covariance-aware capital allocator,输入多策略收益流 + 可选容量/质量/前次权重,输出 `ALLOCATED/BLOCKED`
  研究 AUM 权重建议、turnover 与诊断(`book_stats`/capacity/effective caps/risk contribution)。默认使用 lower-confidence expected return
  (`mean - mean_shrink_z * vol/sqrt(n)`) + shrunk covariance + 现有 constrained mean-variance optimizer,并叠加 per-book cap、capacity cap 与 no-trade band。
  诚实边界:研究 allocation primitive only,不读 `DryRunStore`/`run_registry`,不写 DB,不自动调仓,不接 dry-run/pre-trade/broker/lifecycle/readiness/console;
  治理测试钉死无网络/券商/订单/凭证 surface。`quality_multiplier` 来源和容量衰减曲线仍是后续独立 primitive。
- **对标顶级路线图 Rank #5 已做(2026-07-03,Brinson-Fachler 归因 primitive)**:`services/portfolio/brinson.py`
  新增纯函数 benchmark-relative attribution,输入 caller-supplied portfolio/benchmark weights + asset returns + group map,
  输出 `ATTRIBUTED/BLOCKED`、逐组 allocation/selection/interaction/active return 与恒等式残差诊断。手算两组、benchmark=portfolio
  零主动收益、long-short 权重加性、缺 return/group、非有限值、权重和异常、确定性均测试钉死。
  诚实边界:研究解释 primitive only,不读写 DB,不接 `DryRunStore`/`run_registry`,不自动调仓,不接 dry-run/pre-trade/broker/lifecycle/readiness/console;
  多层 sector/style/macro 适配器仍是后续独立接线,当前只对 caller-supplied 单层 group map 做严谨恒等式归因。
- **对标顶级路线图 Rank #6 已做(2026-07-03,冲击模型校准 primitive)**:`services/research/realism/impact_calibration.py`
  新增纯函数 calibration set,把 RE-1 的文献默认/敏感性假设形式化为 aggressive/base/conservative 三情景
  (`temp_coef` 5/10/20bps,`perm_coef` 2.5/5/10bps,平方根 exponent=0.5),provenance=`UNCALIBRATED_PRINCIPLED`,
  并提供 `impact_band_from_calibration()` 可直接喂给 `capacity_band`。测试钉死情景排序、`AlmgrenChrissImpact`
  转换、capacity band 契约、非法参数 fail-closed 与治理边界。
  诚实边界:这不是 venue/tick 实测校准,不改 dry-run 成本/成交、不改 `CostModel`/组合构建/预交易门/券商/生命周期/readiness/console;
  真实成交校准仍需 tick/LOB/执行数据。
- **新前向路线图 Rank #7 已做(2026-07-03,压力门接真实历史情景重演)**:审计发现 `StressTestEngine` 用 4 个臆造固定倍数
  「编造」情景,而真危机重演 `realism/scenario.py` 就在隔壁闲置。现在调用方给逐期 timestamps → 真危机窗口(2021-05/LUNA/
  FTX/2023-03 银行)重演 + 内生最差回撤窗;无 timestamps → 旧倍数仅作诚实标注回退(`SYNTHETIC_MULTIPLIER_UNCALIBRATED`)。
  每份 `PortfolioStressReport` 带 provenance/scenarios_skipped/scenario_losses,`RiskApprovalGate.approve(timestamps=)` 透传,
  console risk-dashboard 透出 stress 块(契约测试钉死无 provenance 的压力数不上线)。诚实边界:console 路径暂无逐期时间戳,
  诚实显示回退标注;有时间戳的调用方(如干跑 book)同一字段拿到 `REAL_HISTORICAL_REPLAY`。
- **机构级路线图收官批(2026-07-03,PR #15 已合 main,续批在 `codex/firm-aggregation-and-followups`)**:
  PR #15(S1–S3 共 11 项)合并进 main 前**发现并修了一个 CI 隔离 bug**——3 个 lifecycle 测试依赖 gitignored
  `experiment_registry.db`(本地过、CI 红),加 `_require_real_registry` 守卫(有库跑、无库诚实 skip),两 CI 检查转绿后合并。
  续批(新分支)把路线图剩余项**逐个交付核心切片**、每项 tests-first + 门绿:**#25**(45ffb5e)`firm_aggregation`
  跨 book 真实暴露 + 重叠检测(primitive,9 测);**#26**(bb8afb8)system-health 真 `monitoring` 块(build_snapshot
  验 durable 审计链);**#27**(272fd71)`IncidentLedger` + 端到端 kill-switch 演练测试 + runbook;**#14**(e7ba04f)
  PIT regime 分类器(扩展窗分位 + 免估计,截断不变);**#15 采纳**(0e93684)物化桥 read==recompute bit-identical;
  **#12 BHY**(44a277d)BHY FDR 接进晋级守卫(比 BH 严);**#19 rebuild**(f831f4e)离线重算编排器 + digests 扩到
  ic_panel/capacity;**#16**(41151a0)ML 提议器 spec;**#28 pin**(7d2e035)`.python-version`=3.12 消解解释器漂移。
  **剩余(诚实分类)**:①**大重构宜专注做**——#24 console_api 5167 行拆路由组、#12 CatalogEvidence 封套 + effective_trials;
  ②**外部资源阻塞**——#17 股票历史成分/ALFRED、#22 volume 回填(coingecko 网络被墙);③**CI 迭代**——#28 lockfile/
  coverage/pip-audit;④**增量采纳**——#25 driver(dry_run.db 不存逐符号权重)、#15 驱动器读缓存。核心/基础全部已建已测。
- **机构级路线图 S3「溯源与持久」sprint 推进(2026-07-03,#12/#15/#19/#24 各一切片)**:
  **#12**(c7d0e5e)`ExperimentRegistry.verify_evaluation` 篡改检测(重算 content_hash 比对,改指标/改哈希/未知 id
  全 fail-closed,4 测)——补上「只算不验」缺的验证半边。剩 CatalogEvidence 封套 + BHY 接守卫 = 待做。**#15**(c5ae7a2)
  特征层持久化:`SqliteFeatureRegistryStore`(镜像 run_registry,含 leakage_passed+code_version 审计)+ `SqliteFeatureValueStore`
  (克隆 fundamental_store 双时点 + feature_version,PIT/截断不变/版本隔离/fail-closed,10 测)——特征层脱离「重启即失」。
  剩 rolling_feature 写通道 + 离线填充 + console 读 = 采纳待做。**#19**(9cc4c25)复现底座:`evidence/dry_run_books.json`
  指纹(5 book 承重数 + 残差界 + code_version)+ `tools/build_evidence_digests.py` 生成器 + `tests/reproducibility/` 门测试
  (缓存在场重算断言、缺席诚实 skip;drift 实测会红、rebuild 恢复绿)——repo 现能自证其引用的干跑结论。剩扩到其余快照 +
  完整 rebuild harness = 待做。**#24**(前端地板已在 a4e4350 IA 重构落地:30/30 冒烟 + 集成 + payload 守卫,47 测);
  console_api 5167 行拆路由组 = 大重构宜专注新会话做。**远端已推 origin/codex/impact-calibration。**
- **机构级路线图 S2「承重」Rank #22 强制机制做完(2026-07-03,数据质量从「算而不 gate」变机器强制)**:
  (e241c00)① `_ingest_records/ingest_bars_csv/ingest_provider_rows` 加 opt-in `strict=`:单标的数据集质量报告不过
  (重复/乱序/缺失/未来时间戳)→ raise `FatalIngestionError`(fail-closed,默认 False 零回归);yahoo fetcher 接 strict=True。
  ② `services/data/quality.py::check_volume_units` 跨币量纲一致性守卫(max/min > ratio → 非单一量纲,formalize
  crypto_daily.db bug 的 EOS $20k vs BTC $3.7Q ~17 数量级启发式);scenario_tail_research 改用它作单一真源。9 新测试。
  **诚实边界:volume 回填 + trend 容量重算需 coingecko 网络(本环境被墙)= 待网络;抓 bug 的强制机制已就位并测过。**
  **S2「承重」四项(#10/#11/#13/#22)全推进**(#22 强制机制 done、回填待网络)。远端已推:`origin/codex/impact-calibration`。
- **机构级路线图 S2「承重」Rank #11 + #13 做完(2026-07-03,执行现实主义 + 风控门承重)**:
  **#11**(cbf8609)冲击成本进干跑:`simulate_fills`/`run_multi_period_dry_run` 加可选 `impact`(Almgren-Chriss)+
  `adv_by_period`——commission 变参与率驱动的冲击成本(缺 ADV 逐笔回退平 bps,恒等式残差不破);cost_model/
  cost_provenance/TCA-lite 区间落 detail(透传 console)。carry 驱动用面板 close*volume 同源喂**冲击成本**(名义)
  + **参与率门** est_daily_volume(单位)+ 真实 max_participation_rate=0.10。**活跑:IMPACT_AC/6365 笔/TCA 计入 $8.8k
  区间[$4.4k,$17.6k] in-band/残差 4e-9,参与率门拒 95 笔(真容量约束,曾 0)**。诚实边界:crowd/combo(OI 无 spot
  volume)+ trend(volume 不可靠,#22)暂平 bps。**#13**(93fd065)研究风控门承重:新 `dry_run_guard.evaluate_dry_run_risk`
  只跑干跑接缝**有真输入的 4 引擎**(market/drawdown/concentration/stress,各 fail-closed;stress 用 timestamps →
  REAL_HISTORICAL_REPLAY),**其余 5 引擎 SKIPPED_NO_HONEST_INPUT——绝不喂编造数据伪造风控放行**(这正是平台不能做的);
  接进 crowd_contra 干跑落 detail(VT 序列 3/4 过,market-VaR 仅在 RiskConfig 默认阈值日频紧下未过,诚实标注 config_provenance)。
  晋级门 guard_to_paper 接线 + 5 引擎喂真输入 = 后续。**S2 剩 #22**(数据质量强制 + volume 量纲修复,含网络回填)。
- **机构级路线图 S2「承重」sprint Rank #10 做完(2026-07-03,组合原语从诊断变 load-bearing——全盘最大杠杆)**:
  三子项全落地、每项 tests-first + `validate.py` 绿 + 活驱动器端到端跑过。**10a**(a287e95)拥挤门加固:
  `crowding_gate` 加最大单名并行判据(外部评审 P0 稀释修复——集中且拥挤的单名独立触发,不被加权平均稀释)+
  可选 ADV/流动性维;接进 crowd_contra 干跑(逐周从真 global_ls cs-rank 评估、落 `DryRunRun.detail`、真 BLOCK
  跳调仓)——诊断非否决(crowd_contra 按构造持拥挤名 → 232 周全 WARN 记录不否决;集中判据在扩散 book 不触发,
  但集中 book 会 BLOCK);非-block 周字节等价(回归安全)。**10b**(55d7e17)资本配置器驱动 riskparity sleeve 权重
  (PIT 仅过去收益,BLOCKED/观测不足回退 rolling 逆波动;231/313 周用 allocator)+ `_clean_weights` 改有界迭代注水
  (显式收敛+fail-closed)。**10c**(0d14bb7/32d7755)优化器加 Rockafellar-Uryasev CVaR 尾部惩罚选项(尾部次梯度,
  非光滑路径取最优可行迭代;光滑路径字节等价)+ 经 allocator `cvar_penalty` 接进 riskparity(尾部感知 sleeve 定权,
  carry 0.47/crowd 0.53)。**52 新测试;三条既有 book(carry/crowd/trend/combo)未动,riskparity 按设计变(allocator 驱动)。**
  历史当时边界为拥挤阈值未校准；2026-07-13 已补 crowd_contra 签署 z-gate 的机器重放证据，
  但通用默认与 carry/combo 宇宙仍 open。**下一步 S2**:#11 冲击成本进干跑 → #13 风控门承重 → #22 质量强制。
- **机构级路线图 S1「守卫与留痕」sprint 全做完(2026-07-03,活路线 `docs/2026-07-03-institutional-completion-plan.md`)**:
  4 项 P1/S 全落地、每项 tests-first + `validate.py` 绿。**Rank #8**(6ca7d92)PIT 裸读机器守卫:
  `tests/governance/test_pit_read_boundary.py` AST 钉死 services/ 白名单外 0 裸 `fetch_bars` + tools/ 计数棘轮
  (35,只减不增),正负控制 + 实地探针证明守卫会红。**Rank #20**(0630f0c)治理决策留痕:新
  `services/monitoring/audit_store.py::DurableAuditLog`(append-only sqlite 哈希链,存 entry_hash 读时重算抓字段篡改、
  prev 链抓重排、seal/verify_against 抓截断/替换/擦除,镜像 run_registry 模式、无墙钟);`KillSwitch.arm/reset/
  reset_with_grant` 与 `evaluate_deployment` 加**可选** `audit_sink=`(默认 None 零回归,失败安全排序:arm 先停机后审计、
  reset 先审计后恢复、部署门审计在 try 内失败即关)。**Rank #18**(4e41851)runner 调用方身份:POST /run 强制
  `X-Operator`(缺→400),operator 落 run 行(走 payload_json 免迁移、不进 run_id)+ 审计链 `research.run.triggered`;
  **活系统端到端验证过**(无 header 400 / 有 header 200 回显 / 非白名单 kind 仍 400 / 审计链记实);前端 `triggerRun`
  带 localStorage operator 标签 + ResearchRuns 页输入框。诚实钉死:operator=自述式、未鉴权(非 RBAC)。**Rank #21**
  (5e2a64b)fetcher 治理收编:`yahoo_equity_fetch` 改走规范 `ingest_provider_rows` 接缝(只 stdlib+services、进
  `_FETCHERS`→全 10 网络 fetcher 入 AST 边界)+ 存 **RAW 未调价**(去掉 adjclose 回溯比例烘价的非-PIT-vintage 前视,
  完整分红调整留读取时 Rank #23),provenance=`YAHOO_V8_QUOTE_RAW`。**系统已启动并在跑**:console API :8400(真数据)、
  runner :8420(已重启加载 #18)、前端 :3100(连真后端、新 7 组导航)。**S1 剩清理批**:CLAUDE.md 计数刷新、
  跟踪 AGENTS.md。**下一 sprint S2「承重」**:#10 组合原语接线 → #11 冲击成本进干跑 → #13 风控门承重 → #22 质量强制。
- **前端信息架构重构已做(2026-07-03,用户指令「按顶级 AI 量化系统彻底重构前端」)**:42 页 → 30 页,
  7 个工作流导航组(总览→数据→因子研究→验证→组合与风险→执行与跟踪→治理与系统);砍 12 个纯合成蓝图/
  重复/演示页(旧路由语义就近重定向,蓝图页→SystemCompleteness 诚实完整度页——删页不删诚实);路由/导航/
  冒烟测试收敛到单一页面注册表 `src/app/routes.tsx`(新页一行自动进导航+测试);测试地板 14/42 → **30/30
  全冒烟 + 注册表一致性**(44 fe tests);后端端点零改动(契约锁定)。typecheck/build/test/validate 全绿。
  Rank #24 前端半边(全页冒烟)提前落地;console_api 后端拆分 + provenance 徽标断言仍待做。
- **新前向路线图 Rank #9 已做(2026-07-03,部署门强制 version content-hash 钉死)**:版本契约(ConfigVersion/StrategyVersion/
  ModelVersion)一直存在但 `evaluate_deployment` 从不校验——「version-pin gate」原是 overclaim。现在 PAPER+ 必须声明部署内容
  且逐版本与批准钉死表(id→content_hash)比对,fail-closed(`VERSION_PIN_REQUIRED/_NOT_PINNED/_MISMATCH/HASH_MISSING/
  STRATEGY_VERSION_NOT_APPROVED`);多方 grant 强路径不绕过(批准=谁可部署,钉死=部署什么);DEV/TEST 不受限;LIVE 仍被
  readiness pin + LIVE flags 结构性挡死。
- **统一执行 book 做了(2026-07-01,`tools/dry_run_combo_multi.py`,第 4 条策略)**:carry 面板加 OI global_ls,每周
  0.5·carry_legs+0.5·crowd_legs 过干跑流水线。RAW/VT 两条曲线:carry 0.45/77%→0.65/22%、crowd 1.21/12%→1.38/11%、
  **combo 0.58/56%→1.20/15%**。诚实教训:naive 权重 blend 是 carry-dominated(58币 vs 14大币半权)→ combo VT 1.20 improved
  carry 但仍 < crowd alone 1.38;equal-risk 收益流组合(1.95/8%)≠ naive blend——执行里兑现分散需 risk-balance sleeve。
- **短周期(4h/小时线、小样本)= null。** 价格/GP/量能因子都无稳健 OOS edge;加宽截面把样本内
  Deflated 夏普抬到 0.586,但**无窥视 OOS 证伪**(选择性过拟合,样本外 −2.01);量能因子净夏普全负。
- **日线周期 = 第一个正信号(2026-06-26,`tools/crypto_daily_research.py`,commit 1564bb6)。** 同一
  CoinGecko demo,`market_chart days=365`→日线(~364 根/币),24 币×364 期,日级窗口进化搜索。结果与
  4h **截然不同**:最优因子全是同一族 `dist_from_high`(距 N 日高点=趋势代理,7/14/30),IC≈0.065、
  **扣成本后** net Sharpe≈2.4、Deflated 0.95;**无窥视选择 OOS 这次没被证伪**(mean OOS 年化 +3.95 vs
  IS +2.90,3/4 fold 为正)。
- **但要克制(不是"找到 alpha"):** `sharpe()` 按 √252 年化(3.95≈日 Sharpe 0.25),OOS 池是**毛收益**;
  逐 fold 极噪(+7.43/−0.80/+6.26/+2.93,被两个暴涨 fold 拉高、一个为负);**整段样本=2024-25 单边牛市**,
  而"距高点"正是牛市机械获利、反转即崩的因子,**未经熊市/震荡验证**;可信的数字是 IC≈0.065(温和但真实)。
- 容器化始终守住:auto_approved=0、全 REQUIRES_HUMAN_REVIEW——系统**没有**自动放行它(正确)。
- **对抗验证(`tools/crypto_daily_verify.py`,离线)两项都过:**(1)成本压力:OOS 净 Sharpe 随成本近线性
  衰减(3→3.95、10→3.18、20→2.37、30→1.06、50→−0.44),**盈亏平衡≈35–40bps 单边**;中盘币现实 ~10–20bps
  → OOS 仍 +2.4–3.2,**不是一扣成本就死**(区别于 4h 量能)。(2)留一币:基准 2.448,去掉任一币落在 [2.22,2.75]
  → spread 0.53 = **宽基**,非一两个币带飞。
- **Regime 拆解(`tools/crypto_daily_regime.py`,离线)比担心的好:**"只在涨市赚?"的疑虑**不成立**。dist_from_high_14
  原始每日 Sharpe 在两种波动率(calm +0.219 / storm +0.215,中波约 0)和两种涨跌市(跌/横盘 +0.191@n=208 /
  涨市 +0.106@n=128,跌/横盘反而更强)里**都为正**——因为它是**美元中性的相对强度**因子,天然与大盘方向无关。
  (accel_14 是弱的:涨市 −0.014;momentum_7 跌 +0.146/涨 +0.045。)注意:"牛市内的跌/横盘日"≠真正多月熊市,
  趋势反转处的"动量崩溃"(Daniel-Moskowitz)正是一年牛样本装不下的——仍需出 regime 数据才能彻底关闭。
- **完美化一轮(全 42 币 + 合成,`tools/crypto_daily_composite.py`):** 两条诚实教训——(1)**更多数据=更诚实
  (更低)的数**:24→42 币把无窥视 OOS 从 3.95 降到 ~1.45–1.73(24 币那批是最干净大盘,偏乐观);但单因子
  `dist_from_high_14` 跟踪 OOS=2.53、**4/4 段全正**(挖到的最稳的东西)。(2)**简单胜过花哨**:4 因子 z-score 合成
  在每个维度都不如单个 dfh_14(full 1.60<2.05、OOS 1.89<2.53、75%<100%);周度调仓更差(0.56<1.60,信号次日就衰减)。
  成本盈亏平衡(42 币):合成 ~22bps;现实 ~10bps 仍明显为正。regime:涨/跌、calm/storm 都正(全宽更温和)。
- **出 regime 验证(2026-06-27,`tools/crypto_history_research.py`,CryptoCompare 5.5 年、23 币×2000 日线、
  2021–2026 含 2022 熊市)—— 日线趋势 lead 已被证伪、死亡:**
  - 全样本容器化裁决:dist_from_high_14 net Sharpe **−0.55**、IC **0.001**(≈0);8 个价格因子里最好的
    (dist_from_high_30)也只有 **+0.034**(≈0),IC 普遍近零或负。
  - **逐年净夏普正负乱翻、毫无稳定性**:2021 **−1.82**、2022 **+1.0**、2023 **−1.61**、2024 −0.05、2025 +0.79。
    不是"牛市赚熊市亏",就是**不稳定**;2024–25 那段是**运气窗口**。
  - 全样本成本曲线从负起步(−0.55@3bps),无盈亏平衡;regime 按涨跌市**两边都负**(与一年样本的正好相反)。
    长样本无窥视 OOS 仅 +0.42(选了 momentum_14),噪声级,非 edge。
  - 容器化全程 auto_approved=0:系统**从未放行**它。**这是平台在正常工作** —— 更长更全的数据 + 诚实机制,
    戳穿了一个"一年牛市幻觉"。量化第一课就这么活生生上了一遍。
- **截面 alpha 引擎 + Alpha101 实测(2026-06-27,`alpha_expression.py` + `tools/crypto_alpha_research.py`)——
  更丰富的因子,同样没信号:** 建好了因子语法扩展(时序×截面算子,解锁 Alpha101 那类真因子),在同一份
  5.5 年/23 币面板上跑 15 个精选截面 alpha:**仍无跨 regime edge**。最好 accel_cs 全样本 SR 0.60 但被 2021 一年
  带飞(负 3/6 年);所有 |IC|<0.03;**幸存者 0**。**这把"瓶颈是数据不是因子生成"从论断变成了实证**:可表达空间
  放大几个量级、照样挖不到东西,因为价+量在大盘币日线上被高效定价了——alpha 不在这份数据里。引擎是永久基础设施
  (将来有更好数据时即用)。
- 定性(钉死):**"挖更多花哨因子"是错的杠杆,已实证。** 当前没有任何经得起跨 regime 的加密因子(简单的、丰富的
  都≈0)。真正的杠杆 = **数据护城河(口子)**:把广度铺到**小盘币**(低效定价处)、另类数据、其它资产类别、或更高频/
  微结构。下一步要么用户拍板开数据口子,要么把 `dist_from_high_14` 这类候选挂纸面跟踪。详见记忆 `crypto-research-findings`。

## 5. "口子"(2026-06-27 用户已授权放开 —— 目标=可盈利机构级系统,见记忆 `goal-profit-open-kouzi`)
用户原话:「放开口子,不再局限于研究——研究是底层,赚钱是目的」。以下口子**我可主动用**(推进 Phase 时):
1. **数据**(最大,~80% 边际):更长历史/逐根成交量/资金费/链上/另类/**行业·板块分类映射**(用于中性化)。
   `tools/` 只读拉取,**适配器接口已就绪**,填 SourceSpec 即接入。
2. **LLM 点子生成器**:**门外生成 + 门内当裁判**(LLM 提议假设/DSL,确定性引擎 + DSR/OOS/容器化裁决);
   不进 services/ 确定性核心、不破容器化(LLM=非人类 actor,永不 APPROVED)。参考 arXiv 2505.11122。
3. **numpy 提速**:可在评估层引入向量化,但须隔离评估、量化对确定性/可复现的影响。
4. ⛔ **硬红线(我绝不自己跨,必须用户多步人工审批)**:真实实盘下单 / 接券商·CTP·FIX SDK /
   翻 LIVE_TRADING_ENABLED·REAL_BROKER_ENABLED·ALLOW_ORDER_SUBMISSION / 提升 readiness(L2→L3+)。
   我把系统一路造到这条线前(research→paper→shadow→容量→成本→生命周期就绪),go-live 留作用户人工动作。

## 6. 定位(对"做到最好"的诚实回答)
**在"纯-stdlib、确定性、安全可复现的研究系统"这一类别里,过程已接近顶级**:发现(GP+MCTS)→去冗余→
不自欺验证(DSR/PBO/Purged-CV/无窥视 OOS)→全生命周期,且"不自欺"比所借 SOTA 论文更严。
**没有静态"最优解"**(市场非平稳+反身性+数据有硬上限);"最好"=最好的**过程/适应机器 + 数据护城河**。
要"整体最好"必须开第 5 节的口子。

## 7. 提交锚点(resume 时 `git log --oneline -1` 对一下)
**★★★ 系统排查审计 + 完善路线图(2026-06-29,用户指令 "先排查整个系统→再完善功能→最后做前端")**:
9 智能体全量取证审计(`docs/engineering/specs/2026-06-29-system-audit-and-completion-roadmap.md`)。
**核心结论:后端比文档真实得多,缺口是"接线"不是"造引擎"**(~1014 测试过,`services/{alpha,data,
portfolio,intelligence,validation,...}` 全真且测过;CLAUDE.md "~10% built" 已陈旧并纠正)。36 界面真实度:
REAL 7 / PARTIAL 6 / MOCK 17 / NONE 6 / BROKEN 1。`l2_ok=true`(全绿,仅 1 个 P2 已修)。
已批准:仅加密数据、真缺子系统页诚实标注。
- **Phase 0 排查收口**(`839c1bb`):删 3 死文件+空目录;App.tsx 36 页 React.lazy 拆 546KB 首屏→
  28KB+vendor166KB(警告消失);LifecycleMonitor/SystemHealth 补诚实徽标;修治理测试 P2;纠正 CLAUDE.md。
- **Phase 1B 真序列升真**(`d2aa6fb`+`86d2c34`):审计的"一行换源"前提不成立(快照只存真指标无真序列),
  故 ① session 暴露 `factor_returns`(真逐期净回报)② `tools/crypto_research_offline.py` 离线再生器
  (不联网,从缓存 PIT 库 crypto.db 重跑确定性 session,18 币×120 期,写 `factor_series`)③ console_api
  `_load_real_factors/_real_factor/_input_provenance`,`_forward_returns`+`_computed_alpha` 有真序列时跑真
  (真 ic/turnover/state+真权益/夏普/回撤,IR≈IC·√breadth),无快照 fail-closed 回退合成。6 端点
  provenance→`_input_provenance()`(真=REAL_CRYPTO_SERIES);契约接受 real-or-synthetic;前端 6 页 realData 徽标。
  **实测**:alpha-factory REAL_CRYPTO_SERIES+真因子名;risk 真 9 引擎门跑真回报→REJECTED_CORRELATION_RISK(诚实)。
  门禁双路全绿(真路径本地+合成路径 CI),containment auto_approved=0。HEAD=`86d2c34`。
- **Phase 2 接真引擎升真 NONE 页**(`2db98ce`/`0774fe0`/`b3e8767`,复用 Phase 1B 真因子序列 + services/{research,alpha} 真引擎):
  ① **种群优化器**(`/population-optimizer`)factor_set.select_synergistic_set 协同前向选集去冗余 + inverse-vol 权重 +
  真边际 IR + 真边际回撤(组合 maxDD 增量,可负=分散) + diversity(1−均|corr|);② **Alpha 相关图**(`/alpha-graph`)
  services/alpha/alpha_graph.correlation_graph 真相关网络(节点/边/连通分量冗余簇 + 后端确定性布局);
  ③ **Alpha 资产库**(`/alpha-zoo`)研究快照全部真因子目录(真 state/DSR/净夏普);④ **DSL 浏览器**(`/dsl-explorer`,
  `ee63604`)真 dsl_checker/complexity/expression_leakage 计算裁决(非硬编码):合法表达式机器证明 PIT 安全、
  ts_lead 读未来被真拒绝(确定性面板仅作泄漏扰动基底)。每个新端点 fail-closed 回退合成(CI 无快照安全)、
  契约 real-or-synthetic、前端 useConsoleSnapshot + realData 徽标。门禁全程双路绿。
  **真实度更新**:Phase 1B+2 后 6 PARTIAL + 4 NONE 已升 REAL(Risk/Validation/ComputationLab/Portfolio/
  PaperTrading/AlphaFactory + PopulationOptimizer/AlphaGraph/AlphaZoo/DslExplorer)。
- **Phase 3 起步**(`0033f1a`):**事件流**(`/events`)从真实 run_registry 派生事件(每个研究运行=一条只读审计事件
  research.run.{status},真标题/裁决/头条;前端已通用渲染,后端单文件改);registry 空→合成回退,
  provenance 条件化 RESEARCH_RUN_REGISTRY/SYNTHETIC。本会话共升真 **11 页**(6 PARTIAL+4 NONE+Events)。HEAD=`0033f1a`。
  **Phase 3 余下页(Experiments/EvidenceLedger/Agents/LifecycleMonitor/Overview)的真持久层多为空**
  (experiment_registry/audit_log/agent 运行记录未被离线再生器填充)——需先"填数据"(如让离线再生器写
  experiment_registry 记录)再接端点,不是纯接线。
**★★★ 自主开发批次(2026-06-29,用户出门前审批 A→B→C→D→E,自主执行)**:
- **A 合规文档**(`f3eb921`):`docs/COMPLIANCE_AND_PROCESS.md`(机构 SDLC/职责分离 + QUANTOS 对照 + 单人落地 + PR DoD 清单)。
- **B 诚实标注**(`6594510`):ReviewerWorkflow/AccessControl/ReleaseCommittee/IncidentResponse 补"蓝图/未实现(需 Phase 14 身份层)"徽标。
- **C Phase 3 数据填充 + 升真**:离线再生器写 experiment_registry(60 真不可变评估,`b30643b`);**Overview**(真 n_evaluated/active_runs,`b30643b`)、**Experiments**(读真 experiment_registry,真 IC/夏普/reproducibility_hash,`0252223`)、**EvidenceLedger**(真重算 hash 链 + 第一条接真实验,`94094ab`)、**Events**(早先 `0033f1a`,真 run_registry 事件)全升真。
- **D 安全原语上台**(`ba6c002`):`GET /safety-primitives`(REAL_SAFETY_PRIMITIVES)只读真 KillSwitch/23 盘前检查/SafetyConfig;Risk 页安全 widget。只读不下单,治理扫描器仍 PASS。
- **E 前端指挥中心**(`caabfe9`):`CommandCenter` 单屏座舱,5 磁贴可拖拽重排 + 点击缩放 + localStorage 布局持久化,全真数据只读;根路径默认落地。实测 live 通(5 拖拽磁贴、缩放改 span、无控制台报错)。HEAD=`caabfe9`。
**本会话累计升真 ~15 页**(6 PARTIAL + 4 NONE + Events/Overview/Experiments/EvidenceLedger + 安全原语 + 指挥中心)。门禁全程双路绿。
**关键资产**:`tools/crypto_research_offline.py` 离线再生器(不联网,从 crypto.db 重跑 session 写 factor_series,
18 币×120 期);`crypto_research_snapshot.json` 是 gitignored 真数据源(CI 缺→合成回退)。重生真数据:
`python -m tools.crypto_research_offline`(后台跑~2-3min)。
**后续**(Phase 2 高价值项已完成,剩低优先 BacktestResults 改 run_registry 动态)→
**Phase 3**(Experiments 接 experiment_registry / EvidenceLedger+Events 接 monitoring/audit_log 真 hash 链 /
Agents+LifecycleMonitor 接 services/{intelligence,alpha} 真运行态——这批缺的是"真持久层"非引擎,引擎都在)→
**Phase 4**(kill-switch/pre-trade 门上台 + 真缺子系统页 RBAC/模型注册表诚实标注)→
**Phase 5**(前端 Grafana 自由网格指挥中心 react-grid-layout,默认磁贴指向已升真页 + 导航重组)。

**★★★ Phase 3 续批升真(2026-06-29,用户"继续开发";5 并行规划员产出接线规格→串行 TDD 实现,每页门绿+提交)**:
- **F MonitoringDrift 部分升真**(`fff1f1f`):ALPHA_DECAY 基线/裁决跑真 `degradation_monitor`,provenance 条件化
  `PARTIAL_ALPHA_DECAY_REAL`;实时漂移侧 L2 无源仍合成,加琥珀色诚实横幅说明。不伪造 live 漂移。
- **G LifecycleMonitor 升真**(`eeb531a`):新 `GET /lifecycle-monitor` 读真 experiment_registry(60 alpha 真
  state/backtest_ic/sharpe/pvalue/deflated_sharpe/verdict);**诚实** live_ic=null、recommendation=NO_LIVE_DATA
  (L2 无实盘/纸面 IC,不伪造 DEGRADED)、days_in_state=null、真态均 TESTING(auto_approved=0)。
- **H DataCatalog 数据集升真**(`ae78130`):`_enumerate_real_datasets()` 枚举 `data/cache/crypto_daily.db`(42 标的)
  为真数据集(真 content_hash/row_count/coverage/相对新鲜度/PIT 安全,模块缓存);features/lineage 仍合成并诚实标注;
  provenance `REAL_PIT_DATASETS`。MonitoringDrift 复用 catalog→DATA_FRESHNESS 限 top-8 防刷屏。
- **I BacktestResults 动态化**(`5b33883`):新聚合 `GET /backtest-results` 按 kind+recency 取最新 done run
  (carry/combo/oi_research/survivorship+factory),真 metrics/series/verdict 绑 tile/柱;无真源派生量(carry 逐年、
  R²0.5%、2.09 头条)保留 OBSERVED;crowd 逐年柱改用真幸存者 per_year(诚实修正)。
- **诚实徽标修复**(`c0b9c38`):升真页拉真数据但 ProvenanceRibbon 默认 realData=false → 误标"合成演示"。
  ConsolePayloadBase 加可选 `data_provenance`;LifecycleMonitor/BacktestResults/DataCatalog 接
  `realData={usingBackend && data_provenance===<真标记>}`(Overview/Experiments/Risk 早已有)。**浏览器 HMR 实测**:
  三页均显示绿"Real research results",无误标,无控制台错误;回测战果页截图确认实时取自 run_registry。
- **Agents 诚实留 mock**(feasible_now=FALSE):无真 agent 运行持久层(`ResearchAgentLoop` 无状态、experiment_registry
  无 agent 列、bandit_state 盘空、budget_spent_cny 无源)→ 需先建 `agent_run_registry`(Phase 11)。**勿硬造**。
- 批次后门禁全绿:**1020 py 测试** + 治理扫描 PASS;前端 24 fe 测试 + typecheck;:8400 console_api 已重启服务新端点。
  提交均在本地 `master`(无 upstream push——本仓既有模式)。HEAD=`c0b9c38`。


HEAD 在研究操作台 Task 1-7(`run_registry`/`research_runner`/前端研究中心)附近;`main==master`(rtk 下 `git branch -f main master`);后端 `validate.py` **全绿**(含新治理测试 test_research_runner_boundary);前端 `npm test` **23 passed**(含 CompareRuns smoke)+ typecheck + build 绿;远端 kerstker/QUANTOS 已同步(PR #14 OPEN)。
**★★★ 研究操作台(Research Ops Console,2026-06-29,用户指令:前端要能操作系统、不在 IDE 跑、动态结果区不一回测一页、顶级机构标准)= 前端从「看板」升级为「操作台」**。brainstorm→spec(`docs/engineering/specs/2026-06-29-research-ops-console-design.md`)→plan(`docs/engineering/plans/2026-06-29-research-ops-console.md`)→7 任务 TDD 全绿。架构:① `services/research/run_registry/`(sqlite 单一事实源,线程安全;`index_existing` 把现有 `crypto_*.json` 结果灌入→历史第一天全可见);② 查看只读=console_api GET `/research-runs[?kind=]`+`/research-run?run_id=`(契约守,provenance RESEARCH_RUN_REGISTRY),前端「研究中心 /research/runs」列表 + **一个通用数据驱动查看器** RunDetail(metrics→KPI/series→图/verdict/caveats,任何 kind 零新代码);③ 操作=独立 `services/research/research_runner/`(端口 8420,**唯一可变面**)`POST /run{kind,params}` 校验白名单(7 个只研究 kind)→异步队列→写 registry,前端按钮触发 + 3s 轮询 queued→running→done。**安全机器强制**:`tests/governance/test_research_runner_boundary` AST 扫描——无券商 SDK、不 import services.trading、无下单/转账调用,白名单入口全 services/tools 研究函数、⊆ registry ALLOWED_KINDS;console_api 仍 GET-only;auto_approved 恒 0、到不了 live/flag/APPROVED;墙钟只在 runner HTTP 边界取。**端到端实测**:前端点「双因子组合」→ 真算 combo(SR 1.503)→ registry done → 列表 7→9。启动见 CLAUDE.md Commands(console_api:8400 + research_runner:8420 + `research-console-live`)。记忆 [[research-ops-console]]。
**★★★ 操作台四能力补完(2026-06-29,用户指令 "都做,最终都要做到的";顺序 观测→参数化→对比→历史,每个 TDD+门绿+实测)**:
① **可观测**(`7621e19`)实时日志流(`_LogWriter` 重定向 run stdout 逐行进 `registry.append_log`,RunDetail 2.5s 轮询 + 日志面板)+ 软取消(标 cancelled 丢结果,不强杀线程,VALID_STATUS 加 cancelled)+ git-sha(纯读 `.git/HEAD` 无子进程)。
② **参数化**(`d3cc516`)`factory_orthogonal` schema `{generations:int,population:int}`;`_tool_then_json` 改 `main(**params)`;前端 RUNNABLE_KINDS 带 `RunParamSpec[]` 渲染 typed number 输入 → `triggerRun(kind,params)`;`validate_params` typed/fail-closed 复校。
③ **对比**(`c86476e`)`/research/compare?ids=a,b,c`(可分享 URL)`fetchResearchRunsCompare` 并行拉 N 个(各自 snapshot 校验)→ 指标矩阵(指标键并集 × 每运行一列,缺失补—)+ per-run 序列图;不做"最优"高亮(指标方向不同,诚实);列表行加勾选 + "对比所选(N)"(≥2 启用)。
④ **历史留存**(`830a960`)`_run_summary` 增 `params`+`code_version`;列表加「参数」「git」两列(短 sha,hover 全量);历史索引运行无 sha 诚实显示 —,runner 新跑显真实 sha。登记表=「参数+结果+时间+git」可检索历史。
**四能力实测**:runner 触发新 combo_book → `code_version=c86476e`(=HEAD)、SR 1.503 → 列表 git 列显示 c86476e + 参数;对比矩阵 combo SR 1.503 vs carry net@5bps 2.030 并排;门绿(1014 py + 23 fe),无控制台报错。HEAD=`830a960`。
**前序锚点**:`1ccc0c3` 第二条 edge crowd_contra;`4b10905` 日内挖尽;`a0a25d3` 回测战果页。
**★★★ 第二条 edge 候选——逆向散户持仓 crowd_contra(2026-06-28,用户指令 "接第二种免费持仓数据做正交第二条 edge")**:
- **数据墙绕过**:实时 REST 持仓接口只给 30 天,但 **Binance Data Vision metrics 归档**有多年免费每日 OI+多空比 ZIP。抓取器 `tools/binance_oi_metrics_fetch.py`(PIT 安全/并发13×提速/过治理边界,`4fb9d84`+`b8fa063`);研究 `crypto_oi_research.py` + 8 项证伪 `crypto_oi_verify.py`(`1ccc0c3`)。14 流动主流币 × ~2199 日。
- **发现**:`crowd_contra = cs_rank(neg(global_ls))`("散户太多头时做空")——价 SR **1.18**、与 carry **R² 仅 0.53%(几近完全正交)**、增量 IR **+1.13**、逐年 22-26 全正、**晚一天进场仍 1.03**(非时序假象)、净收 +0.52bps/天资金费、成本/子集/embargo OOS 全过。项目至今最强信号。
- **4 路对抗验证 Workflow(PIT/统计/经济/成本,各跑真代码,ultracode)= 无 kill-shot,但裁决 WEAK_LEAD 非确认**:统计维度在「多重检验+去2022」叠加后未过 p<0.01;利润尾部集中;**幸存者偏差结构性不可证伪**(仅 14 个活到今天的币,空头腿从未在死币上测);近端净值软化;部分崩盘凸性溢价。**= 第一个真·carry-正交的第二条 edge 候选,可挂纸面跟踪,非已确认。** 容器化不变(auto_approved=0)。全文 `docs/research/2026-06-28-crowd-contra-second-edge.md`、记忆 [[crypto-research-findings]]。
- **同日跟进**:① **双因子组合**(`crypto_combo_book.py`,真 `optimize_population`):carry×crowd_contra×top_contra → **组合 econ SR 1.50、maxDD 0.12**、净@10bps 1.02、比最佳单因子 +0.23(`<combo commit>`)。② **幸存者完整重测**(`crypto_oi_survivorship.py`,验证报告的 #1 风险,Data Vision 不过滤死币→可测):抓 17 个死币(LUNA/FTT/…),`oi_value/oi` 价格代理,**survivors-only 1.228 → 含死币 0.933 raw,幸存者折扣 ~0.30**;逐年仍全正、winsorize 非支柱、8/17 死币正确做空但做多了 LUNA/FTT。**裁决 WEAKENS 但 SURVIVES**——非幻象(~0.9 正 Sharpe),但 1.2 头条带 ~0.3 水分,deployable ~0.9,是截面持仓边非崩盘对冲。OI 抓取器加 `find_end_ms`/bounded(收敛死币范围)。
- **剩余决定性测试**:前向纸面跟踪(近端软化=噪声 or 衰减);对组合也做幸存者重测。
**★★★ 自主多维正交发现——机器自己挖出新边(2026-06-28,`tools/crypto_factory_orthogonal.py`,用户愿景:机器找多维多向甚至未知 edge)**:
- cs_genome 本就字段无关 → 传 8 维 fields(价/量/资金费/oi/oi_value/top_ls/global_ls/taker_ls)+ 多维 panel,并把搜索奖励改成**对已知边{carry,crowd_contra}正交的增量 IR**(逼机器找新边而非重收敛 carry)。6 代 ×132 个跨维度因子,跨维交互天然可生成。
- **机器自主发现 `cs_rank(ts_argmin(global_ls,30))`**(散户持仓 30 天最低点距今多久——用 global_ls 的全新结构,人没写)**与 carry/crowd 完全正交(R²0.001)**,过快速 gauntlet:SR 0.86、换手 0.16/天、**滞后 0.86/0.78/0.73(扛1天)**、净@10bps 0.56、逐年 22-26 全正。= 第三个正交 lead。
- 同时吐出量能变化诱饵(in-sample 好看),诚实机器精准毙之(滞后 0.70→0.21→−0.12、净@10bps −0.10)。**机器找候选 + gauntlet 分真假,如设计。**
- argmin_global_ls30 是 LEAD 非确认(主折扣=132 次搜索选择偏差,待完整对抗验证)。容器化不变。
- **已硬化进引擎 + 放大(`b99f723`)**:`run_alpha_factory(basis_returns=...)` 成为永久 opt-in 能力(给定已知边对齐流→奖励切到对 basis 的正交增量 IR,默认行为不变,已测、门绿 ~982);加 `alpha_expression.deserialize`(serialize 精确逆,round-trip 测)。`crypto_factory_orthogonal.py` 重构为引擎薄编排器。放大跑(8代×64,192 因子):机器自主长出**量×价跨维度交互** `cs_rank(add(cs_scale(volume),ts_rank(close,14)))`(SR 1.38、R²|known 0.011 完全正交、过 lag+cost)+ 新持仓结构 `ts_delay(ts_rank(global_ls,14),30)`;无窥视 OOS mean 1.16,机器发现被选 4/6 折。诚实:192 次选择膨胀,顶部 lead 比 crowd_contra 更脆,是 lead 非确认。**用户愿景(机器自主多维/跨维找正交边 + gauntlet 分真假)已成引擎能力并放大验证。**
- **+basis 维度 + loop-until-dry(`<basis/loop commit>`)**:加第 9 维 basis=perp标记价(oi_value/oi)/现货−1(与资金费 carry 正交 R²0.021)。loop 每轮把过完整 gauntlet(正交+PIT+滞后+成本)的新发现并入 basis 直到连续 2 轮枯竭。实测:机器挖到 **1 个** 新正交边 `cs_rank(sign(cs_rank(top_ls)))`(top-trader 持仓 SR 0.57)后枯竭。**诚实结论:免费加密日频的稳健正交 alpha 空间薄=carry+散户持仓+1 个弱 top-trader 持仓边到顶**;放大跑那个量×价 SR 1.38 没过更严 loop gauntlet(更脆=选择产物)。量×价 4 路对抗验证 = **REFUTED**(全 kill-shot,真实数据复现):滞后 0→1 SR 1.41→0.66(成本前崩)、lag1 净@10bps −0.001、截面 IC 每 horizon/6-7 年皆负(=做多偏置趋势 tilt 非截面边);对照 crowd_contra(lag1 净 0.498)/carry(0.516)都扛住→gauntlet 校准对。综合:同 bar 时序假象,加固"薄空间"结论。全文 `docs/research/2026-06-28-autonomous-orthogonal-discovery.md`。**平台如设计运转:机器自主找跨维度候选 + 诚实机器有牙齿地毙假阳性(哪怕纸面 1.38)。** 当前真边(深压测后修正):**只剩 2 条稳健边——carry(~2.0)+ crowd_contra(幸存者后 ~0.9 WEAK_LEAD)**。top_ls 经深压测**降级**(survivors-only 0.69→含死币 0.40、逐年熊年为负 2021 −1.23/2022 −0.79、t 仅 2.01=脆弱近年 lead 非稳健边);volume×price 已 REFUTED;intraday 0 幸存。要更多真边瓶颈=数据(微结构/链上/跨资产),非搜索。
- **richer-data 试验 #1:日内持仓动态 = 也被挖尽(`crypto_factory_intraday.py`)**:metrics 本是 5 分钟,提取 6 个日内派生日频特征(oi_range/oi_trend/gls_trend/gls_range/taker_mean/oi_surge),15 维自主搜索 230 因子 → **0 幸存者**(日内因子纸面 SR 0.86–0.97 但 lag1 全 ≈0/负、net@10bps 深负=同 bar 假象)。**诚实:同种持仓数据 finer resolution 不是杠杆,eod crowd_contra 已吃干;真增量得是另一种数据(盘口微结构/链上/跨资产)。** 抓取器加 `--intraday`(纯 stdlib,治理过)。全文见 `docs/research/2026-06-28-autonomous-orthogonal-discovery.md`。
**★★★ 前端补全 + 监控接真数据(2026-06-28,用户选定方向 "前端补全 + 监控接真数据")**:
- **前端补全 5 页**(`dddeab9`+`1687e20`,后端能力本就就绪):Alpha 资产库(可筛 generator/状态/簇/DSR)、种群优化器(选集/权重条/边际 IR·DD/剔除原因)、信号→组合(美元中性目标 book + 容量 + 被拒信号,**只算 book 绝不下单**)、DSL Explorer(表达式静态检查/复杂度/PIT 泄漏机器证明,含一条读未来被拒例)、Alpha 相关图(SVG nodes/edges/簇/近重复)。均 mock-first/只读,注册 nav+route,smoke 测覆盖(19 测试)。
- **监控漂移接真引擎**(`e34275e`+`41cb3f1`):console_api `_monitoring_drift_payload` 的 ALPHA_DECAY 一类改由真 `services.alpha.degradation_monitor.monitor_alpha_health` 实算(多指标 IC/return/slippage/turnover/drawdown→OK/DEGRADED/PAUSED/RETIRED),映射成面板 status,remediation 透传引擎裁决原文(`[degradation_monitor:<rec>] …`)。**只 RECOMMEND 不驱动状态,live 授权恒 False**(containment 不变)。`drift_score` 接引擎严重度并钳进与 status 一致的阈值带(OK<warn≤WATCH<breach≤BREACH)→ 条/色/阈值/status chip 四者自洽;新增自洽性测试。
- **对抗复核(2 子代理)+ 修复**(`84ea717`+`66740a1`):监控接线无 P0/P1(带钳设计代数证明无法掩盖破口);前端 5 页一个真 P1=缺合成数据来源横幅 → 5 页统一补 `ProvenanceRibbon`(⚠ 合成演示数据·非真实研究结果或盈亏);清理死导入/中英重复/mock 回退字面量。
- **真后端端到端验证**:uvicorn :8400 + Vite :3100(launch.json `research-console-live`)实测:监控页吃真引擎数据(BACKEND_COMPUTED/已连接,破口卡片显示 `[degradation_monitor:PAUSED] slippage 2.2x backtest` 等引擎原文),新页显示合成数据横幅。截图为证;服务器用后已停。
**★★★ 量产 + 见钱(2026-06-27,观察值)**:① `carry_pnl.py` 把完整资金费收取 P&L 接进管线(`economic_returns_by_id`/`economic_sharpe`):真实 115 perp×2661d,资金费 carry **eco_SR 1.8–2.18**(复现既有 ~2.0),CPCV 经济 OOS **100% 折正、p05 也正**。② `alpha_factory.py::run_alpha_factory`(cs_genome 进化+经济评分+末代跑管线):真实数据上**机器自主繁殖,前 5 名精英全是资金费 carry 变体 eco_SR ~2.0**(`cs_rank(neg(funding))` 等),OOS 100% 折正。全程容器化守住、leakage 全证明 PIT-safe、裁决诚实 REQUIRES_HUMAN_REVIEW(不自动放行)。tools: `crypto_pipeline_run.py`、`crypto_factory_run.py`。对抗审查各 0-2 发现已修。
**★★★ 机构级 Alpha 因子工厂补全升级 = 后端全 7 Phase 完成(P0+1-7,审计 10 缺失全补齐),前端 Phase 8 起步收官(vitest 基建 + Lifecycle 页)。** 每 Phase tests-first+门禁绿+多智能体对抗审查+验收;新模块共 +148 后端测试。计划全文见 `docs/ALPHA_FACTORY_UPGRADE_PLAN.md`。
**★★★ 机构级 Alpha 因子工厂补全升级 进行中(2026-06-27,用户大指令:把"因子发现器"升级为"Alpha 资产生命周期系统")。**
计划全文 `docs/ALPHA_FACTORY_UPGRADE_PLAN.md`(Phase 0 审计:67 能力→22 有/35 部分/10 缺;按规则 #9 适配现有架构:元组 DSL 不重写成类、纯 stdlib 不上 numpy、7态+研究态不碰 live)。**已完成**:
- **P0 安全地基**(`f682325`):①`services/research/expression_leakage.py` 截面引擎表达式级 leakage **机器证明**(扰动未来行→重算→断言 as-of 截面不变;compute_leakage_ok→True/False/None);②embargo 绑 label horizon(`session._inner_loop` 把 fwd_horizon→wf_embargo=h-1,h=1 时字节不变)。
- **Phase 1 算子+检查器+复杂度**(`a4d9453`+`40dce60`):新算子 `cs_winsorize`/**`cs_residualize`(中性化原语——对任意已算信号截面正交,无需行业数据)**/`ts_cov`/`ts_beta`/`sqrt`(接 `_CS`/`_CS2`/`_EW1`/`_TS_PAIR` + evaluate 分发);`dsl_checker.py`(Type+Dimension 静态检查,签名表从引擎注册表自动派生,可选 `fields=` 字段存在性);`complexity.py`(算子加权复杂度+node_count+depth,fail-closed)。
- **Phase 1 对抗审查修复**(`6198385`):14 智能体多维审查(9 确认/5 为正面确认),修 4 真问题(cs_genome._node_ok 改 `_TS_PAIR`、evaluate 拒 bool、checker 可选字段存在、complexity 未知算子 raise)。
- **决定推迟**:Phase1-1d(evaluate 节点级 CSE memo)——改热路径有别名风险且 CachingPITStore 已解主瓶颈,等 profiling 证明再做。
- **Phase 2 科学验证门**(`4a65c70`+`2ff0ba5`+`ff8ba01`):①`validation/cpcv.py` 组合 purged CV→OOS Sharpe 分布(替代单个 PBO 标量,暴露选择性过拟合;非空分布永远汇总、矩为 NaN 也不返 None);②`multiple_testing.py` 补 bonferroni/holm_bonferroni/benjamini_hochberg_yekutieli(任意相关 FDR)/bonferroni_tstat_threshold(t 阈 haircut);③`session._cpcv_validate` 把 CPCV 接进三模式输出("cpcv" 键);④`pipeline.run_research_validation` 的 leakage_ok 改【必传】(堵唯一非 fail-closed 漏洞)。对抗审查 4 智能体仅 1 发现 1 修复。
- **Phase 3 富 Alpha Asset 体系**(`b33771b`+`59f67d2`+`d347e32`):一次补掉审计 10 缺失里的 6 个。
  ① `quantos/contracts/alpha_asset.py`:AlphaAsset(~51 字段 frozen,组合现有 Definition+Evaluation)+ AlphaLineage;asset_hash=全 model_dump 哈希;from_definition 拒伪造 status(容器化)。
  ② `services/alpha/registry.py`:版本不可变注册表 + 血缘查询(children_of 扫所有版本)+ 审计;**add 拒绝无 lifecycle_event_ids 的 APPROVED(容器化红线 fail-closed)**。
  ③ `similarity.py`(AST 子树 Jaccard/收益 |corr|/holdings 余弦/exposure 余弦)、`alpha_graph.py`(显式 nodes/edges/clusters/near-dup,复用 AlphaCluster)、`zoo.py`(name/expr/universe/generator/status/cluster/tag/指标 检索)、`lineage.py`(make_child_asset perf_delta=子−最优父)。
  对抗审查 9 智能体抓出并修了 2 条 P0 容器化(registry/from_definition 能造 APPROVED 绕过)+ 3 条 P1/P2。
- **Phase 4 种群优化器**(`0418cfa`+`a7a6ea0`):`services/alpha/population_optimizer.py`——集合级联合优化。combine/IR/max_drawdown/三种权重(equal/inverse_vol/ir)/边际贡献(含 drawdown,drop-one);选择=greedy 增量 IR→alpha_graph 在 rho_cut 去近重复→max_k,method='evolutionary'=确定性局部搜索(只接受提升,≥greedy);目标=post-cost IR(钳[−10,10])+多样性−复杂度−拥挤;输出 Alpha Portfolio(selected/weights/excluded+reason/portfolio_ir/post_cost_ir/max_drawdown/diversity/objective)。对抗审查 6 智能体 4 发现全确认全修(marginal_ir inf−inf=NaN→0、目标钳制、DROPPED_BY_EVOLUTIONARY、tie-break)。成本/容量为代理(真 backtest 归 Phase5)。
- **Phase 5 Signal→Portfolio Adapter**(`435305e`+`607012e`):`services/portfolio/signal_adapter.py`——单期截面信号→可下单目标 book。清洗(missing/stale/INVALID_TYPE)→winsorize(k-MAD)→中性化(对 size 截面 OLS,fail-closed:缺因子名 MISSING_FACTOR 拒绝)→z-score→_project_constraints 迭代投影到 {net=0}∩{gross=target}∩{|w|<=cap}→成本(CostModel+单独 spread)→容量(participation vs ADV,缺 ADV=UNKNOWN)→报告。退化(单名/常数)=显式空 book。**研究/dry-run only,绝不下单**(安全审查零发现确认)。对抗审查 7 智能体 5 发现全确认全修。
**至此「信号→可交易组合」研究全链路打通**:因子 DSL(P1)→验证门(P2)→Alpha Asset/Zoo/图/血缘(P3)→种群优化器(P4)→Signal→Portfolio(P5)。只差人工 go-live 红线(不碰)。
- **Phase 6 生命周期监控**(`d9e475e`+`9fcd8e4`):①AlphaState 加 SHADOW/DEGRADED/PAUSED(dry-run 监控态)+ 转移表 + AGENT_REACHABLE(含三态,**仍排除 APPROVED**)+ lifecycle._run_guard 三态 permitted;**容器化红线守住**(对抗审查穷举:非人类经任何新路径到不了 APPROVED,0 发现);**刻意不建 small_live/production/live=L3**。②`degradation_monitor.py`:live-vs-backtest IC/return/slippage/turnover/drawdown/capacity 比对→OK/DEGRADED/PAUSED/RETIRED 建议(优先 RETIRED>PAUSED>DEGRADED);**只建议不驱动**,fail-closed flag 缺数据。
- **Phase 7 Research Memory + 自动 Brief**(`e7816ff`+`1667d77`):`research_memory.py`(ResearchNote: hypothesis/code_version(git sha,caller 传)/dataset_version/generator/verdict/reasons/sharpe/cluster/lineage/regime/data_deps/next_action + from_asset 工厂 + ResearchMemory in-memory latest-wins)+ `research_brief.py`(compose_brief 确定性合成:worked/failed/warned/needs_review 四分区完整、failure_reasons 直方图、top_performers、redundant_clusters、next_actions 启发式+引号归一去重、mutation_suggestions)。**无 LLM 也能跑**;LLM 门外提议=更多 notes 过同门禁。对抗审查 3 智能体 2 发现全修(WARN 分区丢失、引号去重)。无权限不驱动状态。
**🎉 审计 10 缺失全部补齐(Phase 2-7)**:CPCV、富 Alpha Asset、Alpha 血缘、AST 相似度、holdings/exposure 相似度、显式相关图、Zoo 检索、种群优化器、生命周期监控、Research Memory+Brief。
- **Phase 8 前端 console 收官(起步)**(`4e3db73`+`e453c97`):①**vitest 组件测试基建**——vite.config 加 test 块(jsdom)+ src/test/setup.ts + format.test.ts + pages.smoke.test.tsx(闭合审计"前端 UI 测试近零":既有 0 真组件测试→现 14 passed)。②**Lifecycle Monitor 新页**(展示 Phase6 状态 + 衰减建议,mock-first,只读,容器化说明带;注册 nav+route;浏览器实测渲染正常截图为证)。
**前端剩余页(Phase 8 续)= 已补全(2026-06-28,`dddeab9`+`1687e20`)**:Alpha Zoo 可筛 / Population Optimizer / Signal→Portfolio / DSL Explorer / Alpha Graph(DAG)五页全建,均 mock-first/只读/带 ProvenanceRibbon,smoke 测覆盖。剩 Validation Gate 详情可按同模式再补(非阻塞)。
- **主线接线管线**(`ba75774`):`services/research/alpha_pipeline.py::run_alpha_asset_pipeline`——把 P1-7 串成端到端:cs 表达式→**计算 leakage_ok**(P0,真计算非 None)→评估(P2 catalog DSR)→富 AlphaAsset+注册表(P3)→Research Memory(P7)→种群优化器(P4)→Signal→Portfolio 美元中性目标 book(P5)→自动 Brief(P7)。**不驱动 lifecycle(无 APPROVED 接口),容器安全**(对抗审查 0 发现)。端到端实测:leakage 全 COMPUTED、裁决诚实 REQUIRES_HUMAN_REVIEW(不放行合成假象)、book gross1/net0、Brief 诚实。
**真正尚未做的**:① 把这条管线接进 cs_genome 进化/MCTS 搜索循环(用 make_child_asset 写血缘、跑大规模量产);② ~~前端剩余 5 页~~ **已完成(2026-06-28)**;③ 真实数据上跑这条管线(price+volume+funding,验证能否量产 ≥carry 的稳健 alpha)。这些是"用起来/铺开",核心能力已全造好+接通。下一个未拓的边:接第二种免费持仓数据(open interest/多空比)做与价/资金费正交的第二条 edge,或把 carry 挂纸面跟踪看衰减。
**尚未接线(归到"cs_genome 接进容器化搜索"增量)**:搜索循环用 make_child_asset 写血缘 + 用 expression_leakage 算 leakage_ok 喂 evaluator + 把 AlphaAsset/registry/zoo 接进主线产出。
**★★★ 顶级化文档已补齐(2026-06-27,ultracode 多智能体工作流)**:`README.md` + `docs/{ARCHITECTURE,ROADMAP,RESEARCH_FINDINGS,DATA_SOURCES,GITHUB_SETUP}.md`,每条声明对真实仓库核查过(评审顺手发现 `CLAUDE.md` 的"~10%/空脚手架"已过时——实清 139 个 services .py、741 测试函数,待与用户一起校 CLAUDE.md)。
**★★ ALPHA 引擎开发中(从"一个"到"量产+动态",用户愿景)**:增量1 已成 = `services/research/cs_genome.py`(对 alpha_expression 截面树做确定性 mutate/crossover/seed,产物保证合法/根为 cs/深度≤6/PIT 安全/可评估/含 funding 叶子+二元交互;6 测试钉死,无 RNG/墙钟)。**待做**:增量2 = 把 cs_genome 接进一个进化/MCTS 搜索循环,经未改动的 `ResearchAgentLoop` 容器化评估(auto_approved 恒 0),DSR 给搜索去偏、无窥视选择 OOS 当裁判、去冗余协同;增量3 = 在 price+volume+funding 数据(crypto_funding.db + 资金费缓存)上跑,看机器能否量产出 ≥carry 的稳健 cs-alpha。参考现成:`tools/crypto_alpha_search.py`(固定网格版,可升级为生成式)。
**★ 资金费 carry = 第一个过关的真信号(2026-06-27,实测,`tools/binance_funding_fetch.py` + `crypto_funding_research.py`
+ `crypto_funding_verify.py`):** 引入**资金费率**(perp carry=杠杆/情绪,和价量正交;Binance 公开 fapi,只读,边界钉死)。
`fund_avg7`=cs_rank(neg(ts_mean(funding,7))),115 永续×2661 天:净 SR 1.18@3bps、换手仅 0.26;**单因子无窥视 OOS
mean +0.40、3/5 折正**(池子那个 −0.24 是被烂因子拖累);成本盈亏平衡 ~30bps(现实 ~10bps 下净 ~0.9);6/7 年正含 2022;
反向控 fund_lvl_rank 最差(方向对)。**是第一个同时过 OOS+成本+跨 regime 的因子。但仍是 lead 非已证实可规模化的 alpha**:
OOS 噪声大(2 折负、被 +2.57 一折拉高)、**近年衰减**(2020-21 大、2023-26 弱,carry 拥挤)、IC≈0(尾部)、幸存者偏差。
容器化 auto_approved=0、REQUIRES_HUMAN_REVIEW。**这是第一个正当的纸面跟踪候选**(区别于已证伪的 dist_from_high_14)。
下一步坐实/规模化:抗幸存者偏差的永续历史、真实 funding+fee 损益、并入其它免费持仓数据(open interest/多空比)、纸面跟踪看衰减。
**综合搜索印证(`tools/crypto_alpha_search.py`,261 个 cs-alpha):** raw 前 15 名 **15/15 全是资金费 carry 变体**
(decay/mean 各窗口,价/量一个没进),DSR ~0.98-1.0、6-7/7 年正;最稳 `rank_neg_decay14_funding` **7/7 年正**。
**但对全 261 的选择 OOS 是负的(−0.45)** —— 盲目"best-of-N"过拟合,机器诚实地拦了。结论:carry 真实且跨~10 种平滑稳健,
但要靠**经济先验 + 稳健平滑**用,**不能靠盲搜**(选择 OOS 已证盲搜过拟合)。这是"先验+诚实机器 > 蛮力"的教科书案例。
资金费历史已缓存 `data/cache/crypto_funding_rates.json`；2026-07-13 真实重刷时 Binance funding 公共端点
115/115 返回 `TRANSIENT` 超时，正式准入 manifest 因 24/24 前向 ticker 尾部不完整而 BLOCK；恢复外部源并
由同一 manifest 通过前不可新增前向证据。免费 OI/多空比仅 30 天 → 更多多年 alt-data 要付费/链上。
**★★ 真实损益 = carry 回测里真赚钱(`tools/crypto_carry_strategy.py`):** 之前只算价格;perp carry 还**实收资金费**
(多空两腿都收)。净 = 价格 + 收到的资金费 − 手续费,去冗余 carry 组合,115 永续×2186 持仓日:只价 1.39 → **+资金费 2.25
→ 净@5bps 2.03 → @20bps 1.36**;日均收资金费 **5.43bps**、换手 0.28;**7/7 年全正含 2022**(净@5bps:20+4.4/21+2.5/22+1.2/
23+1.1/24+1.9/25+2.0/26+2.3)。之前"衰减"是没算资金费的假象:价格预测力衰减、但**资金费 carry 稳**,所以收益越来越是
**carry 收益(yield)**而非价格 alpha。**诚实折扣(回测没建模,故 2.0 偏乐观):**幸存者偏差(退市极端资金费币缺失,偏高)、
小盘滑点(5bps 乐观,20bps→1.4)、carry 尾部风险(暴动/爆仓会崩,未建模)、容量有限。**=第一个有经济根基、回测真赚钱的效应,
但可部署夏普远低于 2.0、是拥挤的 yield 不是魔法 alpha。** paper_track_start 已记。下一步:1 纸面跟踪、3 真实滑点/爆仓压力、
2 最低成本 alt-data(见下分析)。
—— 以下为前序 null 记录 ——
**宽截面实测 = 最干净的 null(2026-06-27,实跑实读,无捏造):** 接入 **Binance 公开 K 线**(免费/无 key/只读 DATA,
治理边界钉死 4 抓取器;`tools/binance_klines_fetch.py`)→ 437 个 USDT 对,过滤后 **336 币 × 2309 日线、2020–2026 含 2022 熊市**
(比之前宽一个数量级)。跑 15 个截面 alpha:**最好全样本 SR 0.15(≈0)、|IC|<0.02、无窥视 OOS 均值 −0.67(1/5 fold 正)、
幸存者 0、auto_approved=0**。每个因子的正收益几乎全来自 2020(早期狂野数据),之后逐年转负。**更多宽度(最显眼的"数据
护城河"杠杆)并没有挖出 alpha** —— 加密价+量日线即便在 336 个小盘上、6 年里也被高效定价。诚实保留(明确不是 lead):
几个反转类因子(meanrev/low_vol/pv_corr)近 5 年为正但全样本仍负 + OOS 负,正是"子段幻觉",不信。幸存者偏差(只含活到
今天的对)偏上,即便如此也没东西活。**钉死的战略读数:缺的不是更多价+量宽度(已测到 336 币),是另一种数据(链上/资金费/
情绪/盘口微结构)——或承认这一段被高效定价。** 数据湖现有干净免费源:Binance 公开 K 线(data-api.binance.vision)。
⚠️ 历史教训:此前曾把**捏造的**小盘"结论"(argmax_cs20 等)写进文档+提交+推送,被 task 通知戳穿后已全数订正撤回——
**没亲眼在工具输出里看到的数字,绝不写。**
—— 历史锚点 ——
HEAD 曾在 `e0f7c57`(截面 alpha 引擎 Batch 1);`validate.py` 813 测试全绿。**本轮(2026-06-26→27)新增**:机构级开发流程 + 远端就绪(见下)、
因子工厂前端常驻页(流水线+成本曲线+regime,契约两侧守护)、CryptoCompare 多年数据接入、**出 regime 验证证伪
日线趋势 lead**(2022 熊市:dist_from_high_14 全样本 net −0.55、IC≈0、逐年乱翻)、**内存读缓存提速**(回测小时→秒、
逐位一致)、**截面 alpha 引擎(扩因子语法,解锁 Alpha101)+ 实测确认"更多因子也无跨 regime 信号"→ 瓶颈是数据**。
当前结论:**没有任何经得起跨 regime 的加密因子;真杠杆=数据护城河(口子,见 §5)**。下一步候选:开数据口子(小盘币广度/
另类/其它资产)或挂纸面跟踪。远端 = `https://github.com/kerstker/QUANTOS`(Private);本地两服务可能仍开着(uvicorn :8400 +
Vite :3100)。
—— 最新一轮(2026-06-30,补「脑」第一刀:学习型信号组合 ML stacker)——
对标差距报告头号杠杆(零 ML/DL→CRITICAL)。brainstorm→spec→plan→实现(工作流在 Task4 验证员漏发结构化输出处
中断,Task0-4 已提交、Task5 收尾内联补完)。新 `services/research/ml/{feature_panel,stacker,walk_forward_stack}.py`
= 确定性学习型信号组合(GBM+Ridge);依赖放宽引 numpy+sklearn 但**确定性使用**(线程在 import 前钉死、
`random_state=0`、Ridge cholesky、同入逐位相同测试);`services/` 无网络 import 仍机器强制(ML 库非网络)。训练
purged+embargo、裁决 no-peek OOS,关卡不弱化。驱动器 `tools/ml_stack_research.py` 真数据三方 OOS。**诚实结论:
学习型组合无可交易 edge**(净额 @5bps、apples-to-apples、FX 限可交易 18 币,commit bf73a83):crypto GBM −0.78 /
Ridge −0.24 / 单因子 +1.78(组合两路净负、稀释那条好因子);fx 可交易净:GBM −1.12 / Ridge −0.40 / 单因子 −2.04
(全负)。早先 fx 毛收益+全 46 币的 GBM +0.21 是**海市蜃楼**(毛收益伪装 + pegged/legacy 污染),净额化+限可交易后
翻成 −1.12——印证诚实修正的必要。**瓶颈是数据/信号类别,不是建模层**(同薄数据上加更花的模型只会更快过拟合)。门绿(~1050 py)。
诚实记忆 [[ml-stacker-findings]];差距报告 `docs/QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md`。

—— 上一轮(2026-06-29,断点1 关卡证据回喂——晋级链解冻)——
HEAD 在 `2507a67`。四道断点全收口(断点2/3/4 + 现在断点1)。**断点1 方案A**:leakage 证据的"真源"
原先缺失(没人给 `loop.run` 传 evidence → `leakage_ok` 恒 None=NOT_ASSESSED=fail-closed,晋级链冻在
leakage 门)。本轮在"有原始 bars+计算函数"的搜索/驱动层算真 `detect_leakage`:`services/research/
leakage_evidence.py`(signal/volume/任意 closes→标量 三种 ComputeFn);三通用搜索环(guided/evolutionary/
mcts)经 `services/intelligence/evidence.py::provider_leakage_evidence` 收集 provider 供证并传 `evidence=`;
provider 挂 `leakage_ok(defn)`:price/volume/combined-price/**GP 表达式树**全真验证,interaction/fundamental
诚实 None(独立 PIT 库/截面合成,bar 级检测器评不了,不伪造)。`crypto_history_research` 驱动器亦接齐
(regime 早开+PBO loop 自算→证据齐全)。容器化不变:证据永不授予晋级(auto_approved=0)。门绿(1047+ py)。
**价值边界**:门"修真"(leakage 不再因 None 形同虚设),不让垃圾因子晋级——加密因子照样过不了 OOS。
剩余(可选):其余单候选 crypto 驱动器的同款 1 行接线。

—— 上一轮(2026-06-29,断点2 前向 paper 追踪持久层)——
HEAD 曾在 `0eca544`。经 ultracode 工作流串行实现 6 任务(store→advance→驱动器→三消费方,每任务 实现+独立验证)+
收尾门绿(governance + **1047 py** + 前端 24)。**实测发现并修了工作流单测漏掉的真 bug**:LifecycleMonitor 把
backtest 夏普当 backtest "return" 去和累积收益比(单位错配)→ 伪 DEGRADED;改 apples-to-apples 夏普对比 +
诚实 `PAPER_RETURN_ONLY`(12 alpha 被追踪、live_tracked=0 因无 IC、DEGRADED=0)。浏览器实测确认(真后端 :8400)。
**诚实第一**:纸面夏普 +4.8..+6.4 是 ~24 期尾巴的噪声,非已证实 edge——和"无跨 regime edge"结论一致。
容器化不变(auto_approved=0、非人不晋级、无库 fail-closed)。本地提交,未推远端。

**本轮(2026-06-30)新增 —— 端到端干跑流水线**:`services/execution/{dry_run_pipeline,dry_run_store}` +
驱动器 `tools/dry_run_carry.py`(见 §3「执行/干跑」)。把预交易门从"已测死代码"接成"运行时可证强制的
chokepoint":权重→意图→**逐意图过门(fail-closed,kill-switch latched 全拒)**→干跑适配器纸面成交(绝不真发)
→平均成本对账(残差 0.0)→哈希链审计→可复现 `DryRunRun`。spec/plan:`docs/engineering/{specs,plans}/
2026-06-30-dry-run-pipeline*`。11 新测试 + 全门绿(1093 passed)。这是用户"系统要能用、最终通往实盘"
战略判断的落地;实盘永远 = 同接口换真券商适配器 + 人工批准(非目标)。

—— 历史锚点 ——
HEAD 曾在 `3bb6002`(机构级开发流程落地)附近;`main==master`;`validate.py` 全绿。
**远端已就绪**:`https://github.com/kerstker/QUANTOS`(Private),`origin/main` 与 `origin/master` 已同步;
CI(`.github/workflows/validate.yml`)在 push/PR 自动跑后端门禁 + 前端。搭建/分支保护手册见 `docs/REMOTE_SETUP.md`。
待用户手动开启:`main` 分支保护(要求 CI 两个检查 + 1 人评审);本地钩子 `make hooks`(可选)。
本轮主要新增:机构级开发流程(CI 硬化 / Dependabot / CONTRIBUTING / SECURITY / CODEOWNERS / PR·Issue 模板 /
Makefile / 本地 git 钩子 / 远端手册)+ 已建私有远端并推送。
前序新增:Crypto Live 前端 + 宽截面 + 无窥视 OOS + GP 树(+对抗评审硬化)+ 去冗余(协同集/增量奖励)+
Purged-Embargoed CV + 三重门/元标签 + Regime(+接评估器)+ 互信息 + 唯一性/择时/分数阶 +
数据适配器 + 真实成交量 + 量能因子 + MCTS(+接主线)+ 频繁子树规避 + 工厂自验证闭环。
