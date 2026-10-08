"""Keep selection-bearing tool entrypoints on the durable trial ledger."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"

_FORBIDDEN_IMPORTS = {
    "services.alpha.factory": {"AlphaFactory"},
    "services.intelligence.agent_loop": {"ResearchAgentLoop"},
    "services.intelligence.evolutionary_search": {"EvolutionarySearchLoop"},
    "services.intelligence.guided_search": {"GuidedSearchLoop"},
    "services.intelligence.mcts_search": {"MCTSAlphaSearch"},
}
_FORBIDDEN_NAMES = {
    "AlphaFactory",
    "EvolutionarySearchLoop",
    "GuidedSearchLoop",
    "MCTSAlphaSearch",
    "ResearchAgentLoop",
}
_REQUIRED_SEARCH_KEYWORDS = {
    "run_alpha_factory": {
        "dataset_version",
        "search_id",
        "search_kind",
        "search_registry",
        "search_source",
        "timestamp",
    },
    "run_research_session": {
        "dataset_version",
        "registry",
        "search_id",
        "search_kind",
        "search_source",
        "timestamp",
    },
    "run_registered_candidate_batch": {
        "dataset_version",
        "recorded_at",
        "registry",
        "search_id",
        "search_kind",
        "search_source",
    },
}


def _tool_trees():
    for path in sorted(TOOLS.glob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def test_tools_cannot_construct_search_engines_directly() -> None:
    violations: list[str] = []
    for path, tree in _tool_trees():
        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported = {alias.name for alias in node.names}
                forbidden = _FORBIDDEN_IMPORTS.get(node.module, set())
                if imported & forbidden:
                    violations.append(
                        f"{relative}:{node.lineno}: imports direct search engine"
                    )
                if node.module == "services.intelligence" and imported & _FORBIDDEN_NAMES:
                    violations.append(
                        f"{relative}:{node.lineno}: imports direct search engine"
                    )
            if isinstance(node, ast.Call) and _call_name(node) in _FORBIDDEN_NAMES:
                violations.append(
                    f"{relative}:{node.lineno}: constructs {_call_name(node)}"
                )
    assert violations == [], "\n".join(violations)


def test_tool_search_calls_bind_durable_provenance() -> None:
    violations: list[str] = []
    for path, tree in _tool_trees():
        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            keywords = {keyword.arg for keyword in node.keywords if keyword.arg}
            if name in _REQUIRED_SEARCH_KEYWORDS:
                missing = sorted(_REQUIRED_SEARCH_KEYWORDS[name] - keywords)
                if missing:
                    violations.append(
                        f"{relative}:{node.lineno}: {name} missing {','.join(missing)}"
                    )
            if any(
                keyword.arg == "ephemeral"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in node.keywords
            ):
                violations.append(
                    f"{relative}:{node.lineno}: production tool enables ephemeral search"
                )
    assert violations == [], "\n".join(violations)
