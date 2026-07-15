import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _write(root: Path, relative: str, content: str = "") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _edge_pairs(graph: dict) -> list[tuple[str, str]]:
    return [(edge["source"], edge["target"]) for edge in graph["edges"]]


def test_graph_discovers_domain_edges_cycles_and_references(tmp_path):
    from services.governance.service_import_graph import build_service_import_graph

    _write(
        tmp_path,
        "services/a/one.py",
        "from services.b.api import run\n"
        "importlib.import_module('services.c.plugin')\n",
    )
    _write(tmp_path, "services/b/two.py", "import services.a.model\n")
    _write(tmp_path, "services/c/three.py", "VALUE = 1\n")
    _write(
        tmp_path,
        "services/a/tests/test_hidden.py",
        "from services.c import forbidden_test_only_edge\n",
    )

    graph = build_service_import_graph(tmp_path)

    assert graph["nodes"] == ["a", "b", "c"]
    assert _edge_pairs(graph) == [("a", "b"), ("a", "c"), ("b", "a")]
    assert graph["cyclic_sccs"] == [["a", "b"]]
    assert graph["cyclic_edges"] == [["a", "b"], ["b", "a"]]
    assert graph["source_file_count"] == 3
    assert graph["excluded_test_file_count"] == 1
    assert graph["edges"][0]["references"] == [
        {
            "line": 1,
            "module": "services.b.api",
            "path": "services/a/one.py",
        }
    ]


def test_graph_resolves_relative_cross_domain_and_ignores_same_domain(tmp_path):
    from services.governance.service_import_graph import build_service_import_graph

    _write(
        tmp_path,
        "services/a/one.py",
        "from .local import item\n"
        "from services.a.shared import other\n"
        "from ..b.shared import cross_domain\n",
    )
    _write(tmp_path, "services/a/local.py")
    _write(tmp_path, "services/a/shared.py")
    _write(tmp_path, "services/b/shared.py")

    graph = build_service_import_graph(tmp_path)

    assert graph["nodes"] == ["a", "b"]
    assert _edge_pairs(graph) == [("a", "b")]
    assert graph["cyclic_sccs"] == []


def test_graph_fails_closed_on_invalid_python(tmp_path):
    from services.governance.service_import_graph import (
        ImportGraphError,
        build_service_import_graph,
    )

    _write(tmp_path, "services/a/broken.py", "def broken(:\n")

    with pytest.raises(ImportGraphError, match="services/a/broken.py"):
        build_service_import_graph(tmp_path)


def test_graph_catches_top_level_from_and_baselines_dynamic_sites(tmp_path):
    from services.governance.service_import_graph import build_service_import_graph

    _write(
        tmp_path,
        "services/a/one.py",
        "from services import b\n"
        "importlib.import_module(target_module)\n",
    )
    _write(tmp_path, "services/b/two.py")

    graph = build_service_import_graph(tmp_path)

    assert _edge_pairs(graph) == [("a", "b")]
    assert graph["unresolved_dynamic_imports"] == [
        {
            "function": "importlib.import_module",
            "line": 2,
            "path": "services/a/one.py",
        }
    ]


def test_cycle_policy_must_exactly_match_current_cyclic_edges(tmp_path):
    from services.governance.service_import_graph import (
        ImportGraphPolicyError,
        build_service_import_graph,
        verify_cycle_policy,
    )

    _write(tmp_path, "services/a/one.py", "from services.b import item\n")
    _write(tmp_path, "services/b/two.py", "from services.a import item\n")
    graph = build_service_import_graph(tmp_path)
    policy = {
        "schema_version": 1,
        "known_cycle_edges": [["a", "b"], ["b", "a"]],
        "known_unresolved_dynamic_imports": [],
    }

    assert verify_cycle_policy(graph, policy)["status"] == "EXACT_BASELINE"

    with pytest.raises(ImportGraphPolicyError, match="unexpected cycle edges"):
        verify_cycle_policy(
            graph,
            {"schema_version": 1, "known_cycle_edges": [["a", "b"]]},
        )
    with pytest.raises(ImportGraphPolicyError, match="stale cycle permissions"):
        verify_cycle_policy(
            graph,
            {
                "schema_version": 1,
                "known_cycle_edges": [
                    ["a", "b"],
                    ["b", "a"],
                    ["b", "c"],
                ],
            },
        )

    graph["unresolved_dynamic_imports"] = [
        {"path": "services/a/one.py", "line": 7, "function": "import_module"}
    ]
    with pytest.raises(ImportGraphPolicyError, match="unexpected dynamic imports"):
        verify_cycle_policy(graph, policy)


def test_generator_is_deterministic_and_rejects_tampering_or_new_cycle(tmp_path):
    from scripts.generate_service_import_graph import main

    _write(tmp_path, "services/a/one.py", "from services.b import item\n")
    _write(tmp_path, "services/b/two.py", "from services.a import item\n")
    _write(tmp_path, "services/c/three.py", "VALUE = 1\n")
    policy_path = _write(
        tmp_path,
        "config/service_import_policy.json",
        json.dumps(
            {
                "schema_version": 1,
                "known_cycle_edges": [["a", "b"], ["b", "a"]],
            }
        ),
    )
    evidence_path = tmp_path / "evidence/service_import_graph.json"
    args = [
        "--repo-root",
        str(tmp_path),
        "--policy",
        str(policy_path),
        "--evidence",
        str(evidence_path),
    ]

    assert main(args) == 0
    first = evidence_path.read_bytes()
    assert main(args) == 0
    assert evidence_path.read_bytes() == first
    assert main([*args, "--check"]) == 0

    tampered = json.loads(evidence_path.read_text(encoding="utf-8"))
    tampered["edge_count"] += 1
    evidence_path.write_text(json.dumps(tampered), encoding="utf-8")
    assert main([*args, "--check"]) == 1

    assert main(args) == 0
    _write(tmp_path, "services/c/three.py", "from services.b import item\n")
    _write(tmp_path, "services/b/two.py", "from services.a import item\nfrom services.c import item\n")
    assert main(args) == 1


def test_tracked_service_import_graph_is_current():
    from scripts.generate_service_import_graph import main

    assert main(["--repo-root", str(ROOT), "--check"]) == 0


def test_unified_gate_checks_import_graph_before_top_tier_assessment():
    source = (ROOT / "scripts/validate.py").read_text(encoding="utf-8")
    marker = "service-import-graph:current"
    assert marker in source
    assert "generate_service_import_graph.py" in source
    assert source.index(marker) < source.index("top-tier-assessment-artifacts")


def test_top_tier_registry_requires_import_graph_gate_marker():
    registry = json.loads(
        (ROOT / "config/top_tier_requirements.json").read_text(encoding="utf-8")
    )
    requirement = next(
        item
        for item in registry["requirements"]
        if item["id"] == "PLATFORM.CI.UNIFIED_GATE"
    )
    assert "service-import-graph:current" in requirement["checker_args"]["needles"]
