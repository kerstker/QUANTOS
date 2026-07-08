# QUANTOS 只读 console API 镜像(Phase 6 item 3,dim15)。
#
# ⚠ UNBUILT / UNVERIFIED:本机 Docker daemon 不可用,**无 build 证据**——本文件未经 `docker build`
#   验证。真实 build + staging/prod 隔离 + 配置管理 + 监控告警/灾备 = post-L2 部署环境工作。设计与
#   留白记录:docs/superpowers/specs/2026-07-07-containerization-design.md。
#
# 唯一此处可验证的证据 = tests/governance/test_container_boundary.py 的纯文本扫描:它机器钉死本镜像
# **结构上不可能**把凭证/券商 SDK 烤进去、且保留五旗帜——不是证明镜像能 build。
#
# L2 安全不变量(机器钉死):不 COPY key//data//live/、无凭证字面量;pip 层无券商/交易所 SDK;
# 五旗帜 env 默认关死实盘;不暴露 mutating 的 research_runner 端口(8420),只暴露只读 console(8400)。
FROM python:3.12-slim

WORKDIR /app

# 只装只读 API 依赖(requirements.txt = fastapi/uvicorn/pydantic/numpy/sklearn;无任何券商 SDK)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 只拷只读计算所需的树:services/ + quantos/。**绝不**拷 key/(凭证)/ data/(缓存/db)/ live/(执行栈)——
# 只读 console 不需要执行栈,凭证/数据也不该进镜像层。
COPY services/ ./services/
COPY quantos/ ./quantos/

# L2 安全边界:五旗帜结构性关死实盘(镜像层再钉一遍 = 纵深防御,与代码默认一致)
ENV LIVE_TRADING_ENABLED=false \
    REAL_BROKER_ENABLED=false \
    ALLOW_ORDER_SUBMISSION=false \
    ALLOW_AI_DIRECT_TRADING=false \
    REQUIRE_HUMAN_APPROVAL=true

# 只读 console API(GET-only 快照面);**不暴露** research_runner(8420,唯一 mutating 面,独立部署单独治理)
EXPOSE 8400
CMD ["python", "-m", "uvicorn", "services.research.console_api.main:app", "--host", "0.0.0.0", "--port", "8400"]
