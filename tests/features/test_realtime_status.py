from services.features.realtime_status import assess_realtime_feature_freshness


def _status(*, generated: int = 1_000, observed: int = 900) -> dict:
    return {
        "status": "PASS",
        "generated_at_ms": generated,
        "source": {"last_observed_at_ms": observed},
        "batch": {"chain_valid": True},
    }


def test_freshness_requires_recent_completion_and_recent_source() -> None:
    result = assess_realtime_feature_freshness(
        _status(),
        now_ms=1_100,
        max_age_ms=500,
    )

    assert result.ready is True
    assert result.reason == "FRESH"
    assert result.status_age_ms == 100
    assert result.source_age_ms == 200


def test_recent_completion_cannot_refresh_stale_source() -> None:
    result = assess_realtime_feature_freshness(
        _status(generated=10_000, observed=1_000),
        now_ms=10_100,
        max_age_ms=500,
    )

    assert result.ready is False
    assert result.reason == "SOURCE_WATERMARK_STALE"


def test_missing_or_post_completion_source_watermark_blocks() -> None:
    missing = _status()
    missing.pop("source")
    assert assess_realtime_feature_freshness(
        missing, now_ms=1_100, max_age_ms=500
    ).reason == "SOURCE_WATERMARK_MISSING"
    assert assess_realtime_feature_freshness(
        _status(generated=1_000, observed=1_001),
        now_ms=1_100,
        max_age_ms=500,
    ).reason == "SOURCE_WATERMARK_AFTER_COMPLETION"
