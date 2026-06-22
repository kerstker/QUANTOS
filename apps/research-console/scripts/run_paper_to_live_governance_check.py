#!/usr/bin/env python
"""QUANTOS Phase 2H 鈥?Paper-to-Live Governance Scanner
Scans for forbidden implementation items. Must pass before Phase 3A.
"""
import sys, os, ast, json, time

FORBIDDEN_IMPORTS = [
    "ctp","thosttraderapi","thostmduserapi","pyctp","vnpy",
    "quickfix","fix44","fix50","fix_session",
    "broker_sdk","broker_api","broker_gateway",
]

FORBIDDEN_CREDENTIALS = [
    "password","auth_code","investor_password","broker_password",
    "front_address","broker_id","investor_id","account_id",
]

FORBIDDEN_APIS = [
    "live_order","real_order","place_order","send_order","cancel_order",
    "broker_insert","broker_cancel","ctp_insert","fix_send",
    "live_execution","real_execution",
]

FORBIDDEN_FLAGS = [
    "live_trading_allowed = true","live_trading_allowed=true",
    "LIVE_MODE","REAL_TRADING","PRODUCTION_TRADING",
]

SCAN_DIRS = [
    "services/trading",
]

def scan():
    violations = {"imports":[],"credentials":[],"apis":[],"flags":[]}
    files_scanned = 0
    for scan_dir in SCAN_DIRS:
        if not os.path.exists(scan_dir): continue
        for rt, ds, fs in os.walk(scan_dir):
            if "tests" in rt.split(os.sep) or "__pycache__" in rt.split(os.sep):
                continue
            for f in fs:
                if not f.endswith('.py'): continue
                fpath = os.path.join(rt, f); files_scanned += 1
                try:
                    with open(fpath, encoding="utf-8") as fh:
                        content = fh.read()
                    # AST scan for imports
                    try:
                        for node in ast.walk(ast.parse(content)):
                            mod = None
                            if isinstance(node, ast.Import):
                                for a in node.names: mod = a.name
                            elif isinstance(node, ast.ImportFrom): mod = node.module or ""
                            if mod:
                                for fb in FORBIDDEN_IMPORTS:
                                    if fb in mod.lower():
                                        violations["imports"].append(f"{fpath}: imports '{mod}'")
                    except: pass
                    # Content scan for credentials (non-placeholder)
                    for fb in FORBIDDEN_CREDENTIALS:
                        if fb in content.lower():
                            # Allow if _placeholder suffix is present nearby
                            idx = content.lower().find(fb)
                            context = content[max(0,idx-20):idx+len(fb)+20].lower()
                            if "_placeholder" not in context:
                                violations["credentials"].append(f"{fpath}: contains '{fb}'")
                    # Content scan for forbidden APIs
                    for fb in FORBIDDEN_APIS:
                        if fb in content.lower():
                            violations["apis"].append(f"{fpath}: contains '{fb}'")
                    # Content scan for forbidden flags
                    for fb in FORBIDDEN_FLAGS:
                        if fb.lower() in content.lower():
                            violations["flags"].append(f"{fpath}: contains '{fb}'")
                except Exception as e:
                    violations["imports"].append(f"{fpath}: parse error ({e})")

    total = sum(len(v) for v in violations.values())
    passed = total == 0
    return {"passed": passed, "files_scanned": files_scanned, "total_violations": total,
            "violations": violations}

if __name__ == "__main__":
    print("="*60)
    print("QUANTOS Phase 2H 鈥?Paper-to-Live Governance Scanner")
    print("="*60)
    result = scan()
    print(f"Files scanned: {result['files_scanned']}")
    print(f"Violations found: {result['total_violations']}")
    for cat, items in result["violations"].items():
        if items:
            print(f"  {cat}: {len(items)}")
            for item in items[:5]: print(f"    - {item}")
    if result["passed"]:
        print("\nSCANNER PASSED: No forbidden implementation items found.")
    else:
        print(f"\nSCANNER FAILED: {result['total_violations']} violations found.")
    print("="*60)
    sys.exit(0 if result["passed"] else 1)
