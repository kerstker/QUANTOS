from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_repository_hygiene_contract_is_clean():
    from scripts.check_repository_hygiene import audit_repository

    report = audit_repository(ROOT)
    assert report["verdict"] == "PASS", report
    assert report["violations"] == []


def test_repository_hygiene_scope_covers_public_entrypoints():
    from scripts.check_repository_hygiene import CURATED_MARKDOWN

    assert {
        "README.md",
        "CLAUDE.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "docs/README.md",
        "deploy/README.md",
    }.issubset(CURATED_MARKDOWN)
