"""Governance invariants for the canonical ARM64 read-only console image."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "deploy" / "console.Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
ARM64_BASE_DIGEST = (
    "public.ecr.aws/docker/library/python@sha256:"
    "900229622a576409d52f7a66b24cf441415a828d7e503b0107bf56452a4e44ac"
)
L2_FLAGS = (
    "LIVE_TRADING_ENABLED=false",
    "REAL_BROKER_ENABLED=false",
    "ALLOW_ORDER_SUBMISSION=false",
    "ALLOW_AI_DIRECT_TRADING=false",
    "REQUIRE_HUMAN_APPROVAL=true",
)


def _text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def _copy_sources(text: str) -> list[str]:
    sources: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("COPY "):
            parts = stripped.split()[1:]
            sources.extend(parts[:-1])
    return sources


def test_arm64_console_dockerfile_exists_and_pins_arm64_base() -> None:
    text = _text()
    assert f"ARG PYTHON_IMAGE={ARM64_BASE_DIGEST}" in text
    assert len(re.findall(r"^FROM \$\{PYTHON_IMAGE\}", text, re.MULTILINE)) == 2
    assert text.count('test "${TARGETARCH}" = "arm64"') == 2


def test_arm64_console_preserves_read_only_l2_boundary() -> None:
    text = _text()
    compact = text.replace(" ", "").replace("\\\n", "")
    for flag in L2_FLAGS:
        assert flag in compact
    assert "USER 65532:65532" in text
    assert "EXPOSE 8400" in text
    assert "8420" not in "\n".join(
        line for line in text.splitlines() if line.strip().upper().startswith("EXPOSE")
    )


def test_arm64_console_never_copies_secrets_data_or_execution_stack() -> None:
    text = _text()
    for source in _copy_sources(text):
        normalized = source.strip("./").replace("\\", "/").casefold()
        assert not normalized.startswith(("key", "data", "live"))
    assert "-----BEGIN" not in text
    assert not re.search(
        r"(?i)(secret|api[_-]?key|password|access[_-]?token)\s*=\s*\S",
        text,
    )


def test_arm64_console_build_context_excludes_sensitive_and_runtime_trees() -> None:
    ignored = {
        line.strip().rstrip("/")
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {
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
    } <= ignored


def test_arm64_console_pip_layers_have_no_broker_sdk() -> None:
    broker_sdks = (
        "ccxt",
        "python-binance",
        "binance-connector",
        "coinbase",
        "cbpro",
        "krakenex",
        "ib_insync",
        "ibapi",
        "alpaca",
        "oandapyv20",
        "quickfix",
        "ctp",
        "vnpy",
    )
    for line in _text().casefold().splitlines():
        if "pip install" in line:
            assert all(sdk not in line for sdk in broker_sdks)


def test_arm64_console_retains_no_pip_or_build_toolchain_at_runtime() -> None:
    text = _text()
    runtime = text.split("FROM ${PYTHON_IMAGE}", 2)[-1]
    assert "COPY --from=builder /install/ /usr/local/" in runtime
    assert "python -m pip uninstall --yes pip" in runtime
    assert "rm -f /var/log/apk.log" in runtime
    for build_dependency in (
        "build-base",
        "gfortran",
        "linux-headers",
        "openblas-dev",
    ):
        assert build_dependency not in runtime


def test_arm64_console_runtime_pins_fixed_security_packages() -> None:
    runtime = _text().split("FROM ${PYTHON_IMAGE}", 2)[-1]
    assert "libcrypto3=3.5.9-r0" in runtime
    assert "libssl3=3.5.9-r0" in runtime
    assert "libuuid=2.42.3-r1" in runtime


def test_arm64_console_build_tools_are_exact_pinned() -> None:
    text = _text()
    assert re.search(r"^ARG PIP_VERSION=[0-9]+(?:\.[0-9]+){2}$", text, re.MULTILINE)
    assert "m.version('pip') == '${PIP_VERSION}'" in text
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
        assert requirement in (ROOT / "requirements-console-build.txt").read_text()


def test_supply_chain_manifest_binds_arm64_container_surface() -> None:
    from scripts.generate_supply_chain_evidence import _MANIFESTS

    assert {
        "deploy/console.Dockerfile",
        "tests/governance/test_arm64_container_boundary.py",
    } <= set(_MANIFESTS)
