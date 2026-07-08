# PLANNED 脚手架清单(未建,诚实标注)

> Phase 6 item 4(P3-3「清理误导结构」)的诚实交付。核实日期 2026-07-07。

## 背景:为什么"删"和"塞标记"都不对

仓库里有一批 DDD 风格的**空目录树**(auth 网关、在线学习、各种 `*_service`/`model_registry`/
`experiment_tracker` 子包等)。一次核实(`git ls-files` + `find -name '*.py'`)确认了一个关键事实:

**这些空脚手架树全部未被 git 跟踪、且零 `.py` 文件**——它们是早期 scaffold 步骤在本地文件系统留下的
目录残留,**从未提交进仓库**。因此:

- **"删空脚手架"** 在仓库层面**无物可删**:它们不在 git 里,`rm` 本地目录对 repo 是 no-op。
- **"标 PLANNED"** 若把 README/marker 文件塞进这些空目录,反而会把这批误导结构**首次提交进仓库**——
  适得其反,让"看起来在建但其实没建"的假象更显眼。

所以唯一安全且可提交的动作 = **本清单(tracked 诚实说明)+ 一个守卫测试**
(`tests/governance/test_planned_scaffold_boundary.py`):当未来某天有人把一个 stub `.py` 提交进这些
路径、使其成为 tracked dead code 时,守卫立即报错。诚实姿态与 console 的 `REAL_REPO_INVENTORY`
(SystemCompleteness 页诚实列缺失子系统)一脉相承——**删页/删目录不删诚实,用机器检查兜住**。

## PLANNED(未建)路径清单

真实代码住在**扁平的** `services/<domain>/*.py` 模块里,不在下面这些 DDD 空壳里:

| 路径 | 计划中的子系统 | 现状 |
|---|---|---|
| `services/gateway` | API 网关 / auth 层 | 空(未建,无 auth/RBAC 层) |
| `services/learning` | 在线学习层 | 空(未建) |
| `platform/` | k8s/terraform/observability/gitops 等运维基建 | 空(50+ 空子目录;容器化/隔离见 Phase 6 item 3) |
| `config/` | 集中配置 | **本地都不存在**(CLAUDE.md 该项陈旧) |
| `services/alpha/alpha_discovery_engine` | alpha 发现引擎子包 | 空(真代码在 `services/research/alpha_factory.py` 等扁平模块) |
| `services/alpha/alpha_validation` | alpha 验证子包 | 空(真代码在 `services/research/validation/`) |
| `services/data/data_quality_service` | 数据质量服务 | 空(真代码在 `services/data/*.py`) |
| `services/data/feature_store_service` | 特征库服务 | 空 |
| `services/data/market_data_service` | 行情服务 | 空 |
| `services/data/tick_service` | tick 服务 | 空(或本地缺失) |
| `services/research/model_registry` | 模型注册表 | 空 |
| `services/research/experiment_tracker` | 实验追踪 | 空(真代码在 `services/research/experiment_registry/`) |
| `services/research/dataset_builder` | 数据集构建 | 空 |
| `services/research/research_agent_loop` | agent 循环子包 | 空(真代码在 `services/intelligence/` 的 `ResearchAgentLoop`) |
| `services/portfolio/portfolio_research` | 组合研究子包 | 空(真代码在 `services/portfolio/*.py`) |

## 不在本清单的相邻情况

- `services/trading/pre_trade_risk/` —— **另一类**:隔离的**非功能坏代码**(缺域模块 + undefined-name),
  已被 `services/risk/pre_trade` 取代,单独治理,不是"空脚手架"。
- `schemas/` + `proto/` —— 已有各自的 `README.md` 诚实说明(schemas 有真实生成产物 + drift 守卫,
  proto 明说"还没有 .proto 文件、故意 tracked"),不是误导结构。
- 真实包内的空 DDD 子壳(如 `services/research/backtest_engine/{application,domain}`)—— 顶层包有真代码,
  空子壳同样未跟踪、无害,守卫清单未逐一收录(可按需扩)。

## 维护

新建了一个真实子系统 → 把它从上表移走(它现在是真代码)。想给某个 PLANNED 路径加占位 → **不要**在空目录里
塞文件;改成在本清单加一行 + 在扁平 `services/<domain>/*.py` 里落真代码。守卫测试会持续钉死"PLANNED 路径
保持 code-free"。
