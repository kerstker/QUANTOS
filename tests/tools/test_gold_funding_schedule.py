import hashlib
import json

import pytest

from tools import gold_bar_capture as capture


def setup(monkeypatch, *, event=1000, next_time=2000, received=1010, symbol='XAUUSDT'):
    def fetch(out, endpoint, requested):
        assert endpoint == '/fapi/v1/premiumIndex?symbol=XAUUSDT'
        raw = json.dumps(dict(symbol=symbol, time=event, nextFundingTime=next_time)).encode()
        (out / 'response.json').write_bytes(raw)
        capture._write(out / 'receipt.json', dict(requested_at_ms=1001,
            received_at_ms=received, response_sha256=hashlib.sha256(raw).hexdigest()))
        return raw, received
    monkeypatch.setattr(capture, '_fetch', fetch)


def test_schedule_keeps_forecast_distinct_from_settlement(tmp_path, monkeypatch):
    setup(monkeypatch)
    result = capture.capture_funding_schedule(tmp_path / 'out')
    assert result['next_settlement_nominal_ms'] == 2000
    assert result['available_at_ms'] == 1010
    assert result['status'] == 'FORECAST_OBSERVED'
    assert result['actual_settlement_time_proven'] is False
    assert result['schedule_completeness_proven'] is False
    assert result['submission_enabled'] is False


def test_expired_forecast_is_retained_as_expired(tmp_path, monkeypatch):
    setup(monkeypatch, next_time=1005)
    result = capture.capture_funding_schedule(tmp_path / 'out')
    assert result['status'] == 'FORECAST_EXPIRED_ON_RECEIPT'
    assert (tmp_path / 'out/response.json').exists()


@pytest.mark.parametrize('kwargs', [{'event': 1011}, {'received': 31001},
                                    {'symbol': 'BTCUSDT'}, {'next_time': True}])
def test_invalid_clock_or_symbol_is_not_an_admitted_forecast(tmp_path, monkeypatch, kwargs):
    setup(monkeypatch, **kwargs)
    with pytest.raises(ValueError):
        capture.capture_funding_schedule(tmp_path / 'out')
    assert (tmp_path / 'out/response.json').exists()
