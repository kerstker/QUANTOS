"""The third-alpha dry-run path must remain visibly non-authoritative."""
from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "tools" / "top_contra_dry_run_diagnostic.py"
REPORT = ROOT / "data" / "cache" / "top_contra_dry_run_diagnostic.json"


def test_top_diagnostic_does_not_import_canonical_event_or_store_writers():
    tree = ast.parse(DRIVER.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    forbidden = {"open_domain_event_bus", "save_and_project_dry_run", "DryRunStore"}
    assert imported.isdisjoint(forbidden)


def test_top_diagnostic_report_is_not_official_paper_evidence():
    if not REPORT.exists():
        return
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["authoritative"] is False
    assert report["evidence_class"] == "EXECUTION_DIAGNOSTIC_ONLY"
    assert report["persistence"] == "NO_CANONICAL_EVENT_OR_DRY_RUN_STORE_WRITE"
    assert "NOT_FORWARD_PAPER_EVIDENCE" in report["blockers"]
