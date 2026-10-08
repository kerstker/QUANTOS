# 反馈后台单元与维护范围的可选登记

## 实现

新增固定可选组合 `quantos-demo-feedback.service` / `.timer`。原 UNITS 不变；只有 root-owned `/etc/quantos/demo-observation/feedback-automation.json` 通过确切 schema/源码摘要/单元列表检查，SystemdBackend 才纳入完整组合。任意服务名、半组合及错误登记均拒绝。

原旧范围仍写 DEMO_SCOPED_SERVICE_MAINTENANCE_V1，新组合写明确 V2。暂停/恢复使用已固定日志中的单元集合，每次观察重核当前登记范围，快照后增删登记在变更前拒绝。inhibitor 为同一完整集合安装/移除原有效条件，原 timer先停、service排空、仅恢复原 active 状态保持。维护窗口将原记录中的确切组合传给 inhibitor；旧调用不变。此范围扩展不声称全局静默，仍需原进程扫描及 writer exclusion。

新增离线 renderer `scripts/render_demo_feedback_units.py`，输出 service、timer 和登记JSON，具有逐文件摘要 review.json；只写显式输出目录，不安装/启用/启动。服务只调用 --feedback-pending，固定 release/venv，无进程重启循环；timer每次结束后五分钟检查。父进程禁网、固定写目录、阻断凭证/签发私钥文件，保留降权所需 SETUID/SETGID 与私有反馈读取所需 DAC_READ_SEARCH。研究根限显式 /home 或 /srv 下的 state 路径。文件无 [Install] 自动启用段，仍是待审部署材料。

## 验证

可选维护组合的新回归先因缺少 FEEDBACK_UNITS 失败，renderer先因模块缺失失败。实现后 renderer/服务/inhibitor/窗口联合75 passed；追加路径约束后 renderer/维护操作员/准备/worker handoff/进程检查联合55 passed。覆盖旧范围兼容、新任务暂停/排空/恢复、登记中途变化拒绝、真实临时 drop-in 字节及完整窗口组合传播。改动文件 Ruff E9/F821通过。

系统d、root身份和后台执行在测试中为替身；没有实际安装或启动单元，也未证明实际 systemd 沙箱、独立UID及进程排空行为已通过。

## 待交付

仍需冻结一致源码、完整验收并将双单元、publication profile、研究身份、自动campaign配置及维护登记纳入同一安装/回滚交付；半安装或维护窗口内切换登记不可视为可运行。尚未为当前脏工作树生成带真实 release 证明的可发布包，未做现场变更。真实 Demo、持续监控和独立经济准入仍未完成。

## 2026-09-29 固定候选交付复核

上述“尚未冻结”是此前检查点。现已冻结候选源码 `817ff6f467b6e1dbe16117c20f841997717a3662`，完整验收仍在运行；已结束的后端阶段为9515通过、19跳过，不能代表全部阶段通过。原工作区和候选工作树分别保留，候选验收期间不再修改源文件。

使用固定候选的 `python -m scripts.render_demo_feedback_units` 生成双单元和维护登记，在独立临时根内由宿主 `systemd-analyze verify --man=no --generators=no --root=...` 实际检查，退出0、无诊断。研究根、解释器和依赖target均为显式测试占位；只证明systemd语法及依赖解析，不证明真实身份切换、沙箱或后台运行。未安装、启动或启用服务。直接对宿主检查因候选解释器未安装及无关受保护单元不可读而退出1，此结果不能算作运行验收成功。

交付复核确认 `demo_operating_bundle` 是首次装配入口，材料清单不含反馈双配置和单元，不能用它重做既有安装。后续升级路径须将源版本、双配置、单元摘要、既有配置前像和研究身份共同绑定，保留原注册表、签发状态及审计历史。实际跨UID交付、维护排空和失败回滚仍需验证。

结果保存在本机 `quantos-dev-output/factory-candidate-20260928-zzg9dr5k/feedback-unit-review/systemd-static-review.json` 与 `feedback-delivery-review.json`。同目录 `retain-acceptance.py` 绑定当前验收驱动进程及启动时间，等待结束后仅在完整验收成功、报告副本一致、源码和工作树边界通过原校验器时留存发布证据；不安装、不提交源码、不切换服务。当前该任务仍在等待，不能提前声明证据留存成功。

## 同日验收完成与独立交付演练

后续canonical ALL已实际退出0、32/32阶段通过，21跳过保留；证据留存成功，独立证据提交为 `86c235374c27d2041fbc1cf834903cb0c307df78`，原源码锚仍为 `817ff6f467b6e1dbe16117c20f841997717a3662`。服务安装路径和运行参数必须使用产物提交，之前使用源码提交的单元仅作静态探针，不能直接安装。新材料位于同结果根的 `feedback-artifact-review`；其中研究状态根仍需与实际配置共同核验。

源码bundle完成独立克隆及原证据校验；76份锁定wheel按摘要复制，新venv使用无索引、强制哈希方式安装并通过pip check，随后从独立克隆导入控制器、发布器、campaign并重验清单。外部仅安装入口沿用原安装器的载荷/来源/依赖校验，18项测试通过，79份载荷完成私有暂存演练；该入口不调用返回的维护launcher，不启动服务。管理员安装、部分安装恢复和驻留服务升级/回滚尚未验收，不能把本地演练当作现场安装成功。

进一步在已存在的原生ARM64容器镜像中执行真实UID探针：挂载验收产物与独立依赖为只读，禁网、只读根、临时内存目录，使用合成非交易内容。原 `read_export` 以root读取执行UID 2001的受保护目录，原 `drop_identity` 永久切换至研究UID/GID 2003后无法恢复root且无法再次读取执行目录；原 `publish` 的重复交付保持inode/内容不变，冲突和目录符号链接均拒绝。退出0，报告 `cross-uid-probe-result.json`。未替换原身份/文件检查，但探针未调用完整 `run` 或语义反馈校验，夹具初始化所需capabilities与systemd单元不同，故不证明实际systemd沙箱、签名链或现场端到端通过；未接交易所或改变宿主运行状态。

## 双配置生成增量（尚未纳入86c235产物）

renderer现可显式接收executor UID、research UID/GID，在同一私有输出目录生成双单元、维护登记和publication profile，四文件共同纳入review摘要。配置读取器的原身份/路径/schema检查提取为共用纯校验函数，生成器和运行读取使用同一规则；UID缺失、root或执行/研究身份别名均在创建输出目录前拒绝。未提供三身份时保留原仅单元审阅模式，并通过 `publication_profile_included` 如实标识。

六个新增回归先失败，修复后连同CLI逐文件摘要、无半成品和原发布/维护三组52通过，Ruff E9/F821通过。真实安装仍须核对受保护注册表的executor UID和研究服务身份，生成文件不证明身份已经正确登记；没有写 `/etc`、启动服务或读取凭证。此增量仍在开发工作区，既有32/32结果不覆盖它。
