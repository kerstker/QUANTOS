"""Bounded capture availability facts, never strategy or account performance."""
from copy import deepcopy
import re

from quantos.local_artifacts import with_result_hash


def validate_capture_memory(value):
    if (type(value) is not dict or set(value)!={'schema','as_of_ms','records','authoritative','result_hash'}
            or value['schema']!='GOLD_CAPTURE_MEMORY_V1' or value['authoritative'] is not False
            or type(value['as_of_ms']) is not int or not 0<=value['as_of_ms']<=8640000000000000
            or with_result_hash({k:v for k,v in value.items() if k!='result_hash'})!=value
            or type(value['records']) is not list or not 1<=len(value['records'])<=5):
        raise ValueError('INVALID_GOLD_CAPTURE_MEMORY')
    fields={'input_id','request_hash','files','capture_status','ended_at_ms','duration_ms',
            'unique_aggregates','window_ms','max_age_ms','window_count','first_boundary_delay_ms'}
    seen=set()
    for row in value['records']:
        if (type(row) is not dict or set(row)!=fields or type(row['input_id']) is not int
                or not 1<=row['input_id']<=1000 or row['input_id'] in seen
                or row['capture_status'] not in ('NO_CAPTURE','NO_DATA','INTERRUPTED','CAPTURED_PUBLIC_PREFIX','UNVERIFIABLE_CAPTURE')
                or type(row['files']) is not dict
                or set(row['files'])-{'request.json','template.json','design.json','trades.json','quotes.json','design-memory.json','research-memory.json'}
                or any(type(h) is not str or re.fullmatch('[a-f0-9]{64}',h) is None
                       for h in [row['request_hash'],*row['files'].values()])):
            raise ValueError('INVALID_GOLD_CAPTURE_MEMORY_ROW')
        for name in ('ended_at_ms','duration_ms','unique_aggregates','window_ms','max_age_ms','window_count','first_boundary_delay_ms'):
            n=row[name]
            if n is not None and (type(n) is not int or not 0<=n<=value['as_of_ms']):
                raise ValueError('INVALID_GOLD_CAPTURE_MEMORY_FACT')
        if row['unique_aggregates'] is not None and row['unique_aggregates']>20000:
            raise ValueError('INVALID_GOLD_CAPTURE_MEMORY_COUNT')
        if row['capture_status'] in ('NO_CAPTURE','UNVERIFIABLE_CAPTURE') and any(row[k] is not None
                for k in ('ended_at_ms','duration_ms','unique_aggregates','first_boundary_delay_ms')):
            raise ValueError('UNVERIFIED_GOLD_CAPTURE_STATISTICS')
        seen.add(row['input_id'])
    return deepcopy(value)
