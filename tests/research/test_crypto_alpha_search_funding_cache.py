import json

import pytest

from tools.binance_funding_fetch import FundingDataError
from tools.crypto_alpha_search import _load_funding

_DAY = 86_400_000
_NOW = 10 * _DAY


def _complete(rate=0.001):
    return {day * _DAY: rate for day in range(7, 10)}


def test_existing_funding_cache_is_validated_before_use(tmp_path):
    path = tmp_path / "funding.json"
    path.write_text(
        json.dumps({"BTC": {str(7 * _DAY): 0.001, str(9 * _DAY): 0.001}}),
        encoding="utf-8",
    )

    with pytest.raises(FundingDataError, match="completeness"):
        _load_funding(["BTC"], _NOW, cache_path=str(path))


def test_failed_fetch_does_not_publish_partial_funding_cache(tmp_path):
    path = tmp_path / "funding.json"

    def fetch(ticker, *, days, now_ms):
        if ticker == "ETH":
            raise RuntimeError("offline")
        return _complete()

    with pytest.raises(RuntimeError, match="offline"):
        _load_funding(
            ["BTC", "ETH"], _NOW, fetch=fetch, cache_path=str(path), sleep=0
        )

    assert not path.exists()


def test_valid_funding_cache_round_trip(tmp_path):
    path = tmp_path / "funding.json"

    out = _load_funding(
        ["BTC", "ETH"],
        _NOW,
        fetch=lambda ticker, **_kwargs: _complete(0.001 if ticker == "BTC" else -0.001),
        cache_path=str(path),
        sleep=0,
    )

    assert set(out) == {"BTC", "ETH"}
    assert _load_funding(["BTC", "ETH"], _NOW, cache_path=str(path)) == out
