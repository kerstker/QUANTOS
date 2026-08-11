"""Fail-closed checks for QUANTOS repository layout and public documentation.

This checker is intentionally read-only.  It protects the small, curated entry
surface that contributors and GitHub visitors rely on without pretending that
historical design records should be rewritten on every release.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import unquote


ROOT_MARKDOWN_ALLOWLIST = {
    "AGENTS.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "QUANTOS_MASTER_BLUEPRINT.md",
    "README.md",
    "SECURITY.md",
}

CURATED_MARKDOWN = {
    "README.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "docs/README.md",
    "docs/GITHUB_SETUP.md",
    "docs/PLANNED_SCAFFOLDS.md",
    "docs/runbooks/local-research-stack.md",
    "deploy/README.md",
}

REQUIRED_IGNORE_RULES = {
    ".codex_tmp/",
    ".tmp-*/",
    ".claude/adjudication-*/",
    "__pycache__/",
    ".pytest_cache/",
    ".runtime/",
    "key/",
    "data/cache/",
}

FORBIDDEN_TRACKED_FILES = {
    ".agents/.gitkeep",
    ".benchmarks/.gitkeep",
    ".codex/.gitkeep",
    "Portfolio",
    "docs/QUANTOS_DOCUMENTATION_CONTROL_CENTER.md",
}
FORBIDDEN_ROOT_DOCS = {
    "COMPLETION_STATUS.md",
    "QUANTOS_SYSTEM_AUDIT_REPORT.md",
    "WAVE0_COMPLETION_SUMMARY.md",
}
FORBIDDEN_PLACEHOLDER_ROOTS = (
    "platform/",
    "schemas/avro/",
    "schemas/protobuf/",
    "services/gateway/",
    "services/learning/",
)
MOJIBAKE_MARKERS = ("\ufffd", "\u951f\u65a4\u62f7")
MARKDOWN_LINK = re.compile(r"!?(?:\[[^\]]*\])\(([^)]+)\)")


def _tracked_files(root: Path) -> set[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
    )
    tracked = {
        item.decode("utf-8").replace("\\", "/")
        for item in completed.stdout.split(b"\0")
        if item
    }
    # A tracked file removed in the working tree is already absent from the
    # candidate repository snapshot.  Ignoring it here lets the checker verify
    # an intentional cleanup before the deletion is staged or committed.
    return {relative for relative in tracked if (root / relative).exists()}


def _relative_link_violations(root: Path, relative_path: str, text: str) -> list[str]:
    violations: list[str] = []
    source = root / relative_path
    for raw_target in MARKDOWN_LINK.findall(text):
        target = raw_target.strip().strip("<>").split(maxsplit=1)[0]
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        target_path = unquote(target.split("#", 1)[0])
        if not target_path:
            continue
        resolved = (source.parent / target_path).resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            violations.append(f"{relative_path}: link escapes repository: {target}")
            continue
        if not resolved.exists():
            violations.append(f"{relative_path}: broken relative link: {target}")
    return violations


def audit_repository(repo_root: Path | str) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    tracked = _tracked_files(root)
    violations: list[str] = []

    root_markdown = {path for path in tracked if "/" not in path and path.endswith(".md")}
    unexpected_root_docs = sorted(root_markdown - ROOT_MARKDOWN_ALLOWLIST)
    violations.extend(f"unexpected root document: {path}" for path in unexpected_root_docs)

    for path in sorted((FORBIDDEN_TRACKED_FILES | FORBIDDEN_ROOT_DOCS) & tracked):
        violations.append(f"obsolete or accidental tracked file: {path}")

    for path in sorted(tracked):
        if path.endswith("/PLANNED.md") and path.startswith(FORBIDDEN_PLACEHOLDER_ROOTS):
            violations.append(f"distributed placeholder marker: {path}")

    gitignore_path = root / ".gitignore"
    ignore_lines = {
        line.strip()
        for line in gitignore_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    for rule in sorted(REQUIRED_IGNORE_RULES - ignore_lines):
        violations.append(f"missing .gitignore rule: {rule}")

    for relative_path in sorted(CURATED_MARKDOWN):
        path = root / relative_path
        if not path.is_file():
            violations.append(f"missing curated document: {relative_path}")
            continue
        text = path.read_text(encoding="utf-8")
        for marker in MOJIBAKE_MARKERS:
            if marker in text:
                violations.append(f"{relative_path}: mojibake marker {marker!r}")
        violations.extend(_relative_link_violations(root, relative_path, text))

    return {
        "schema_version": 1,
        "verdict": "PASS" if not violations else "FAIL",
        "tracked_file_count": len(tracked),
        "curated_document_count": len(CURATED_MARKDOWN),
        "violations": violations,
    }


def main() -> int:
    report = audit_repository(Path(__file__).resolve().parents[1])
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
