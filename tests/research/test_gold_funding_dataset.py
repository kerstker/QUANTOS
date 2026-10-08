import json
from decimal import Decimal as D

import pytest

from services.research.gold_funding_dataset import load_funding


def rows():
    return [dict(symbol='XAUUSDT', fundingTime=t, fundingRate=rate, markPrice='100')
            for t, rate in ((100, '.001'), (200, '-.002'))]


def test_signed_rates_convert_to_original_ledger_events_without_cash_invention():
    events = load_funding(json.dumps(rows()).encode(), start_ms=0, end_ms=300,
                          requested_at_ms=400, received_at_ms=410)
    assert [e.funding_rate for e in events] == [D('.001'), D('-.002')]
    assert [e.timestamp for e in events] == [100, 200]
    assert all(e.instrument_id == 'XAUUSDT' for e in events)


@pytest.mark.parametrize('change', [dict(symbol='BTCUSDT'), dict(fundingTime=500),
                                   dict(fundingRate='NaN'), dict(markPrice='0')])
def test_bad_funding_rejected(change):
    values = rows()
    values[0].update(change)
    with pytest.raises(ValueError):
        load_funding(json.dumps(values).encode(), start_ms=0, end_ms=300,
                      requested_at_ms=400, received_at_ms=410)


def test_duplicates_and_full_page_require_resolution_not_silent_success():
    for values in (rows() + rows(), [rows()[0]] * 1000):
        with pytest.raises(ValueError):
            load_funding(json.dumps(values).encode(), start_ms=0, end_ms=300,
                          requested_at_ms=400, received_at_ms=410)


def test_explicit_regular_rate_type_is_supported_but_other_types_are_not_guessed():
    values = rows()
    for row in values:
        row['rateType'] = 'Regular'
    assert len(load_funding(json.dumps(values).encode(), start_ms=0, end_ms=300,
                            requested_at_ms=400, received_at_ms=410)) == 2
    values[0]['rateType'] = 'Unknown'
    with pytest.raises(ValueError):
        load_funding(json.dumps(values).encode(), start_ms=0, end_ms=300,
                      requested_at_ms=400, received_at_ms=410)


def test_observed_funding_retains_publication_clock_and_exact_source():
    import hashlib
    from services.research.gold_funding_dataset import load_funding_observations
    raw = json.dumps(rows()).encode()
    observed = load_funding_observations(raw, start_ms=0, end_ms=300,
        requested_at_ms=400, received_at_ms=410)
    assert [row.event.timestamp for row in observed] == [100, 200]
    assert all(row.available_at_ms == 410 for row in observed)
    assert all(row.requested_at_ms == 400 for row in observed)
    assert all(row.source_sha256 == hashlib.sha256(raw).hexdigest() for row in observed)
    assert observed[0].event.funding_rate == D('.001')
    assert observed[0].as_document()['settlement_at_ms'] == 100
    assert observed[0].as_document()['available_at_ms'] == 410
    assert observed[0].as_document()['account_cash_verified'] is False


def test_observed_funding_cannot_backdate_availability_or_change_source():
    from dataclasses import replace
    from services.research.gold_funding_dataset import load_funding_observations
    row = load_funding_observations(json.dumps(rows()).encode(), start_ms=0,
        end_ms=300, requested_at_ms=400, received_at_ms=410)[0]
    for change in ({'available_at_ms': 99}, {'requested_at_ms': 99},
                   {'source_sha256': 'not-a-digest'}):
        with pytest.raises(ValueError):
            replace(row, **change)
