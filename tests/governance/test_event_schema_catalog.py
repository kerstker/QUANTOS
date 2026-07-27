import json
from pathlib import Path

from quantos.contracts.events import (
    EVENT_ALPHA_PROMOTED,
    EVENT_DEPLOYMENT_RECORDED,
    EVENT_KILL_SWITCH_ACTIVATED,
    EVENT_KILL_SWITCH_RESET,
    EVENT_TIMESTAMP_UNIT_MICROSECONDS,
    EVENT_TIMESTAMP_UNIT_MILLISECONDS,
)
from scripts.generate_event_schema_catalog import (
    build_event_schema_catalog,
    event_schema_catalog_is_current,
    write_event_schema_catalog,
)


ROOT = Path(__file__).resolve().parents[2]


def test_catalog_is_deterministic_and_latest_versions_use_microseconds():
    first = build_event_schema_catalog(ROOT)
    second = build_event_schema_catalog(ROOT)

    assert first == second
    assert first["verdict"] == "PASS"
    assert first["event_type_count"] == 13
    assert first["schema_definition_count"] == 18
    assert first["canonical_latest_timestamp_unit"] == (
        EVENT_TIMESTAMP_UNIT_MICROSECONDS
    )
    assert first["latest_timestamp_units_all_canonical"] is True
    assert {
        (item["event_type"], item["version"], item["timestamp_unit"])
        for item in first["legacy_timestamp_unit_versions"]
    } == {
        (
            EVENT_ALPHA_PROMOTED,
            "1.0.0",
            EVENT_TIMESTAMP_UNIT_MILLISECONDS,
        ),
        (
            EVENT_DEPLOYMENT_RECORDED,
            "1.0.0",
            EVENT_TIMESTAMP_UNIT_MILLISECONDS,
        ),
        (
            EVENT_KILL_SWITCH_ACTIVATED,
            "1.0.0",
            EVENT_TIMESTAMP_UNIT_MILLISECONDS,
        ),
        (
            EVENT_KILL_SWITCH_RESET,
            "1.0.0",
            EVENT_TIMESTAMP_UNIT_MILLISECONDS,
        ),
    }
    for event in first["schemas"].values():
        for definition in event["definitions"]:
            assert len(definition["payload_schema_sha256"]) == 64
            assert definition["payload_schema"]["additionalProperties"] is False


def test_catalog_current_check_rejects_tampering(tmp_path):
    path = tmp_path / "event-schema-catalog.json"
    document = build_event_schema_catalog(ROOT)
    write_event_schema_catalog(path, document)

    assert event_schema_catalog_is_current(ROOT, path)

    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["schemas_sha256"] = "0" * 64
    path.write_text(json.dumps(tampered), encoding="utf-8")
    assert not event_schema_catalog_is_current(ROOT, path)


def test_catalog_binds_all_timestamp_producers_and_normalizer():
    document = build_event_schema_catalog(ROOT)
    assert {
        "quantos/contracts/events.py",
        "quantos/contracts/event_schemas.py",
        "live/execution/shadow_store.py",
        "services/alpha/events.py",
        "services/alpha/maintenance_events.py",
        "services/console/api/system_views.py",
        "services/data/admission_store.py",
        "services/data/events.py",
        "services/data/refresh_history.py",
        "services/deployment/events.py",
        "services/deployment/runtime.py",
        "services/deployment/store.py",
        "services/execution/events.py",
        "services/execution/dry_run_store.py",
        "services/monitoring/domain_event_runtime.py",
        "services/monitoring/event_health.py",
        "services/risk/pre_trade/events.py",
        "services/risk/pre_trade/kill_switch_store.py",
        "services/risk/research_risk/events.py",
        "scripts/generate_event_schema_catalog.py",
        "tools/data_quality_admission.py",
    } == set(document["implementation_sha256"])
