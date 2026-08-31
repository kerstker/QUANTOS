"""治理:机器钉死 QUANTOS 容器镜像的 L2 安全不变量(Phase 6 item 3,dim15 的 clean-additive 半区)。

诚实边界:本测试只负责静态结构不变量;真实双冷构建、只读/非 root 冒烟和 Trivy 结果由
`evidence/reproducibility/container_build_arm64_latest.json`、
`evidence/vulnerability/container_trivy_arm64_latest.json` 与
`evidence/runtime/console_arm64_smoke_latest.json` 独立保留。现有复现证据仅覆盖原生 ARM64 主机/同 BuildKit,
不等于跨主机复现、身份签名或 staging/prod 部署证明。

镜像不变量:①不 COPY key//data//live/、无凭证字面量;②pip 层无券商/交易所 SDK;③五旗帜 env 默认关死实盘;
④.dockerignore 排除凭证/数据/执行栈;⑤只读 console 镜像不暴露 mutating 的 research_runner 端口(8420)。
"""
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DOCKERFILE = os.path.join(_ROOT, "deploy", "console.Dockerfile")
_DOCKERIGNORE = os.path.join(_ROOT, ".dockerignore")

_BROKER_SDK = ("ccxt", "python-binance", "binance-connector", "coinbase", "cbpro", "krakenex",
               "ib_insync", "ibapi", "alpaca", "oandapyV20", "quickfix", "ctp", "vnpy")
_L2_FLAGS = ("LIVE_TRADING_ENABLED=false", "REAL_BROKER_ENABLED=false",
             "ALLOW_ORDER_SUBMISSION=false", "ALLOW_AI_DIRECT_TRADING=false",
             "REQUIRE_HUMAN_APPROVAL=true")


def _dockerfile_text():
    return open(_DOCKERFILE, encoding="utf-8").read()


def _copy_targets(text):
    """所有 COPY <src> <dst> 的源。"""
    srcs = []
    for line in text.splitlines():
        s = line.strip()
        if s.upper().startswith("COPY "):
            parts = s.split()[1:]
            if len(parts) >= 2:
                srcs.extend(parts[:-1])          # 最后一个是目的地
    return srcs


def test_dockerfile_exists():
    assert os.path.isfile(_DOCKERFILE), "deploy/console.Dockerfile must exist (invariant-test subject)"


def test_dockerfile_never_copies_secrets_or_live_or_data():
    srcs = _copy_targets(_dockerfile_text())
    for src in srcs:
        norm = src.strip("./").replace("\\", "/")
        assert not norm.startswith("key"), f"Dockerfile must not COPY credentials: {src}"
        assert not norm.startswith("live"), f"read-only console image must not COPY live/: {src}"
        assert not norm.startswith("data"), f"Dockerfile must not COPY data/ (db/cache): {src}"


def test_dockerfile_has_no_credential_literals():
    text = _dockerfile_text()
    assert "-----BEGIN" not in text, "no private-key material in the Dockerfile"
    # 无形如 SECRET=/APIKEY=/TOKEN=<value> 的硬编码凭证(五旗帜是 *_ENABLED=false,不匹配下式)
    assert not re.search(r"(?i)(secret|api[_-]?key|password|access[_-]?token)\s*=\s*\S", text), \
        "no hardcoded credential literals in the Dockerfile"


def test_pip_layers_have_no_broker_sdk():
    text = _dockerfile_text().lower()
    for line in text.splitlines():
        if "pip install" in line:
            for sdk in _BROKER_SDK:
                assert sdk not in line, f"broker/exchange SDK must not be in a pip layer: {sdk}"


def test_l2_flags_are_set_false_in_image():
    text = _dockerfile_text().replace(" ", "").replace("\\", "").replace("\n", "\n")
    compact = _dockerfile_text().replace(" ", "").replace("\\\n", "")
    for flag in _L2_FLAGS:
        assert flag.replace(" ", "") in compact, f"image must pin L2 flag: {flag}"


def test_readonly_console_does_not_expose_mutating_runner_port():
    for line in _dockerfile_text().splitlines():
        if line.strip().upper().startswith("EXPOSE"):
            assert "8420" not in line, "read-only console image must not EXPOSE research_runner (8420)"


def test_dockerignore_excludes_secrets_and_data():
    assert os.path.isfile(_DOCKERIGNORE), ".dockerignore must exist"
    ignored = {l.strip().rstrip("/") for l in open(_DOCKERIGNORE, encoding="utf-8")
               if l.strip() and not l.strip().startswith("#")}
    for needed in (
        "key",
        "data",
        "live",
        ".env",
        "*.db",
        "*.secret.yaml",
        "node_modules",
        "**/__pycache__",
        "**/*.pyc",
        "**/*.pyc.*",
        "**/*.pyo",
    ):
        assert needed.rstrip("/") in ignored, f".dockerignore must exclude {needed}"


def test_base_image_is_immutable_arm64_manifest_and_build_is_multistage():
    text = _dockerfile_text()
    match = re.search(
        r"^ARG PYTHON_IMAGE=(public\.ecr\.aws/docker/library/python@sha256:[0-9a-f]{64})$",
        text,
        re.MULTILINE,
    )
    assert match, "Python base image must be an immutable official-image digest"
    assert match.group(1).endswith(
        "900229622a576409d52f7a66b24cf441415a828d7e503b0107bf56452a4e44ac"
    )
    assert len(re.findall(r"^FROM \$\{PYTHON_IMAGE\}", text, re.MULTILINE)) == 2
    assert re.search(r"^FROM \$\{PYTHON_IMAGE\} AS builder$", text, re.MULTILINE)
    assert text.count('test "${TARGETARCH}" = "arm64"') == 2


def test_runtime_is_non_root_and_does_not_retain_pip_or_build_toolchain():
    text = _dockerfile_text()
    runtime = text.split("FROM ${PYTHON_IMAGE}", 2)[-1]
    assert "COPY --from=builder /install/ /usr/local/" in runtime
    assert "python -m pip uninstall --yes pip" in runtime
    assert "rm -f /var/log/apk.log" in runtime
    assert "USER 65532:65532" in runtime
    for build_dependency in ("build-base", "gfortran", "linux-headers", "openblas-dev"):
        assert build_dependency not in runtime


def test_container_build_tool_versions_are_exact_pinned():
    text = _dockerfile_text()
    assert re.search(r"^ARG PIP_VERSION=[0-9]+(?:\.[0-9]+){2}$", text, re.MULTILINE)
    assert '"pip==${PIP_VERSION}"' in text
    for requirement in (
        "meson-python==0.18.0",
        "Cython==3.2.8",
        "numpy==2.3.5",
        "scipy==1.16.3",
        "meson==1.11.2",
        "ninja==1.13.0",
        "packaging==26.2",
        "pyproject-metadata==0.12.1",
    ):
        assert requirement in text, f"container build dependency must be exact pinned: {requirement}"
