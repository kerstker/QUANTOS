#!/usr/bin/env python
"""QUANTOS Phase 2G.1 鈥?Dry-Run Adapter Integration Gate Runner"""
import sys,os,json,time,argparse
sys.path.insert(0,os.path.join(os.path.dirname(__file__),'..'))
import pytest
GATE_TESTS=[("tests/integration/test_dry_run_all.py","1-65. Dry-Run Adapters Complete Integration Suite")]
def run_gate(verbose=False):
    results={};start=time.time()
    for tf,desc in GATE_TESTS:
        t0=time.time()
        try:ec=pytest.main([tf,"-q","--tb=short","--no-header","-p","no:warnings"]);passed=ec==pytest.ExitCode.OK
        except:passed=False
        d=time.time()-t0;results[tf]={"description":desc,"passed":passed,"duration_seconds":round(d,2)}
        print(f"  [{'PASS' if passed else 'FAIL'}] {desc} ({d:.1f}s)")
    total=time.time()-start;ap=all(r["passed"]for r in results.values())
    return{"gate_name":"Phase 2G.1 鈥?Dry-Run Adapter Integration Gate","timestamp":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"all_passed":ap,"total_tests":len(GATE_TESTS),"passed_count":sum(1 for r in results.values()if r["passed"]),"failed_count":sum(1 for r in results.values()if not r["passed"]),"total_duration_seconds":round(total,2)}
def main():
    p=argparse.ArgumentParser();p.add_argument("-v","--verbose",action="store_true")
    args=p.parse_args()
    print("="*60);print("QUANTOS Phase 2G.1 鈥?Dry-Run Adapter Integration Gate");print("="*60)
    report=run_gate(args.verbose);print(f"\n{'='*60}")
    if report["all_passed"]:print(f"GATE PASSED")
    else:print(f"GATE FAILED")
    return 0 if report["all_passed"] else 1
if __name__=="__main__":sys.exit(main())
