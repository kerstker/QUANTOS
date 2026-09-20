# QUANTOS canonical read-only console image for native Linux ARM64 hosts.
# Cross-architecture execution is deliberately unsupported: both stages fail
# closed unless BuildKit resolves TARGETARCH=arm64. The image never copies
# credentials, runtime data, or the execution stack.
ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python@sha256:900229622a576409d52f7a66b24cf441415a828d7e503b0107bf56452a4e44ac
ARG PIP_VERSION=26.1.2
ARG SOURCE_DATE_EPOCH=1781569204

FROM ${PYTHON_IMAGE} AS builder
ARG PIP_VERSION
ARG SOURCE_DATE_EPOCH
ARG TARGETARCH

RUN test "${TARGETARCH}" = "arm64"

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

# 2026-09-20 审计 P2-11(哈希锁定依赖)——**未修复,原因是硬约束,不是疏忽**。
# 本镜像基于 Alpine(musl)。对 musllinux_1_2_aarch64 + --only-binary=:all: 实测
# 失败:`pydantic-core==2.46.4` 在 PyPI 上没有 musllinux aarch64 wheel
# (Rust 扩展),numpy/scipy/cryptography 同理。这正是下面要装 build-base /
# gfortran / openblas-dev 并从源码编译的原因。
# 因此 requirements-ci.lock 与 requirements-ci-linux-aarch64.lock 都不可用于此处:
#   * 二者是 manylinux(glibc)平台标签,在 musl 上无解;
#   * 二者由 requirements-dev.txt 生成,会把 pytest / pip-audit / import-linter
#     等测试工具装进生产镜像。
# 可行的两条路都需要单独评审,都不能当作"一行改动":
#   (a) 换成 glibc/manylinux 基础镜像 —— 会改变镜像身份,使
#       services/governance/arm64_console_evidence.py 与
#       cross_host_container_reproducibility.py 所校验的已提交容器证据失效;
#   (b) 改为对 **sdist** 做哈希锁定(需要 pin pydantic-core/numpy/scipy 等的
#       sdist 摘要并重构这里的 --no-build-isolation 构建步骤)。
# 在那之前,不要把这个 COPY 换成 requirements-ci*.lock —— 那会让镜像构建失败,
# 而不是让它更安全。
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
ARG TARGETARCH

RUN test "${TARGETARCH}" = "arm64"

WORKDIR /app

RUN apk add --no-cache \
        libcrypto3=3.5.8-r0 \
        libgomp=15.2.0-r5 \
        libssl3=3.5.8-r0 \
        libstdc++=15.2.0-r5 \
        libuuid=2.42.3-r1 \
        openblas=0.3.30-r2 \
    && rm -f /var/log/apk.log
COPY --from=builder /install/ /usr/local/
RUN python -m pip uninstall --yes pip

COPY services/ ./services/
COPY quantos/ ./quantos/

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

EXPOSE 8400
CMD ["python", "-m", "uvicorn", "services.console.main:app", "--host", "0.0.0.0", "--port", "8400"]
