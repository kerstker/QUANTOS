"""Render disabled QUANTOS L2 systemd units into an isolated output directory.

This tool has no install, enable, start, sudo, or systemctl capability.  It
binds an immutable release commit to an explicit module/cadence allowlist so an
operator can review the resulting files before a separate privileged process.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import secrets
from typing import Iterable, Sequence

from tools.l2_schedule_entrypoint import ALLOWED_L2_MODULES
from tools.l2_scheduler_watchdog import EXPECTED_TASKS


class SystemdRenderError(RuntimeError):
    """A requested unit set is not a safe deterministic L2 configuration."""


REPO = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPO / "deploy" / "systemd"
_COMMIT = re.compile(r"[0-9a-f]{40}")
_MODULE = re.compile(r"[a-z0-9_]+")
_SAFE_POSIX_MOUNT = re.compile(r"/[A-Za-z0-9._/-]+")
_RELEASE_TOKEN = "__QUANTOS_RELEASE_SHA__"
_RESIDENT_MODULES = frozenset({"binance_realtime_stream"})
_WATCHDOG_MODULE = "l2_scheduler_watchdog"
_PROTECTED_SYSTEM_ROOTS = tuple(
    PurePosixPath(path)
    for path in (
        "/boot",
        "/dev",
        "/etc",
        "/opt/quantos/releases",
        "/opt/quantos/venvs",
        "/proc",
        "/run",
        "/srv/quantos/state",
        "/sys",
        "/usr",
    )
)


def _safe_external_mount(value: str) -> str | None:
    if _SAFE_POSIX_MOUNT.fullmatch(value) is None:
        return None
    mount = PurePosixPath(value)
    if (
        not mount.is_absolute()
        or mount == PurePosixPath("/")
        or os.fspath(mount) != value
        or any(part in {".", ".."} for part in mount.parts)
        or any(root == mount or root in mount.parents or mount in root.parents for root in _PROTECTED_SYSTEM_ROOTS)
    ):
        return None
    return os.fspath(mount)

# The Linux calendar preserves the Windows schedule's local wall-clock and
# dependency offsets. Fixed calendar ticks do not drift with worker duration;
# systemd itself suppresses a new activation while the bound service is active.
CALENDARS: dict[str, tuple[str, str]] = {
    "crypto_factory_loop": ("Sun *-*-* 20:00:00 Asia/Shanghai", "6h"),
    "ic_panel_research": ("Sun *-*-* 20:30:00 Asia/Shanghai", "6h"),
    "paper_alpha_evaluation": ("Sun *-*-* 20:45:00 Asia/Shanghai", "6h"),
    "dry_run_candidate_multi": ("*-*-* 21:10:00 Asia/Shanghai", "30m"),
    "candidate_portfolio_evidence": ("*-*-* 21:45:00 Asia/Shanghai", "10m"),
    "forward_track_advance": ("*-*-* 21:00:00 Asia/Shanghai", "35m"),
    "alpha_maintenance_sweep": ("Mon *-*-* 09:05:00 Asia/Shanghai", "6h"),
    "defillama_tvl_forward_refresh": ("*-*-* 09:30:00 Asia/Shanghai", "10m"),
    "defillama_stablecoin_forward_refresh": ("*-*-* 09:40:00 Asia/Shanghai", "5m"),
    "hyperliquid_public_forward_refresh": ("*-*-* 09:50:00 Asia/Shanghai", "5m"),
    "binance_book_depth_forward_refresh": ("*-*-* 10:00:00 Asia/Shanghai", "30m"),
    "cftc_tff_forward_refresh": ("Tue *-*-* 10:10:00 Asia/Shanghai", "5m"),
    "domain_event_seal": ("*-*-* 02:30:00 Asia/Shanghai", "6h"),
    "artifact_admission_seal": ("*-*-* 02:40:00 Asia/Shanghai", "6h"),
    "l2_backup": ("*-*-* 03:00:00 Asia/Shanghai", "6h"),
    "raw_archive_audit": ("*-*-* 04:00:00 Asia/Shanghai", "2h"),
    "gonogo_report": ("*-*-* 23:00:00 Asia/Shanghai", "6h"),
    "alpha_paper_cycle": ("*-*-* 23:05:00 Asia/Shanghai", "6h"),
    "paper_research_risk_bundle": ("*-*-* 23:08:00 Asia/Shanghai", "6h"),
    "paper_order_preview": ("*-*-* 23:10:00 Asia/Shanghai", "6h"),
    "paper_shadow_auto": ("*-*-* 21:20:00 Asia/Shanghai", "6h"),
}

INTERVALS: dict[str, tuple[str, str]] = {
    "alpha_lifecycle_cockpit": ("*-*-* *:01/5:00 Asia/Shanghai", "6h"),
    "alpha_production_workflow": ("*-*-* *:03/5:00 Asia/Shanghai", "6h"),
    "domain_event_ops": ("*-*-* *:01/15:00 Asia/Shanghai", "5m"),
    "external_heartbeat": ("*-*-* *:03/5:00 Asia/Shanghai", "6h"),
    "paper_realtime": ("*-*-* *:04/5:00 Asia/Shanghai", "6h"),
    "binance_realtime_market": ("*-*-* *:02/5:00 Asia/Shanghai", "6h"),
    "binance_systemic_risk": ("*-*-* *:08/30:00 Asia/Shanghai", "6h"),
    "binance_rpi_depth": ("*-*-* *:10:00 Asia/Shanghai", "6h"),
    "binance_realtime_features": ("*-*-* *:01/5:00 Asia/Shanghai", "45m"),
    "binance_realtime_drift_alerts": ("*-*-* *:02/5:00 Asia/Shanghai", "6h"),
    "realtime_alpha_discovery": ("*-*-* *:05:00 Asia/Shanghai", "30m"),
    "realtime_alpha_forward_shadow": ("*-*-* *:*:00 Asia/Shanghai", "45s"),
    "realtime_alpha_formal_shadow": ("*-*-* *:*:15 Asia/Shanghai", "45s"),
    "realtime_alpha_execution_paper": ("*-*-* *:*:30 Asia/Shanghai", "5m"),
    "paper_multivenue_sor": ("*-*-* *:03/5:00 Asia/Shanghai", "6h"),
    "paper_operational_evidence": ("*-*-* *:07/5:00 Asia/Shanghai", "6h"),
    "paper_realtime_10k": ("*-*-* *:07/5:00 Asia/Shanghai", "6h"),
    "paper_shadow_realtime": ("*-*-* *:06/5:00 Asia/Shanghai", "6h"),
    "paper_health": ("*-*-* *:05/5:00 Asia/Shanghai", "6h"),
}


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _timer_for(module: str, template: str) -> str:
    if module in CALENDARS:
        schedule, _timeout = CALENDARS[module]
        timer_line = f"OnCalendar={schedule}"
    elif module in INTERVALS:
        schedule, _timeout = INTERVALS[module]
        timer_line = f"OnCalendar={schedule}"
    else:
        raise SystemdRenderError("module has no reviewed Linux cadence")
    rendered = (
        template.replace("OnCalendar=*-*-* 00:00:00", timer_line)
        .replace("X-Quantos-Template-State=DO_NOT_INSTALL_UNRENDERED\n", "")
    )
    return rendered.rstrip() + "\n\n[Install]\nWantedBy=timers.target\n"


def _timeout_for(module: str) -> str:
    if module in CALENDARS:
        return CALENDARS[module][1]
    if module in INTERVALS:
        return INTERVALS[module][1]
    raise SystemdRenderError("module has no reviewed Linux timeout")


def render_units(
    *,
    output_root: str | Path,
    release_commit: str,
    modules: Iterable[str],
    backup_mount: str | None = None,
    event_seal_mount: str | None = None,
    heartbeat_url: str | None = None,
) -> tuple[Path, ...]:
    if _COMMIT.fullmatch(release_commit) is None:
        raise SystemdRenderError("release commit must be 40 lowercase hex characters")
    requested = tuple(modules)
    if not requested or len(set(requested)) != len(requested):
        raise SystemdRenderError("requested modules must be non-empty and unique")
    allowed_watchdog_modules = {spec.module for spec in EXPECTED_TASKS.values()}
    for module in requested:
        if (
            _MODULE.fullmatch(module) is None
            or module not in ALLOWED_L2_MODULES
            or module not in allowed_watchdog_modules
            or module in _RESIDENT_MODULES
            or module == _WATCHDOG_MODULE
            or module not in CALENDARS | INTERVALS
        ):
            raise SystemdRenderError("requested module is not a rendered oneshot worker")
    if "l2_backup" in requested:
        if backup_mount is None:
            raise SystemdRenderError("backup mount is required for the backup worker")
        normalized_backup_mount = _safe_external_mount(backup_mount)
        if normalized_backup_mount is None:
            raise SystemdRenderError("backup mount must be an external absolute POSIX path")
    elif backup_mount is not None:
        raise SystemdRenderError("backup mount was provided without the backup worker")
    else:
        normalized_backup_mount = None
    event_modules = {"domain_event_seal", "artifact_admission_seal"}
    if event_modules.intersection(requested):
        if event_seal_mount is None:
            raise SystemdRenderError("event seal mount is required and must be a safe POSIX path")
        normalized_event_mount = _safe_external_mount(event_seal_mount)
        if normalized_event_mount is None:
            raise SystemdRenderError("event seal mount must be an external absolute POSIX path")
    elif event_seal_mount is not None:
        raise SystemdRenderError("event seal mount was provided without an event seal worker")
    else:
        normalized_event_mount = None
    if "external_heartbeat" in requested:
        if heartbeat_url is None or re.fullmatch(
            r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*)?",
            heartbeat_url,
        ) is None:
            raise SystemdRenderError("an explicit safe HTTPS heartbeat URL is required")
    elif heartbeat_url is not None:
        raise SystemdRenderError("heartbeat URL was provided without the heartbeat worker")
    output = Path(output_root)
    if output.exists():
        raise SystemdRenderError("output directory must not already exist")
    output.mkdir(parents=True, mode=0o700)
    service_template = (TEMPLATE_ROOT / "quantos-l2@.service").read_text(encoding="utf-8")
    timer_template = (TEMPLATE_ROOT / "quantos-l2@.timer").read_text(encoding="utf-8")
    written: list[Path] = []
    for module in sorted(requested):
        service = (
            service_template.replace(_RELEASE_TOKEN, release_commit)
            .replace("%i --release-commit", f"{module} --release-commit")
            .replace(" (%i)", f" ({module})")
        )
        timeout = _timeout_for(module)
        service = service.replace("Type=oneshot", f"Type=oneshot\nTimeoutStartSec={timeout}")
        if module == "l2_backup":
            assert normalized_backup_mount is not None
            service = service.replace(
                "After=network-online.target",
                (
                    "After=network-online.target\n"
                    f"RequiresMountsFor={normalized_backup_mount}"
                ),
                1,
            ).replace(
                "Environment=QUANTOS_STATE_ROOT=/srv/quantos/state",
                (
                    "Environment=QUANTOS_STATE_ROOT=/srv/quantos/state\n"
                    f"Environment=QUANTOS_BACKUP_DEST={normalized_backup_mount}\n"
                    "LoadCredential=l2-backup-passphrase:"
                    "/etc/quantos/credentials/l2-backup-passphrase"
                ),
                1,
            ).replace(
                "ReadWritePaths=/srv/quantos/state",
                (
                    "ReadWritePaths=/srv/quantos/state\n"
                    f"ReadWritePaths={normalized_backup_mount}"
                ),
                1,
            )
        if module in event_modules:
            assert normalized_event_mount is not None
            service = service.replace(
                "After=network-online.target",
                "After=network-online.target\n"
                f"RequiresMountsFor={normalized_event_mount}",
                1,
            ).replace(
                "Environment=QUANTOS_STATE_ROOT=/srv/quantos/state",
                "Environment=QUANTOS_STATE_ROOT=/srv/quantos/state\n"
                f"Environment=QUANTOS_EVENT_SEAL_DEST={normalized_event_mount}",
                1,
            ).replace(
                "ReadWritePaths=/srv/quantos/state",
                "ReadWritePaths=/srv/quantos/state\n"
                f"ReadWritePaths={normalized_event_mount}",
                1,
            )
        if module == "external_heartbeat":
            assert heartbeat_url is not None
            service = service.replace(
                "Environment=QUANTOS_STATE_ROOT=/srv/quantos/state",
                "Environment=QUANTOS_STATE_ROOT=/srv/quantos/state\n"
                f"Environment=QUANTOS_HEARTBEAT_URL={heartbeat_url}",
                1,
            )
        timer = (
            _timer_for(module, timer_template)
            .replace(_RELEASE_TOKEN, release_commit)
            .replace("(%i)", f"({module})")
            .replace("@%i.service", f"@{module}.service")
        )
        service_path = output / f"quantos-l2@{module}.service"
        timer_path = output / f"quantos-l2@{module}.timer"
        _atomic_write(service_path, service)
        _atomic_write(timer_path, timer)
        written.extend((service_path, timer_path))
    return tuple(written)


def render_special_units(
    *, output_root: str | Path, release_commit: str
) -> tuple[Path, ...]:
    """Bind the resident stream and watchdog without installing either unit."""

    if _COMMIT.fullmatch(release_commit) is None:
        raise SystemdRenderError("release commit must be 40 lowercase hex characters")
    output = Path(output_root)
    if output.exists():
        raise SystemdRenderError("output directory must not already exist")
    output.mkdir(parents=True, mode=0o700)
    names = (
        "quantos-binance-realtime-stream.service",
        "quantos-l2-watchdog.service",
        "quantos-l2-watchdog.timer",
    )
    written: list[Path] = []
    for name in names:
        content = (TEMPLATE_ROOT / name).read_text(encoding="utf-8")
        if content.count(_RELEASE_TOKEN) == 0:
            raise SystemdRenderError("special unit is missing the release token")
        rendered = content.replace(_RELEASE_TOKEN, release_commit)
        if _RELEASE_TOKEN in rendered:
            raise SystemdRenderError("special unit release binding is incomplete")
        if name == "quantos-binance-realtime-stream.service":
            rendered = rendered.rstrip() + "\n\n[Install]\nWantedBy=multi-user.target\n"
        elif name == "quantos-l2-watchdog.timer":
            rendered = rendered.rstrip() + "\n\n[Install]\nWantedBy=timers.target\n"
        path = output / name
        _atomic_write(path, rendered)
        written.append(path)
    return tuple(written)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render disabled QUANTOS L2 systemd units")
    parser.add_argument("--output", required=True)
    parser.add_argument("--release-commit", required=True)
    parser.add_argument("--module", action="append")
    parser.add_argument("--backup-mount")
    parser.add_argument("--event-seal-mount")
    parser.add_argument("--heartbeat-url")
    parser.add_argument("--special-units", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.special_units:
            if args.module or args.backup_mount or args.event_seal_mount or args.heartbeat_url:
                raise SystemdRenderError(
                    "special-unit rendering cannot include worker options"
                )
            paths = render_special_units(
                output_root=args.output,
                release_commit=args.release_commit,
            )
        else:
            if not args.module:
                raise SystemdRenderError("at least one worker module is required")
            paths = render_units(
                output_root=args.output,
                release_commit=args.release_commit,
                modules=args.module,
                backup_mount=args.backup_mount,
                event_seal_mount=args.event_seal_mount,
                heartbeat_url=args.heartbeat_url,
            )
    except (OSError, UnicodeError, SystemdRenderError) as exc:
        raise SystemExit(str(exc)) from None
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
