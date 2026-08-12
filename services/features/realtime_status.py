"""Fail-closed freshness contract for realtime feature status artifacts."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class RealtimeFeatureFreshness:
    ready: bool
    reason: str
    status_age_ms: int | None
    source_age_ms: int | None
    generated_at_ms: int | None
    source_last_observed_at_ms: int | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_realtime_feature_freshness(
    status: Mapping[str, Any],
    *,
    now_ms: int,
    max_age_ms: int,
) -> RealtimeFeatureFreshness:
    """Require both a recently completed PASS and recent public source data."""
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise ValueError("now_ms must be a non-negative integer")
    if (
        isinstance(max_age_ms, bool)
        or not isinstance(max_age_ms, int)
        or max_age_ms < 0
    ):
        raise ValueError("max_age_ms must be a non-negative integer")

    generated = status.get("generated_at_ms")
    source = status.get("source")
    source_observed = (
        source.get("last_observed_at_ms") if isinstance(source, Mapping) else None
    )

    def result(
        ready: bool,
        reason: str,
        *,
        status_age: int | None = None,
        source_age: int | None = None,
    ) -> RealtimeFeatureFreshness:
        return RealtimeFeatureFreshness(
            ready=ready,
            reason=reason,
            status_age_ms=status_age,
            source_age_ms=source_age,
            generated_at_ms=(generated if type(generated) is int else None),
            source_last_observed_at_ms=(
                source_observed if type(source_observed) is int else None
            ),
        )

    if status.get("status") != "PASS":
        return result(False, "STATUS_NOT_PASS")
    if type(generated) is not int:
        return result(False, "STATUS_COMPLETION_TIME_MISSING")
    status_age = now_ms - generated
    if status_age < 0:
        return result(False, "STATUS_COMPLETION_TIME_IN_FUTURE", status_age=status_age)
    if status_age > max_age_ms:
        return result(False, "STATUS_COMPLETION_STALE", status_age=status_age)
    batch = status.get("batch")
    if not isinstance(batch, Mapping) or batch.get("chain_valid") is not True:
        return result(False, "BATCH_CHAIN_INVALID", status_age=status_age)
    if type(source_observed) is not int:
        return result(False, "SOURCE_WATERMARK_MISSING", status_age=status_age)
    if source_observed > generated:
        return result(
            False,
            "SOURCE_WATERMARK_AFTER_COMPLETION",
            status_age=status_age,
            source_age=now_ms - source_observed,
        )
    source_age = now_ms - source_observed
    if source_age < 0:
        return result(
            False,
            "SOURCE_WATERMARK_IN_FUTURE",
            status_age=status_age,
            source_age=source_age,
        )
    if source_age > max_age_ms:
        return result(
            False,
            "SOURCE_WATERMARK_STALE",
            status_age=status_age,
            source_age=source_age,
        )
    return result(
        True,
        "FRESH",
        status_age=status_age,
        source_age=source_age,
    )


__all__ = ["RealtimeFeatureFreshness", "assess_realtime_feature_freshness"]
