# QUANTOS canonical read-only console image for native Linux ARM64 hosts.
# Cross-architecture execution is deliberately unsupported: both stages fail
# closed unless BuildKit resolves TARGETARCH=arm64. The image never copies
# credentials, runtime data, or the execution stack.
ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python@sha256:900229622a576409d52f7a66b24cf441415a828d7e503b0107bf56452a4e44ac
ARG PIP_VERSION=26.2.1
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
        zlib=1.3.2-r1 \
        build-base=0.5-r4 \
        gfortran=15.2.0-r5 \
        linux-headers=7.0.0-r1 \
        openblas-dev=0.3.30-r2

# Separate native musl locks preserve the reviewed build toolchain and runtime.
# Only scikit-learn uses a hash-pinned sdist; all build tools are pinned wheels.
# No build-isolation downloads or implicit development packages enter runtime.
COPY requirements-console-build.lock requirements-console-runtime.lock ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements-console-build.lock \
    && python -c "import importlib.metadata as m; assert m.version('pip') == '${PIP_VERSION}'" \
    && python -m pip install --no-cache-dir --require-hashes --no-build-isolation \
        --ignore-installed --prefix=/install -r requirements-console-runtime.lock

FROM ${PYTHON_IMAGE}
ARG TARGETARCH

RUN test "${TARGETARCH}" = "arm64"

WORKDIR /app

RUN apk add --no-cache \
        zlib=1.3.2-r1 \
        libcrypto3=3.5.9-r0 \
        libgomp=15.2.0-r5 \
        libssl3=3.5.9-r0 \
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
