from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "sigstore-evidence.yml"
BUILDER = ROOT / "tools" / "build_reproducible_source_bundle.py"
SPEC = (
    ROOT
    / "docs"
    / "engineering"
    / "specs"
    / "2026-07-30-external-sigstore-evidence-design.md"
)
ACTION = re.compile(r"uses:\s*([^\s#]+)")
PINNED_ACTION = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def test_external_sigstore_workflow_is_pinned_and_least_privilege() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    actions = ACTION.findall(source)
    assert actions
    assert all(PINNED_ACTION.fullmatch(action) for action in actions)
    assert "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0" in actions
    assert "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in actions
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in actions
    assert "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093" in actions
    assert "contents: read" in source
    assert source.count("id-token: write") == 1
    assert "contents: write" not in source
    assert "packages: write" not in source
    assert "secrets." not in source
    assert "pull_request_target:" not in source


def test_external_sigstore_workflow_proves_two_host_reproduction_and_dsse() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "ubuntu-22.04" in source
    assert "ubuntu-24.04" in source
    assert source.count("build_reproducible_source_bundle.py") >= 2
    assert "cmp --silent" in source
    assert "sha256sum" in source
    assert "python -m sigstore attest" in source
    assert "--rekor-version 1" in source
    assert "--predicate-type https://slsa.dev/provenance/v1" in source
    assert "--bundle external-sigstore/quantos-source.sigstore.json" in source
    assert "ClientTrustConfig.production" in source
    assert "github.workflow_ref" in source
    assert "external-sigstore-evidence-" in source
    assert "retention-days: 90" in source


def test_external_sigstore_contract_is_in_supply_chain_manifest() -> None:
    generator = (ROOT / "scripts" / "generate_supply_chain_evidence.py").read_text(
        encoding="utf-8"
    )
    for relative in (
        ".github/workflows/sigstore-evidence.yml",
        "tools/build_reproducible_source_bundle.py",
        "tests/tools/test_reproducible_source_bundle.py",
        "tests/governance/test_external_sigstore_workflow.py",
        "docs/engineering/specs/2026-07-30-external-sigstore-evidence-design.md",
    ):
        assert f'"{relative}"' in generator
    assert BUILDER.is_file()
    assert SPEC.is_file()
