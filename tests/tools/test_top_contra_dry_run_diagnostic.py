"""The third-alpha dry-run path must remain visibly non-authoritative."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from services.execution.backtest_authority import (
    BacktestIntegrityBlocked,
    LEGACY_INVALIDATION_CODE,
)
from tools import top_contra_dry_run_diagnostic as diagnostic


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


def test_top_diagnostic_main_quarantines_before_data_read_or_artifact_write(
    monkeypatch, tmp_path
):
    output = tmp_path / "top_contra_dry_run_diagnostic.json"
    monkeypatch.setattr(diagnostic, "OUTPUT", str(output))
    monkeypatch.setattr(
        diagnostic,
        "build_panel",
        lambda: pytest.fail("legacy history was read before quarantine"),
    )
    monkeypatch.setattr(
        diagnostic,
        "atomic_write_json",
        lambda *_args, **_kwargs: pytest.fail("artifact write bypassed quarantine"),
    )

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        diagnostic.main()

    assert not output.exists()


def test_top_diagnostic_report_is_not_official_paper_evidence():
    if not REPORT.exists():
        return
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["authoritative"] is False
    assert report["evidence_class"] == "EXECUTION_DIAGNOSTIC_ONLY"
    assert report["persistence"] == "NO_CANONICAL_EVENT_OR_DRY_RUN_STORE_WRITE"
    assert "NOT_FORWARD_PAPER_EVIDENCE" in report["blockers"]
