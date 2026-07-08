#!/usr/bin/env python
"""QUANTOS fail-closed validation entrypoint (Phase 0.5).

Single command CI and humans run. Orchestrates, in order:
  1. the paper-to-live governance scanner (no broker SDK / secrets / live APIs),
  2. the research-side pytest suites (run from the repo root), and
  3. the trading-side pytest suites (run from apps/research-console, whose own
     `services` package tree differs from the repo-root one).

Each suite group is run as a separate subprocess with the correct working
directory, because the two `services` package trees collide in one process
(a structural duplication tracked for Phase 1). Internal pytest is invoked
directly (not via the shell), so it is unaffected by any shell command hooks.

Exit code is non-zero if ANY check fails (fail-closed).
"""
import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APPS = os.path.join(REPO, "apps", "research-console")
SCANNER = os.path.join(APPS, "scripts", "run_paper_to_live_governance_check.py")

# (name, argv, cwd)
# Since Phase 1 unified the two service trees under the repo root, all Python
# test suites resolve from a single rootdir and run in one pytest invocation.
CHECKS = [
    (
        "governance-scanner",
        [sys.executable, SCANNER],
        REPO,
    ),
    (
        "pytest: all suites (contracts, governance, research, trading)",
        [
            sys.executable, "-m", "pytest", "-q",
            "tests/contracts",
            "tests/data",
            "tests/features",
            "tests/research",
            "tests/alpha",
            "tests/portfolio",
            "tests/risk",
            "tests/execution",
            "tests/intelligence",
            "tests/monitoring",
            "tests/deployment",
            "tests/governance",
            # 2026-07-07:证据摘要漂移已纠正(crowd_contra t_stat 样本延长软化,tools.build_evidence_digests
            # 重生成),该 reproducibility 门现纳入 CI——本地有 data/cache/ artifact 时校验数字不漂移,
            # CI 无 artifact 时诚实 skip(绝不当作 passed)。
            "tests/reproducibility",
            "services/research/console_api/tests/test_console_api.py",
            "services/research/factor_engine/tests/test_model_compile.py",
            "services/trading/dry_run_adapters/tests/test_dry_run.py",
            "tests/integration/test_dry_run_all.py",
        ],
        REPO,
    ),
    (
        # 2026-07-06 审计发现并修复:live 执行栈(tests/live,36 文件——最安全关键的下单/风控/
        # 对账代码)+ trading + tools 此前从未进入这个 fail-closed 门禁,回归不会被 CI 抓到。三者
        # 离线、快、全绿(527 tests)。作为独立子进程运行,是因为 tests/live 与 tests/research 存在
        # 同名测试文件(test_pipeline.py / test_contracts.py 等),在同一 pytest 进程里会 basename
        # 冲突收集失败——正是本文件顶部 docstring 描述的"每组独立子进程"模式。
        "pytest: live execution stack (live, trading, tools)",
        [
            sys.executable, "-m", "pytest", "-q",
            "tests/live",
            "tests/trading",
            "tests/tools",
        ],
        REPO,
    ),
]


def run():
    results = []
    for name, argv, cwd in CHECKS:
        print(f"\n{'=' * 70}\n[validate] {name}\n  cwd: {cwd}\n{'=' * 70}")
        proc = subprocess.run(argv, cwd=cwd)
        results.append((name, proc.returncode == 0))

    print(f"\n{'=' * 70}\n[validate] SUMMARY\n{'=' * 70}")
    all_ok = True
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        all_ok = all_ok and ok
    print("=" * 70)
    if all_ok:
        print("VALIDATE PASSED (fail-closed gate green).")
        return 0
    print("VALIDATE FAILED.")
    return 1


if __name__ == "__main__":
    sys.exit(run())
