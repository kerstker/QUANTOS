from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.ci_reproducibility import (
    CiReproducibilityError,
    build_lock_document,
    evaluate_coverage_summary,
    extract_backend_pytest_commands,
    verify_lock_document,
)
from scripts.validate import CHECKS


REPO = Path(__file__).resolve().parents[2]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _report() -> dict[str, object]:
    return {
        "version": "1",
        "install": [
            {
                "download_info": {
                    "url": "https://files.pythonhosted.org/packages/pytest-9.1.1-py3-none-any.whl",
                    "archive_info": {"hashes": {"sha256": "a" * 64}},
                },
                "metadata": {"name": "pytest", "version": "9.1.1"},
            },
            {
                "download_info": {
                    "url": "https://files.pythonhosted.org/packages/coverage-7.13.5-cp312.whl",
                    "archive_info": {"hashes": {"sha256": "b" * 64}},
                },
                "metadata": {"name": "coverage", "version": "7.13.5"},
            },
        ],
    }


def test_hash_locked_document_round_trips_and_binds_source_inputs() -> None:
    source_hashes = {"requirements.txt": _sha("runtime"), "requirements-dev.txt": _sha("dev")}
    document = build_lock_document(
        _report(),
        source_hashes=source_hashes,
        python_version="3.12",
        platform_tag="ubuntu_24_04_x86_64",
    )

    verified = verify_lock_document(
        document,
        expected_source_hashes=source_hashes,
        required_python_version="3.12",
        required_platform_tag="ubuntu_24_04_x86_64",
    )

    assert verified.package_count == 2
    assert verified.package_names == ("coverage", "pytest")
    assert "--hash=sha256:" + "a" * 64 in document
    assert "--hash=sha256:" + "b" * 64 in document


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda row: row["download_info"]["archive_info"].pop("hashes"), "sha256"),
        (lambda row: row["download_info"].__setitem__("url", "http://example.test/pkg.whl"), "HTTPS"),
        (lambda row: row["metadata"].__setitem__("version", "latest"), "version"),
    ],
)
def test_lock_generation_rejects_unpinned_or_untrusted_artifacts(mutation, reason: str) -> None:
    report = _report()
    mutation(report["install"][0])
    with pytest.raises(CiReproducibilityError, match=reason):
        build_lock_document(
            report,
            source_hashes={"requirements.txt": "a" * 64},
            python_version="3.12",
            platform_tag="ubuntu_24_04_x86_64",
        )


def test_lock_verification_rejects_source_or_target_drift() -> None:
    source_hashes = {"requirements.txt": "a" * 64}
    document = build_lock_document(
        _report(),
        source_hashes=source_hashes,
        python_version="3.12",
        platform_tag="ubuntu_24_04_x86_64",
    )
    with pytest.raises(CiReproducibilityError, match="source requirement digest"):
        verify_lock_document(
            document,
            expected_source_hashes={"requirements.txt": "b" * 64},
            required_python_version="3.12",
            required_platform_tag="ubuntu_24_04_x86_64",
        )
    with pytest.raises(CiReproducibilityError, match="Python target"):
        verify_lock_document(
            document,
            expected_source_hashes=source_hashes,
            required_python_version="3.13",
            required_platform_tag="ubuntu_24_04_x86_64",
        )


def test_backend_coverage_reuses_every_canonical_pytest_suite() -> None:
    commands = extract_backend_pytest_commands(CHECKS)
    expected = tuple(tuple(argv) for name, argv, _ in CHECKS if name.startswith("pytest:"))
    assert commands == expected
    assert len(commands) == 2
    assert any("tests/governance" in command for command in commands)
    assert any("tests/live" in command for command in commands)


def test_coverage_summary_enforces_line_and_branch_floors() -> None:
    summary = {
        "covered_lines": 80,
        "num_statements": 100,
        "covered_branches": 60,
        "num_branches": 80,
    }
    accepted = evaluate_coverage_summary(summary, minimum_line_percent=80.0, minimum_branch_percent=75.0)
    assert accepted.line_percent == 80.0
    assert accepted.branch_percent == 75.0

    with pytest.raises(CiReproducibilityError, match="line coverage"):
        evaluate_coverage_summary(summary, minimum_line_percent=80.01, minimum_branch_percent=70.0)
    with pytest.raises(CiReproducibilityError, match="branch coverage"):
        evaluate_coverage_summary(summary, minimum_line_percent=70.0, minimum_branch_percent=75.01)


def test_repository_ci_contract_uses_hash_lock_and_both_coverage_gates() -> None:
    workflow = (REPO / ".github/workflows/validate.yml").read_text(encoding="utf-8")
    package = json.loads((REPO / "apps/research-console/package.json").read_text(encoding="utf-8"))
    policy = json.loads((REPO / "config/ci_coverage_policy.json").read_text(encoding="utf-8"))
    lock = (REPO / "requirements-ci.lock").read_text(encoding="utf-8")
    source_hashes = {
        name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
        for name in ("requirements.txt", "requirements-dev.txt")
    }

    verify_lock_document(
        lock,
        expected_source_hashes=source_hashes,
        required_python_version="3.12",
        required_platform_tag="ubuntu_24_04_x86_64",
    )
    assert "--require-hashes -r requirements-ci.lock" in workflow
    assert "python scripts/run_backend_coverage.py" in workflow
    assert "npm run test:coverage" in workflow
    assert package["scripts"]["test:coverage"] == "vitest run --coverage"
    assert package["devDependencies"]["@vitest/coverage-v8"] == "4.1.10"
    assert policy == {
        "schema_version": 1,
        "backend": {
            "scope": ["quantos", "services", "live", "tools"],
            "minimum_line_percent": 78.0,
            "minimum_branch_percent": 67.0,
        },
        "frontend": {
            "scope": "src/**/*.{ts,tsx}",
            "lines": 73.0,
            "statements": 68.0,
            "functions": 68.0,
            "branches": 63.0,
        },
    }
