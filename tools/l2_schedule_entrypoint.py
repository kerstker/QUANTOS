"""Load an allowlisted L2 scheduled worker from an immutable release root.

The process working directory remains the mutable operator-owned state root, so
existing ``data/cache`` contracts stay intact.  Code resolution is pinned to the
directory containing this entrypoint and is verified after import; a dirty state
checkout therefore cannot shadow the scheduled worker or any of its dependencies.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import importlib
from pathlib import Path
import sys
from types import ModuleType


ALLOWED_L2_MODULES = frozenset(
    {
        "alpha_paper_cycle",
        "alpha_maintenance_sweep",
        "artifact_admission_seal",
        "crypto_factory_loop",
        "domain_event_ops",
        "domain_event_seal",
        "external_heartbeat",
        "forward_track_advance",
        "gonogo_report",
        "ic_panel_research",
        "l2_backup",
        "l2_scheduler_watchdog",
        "paper_health",
        "paper_alpha_evaluation",
        "paper_order_preview",
        "paper_research_risk_bundle",
        "paper_realtime",
        "paper_realtime_10k",
        "paper_shadow_auto",
        "paper_shadow_realtime",
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
    original_argv = list(sys.argv)
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
        # Worker CLIs commonly parse ``sys.argv``. Hide the launcher's own
        # module selector so the worker observes the same empty argument
        # surface it would receive when invoked directly by the scheduler.
        sys.argv[:] = [str(Path(module_file).resolve())]
        result = worker_main()
        if result is None:
            return 0
        if isinstance(result, bool) or not isinstance(result, int):
            raise ScheduleEntrypointError(
                "scheduled module returned an ambiguous exit code"
            )
        return result
    finally:
        sys.path[:] = original_path
        sys.argv[:] = original_argv


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an allowlisted QUANTOS L2 worker")
    parser.add_argument("module", choices=sorted(ALLOWED_L2_MODULES))
    args = parser.parse_args(argv)
    return run_scheduled_module(args.module)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ALLOWED_L2_MODULES",
    "ScheduleEntrypointError",
    "run_scheduled_module",
]
