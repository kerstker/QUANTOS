# Phase 6 —— 事件总线 + 架构解耦 + 容器化 + 清理误导结构(诚实裁定 + 落地)

> 2026-07-07 · ultracode 4-agent 并行调查(均亲自 Read/亲跑)+ 主审 TDD 落地 · IMPLEMENTATION_ROADMAP.md Phase 6
> 核心张力:Phase 6 含**架构解耦(拆 research 子包、console_api 拆 router)**,属用户明令的**"不做大重构"**红线。
> 一律按"能纯加法做的就做,大重构只记录不执行"处理。四项都拆成 clean-additive 半区(已做)+ 大重构/不可验证半区(留)。

> **2026-07-12 后续状态:** 本文的 UNBUILT/daemon 不通描述保留为 2026-07-07 历史。容器现已完成
> 同主机双冷构建、只读/非 root 冒烟和 Trivy 扫描；证据见
> `evidence/reproducibility/container_build_latest.json`。staging/prod 与跨主机复现仍留白。

## 四项裁定总览

| 子项 | 裁定 | 本轮做了(clean-additive) | 留白 |
|---|---|---|---|
| 1 统一事件总线(P2-5) | MIXED | 独立落盘总线 `event_bus.py`(publish/replay/subscribe,correlation/causation 存**列**不改 Event 契约) | 把 ID 穿线进真实 emit 点 = 大重构 |
| 2 架构解耦(P2-4) | MIXED | 域 import 边界测试(AST,钉今天已成立的 leaf 不变量) | research 拆子包 + console_api 拆 router = 大重构(域间无环今天不成立=有环) |
| 3 容器化(dim15) | MIXED | 镜像 L2 安全不变量测试 + 标注 UNBUILT 的 Dockerfile + .dockerignore | build/staging/prod/监控 = daemon 不通 + post-L2 |
| 4 清理误导结构(P3-3) | CLEAN | 空脚手架守卫测试 + `PLANNED_SCAFFOLDS.md` | —(空目录未被 git 跟踪,删=no-op) |

## 子项1 · 统一事件总线(P2-5)—— 独立总线 ✅,producer 穿线留

**已有**:`quantos/contracts/events.py::Event`(frozen pydantic 事件包 + reproducibility_hash + 5 个知名事件类型
常量)**存在但 inert**——运行时从未被 services/ 实例化;**无任何 bus/publish/subscribe/replay**;无 correlation/
causation 字段。`services/monitoring/audit_store.py::DurableAuditLog` 是理想的持久化模板(append-only 哈希链
sqlite,event_id 去重 = 防重放)但键在 `AuditEvent` 契约、非 `Event`。

**已落地**:`services/monitoring/event_bus.py::DurableEventBus`——镜像 audit_store 的 sqlite 模式(`CREATE TABLE
IF NOT EXISTS` + `threading.Lock` + 父目录自建;时间由 event 自带 → 确定可复现)。`publish(event, correlation_id,
causation_id)` 把两个 ID 存**总线列**(**不改** frozen Event 契约 → reproducibility_hash + 生成 schema 字节等价);
`replay(correlation_id, since_seq)` 按因果组回放;`records()` 暴露因果 ID;进程内**同步订阅**(commit 之后、锁外
通知,persistence-first)。fail-closed:非 Event → TypeError,重复 event_id → ValueError。**与审计链互补**——
审计链答"这条决策没被篡改",总线答"这串事件的因果链 + 可否重放"。**9 tests**。

**留白(大重构)**:把 correlation/causation 穿线进真实 emit 点(kill-switch/pretrade/promotion/dry-run)让因果图
真的长出来——改多处既有行为,按红线留后续。总线本体独立可用/可测,不依赖那步。

## 子项2 · 架构解耦(P2-4)—— import 边界锁 ✅,拆模块留

**已有**:import-linter **未安装**(不在 pyproject/requirements),无 import 契约配置。但模式已在:
`tests/governance/test_live_execution_boundary.py` AST-walk 树 + 断言禁止跨树边,已在门禁里跑。

**已落地**:`tests/governance/test_domain_import_boundary.py`(AST,移动零代码)——**只断言今天已成立**的 leaf/单向
不变量:①quantos/contracts 是真 leaf(不 import services/live/tools);②services/data 不 import 别的 services 域;
③live/ 不 import tools/;④services/ 不 import live/(纵深复述)。+teeth 自检非空洞。这些锁是未来解耦的**回归网**。
选 AST 测试而非加 import-linter 依赖:镜像既有模式、无新 pinned dep、无需改 validate.py(tests/governance 已在组内)。

**留白(大重构)**:roadmap 标题"import-linter 钉死域间无环"**今天不可满足**——research god-package 确有环
(research<->{execution,risk,intelligence,portfolio,alpha})。要绿必须**拆 research 子包**(85 .py 跨模块搬代码)
+ 拆 5320 行 console_api 为 <800 行 router(契约测试锁死 payload,每搬一条路由都有回归风险)。= 高风险大重构,
按红线留后续;上面的锁应**先落地**(它们钉住已成立的不变量,给未来解耦兜底)。

## 子项3 · 容器化(dim15)—— 安全不变量薄片 ✅,build/隔离留

详见 `docs/engineering/specs/2026-07-07-containerization-design.md`。本机 Docker daemon 不可用 → build/冒烟
**不可验证**。做了唯一可验证的 clean-additive 薄片:`tests/governance/test_container_boundary.py`(7 tests)机器钉死
镜像 L2 不变量(不 COPY key//data//live/、pip 层无券商 SDK、五旗帜、不暴露 8420),`deploy/console.Dockerfile`
(**标注 UNBUILT/UNVERIFIED**)+ `.dockerignore` 作为被扫描对象。证明镜像**不可能烤进秘密/SDK**,非证明能 build。
真实 build/staging/prod/配置管理/监控 = post-L2 留白。

## 子项4 · 清理误导结构(P3-3)—— 守卫 + 诚实清单 ✅

详见 `docs/PLANNED_SCAFFOLDS.md`。**关键核实**:那批 DDD 空脚手架树**全部未被 git 跟踪、零 .py**(本地残留、
从未提交),config/ 本地都不存在。所以"删"是 git no-op,"往空目录塞 PLANNED marker"反而把误导结构**首次提交**
(适得其反)。唯一安全可提交动作 = `tests/governance/test_planned_scaffold_boundary.py`(守卫:PLANNED 路径若
获得 .py 即报错,防未来悄悄提交 tracked dead code)+ tracked 的诚实清单 `docs/PLANNED_SCAFFOLDS.md`。不删任何
目录、不往空目录塞文件。诚实姿态与 console `REAL_REPO_INVENTORY` 一脉相承。

## 诚实边界总结

Phase 6 与前几个 Phase 的最大不同是**它的核心价值项(架构解耦、真实容器化)本质上是大重构 / 需真实部署环境**,
直接撞用户"不做大重构"红线 + 本机环境限制。诚实答案:**把每项拆成"纯加法能做的"(独立事件总线 / import 边界锁 /
镜像安全不变量测试 / 空脚手架守卫)与"大重构或不可验证的"(producer 穿线 / 拆模块 / 真实 build 与隔离),前者
TDD 做完过全量门禁,后者用设计文档 + 回归网锁记录、留用户拍板**。**全量门禁绿(validate.py 三检全 PASS)。**

**留后续(需用户拍板)**:①事件 producer 穿线(让因果图长出来);②research god-package 拆子包 + console_api 拆
router(让"域间无环"能绿,回归网已就位);③真实容器 build + staging/prod 隔离 + 配置管理 + 监控告警/灾备
(post-L2 部署环境)。
