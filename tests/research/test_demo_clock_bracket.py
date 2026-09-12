"""Clock domains stay separate; brackets never claim exact transport latency."""
from copy import deepcopy

import pytest

from quantos.contracts.binance_demo_clock import (
    bracket_bounds, event_local_interval, compatible_event, SCHEMA,
)


def bracket(offset=1000):
    return {'schema': SCHEMA, 'provenance': 'SYNTHETIC_TEST_FIXTURE',
        'before': {'requested_at_ms': 10000, 'received_at_ms': 10020,
            'server_time_ms': 10010+offset, 'response_sha256': 'a'*64, 'source_sha256': 'b'*64},
        'after': {'requested_at_ms': 10300, 'received_at_ms': 10320,
            'server_time_ms': 10310+offset, 'response_sha256': 'c'*64, 'source_sha256': 'd'*64}}


def test_positive_offset_does_not_make_a_received_event_come_from_the_future():
    raw=bracket(); before=deepcopy(raw)
    bounds=bracket_bounds(raw, start_ms=10030, end_ms=10290)
    assert raw==before
    assert bounds['offset_lower_ms']==489 and bounds['offset_upper_ms']==1511
    assert event_local_interval(11100,bounds)==(9589,10611)
    assert compatible_event(11100,claimed_at_ms=10040,received_at_ms=10110,bounds=bounds)
    assert bounds['exact_latency_ms'] is None and bounds['source_verified'] is False
    assert bounds['interior_drift_verified'] is False


@pytest.mark.parametrize('offset',[-1000,0,1000])
def test_offset_sign_does_not_change_local_availability(offset):
    bounds=bracket_bounds(bracket(offset), start_ms=10030, end_ms=10290)
    assert compatible_event(10100+offset,claimed_at_ms=10040,received_at_ms=10110,bounds=bounds)
    assert not compatible_event(9000+offset,claimed_at_ms=10040,received_at_ms=10110,bounds=bounds)
    assert not compatible_event(12000+offset,claimed_at_ms=10040,received_at_ms=10110,bounds=bounds)


@pytest.mark.parametrize('change',['future_before','early_after','same_probe','negative_rtt','huge_rtt',
    'large_offset','drift','float','boolean','hash','extra','authority','long_window','outside_span'])
def test_missing_or_inconsistent_clock_evidence_is_rejected(change):
    raw=bracket(); start,end=10030,10290
    if change=='future_before': start=10019
    elif change=='early_after': end=10301
    elif change=='same_probe': raw['after']=deepcopy(raw['before'])
    elif change=='negative_rtt': raw['before']['received_at_ms']=9999
    elif change=='huge_rtt': raw['after']['received_at_ms']=16000
    elif change=='large_offset': raw['before']['server_time_ms']+=2001
    elif change=='drift': raw['after']['server_time_ms']+=501
    elif change=='float': raw['before']['server_time_ms']=11010.0
    elif change=='boolean': raw['before']['requested_at_ms']=True
    elif change=='hash': raw['before']['response_sha256']=''
    elif change=='extra': raw['max_drift_ms']=100000
    elif change=='authority': raw['provenance']='VERIFIED_VENUE'
    elif change=='long_window': end=start+150001
    else:
        raw['after']['requested_at_ms']+=160000
        raw['after']['received_at_ms']+=160000
        raw['after']['server_time_ms']+=160000
    with pytest.raises(ValueError): bracket_bounds(raw,start_ms=start,end_ms=end)


def test_compatibility_is_not_causal_certification_or_a_midpoint_latency():
    bounds=bracket_bounds(bracket(),start_ms=10030,end_ms=10290)
    lo,hi=event_local_interval(11100,bounds)
    assert lo < 10040 < 10110 < hi
    assert compatible_event(11100,claimed_at_ms=10040,received_at_ms=10110,bounds=bounds)
    with pytest.raises(ValueError): compatible_event(11100,claimed_at_ms=10111,received_at_ms=10110,bounds=bounds)
    with pytest.raises(ValueError): event_local_interval(True,bounds)


def test_different_sources_are_retained_in_immutable_bracket_identity():
    a=bracket(); b=deepcopy(a); b['after']['source_sha256']='e'*64
    x=bracket_bounds(a,start_ms=10030,end_ms=10290)
    y=bracket_bounds(b,start_ms=10030,end_ms=10290)
    assert x['offset_lower_ms']==y['offset_lower_ms']
    assert x['input_sha256']!=y['input_sha256']
