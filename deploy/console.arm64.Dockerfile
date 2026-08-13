# QUANTOS read-only console image for Linux ARM64 hosts.
# This is additive to the retained amd64 recipe and has its own immutable
# single-platform base digest. It never copies credentials, runtime data, or
# the execution stack into the image.
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
        libgomp=15.2.0-r5 \
        libstdc++=15.2.0-r5 \
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
