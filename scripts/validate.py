#!/usr/bin/env python
"""QUANTOS fail-closed validation entrypoint (Phase 0.5).

Single command CI and humans run. Orchestrates, in order:
  1. non-derived evidence, authority, recovery, and governance prerequisites,
  2. supply-chain then top-tier derived-evidence refresh + readback checks,
  3. backend and live-stack pytest suites,
  4. frontend typecheck, tests, and production build,
  5. primary report publication, and
  6. Phase 13 refresh + readback against that report.

Each suite group is run as a separate subprocess with the correct working
directory, because the two `services` package trees collide in one process
(a structural duplication tracked for Phase 1). Internal pytest is invoked
directly (not via the shell), so it is unaffected by any shell command hooks.

Exit code is non-zero if ANY check fails (fail-closed).
Derived evidence is rewritten only by a full canonical run and only after all
non-derived prerequisites pass. Subset runs remain read-only.
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
import xml.etree.ElementTree

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
try:
    sys.path.remove(REPO)
except ValueError:
    pass
sys.path.insert(0, REPO)

from scripts.evidence_binding import (  # noqa: E402
    resolve_canonical_source_anchor,
    validation_binding,
    write_release_evidence_manifest,
)

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
        "container-trivy-arm64-evidence:current",
        [
            sys.executable,
            os.path.join(
                REPO,
                "scripts",
                "generate_arm64_container_audit_evidence.py",
            ),
            "--check",
        ],
        REPO,
    ),
    (
        "container-reproducibility-arm64-evidence:current",
        [
            sys.executable,
            os.path.join(
                REPO,
                "scripts",
                "generate_arm64_container_reproducibility_evidence.py",
            ),
            "--check",
        ],
        REPO,
    ),
    (
        "console-smoke-arm64-evidence:current",
        [
            sys.executable,
            os.path.join(
                REPO,
                "scripts",
                "generate_arm64_console_smoke_evidence.py",
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
        "artifact-admission-authority:provision",
        [
            sys.executable,
            os.path.join(
                REPO,
                "tools",
                "provision_artifact_admission_authority.py",
            ),
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
        "feature-distribution-drift-evidence:current",
        [
            sys.executable,
            "-m",
            "tools.feature_distribution_drift_research",
            "--check",
        ],
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
        # 2026-09-20 审计 P1-9:.importlinter 有契约却**从来没有 runner**,
        # 所以 research→alpha 的 29 条违规、以及 tools/ 完全不在依赖图里,
        # 一直是文件里的口头约定。这里把它接进门禁。
        # 必须用 `-c` 形式调用:`python -m importlinter.cli` 会以 0 退出且
        # **完全没有输出**(该模块没有 __main__ 守卫),那正是我们要消灭的
        # 静默假绿。已验证:契约破裂时该命令退出 1。
        "import-graph-contracts",
        [
            sys.executable,
            "-c",
            "from importlinter.cli import lint_imports_command; lint_imports_command()",
        ],
        REPO,
    ),
    (
        "pytest: all suites (contracts, governance, research, trading)",
        [
            sys.executable, "-m", "pytest", "-q",
            # Phase 13 consumes the completed primary validation report and is
            # refreshed/tested in the post-report DAG below. Running its
            # current-artifact assertion here creates an unavoidable first-run
            # cycle after any validation change.
            "--ignore=tests/governance/test_727_final_audit.py",
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
            # 2026-07-27:Alpha Factory P5-1(727 §7/§8,Hypothesis 核心 + Store +
            # TrialProvenance 接线进两个 canonical 搜索入口)。
            "tests/alpha_factory",
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
            # 2026-09-20 审计 P0-3:这两个集成测试文件存在且全绿(18 tests),但从未进入
            # 这张表——canonical 门禁只在手动调用时跑到它们,回归抓不到。hourly alpha→paper
            # 恢复路径与 MFT testnet 执行矩阵都属于最安全关键的端到端证据,必须常驻门禁。
            "tests/integration/test_hourly_alpha_to_paper.py",
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
            # 2026-09-20 审计 P0-3:同上的漏测——testnet 执行矩阵此前只在手动调用时跑。
            "tests/integration/test_mft_testnet_execution.py",
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
        [
            NPM,
            "test",
            "--",
            "--maxWorkers=1",
            "--no-file-parallelism",
        ],
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
DEFAULT_SUBSET_REPORT = os.path.join(REPO, "evidence", "validation_report_subset.json")
_VALIDATION_ENV: dict[str, str] | None = None
_CANONICAL_ENV_KEYS = (
    "QUANTOS_VALIDATION_RUN_ID",
    "QUANTOS_VALIDATION_SOURCE_COMMIT",
    "QUANTOS_VALIDATION_STARTED_AT_MS",
)
_VALIDATION_RUN_ID_DOMAIN = "quantos-validation-run:v2"
_VALIDATION_TIMING_KEYS = frozenset({"generated_at_ms", "started_at_ms", "duration_ms"})


def _deterministic_validation_run_id(
    source_commit: str,
    checks_manifest_sha256: str,
    derived_refresh_manifest_sha256: str,
    phase13_manifest_sha256: str = "",
) -> str:
    """Derive canonical full-run identity without wall-clock input."""
    payload = json.dumps(
        {
            "domain": _VALIDATION_RUN_ID_DOMAIN,
            "source_commit": source_commit,
            "checks_manifest_sha256": checks_manifest_sha256,
            "derived_refresh_manifest_sha256": derived_refresh_manifest_sha256,
            "phase13_manifest_sha256": phase13_manifest_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_pytest_command(command) -> bool:
    """Identify pytest self-test subprocesses by their executable arguments."""
    items = [str(item).lower() for item in command]
    return any(
        item == "pytest"
        or item.endswith("\\pytest.exe")
        or item.endswith("/pytest")
        or (
            item == "-m"
            and index + 1 < len(items)
            and items[index + 1] == "pytest"
        )
        for index, item in enumerate(items)
    )


# 2026-09-20 审计 P0-3:下列测试文件是端到端最安全关键的证据,必须真的**执行**。
# 门禁此前只看退出码,pytest 退出码 0 同时覆盖"全部执行并通过"与"整个文件被 skip 掉",
# 于是缺失 artifact / 缺失 adapter / pytest.skip 都能伪装成 PASS。策略按**文件**判定
# 而不是按阶段:仓库没有 data/cache artifact 时 tests/reproducibility 的 skip 是
# 文档化的诚实设计(见 tests/reproducibility/test_evidence_snapshots.py),按阶段判定
# 会把它误打成红色,按文件判定则两者都能正确区分。
NO_SKIP_TEST_PATHS = (
    "tests/integration/test_dry_run_all.py",
    "tests/integration/test_hourly_alpha_to_paper.py",
    "tests/integration/test_mft_testnet_execution.py",
)


def _normalise_test_path(value) -> str:
    """仓库内路径归一为 posix 相对路径;仓库外保留绝对路径。"""
    text = str(value).replace("\\", "/").strip()
    if not text:
        return ""
    try:
        relative = os.path.relpath(os.path.abspath(text), REPO).replace(os.sep, "/")
    except (OSError, ValueError):
        return text
    if relative.startswith("../"):
        return os.path.abspath(text).replace(os.sep, "/")
    return relative


def _paths_denote_same_test(left: str, right: str) -> bool:
    """同一测试文件的两种写法(相对/绝对)判等;同名不同目录不得误判。

    尾部两个路径分量都相同才算同一文件——`test_all_skipped.py` 与
    `/tmp/pytest-xxx/test_all_skipped.py` 判等,而 tests/live/test_pipeline.py
    与 tests/research/test_pipeline.py 不会互相误配。
    """
    if not left or not right:
        return False
    if left == right:
        return True
    left_parts = [part for part in left.split("/") if part]
    right_parts = [part for part in right.split("/") if part]
    if not left_parts or not right_parts:
        return False
    tail = min(len(left_parts), len(right_parts), 2)
    return left_parts[-tail:] == right_parts[-tail:]


def _read_pytest_counts(path: str) -> dict | None:
    """回读 pytest junitxml 的真实计数;缺失或不可解析一律返回 None(fail-closed)。"""
    try:
        root = xml.etree.ElementTree.parse(path).getroot()
    except (OSError, xml.etree.ElementTree.ParseError, ValueError):
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        return None
    totals = {"tests": 0, "skipped": 0, "failed": 0, "errors": 0}
    skipped_files: set[str] = set()
    for suite in suites:
        for key, attribute in (
            ("tests", "tests"),
            ("skipped", "skipped"),
            ("failed", "failures"),
            ("errors", "errors"),
        ):
            try:
                totals[key] += int(suite.get(attribute) or 0)
            except (TypeError, ValueError):
                return None
        for case in suite.iter("testcase"):
            if case.find("skipped") is None:
                continue
            file_attribute = case.get("file")
            if file_attribute:
                skipped_files.add(_normalise_test_path(file_attribute))
    totals["skipped_files"] = sorted(skipped_files)
    return totals


def _load_bearing_verdict(argv, counts: dict | None) -> str | None:
    """返回 load-bearing 测试未真正执行的失败原因;不涉及 load-bearing 时返回 None。"""
    if not _is_pytest_command(argv):
        return None
    declared = [_normalise_test_path(item) for item in argv]
    matched = [
        _normalise_test_path(path)
        for path in NO_SKIP_TEST_PATHS
        if any(_paths_denote_same_test(item, _normalise_test_path(path)) for item in declared)
    ]
    if not matched:
        return None
    if counts is None:
        return (
            "load-bearing test counts could not be read (missing or unparseable "
            "pytest junit report): " + ", ".join(matched)
        )
    executed = int(counts.get("tests") or 0) - int(counts.get("skipped") or 0)
    if executed <= 0:
        return "load-bearing stage executed zero tests: " + ", ".join(matched)
    skipped_files = counts.get("skipped_files") or ()
    skipped_load_bearing = [
        path
        for path in matched
        if any(_paths_denote_same_test(value, path) for value in skipped_files)
    ]
    if skipped_load_bearing:
        return "load-bearing tests were skipped: " + ", ".join(skipped_load_bearing)
    return None


_PHASE13_CANONICAL_ENV_STAGES = frozenset({
    "phase13-readiness:refresh",
    "phase13-readiness:current",
    "phase13-readiness:tests",
})


def _child_env(command, *, preserve_canonical: bool = False) -> dict[str, str]:
    """Keep canonical identity for evidence and Phase 13's pytest assertion."""
    env = dict(_VALIDATION_ENV or os.environ)
    if _is_pytest_command(command) and not preserve_canonical:
        for key in _CANONICAL_ENV_KEYS:
            env.pop(key, None)
    return env


def _summary(results: list[dict]) -> dict[str, int]:
    return {
        "selected": len(results),
        "passed": sum(result["status"] == "PASS" for result in results),
        "failed": sum(result["status"] == "FAIL" for result in results),
        "timed_out": sum(result["status"] == "TIMEOUT" for result in results),
        "errored": sum(result["status"] == "ERROR" for result in results),
        # 2026-09-20 审计 P0-3:pytest 的 skip 数量必须进机器报告,否则"诚实 skip"
        # 与"整体没跑"在证据里无法区分。策略判定见 _load_bearing_verdict。
        "skipped": sum(
            int((result.get("pytest_counts") or {}).get("skipped") or 0)
            for result in results
        ),
    }


def _overall_status(summary: dict[str, int]) -> str:
    return (
        "PASS"
        if summary["selected"] and summary["passed"] == summary["selected"]
        else "FAIL"
    )


def _phase13_refresh_checks() -> list[tuple[str, list[str], str]]:
    generator = os.path.join(REPO, "scripts", "generate_727_final_audit.py")
    tests = os.path.join(REPO, "tests", "governance", "test_727_final_audit.py")
    return [
        (
            "phase13-readiness:refresh",
            [sys.executable, generator, "--repo-root", REPO],
            REPO,
        ),
        (
            "phase13-readiness:current",
            [sys.executable, generator, "--repo-root", REPO, "--check"],
            REPO,
        ),
        (
            "phase13-readiness:tests",
            [sys.executable, "-m", "pytest", "-q", tests],
            REPO,
        ),
    ]


def _derived_evidence_refresh_checks() -> list[
    tuple[str, tuple[str, list[str], str]]
]:
    """Return the load-bearing derived-evidence DAG in dependency order.

    These refreshes are used only by a full canonical run after every
    non-derived check has passed. Subset validation remains read-only, and a
    failing prerequisite can never overwrite tracked evidence.
    """
    return [
        (
            "supply-chain-baseline:current",
            (
                "supply-chain-baseline:refresh",
                [
                    sys.executable,
                    os.path.join(REPO, "scripts", "generate_supply_chain_evidence.py"),
                ],
                REPO,
            ),
        ),
        (
            "top-tier-assessment-artifacts",
            (
                "top-tier-assessment:refresh",
                [
                    sys.executable,
                    os.path.join(REPO, "scripts", "assess_top_tier.py"),
                ],
                REPO,
            ),
        ),
    ]


def _is_derived_evidence_consumer(stage_name: str) -> bool:
    return stage_name.startswith(("pytest:", "frontend:"))


def _expected_full_stage_names(selected, derived_specs, phase13_specs) -> list[str]:
    derived_by_current = {name: refresh for name, refresh in derived_specs}
    selected_derived = [check for check in selected if check[0] in derived_by_current]
    selected_non_derived = [check for check in selected if check[0] not in derived_by_current]
    names = [check[0] for check in selected_non_derived if not _is_derived_evidence_consumer(check[0])]
    for current_name, refresh_check in derived_specs:
        if any(check[0] == current_name for check in selected_derived):
            names.extend([refresh_check[0], current_name])
    names.extend(check[0] for check in selected_non_derived if _is_derived_evidence_consumer(check[0]))
    names.extend(name for name, _, _ in phase13_specs)
    return names


def _sha256_file(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_release_evidence_manifest() -> None:
    document = write_release_evidence_manifest(REPO)
    print(
        "[validate] release evidence manifest: "
        f"{document['artifact_count']} artifacts bound",
        flush=True,
    )


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


def _without_timing(report: dict) -> dict:
    value = json.loads(json.dumps(report, ensure_ascii=False, sort_keys=True))
    for key in _VALIDATION_TIMING_KEYS:
        value.pop(key, None)
    validation = value.get("validation")
    if isinstance(validation, dict):
        validation.pop("generated_at", None)
    for result in value.get("results", []):
        if isinstance(result, dict):
            result.pop("duration_ms", None)
    return value


def _preserve_existing_timing(report: dict, existing: dict) -> dict:
    if _without_timing(report) != _without_timing(existing):
        return report
    preserved = json.loads(json.dumps(report, ensure_ascii=False, sort_keys=True))
    for key in _VALIDATION_TIMING_KEYS:
        if key in existing:
            preserved[key] = existing[key]
    if isinstance(existing.get("validation"), dict) and isinstance(
        preserved.get("validation"), dict
    ) and "generated_at" in existing["validation"]:
        preserved["validation"]["generated_at"] = existing["validation"]["generated_at"]
    for current, previous in zip(preserved.get("results", []), existing.get("results", [])):
        if isinstance(current, dict) and isinstance(previous, dict) and "duration_ms" in previous:
            current["duration_ms"] = previous["duration_ms"]
    return preserved


def _serialize_report(report: dict) -> bytes:
    return (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _write_report(
    path: str,
    report: dict,
    *,
    preserve_timing: bool = True,
    existing: dict | None = None,
) -> None:
    path = os.path.abspath(path)
    existing_bytes = _serialize_report(existing) if isinstance(existing, dict) else None
    if preserve_timing and isinstance(existing, dict):
        report = _preserve_existing_timing(report, existing)
    if preserve_timing and existing is None and os.path.isfile(path):
        try:
            with open(path, "rb") as handle:
                existing_bytes = handle.read()
            existing = json.loads(existing_bytes.decode("utf-8"))
            if preserve_timing and isinstance(existing, dict):
                report = _preserve_existing_timing(report, existing)
        except (OSError, UnicodeError, ValueError, TypeError):
            pass
    serialized = _serialize_report(report)
    if existing_bytes == serialized:
        return
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".validation-report-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(serialized)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _load_completed_canonical_report(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            existing = json.load(handle)
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    return existing if isinstance(existing, dict) else None


def _completed_canonical_report_matches(
    path_or_report, report: dict, *, expected_selected_stages=None
) -> bool:
    """Return whether a parsed report holds this run's complete PASS evidence."""
    existing = (
        path_or_report
        if isinstance(path_or_report, dict)
        else _load_completed_canonical_report(path_or_report)
    )
    if existing is None or not isinstance(report, dict):
        return False
    report_validation = report.get("validation")
    if not isinstance(report_validation, dict):
        return False
    validation = existing.get("validation")
    selected_stages = existing.get("selected_stages")
    results = existing.get("results")
    phase13_stages = {
        "phase13-readiness:refresh",
        "phase13-readiness:current",
        "phase13-readiness:tests",
    }
    if (
        not isinstance(selected_stages, list)
        or any(not isinstance(stage, str) for stage in selected_stages)
        or len(selected_stages) != len(set(selected_stages))
        or not isinstance(results, list)
        or any(
            not isinstance(result, dict)
            or not isinstance(result.get("stage"), str)
            or not isinstance(result.get("status"), str)
            for result in results
        )
    ):
        return False
    result_stages = [result["stage"] for result in results]
    if len(result_stages) != len(set(result_stages)):
        return False
    if set(result_stages) != set(selected_stages):
        return False
    if expected_selected_stages is not None and (
        selected_stages != list(expected_selected_stages)
        or result_stages != list(expected_selected_stages)
    ):
        return False
    summary = existing.get("summary")
    if not isinstance(summary, dict) or summary != _summary(results):
        return False
    return (
        existing.get("report_kind") == "FAIL_CLOSED_VALIDATION"
        and existing.get("selection_scope") == "ALL"
        and existing.get("overall_status") == "PASS"
        and isinstance(validation, dict)
        and validation.get("current") is True
        and validation.get("source_commit") == existing.get("commit")
        and isinstance(validation.get("generated_at"), int)
        and not isinstance(validation.get("generated_at"), bool)
        and validation.get("generated_at") >= 0
        and phase13_stages.issubset(set(selected_stages))
        and all(result["status"] == "PASS" for result in results)
        and all(
            next(result for result in results if result["stage"] == stage)["status"]
            == "PASS"
            for stage in phase13_stages
        )
        and existing.get("commit") == report.get("commit")
        and existing.get("checks_manifest_sha256") == report.get("checks_manifest_sha256")
        and existing.get("derived_refresh_manifest_sha256") == report.get("derived_refresh_manifest_sha256")
        and existing.get("phase13_manifest_sha256") == report.get("phase13_manifest_sha256")
        and existing.get("validate_script_sha256") == report.get("validate_script_sha256")
        and validation.get("run_id") == report_validation.get("run_id")
    )


def _canonical_started_at_ms(
    path_or_report, report: dict, *, fresh_started_at_ms: int,
    expected_selected_stages=None,
) -> int | None:
    """Reuse a completed canonical time anchor only when it is not from the future."""
    existing = (
        path_or_report
        if isinstance(path_or_report, dict)
        else _load_completed_canonical_report(path_or_report)
    )
    if existing is None or not isinstance(report, dict):
        return None
    report_validation = report.get("validation")
    if not isinstance(report_validation, dict):
        return None
    # Validate the already parsed object, avoiding a second read/TOCTOU window.
    validation = existing.get("validation")
    selected_stages = existing.get("selected_stages")
    results = existing.get("results")
    phase13_stages = {
        "phase13-readiness:refresh",
        "phase13-readiness:current",
        "phase13-readiness:tests",
    }
    if (
        not isinstance(selected_stages, list)
        or any(not isinstance(stage, str) for stage in selected_stages)
        or len(selected_stages) != len(set(selected_stages))
        or not isinstance(results, list)
        or any(
            not isinstance(result, dict)
            or not isinstance(result.get("stage"), str)
            or not isinstance(result.get("status"), str)
            for result in results
        )
    ):
        return None
    result_stages = [result["stage"] for result in results]
    if (
        len(result_stages) != len(set(result_stages))
        or set(result_stages) != set(selected_stages)
    ):
        return None
    if expected_selected_stages is not None and (
        selected_stages != list(expected_selected_stages)
        or result_stages != list(expected_selected_stages)
    ):
        return None
    summary = existing.get("summary")
    if not isinstance(summary, dict) or summary != _summary(results):
        return None
    if not (
        existing.get("report_kind") == "FAIL_CLOSED_VALIDATION"
        and existing.get("selection_scope") == "ALL"
        and existing.get("overall_status") == "PASS"
        and isinstance(validation, dict)
        and validation.get("current") is True
        and validation.get("source_commit") == existing.get("commit")
        and isinstance(validation.get("generated_at"), int)
        and not isinstance(validation.get("generated_at"), bool)
        and validation.get("generated_at") >= 0
        and phase13_stages.issubset(set(selected_stages))
        and all(result["status"] == "PASS" for result in results)
        and existing.get("commit") == report.get("commit")
        and existing.get("checks_manifest_sha256") == report.get("checks_manifest_sha256")
        and existing.get("derived_refresh_manifest_sha256") == report.get("derived_refresh_manifest_sha256")
        and existing.get("phase13_manifest_sha256") == report.get("phase13_manifest_sha256")
        and existing.get("validate_script_sha256") == report.get("validate_script_sha256")
        and validation.get("run_id") == report_validation.get("run_id")
    ):
        return None
    validation_generated_at = validation.get("generated_at")
    started_at_ms = existing.get("started_at_ms")
    if (
        isinstance(fresh_started_at_ms, bool)
        or not isinstance(fresh_started_at_ms, int)
        or isinstance(started_at_ms, bool)
        or not isinstance(started_at_ms, int)
        or started_at_ms < 0
        or started_at_ms > fresh_started_at_ms
        or isinstance(validation_generated_at, bool)
        or not isinstance(validation_generated_at, int)
        or validation_generated_at < started_at_ms
        or validation_generated_at > fresh_started_at_ms
    ):
        return None
    return started_at_ms


def _run_check(name: str, argv, cwd: str, timeout_seconds: float) -> dict:
    started = time.monotonic()
    command = [str(item) for item in argv]
    absolute_cwd = os.path.abspath(cwd)
    # 2026-09-20 审计 P0-3:pytest 阶段额外挂一个 junitxml 报告以回读真实计数。
    # 报告路径是宿主临时的,绝不能进 `command`——`command` 会写进被哈希的证据报告,
    # 注入临时路径会让同一棵树两次运行的报告不同。`executed` 才是真正跑的命令。
    executed = list(command)
    counts: dict | None = None
    junit_path: str | None = None
    if _is_pytest_command(command) and not any(
        str(item).startswith("--junitxml") for item in command
    ):
        try:
            junit_fd, junit_path = tempfile.mkstemp(
                prefix=".validate-junit-", suffix=".xml"
            )
            os.close(junit_fd)
        except OSError:
            junit_path = None
        if junit_path is not None:
            # xunit2 omits testcase.file, so a required-file skip can hide
            # behind an unrelated passing test. Keep file identity available
            # to the per-file no-skip policy, regardless of project defaults.
            executed = command + [f"--junitxml={junit_path}", "-o", "junit_family=xunit1"]
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
    captured_tail: str | None = None
    try:
        # stdin=DEVNULL: under detached/agent-hosted shells the inherited
        # console handles can be/become invalid; any child spawned with an
        # inherited dead handle then dies in _make_inheritable with WinError 6
        # before it even starts (observed 2026-08-02 as the 26/27 stage
        # failure: every subprocess test in tests/governance).  Every check is
        # non-interactive, so a NUL stdin is the honest contract.  If even the
        # inherited stdout/stderr handles are dead, fall back to captured
        # pipes — the gate must not depend on the host console at all.
        preserve_canonical = name in _PHASE13_CANONICAL_ENV_STAGES
        try:
            process = subprocess.Popen(executed, cwd=absolute_cwd, stdin=subprocess.DEVNULL, env=_child_env(command, preserve_canonical=preserve_canonical))
        except OSError as exc:
            # ASCII-only, locale-independent detail: the report is hashed
            # evidence, and localized OS error text (e.g. GBK WinError text)
            # would make it host-dependent.
            detail = (
                "host console handles unavailable "
                f"({type(exc).__name__} winerror={getattr(exc, 'winerror', None)}); "
                "child output captured instead of streamed"
            )
            process = subprocess.Popen(
                executed,
                cwd=absolute_cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=_child_env(command, preserve_canonical=preserve_canonical),
            )
        try:
            if process.stdout is not None:
                captured, _ = process.communicate(timeout=timeout_seconds)
                returncode = process.returncode
                captured_tail = captured.decode("utf-8", errors="replace")[-4000:]
            else:
                returncode = process.wait(timeout=timeout_seconds)
            status = "PASS" if returncode == 0 else "FAIL"
        except subprocess.TimeoutExpired:
            detail = f"stage exceeded {timeout_seconds:.1f}s"
            try:
                process.kill()
            finally:
                if process.stdout is not None:
                    captured, _ = process.communicate()
                    captured_tail = captured.decode("utf-8", errors="replace")[-4000:]
                else:
                    process.wait()
            status = "TIMEOUT"
    except OSError as exc:
        detail = f"{type(exc).__name__}: {exc}"

    if junit_path is not None:
        counts = _read_pytest_counts(junit_path)
        try:
            os.unlink(junit_path)
        except OSError:
            pass
    if status == "PASS":
        # 退出码 0 只说明 pytest 没报错,不说明测试真的执行了。
        load_bearing = _load_bearing_verdict(command, counts)
        if load_bearing is not None:
            status, detail = "FAIL", load_bearing

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
    if counts is not None:
        result["pytest_counts"] = counts
    if captured_tail and status != "PASS":
        # Keep a bounded failure tail in the machine report so a degraded-host
        # run remains diagnosable without the live console stream.
        result["child_output_tail"] = captured_tail
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
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS, checks=None,
        phase13_refresh_checks=None, derived_refresh_checks=None) -> int:
    """Run selected checks with live progress and a fail-closed machine report."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    checks = list(checks or CHECKS)
    selected = _select_checks(stages, checks)
    selection_scope = "ALL" if not stages or stages == ["all"] else "SUBSET"
    derived_specs = (
        list(derived_refresh_checks)
        if derived_refresh_checks is not None
        else _derived_evidence_refresh_checks()
    )
    derived_by_current = {
        current_name: refresh_check
        for current_name, refresh_check in derived_specs
    }
    if len(derived_by_current) != len(derived_specs):
        raise ValueError("derived evidence refresh targets must be unique")
    allow_canonical_reuse = selection_scope == "ALL" and phase13_refresh_checks is None
    if selection_scope == "ALL" and phase13_refresh_checks == []:
        raise ValueError("canonical ALL validation requires Phase 13 checks")
    phase13_specs = (
        _phase13_refresh_checks()
        if phase13_refresh_checks is None
        else list(phase13_refresh_checks)
    )
    phase13_names = [name for name, _, _ in phase13_specs]
    if selection_scope == "ALL" and (
        phase13_names != [
            "phase13-readiness:refresh",
            "phase13-readiness:current",
            "phase13-readiness:tests",
        ]
        or len(phase13_names) != len(set(phase13_names))
        or any(not isinstance(name, str) for name in phase13_names)
    ):
        raise ValueError("ALL validation requires exactly the canonical Phase 13 checks")
    phase13_manifest_sha256 = _checks_manifest_hash(phase13_specs)
    expected_selected_stages = _expected_full_stage_names(
        selected, derived_specs, phase13_specs
    )
    canonical_report_existing = (
        _load_completed_canonical_report(json_report)
        if allow_canonical_reuse
        else None
    )
    started_at_ms = int(time.time() * 1000)
    started = time.monotonic()
    canonical_report_reused = False
    if selection_scope == "ALL":
        source_commit = resolve_canonical_source_anchor(REPO)
        checks_manifest_sha256 = _checks_manifest_hash(checks)
        derived_refresh_manifest_sha256 = _checks_manifest_hash(
            [refresh_check for _, refresh_check in derived_specs]
        )
        validation_run_id = _deterministic_validation_run_id(
            source_commit,
            checks_manifest_sha256,
            derived_refresh_manifest_sha256,
            phase13_manifest_sha256,
        )
        identity_report = {
            "commit": source_commit,
            "checks_manifest_sha256": checks_manifest_sha256,
            "derived_refresh_manifest_sha256": derived_refresh_manifest_sha256,
            "phase13_manifest_sha256": phase13_manifest_sha256,
            "validate_script_sha256": _sha256_file(__file__),
            "validation": {"run_id": validation_run_id},
        }
        previous_started_at_ms = (
            _canonical_started_at_ms(
                canonical_report_existing,
                identity_report,
                fresh_started_at_ms=started_at_ms,
                expected_selected_stages=expected_selected_stages,
            )
            if allow_canonical_reuse
            else None
        )
        canonical_report_reused = previous_started_at_ms is not None
        if canonical_report_reused:
            started_at_ms = previous_started_at_ms
    else:
        source_commit = _git_commit()
        validation_run_id = hashlib.sha256(
            f"{source_commit}:{started_at_ms}".encode("utf-8")
        ).hexdigest()
    global _VALIDATION_ENV
    if selection_scope == "ALL":
        _VALIDATION_ENV = {
            **os.environ,
            "QUANTOS_VALIDATION_RUN_ID": validation_run_id,
            "QUANTOS_VALIDATION_SOURCE_COMMIT": source_commit,
            "QUANTOS_VALIDATION_STARTED_AT_MS": str(started_at_ms),
        }
    else:
        # A subset run is diagnostic and read-only; it is not the canonical
        # validation run that produced tracked evidence.  Passing its
        # short-lived identity to evidence checks makes a correctly bound
        # artifact fail solely because the operator selected ``--stage``.
        # Strip even inherited values so standalone ``:current`` checks use
        # their persisted binding and the repository's stable source anchor.
        _VALIDATION_ENV = {
            key: value
            for key, value in os.environ.items()
            if key not in _CANONICAL_ENV_KEYS
        }
    if selection_scope == "SUBSET":
        # Subset validation is deliberately read-only and preserves the
        # operator-requested order.
        results = [
            _run_check(name, argv, cwd, timeout_seconds)
            for name, argv, cwd in selected
        ]
    else:
        selected_derived = [
            check for check in selected if check[0] in derived_by_current
        ]
        selected_non_derived = [
            check for check in selected if check[0] not in derived_by_current
        ]
        prerequisite_checks = [
            check
            for check in selected_non_derived
            if not _is_derived_evidence_consumer(check[0])
        ]
        consumer_checks = [
            check
            for check in selected_non_derived
            if _is_derived_evidence_consumer(check[0])
        ]
        results = [
            _run_check(name, argv, cwd, timeout_seconds)
            for name, argv, cwd in prerequisite_checks
        ]
        prerequisites_passed = _overall_status(_summary(results)) == "PASS"
        selected_derived_by_name = {
            check[0]: check for check in selected_derived
        }
        if prerequisites_passed:
            dependency_chain_passed = True
            for current_name, refresh_check in derived_specs:
                current_check = selected_derived_by_name.get(current_name)
                if current_check is None:
                    continue
                if dependency_chain_passed:
                    refresh_result = _run_check(*refresh_check, timeout_seconds)
                    results.append(refresh_result)
                    current_result = _run_check(*current_check, timeout_seconds)
                    results.append(current_result)
                    dependency_chain_passed = (
                        refresh_result["status"] == "PASS"
                        and current_result["status"] == "PASS"
                    )
                else:
                    # Do not rewrite a downstream artifact from a failed or
                    # stale upstream dependency. Its read-only check remains
                    # observable and fail-closed in the report.
                    results.append(_run_check(*current_check, timeout_seconds))
        else:
            # A failed prerequisite must never rewrite evidence. The current
            # checks still run so drift remains visible in the report.
            results.extend(
                _run_check(name, argv, cwd, timeout_seconds)
                for name, argv, cwd in selected_derived
            )
        # Governance tests and frontend checks consume generated evidence, so
        # they run after the DAG and independently retain fail-closed status.
        results.extend(
            _run_check(name, argv, cwd, timeout_seconds)
            for name, argv, cwd in consumer_checks
        )

    summary = _summary(results)
    overall_status = _overall_status(summary)
    selected_names = [result["stage"] for result in results]
    effective_report = json_report
    if (
        selection_scope == "SUBSET"
        and os.path.abspath(json_report) == os.path.abspath(DEFAULT_REPORT)
    ):
        effective_report = DEFAULT_SUBSET_REPORT

    def build_report() -> dict:
        return {
            "schema_version": 1,
            "report_kind": "FAIL_CLOSED_VALIDATION",
            "generated_at_ms": int(time.time() * 1000),
            "started_at_ms": started_at_ms,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "commit": source_commit,
            "validation": validation_binding(
                generated_at=int(time.time() * 1000),
                run_id=validation_run_id,
                source_commit=source_commit,
                current=overall_status == "PASS",
            ),
            "readiness": "L2_DRY_RUN_ONLY",
            "selection_scope": selection_scope,
            "requested_stages": list(stages or ["all"]),
            "selected_stages": selected_names,
            "timeout_seconds": timeout_seconds,
            "checks_manifest_sha256": _checks_manifest_hash(checks),
            "derived_refresh_manifest_sha256": _checks_manifest_hash(
                [refresh_check for _, refresh_check in derived_specs]
            ),
            "phase13_manifest_sha256": phase13_manifest_sha256,
            "validate_script_sha256": _sha256_file(__file__),
            "summary": summary,
            "overall_status": overall_status,
            "results": results,
        }

    # Phase 13 readiness consumes this report.  Publish the completed primary
    # result before refreshing those derived artifacts, otherwise the tracked
    # readiness test and the validation report form a circular dependency:
    # one failed attempt can poison every later attempt after the defect is fixed.
    report = build_report()
    report_written = True
    skip_preliminary = canonical_report_reused and overall_status == "PASS"
    try:
        if not skip_preliminary:
            _write_report(
                effective_report,
                report,
                preserve_timing=canonical_report_reused,
                existing=canonical_report_existing,
            )
        else:
            print(
                "[validate] retaining completed canonical report until Phase 13 finishes",
                flush=True,
            )
    except OSError as exc:
        report_written = False
        overall_status = "FAIL"
        print(
            f"[validate] ERROR cannot write preliminary validation report: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

    if selection_scope == "ALL" and report_written:
        refresh_checks = phase13_specs
        for name, argv, cwd in refresh_checks:
            results.append(_run_check(name, argv, cwd, timeout_seconds))
            selected_names.append(name)
        summary = _summary(results)
        overall_status = _overall_status(summary)

    report = build_report()
    try:
        _write_report(
            effective_report,
            report,
            preserve_timing=canonical_report_reused,
            existing=canonical_report_existing,
        )
        print(f"[validate] report: {os.path.abspath(effective_report)}", flush=True)
    except OSError as exc:
        print(
            f"[validate] ERROR cannot write validation report: {type(exc).__name__}: {exc}",
            flush=True,
        )
        overall_status = "FAIL"

    if (
        selection_scope == "ALL"
        and overall_status == "PASS"
        and os.path.abspath(effective_report) == os.path.abspath(DEFAULT_REPORT)
    ):
        try:
            _publish_release_evidence_manifest()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            print(
                "[validate] ERROR cannot publish release evidence manifest: "
                f"{type(exc).__name__}: {exc}",
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
