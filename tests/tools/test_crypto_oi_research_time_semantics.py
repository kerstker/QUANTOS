from tools.crypto_oi_research import oi_field_as_of


def test_oi_join_requires_explicit_availability_not_the_daily_cache_key():
    record = {
        "global_ls": 1.25,
        "source_event_time_ms": 86_100_000,
        "available_time_ms": 86_400_000,
        "ingestion_time_ms": 86_400_000,
    }

    assert oi_field_as_of(record, "global_ls", decision_time_ms=86_399_999) is None
    assert oi_field_as_of(record, "global_ls", decision_time_ms=86_400_000) == 1.25


def test_oi_join_fails_closed_for_legacy_records_without_time_provenance():
    assert oi_field_as_of(
        {"global_ls": 1.25},
        "global_ls",
        decision_time_ms=86_400_000,
    ) is None


def test_oi_join_rejects_backdated_availability_before_actual_ingestion():
    assert oi_field_as_of(
        {
            "global_ls": 1.25,
            "source_event_time_ms": 86_100_000,
            "available_time_ms": 86_400_000,
            "ingestion_time_ms": 100_000_000,
        },
        "global_ls",
        decision_time_ms=100_000_000,
    ) is None

    assert oi_field_as_of(
        {
            "global_ls": 1.25,
            "source_event_time_ms": 86_100_000,
            "available_time_ms": 100_000_000,
            "ingestion_time_ms": 100_000_000,
        },
        "global_ls",
        decision_time_ms=100_000_000,
    ) == 1.25
