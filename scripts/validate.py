#!/usr/bin/env python
"""QUANTOS fail-closed validation entrypoint (Phase 0.5).

Single command CI and humans run. Orchestrates, in order:
  1. generated evidence and architecture drift checks,
  2. a fresh isolated risk-state recovery drill,
  3. the paper-to-live governance scanner,
  4. backend and live-stack pytest suites, and
  5. frontend typecheck, tests, and production build.

Each suite group is run as a separate subprocess with the correct working
directory, because the two `services` package trees collide in one process
(a structural duplication tracked for Phase 1). Internal pytest is invoked
directly (not via the shell), so it is unaffected by any shell command hooks.

Exit code is non-zero if ANY check fails (fail-closed).
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APPS = os.path.join(REPO, "apps", "research-console")
SCANNER = os.path.join(APPS, "scripts", "run_paper_to_live_governance_check.py")
NPM = shutil.which("npm") or "npm"

# (name, argv, cwd)
# Since Phase 1 unified the two service trees under the repo root, all Python
# test suites resolve from a single rootdir and run in one pytest invocation.
CHECKS = [
    (
        "python-audit-evidence:current",
        [
            sys.executable,
            os.path.join(REPO, "scripts", "generate_python_audit_evidence.py"),
            "--check",
        ],
        REPO,
    ),
    (
        "npm-audit-evidence:current",
        [
            sys.executable,
            os.path.join(REPO, "scripts", "generate_npm_audit_evidence.py"),
            "--check",
        ],
        REPO,
    ),
    (
        "actions-osv-evidence:current",
        [
            sys.executable,
            os.path.join(REPO, "scripts", "generate_actions_audit_evidence.py"),
            "--check",
        ],
        REPO,
    ),
    (
        "container-trivy-evidence:current",
        [
            sys.executable,
            os.path.join(REPO, "scripts", "generate_container_audit_evidence.py"),
            "--check",
        ],
        REPO,
    ),
    (
        "container-reproducibility-evidence:current",
        [
            sys.executable,
            os.path.join(
                REPO,
                "scripts",
                "generate_container_reproducibility_evidence.py",
            ),
            "--check",
        ],
        REPO,
    ),
    (
        "event-schema-catalog:current",
        [
            sys.executable,
            os.path.join(REPO, "scripts", "generate_event_schema_catalog.py"),
            "--check",
        ],
        REPO,
    ),
    (
        "service-import-graph:current",
        [
            sys.executable,
            os.path.join(REPO, "scripts", "generate_service_import_graph.py"),
            "--check",
        ],
        REPO,
    ),
    (
        "supply-chain-baseline:current",
        [
            sys.executable,
            os.path.join(REPO, "scripts", "generate_supply_chain_evidence.py"),
            "--check",
        ],
        REPO,
    ),
    (
        "model-risk-history:current",
        [
            sys.executable,
            os.path.join(REPO, "scripts", "update_model_risk_history.py"),
            "--check",
        ],
        REPO,
    ),
    (
        "crowding-calibration-evidence:current",
        [
            sys.executable,
            "-m",
            "tools.crowding_metric_research",
            "--check",
        ],
        REPO,
    ),
    (
        "top-tier-assessment-artifacts",
        [sys.executable, os.path.join(REPO, "scripts", "assess_top_tier.py"), "--check"],
        REPO,
    ),
    (
        "backup-evidence:current",
        [
            sys.executable,
            os.path.join(REPO, "scripts", "check_backup_evidence.py"),
            "--check",
        ],
        REPO,
    ),
    (
        "risk-recovery-drill:fresh",
        [sys.executable, os.path.join(REPO, "scripts", "run_risk_recovery_gate.py")],
        REPO,
    ),
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
            # 2026-07-27:三个正交状态机(727 §3)。新加的测试目录必须同时进这张表,
            # 否则它只在手动调用时跑,门禁抓不到回归。
            "tests/lifecycle",
            # 2026-07-27:自治内核 K1(727 §2.5/§4.2 Phase 2,command bus + 幂等命令日志)。
            "tests/orchestration",
            # 2026-07-27:Factor Garden F1(727 §6/Phase 4,因子契约加宽 + 注册表边界)。
            "tests/factor_garden",
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
            "services/console/api/tests/test_console_api.py",
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
    (
        "frontend: typecheck",
        [NPM, "run", "typecheck"],
        APPS,
    ),
    (
        "frontend: eslint",
        [NPM, "run", "eslint"],
        APPS,
    ),
    (
        "frontend: tests",
        [NPM, "test"],
        APPS,
    ),
    (
        "frontend: production build",
        [NPM, "run", "build"],
        APPS,
    ),
]


DEFAULT_TIMEOUT_SECONDS = 1_800.0
DEFAULT_REPORT = os.path.join(REPO, "evidence", "validation_report.json")


def _sha256_file(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO,
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"
    commit = proc.stdout.strip()
    return commit if proc.returncode == 0 and commit else "UNKNOWN"


def _checks_manifest_hash(checks) -> str:
    manifest = [
        {
            "name": name,
            "argv": [str(item) for item in argv],
            "cwd": os.path.abspath(cwd),
        }
        for name, argv, cwd in checks
    ]
    encoded = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _select_checks(stages, checks):
    """Resolve exact names, prefixes, and a few stable operator aliases."""
    checks = list(checks)
    if not stages or "all" in stages:
        if stages and any(stage != "all" for stage in stages):
            raise ValueError("--stage all cannot be combined with another stage")
        return checks

    names = [name for name, _, _ in checks]
    selected = []
    for requested in stages:
        if requested == "evidence":
            matches = [
                check for check in checks
                if check[0].endswith(":current")
                or check[0] == "top-tier-assessment-artifacts"
            ]
        elif requested == "backend":
            matches = [check for check in checks if check[0].startswith("pytest: all suites")]
        elif requested == "live":
            matches = [check for check in checks if check[0].startswith("pytest: live execution")]
        elif requested == "frontend":
            matches = [check for check in checks if check[0].startswith("frontend:")]
        elif requested == "phase0":
            matches = [
                check for check in checks
                if check[0] in {
                    "top-tier-assessment-artifacts",
                    "backup-evidence:current",
                    "governance-scanner",
                }
            ]
        elif requested == "governance":
            matches = [check for check in checks if check[0] == "governance-scanner"]
        else:
            matches = [check for check in checks if check[0] == requested]
            if not matches:
                matches = [
                    check for check in checks
                    if check[0].startswith(requested + ":")
                ]
        if not matches:
            available = ", ".join(names)
            raise ValueError(
                f"unknown validation stage {requested!r}; available stages: {available}"
            )
        for check in matches:
            if check not in selected:
                selected.append(check)
    return [check for check in checks if check in selected]


def _write_report(path: str, report: dict) -> None:
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".validation-report-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _run_check(name: str, argv, cwd: str, timeout_seconds: float) -> dict:
    started = time.monotonic()
    command = [str(item) for item in argv]
    absolute_cwd = os.path.abspath(cwd)
    print(
        f"\n{'=' * 70}\n[validate] START {name}\n"
        f"  cwd: {absolute_cwd}\n  timeout: {timeout_seconds:.1f}s\n"
        f"  command: {subprocess.list2cmdline(command)}\n{'=' * 70}",
        flush=True,
    )
    process = None
    status = "ERROR"
    returncode = None
    detail = None
    try:
        process = subprocess.Popen(command, cwd=absolute_cwd)
        try:
            returncode = process.wait(timeout=timeout_seconds)
            status = "PASS" if returncode == 0 else "FAIL"
        except subprocess.TimeoutExpired:
            detail = f"stage exceeded {timeout_seconds:.1f}s"
            try:
                process.kill()
            finally:
                process.wait()
            status = "TIMEOUT"
    except OSError as exc:
        detail = f"{type(exc).__name__}: {exc}"

    duration_ms = int((time.monotonic() - started) * 1000)
    result = {
        "stage": name,
        "status": status,
        "returncode": returncode,
        "duration_ms": duration_ms,
        "timeout_seconds": timeout_seconds,
        "command": command,
        "cwd": absolute_cwd,
    }
    if detail:
        result["detail"] = detail
    if status == "TIMEOUT" and process is not None:
        result["stuck_process"] = {
            "pid": process.pid,
            "stage": name,
            "command": command,
            "cwd": absolute_cwd,
        }
    print(
        f"[validate] END {name}: {status} ({duration_ms}ms)"
        + (f" — {detail}" if detail else ""),
        flush=True,
    )
    return result


def run(*, stages=None, json_report: str = DEFAULT_REPORT,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS, checks=None) -> int:
    """Run selected checks with live progress and a fail-closed machine report."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    checks = list(checks or CHECKS)
    selected = _select_checks(stages, checks)
    started_at_ms = int(time.time() * 1000)
    started = time.monotonic()
    results = [
        _run_check(name, argv, cwd, timeout_seconds)
        for name, argv, cwd in selected
    ]
    finished_at_ms = int(time.time() * 1000)
    summary = {
        "selected": len(results),
        "passed": sum(result["status"] == "PASS" for result in results),
        "failed": sum(result["status"] == "FAIL" for result in results),
        "timed_out": sum(result["status"] == "TIMEOUT" for result in results),
        "errored": sum(result["status"] == "ERROR" for result in results),
    }
    overall_status = (
        "PASS"
        if summary["selected"] and summary["passed"] == summary["selected"]
        else "FAIL"
    )
    selected_names = [name for name, _, _ in selected]
    report = {
        "schema_version": 1,
        "report_kind": "FAIL_CLOSED_VALIDATION",
        "generated_at_ms": finished_at_ms,
        "started_at_ms": started_at_ms,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "commit": _git_commit(),
        "readiness": "L2_DRY_RUN_ONLY",
        "selection_scope": "ALL" if not stages or stages == ["all"] else "SUBSET",
        "requested_stages": list(stages or ["all"]),
        "selected_stages": selected_names,
        "timeout_seconds": timeout_seconds,
        "checks_manifest_sha256": _checks_manifest_hash(checks),
        "validate_script_sha256": _sha256_file(__file__),
        "summary": summary,
        "overall_status": overall_status,
        "results": results,
    }
    try:
        _write_report(json_report, report)
        print(f"[validate] report: {os.path.abspath(json_report)}", flush=True)
    except OSError as exc:
        print(
            f"[validate] ERROR cannot write validation report: {type(exc).__name__}: {exc}",
            flush=True,
        )
        overall_status = "FAIL"

    print(f"\n{'=' * 70}\n[validate] SUMMARY\n{'=' * 70}")
    for result in results:
        print(f"  {result['status']:<7} {result['stage']}")
    print("=" * 70)
    if overall_status == "PASS":
        print("VALIDATE PASSED (fail-closed gate green).")
        return 0
    print("VALIDATE FAILED.")
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        action="append",
        dest="stages",
        help=(
            "run one or more checks (repeatable); accepts an exact name, prefix, "
            "or alias: all, evidence, backend, live, frontend, phase0, governance"
        ),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="per-stage wall-clock budget (default: 1800 seconds)",
    )
    parser.add_argument(
        "--json-report",
        default=DEFAULT_REPORT,
        help="machine-readable report path (default: evidence/validation_report.json)",
    )
    args = parser.parse_args(argv)
    try:
        return run(
            stages=args.stages,
            json_report=args.json_report,
            timeout_seconds=args.timeout_seconds,
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
