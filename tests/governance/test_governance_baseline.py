"""QUANTOS Phase 0.5 governance baseline tests.

These tests encode the safety invariants of the repository as executable,
fail-closed checks:
  * the paper-to-live governance scanner actually covers the safety-critical
    package allow-list (not a vacuous empty-tree pass),
  * it catches planted forbidden items (positive controls), and
  * it does not false-positive on legitimate domain field names / placeholders,
  * the current real code is clean (no live trading / broker SDK / secrets),
  * the broken pre_trade_risk module is quarantined.
"""
import importlib.util
import os

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCANNER_PATH = os.path.join(
    REPO, "apps", "research-console", "scripts", "run_paper_to_live_governance_check.py"
)


def _load_scanner():
    spec = importlib.util.spec_from_file_location("quantos_gov_scanner", SCANNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SCANNER = _load_scanner()


# --- coverage: the scanner must scan the real safety-critical trees ----------

def _covered(path):
    """True if `path` is inside one of the scanner's roots."""
    roots = [os.path.normcase(os.path.abspath(d)) for d in SCANNER.DEFAULT_SCAN_DIRS]
    p = os.path.normcase(os.path.abspath(path))
    return any(p == r or p.startswith(r + os.sep) for r in roots)


def test_scanner_covers_safety_critical_trees():
    """The scan roots MUST cover every order/risk/credential tree — including the
    only running service (console_api) and the real factor engine, which the
    previous granular allow-list silently omitted (audit P1)."""
    for r in ("services/risk", "services/research/paper_trading", "services/trading",
              "services/console/api", "services/research/factor_engine",
              "quantos/contracts"):
        assert _covered(os.path.join(REPO, *r.split("/"))), f"not in scan scope: {r}"


def test_scanner_covers_every_python_source_under_services():
    """Meta-test: no non-test Python source under services/ escapes the scan roots,
    so coverage cannot silently regress when new packages are added."""
    import glob
    missed = [
        f for f in glob.glob(os.path.join(REPO, "services", "**", "*.py"), recursive=True)
        if "tests" not in f.split(os.sep) and "__pycache__" not in f and not _covered(f)
    ]
    assert not missed, missed


def test_scanner_actually_scans_real_files():
    """files_scanned must reflect the real code, not a single trivial file."""
    result = SCANNER.scan()
    assert result["files_scanned"] >= 30, result   # whole services/ + quantos/ tree


def test_current_repo_is_clean():
    """The real code today must pass the governance scan."""
    result = SCANNER.scan()
    assert result["passed"], result["violations"]


# --- positive controls: planted forbidden items MUST be caught ---------------

def test_positive_control_hardcoded_secret(tmp_path):
    (tmp_path / "leak.py").write_text('password = "hunter2real"\n', encoding="utf-8")
    result = SCANNER.scan([str(tmp_path)])
    assert not result["passed"]
    assert result["violations"]["credentials"], result


def test_positive_control_forbidden_import(tmp_path):
    (tmp_path / "bad.py").write_text("import ctp\n", encoding="utf-8")
    result = SCANNER.scan([str(tmp_path)])
    assert not result["passed"]
    assert result["violations"]["imports"], result


def test_positive_control_live_flag(tmp_path):
    (tmp_path / "flag.py").write_text("live_trading_allowed = True\n", encoding="utf-8")
    result = SCANNER.scan([str(tmp_path)])
    assert not result["passed"]
    assert result["violations"]["flags"], result


def test_positive_control_live_order_api(tmp_path):
    (tmp_path / "api.py").write_text("def place_order(x):\n    return x\n", encoding="utf-8")
    result = SCANNER.scan([str(tmp_path)])
    assert not result["passed"]
    assert result["violations"]["apis"], result


def test_positive_control_real_arming_flag(tmp_path):
    # the genuine SafetyConfig arming flag set True must be caught (audit P1) —
    # the old FORBIDDEN_FLAGS list watched only for names that never existed.
    (tmp_path / "arm.py").write_text(
        "cfg = SafetyConfig(live_trading_enabled=True, real_broker_enabled=True)\n", encoding="utf-8")
    result = SCANNER.scan([str(tmp_path)])
    assert not result["passed"] and result["violations"]["flags"], result


def test_positive_control_human_approval_disabled(tmp_path):
    (tmp_path / "arm2.py").write_text("cfg = SafetyConfig(require_human_approval=False)\n", encoding="utf-8")
    result = SCANNER.scan([str(tmp_path)])
    assert not result["passed"] and result["violations"]["flags"], result


# --- no false positives on legitimate domain code ----------------------------

def test_no_false_positive_on_field_names_and_placeholders(tmp_path):
    (tmp_path / "ok.py").write_text(
        'account_id: str = ""\n'
        "broker_id_placeholder = 'broker_id_placeholder'\n"
        'investor = "investor_id_placeholder"\n'
        'status: str = "CREATED"\n',
        encoding="utf-8",
    )
    result = SCANNER.scan([str(tmp_path)])
    assert result["passed"], result["violations"]


def test_no_false_positive_on_benign_live_order_field(tmp_path):
    # word-boundary matching must NOT flag a benign field whose name merely
    # contains 'live_order' as a substring (audit P2: contains_live_orders).
    (tmp_path / "ok2.py").write_text(
        "contains_live_orders: bool = False\nhas_live_order_flag = False\n", encoding="utf-8")
    result = SCANNER.scan([str(tmp_path)])
    assert result["passed"], result["violations"]


# --- quarantine of the broken pre-trade risk module --------------------------

def test_pre_trade_risk_is_quarantined():
    main = os.path.join(REPO, "services", "trading", "pre_trade_risk", "main.py")
    content = open(main, encoding="utf-8").read()
    assert "QUANTOS_QUARANTINED_NON_FUNCTIONAL" in content, "quarantine marker missing"
