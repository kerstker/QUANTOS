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
import importlib
from pathlib import Path
import sys
import traceback
from types import ModuleType


ALLOWED_L2_MODULES = frozenset(
    {
        # A 股微盘研究栈(只读公共数据取数/回填;no-arg main 与 argv-scrub 约定兼容)
        "ashare_kline_fetch",
        "ashare_ifind_fetch",
        "ashare_paper_track",
        "alpha_maintenance_sweep",
        "crypto_factory_loop",
        "domain_event_ops",
        "domain_event_seal",
        "external_heartbeat",
        "forward_track_advance",
        "gonogo_report",
        "alpha_paper_cycle",
        "paper_order_preview",
        "paper_realtime",
        "paper_realtime_10k",
        "paper_health",
        "l2_paper_operator",
        "ic_panel_research",
        "l2_backup",
        "l2_scheduler_watchdog",
    }
)


class ScheduleEntrypointError(RuntimeError):
    """A scheduled worker could not be resolved to the approved release root."""


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def run_scheduled_module(
    module_name: str,
    *,
    code_root: str | Path | None = None,
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
    release_tools = release_root / "tools"
    original_path = list(sys.path)
    try:
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
        # The module name is the entrypoint's argument, not a worker argument.
        # Clear it while invoking the worker so argparse-based L2 workers (for
        # example l2_backup) do not interpret ``l2_backup`` as an unknown flag.
        # Restore argv even when the worker raises; scheduled invocations never
        # pass through arbitrary worker flags.
        original_argv = sys.argv
        try:
            sys.argv = [f"tools.{module_name}"]
            result = worker_main()
        finally:
            sys.argv = original_argv
        if result is None:
            return 0
        if isinstance(result, bool) or not isinstance(result, int):
            raise ScheduleEntrypointError(
                "scheduled module returned an ambiguous exit code"
            )
        return result
    finally:
        sys.path[:] = original_path


def _open_schedule_log(log_dir: str | Path, module_name: str):
    """Open the per-worker log used by non-console scheduled invocations."""
    path = Path(log_dir) / f"{module_name}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a", encoding="utf-8", buffering=1)
    started_at = datetime.now(timezone.utc).isoformat()
    handle.write(f"\n[{started_at}] START module={module_name}\n")
    handle.flush()
    return handle


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an allowlisted QUANTOS L2 worker")
    parser.add_argument("module", choices=sorted(ALLOWED_L2_MODULES))
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="append stdout/stderr and tracebacks to <log-dir>/<module>.log",
    )
    args = parser.parse_args(argv)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    log_handle = None
    if args.log_dir is not None:
        log_handle = _open_schedule_log(args.log_dir, args.module)
        sys.stdout = log_handle
        sys.stderr = log_handle
    try:
        result = run_scheduled_module(args.module)
        if log_handle is not None:
            finished_at = datetime.now(timezone.utc).isoformat()
            print(f"[{finished_at}] END module={args.module} exit_code={result}", flush=True)
        return result
    except BaseException:
        if log_handle is not None:
            traceback.print_exc()
            finished_at = datetime.now(timezone.utc).isoformat()
            print(f"[{finished_at}] END module={args.module} exception", flush=True)
        raise
    finally:
        if log_handle is not None:
            log_handle.flush()
            sys.stdout = original_stdout
            sys.stderr = original_stderr
            log_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ALLOWED_L2_MODULES",
    "ScheduleEntrypointError",
    "run_scheduled_module",
]
