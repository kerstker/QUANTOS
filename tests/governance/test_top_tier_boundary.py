import ast
import json
import subprocess
import sys
from pathlib import Path

from scripts.assess_top_tier import main, render_matrix
from services.governance.top_tier_assessment import assess_repository


ROOT = Path(__file__).resolve().parents[2]
ASSESSOR_FILES = [
    ROOT / "services/governance/top_tier_assessment.py",
    ROOT / "scripts/assess_top_tier.py",
]


def test_cli_generates_stable_json_and_matrix(tmp_path):
    json_path = tmp_path / "assessment.json"
    matrix_path = tmp_path / "matrix.md"
    args = ["--repo-root", str(ROOT), "--json-out", str(json_path), "--matrix-out", str(matrix_path)]
    assert main(args) == 0
    first_json = json_path.read_bytes()
    first_matrix = matrix_path.read_bytes()
    assert main(args) == 0
    assert json_path.read_bytes() == first_json
    assert matrix_path.read_bytes() == first_matrix
    payload = json.loads(first_json)
    assert payload["schema_version"] == 1
    assert payload["verdict"] == "NOT_TOP_TIER"
    assert "| Requirement |" in first_matrix.decode("utf-8")


def test_matrix_is_derived_from_assessment_not_existing_file():
    report = assess_repository(ROOT, ROOT / "config/top_tier_requirements.json")
    rendered = render_matrix(report)
    assert report["requirements"][0]["id"] in rendered
    assert "NOT_TOP_TIER" in rendered


def test_assessor_has_no_network_secret_or_order_surface():
    forbidden_imports = {"socket", "urllib", "http", "requests", "httpx", "aiohttp", "websocket", "websockets"}
    forbidden_calls = {"place_order", "submit_order", "withdraw", "transfer"}
    forbidden_text = {"api_key", "api_secret", "live_ops_token", "governance_token", "research_runner_token"}
    for path in ASSESSOR_FILES:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        roots = set()
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)
        assert not roots & forbidden_imports
        assert not calls & forbidden_calls
        assert not any(marker in source.lower() for marker in forbidden_text)


def test_current_red_lines_keep_system_fail_closed():
    report = assess_repository(ROOT, ROOT / "config/top_tier_requirements.json")
    assert report["summary"]["red_lines_failed"] > 0
    assert report["verdict"] == "NOT_TOP_TIER"


def test_documented_cli_command_runs_from_repo_root(tmp_path):
    proc = subprocess.run(
        [sys.executable, "scripts/assess_top_tier.py", "--json-out", str(tmp_path / "a.json"),
         "--matrix-out", str(tmp_path / "m.md")],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_check_mode_rejects_hand_edited_artifact(tmp_path):
    json_path, matrix_path = tmp_path / "a.json", tmp_path / "m.md"
    args = ["--repo-root", str(ROOT), "--json-out", str(json_path), "--matrix-out", str(matrix_path)]
    assert main(args) == 0
    json_path.write_text("{}\n", encoding="utf-8")
    assert main([*args, "--check"]) == 1
