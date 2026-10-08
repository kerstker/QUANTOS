"""Versioned native input references and complete original bindings; no authority."""
import hashlib
import json
import re
from copy import deepcopy

from quantos.contracts import gold_chunked_source as source
from quantos.contracts import gold_segmented_input as segments
from quantos.contracts.registered_research_run import canonical, digest
from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode

INPUT_SCHEMA = 'QUANTOS_GOLD_NATIVE_SOURCE_INPUT_V1'
STORAGE_SCHEMA = 'QUANTOS_GOLD_NATIVE_SOURCE_STORAGE_V1'
BUDGETED_INPUT_SCHEMA = 'QUANTOS_GOLD_NATIVE_SOURCE_INPUT_V2'
FUNDING_INPUT_SCHEMA = 'QUANTOS_GOLD_NATIVE_SOURCE_INPUT_V3'
BUDGETED_FUNDING_INPUT_SCHEMA = 'QUANTOS_GOLD_NATIVE_SOURCE_INPUT_V4'
REANALYSIS_INPUT_SCHEMA = 'QUANTOS_GOLD_NATIVE_REANALYSIS_INPUT_V1'
BUDGETED_REANALYSIS_INPUT_SCHEMA = 'QUANTOS_GOLD_NATIVE_REANALYSIS_INPUT_V2'
REANALYSIS_INPUT_SCHEMAS = (REANALYSIS_INPUT_SCHEMA, BUDGETED_REANALYSIS_INPUT_SCHEMA)
BUDGETED_STORAGE_SCHEMA = 'QUANTOS_GOLD_NATIVE_SOURCE_STORAGE_V2'
INPUT_SCHEMAS = (INPUT_SCHEMA, BUDGETED_INPUT_SCHEMA, FUNDING_INPUT_SCHEMA, BUDGETED_FUNDING_INPUT_SCHEMA,
                 *REANALYSIS_INPUT_SCHEMAS)
BUDGETED_INPUT_SCHEMAS = (BUDGETED_INPUT_SCHEMA, BUDGETED_FUNDING_INPUT_SCHEMA, BUDGETED_REANALYSIS_INPUT_SCHEMA)
FUNDING_INPUT_SCHEMAS = (FUNDING_INPUT_SCHEMA, BUDGETED_FUNDING_INPUT_SCHEMA)
MATERIAL_SCHEMA = 'QUANTOS_PROJECT_GOLD_SOURCE_ORIGINALS_V1'
CUT_SCHEMA = 'QUANTOS_PROJECT_RESEARCH_CUT_V3'
DIRECTORY = 'input-parts'
MANIFEST = DIRECTORY+'/manifest.json'
LIMITS = dict(schema='QUANTOS_PROJECT_GOLD_SOURCE_LIMITS_V1', maximum_bytes=source.MAX_SOURCE_BYTES)


def require(value, code):
    if not value:
        raise ValueError('GOLD_NATIVE_SOURCE_'+code)


def storage(value):
    budgeted = type(value) is dict and value.get('schema') == BUDGETED_STORAGE_SCHEMA
    require(type(value) is dict and set(value) == {'schema', 'maximum_bytes', 'maximum_preparation_bytes',
        *({'replay_budget'} if budgeted else set())},
            'STORAGE_FIELDS')
    require(value['schema'] in (STORAGE_SCHEMA, BUDGETED_STORAGE_SCHEMA) and type(value['maximum_bytes']) is int
        and source.RESERVED_BYTES < value['maximum_bytes'] <= source.MAX_SOURCE_BYTES
        and type(value['maximum_preparation_bytes']) is int
        and 0 < value['maximum_preparation_bytes'] <= source.MAX_SOURCE_BYTES, 'STORAGE_BUDGET')
    if budgeted:
        from quantos.contracts.perpetual_replay_budget import validate
        validate(value['replay_budget'])
    return value


def _raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _descriptor(value):
    require(type(value) is dict and {'schema', 'capture', 'journal_files'} <= set(value)
        <= {'schema', 'capture', 'journal_files', 'observed_funding_records', 'settlement_window_ms'}
        and value['schema'] == source.PART_SCHEMA and type(value['journal_files']) is dict
        and set(value['journal_files']) == set(segments.JOURNAL_FILES), 'PART_FIELDS')
    refs = [value['capture'], *value['journal_files'].values()]
    if 'observed_funding_records' in value:
        refs.append(value['observed_funding_records'])
        require('settlement_window_ms' in value, 'FUNDING_WINDOW')
    if 'settlement_window_ms' in value:
        require(type(value['settlement_window_ms']) is int and 1 <= value['settlement_window_ms'] <= 1000,
                'FUNDING_WINDOW')
    for ref in refs:
        source.blob(ref)
    return refs


def input_document(value):
    budgeted = type(value) is dict and value.get('input_schema') in BUDGETED_INPUT_SCHEMAS
    reanalysis = type(value) is dict and value.get('input_schema') in REANALYSIS_INPUT_SCHEMAS
    require(type(value) is dict and set(value) == {'input_schema', 'source_binding', 'forward_input',
        *({'replay_budget'} if budgeted else set()),
        *source.CONFIGURATION_FIELDS, *({'portfolio_plan'} if 'portfolio_plan' in value else set()),
        *({'settlement_window_ms'} if 'settlement_window_ms' in value else set())}
        and value['input_schema'] in INPUT_SCHEMAS, 'INPUT_FIELDS')
    source.configuration({k: value[k] for k in source.CONFIGURATION_FIELDS | {'portfolio_plan'} if k in value})
    binding = value['source_binding']
    require(type(binding) is dict and set(binding) == {'schema', 'source_id', 'manifest_sha256', 'part_count', 'blob_bytes',
        *({'collection_source_identity'} if reanalysis else set())}
        and binding['schema'] == ('QUANTOS_GOLD_SOURCE_EXECUTION_BINDING_V2' if reanalysis
                                 else 'QUANTOS_GOLD_SOURCE_EXECUTION_BINDING_V1'), 'BINDING_FIELDS')
    if reanalysis:
        source_identity(binding['collection_source_identity'])
        source_identity(value['release_identity'])
    require(all(type(binding[k]) is str and re.fullmatch('[0-9a-f]{64}', binding[k]) for k in
        ('source_id', 'manifest_sha256')) and type(binding['part_count']) is int
        and 1 <= binding['part_count'] <= source.MAX_PARTS and type(binding['blob_bytes']) is int
        and 0 < binding['blob_bytes'] <= source.MAX_SOURCE_BYTES, 'BINDING')
    if budgeted:
        from quantos.contracts.perpetual_replay_budget import validate
        budget = validate(value['replay_budget'])
        require(binding['part_count'] <= budget['maximum_environment_segments'], 'REPLAY_ENVIRONMENT_BUDGET')
    observed = value['forward_input']
    require(type(observed) is dict and set(observed) == {'journal_messages_sha256', 'closed_observations'}
        and type(observed['journal_messages_sha256']) is str
        and re.fullmatch('[0-9a-f]{64}', observed['journal_messages_sha256']) is not None
        and type(observed['closed_observations']) is list
        and len(observed['closed_observations']) <= 100_000, 'FORWARD_FIELDS')
    for row in observed['closed_observations']:
        require(type(row) is list and len(row) == 2
            and all(type(t) is int and 0 <= t <= 2**63-1 for t in row) and row[0] <= row[1], 'FORWARD_ROW')
    if 'settlement_window_ms' in value:
        require(type(value['settlement_window_ms']) is int and 1 <= value['settlement_window_ms'] <= 1000,
                'FUNDING_WINDOW')
    require(value['input_schema'] not in FUNDING_INPUT_SCHEMAS or 'settlement_window_ms' in value,
            'FUNDING_WINDOW_REQUIRED')
    canonical(value)
    return value


def source_identity(value):
    """Exact code provenance metadata; it is not independent source authority."""
    from quantos.contracts.registered_research_run import _hash, _text
    require(type(value) is dict and set(value) == {
        'commit', 'python_source_sha256', 'dependency_lock_sha256', 'python_version', 'architecture'}
        and type(value['commit']) is str and re.fullmatch('[0-9a-f]{40}', value['commit']) is not None
        and _hash(value['python_source_sha256']) and _hash(value['dependency_lock_sha256'])
        and _text(value['python_version']) and _text(value['architecture']), 'CODE_IDENTITY')
    return value


def collection_identity(document):
    input_document(document)
    return (document['source_binding']['collection_source_identity']
        if document['input_schema'] in REANALYSIS_INPUT_SCHEMAS else document['release_identity'])


def reanalysis_reference(reference, analysis_source_identity, replay_budget=None):
    """New replay identity, with the original manifest and collector kept intact."""
    input_document(reference)
    require(reference['input_schema'] in (INPUT_SCHEMA, FUNDING_INPUT_SCHEMA), 'ORIGINAL_REFERENCE_VERSION')
    source_identity(reference['release_identity'])
    source_identity(analysis_source_identity)
    value = deepcopy(reference)
    value['input_schema'] = REANALYSIS_INPUT_SCHEMA
    value['source_binding'].update(schema='QUANTOS_GOLD_SOURCE_EXECUTION_BINDING_V2',
        collection_source_identity=deepcopy(reference['release_identity']))
    value['release_identity'] = deepcopy(analysis_source_identity)
    if replay_budget is not None:
        from quantos.contracts.perpetual_replay_budget import validate
        value.update(input_schema=BUDGETED_REANALYSIS_INPUT_SCHEMA, replay_budget=validate(replay_budget))
    return input_document(value)


def bound_reference(reference, document):
    """Reconstruct either version from original bytes, never from claimed results."""
    input_document(document)
    if document['input_schema'] in REANALYSIS_INPUT_SCHEMAS:
        return reanalysis_reference(reference, document['release_identity'], document.get('replay_budget'))
    return execution_reference(reference, document.get('replay_budget'))


def execution_reference(reference, replay_budget=None):
    input_document(reference)
    require(reference['input_schema'] in (INPUT_SCHEMA, FUNDING_INPUT_SCHEMA), 'ORIGINAL_REFERENCE_VERSION')
    value = deepcopy(reference)
    if replay_budget is not None:
        from quantos.contracts.perpetual_replay_budget import validate
        value.update(input_schema=BUDGETED_FUNDING_INPUT_SCHEMA if reference['input_schema'] == FUNDING_INPUT_SCHEMA
            else BUDGETED_INPUT_SCHEMA, replay_budget=validate(replay_budget))
    return input_document(value)


def input_storage(value, document):
    storage(value)
    input_document(document)
    require((document['input_schema'] in BUDGETED_INPUT_SCHEMAS) == (value['schema'] == BUDGETED_STORAGE_SCHEMA),
            'REPLAY_BUDGET_VERSION')
    require(document.get('replay_budget') == value.get('replay_budget'), 'REPLAY_BUDGET_BINDING')
    return value


def bind_plan(plan, document):
    from quantos.contracts.registered_research_run import SOURCE_REANALYSIS_SCHEMAS, EVALUATED_SOURCE_REANALYSIS_SCHEMAS
    input_storage(plan['source_storage'], document)
    require((document['input_schema'] in REANALYSIS_INPUT_SCHEMAS)
        == (plan['schema'] in SOURCE_REANALYSIS_SCHEMAS), 'REANALYSIS_PLAN_VERSION')
    if plan['schema'] in EVALUATED_SOURCE_REANALYSIS_SCHEMAS:
        require(plan['evaluation_core']['dataset_manifest_sha256']
            == document['source_binding']['manifest_sha256'], 'EVALUATION_DATASET')


def simulation_budget(identity, simulation):
    """Versioned budget binding only; never proof of source, risk or economics."""
    if 'replay_budget' not in identity:
        return simulation.get('schema_version') == 'LINEAR_PERPETUAL_FORWARD_SIMULATION_V3' and 'replay_budget' not in simulation
    from collections.abc import Sequence
    from quantos.contracts.perpetual_replay_budget import validate
    budget = validate(dict(identity['replay_budget']))
    segments = simulation.get('environment_segments')
    return (identity.get('schema') == 'GOLD_CHUNKED_BAR_QUOTE_BINDING_V3'
        and simulation.get('schema_version') == 'LINEAR_PERPETUAL_FORWARD_SIMULATION_V4'
        and dict(simulation.get('replay_budget', {})) == budget
        and isinstance(segments, Sequence)
        and len(segments) == identity.get('input_segment_count')
        and 1 <= len(segments) <= budget['maximum_environment_segments']
        and identity['quote_manifest']['end_ms']-identity['quote_manifest']['start_ms'] <= budget['maximum_elapsed_ms'])


def result_budget(value, document, report):
    input_storage(value, document)
    identity = report['dataset_identity']
    engines = [report['cash_baseline'], *(t['simulation'] for t in report['trials'])]
    if 'portfolio' in report:
        engines.append(report['portfolio']['simulation'])
    if document['input_schema'] in BUDGETED_INPUT_SCHEMAS:
        require(dict(identity.get('replay_budget', {})) == document['replay_budget'], 'REPORT_REPLAY_BUDGET')
        require(all(simulation_budget(identity, engine) for engine in engines), 'REPORT_SIMULATION_BUDGET')
    else:
        require('replay_budget' not in identity and all('replay_budget' not in e for e in engines),
                'LEGACY_REPORT_REPLAY_BUDGET')


def inspect(manifest, names, read, *, manifest_sha256):
    """Read every transitive original; the caller retains the physical cut."""
    source.manifest(manifest)
    used, parts = {}, []
    def take(ref):
        source.blob(ref)
        require(ref['sha256'] in names, 'BLOB_MISSING')
        raw = read(ref)
        require(type(raw) is bytes and len(raw) == ref['bytes'] and digest(raw) == ref['sha256'], 'BLOB_BYTES')
        used.setdefault(ref['sha256'], ref['bytes'])
        require(used[ref['sha256']] == ref['bytes'], 'BLOB_BINDING')
        return raw
    for ref in manifest['parts']:
        part = decode(take(ref))
        for binding in _descriptor(part):
            take(binding)
        parts.append(part)
    funding_policy = manifest['schema'] == source.FUNDING_SCHEMA
    if funding_policy:
        window = parts[0].get('settlement_window_ms')
        require(type(window) is int and 1 <= window <= 1000, 'FUNDING_WINDOW_REQUIRED')
        for index, part in enumerate(parts):
            if 'observed_funding_records' in part:
                require(part['settlement_window_ms'] == window, 'FUNDING_POLICY_CHANGED')
            elif 'settlement_window_ms' in part:
                require(index == 0 and part['settlement_window_ms'] == window, 'FUNDING_HEADER')
    require(set(used) == set(names) and len(used) == manifest['blob_count']
        and sum(used.values()) == manifest['blob_bytes'], 'BLOB_POPULATION')
    from quantos.contracts.gold_prefix_chain import SCHEMA as delta_schema, journals as chain_journals
    first_summary = decode(take(parts[0]['journal_files']['summary.json']))
    delta = first_summary.get('schema') == delta_schema
    require(all((decode(take(part['journal_files']['summary.json'])).get('schema') == delta_schema) == delta
        for part in parts), 'JOURNAL_VERSION_CHANGED')
    selected = parts if delta else [parts[-1]]
    previous = None
    wire_hash = hashlib.sha256()
    observed = []
    for part in selected:
        messages = part['journal_files']['messages.jsonl']
        body = take(messages)
        summary = decode(take(part['journal_files']['summary.json']))
        if delta:
            raw = {n: take(ref) for n,ref in part['journal_files'].items()}
            chain_journals(raw, previous)
            require(summary['execution_source'] == manifest['configuration']['release_identity'], 'EXECUTION_SOURCE')
            if previous is not None:
                require(all(raw[n] == previous[n] for n in ('plan.json','warmup.json','warmup-receipt.json')),
                        'JOURNAL_HISTORY_CHANGED')
            previous = raw
            body = b'' if body == b'\n' and not summary['messages'] else body
            wire_hash.update(body)
            require(wire_hash.hexdigest() == summary['prefix_messages_sha256'], 'WIRE_PREFIX_DIGEST')
        lines = body.splitlines()
        require((len(lines) == summary['messages'] if delta else len(lines) <= 100_000), 'JOURNAL_BOUND')
        for line in lines:
            entry = decode(line)
            message = decode(entry['raw'])
            candle = message['data']['k']
            if candle['x'] is True and (not delta or entry['decision']['status'] == 'CLOSED_BAR'):
                close = candle['T']+1
                require(candle['s'] == 'XAUUSDT' and candle['i'] == '15m'
                    and candle['t'] == close-900_000, 'JOURNAL_MARKET')
                observed.append([close, entry['received_at_ms']])
    journal_digest = wire_hash.hexdigest() if delta else messages['sha256']
    reference = dict(manifest['configuration'], input_schema=FUNDING_INPUT_SCHEMA if funding_policy else INPUT_SCHEMA,
        source_binding=dict(schema='QUANTOS_GOLD_SOURCE_EXECUTION_BINDING_V1', source_id=manifest['source_id'],
            manifest_sha256=manifest_sha256, part_count=manifest['part_count'], blob_bytes=manifest['blob_bytes']),
        forward_input=dict(journal_messages_sha256=journal_digest, closed_observations=observed))
    if 'settlement_window_ms' in parts[0]:
        reference['settlement_window_ms'] = parts[0]['settlement_window_ms']
    input_document(reference)
    return reference, parts, {'blobs/'+key: dict(sha256=key, bytes=size) for key, size in used.items()}


def validate_material(plan, document, originals, material):
    bind_plan(plan, document)
    require(type(material) is dict and set(material) == {'schema', 'status', 'files', 'bytes', 'part_count', 'parts'}
        and material['schema'] == MATERIAL_SCHEMA and material['status'] in ('COMPLETE', 'INCOMPLETE'), 'MATERIAL_FIELDS')
    files = material['files']
    require(type(files) is dict and len(files) <= source.MAX_BLOBS+1, 'MATERIAL_FILES')
    for name, ref in files.items():
        match = re.fullmatch(r'blobs/([0-9a-f]{64})', name) if type(name) is str else None
        require(name == 'manifest.json' or match is not None, 'MATERIAL_NAME')
        source.blob(ref)
        require(name == 'manifest.json' or match[1] == ref['sha256'], 'MATERIAL_HASH')
    require(type(material['bytes']) is int and material['bytes'] == sum(v['bytes'] for v in files.values())
        and material['bytes'] <= plan['source_storage']['maximum_bytes'], 'MATERIAL_BYTES')
    require(type(material['parts']) is list and type(material['part_count']) is int
        and material['part_count'] == len(material['parts']), 'MATERIAL_PARTS')
    if material['status'] == 'INCOMPLETE':
        require(MANIFEST not in originals and 'manifest.json' not in files and not material['parts'], 'PARTIAL_MANIFEST')
        return material
    require(MANIFEST in originals and 'manifest.json' in files, 'MANIFEST_MISSING')
    import base64
    raw = base64.b64decode(originals[MANIFEST]['bytes_base64'], validate=True)
    manifest = source.manifest(decode(raw))
    require(digest(raw) == document['source_binding']['manifest_sha256'] == files['manifest.json']['sha256']
        and len(raw) == files['manifest.json']['bytes'] and material['part_count'] == manifest['part_count']
        and manifest['configuration'] == {k: (collection_identity(document) if k == 'release_identity' else document[k])
            for k in source.CONFIGURATION_FIELDS | {'portfolio_plan'} if k in document},
        'MANIFEST_BINDING')
    require(manifest['maximum_bytes'] <= plan['source_storage']['maximum_bytes'], 'SOURCE_BUDGET')
    used = {}
    for part, descriptor in zip(material['parts'], manifest['parts'], strict=True):
        raw_part = _raw(part)
        require(len(raw_part) == descriptor['bytes'] and digest(raw_part) == descriptor['sha256'], 'PART_IDENTITY')
        for ref in [descriptor, *_descriptor(part)]:
            require(files.get('blobs/'+ref['sha256']) == ref, 'PART_ORIGINAL_MISSING')
            used[ref['sha256']] = ref['bytes']
    require(set(files) == {'manifest.json', *('blobs/'+name for name in used)}
        and sum(used.values()) == manifest['blob_bytes'] and len(used) == manifest['blob_count']
        and document['source_binding']['source_id'] == manifest['source_id']
        and document['source_binding']['part_count'] == manifest['part_count']
        and document['source_binding']['blob_bytes'] == manifest['blob_bytes'], 'MATERIAL_POPULATION')
    return material
