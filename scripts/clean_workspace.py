"""Safely remove disposable local build/test artifacts from a QUANTOS checkout.

The command is a dry run unless ``--apply`` is supplied.  Credentials,
runtime authority databases, raw data, dependencies, and Git metadata are not
candidate paths.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Iterable


EXACT_DISPOSABLE_PATHS = (
    ".coverage",
    "coverage.json",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".codex_tmp",
    "playwright-report",
    "test-results",
    "apps/research-console/coverage",
    "apps/research-console/dist",
    "apps/research-console/playwright-report",
    "apps/research-console/test-results",
    "apps/research-console/tsconfig.tsbuildinfo",
    "evidence/validation_report_subset.json",
)

PROTECTED_TOP_LEVEL = {".git", "data", "key", "node_modules"}


def _assert_within(root: Path, path: Path) -> None:
    resolved_root = root.resolve()
    resolved_path = path.resolve(strict=False)
    if resolved_path == resolved_root:
        raise ValueError("refusing to clean the repository root")
    try:
        relative = resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"refusing path outside repository: {path}") from exc
    if (
        not relative.parts
        or relative.parts[0] in PROTECTED_TOP_LEVEL
        or "node_modules" in relative.parts
    ):
        raise ValueError(f"refusing protected path: {relative.as_posix()}")


def _path_size(path: Path) -> int:
    if path.is_symlink() or path.is_file():
        return path.lstat().st_size
    return sum(
        child.lstat().st_size
        for child in path.rglob("*")
        if child.is_file() or child.is_symlink()
    )


def _python_cache_paths(root: Path) -> Iterable[Path]:
    """Yield caches without descending into credentials, data, Git, or dependencies."""
    for directory, child_directories, _files in root.walk(top_down=True):
        if directory == root:
            child_directories[:] = [
                name for name in child_directories if name not in PROTECTED_TOP_LEVEL
            ]
        else:
            child_directories[:] = [
                name for name in child_directories if name != "node_modules"
            ]
        if directory.name == "__pycache__":
            yield directory
            child_directories.clear()


def _discover(root: Path, *, include_runtime: bool) -> list[Path]:
    candidates = {root / relative for relative in EXACT_DISPOSABLE_PATHS}
    if include_runtime:
        candidates.add(root / ".runtime")
    candidates.update(root.glob(".tmp-*"))
    candidates.update(path for path in root.glob("*.tmp") if path.is_file())
    candidates.update(_python_cache_paths(root))

    existing: list[Path] = []
    for candidate in sorted(candidates, key=lambda item: (len(item.parts), item.as_posix())):
        if not candidate.exists() and not candidate.is_symlink():
            continue
        _assert_within(root, candidate)
        if any(accepted == candidate or accepted in candidate.parents for accepted in existing):
            continue
        existing.append(candidate)
    return existing


def _remove(paths: Iterable[Path]) -> list[str]:
    errors: list[str] = []
    for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            else:
                shutil.rmtree(path)
        except OSError as exc:
            errors.append(f"{path}: {type(exc).__name__}")
    return errors


def clean_workspace(
    repo_root: Path | str,
    *,
    apply: bool = False,
    include_runtime: bool = False,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    candidates = _discover(root, include_runtime=include_runtime)
    items = [
        {
            "relative_path": path.relative_to(root).as_posix(),
            "size_bytes": _path_size(path),
        }
        for path in candidates
    ]
    errors = _remove(candidates) if apply else []
    removed_count = (
        sum(not path.exists() and not path.is_symlink() for path in candidates)
        if apply
        else 0
    )
    return {
        "schema_version": 1,
        "mode": "APPLY" if apply else "DRY_RUN",
        "candidate_count": len(candidates),
        "candidate_bytes": sum(item["size_bytes"] for item in items),
        "removed_count": removed_count,
        "include_runtime": include_runtime,
        "protected_top_level": sorted(PROTECTED_TOP_LEVEL),
        "items": items,
        "errors": errors,
        "verdict": "PASS" if not errors else "PARTIAL",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean disposable QUANTOS workspace artifacts")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--apply", action="store_true", help="remove discovered paths")
    parser.add_argument(
        "--include-runtime",
        action="store_true",
        help="also remove .runtime; stop managed local services first",
    )
    args = parser.parse_args()
    report = clean_workspace(
        args.repo_root,
        apply=args.apply,
        include_runtime=args.include_runtime,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
