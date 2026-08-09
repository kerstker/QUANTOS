"""Load an allowlisted L2 scheduled worker from an immutable release root.

The process working directory remains the mutable operator-owned state root, so
existing ``data/cache`` contracts stay intact.  Code resolution is pinned to the
directory containing this entrypoint and is verified after import; a dirty state
checkout therefore cannot shadow the scheduled worker or any of its dependencies.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
import hashlib
import importlib
import json
import ntpath
import os
from pathlib import Path
import re
import subprocess
import sys
from types import ModuleType


ALLOWED_L2_MODULES = frozenset(
    {
        "alpha_paper_cycle",
        "alpha_lifecycle_cockpit",
        "alpha_production_workflow",
        "alpha_maintenance_sweep",
        "artifact_admission_seal",
        "binance_realtime_market",
        "binance_systemic_risk",
        "binance_rpi_depth",
        "binance_realtime_features",
        "binance_realtime_drift_alerts",
        "binance_realtime_stream",
        "candidate_portfolio_evidence",
        "crypto_factory_loop",
        "dry_run_candidate_multi",
        "domain_event_ops",
        "domain_event_seal",
        "external_heartbeat",
        "forward_track_advance",
        "hourly_alpha_advance",
        "gonogo_report",
        "ic_panel_research",
        "l2_backup",
        "l2_scheduler_watchdog",
        "mft_l2_runtime",
        "paper_health",
        "paper_alpha_evaluation",
        "paper_multivenue_sor",
        "paper_operational_evidence",
        "paper_order_preview",
        "paper_research_risk_bundle",
        "paper_realtime",
        "paper_realtime_10k",
        "paper_shadow_auto",
        "paper_shadow_realtime",
        "raw_archive_audit",
        "realtime_alpha_discovery",
        "realtime_alpha_execution_paper",
        "realtime_alpha_formal_shadow",
        "realtime_alpha_forward_shadow",
    }
)


class ScheduleEntrypointError(RuntimeError):
    """A scheduled worker could not be resolved to the approved release root."""


_HEADLESS_STREAMS: list[object] = []
_GIT_COMMIT = re.compile(r"[0-9a-f]{40}")
_RELEASE_COMMIT_ENV = "QUANTOS_RELEASE_COMMIT"
_RELEASE_ROOT_ENV = "QUANTOS_RELEASE_ROOT"
_STATE_ROOT_ENV = "QUANTOS_STATE_ROOT"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ATTESTABLE_EXIT_CODES = frozenset({0, 2})
REQUIRED_INPUT_ATTESTATION_MODULES = frozenset(
    {
        "alpha_production_workflow",
        "binance_realtime_features",
        "binance_realtime_market",
        "paper_multivenue_sor",
        "realtime_alpha_discovery",
        "realtime_alpha_execution_paper",
        "realtime_alpha_formal_shadow",
        "realtime_alpha_forward_shadow",
    }
)
_ATTESTATION_SAFETY = {
    "paper_only": True,
    "live_allowed": False,
    "order_submission_allowed": False,
    "credentials_accessed": False,
    "broker_connectivity": False,
}


def _configure_headless_logging(module_name: str) -> None:
    """Route pythonw output to an IDE-readable per-worker log."""
    if os.path.splitext(ntpath.basename(sys.executable))[0].lower() != "pythonw":
        return
    log_root = Path.cwd() / ".runtime" / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    stream = (log_root / f"{module_name}.log").open(
        "a",
        encoding="utf-8",
        buffering=1,
    )
    _HEADLESS_STREAMS.append(stream)
    sys.stdout = stream
    sys.stderr = stream


def _install_headless_subprocess_guard(
    *,
    executable: str | None = None,
    platform_name: str | None = None,
) -> None:
    """Force subprocesses created by a Windows pythonw worker to stay headless.

    Scheduled modules normally do not create child processes.  This guard
    protects the operator desktop if an allowlisted worker (or a helper it
    imports) adds one later.  An explicit request for a new console is rejected
    instead of silently weakening the no-window contract.
    """
    current_executable = sys.executable if executable is None else executable
    current_platform = os.name if platform_name is None else platform_name
    executable_stem = os.path.splitext(ntpath.basename(current_executable))[0].lower()
    if current_platform != "nt" or executable_stem != "pythonw":
        return
    current_popen = subprocess.Popen
    if getattr(current_popen, "_quantos_headless_guard", False):
        return
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    new_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    if not no_window:
        raise ScheduleEntrypointError("Windows no-window process flag is unavailable")

    class HeadlessPopen(current_popen):  # type: ignore[misc, valid-type]
        _quantos_headless_guard = True

        def __init__(self, *args: object, **kwargs: object) -> None:
            creationflags = int(kwargs.get("creationflags", 0))
            if new_console and creationflags & new_console:
                raise ScheduleEntrypointError(
                    "scheduled L2 worker attempted to create an interactive console"
                )
            kwargs["creationflags"] = creationflags | no_window
            super().__init__(*args, **kwargs)

    subprocess.Popen = HeadlessPopen


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _require_separate_roots(release_root: Path, state_root: Path) -> None:
    if _is_within(release_root, state_root) or _is_within(state_root, release_root):
        raise ScheduleEntrypointError(
            "release and state roots must be separate and non-overlapping"
        )


def _verify_release_identity(
    release_root: Path,
    expected_release_commit: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """Fail closed unless ``release_root`` is the exact clean registered commit.

    ``GIT_OPTIONAL_LOCKS=0`` keeps verification read-only for a release checkout.
    Both tracked and untracked changes are rejected; ignored mutable state is not
    part of the release identity and belongs in the separate state root.
    """
    if _GIT_COMMIT.fullmatch(expected_release_commit) is None:
        raise ScheduleEntrypointError("registered release commit is invalid")
    command_options = {
        "capture_output": True,
        "text": True,
        "check": False,
        "timeout": 10,
        "env": {**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    }
    try:
        head = runner(
            [
                "git",
                "-C",
                str(release_root),
                "rev-parse",
                "--verify",
                "HEAD",
            ],
            **command_options,
        )
        if head.returncode != 0:
            raise ScheduleEntrypointError("release root has no verifiable Git HEAD")
        actual_commit = head.stdout.strip()
        if _GIT_COMMIT.fullmatch(actual_commit) is None:
            raise ScheduleEntrypointError("release root Git HEAD is invalid")
        if actual_commit != expected_release_commit:
            raise ScheduleEntrypointError(
                "release root HEAD does not match the registered release commit"
            )
        status = runner(
            [
                "git",
                "-C",
                str(release_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--ignore-submodules=none",
                "--",
            ],
            **command_options,
        )
        if status.returncode != 0:
            raise ScheduleEntrypointError("release root cleanliness is unverifiable")
        if status.stdout.strip():
            raise ScheduleEntrypointError("release root is not clean")
        return actual_commit
    except ScheduleEntrypointError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScheduleEntrypointError("release identity verification failed") from exc


def _receipt_document(payload: dict[str, object]) -> dict[str, object]:
    document = dict(payload)
    canonical = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    document["receipt_sha256"] = hashlib.sha256(canonical).hexdigest()
    return document


def _write_release_run_receipt(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(
            _receipt_document(payload),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _validate_worker_attestation(
    module_name: str,
    payload: object,
    *,
    expected_release_commit: str,
) -> tuple[dict[str, object], str]:
    expected_keys = {
        "schema_version",
        "module",
        "release_commit",
        "complete",
        "input_hashes",
        "output_hashes",
        "safety",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise ScheduleEntrypointError("worker input/evidence attestation is malformed")
    if (
        payload.get("schema_version") != 1
        or payload.get("module") != module_name
        or payload.get("release_commit") != expected_release_commit
        or payload.get("complete") is not True
    ):
        raise ScheduleEntrypointError("worker input/evidence attestation is invalid")
    for field in ("input_hashes", "output_hashes"):
        values = payload.get(field)
        if (
            not isinstance(values, dict)
            or not values
            or any(
                not isinstance(name, str)
                or not name
                or not isinstance(value, str)
                or _SHA256.fullmatch(value) is None
                for name, value in values.items()
            )
        ):
            raise ScheduleEntrypointError(
                "worker input/evidence attestation contains an invalid digest set"
            )
    if payload.get("safety") != _ATTESTATION_SAFETY:
        raise ScheduleEntrypointError(
            "worker input/evidence attestation violates the L2 safety contract"
        )
    document = json.loads(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )
    canonical = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return document, hashlib.sha256(canonical).hexdigest()


def run_scheduled_module(
    module_name: str,
    *,
    code_root: str | Path | None = None,
    expected_release_commit: str,
    release_verifier: Callable[[Path, str], str] = _verify_release_identity,
    on_release_verified: Callable[[Path, str], None] | None = None,
    on_worker_attested: Callable[[dict[str, object], str], None] | None = None,
    importer: Callable[[str], ModuleType] = importlib.import_module,
) -> int:
    """Run one fixed L2 module while preserving the caller's state directory."""
    if module_name not in ALLOWED_L2_MODULES:
        raise ScheduleEntrypointError("scheduled module is not allowlisted")
    release_root = (
        Path(__file__).resolve().parents[1]
        if code_root is None
        else Path(code_root).resolve()
    )
    state_root = Path.cwd().resolve()
    _require_separate_roots(release_root, state_root)
    verified_commit = release_verifier(release_root, expected_release_commit)
    if verified_commit != expected_release_commit:
        raise ScheduleEntrypointError("release verifier returned an unexpected commit")
    if on_release_verified is not None:
        on_release_verified(release_root, verified_commit)
    release_tools = release_root / "tools"
    original_path = list(sys.path)
    original_argv = list(sys.argv)
    original_release_commit = os.environ.get(_RELEASE_COMMIT_ENV)
    original_release_root = os.environ.get(_RELEASE_ROOT_ENV)
    original_state_root = os.environ.get(_STATE_ROOT_ENV)
    try:
        os.environ[_RELEASE_COMMIT_ENV] = verified_commit
        os.environ[_RELEASE_ROOT_ENV] = str(release_root)
        os.environ[_STATE_ROOT_ENV] = str(state_root)
        sys.path.insert(0, str(release_root))
        module = importer(f"tools.{module_name}")
        module_file = getattr(module, "__file__", None)
        if not isinstance(module_file, str) or not _is_within(
            Path(module_file).resolve(), release_tools
        ):
            raise ScheduleEntrypointError(
                "scheduled module resolved outside the release root"
            )
        worker_main = getattr(module, "main", None)
        if not callable(worker_main):
            raise ScheduleEntrypointError("scheduled module has no callable main")
        # Worker CLIs commonly parse ``sys.argv``. Hide the launcher's own
        # module selector so the worker observes the same empty argument
        # surface it would receive when invoked directly by the scheduler.
        sys.argv[:] = [str(Path(module_file).resolve())]
        try:
            result = worker_main()
        except SystemExit as exc:
            # Tool CLIs commonly use ``sys.exit(code)`` inside ``main`` rather
            # than returning the code.  Normalize that narrow control-flow
            # signal so the launcher can always finalize its release receipt.
            # KeyboardInterrupt and other BaseException subclasses still pass
            # through unchanged.
            result = 0 if exc.code is None else exc.code
        if result is None:
            return 0
        if isinstance(result, bool) or not isinstance(result, int):
            raise ScheduleEntrypointError(
                "scheduled module produced an ambiguous exit code"
            )
        attestation_fn = getattr(module, "release_run_attestation", None)
        if callable(attestation_fn):
            try:
                attestation, attestation_sha256 = _validate_worker_attestation(
                    module_name,
                    attestation_fn(),
                    expected_release_commit=verified_commit,
                )
            except ScheduleEntrypointError:
                raise
            except Exception as exc:
                raise ScheduleEntrypointError(
                    "worker input/evidence attestation failed"
                ) from exc
            if on_worker_attested is not None:
                on_worker_attested(attestation, attestation_sha256)
        elif (
            module_name in REQUIRED_INPUT_ATTESTATION_MODULES
            and result in _ATTESTABLE_EXIT_CODES
        ):
            raise ScheduleEntrypointError(
                "required worker input/evidence attestation is unavailable"
            )
        return result
    finally:
        sys.path[:] = original_path
        sys.argv[:] = original_argv
        if original_release_commit is None:
            os.environ.pop(_RELEASE_COMMIT_ENV, None)
        else:
            os.environ[_RELEASE_COMMIT_ENV] = original_release_commit
        if original_release_root is None:
            os.environ.pop(_RELEASE_ROOT_ENV, None)
        else:
            os.environ[_RELEASE_ROOT_ENV] = original_release_root
        if original_state_root is None:
            os.environ.pop(_STATE_ROOT_ENV, None)
        else:
            os.environ[_STATE_ROOT_ENV] = original_state_root


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an allowlisted QUANTOS L2 worker")
    parser.add_argument("module", choices=sorted(ALLOWED_L2_MODULES))
    parser.add_argument("--release-commit", required=True)
    args = parser.parse_args(argv)
    _configure_headless_logging(args.module)
    _install_headless_subprocess_guard()
    started_at = datetime.now(timezone.utc).isoformat()
    release_root = Path(__file__).resolve().parents[1]
    state_root = Path.cwd().resolve()
    _require_separate_roots(release_root, state_root)
    receipt_path = (
        state_root
        / ".runtime"
        / "l2-release-runs"
        / f"{int(datetime.now(timezone.utc).timestamp() * 1000)}-{os.getpid()}-{args.module}.json"
    )
    receipt: dict[str, object] = {
        "schema_version": 2,
        "module": args.module,
        "status": "STARTED",
        "release_commit": args.release_commit,
        "release_root": str(release_root),
        "state_root": str(state_root),
        "release_verified": False,
        "worker_attestation_required": (
            args.module in REQUIRED_INPUT_ATTESTATION_MODULES
        ),
        "worker_attested": False,
        "worker_attestation_sha256": None,
        "worker_attestation": None,
        "verified_at": None,
        "started_at": started_at,
        "finished_at": None,
        "exit_code": None,
        "error_type": None,
    }
    _write_release_run_receipt(receipt_path, receipt)
    print(
        f"[l2-schedule] START module={args.module} "
        f"release_commit={args.release_commit} at={started_at}",
        flush=True,
    )

    def record_release_verification(root: Path, commit: str) -> None:
        receipt.update(
            {
                "release_commit": commit,
                "release_root": str(root),
                "release_verified": True,
                "verified_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        _write_release_run_receipt(receipt_path, receipt)

    def record_worker_attestation(
        document: dict[str, object], digest: str
    ) -> None:
        receipt.update(
            {
                "worker_attested": True,
                "worker_attestation_sha256": digest,
                "worker_attestation": document,
            }
        )
        _write_release_run_receipt(receipt_path, receipt)

    try:
        result = run_scheduled_module(
            args.module,
            expected_release_commit=args.release_commit,
            on_release_verified=record_release_verification,
            on_worker_attested=record_worker_attestation,
        )
    except Exception as exc:
        receipt.update(
            {
                "status": "FAILED",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error_type": type(exc).__name__,
            }
        )
        _write_release_run_receipt(receipt_path, receipt)
        print(
            f"[l2-schedule] END module={args.module} status=ERROR "
            f"error_type={type(exc).__name__}",
            flush=True,
        )
        raise
    receipt.update(
        {
            "status": "COMPLETED" if result == 0 else "FAILED",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": result,
            "error_type": None if result == 0 else "NONZERO_EXIT",
        }
    )
    _write_release_run_receipt(receipt_path, receipt)
    print(
        f"[l2-schedule] END module={args.module} exit_code={result}",
        flush=True,
    )
    return result


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ALLOWED_L2_MODULES",
    "REQUIRED_INPUT_ATTESTATION_MODULES",
    "ScheduleEntrypointError",
    "_configure_headless_logging",
    "_install_headless_subprocess_guard",
    "_verify_release_identity",
    "run_scheduled_module",
]
