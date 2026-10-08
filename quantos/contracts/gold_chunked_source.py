"""Bounded manifest of immutable original Gold input files; no authority."""
from decimal import Decimal
import re

from quantos.contracts.registered_research_run import canonical, MAX_INPUT_BYTES
from quantos.contracts.backtest_identity import exact_identity_hash

SCHEMA = 'QUANTOS_GOLD_CHUNKED_SOURCE_V1'
FUNDING_SCHEMA = 'QUANTOS_GOLD_CHUNKED_SOURCE_V2'
PART_SCHEMA = 'QUANTOS_GOLD_CHUNKED_SOURCE_PART_V1'
MAX_PARTS = 4096
MAX_BLOBS = MAX_PARTS * 9
MAX_MANIFEST_BYTES = 1_048_576
MAX_SOURCE_BYTES = 32 * 1024**3
RESERVED_BYTES = MAX_MANIFEST_BYTES + 4096
CONFIGURATION_FIELDS = {'release_identity','quantity','initial_collateral','fee_rate'}


def require(condition, code):
    if not condition:
        raise ValueError('GOLD_SOURCE_'+code)


def blob(value):
    require(type(value) is dict and set(value)=={'sha256','bytes'}, 'BLOB_FIELDS')
    require(type(value['sha256']) is str and re.fullmatch('[0-9a-f]{64}',value['sha256']) is not None,
            'BLOB_ID')
    require(type(value['bytes']) is int and 0 < value['bytes'] <= MAX_INPUT_BYTES, 'BLOB_BYTES')
    return value


def configuration(value):
    require(type(value) is dict and CONFIGURATION_FIELDS <= set(value)
        <= CONFIGURATION_FIELDS | {'portfolio_plan'}, 'CONFIGURATION_FIELDS')
    require(type(value['release_identity']) is dict and bool(value['release_identity']), 'SOURCE')
    for name in ('quantity','initial_collateral','fee_rate'):
        text=value[name]
        require(type(text) is str and 0<len(text)<=100, 'CONFIGURATION_DECIMAL')
        try:
            number=Decimal(text)
        except Exception as exc:
            raise ValueError('GOLD_SOURCE_CONFIGURATION_DECIMAL') from exc
        require(number.is_finite() and number>0, 'CONFIGURATION_DECIMAL')
    canonical(value,maximum=MAX_MANIFEST_BYTES)
    return value


def identity(value):
    return exact_identity_hash({k:v for k,v in value.items() if k!='source_id'},name='gold_chunked_source')


def manifest(value):
    require(type(value) is dict and set(value)=={'schema','configuration','parts','part_count',
        'blob_count','blob_bytes','maximum_bytes','source_id','status','replay_ready',
        'economic_eligible','submission_enabled'}, 'MANIFEST_FIELDS')
    require(value['schema'] in (SCHEMA, FUNDING_SCHEMA) and value['status']=='FROZEN_ORIGINALS_NOT_EXECUTION_VALIDATED'
        and all(value[name] is False for name in ('replay_ready','economic_eligible','submission_enabled')),
        'MANIFEST_STATUS')
    configuration(value['configuration'])
    require(type(value['parts']) is list and type(value['part_count']) is int
        and 1<=value['part_count']==len(value['parts'])<=MAX_PARTS, 'PART_COUNT')
    for row in value['parts']:
        blob(row)
    require(type(value['blob_count']) is int and 1<=value['blob_count']<=MAX_BLOBS,
            'BLOB_COUNT')
    require(type(value['maximum_bytes']) is int and RESERVED_BYTES<value['maximum_bytes']<=MAX_SOURCE_BYTES
        and type(value['blob_bytes']) is int
        and 0<value['blob_bytes']<=value['maximum_bytes']-RESERVED_BYTES, 'TOTAL_BYTES')
    canonical(value,maximum=MAX_MANIFEST_BYTES)
    require(value['source_id']==identity(value), 'MANIFEST_IDENTITY')
    return value
