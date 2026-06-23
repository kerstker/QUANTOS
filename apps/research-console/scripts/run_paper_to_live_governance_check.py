#!/usr/bin/env python
"""QUANTOS Phase 2H - Paper-to-Live Governance Scanner.

Scans the order/risk/credential/execution-bearing packages for forbidden
implementation items (real broker SDK imports, hardcoded credentials, live
order APIs, live-trading flags). Must pass before any broker connectivity.

Phase 0.5 hardening (see docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md, gate G0.5):
  * SCAN_DIRS is now anchored to the repository root (was a relative
    "services/trading" that, from repo root, scanned the EMPTY root tree and
    passed vacuously). It now covers every package that can touch
    orders/risk/credentials, including the real research-risk engine.
  * The credential check now flags credential names *assigned a non-empty,
    non-placeholder string literal* (i.e. an actual hardcoded secret) instead
    of any substring occurrence. This removes false positives on legitimate
    domain field names such as `account_id: str = ""` while still catching
    `password = "hunter2"` and `front_address = "tcp://..."`.

Detection rules (imports / APIs / flags) are otherwise unchanged.
"""
import sys, os, ast, re

FORBIDDEN_IMPORTS = [
    "ctp", "thosttraderapi", "thostmduserapi", "pyctp", "vnpy",
    "quickfix", "fix44", "fix50", "fix_session",
    "broker_sdk", "broker_api", "broker_gateway",
]

FORBIDDEN_CREDENTIALS = [
    "password", "auth_code", "investor_password", "broker_password",
    "front_address", "broker_id", "investor_id", "account_id",
]

FORBIDDEN_APIS = [
    "live_order", "real_order", "place_order", "send_order", "cancel_order",
    "broker_insert", "broker_cancel", "ctp_insert", "fix_send",
    "live_execution", "real_execution",
]

# Legacy literal flag strings (kept as defense-in-depth). The PRIMARY flag check
# is _LIVE_FLAG_RE below, which detects the REAL SafetyConfig arming flags — the
# previous list watched for names (LIVE_MODE, ...) that do not exist anywhere in
# this codebase while the genuine arming flags passed clean (audit P1).
FORBIDDEN_FLAGS = [
    "live_trading_allowed = true", "live_trading_allowed=true",
    "LIVE_MODE", "REAL_TRADING", "PRODUCTION_TRADING",
]

# The genuine SafetyConfig flags whose arming makes live_trading_armed True
# (quantos/contracts/config.py). Matched on original-case content so True/False
# are exact. Catches both `flag = True` and the keyword form `SafetyConfig(flag=True)`.
_LIVE_FLAGS = [
    "live_trading_enabled", "real_broker_enabled",
    "allow_order_submission", "allow_ai_direct_trading",
]
_LIVE_FLAG_RE = [re.compile(r"\b" + f + r"\s*=\s*True\b") for f in _LIVE_FLAGS]
_HUMAN_OFF_RE = re.compile(r"\brequire_human_approval\s*=\s*False\b")

# Forbidden order/execution APIs matched on WORD BOUNDARIES (+ optional plural),
# not bare substrings: this catches a real `place_order(` / renamed `live_order`
# without false-positiving on a benign field like `contains_live_orders` (audit P1/P2).
_API_RE = [re.compile(r"\b" + re.escape(fb) + r"s?\b") for fb in FORBIDDEN_APIS]

# Repository root, resolved relative to this file so the scan is independent of
# the current working directory (apps/research-console/scripts/ -> 3 levels up).
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

# Scan the ENTIRE Python order/risk/credential surface by package ROOT, not a
# hand-maintained list of granular sub-paths. The previous list omitted the only
# running service (services/research/console_api) and the real factor engine
# (services/research/factor_engine) because it named research's children but not
# the parent, so code added there passed the gate vacuously (audit P1). Rooting at
# services/ + quantos/ makes coverage exhaustive and self-maintaining.
SCAN_SUBDIRS = [
    "quantos",
    "services",
]

DEFAULT_SCAN_DIRS = [os.path.join(REPO_ROOT, d) for d in SCAN_SUBDIRS]

# A credential token assigned a string literal:  token = "x"  |  token: T = "x"
_ASSIGN_RE = {
    c: re.compile(r"\b" + re.escape(c) + r"\b\s*(?::[^=\n]*)?=\s*(['\"])(.*?)\1")
    for c in FORBIDDEN_CREDENTIALS
}
# A credential token as a mapping key:  "token": "x"
_MAP_RE = {
    c: re.compile(r"['\"]" + re.escape(c) + r"['\"]\s*:\s*(['\"])(.*?)\1")
    for c in FORBIDDEN_CREDENTIALS
}


def _is_real_secret(value: str) -> bool:
    """A captured literal is a real secret unless it is empty or an obvious
    placeholder/example token."""
    v = value.strip().lower()
    if not v:
        return False
    for marker in ("placeholder", "example", "dummy", "redacted", "xxx", "<", "{"):
        if marker in v:
            return False
    return True


def _credential_violations(fpath, content):
    out = []
    for c in FORBIDDEN_CREDENTIALS:
        for rx in (_ASSIGN_RE[c], _MAP_RE[c]):
            for m in rx.finditer(content):
                if _is_real_secret(m.group(2)):
                    out.append(f"{fpath}: '{c}' assigned a literal value")
                    break
    return out


def scan(scan_dirs=None):
    scan_dirs = DEFAULT_SCAN_DIRS if scan_dirs is None else scan_dirs
    violations = {"imports": [], "credentials": [], "apis": [], "flags": []}
    files_scanned = 0
    dirs_present = []
    for scan_dir in scan_dirs:
        if not os.path.isdir(scan_dir):
            continue
        dirs_present.append(scan_dir)
        for rt, ds, fs in os.walk(scan_dir):
            parts = rt.split(os.sep)
            if "tests" in parts or "__pycache__" in parts:
                continue
            for f in fs:
                if not f.endswith(".py"):
                    continue
                fpath = os.path.join(rt, f)
                files_scanned += 1
                try:
                    with open(fpath, encoding="utf-8") as fh:
                        content = fh.read()
                except Exception as e:
                    violations["imports"].append(f"{fpath}: read error ({e})")
                    continue
                # AST scan for forbidden imports
                try:
                    for node in ast.walk(ast.parse(content)):
                        mod = None
                        if isinstance(node, ast.Import):
                            for a in node.names:
                                mod = a.name
                        elif isinstance(node, ast.ImportFrom):
                            mod = node.module or ""
                        if mod:
                            for fb in FORBIDDEN_IMPORTS:
                                if fb in mod.lower():
                                    violations["imports"].append(f"{fpath}: imports '{mod}'")
                except SyntaxError:
                    pass
                # Hardcoded credentials (assigned secret literals only)
                violations["credentials"].extend(_credential_violations(fpath, content))
                # Forbidden live-order APIs (word-boundary, not bare substring)
                low = content.lower()
                for rx, fb in zip(_API_RE, FORBIDDEN_APIS):
                    if rx.search(low):
                        violations["apis"].append(f"{fpath}: contains '{fb}'")
                # Forbidden live-trading flags: the REAL arming flags set True,
                # require_human_approval set False, plus the legacy literals.
                for rx, f in zip(_LIVE_FLAG_RE, _LIVE_FLAGS):
                    if rx.search(content):
                        violations["flags"].append(f"{fpath}: arms '{f}=True'")
                if _HUMAN_OFF_RE.search(content):
                    violations["flags"].append(f"{fpath}: sets 'require_human_approval=False'")
                for fb in FORBIDDEN_FLAGS:
                    if fb.lower() in low:
                        violations["flags"].append(f"{fpath}: contains '{fb}'")

    total = sum(len(v) for v in violations.values())
    return {
        "passed": total == 0,
        "files_scanned": files_scanned,
        "dirs_scanned": dirs_present,
        "total_violations": total,
        "violations": violations,
    }


def main():
    print("=" * 60)
    print("QUANTOS Phase 2H - Paper-to-Live Governance Scanner")
    print("=" * 60)
    result = scan()
    print(f"Dirs scanned:  {len(result['dirs_scanned'])}")
    print(f"Files scanned: {result['files_scanned']}")
    print(f"Violations:    {result['total_violations']}")
    for cat, items in result["violations"].items():
        if items:
            print(f"  {cat}: {len(items)}")
            for item in items[:5]:
                print(f"    - {item}")
    if result["passed"]:
        print("\nSCANNER PASSED: No forbidden implementation items found.")
    else:
        print(f"\nSCANNER FAILED: {result['total_violations']} violations found.")
    print("=" * 60)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
