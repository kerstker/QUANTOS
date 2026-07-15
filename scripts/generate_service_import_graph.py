#!/usr/bin/env python
"""Generate and verify deterministic service-domain import-graph evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Sequence


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from services.governance.service_import_graph import (  # noqa: E402
    ImportGraphError,
    build_service_import_graph,
    verify_cycle_policy,
)


DEFAULT_POLICY = REPO / "config" / "service_import_policy.json"
DEFAULT_EVIDENCE = REPO / "evidence" / "service_import_graph.json"


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def build_evidence(
    repo_root: str | Path, policy_path: str | Path
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    policy_file = Path(policy_path).resolve()
    policy_bytes = policy_file.read_bytes()
    policy = json.loads(policy_bytes.decode("utf-8"))
    if not isinstance(policy, dict):
        raise ImportGraphError("service import policy must be a JSON object")
    graph = build_service_import_graph(root)
    policy_result = verify_cycle_policy(graph, policy)
    graph_bytes = _canonical_bytes(graph)
    return {
        **graph,
        "verdict": "PASS_KNOWN_CYCLES_EXACTLY_BASELINED",
        "mode": "STATIC_AST_SERVICE_DOMAIN_GRAPH",
        "graph_sha256": _sha256(graph_bytes),
        "policy_path": _display_path(policy_file, root),
        "policy_sha256": _sha256(policy_bytes),
        "policy": policy_result,
    }


def _write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(_canonical_bytes(document))
    temporary.replace(path)


def _require_current(path: Path, document: dict[str, Any]) -> None:
    try:
        actual = path.read_bytes()
    except OSError as exc:
        raise ImportGraphError(f"import graph artifact unavailable: {exc}") from exc
    if actual != _canonical_bytes(document):
        raise ImportGraphError(f"import graph artifact drift: regenerate {path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate deterministic QUANTOS service import-graph evidence"
    )
    parser.add_argument("--repo-root", default=str(REPO))
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--evidence", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        evidence = build_evidence(args.repo_root, args.policy)
        if args.check:
            _require_current(Path(args.evidence), evidence)
        else:
            _write(Path(args.evidence), evidence)
    except (OSError, UnicodeError, json.JSONDecodeError, ImportGraphError) as exc:
        print(f"service import graph failed: {exc}", file=sys.stderr)
        return 1
    print(
        "service import graph: current; "
        f"domains={evidence['node_count']}; edges={evidence['edge_count']}; "
        f"cyclic_sccs={len(evidence['cyclic_sccs'])}; "
        f"cyclic_edges={len(evidence['cyclic_edges'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
