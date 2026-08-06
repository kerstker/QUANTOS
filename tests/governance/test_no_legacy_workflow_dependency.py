from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
_SKIP_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".runtime",
    ".venv",
    ".worktrees",
    "__pycache__",
    "cache",
    "dist",
    "node_modules",
    "worktrees",
}
_TEXT_SUFFIXES = {
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_TEXT_NAMES = {".gitignore", "Dockerfile", "Makefile"}
_ENGINEERING_DOC = re.compile(
    r"docs/engineering/(?:specs|plans)/[A-Za-z0-9._-]+\.md"
)


def _is_formal_workflow_doc(relative: Path) -> bool:
    parts = relative.parts
    return (
        len(parts) == 4
        and parts[0] == "docs"
        and parts[1] == "super" + "powers"
        and parts[2] in {"plans", "specs"}
        and relative.suffix.lower() == ".md"
    )


def _repository_text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if relative.parts[0] == "." + "super" + "powers":
            continue
        if _SKIP_PARTS & set(relative.parts):
            continue
        if path.suffix.lower() in _TEXT_SUFFIXES or path.name in _TEXT_NAMES:
            yield path, relative


def test_repository_has_no_legacy_workflow_dependency():
    marker = "super" + "powers"
    violations: list[str] = []

    for path, relative in _repository_text_files():
        if _is_formal_workflow_doc(relative):
            continue
        rendered = relative.as_posix()
        if marker in rendered.lower():
            violations.append(f"path:{rendered}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if marker in text.lower():
            violations.append(f"content:{rendered}")

    assert not violations, "legacy workflow dependency remains:\n" + "\n".join(
        violations[:50]
    )


def test_legacy_workflow_marker_is_allowed_only_in_formal_docs():
    marker = "super" + "powers"
    assert _is_formal_workflow_doc(Path("docs") / marker / "plans" / "approved.md")
    assert _is_formal_workflow_doc(Path("docs") / marker / "specs" / "approved.md")
    assert not _is_formal_workflow_doc(Path("docs") / marker / "plans" / "nested" / "x.md")
    assert not _is_formal_workflow_doc(Path("services") / "workflow.py")
    assert not _is_formal_workflow_doc(
        Path("services") / ("." + marker) / "workflow.py"
    )
    assert not _is_formal_workflow_doc(Path("config") / "workflow.yaml")


def test_engineering_document_references_resolve():
    references: set[str] = set()
    for path, _relative in _repository_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        references.update(_ENGINEERING_DOC.findall(text))

    missing = sorted(reference for reference in references if not (ROOT / reference).is_file())
    assert not missing, "missing engineering documents:\n" + "\n".join(missing)
