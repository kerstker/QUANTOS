"""Complete technical result carrier, never native registration or authority."""
import re

from quantos.contracts.registered_research_run import canonical, MAX_OUTPUT_BYTES
from quantos.streaming_identity import MAX_BYTES

SCHEMA = 'PERPETUAL_SIMULATION_CHUNKED_RESULT_V1'
MAX_PARTS = 4096
MAX_MANIFEST_BYTES = 1_048_576
MAX_ROWS = 100_000_000


def require(condition, code):
    if not condition:
        raise ValueError('CHUNKED_RESULT_'+code)


def sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def validate(value):
    require(type(value) is dict and set(value)==set('schema status parts bytes sha256 input_hash result_hash authoritative economic_eligible submission_enabled native_registered input_archive'.split()), 'FIELDS')
    canonical(value, maximum=MAX_MANIFEST_BYTES)
    require(value['schema']==SCHEMA and value['status']=='TECHNICAL_VALIDATION_ONLY', 'SCHEMA')
    require(all(value[k] is False for k in ('authoritative','economic_eligible','submission_enabled','native_registered')), 'AUTHORITY')
    require(all(sha(value[k]) for k in ('sha256','input_hash','result_hash')), 'IDENTITY')
    require(type(value['bytes']) is int and 0<value['bytes']<=MAX_BYTES, 'BYTES')
    parts = value['parts']
    require(type(parts) is list and 0<len(parts)<=MAX_PARTS, 'PART_COUNT')
    for part in parts:
        require(type(part) is dict and set(part)=={'sha256','bytes'} and sha(part['sha256'])
            and type(part['bytes']) is int and 0<part['bytes']<=MAX_OUTPUT_BYTES, 'PART')
    require(sum(p['bytes'] for p in parts)==value['bytes'], 'PART_BYTES')
    archive=value['input_archive']
    require(type(archive) is dict and set(archive)=={'file','sha256','frame_count','batch_count'}
        and archive['file']=='history.sqlite3' and sha(archive['sha256'])
        and type(archive['frame_count']) is int and 0<archive['frame_count']<=MAX_ROWS
        and type(archive['batch_count']) is int and 0<archive['batch_count']<=archive['frame_count'], 'INPUT_ARCHIVE')
    return value
