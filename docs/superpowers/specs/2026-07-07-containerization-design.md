# 容器化 + 环境隔离(Phase 6 item 3,dim15)—— 诚实边界:只做可验证的安全不变量薄片

> 2026-07-07 · ultracode 调查 + 主审 TDD 落地 · IMPLEMENTATION_ROADMAP.md Phase 6 item 3

## 核心诚实判断

本机 Docker daemon **不可用**(`docker info` 报 `open //./pipe/dockerDesktopLinuxEngine: The system
cannot find the file specified`;CLI 在但引擎未起)。所以 roadmap 写的验收标准——"Docker 起停 + 环境隔离
测试 / 容器冒烟测试"——在此环境**根本不可验证**(NOT_VERIFIABLE_HERE)。

在这种情况下**写一个跑都跑不起来的 Dockerfile 是典型的"未测却显得已完成"陷阱**:它看起来交付了容器化,
但零 build/run 证据背书。唯一诚实的做法是把"可验证的"和"不可验证的"切开:

- **可验证、且 clean-additive 的薄片(已做)**:一个纯文本扫描的治理测试,机器钉死镜像的 L2 安全不变量——
  它证明镜像**结构上不可能**把凭证/券商 SDK 烤进去、且保留五旗帜,**不是**证明镜像能 build。
- **不可验证 / post-L2(留白)**:真实 build、staging/prod 隔离、配置管理、监控告警/灾备。

## 已落地(可验证薄片)

- `deploy/console.Dockerfile`:最小**只读** console API 镜像(python:3.12-slim,`pip install -r
  requirements.txt` = fastapi/uvicorn/pydantic/numpy/sklearn,**无任何券商 SDK**;只 COPY services/ +
  quantos/,**绝不** COPY key//data//live/;ENV 钉五旗帜关死实盘;只 EXPOSE 8400 只读面、**不暴露** 8420
  mutating 的 research_runner)。**header 明确标注 UNBUILT/UNVERIFIED**——本机无 build 证据。
- `.dockerignore`:排除 key//data//live//.env/*.db/*.secret.yaml/node_modules,与 .gitignore 凭证/数据
  口径一致(纵深防御:即便误加 `COPY .` 也不会把凭证进 build context)。
- `tests/governance/test_container_boundary.py`(7 tests):纯文本/正则扫描上面两个文件,断言五类不变量
  (不 COPY 凭证/live/data、无凭证字面量、pip 层无券商 SDK、五旗帜 env 就位、不暴露 8420、.dockerignore
  排除项齐全)。**这是此环境唯一能拿到的证据**——证明镜像不可能烤进秘密/SDK,而非镜像能 build。

## 留白(需真实部署环境 / post-L2)

1. **真实 `docker build` + `docker compose up` + 容器冒烟**——daemon 本机不通,无法验证。
2. **staging/prod 环境隔离**——无基建落脚点(platform/kubernetes、platform/terraform 皆空,见
   `docs/PLANNED_SCAFFOLDS.md`),需真实集群/镜像仓库。
3. **配置管理**(12-factor 配置、运行时 secrets 注入)——需真实 secrets manager,此处不能安全 author。
4. **监控告警/灾备基建**——需 Prometheus/Grafana/alertmanager 之类真实运行基建。

**注**:roadmap 相邻项 P2-4(console_api/main.py 5320 行 → 拆 <800 行 router)是 LARGE_REFACTOR,**不属**
本项;容器化不得被当作重构那个模块的借口。真实容器化 = post-L2 = 显式非目标,前提是真实部署环境 +(仍然)
五旗帜结构性关死实盘。
