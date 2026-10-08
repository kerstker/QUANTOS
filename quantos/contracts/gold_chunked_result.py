"""Complete original Gold report transport; no candidate or economic authority."""
from collections.abc import Mapping

from quantos.contracts import registered_research_run as native
from quantos.contracts import perpetual_chunked_result as core
from quantos.streaming_identity import MAX_BYTES

SCHEMA = 'QUANTOS_GOLD_CHUNKED_REPORT_V1'
STORAGE_SCHEMA = 'QUANTOS_GOLD_CHUNKED_REPORT_STORAGE_V1'
REFERENCE_SCHEMA = 'QUANTOS_GOLD_CHUNKED_REPORT_REFERENCE_V1'
MAX_PARTS = core.MAX_PARTS
MAX_MANIFEST_BYTES = core.MAX_MANIFEST_BYTES


def storage(value):
    native.require(type(value) is dict and set(value) ==
        {'schema', 'maximum_bytes', 'maximum_index_bytes', 'chunk_bytes'}, 'GOLD_RESULT_STORAGE')
    native.require(value['schema'] == STORAGE_SCHEMA
        and all(type(value[k]) is int and 0 < value[k] <= MAX_BYTES
                for k in ('maximum_bytes', 'maximum_index_bytes'))
        and type(value['chunk_bytes']) is int and 1 <= value['chunk_bytes'] <= native.MAX_OUTPUT_BYTES,
        'GOLD_RESULT_STORAGE')
    return value


def validate(value):
    fields = set('schema status parts bytes sha256 input_sha256 result_hash source_identity_sha256 '
                 'authoritative economic_eligible submission_enabled'.split())
    core.require(type(value) is dict and set(value) == fields, 'GOLD_FIELDS')
    native.canonical(value, maximum=MAX_MANIFEST_BYTES)
    core.require(value['schema'] == SCHEMA and value['status'] == 'TECHNICAL_VALIDATION_ONLY'
        and all(value[k] is False for k in ('authoritative', 'economic_eligible', 'submission_enabled')),
        'GOLD_AUTHORITY')
    core.require(all(core.sha(value[k]) for k in
        ('sha256', 'input_sha256', 'result_hash', 'source_identity_sha256')), 'GOLD_IDENTITY')
    core.require(type(value['bytes']) is int and 0 < value['bytes'] <= MAX_BYTES, 'GOLD_BYTES')
    parts = value['parts']
    core.require(type(parts) is list and 0 < len(parts) <= MAX_PARTS, 'GOLD_PARTS')
    for part in parts:
        core.require(type(part) is dict and set(part) == {'sha256', 'bytes'} and core.sha(part['sha256'])
            and type(part['bytes']) is int and 0 < part['bytes'] <= native.MAX_OUTPUT_BYTES, 'GOLD_PART')
    core.require(sum(p['bytes'] for p in parts) == value['bytes'], 'GOLD_PART_BYTES')
    return value


def bind_result(result, manifest):
    core.require(result.get('schema') == 'GOLD_BAR_PUBLIC_COMPARISON_V1'
        and result.get('result_hash') == manifest['result_hash']
        and all(result.get(k) is False for k in
            ('authoritative', 'economic_eligible', 'submission_enabled', 'historical_oos_evaluated')),
        'GOLD_RESULT_BINDING')
    source = result.get('release_identity')
    core.require(isinstance(source, Mapping) and set(source) ==
        {'commit', 'python_source_sha256', 'dependency_lock_sha256', 'python_version', 'architecture'},
        'GOLD_SOURCE_BINDING')
    core.require(native.digest(dict(source)) == manifest['source_identity_sha256'], 'GOLD_SOURCE_BINDING')


def reference(manifest, manifest_sha256):
    validate(manifest)
    core.require(core.sha(manifest_sha256), 'GOLD_MANIFEST_PIN')
    return dict(schema=REFERENCE_SCHEMA, manifest_sha256=manifest_sha256,
        **{k: manifest[k] for k in ('bytes', 'sha256', 'result_hash', 'input_sha256', 'source_identity_sha256')})


def validate_reference(value):
    core.require(type(value) is dict and set(value) ==
        set('schema manifest_sha256 bytes sha256 result_hash input_sha256 source_identity_sha256'.split()),
        'GOLD_REFERENCE')
    core.require(value['schema'] == REFERENCE_SCHEMA
        and type(value['bytes']) is int and 0 < value['bytes'] <= MAX_BYTES
        and all(core.sha(value[k]) for k in
            ('manifest_sha256', 'sha256', 'result_hash', 'input_sha256', 'source_identity_sha256')),
        'GOLD_REFERENCE')
    return value
