"""Machine-enforced L2 boundary for the authority-bound forward evidence path."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def imported_roots(*paths: Path) -> set[str]:
    roots: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_forward_services_are_offline_and_l2_only():
    forbidden = {"requests", "httpx", "urllib", "socket", "websockets"}
    imports = imported_roots(
        ROOT / "services/research/paper_trading/forward.py",
        ROOT / "services/research/paper_trading/forward_store.py",
        ROOT / "services/research/paper_trading/forward_evidence.py",
    )
    assert imports.isdisjoint(forbidden)


def test_official_forward_path_cannot_count_legacy_or_boolean_admission():
    driver = _source("tools/forward_track_advance.py")
    status = _source("tools/forward_evidence_status.py")

    assert "attested_realizations" in driver
    assert "legacy_unattested_decisions" in status
    assert "LIVE_TRADING_ENABLED" not in driver
    assert '"quality_evidence_" + "current"' not in driver
    assert "quality_evidence_current=admission_ref" not in driver
    assert "quality_evidence_current=refresh_evidence_available" not in driver
    assert "isinstance(admission_ref, ForwardAdmissionRef)" in driver
    assert "refresh_evidence_available is not True" in driver
