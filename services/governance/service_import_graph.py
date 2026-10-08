"""Deterministic static domain-import graph and cycle-policy verification."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


class ImportGraphError(ValueError):
    """The production import graph cannot be established safely."""


class ImportGraphPolicyError(ImportGraphError):
    """The current cyclic edges do not exactly match reviewed policy."""


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _dynamic_import_function(node: ast.Call) -> str | None:
    func = node.func
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "importlib"
        and func.attr == "import_module"
    ):
        return "importlib.import_module"
    if isinstance(func, ast.Name) and func.id in {"import_module", "__import__"}:
        return func.id
    return None


def _resolve_from_import(node: ast.ImportFrom, package: str) -> str | None:
    if node.level == 0:
        return node.module
    parts = package.split(".") if package else []
    parents = node.level - 1
    if parents > len(parts):
        return None
    base = parts[: len(parts) - parents]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base) if base else None


def _imports(
    tree: ast.AST, *, package: str
) -> tuple[list[tuple[str, int]], list[tuple[int, str]]]:
    found: list[tuple[str, int]] = []
    unresolved_dynamic: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_from_import(node, package)
            if module is not None:
                if module == "services":
                    found.extend(
                        (f"services.{alias.name}", node.lineno)
                        for alias in node.names
                        if alias.name != "*"
                    )
                else:
                    found.append((module, node.lineno))
        elif isinstance(node, ast.Call):
            function = _dynamic_import_function(node)
            if function is None:
                continue
            if (
                node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                found.append((node.args[0].value, node.lineno))
            else:
                unresolved_dynamic.append((node.lineno, function))
    return found, unresolved_dynamic


def _strongly_connected_components(
    nodes: list[str], adjacency: dict[str, set[str]]
) -> list[list[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def connect(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for target in sorted(adjacency[node]):
            if target not in indices:
                connect(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])

        if lowlinks[node] != indices[node]:
            return
        component: list[str] = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        components.append(sorted(component))

    for node in nodes:
        if node not in indices:
            connect(node)
    return sorted(components)


def build_service_import_graph(repo_root: str | Path) -> dict[str, Any]:
    """Build the production ``services/<domain>`` absolute-import graph."""
    root = Path(repo_root).resolve()
    services = root / "services"
    if not services.is_dir():
        raise ImportGraphError(f"services directory unavailable: {services}")

    production_files: list[Path] = []
    excluded_test_file_count = 0
    for path in sorted(services.rglob("*.py")):
        relative_parts = path.relative_to(services).parts
        if "__pycache__" in relative_parts:
            continue
        if "tests" in relative_parts:
            excluded_test_file_count += 1
            continue
        if len(relative_parts) < 2:
            continue
        production_files.append(path)

    nodes = {path.relative_to(services).parts[0] for path in production_files}
    references: dict[tuple[str, str], set[tuple[str, int, str]]] = {}
    unresolved_dynamic_imports: list[dict[str, Any]] = []
    for path in production_files:
        source = path.relative_to(services).parts[0]
        relative = _relative(path, root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise ImportGraphError(f"cannot parse production source {relative}: {exc}") from exc
        package = ".".join(path.parent.relative_to(root).parts)
        imports, unresolved = _imports(tree, package=package)
        unresolved_dynamic_imports.extend(
            {"path": relative, "line": line, "function": function}
            for line, function in unresolved
        )
        for module, line in imports:
            parts = module.split(".")
            if len(parts) < 2 or parts[0] != "services":
                continue
            target = parts[1]
            nodes.add(target)
            if target == source:
                continue
            references.setdefault((source, target), set()).add(
                (relative, line, module)
            )

    ordered_nodes = sorted(nodes)
    edges = []
    adjacency = {node: set() for node in ordered_nodes}
    for (source, target), edge_references in sorted(references.items()):
        adjacency[source].add(target)
        edges.append(
            {
                "source": source,
                "target": target,
                "references": [
                    {"path": path, "line": line, "module": module}
                    for path, line, module in sorted(edge_references)
                ],
            }
        )

    components = _strongly_connected_components(ordered_nodes, adjacency)
    cyclic_sccs = [component for component in components if len(component) > 1]
    component_by_node = {
        node: tuple(component) for component in cyclic_sccs for node in component
    }
    cyclic_edges = [
        [edge["source"], edge["target"]]
        for edge in edges
        if edge["source"] in component_by_node
        and component_by_node[edge["source"]] == component_by_node.get(edge["target"])
    ]
    return {
        "schema_version": 1,
        "scope": "services production Python; directories named tests excluded",
        "source_file_count": len(production_files),
        "excluded_test_file_count": excluded_test_file_count,
        "node_count": len(ordered_nodes),
        "edge_count": len(edges),
        "nodes": ordered_nodes,
        "edges": edges,
        "unresolved_dynamic_imports": sorted(
            unresolved_dynamic_imports,
            key=lambda item: (item["path"], item["line"], item["function"]),
        ),
        "cyclic_sccs": cyclic_sccs,
        "cyclic_edges": cyclic_edges,
    }


def _policy_edges(policy: dict[str, Any]) -> set[tuple[str, str]]:
    if policy.get("schema_version") != 1:
        raise ImportGraphPolicyError("cycle policy schema_version must be 1")
    raw_edges = policy.get("known_cycle_edges")
    if not isinstance(raw_edges, list):
        raise ImportGraphPolicyError("known_cycle_edges must be a list")
    normalized: list[tuple[str, str]] = []
    for raw in raw_edges:
        if (
            not isinstance(raw, list)
            or len(raw) != 2
            or not all(isinstance(item, str) and item for item in raw)
            or raw[0] == raw[1]
        ):
            raise ImportGraphPolicyError(f"invalid known cycle edge: {raw!r}")
        normalized.append((raw[0], raw[1]))
    if len(set(normalized)) != len(normalized):
        raise ImportGraphPolicyError("known_cycle_edges contains duplicates")
    return set(normalized)


def _policy_dynamic_imports(
    policy: dict[str, Any],
) -> set[tuple[str, int, str]]:
    raw_sites = policy.get("known_unresolved_dynamic_imports", [])
    if not isinstance(raw_sites, list):
        raise ImportGraphPolicyError("known_unresolved_dynamic_imports must be a list")
    normalized: list[tuple[str, int, str]] = []
    for raw in raw_sites:
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("path"), str)
            or not raw["path"]
            or not isinstance(raw.get("line"), int)
            or raw["line"] <= 0
            or not isinstance(raw.get("function"), str)
            or not raw["function"]
        ):
            raise ImportGraphPolicyError(
                f"invalid unresolved dynamic import permission: {raw!r}"
            )
        normalized.append((raw["path"], raw["line"], raw["function"]))
    if len(set(normalized)) != len(normalized):
        raise ImportGraphPolicyError(
            "known_unresolved_dynamic_imports contains duplicates"
        )
    return set(normalized)


def verify_cycle_policy(
    graph: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any]:
    """Require reviewed policy to grant exactly the current cyclic direct edges."""
    known = _policy_edges(policy)
    current = {tuple(edge) for edge in graph.get("cyclic_edges", [])}
    unexpected = sorted(current - known)
    if unexpected:
        raise ImportGraphPolicyError(f"unexpected cycle edges: {unexpected}")
    stale = sorted(known - current)
    if stale:
        raise ImportGraphPolicyError(f"stale cycle permissions: {stale}")
    known_dynamic = _policy_dynamic_imports(policy)
    current_dynamic = {
        (site["path"], site["line"], site["function"])
        for site in graph.get("unresolved_dynamic_imports", [])
    }
    unexpected_dynamic = sorted(current_dynamic - known_dynamic)
    if unexpected_dynamic:
        raise ImportGraphPolicyError(
            f"unexpected dynamic imports: {unexpected_dynamic}"
        )
    stale_dynamic = sorted(known_dynamic - current_dynamic)
    if stale_dynamic:
        raise ImportGraphPolicyError(
            f"stale dynamic import permissions: {stale_dynamic}"
        )
    return {
        "status": "EXACT_BASELINE",
        "known_cycle_edge_count": len(known),
        "known_unresolved_dynamic_import_count": len(known_dynamic),
        "unexpected_cycle_edges": [],
        "stale_cycle_permissions": [],
        "unexpected_dynamic_imports": [],
        "stale_dynamic_import_permissions": [],
    }


__all__ = [
    "ImportGraphError",
    "ImportGraphPolicyError",
    "build_service_import_graph",
    "verify_cycle_policy",
]
