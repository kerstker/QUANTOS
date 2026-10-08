# 独立研究核验：Windows 11 操作员初始化材料

状态：用户公钥已交回并核对，Windows 11 x64 离线原格式签发工具已打包；
用户已交回本机原预检查公开报告，十字段和公钥 pin 核对通过；
用户又交回材料目录初始化摘要，私有 ACL 检查通过，文件系统策略为“无审核”；
用户执行显式审计配置后在 owner/group/DACL 保持检查失败，部分配置可能已写入；
原失败与备份保留，正在只读核对前后权限，真实读写探针尚未通过；
信任候选未安装，实际独立核验和 OOS 封存尚未完成。
原初始化材料和验证记录保持。
承接 [A1/G1 路线](../plans/2026-09-05-dgx-engine-foundation-roadmap-review.md)
和 [既定 F1–F5](../plans/2026-09-28-ai-factory-closed-loop.md)。
完整交付四组、五秒链与实际持续验收保持，不增加机构异机要求。

## 用户指定的控制面

用户明确：核验负责人、证据与 OOS 封存负责人、本机管理员均为本人；
核验环境是当前 Windows 11 PC；初始化前尚无 Ed25519 公钥。
这是用户提供的配置依据，尚未在该 PC 实查密钥生成或文件权限。

在该 PC 上由用户生成并保管专用研究签发密钥。DGX 只接收公钥和原件绑定的
核验材料。实际来源、结果与时间核验须由用户控制；公钥格式有效不证明
独立复算、来源可知性、成本、完整风险或锁定 OOS 已成立。

## 可审阅材料

开发输出目录为
`gold-project-closure-20261003-3pr5mrly/independent-review-onboarding-20261006`。
`Windows11-independent-review-bootstrap.zip` 仅含三个文件：

- `initialize-independent-review.ps1`：Windows 本地 OpenSSH Ed25519 初始化；
  交互输入口令，固定当前用户本地目录，确认目录 ACL；已有目录拒绝覆盖。
  公钥导出严格核对 OpenSSH 长度、类型、原始 32 字节和 SHA-256 指纹。
- `README.zh-CN.txt`：本机执行、仅交回公钥 JSON、错误保留与后续核验说明。
- `onboarding-plan.json`：已指定人员和环境；公钥、有效期、安装位置及 OOS
  边界仍为空，不是运行时配置或准入记录。

另有仅在开发侧使用的 `prepare-public-trust.py`：根据交回的公共 JSON
准备原 `QUANTOS_RESEARCH_EVIDENCE_TRUST_V1` 候选。复用实际消费者校验
独立 principal、用途、市场、代际、有效期、公钥指纹与精确 canonical bytes。
无尾部换行的候选文件摘要与原读取器 pin 一致；显式有效期由受控配置提供，
不推定当前时间、安装位置或签发权限。已有输出拒绝覆盖。

候选只准备研究来源和结果核验。原研究配置三项环境变量及独立固定读者沿用
[既有评审入口](2026-10-04-registered-project-review-product-entry.md)。
实际安装和签发是受控操作；没有新收据数据库、权限开关或现金账本。

## OOS 与剩余工程

当前长期公开观察的原协议不含独立锁定 OOS，不能事后改成未看样本。
新的 OOS 须先冻结研究设计、代码、候选、成本风险、预算、停止和揭盲政策，
明确 train / validation / locked_oos 不重叠边界，再由用户封存并保留实际访问记录。
本批未指定日期、预注册新研究、签名、修改旧窗口或建立独立时间证明。

Gold 完整九引擎风险、Gold 锁定 OOS 消费接线、实际独立控制、现场同 cut
现金恢复及获准 Demo—现金—再研究仍待关闭。
[原成交风险消费者](2026-10-06-gold-execution-risk-review.md)
保持诊断范围，公钥不能替代这些缺口或使经济准入通过。

## 验证和边界

公共候选构造器缺失的红例保留：一项测试七个子例报错。
纯公件合同两项通过；最终七项测试全部通过，6.048 秒，driver
6.131374585 秒。覆盖 RFC 8032 原公钥经真实 PowerShell 导出后进入实际
`research_evidence_authority._registry`、畸形/重复/非公件拒绝、已有输出保持、
错误 principal/指纹/权限/有效期拒绝、canonical 文件 pin 与未安装状态。

PowerShell 测试工具是隔离的官方 7.4.13 ARM64 资产，精确官方 SHA-256
已核对；初次下载不完整与原部分文件保留，以真实 HTTP 206 续接后才完整核验。
没有升级 QUANTOS 依赖。七项只验证 PowerShell 语法、公钥导出与原信任格式，
不证明 Windows 11 / PowerShell 5.1 的实际密钥生成或 ACL 操作已执行。
两份 Python 辅助器完整 Ruff 通过；3131 份原生产源码和三个辅助器测试时保持。
压缩包成员与原字节逐项核对，仅 5935 字节，未包含测试运行时或任何私钥。

本批是外部操作交接，未变更生产 Python 或发行 Source；未生成实际密钥、
安装 trust、签名、部署、切服务、写受保护状态、读取真实凭据或下单。
完整交付目标保持 ACTIVE，资料准备不计为独立核验或系统验收完成。

## 公钥交回后的实际进展

2026-10-06 用户交回 `review-public.json`，原文件字节完整留存；严格字段、
重复字段拒绝、原始 32 字节 Ed25519 公钥和 SHA-256 指纹核对通过。
key_id 为 `research-review-20261006-01`，principal_id 为
`quantos-user-independent-reviewer`，公钥指纹为
`67bdfa3ae32a815e261728e07cb4e7e762e01b5e37f7f2bd16ad6b08508cf7ce`。
导出明确 `custody_status=NOT_INDEPENDENTLY_VERIFIED_BY_THIS_EXPORT`；不能
仅凭公钥交回声称私钥保管、Windows ACL、独立复算或时间证明已完成。

复用冻结 21eab 的真实 `_registry` 准备公共候选，精确文件 SHA-256 为
`4bc1d6e9d1d970037cb4b8dfeb0632c4ab3c256d79a31786ea538d00e96a9b6e`。
候选代际 1，authority_id 为 `quantos-user-independent-research-review`；
用途仅 SOURCE_ATTESTATION/RESULT_REVIEW，市场仅 XAUUSDT。
建议密钥窗口为 UTC 2026-10-06 05:05:46 至 2027-01-04 05:05:46；它是
90 天开发草案，尚未经操作员确认，不是 OOS 边界或独立时间证据。
候选内部 ACTIVE 字段尚无运行时效力，实际 trust 未安装，原经济准入和
下单开关仍关闭。候选不包含任何私钥，也未签发评审结果。

原件位于 `gold-project-closure-20261003-3pr5mrly/independent-review-public-key-20261006`：
公共原件、候选、准备收据与 `public-key-intake.json` 均单独持久化。
原 21eab HEAD 干净且保持，没有变更发行 Source、实际配置或保护状态。
交回时仍需可审阅的 Windows 原格式签发工具、真实核验材料、预先锁定 OOS
及用户控制的封存/访问记录；不补签当前旧窗口或把 UNKNOWN 改为 PASS。

## Windows x64 原格式签发工具

用户确认系统类型 x64。外部开发输出为
`gold-project-closure-20261003-3pr5mrly/independent-review-signing-20261006`。
`Windows11-independent-review-signing-x64.zip` 为 15,268,445 字节，219 成员；
SHA-256 为 `11f170b92a1a5faa02cf8dc00f719f3505d91332f7020bfbe1e4aea3aaedc3a2`。
公开清单逐项固定 218 文件，压缩包全部成员与展开原字节、实际交回的公共
JSON 及冻结 21eab 的三段签名函数／JSON 读取合同核对通过。

包内专用 Python 3.12.10 x64 运行时来自官方 embeddable 资产；Windows
cryptography 50.0.0、cffi 2.1.0、pycparser 3.0、bcrypt 5.0.0 的精确资产
SHA-256 与官方发布元数据核对，34 个 PE 成员的 AMD64 架构核对。
它们仅供独立 Windows 工具；QUANTOS 应用虚拟环境和 ARM64 锁没有变更。
在 Linux 未执行 Windows 二进制，不把下载摘要当作独立身份签名核验。

首轮由用户在 PC 执行 `start-independent-review.ps1 -CheckOnly`。先验证全部
包内字节、实际成员及无 reparse 路径，再检查固定密钥目录／文件的 owner、
限定用户与 SYSTEM 的 ACL 和目录继承保护；只读元数据，不读私钥内容。
专用 Python 再核对已交回公共 JSON pin。通过才创建独占公开报告
`signer-preflight-*.json`；本机实际运行和保管检查待交回，不能先记 PASS。

真实签发仅接受既有 SOURCE_ATTESTATION／RESULT_REVIEW 的 V2／V3 canonical
未签声明。操作者先审阅完整原声明，显式输入 `SIGN <payload SHA-256>`，
再本机输入口令。只接受固定路径的加密 OpenSSH Ed25519 私钥，必须匹配已
交回公钥；生成现有 64 字节原 Ed25519 签名，不使用 SSHSIG 或新 authority
格式。声明、时间、角色、字段、输入原件和输出边界受检，已有文件拒绝覆盖；
UNKNOWN／FAIL 保持。缺少真实材料时没有提供可签的伪研究声明。

缺失辅助器红例、初次隔离导入错误和 Windows CRLF 加密键头反例均保留。
修复后 8 项测试通过，0.557 秒，driver 0.706926026 秒，3132 冻结 Python
前后相同。只用公开 RFC 8032 测试种子，四种目的／版本的 payload 与实际
Source 相同，签名经原 `_verify` 接受；覆盖错误格式、歧义 JSON、未知结论、
口令／公钥错配、未加密键、CRLF、symlink／hardlink／大小边界和原输出保持。
签发辅助器完整 Ruff 通过。真实隔离 PowerShell 7.4.13 的原脚本语法和
Windows 路径字符串表达式通过；这些不是 Windows 11／PowerShell 5.1 的
实际运行证明。包内没有测试私钥、实际私钥或测试驱动。

签名仅认证原声明，不替代独立复算、原材料真实性、完整风险、时间和锁定
OOS。首轮检查不安装 trust、不签声明、不改变经济或交易权限。原最终同版
完整门仍 31 PASS／1 TIMEOUT，完整四组、五秒链及实际 24 小时保持；
资料与组件完成不宣称系统交付完成。


## Windows 实际 ACL 错误定位

用户再次交回的review-public.json与首份598字节导出完全一致（导出SHA
374e77317e8b2008207990074f8e04e24a3fdb1aec47e18014eef7cb79424e33），
仍是公钥，不是signer-preflight报告。实际CheckOnly输出为
KEY_CUSTODY_UNEXPECTED_PRINCIPAL。用户按只读命令交回目录/文件ACL：
两者owner均为当前用户、继承均保护；目录只有用户及SYSTEM。私钥文件另有
S-1-5-32-544（Administrators）显式Allow/FullControl，触原签发器拒绝。

初始化流程仅在调用ssh-keygen之前检查目录ACL，没有在生成后核对私钥文件
ACL；本次本机自检发现该差异。原材料/公钥保持。已准备经隔离PowerShell
语法核验的定向命令，核对原用户SID、owner、保护及原三条规则，先把SDDL
元数据保存到本机TEMP新文件，再仅RemoveAccessRuleSpecific该Administrators
规则并调用原CheckOnly。其余用户/SYSTEM权限不扩大，未修改原签发器或包
清单，不重建密钥。开发侧不读私钥内容、不在PC执行权限操作；实际结果待回。

原公共重交原件、用户报告的元数据与命令保留于外部
independent-review-acl-diagnosis-20261006。用户报告不是独立实查收据；在PC
自检成功输出/公开报告交回前，不记本机检查PASS。实际trust、签名、时间和
锁定OOS仍未完成；未改变经济或交易准入。


用户随后报告指定H:\trading\repair-command.ps1不存在，PowerShell未执行修复。
已交同一经语法检查的命令块直接粘贴执行，省去下载/另存路径。该报告不是
权限操作失败或成功；原公钥和材料不变，实际ACL修复/CheckOnly结果仍待回。

## 用户报告修复后的原检查通过

2026-10-06 用户交回执行输出：先保存 ACL_METADATA_BACKUP 至本机 TEMP，
原签发器返回 SIGNER_RUNTIME_AND_PUBLIC_PIN_READY_NO_KEY_READ，key_id
仍为 research-review-20261006-01，private_key_read=false、trust_installed=false、
independent_verification_complete=false、submission_enabled=false；随后输出
PUBLIC_PREFLIGHT_REPORT_CREATED。公开报告路径为
`H:\trading\Windows11-independent-review-signing-x64\signer-preflight-20261006-125816-0249384.json`。

这表示操作员报告的 ACL 修复及原 CheckOnly 已成功，不能继续写成“尚无 PC
输出”。原终端文本与结构化收据留存在外部
independent-review-windows-preflight-20261006；状态为
WINDOWS_SIGNER_PREFLIGHT_OPERATOR_REPORTED_PASS。公开 JSON 内容尚未交回，
public_report_content_received=false。已请求只读 Get-Content -Raw 输出该报告，
用于对照原十字段 schema、公钥 pin 和全部 false 边界；没有请求私钥或备份
文件内容，也不需要重新生成密钥或再次修复 ACL。

本机文件名使用本机 UTC 时钟，不是独立时间证明；脚本检查不能替代独立
来源／结果复算、预锁 OOS、封存访问历史或经济准入。实际 trust 未安装，
签发和完整独立核验未完成，原交易边界保持。

## 公开报告接收与后续交接准备

开发侧已准备 `independent-review-preflight-intake-20261006/preflight_intake.py`，
仅接收操作员明确交回的公开报告，不读取 PC 或私钥，不签发或安装 trust。
复用当前冻结 584 的原重复字段拒绝 JSON 读取器；核对原 Windows launcher
十字段、固定 key_id／公钥指纹及六项严格布尔 false。字符串 false、数字 0、
额外／缺失／重复字段、错误指纹或身份均拒绝，不从终端成功文字合成报告。
完整收到的字节独占留存，链接／超限／读取中变化拒绝，已有输出不覆盖。
接收收据仅证明公开报告的格式与 pin 匹配；不证明独立 ACL 实查或时间。

缺少接收器的原失败日志保留。按实际原 launcher 提取公开报告合同的六项
定向测试通过，新辅助器与测试完整 Ruff 通过；原源码与 Main HEAD/index
保持。测试全部使用合成公开报告，actual_public_report_received=false，
不是用户 PC 的再次实查或独立来源／结果核验完成。

同目录的 `README.zh-CN.txt` 与 `handoff-plan.json` 明确当前唯一操作员待办：
交回已经生成的公开报告内容。后续由开发侧准备完整原件核验包及 PC 可执行
步骤，再确定用户控制的原件／新 OOS 封存／实际访问记录位置。九引擎独立
风险、锁定 OOS 消费和现场闭环仍是开发及实际验收责任；不要求用户手写
研究合同或把旧观察倒签为 OOS。目录／日期／信任有效期保持未指定或未确认，
没有提前建立封存、签名、权限或新的经济状态。完整四组及 F1–F5 不变。

## 公开报告已接收；用户已指定 Windows 材料目录

用户随后贴回原 Get-Content -Raw 的公开 JSON。现有接收器实际执行
退出0，十字段、key_id、公钥指纹及全部严格 false 均匹配。接收与原文本
留存在 independent-review-preflight-intake-20261006/operator-public-report-20261006，
状态 PUBLIC_PREFLIGHT_SCHEMA_AND_PIN_MATCH，public_report_content_received=true。
对话接收的 LF 文本 SHA 为
ce4d0a69b82f5ed745f16ae07f055eb290fa4492e030042912ed982813fa8347；
它不是未实读的 Windows 文件原字节摘要或独立时间证明。此前“正文待回”
记录为历史等待状态，不再要求重复交回同一报告。原签发包不改。

用户选择 H:\trading\QUANTOS-independent-review 为材料工作目录。
independent-review-workspace-20261006/Windows11-review-workspace.zip
已打包核对，7147字节、四成员，SHA 为
893d1e7ccae741c4f72526ca62475e427a4b7579e5548d1c546df700d5a6357a。
脚本默认只读；显式 -Initialize 只创建新的所选根及四个角色目录，
以当前已报告 SID 和 SYSTEM 的原始规则限定 owner、保护和权限。
本地 NTFS／父路径／reparse 前置先检查，已有目录拒绝修改，公开报告
独占写入。审计策略仅按官方文件系统 GUID 查询，Security 日志只取
元数据，不读取事件内容、不更改系统策略/SACL或日志保留。

五项真实隔离 PowerShell 解析、纯元数据守卫、路径、独占公件和非 Windows
拒绝检查通过，最终1.700秒、driver见原 exit；测试源摘要保持，
测试完整 Ruff 通过。初次测试驱动意外前导 plus 的解析失败、误分类及修正
说明保留，之后脚本实际缺失的失败日志单列；没有把驱动失败冒称产品红例。
目录创建／ACL 和审计查询仍待用户 PC 执行，不借 Linux 检查声称已执行。

当前只准备目录和前置实查；空 sealed-oos/access-log 不是封存或访问历史。
新的边界、冻结设计及实际访问审计仍待建立，信任／签发／独立来源结果
核验和经济准入均未完成。实际完整发行门仍由冻结584原进程继续，
不是本辅助包的组件成绩。未变更发行 Source、生产配置或交易权限。

## 当前研究入口的信任草案兼容检查

发现原21eab公共候选的 producer_ids 不含当前 Gold BACKTEST 的
REGISTERED_GOLD_REANALYSIS_RUNNER_V1。冻结584的实际 _registry 读取
原精确 canonical 候选并报 RESEARCH_EVIDENCE_PRODUCER，错误收据保留。
新外部 independent-review-current-trust-20261006 候选只增加该明确入口，
当前三研究 producer 格式/pin 全部接受；未知 producer 和错误 pin 两个
实际调用仍分别拒绝。它不是某次实际研究材料、签名或经济资格的接受。

新候选 SHA 为
8e859db6a701dc3a17bfefae83d799e9a3c9c1d210f14b2dc115506b523922ce，
代际1、公钥/principal/authority、用途、市场与原未确认的90天有效期均保持。
原草案和 Windows 签发包没有覆盖；不能替换清单内旧文件来绕过包核对。
实际范围、期限、位置与安装仍须以完整材料供用户审阅，trust未安装。

## 用户材料目录实查与访问审计工具

用户在 PC 执行原 Initialize 并回贴终端摘要：PRIVATE_WORKSPACE_CREATED_ACCESS_AUDIT_PENDING；
H:\trading\QUANTOS-independent-review 与当前 SID 匹配，directory_acl_verified=true；
文件系统子类别查询成功，但本机 KERSTS-PC 的结果为“无审核”。
Security 日志仅元数据查询成功。回贴未含完整原报告的结束部分或文件路径，
仅记 OPERATOR_REPORTED；不因此重复要求用户运行初始化。后续工具会实查当前 ACL。
access_history_established/oos_lock_established/trust_installed/submission_enabled 仍 false。
外部 operator-workspace-result.json 保存这份有限接收记录。

independent-review-access-audit-20261006 的 Windows11-review-access-audit.zip
为四件平铺包，11554 字节、SHA
48312590c9f5e1d2e4b1684627eb8d64a61602c649f397ab8531af7d2fd24316。
默认 CheckOnly，只在同用户管理员窗口显式 EnableAccessAudit 才改变配置。
复用目录初始化工具原纯守卫块，共同助手摘要 f3423bcc 在加载前核对；
主脚本重新检查本人 SID、固定 NTFS、本机路径无 reparse、五个目录原私有 ACL。

改变前在 access-log 独占新建私有 audit-setup 目录，保存原单一文件系统策略 CSV、
三个角色目录完整 SDDL 和 owner/group/DACL；备份成功才请求启用该全机子类别的
成功/失败审计。只向 originals/sealed-oos/review-replies 追加 Everyone 继承 SACL；
这是审计规则，不授予访问权限，原 custody 必须逐项保持。其他材料已存在则在
更改前拒绝；只容许前次公开 probe，避免把既有 OOS/材料补称已建立审计历史。
根目录、access-log 和密钥目录不添加 SACL，也不改日志保留或其他审计子类别。

随后独占写入并读取 sealed-oos 下的公开 ASCII probe，仅查询该实际路径/当前 SID 的
Security 4663 新 RecordId，解析器再绑定当前 PID、对象类型、原 provider/channel，
拒绝旧记录、重复字段、畸形 XML 和外部实体。ReadData/WriteData 实际使用都出现才
报告 ACCESS_AUDIT_CONFIGURED_REAL_READ_WRITE_PROBE_VERIFIED。实际原件只含该
公开 probe 的事件 XML；probe、配置备份、失败与成功报告均保留且不覆盖。
失败不自动关闭全机策略或删除日志。未试验失败访问；4663 本身只有成功事件，
后策略查询保留原文，不把请求开启失败审计当作失败事件已观察。

七项真实隔离 PowerShell 解析/守卫/原 XML 解析/事件完整性/独占备份先于修改/
失败日志/非 Windows 拒绝通过，测试2.398秒、driver2.447秒，完整 Ruff 通过。
初轮一项失败来自测试命令最后已捕获异常仍留下 PowerShell 失败退出状态；
加入明确的成功末命令后通过，初轮输出及修正说明保留。实际 Windows 原生 API
不以 Linux 解析或合成事件夹具代替，PC 启用结果仍待用户执行交回。
现时连续导出/日志保留窗口未建立，报告保留完整访问历史、OOS、独立完成、
经济权限与下单均 false；本机时间不认证，不读取真实私钥。

依据 [Windows 文件系统审计](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/auditing/audit-file-system)
需同时有适用的系统策略与对象 SACL；[4663 原事件合同](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/auditing/event-4663)
用于验证实际权限使用。工具只准备相应操作材料，不声明 A1/G1 已完成。

当前 584 同版全门的 backend 阶段已真实超过原 3600 秒预算，TIMEOUT 原件保留；
原 canonical 仍继续后续阶段，不是整门通过。低优先级独立 584 克隆仅对一项
原统计回归进行耗时剖析，原夹具/断言/安全环境保持；单项结果不替代 ALL 验收。
目录审计交接可独立继续，未改变发行 Source、现场服务或完整交付条件。

该隔离单项已实际 exit0/1 PASS，剖析49.599秒，源码前后仍3138/4333b6db。
原统计测试没有单项死锁；带 cProfile 开销时发现大量重复传输校验/序列化调用，
热点累计时间重叠，不等于原无剖析时的延迟，也不能据此确认整组超时的唯一原因。
诊断原日志/pstats/热点与限定收据保留于 backend-profile-20261007；
未改测试或实现、未重跑完整门、未把诊断成功当作原超时已解决。

## Windows 审计启用实际失败：保留配置并只读诊断

用户回贴实际 Windows 终端：原启用命令打印备份目录
`H:\trading\QUANTOS-independent-review\access-log\audit-setup-2dd5dca016e44b139839d39f74c71881`，
随后在原脚本第 38 行抛出 `REVIEW_AUDIT_CHANGED_OWNER_GROUP_OR_DACL`。
仅记 OPERATOR_REPORTED_FAILURE；尚未收到 PC 上的配置失败 JSON 和前后 ACL 原元数据。
外部接收记录保留于 `independent-review-access-audit-failure-20261007`，原 ZIP 不覆盖。

原脚本先备份、请求系统文件系统审计，再逐角色添加 SACL 并复查权限。
此次错误可能出现在写入前或写入后的字符串比较；终端本身不能确定失败角色、
已写入的范围或实际权限差异。系统策略和部分 SACL 可能已改变，不能据此声明
回滚、权限已恢复、审计链路可用或真实读写事件已通过。不重复执行启用或初始化，
不关闭全机审计策略、不删除备份或日志，也不绕过 custody 检查。

当前只读诊断要求读取本次备份的 `configuration-failure.json`，并对比
originals、sealed-oos、review-replies 的备份 custody SDDL 和当前 owner/group/DACL：
所有者和组 SID、继承保护及各 ACE 的 SID、类型、权限、继承与传播标志。
不读取私钥、研究原件或 Security 事件正文。待实际元数据明确差异后再决定修复。

开发侧核对原代码确认使用 `Set-Acl` 提交整个 ACL。Microsoft 的
[PowerShell 文件系统 provider 源码](https://github.com/PowerShell/PowerShell/blob/master/src/System.Management.Automation/namespaces/FileSystemSecurity.cs)
也显示其先尝试写入 All sections；
[.NET Framework 文件系统权限参考源码](https://github.com/microsoft/referencesource/blob/main/mscorlib/system/security/accesscontrol/filesecurity.cs)
则按修改标记决定持久化的 sections。这些资料支持检查写入范围，不能证明该 PC
的权限变化原因。没有以 SDDL 格式猜测放宽检查，也没有交付未经原生核验的修复。

此前 Linux 解析和纯函数测试通过仍保留为限定证据，不替代此次 Windows 失败。
完整访问历史、OOS 封存、信任安装、独立核验、经济权限与下单均未成立。

### 审计部分专用写入修正版与只读诊断包

外部独立目录independent-review-access-audit-sacl-only-20261007保留原三文件副本，
原启用包／备份／失败不覆盖。修正版用新的DirectorySecurity对象仅导入Audit部分，
再通过DirectoryInfo.SetAccessControl提交已修改的审计部分；不经Set-Acl provider提交
owner/group/DACL。原字符串保持守卫、ACL事实守卫、先备份、失败留存、原探针和边界不变。

实际配置函数接模拟OS边界先因全权限提交后custody变化而FAIL，改为只提交Audit后，
连同原七项共8 PASS／2.639秒。模型验证写入范围和守卫，未证明该PC的根因或修复成功。
首轮mock误拦截公共输出编码构造器的夹具错误另保留，修正后红例才作为上述证据。
修正版实际PC执行待原备份／当前元数据对比，没有交付用户重跑启用。

另有平铺只读Windows11-review-audit-diagnostic-v2.zip，6367字节／SHAc8c73b96；
未交付的初版归档／四项结果原样保留，最终版让各角色规则与SDDL取自同一ACL对象。
仅包含inspect-review-audit-failure.ps1、原公共helpers、说明和manifest，没有启用入口。
锁定该账户SID、工作目录和本次备份，只读两份有大小限制的配置元数据、三个角色
当前ACL，显示逐角色原／当前owner/group/DACL原SDDL和当前规则；格式或权限差异
均显示false，不擅自规范化或声明恢复。完整路径绑定、重复／未知角色、其他文件名／
过大文件、差异的诚实报告、同一ACL对象与非Windows拒绝5 PASS，driver3.973秒，
源码前后相同，Ruff通过。
包成员与本地文件逐字核对；可以替代此前手动只读命令，输出尚待用户PC执行交回。
不查询Security事件正文、不读取私钥／研究原件，不修改权限、策略、日志、OOS或信任。
实际PC权限状态、审计原生链路和完整访问历史仍未建立，不能放宽检查后重跑旧启用器。
