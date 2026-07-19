"""P2-5:关键事件 producer 必须真实接线，不能只留下孤立总线原语。"""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCERS = (
    ROOT / "services" / "execution" / "events.py",
    ROOT / "services" / "alpha" / "maintenance_events.py",
    ROOT / "services" / "alpha" / "events.py",
    ROOT / "services" / "risk" / "pre_trade" / "events.py",
    ROOT / "services" / "deployment" / "events.py",
    ROOT / "services" / "data" / "events.py",
    ROOT / "services" / "risk" / "research_risk" / "events.py",
)
DRIVERS = (
    "dry_run_carry.py",
    "dry_run_carry_multi.py",
    "dry_run_trend_multi.py",
    "dry_run_crowd_contra_multi.py",
    "dry_run_crowd_contra_signal_adapter_multi.py",
    "dry_run_combo_multi.py",
    "dry_run_combo_riskparity_multi.py",
)


def _imports_event(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "quantos.contracts.events"
        and any(alias.name == "Event" for alias in node.names)
        for node in ast.walk(tree)
    )


def test_load_bearing_service_domains_produce_canonical_events():
    assert all(path.is_file() for path in PRODUCERS)
    assert all(_imports_event(path) for path in PRODUCERS)


def test_all_official_dry_run_drivers_open_the_shared_durable_bus():
    for filename in DRIVERS:
        source = (ROOT / "tools" / filename).read_text(encoding="utf-8")
        assert "open_domain_event_bus" in source, filename
        assert "event_bus=event_bus" in source, filename
        assert "publish_completion_event=False" in source, filename
        assert "save_and_project_dry_run" in source, filename
        assert "save_and_project_dry_run(" in source, filename


def test_alpha_maintenance_sweep_opens_the_same_durable_bus():
    source = (ROOT / "tools" / "alpha_maintenance_sweep.py").read_text(encoding="utf-8")
    assert "open_domain_event_bus" in source
    assert "event_bus=event_bus" in source
    assert "project_pending_domain_events" in source
    assert "project_pending_domain_events(event_bus" in source
    assert "MaintenanceStore" in source
    assert "dispatch_maintenance_event_outbox" in source
    assert "publish_maintenance_events" not in source


def test_promotion_state_and_event_share_the_experiment_store_transaction():
    promotion = (ROOT / "services" / "alpha" / "promotion.py").read_text(
        encoding="utf-8"
    )
    registry = (
        ROOT / "services" / "research" / "experiment_registry" / "registry.py"
    ).read_text(encoding="utf-8")
    store = (
        ROOT / "services" / "research" / "experiment_registry" / "store.py"
    ).read_text(encoding="utf-8")
    assert "EventOutboxMessage" in promotion
    assert "event_outbox=(" in promotion
    assert "save_catalog_snapshot" in registry
    assert "BEGIN IMMEDIATE" in store
    assert "experiment_event_outbox" in store


def test_kill_switch_and_deployment_decisions_have_fail_closed_event_hooks():
    kill_switch = (
        ROOT / "services" / "risk" / "pre_trade" / "kill_switch.py"
    ).read_text(encoding="utf-8")
    deployment = (ROOT / "services" / "deployment" / "gate.py").read_text(
        encoding="utf-8"
    )
    assert "kill_switch_reset_event" in kill_switch
    reset_path = kill_switch.split("def reset(", 1)[1].split(
        "def reset_with_grant", 1
    )[0]
    assert reset_path.index("event_sink.publish_once") < reset_path.index(
        'model_copy(update={"tripped": False'
    )
    assert "deployment_recorded_event" in deployment
    assert "event_sink.publish_once" in deployment


def test_official_kill_switch_entrypoints_use_transactional_authority():
    sentinel = (ROOT / "tools" / "live_risk_sentinel.py").read_text(
        encoding="utf-8"
    )
    driver = (ROOT / "tools" / "live_track_advance.py").read_text(
        encoding="utf-8"
    )
    reset = (ROOT / "tools" / "live_kill_switch_reset.py").read_text(
        encoding="utf-8"
    )
    for name, source in (("sentinel", sentinel), ("driver", driver)):
        assert "open_authoritative_kill_switch_store" in source, name
        assert "respond_to_breach_persisted" in source, name
        assert "save_kill_switch_state_if_tripped" not in source, name
    assert "open_authoritative_kill_switch_store" in reset
    assert "reset_with_grant_and_record" in reset
    assert "save_kill_switch_state_if_tripped" not in reset


def test_kill_switch_state_and_event_share_one_sqlite_transaction():
    store = (
        ROOT / "services" / "risk" / "pre_trade" / "kill_switch_store.py"
    ).read_text(encoding="utf-8")
    runtime = (
        ROOT / "services" / "risk" / "pre_trade" / "kill_switch_runtime.py"
    ).read_text(encoding="utf-8")
    assert "BEGIN IMMEDIATE" in store
    assert "kill_switch_transitions" in store
    assert "event_json" in store
    assert "event_outbox=EventOutboxMessage" in runtime


def test_domain_event_bus_tool_has_no_network_or_order_capability():
    source = (ROOT / "tools" / "domain_event_bus.py").read_text(encoding="utf-8")
    for forbidden in (
        "urllib", "socket", "requests", "httpx", "aiohttp", "place_order",
        "submit_order", "withdraw", "transfer(",
    ):
        assert forbidden not in source


def test_projection_runtime_uses_load_bearing_consumer_group_coordination():
    source = (
        ROOT / "services" / "monitoring" / "domain_event_runtime.py"
    ).read_text(encoding="utf-8")
    assert "DOMAIN_EVENT_PROJECTION_PARTITIONS = 1" in source
    assert "heartbeat_consumer_group(" in source
    assert "claim_partition(" in source
    assert "leave_consumer_group(" in source
    assert ".claim_next(" not in source


def test_consumer_group_coordinator_has_no_network_or_execution_capability():
    source = (
        ROOT / "services" / "monitoring" / "consumer_group.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "urllib",
        "socket",
        "requests",
        "httpx",
        "aiohttp",
        "place_order",
        "submit_order",
        "withdraw",
        "transfer(",
        "services.trading",
        "live.",
    ):
        assert forbidden not in source


def test_deployment_authority_is_transactional_and_non_executing():
    store = (ROOT / "services" / "deployment" / "store.py").read_text(
        encoding="utf-8"
    )
    runtime = (ROOT / "services" / "deployment" / "runtime.py").read_text(
        encoding="utf-8"
    )
    domain_runtime = (
        ROOT / "services" / "monitoring" / "domain_event_runtime.py"
    ).read_text(encoding="utf-8")
    tool = (ROOT / "tools" / "deployment_events.py").read_text(
        encoding="utf-8"
    )
    assert "BEGIN IMMEDIATE" in store
    assert "deployment_evaluations" in store
    assert "event_json" in store
    assert "ELIGIBLE_NOT_APPLIED" in store
    assert '"DEPLOYED"' not in store
    assert "evaluate_deployment(" in runtime
    assert "record_evaluation(" in runtime
    assert "publish_once(" not in runtime
    assert "dispatch_deployment_event_outbox" in domain_runtime
    assert "dispatch_deployment_event_outbox" in tool
    assert "project_pending_domain_events" in tool
    for source in (store, runtime, tool):
        for forbidden in (
            "urllib",
            "socket",
            "requests",
            "httpx",
            "aiohttp",
            "place_order",
            "submit_order",
            "withdraw",
            "services.trading",
            "live.",
        ):
            assert forbidden not in source


def test_data_refresh_history_and_event_share_one_sqlite_transaction():
    store = (ROOT / "services" / "data" / "refresh_history.py").read_text(
        encoding="utf-8"
    )
    driver = (ROOT / "tools" / "live_data_refresh.py").read_text(
        encoding="utf-8"
    )
    runtime = (
        ROOT / "services" / "monitoring" / "domain_event_runtime.py"
    ).read_text(encoding="utf-8")
    assert "BEGIN IMMEDIATE" in store
    assert "refresh_event_outbox" in store
    assert "data_refresh_recorded_event" in store
    assert "dispatch_refresh_event_outbox" in driver
    assert "project_pending_domain_events" in driver
    assert "dispatch_refresh_event_outbox" in runtime


def test_dry_run_research_risk_event_binds_the_exact_run_snapshot():
    store = (ROOT / "services" / "execution" / "dry_run_store.py").read_text(
        encoding="utf-8"
    )
    runtime = (ROOT / "tools" / "domain_event_bus.py").read_text(
        encoding="utf-8"
    )
    assert "EVENT_RESEARCH_RISK_EVALUATED" in store
    assert "run_repro_hash" in store
    assert "research_risk_evaluated_event" in runtime
    assert "save_run_with_outbox" in runtime


def test_event_health_is_alert_only_and_has_no_execution_or_compaction_capability():
    health = (ROOT / "services" / "monitoring" / "event_health.py").read_text(
        encoding="utf-8"
    )
    assert "APPEND_ONLY_ALERT_ONLY" in health
    for forbidden in (
        "DELETE FROM",
        "VACUUM",
        "urllib",
        "socket",
        "requests",
        "httpx",
        "place_order",
        "submit_order",
        "services.trading",
        "live.",
    ):
        assert forbidden not in health
    tool = (ROOT / "tools" / "domain_event_health.py").read_text(
        encoding="utf-8"
    )
    assert "read_domain_event_bus_health" in tool
    assert "alert_emitter" in tool
    for forbidden in (
        "DELETE FROM",
        "VACUUM",
        "place_order",
        "submit_order",
        "withdraw",
        "services.trading",
        "live.",
    ):
        assert forbidden not in tool


def test_unified_outbox_recovery_has_seven_sources_and_no_execution_capability():
    source = (
        ROOT / "tools" / "domain_event_outbox_drain.py"
    ).read_text(encoding="utf-8")
    for dispatch in (
        "dispatch_dry_run_outbox",
        "dispatch_experiment_event_outbox",
        "dispatch_maintenance_event_outbox",
        "dispatch_kill_switch_event_outbox",
        "dispatch_deployment_event_outbox",
        "dispatch_refresh_event_outbox",
        "dispatch_data_quality_admission_event_outbox",
    ):
        assert dispatch in source
    assert source.count("OutboxSource(") == 7
    assert source.count("create=False") == 7
    assert "create=not event_authority_exists" in source
    assert source.count("resolved_projector(") == 1
    for forbidden in (
        "urllib",
        "socket",
        "requests",
        "httpx",
        "aiohttp",
        "place_order",
        "submit_order",
        "withdraw",
        "transfer(",
        "services.trading",
        "live.",
        "DELETE FROM",
        "VACUUM",
    ):
        assert forbidden not in source


def test_scheduled_domain_event_ops_is_l2_only_and_fail_closed():
    source = (ROOT / "tools" / "domain_event_ops.py").read_text(
        encoding="utf-8"
    )
    assert source.index("drain_runner(") < source.index("health_runner(")
    assert "L2_DRY_RUN_EVENT_OPERATIONS" in source
    assert "DOMAIN_EVENT_DRAIN_EXCEPTION" in source
    assert "DOMAIN_EVENT_HEALTH_EXCEPTION" in source
    assert '"order_submission": False' in source
    assert '"broker_connectivity": False' in source
    for forbidden in (
        "place_order",
        "submit_order",
        "withdraw",
        "transfer(",
        "services.trading",
        "live.",
        "DELETE FROM",
        "VACUUM",
    ):
        assert forbidden not in source


def test_owner_store_migration_is_staged_and_has_no_execution_capability():
    source = (
        ROOT / "tools" / "domain_event_owner_migration.py"
    ).read_text(encoding="utf-8")
    assert source.index("spec.migrate(stage_path)") < source.index(
        "os.replace(stage_path, spec.path)"
    )
    assert source.index("staged.protected != before.protected") < source.index(
        "os.replace(stage_path, spec.path)"
    )
    assert "immutable=1" in source
    assert "ActiveSqliteSidecarError" in source
    assert '"order_submission": False' in source
    assert '"broker_connectivity": False' in source
    for forbidden in (
        "place_order",
        "submit_order",
        "withdraw",
        "services.trading",
        "live.",
        "urllib",
        "requests",
        "httpx",
    ):
        assert forbidden not in source


def test_no_production_caller_bypasses_authoritative_deployment_runtime():
    allowed = {ROOT / "services" / "deployment" / "runtime.py"}
    offenders = []
    for root_name in ("services", "live", "tools"):
        for path in (ROOT / root_name).rglob("*.py"):
            if path in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            calls_gate = any(
                isinstance(node, ast.Call)
                and (
                    (
                        isinstance(node.func, ast.Name)
                        and node.func.id == "evaluate_deployment"
                    )
                    or (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr == "evaluate_deployment"
                    )
                )
                for node in ast.walk(tree)
            )
            if calls_gate:
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []
