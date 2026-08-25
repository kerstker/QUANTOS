"""Static safety contract for the disabled-by-default Linux L2 units."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = ROOT / "deploy" / "systemd"
RENDERER = ROOT / "tools" / "render_systemd_l2_units.py"
SERVICE = SYSTEMD / "quantos-l2@.service"
TIMER = SYSTEMD / "quantos-l2@.timer"
STREAM = SYSTEMD / "quantos-binance-realtime-stream.service"
BYBIT_STREAM = SYSTEMD / "quantos-bybit-realtime-stream.service"
WATCHDOG_SERVICE = SYSTEMD / "quantos-l2-watchdog.service"
WATCHDOG_TIMER = SYSTEMD / "quantos-l2-watchdog.timer"
PROXY_PASS_ENVIRONMENT = (
    "PassEnvironment=HTTP_PROXY HTTPS_PROXY FTP_PROXY NO_PROXY ALL_PROXY "
    "http_proxy https_proxy ftp_proxy no_proxy all_proxy"
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_input_templates_are_not_installable_or_enabled_by_repository_action() -> None:
    files = (
        SERVICE,
        TIMER,
        STREAM,
        BYBIT_STREAM,
        WATCHDOG_SERVICE,
        WATCHDOG_TIMER,
        SYSTEMD / "README.md",
    )
    assert all(path.is_file() for path in files)
    corpus = "\n".join(_text(path) for path in files)
    assert "systemctl enable" not in corpus
    assert "systemctl start" not in corpus
    assert "WantedBy=multi-user.target" not in _text(SERVICE)
    assert "[Install]" not in _text(TIMER)
    assert "[Install]" not in _text(STREAM)
    assert "[Install]" not in _text(BYBIT_STREAM)
    assert "[Install]" not in _text(WATCHDOG_TIMER)


def test_worker_service_binds_immutable_release_and_mutable_state() -> None:
    text = _text(SERVICE)
    assert text.count("SuccessExitStatus=2") == 1
    assert "Environment=QUANTOS_RELEASE_ROOT=/opt/quantos/releases/__QUANTOS_RELEASE_SHA__" in text
    assert "Environment=QUANTOS_RELEASE_COMMIT=__QUANTOS_RELEASE_SHA__" in text
    assert "Environment=QUANTOS_STATE_ROOT=/srv/quantos/state" in text
    assert "WorkingDirectory=/srv/quantos/state" in text
    assert (
        "ExecStart=/opt/quantos/venvs/__QUANTOS_RELEASE_SHA__/bin/python "
        "/opt/quantos/releases/__QUANTOS_RELEASE_SHA__/tools/l2_schedule_entrypoint.py "
        "%i --release-commit __QUANTOS_RELEASE_SHA__"
    ) in text
    assert "ReadOnlyPaths=/opt/quantos/releases/__QUANTOS_RELEASE_SHA__" in text
    assert "ReadWritePaths=/srv/quantos/state" in text


def test_units_pin_l2_safety_and_systemd_hardening() -> None:
    for path in (SERVICE, STREAM, BYBIT_STREAM, WATCHDOG_SERVICE):
        text = _text(path)
        for directive in (
            "User=quantos",
            "Group=quantos",
            "UMask=0077",
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "PrivateTmp=true",
            "CapabilityBoundingSet=",
        ):
            assert directive in text
        assert "EnvironmentFile=" not in text
        for line in (
            "Environment=LIVE_TRADING_ENABLED=false",
            "Environment=REAL_BROKER_ENABLED=false",
            "Environment=ALLOW_ORDER_SUBMISSION=false",
            "Environment=ALLOW_AI_DIRECT_TRADING=false",
            "Environment=REQUIRE_HUMAN_APPROVAL=true",
        ):
            assert text.count(line) == 1
        assert text.count("Environment=QUANTOS_L2_DRY_RUN_ONLY=true") == 1


def test_public_proxy_transport_is_name_only_and_exact() -> None:
    for path in (SERVICE, STREAM, BYBIT_STREAM):
        text = _text(path)
        assert text.count(PROXY_PASS_ENVIRONMENT) == 1
        for name in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "FTP_PROXY",
            "NO_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "ftp_proxy",
            "no_proxy",
            "all_proxy",
        ):
            assert f"Environment={name}=" not in text
    assert "PassEnvironment=" not in _text(WATCHDOG_SERVICE)


def test_resident_stream_is_isolated_and_restarts_without_order_authority() -> None:
    for path, module in (
        (STREAM, "binance_realtime_stream"),
        (BYBIT_STREAM, "bybit_realtime_stream"),
    ):
        text = _text(path)
        assert f"{module} --release-commit __QUANTOS_RELEASE_SHA__" in text
        assert "Restart=always" in text
        assert "RuntimeMaxSec=23h55m" in text
        assert "KillMode=control-group" in text
        assert "ALLOW_ORDER_SUBMISSION=true" not in text


def test_timer_template_is_explicitly_placeholder_only() -> None:
    timer = _text(TIMER)
    assert "OnCalendar=*-*-* 00:00:00" in timer
    assert "Persistent=true" in timer
    assert "AccuracySec=1s" in timer
    assert "RandomizedDelaySec=0" in timer
    assert "DO_NOT_INSTALL_UNRENDERED" in timer


def test_supply_chain_manifest_binds_linux_systemd_surface() -> None:
    from scripts.generate_supply_chain_evidence import _MANIFESTS

    assert {
        "deploy/systemd/README.md",
        "deploy/systemd/quantos-l2@.service",
        "deploy/systemd/quantos-l2@.timer",
        "deploy/systemd/quantos-binance-realtime-stream.service",
        "deploy/systemd/quantos-bybit-realtime-stream.service",
        "deploy/systemd/quantos-l2-watchdog.service",
        "deploy/systemd/quantos-l2-watchdog.timer",
        "tools/render_systemd_l2_units.py",
        "tests/governance/test_systemd_l2_boundary.py",
    } <= set(_MANIFESTS)


def test_renderer_is_non_installing_and_requires_clean_release_contract() -> None:
    text = _text(RENDERER)
    assert "subprocess.run" not in text
    assert "os.system" not in text
    assert "__QUANTOS_RELEASE_SHA__" in text
    assert "ALLOWED_L2_MODULES" in text
    assert "EXPECTED_TASKS" in text


def test_renderer_emits_only_requested_allowlisted_worker(tmp_path: Path) -> None:
    from tools.render_systemd_l2_units import render_units

    release = "a" * 40
    output = tmp_path / "rendered"
    paths = render_units(
        output_root=output,
        release_commit=release,
        modules=("domain_event_ops",),
    )

    assert {path.name for path in paths} == {
        "quantos-l2@domain_event_ops.service",
        "quantos-l2@domain_event_ops.timer",
    }
    service = _text(output / "quantos-l2@domain_event_ops.service")
    timer = _text(output / "quantos-l2@domain_event_ops.timer")
    assert "__QUANTOS_RELEASE_SHA__" not in service + timer
    assert release in service + timer
    assert f"Environment=QUANTOS_RELEASE_COMMIT={release}" in service
    assert service.count(PROXY_PASS_ENVIRONMENT) == 1
    assert "%i" not in service
    assert "domain_event_ops" in service
    assert service.count("SuccessExitStatus=2") == 1
    assert "OnCalendar=*-*-* *:01/15:00" in timer
    assert "DO_NOT_INSTALL_UNRENDERED" not in timer
    assert "WantedBy=timers.target" in timer


def test_renderer_rejects_unknown_resident_and_watchdog_modules(tmp_path: Path) -> None:
    import pytest

    from tools.render_systemd_l2_units import SystemdRenderError, render_units

    for module in ("not_allowlisted", "binance_realtime_stream", "l2_scheduler_watchdog"):
        with pytest.raises(SystemdRenderError):
            render_units(
                output_root=tmp_path / module,
                release_commit="a" * 40,
                modules=(module,),
            )


def test_renderer_requires_and_binds_external_backup_mount(tmp_path: Path) -> None:
    import pytest

    from tools.render_systemd_l2_units import SystemdRenderError, render_units

    with pytest.raises(SystemdRenderError, match="backup mount"):
        render_units(
            output_root=tmp_path / "missing",
            release_commit="a" * 40,
            modules=("l2_backup",),
        )

    output = tmp_path / "bound"
    render_units(
        output_root=output,
        release_commit="a" * 40,
        modules=("l2_backup",),
        backup_mount="/mnt/quantos-backup",
    )
    service = _text(output / "quantos-l2@l2_backup.service")
    assert "RequiresMountsFor=/mnt/quantos-backup" in service
    assert "ReadWritePaths=/mnt/quantos-backup" in service
    assert "Environment=QUANTOS_BACKUP_DEST=/mnt/quantos-backup" in service
    assert (
        "LoadCredential=l2-backup-passphrase:"
        "/etc/quantos/credentials/l2-backup-passphrase"
    ) in service


def test_renderer_rejects_unsafe_backup_mount(tmp_path: Path) -> None:
    import pytest

    from tools.render_systemd_l2_units import SystemdRenderError, render_units

    for mount in (
        "relative",
        "/",
        "/srv/quantos/state/backup",
        "/mnt/has space",
        "/mnt/ok\nUser=root",
        "/mnt/%i",
        "/mnt/../etc",
        "/mnt\\backup",
        "/opt/quantos/releases/backup",
        "/opt/quantos/venvs/backup",
        "/etc/quantos/credentials/backup",
        "/proc/quantos-backup",
    ):
        with pytest.raises(SystemdRenderError):
            render_units(
                output_root=tmp_path / mount.replace("/", "_"),
                release_commit="a" * 40,
                modules=("l2_backup",),
                backup_mount=mount,
            )


def test_renderer_requires_conditional_external_configuration(tmp_path: Path) -> None:
    import pytest

    from tools.render_systemd_l2_units import SystemdRenderError, render_units

    with pytest.raises(SystemdRenderError, match="event seal mount"):
        render_units(
            output_root=tmp_path / "seal-missing",
            release_commit="a" * 40,
            modules=("domain_event_seal",),
        )
    seal = tmp_path / "seal"
    render_units(
        output_root=seal,
        release_commit="a" * 40,
        modules=("domain_event_seal", "artifact_admission_seal"),
        event_seal_mount="/mnt/quantos-seals",
    )
    for module in ("domain_event_seal", "artifact_admission_seal"):
        service = _text(seal / f"quantos-l2@{module}.service")
        assert "RequiresMountsFor=/mnt/quantos-seals" in service
        assert "Environment=QUANTOS_EVENT_SEAL_DEST=/mnt/quantos-seals" in service
        assert "ReadWritePaths=/mnt/quantos-seals" in service

    with pytest.raises(SystemdRenderError, match="HTTPS heartbeat"):
        render_units(
            output_root=tmp_path / "heartbeat-missing",
            release_commit="a" * 40,
            modules=("external_heartbeat",),
        )
    heartbeat = tmp_path / "heartbeat"
    render_units(
        output_root=heartbeat,
        release_commit="a" * 40,
        modules=("external_heartbeat",),
        heartbeat_url="https://heartbeat.internal.example/quantos",
    )
    assert (
        "Environment=QUANTOS_HEARTBEAT_URL=https://heartbeat.internal.example/quantos"
        in _text(heartbeat / "quantos-l2@external_heartbeat.service")
    )


def test_renderer_rejects_external_configuration_injection(tmp_path: Path) -> None:
    import pytest

    from tools.render_systemd_l2_units import SystemdRenderError, render_units

    with pytest.raises(SystemdRenderError):
        render_units(
            output_root=tmp_path / "seal-inject",
            release_commit="a" * 40,
            modules=("domain_event_seal",),
            event_seal_mount="/mnt/seal\nUser=root",
        )
    with pytest.raises(SystemdRenderError):
        render_units(
            output_root=tmp_path / "heartbeat-inject",
            release_commit="a" * 40,
            modules=("external_heartbeat",),
            heartbeat_url="https://ok.example/\nUser=root",
        )


def test_renderer_preserves_one_minute_dependency_offsets(tmp_path: Path) -> None:
    from tools.render_systemd_l2_units import render_units

    output = tmp_path / "offsets"
    render_units(
        output_root=output,
        release_commit="a" * 40,
        modules=(
            "realtime_alpha_forward_shadow",
            "realtime_alpha_formal_shadow",
            "realtime_alpha_execution_paper",
        ),
    )
    assert "OnCalendar=*-*-* *:*:00 Asia/Shanghai" in _text(
        output / "quantos-l2@realtime_alpha_forward_shadow.timer"
    )
    assert "OnCalendar=*-*-* *:*:15 Asia/Shanghai" in _text(
        output / "quantos-l2@realtime_alpha_formal_shadow.timer"
    )
    assert "OnCalendar=*-*-* *:*:30 Asia/Shanghai" in _text(
        output / "quantos-l2@realtime_alpha_execution_paper.timer"
    )


def test_watchdog_has_dedicated_hardened_unit_and_timer() -> None:
    service = _text(WATCHDOG_SERVICE)
    timer = _text(WATCHDOG_TIMER)
    assert "l2_scheduler_watchdog --release-commit __QUANTOS_RELEASE_SHA__" in service
    assert "WorkingDirectory=/srv/quantos/state" in service
    assert "Environment=QUANTOS_SCHEDULER_PROVIDER=systemd" in service
    assert (
        "Environment=QUANTOS_WIKIMEDIA_ATTENTION_RELEASE_COMMIT="
        "__QUANTOS_ATTENTION_RELEASE_SHA__"
    ) in service
    assert "NoNewPrivileges=true" in service
    assert "CapabilityBoundingSet=" in service
    assert "OnUnitInactiveSec=15m" in timer
    assert "Persistent=true" in timer


def test_renderer_binds_resident_stream_and_watchdog_without_installing(
    tmp_path: Path,
) -> None:
    from tools.render_systemd_l2_units import render_special_units

    release = "b" * 40
    output = tmp_path / "special"
    paths = render_special_units(output_root=output, release_commit=release)

    assert {path.name for path in paths} == {
        "quantos-binance-realtime-stream.service",
        "quantos-bybit-realtime-stream.service",
        "quantos-l2-watchdog.service",
        "quantos-l2-watchdog.timer",
    }
    corpus = "\n".join(_text(path) for path in paths)
    assert "__QUANTOS_RELEASE_SHA__" not in corpus
    assert "__QUANTOS_ATTENTION_RELEASE_SHA__" not in corpus
    assert release in corpus
    assert "systemctl enable" not in corpus
    assert "systemctl start" not in corpus
    assert "WantedBy=multi-user.target" in _text(
        output / "quantos-binance-realtime-stream.service"
    )
    assert _text(output / "quantos-binance-realtime-stream.service").count(
        PROXY_PASS_ENVIRONMENT
    ) == 1
    assert "WantedBy=multi-user.target" in _text(
        output / "quantos-bybit-realtime-stream.service"
    )
    assert _text(output / "quantos-bybit-realtime-stream.service").count(
        PROXY_PASS_ENVIRONMENT
    ) == 1
    assert "PassEnvironment=" not in _text(
        output / "quantos-l2-watchdog.service"
    )
    assert "WantedBy=timers.target" in _text(
        output / "quantos-l2-watchdog.timer"
    )


def test_renderer_can_pin_frozen_attention_collector_independently(
    tmp_path: Path,
) -> None:
    from tools.render_systemd_l2_units import render_special_units

    release = "b" * 40
    attention_release = "c" * 40
    output = tmp_path / "special"
    render_special_units(
        output_root=output,
        release_commit=release,
        attention_release_commit=attention_release,
    )

    service = _text(output / "quantos-l2-watchdog.service")
    assert f"Environment=QUANTOS_RELEASE_COMMIT={release}" in service
    assert (
        "Environment=QUANTOS_WIKIMEDIA_ATTENTION_RELEASE_COMMIT="
        f"{attention_release}"
    ) in service
