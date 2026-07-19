"""Governance boundary for the pure Pass-M family-schema composer."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULES = (
    ROOT
    / "services"
    / "research"
    / "experiment_registry"
    / "family_schema_composition.py",
    ROOT
    / "services"
    / "research"
    / "experiment_registry"
    / "family_schema_v2_catalog.py",
)
FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "aiohttp",
        "http",
        "httpx",
        "importlib",
        "multiprocessing",
        "os",
        "random",
        "requests",
        "secrets",
        "socket",
        "sqlite3",
        "subprocess",
        "time",
        "tools",
        "urllib",
        "websocket",
        "websockets",
    }
)
FORBIDDEN_FUNCTION_PREFIXES = (
    "activate",
    "execute",
    "migrate",
    "open",
    "render",
    "seal",
    "write",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _absolute_import_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module is not None
        ):
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_family_schema_composition_has_no_io_runtime_or_dynamic_loader() -> None:
    for path in MODULES:
        tree = _tree(path)
        forbidden = _absolute_import_roots(tree) & FORBIDDEN_IMPORT_ROOTS
        assert not forbidden, f"{path.name} imports forbidden runtime roots: {forbidden}"

        calls = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Attribute, ast.Name))
        }
        assert not calls.intersection(
            {
                "connect",
                "execute",
                "executescript",
                "getenv",
                "open",
                "read_bytes",
                "read_text",
                "write_bytes",
                "write_text",
            }
        )


def test_family_schema_composition_exposes_no_render_migration_or_activation_surface() -> None:
    for path in MODULES:
        function_names = {
            node.name
            for node in ast.walk(_tree(path))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert not any(
            name.startswith(prefix)
            for name in function_names
            for prefix in FORBIDDEN_FUNCTION_PREFIXES
        )

    from services.research.experiment_registry.family_schema_composition import (
        compose_family_schema_v3,
    )

    composition = compose_family_schema_v3()
    assert not hasattr(composition, "ddl")
    assert not hasattr(composition, "manifest_hash")
    assert not hasattr(composition, "activate")


def test_composed_renderer_runtime_is_transaction_scoped_and_fail_closed() -> None:
    from services.research.experiment_registry.family_schema_composition import (
        compose_family_schema_v3,
    )
    from services.research.experiment_registry.family_schema_spec import (
        FAMILY_SCHEMA_RENDERER_GRAMMAR,
        SqliteNativeExtensionPolicy,
        SqliteTrustedSchemaProfile,
    )

    composition = compose_family_schema_v3()
    runtime = composition.renderer_grammar.sqlite_runtime

    assert composition.renderer_grammar is FAMILY_SCHEMA_RENDERER_GRAMMAR
    assert runtime.trusted_schema_profile is (
        SqliteTrustedSchemaProfile.VERIFY_SEALED_THEN_TRUST_TRANSACTION_V1
    )
    assert runtime.native_extension_policy is (
        SqliteNativeExtensionPolicy.INNOCUOUS_EXTENSION_OFF
    )
    assert runtime.connection_cached_statements == 0
    assert runtime.authorizer_deny_by_default is True
    assert runtime.requires_exact_sealed_catalog is True
    assert runtime.requires_schema_version_double_read is True
    assert runtime.requires_manifest_bound_function_hashes is True
    assert runtime.allows_load_extension is False
    assert runtime.allows_connection_reuse is False
    assert runtime.closes_connection_on_every_exit is True
