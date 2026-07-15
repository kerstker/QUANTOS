# QUANTOS 只读 console API 镜像(Phase 6 item 3,dim15)。
#
# 2026-07-12 已完成真实构建、只读/非 root 冒烟、Trivy 扫描,并以同一输入做两次 --no-cache OCI
# 构建,归档字节一致。保留证据:evidence/reproducibility/container_build_latest.json 与
# evidence/vulnerability/container_trivy_latest.json。诚实边界:仅同主机/同 BuildKit,尚无跨主机复现
# 或外部身份签名;staging/prod 隔离、配置管理、监控告警/灾备仍是部署环境工作。
#
# L2 安全不变量(静态测试 + 真实镜像冒烟):不 COPY key//data//live/、无凭证字面量;pip 层无券商/交易所 SDK;
# 五旗帜 env 默认关死实盘;不暴露 mutating 的 research_runner 端口(8420),只暴露只读 console(8400)。
ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python@sha256:aa679aa4eed6eb56c1dc6ad3f1b98b7d2d788fd961596779d188fdedad97fb38
ARG PIP_VERSION=26.1.2
ARG SOURCE_DATE_EPOCH=1781569204

FROM ${PYTHON_IMAGE} AS builder
ARG PIP_VERSION
ARG SOURCE_DATE_EPOCH

WORKDIR /build

ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH} \
    PYTHONHASHSEED=0 \
    TMPDIR=/build/tmp \
    CFLAGS="-ffile-prefix-map=/build=. -fdebug-prefix-map=/build=." \
    CXXFLAGS="-ffile-prefix-map=/build=. -fdebug-prefix-map=/build=." \
    FFLAGS="-ffile-prefix-map=/build=. -fdebug-prefix-map=/build=."

RUN mkdir -p "${TMPDIR}" \
    && apk add --no-cache \
        build-base=0.5-r4 \
        gfortran=15.2.0-r5 \
        linux-headers=7.0.0-r1 \
        openblas-dev=0.3.30-r2

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade "pip==${PIP_VERSION}" \
    && python -m pip install --no-cache-dir \
        meson-python==0.18.0 \
        Cython==3.2.8 \
        numpy==2.3.5 \
        scipy==1.16.3 \
        meson==1.11.2 \
        ninja==1.13.0 \
        packaging==26.2 \
        pyproject-metadata==0.12.1 \
    && python -m pip install --no-cache-dir --no-build-isolation \
        --prefix=/install -r requirements.txt

FROM ${PYTHON_IMAGE}

WORKDIR /app

# Runtime keeps only the libraries required by compiled scientific packages.
RUN apk add --no-cache \
        libgomp=15.2.0-r5 \
        libstdc++=15.2.0-r5 \
        openblas=0.3.30-r2 \
    && rm -f /var/log/apk.log
COPY --from=builder /install/ /usr/local/
RUN python -m pip uninstall --yes pip

# 只拷只读计算所需的树:services/ + quantos/。**绝不**拷 key/(凭证)/ data/(缓存/db)/ live/(执行栈)——
# 只读 console 不需要执行栈,凭证/数据也不该进镜像层。
COPY services/ ./services/
COPY quantos/ ./quantos/

# L2 安全边界:五旗帜结构性关死实盘(镜像层再钉一遍 = 纵深防御,与代码默认一致)
ENV LIVE_TRADING_ENABLED=false \
    REAL_BROKER_ENABLED=false \
    ALLOW_ORDER_SUBMISSION=false \
    ALLOW_AI_DIRECT_TRADING=false \
    REQUIRE_HUMAN_APPROVAL=true \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup -S -g 65532 quantos \
    && adduser -S -D -H -u 65532 -G quantos quantos
USER 65532:65532

# 只读 console API(GET-only 快照面);**不暴露** research_runner(8420,唯一 mutating 面,独立部署单独治理)
EXPOSE 8400
CMD ["python", "-m", "uvicorn", "services.console.main:app", "--host", "0.0.0.0", "--port", "8400"]
