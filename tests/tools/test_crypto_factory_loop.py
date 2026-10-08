from __future__ import annotations

import pytest

from tools.crypto_factory_loop import next_search_recorded_at


def test_search_clock_uses_post_build_observation_and_advances_same_tick() -> None:
    post_build = 1_785_000_240_000

    first = next_search_recorded_at(previous=None, observed_at=post_build)
    second = next_search_recorded_at(previous=first, observed_at=post_build)
    later = next_search_recorded_at(previous=second, observed_at=post_build + 50)

    assert first == post_build
    assert second == post_build + 1
    assert later == post_build + 50


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_search_clock_rejects_invalid_authority_times(value: object) -> None:
    with pytest.raises(ValueError):
        next_search_recorded_at(previous=None, observed_at=value)  # type: ignore[arg-type]
