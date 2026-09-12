"""Offline, bounded two-clock compatibility; no time synchronization or authority.

Server time is observed somewhere between request and response. We retain that
interval, integer timestamp quantization, and a fixed 500 ms interior drift
assumption. Two probes cannot establish that no clock step happened between them.
Neither a compatible interval nor an imported response digest certifies causality.
"""
import hashlib
import json
import re

SCHEMA = 'BINANCE_USDM_CLOCK_BRACKET_V1'
QUERY_SCHEMA = 'DEMO_ORIGINAL_QUERY_CLOCK_V1'
POLICY = 'ENDPOINT_ENVELOPE_WITH_DECLARED_500MS_INTERIOR_DRIFT_V1'
MAX_TIME = 8640000000000000
MAX_RTT_MS = 5000
MAX_OFFSET_MS = 2000
INTERIOR_DRIFT_MS = 500
QUANTIZATION_MS = 1


def _time(value):
    if type(value) is not int or not 0 <= value <= MAX_TIME:
        raise ValueError('DEMO_CLOCK_EXACT_MILLISECOND_REQUIRED')
    return value


def _fields(value, keys):
    if type(value) is not dict or set(value) != set(keys.split()):
        raise ValueError('DEMO_CLOCK_FIELDS_INVALID')


def _probe(value):
    _fields(value, 'requested_at_ms received_at_ms server_time_ms response_sha256 source_sha256')
    sent,received,server=(_time(value[k]) for k in ('requested_at_ms','received_at_ms','server_time_ms'))
    for key in ('response_sha256','source_sha256'):
        if type(value[key]) is not str or re.fullmatch('[a-f0-9]{64}',value[key]) is None:
            raise ValueError('DEMO_CLOCK_SOURCE_DIGEST_REQUIRED')
    midpoint=server-(sent+received)//2
    if not 0 <= received-sent <= MAX_RTT_MS or abs(midpoint)>MAX_OFFSET_MS:
        raise ValueError('DEMO_CLOCK_PROBE_OUTSIDE_BOUND')
    return sent,received,server-received-QUANTIZATION_MS,server-sent+QUANTIZATION_MS,midpoint


def bracket_bounds(document, *, start_ms, end_ms):
    """Calculate an explicit uncertainty envelope around a local receive window.

    Read-only source observations are required on both sides; a future probe is
    usable only for retrospective diagnostics, never for a past trading decision.
    Legacy records are not converted or supplemented in place.
    """
    _fields(document,'schema provenance before after')
    if document['schema']!=SCHEMA or document['provenance'] not in ('SYNTHETIC_TEST_FIXTURE','IMPORTED_UNVERIFIED'):
        raise ValueError('DEMO_CLOCK_UNVERIFIED_BRACKET_REQUIRED')
    start,end=_time(start_ms),_time(end_ms)
    before,after=_probe(document['before']),_probe(document['after'])
    if (not before[1] <= start < end <= after[0] or end-start>150000 or
            after[1]-before[0]>160000 or abs(after[4]-before[4])>INTERIOR_DRIFT_MS):
        raise ValueError('DEMO_CLOCK_BRACKET_WINDOW_OR_DRIFT')
    return {'schema':SCHEMA,'policy':POLICY,
        'input_sha256':hashlib.sha256(json.dumps(document,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest(),
        'start_ms':start,'end_ms':end,'available_at_ms':after[1],
        'offset_lower_ms':min(before[2],after[2])-INTERIOR_DRIFT_MS,
        'offset_upper_ms':max(before[3],after[3])+INTERIOR_DRIFT_MS,
        'local_domain':'CAPTURE_PROCESS_EPOCH_MS','venue_domain':'BINANCE_USDM_SERVER_EPOCH_MS',
        'quantization_ms':QUANTIZATION_MS,'declared_interior_drift_ms':INTERIOR_DRIFT_MS,
        'source_verified':False,'interior_drift_verified':False,'exact_latency_ms':None,
        'causality_status':'COMPATIBILITY_ONLY_NOT_CAUSAL_CERTIFICATION'}


def event_local_interval(venue_ms, bounds):
    """Return possible local times; never select a midpoint as the true event."""
    venue=_time(venue_ms)
    lower,upper=bounds['offset_lower_ms'],bounds['offset_upper_ms']
    if type(lower) is not int or type(upper) is not int or lower>upper:
        raise ValueError('DEMO_CLOCK_OFFSET_INTERVAL_INVALID')
    return venue-upper,venue-lower


def compatible_event(venue_ms, *, claimed_at_ms, received_at_ms, bounds):
    """Check interval overlap with the known local claim/receive ordering."""
    claimed,received=_time(claimed_at_ms),_time(received_at_ms)
    if not bounds['start_ms'] <= claimed <= received <= bounds['end_ms']:
        raise ValueError('DEMO_CLOCK_LOCAL_ORDER_OR_WINDOW')
    lower,upper=event_local_interval(venue_ms,bounds)
    return lower<=received and upper>=claimed


def validate_query_clock(document, *, query_requested_at_ms):
    """Verify retained public clock bytes before their associated private query.

    The source digest is provenance, not a self-issued source certification. The
    protected caller obtains these bytes from its fixed public transport.
    """
    _fields(document,'schema requested_at_ms received_at_ms server_time_ms response_sha256 source_sha256 raw_utf8')
    if document['schema']!=QUERY_SCHEMA or type(document['raw_utf8']) is not str:
        raise ValueError('DEMO_QUERY_CLOCK_SCHEMA')
    encoded=document['raw_utf8'].encode()
    if len(encoded)>512 or hashlib.sha256(encoded).hexdigest()!=document['response_sha256']:
        raise ValueError('DEMO_QUERY_CLOCK_RESPONSE_DIGEST')
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:raise ValueError('DEMO_QUERY_CLOCK_DUPLICATE_JSON')
            value[key]=item
        return value
    try:
        body=json.loads(document['raw_utf8'],object_pairs_hook=pairs)
    except (TypeError,UnicodeError,RecursionError) as exc:
        raise ValueError('DEMO_QUERY_CLOCK_JSON') from exc
    _fields(body,'serverTime')
    if type(body['serverTime']) is not int or body['serverTime']!=document['server_time_ms']:
        raise ValueError('DEMO_QUERY_CLOCK_SERVER_TIME')
    probe={k:document[k] for k in ('requested_at_ms','received_at_ms','server_time_ms','response_sha256','source_sha256')}
    value=_probe(probe)
    if not 0<=_time(query_requested_at_ms)-value[1]<=60000:
        raise ValueError('DEMO_QUERY_CLOCK_FUTURE_OR_STALE_PROBE')
    return probe
