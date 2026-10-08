from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "tools" / "crypto_oi_top_verify.py"
REPORT = ROOT / "data" / "cache" / "crypto_oi_top_verify.json"


def test_top_refutation_driver_has_no_network_or_order_imports():
    tree = ast.parse(DRIVER.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported.isdisjoint(
        {"urllib", "requests", "httpx", "ccxt", "binance", "socket"}
    )


@pytest.mark.skipif(
    not REPORT.exists(),
    reason="optional local research artifact is outside the tracked checkout",
)
def test_top_refutation_report_is_diagnostic_and_machine_readable():
    assert REPORT.exists()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["evidence_class"] == "RESEARCH_REFUTATION_DIAGNOSTIC"
    assert set(report["refutation_checks"]) == {
        "survives_1d_lag",
        "survives_10bps_cost",
        "broad_across_years",
        "survives_subsets",
        "adds_to_carry",
    }
    assert 0 <= report["n_pass"] <= 5
    assert report["verdict"].startswith(("SURVIVES", "PARTIAL", "FAILS"))
