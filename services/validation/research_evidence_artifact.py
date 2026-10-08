"""Historical, self-verifying research receipts, never current admission.

Trust selection belongs to the fixed reader at composition time. A retained
public registry proves what was checked then; it cannot authorize a later use.
The native technical records and all risk limitations remain unchanged.
"""
from __future__ import annotations

import base64
import copy

from quantos.contracts.json_integrity import load_json_without_duplicate_keys
from quantos.contracts.research_evidence_json import MAX_BYTES as MAX_BYTES, require, canonical, digest, decode
from services.validation import research_evidence_authority as authority
from services.validation import research_result_projection as projection

SCHEMA = 'QUANTOS_RESEARCH_EVIDENCE_ARTIFACT_V1'
POPULATION_SCHEMA = 'QUANTOS_RESEARCH_REGISTRY_POPULATION_V1'
FIELDS = frozenset(('schema', 'search_identity', 'config_hash', 'native_trial_mapping',
    'provenance_trial_mapping', 'population', 'snapshot', 'assessment'))


def pack_snapshot(snapshot):
    authority._snapshot(snapshot, snapshot['subject'])
    value = {k: copy.deepcopy(v) for k, v in snapshot.items() if k != 'originals'}
    value['originals'] = {k: None if v is None else base64.b64encode(v).decode('ascii')
                          for k, v in snapshot['originals'].items()}
    canonical(value)
    return value


def unpack_snapshot(value):
    projection._fields(value, authority.SNAPSHOT_FIELDS, 'SNAPSHOT_FIELDS')
    projection._fields(value['originals'], projection.ROLES, 'ORIGINAL_ROLES')
    result = {k: copy.deepcopy(v) for k, v in value.items() if k != 'originals'}
    originals = {}
    for key, raw in value['originals'].items():
        if raw is None:
            originals[key] = None
            continue
        require(type(raw) is str and len(raw) <= (projection.MAX_LEAF+2)//3*4, 'ORIGINAL_BOUND')
        try:
            data = base64.b64decode(raw, validate=True)
        except (ValueError, UnicodeError) as exc:
            raise ValueError('RESEARCH_ARTIFACT_BASE64') from exc
        require(base64.b64encode(data).decode() == raw, 'BASE64')
        originals[key] = data
    result['originals'] = originals
    authority._snapshot(result, result['subject'])
    return result


def validate_population(value):
    projection._fields(value, ('schema', 'trials', 'provenance', 'oos', 'search_candidates', 'sha256'), 'POPULATION_FIELDS')
    require(value['schema'] == POPULATION_SCHEMA, 'POPULATION_SCHEMA')
    require(value['sha256'] == digest({k:v for k,v in value.items() if k != 'sha256'}), 'POPULATION_HASH')
    for name in ('trials', 'provenance', 'oos', 'search_candidates'):
        require(type(value[name]) is list and len(value[name]) <= projection.MAX_ITEMS, 'POPULATION_BOUND')
    previous = ''
    for row in value['trials']:
        projection._fields(row, ('config_hash', 'first_seen_ts'), 'POPULATION_TRIAL')
        projection._text(row['config_hash']); projection._integer(row['first_seen_ts'])
        require(row['config_hash'] > previous, 'POPULATION_ORDER')
        previous = row['config_hash']
    sequence = 0
    for row in value['search_candidates']:
        projection._fields(row, ('candidate_seq', 'search_id', 'config_hash', 'status', 'registered_at',
            'prev_candidate_hash', 'candidate_hash', 'search_identity', 'search_started_at'), 'POPULATION_SEARCH')
        projection._integer(row['candidate_seq'], minimum=1)
        projection._integer(row['registered_at']); projection._integer(row['search_started_at'])
        require(row['candidate_seq'] > sequence and row['status'] == 'REGISTERED'
                and row['registered_at'] >= row['search_started_at'], 'POPULATION_SEARCH_STATE')
        projection._fields(row['search_identity'],
            ('search_id', 'search_kind', 'source', 'dataset_version', 'identity_hash'), 'SEARCH_FIELDS')
        require(row['search_id'] == row['search_identity']['search_id'], 'POPULATION_SEARCH_IDENTITY')
        sequence = row['candidate_seq']
    return value


def verify_population_prefix(retained, current, *, exact=False):
    validate_population(retained); validate_population(current)
    if exact:
        require(canonical(retained) == canonical(current), 'POPULATION_CHANGED')
        return
    current_trials = {r['config_hash']: r for r in current['trials']}
    require(all(canonical(current_trials.get(r['config_hash'])) == canonical(r)
                for r in retained['trials']), 'POPULATION_PREFIX')
    for key in ('provenance', 'oos', 'search_candidates'):
        require(canonical(retained[key]) == canonical(current[key][:len(retained[key])]), 'POPULATION_PREFIX')


def validate_binding(*, snapshot, population, search_identity, config_hash,
                     native_trial_mapping, provenance_trial_mapping, projection_value):
    validate_population(population)
    projection._fields(search_identity,
        ('search_id', 'search_kind', 'source', 'dataset_version', 'identity_hash'), 'SEARCH_FIELDS')
    subject = snapshot['subject']
    require(search_identity['source'] == subject['producer_id']
        and search_identity['search_kind'] == subject['evidence_kind'], 'SEARCH_SUBJECT')
    docs = {k: None if v is None else projection._decode(v) for k,v in snapshot['originals'].items()}
    history = docs['trial_history']
    require(type(native_trial_mapping) is dict and set(native_trial_mapping) == set(history), 'MAPPING_NATIVE_SET')
    trials = {r['config_hash']: r for r in population['trials']}
    require(set(native_trial_mapping.values()) == set(trials), 'POPULATION_COMPLETE')
    for tid, events in history.items():
        first = events[0]
        require(native_trial_mapping[tid] == first['exact_definition_id']
            and trials[native_trial_mapping[tid]]['first_seen_ts'] <= first['at'], 'MAPPING_DEFINITION')
    selected_trial = projection_value['trial_binding']['candidate_trial_id']
    require(config_hash == native_trial_mapping.get(selected_trial), 'MAPPING_CANDIDATE')
    if subject['native_adapter'] == 'GOLD_BAR_FORWARD_V1':
        from quantos.contracts.gold_trial_identity import gold_trial_definition_id
        manifest, report = docs['manifest'], docs['record']
        names = [trial['candidate'] for trial in report['trials']]
        if len(manifest['trial_ids']) == len(names)+1:
            names.append('net_portfolio')
        require(set(manifest['trial_ids']) == set(history), 'MAPPING_NATIVE_PREIMAGES')
        for tid, name in zip(manifest['trial_ids'], names, strict=True):
            expected = gold_trial_definition_id(input_sha256=manifest['input_sha256'],
                candidate=name, source=docs['source_identity'])
            require(history[tid][0]['exact_definition_id'] == expected
                    and history[tid][0]['alpha_id'] == expected, 'MAPPING_NATIVE_PREIMAGE')
    else:
        # This adapter retains one candidate preimage. Other native definitions
        # need their own independently bound preimages, not caller mapping alone.
        require(set(history) == {selected_trial}, 'MAPPING_NATIVE_PREIMAGES')
        expected = projection._digest(docs['candidate_definition'])
        require(history[selected_trial][0]['exact_definition_id'] == expected, 'MAPPING_NATIVE_PREIMAGE')
    provenance = population['provenance']
    candidates = population['search_candidates']
    require({row['config_hash'] for row in candidates} == set(trials), 'UNMAPPED_SEARCH_POPULATION')
    require(type(provenance_trial_mapping) is dict
        and set(provenance_trial_mapping) == {r['trial_id'] for r in provenance}, 'MAPPING_PROVENANCE_SET')
    if provenance:
        require(len(set(provenance_trial_mapping.values())) == len(provenance)
            and set(provenance_trial_mapping.values()) == set(history), 'MAPPING_ATTEMPTS_COMPLETE')
        pairs = {(row['search_id'], row['config_hash']): row for row in candidates}
        require(len(pairs) == len(candidates) == len(provenance)
            and set(pairs) == {(row['research_session_id'], row['config_hash']) for row in provenance},
            'UNMAPPED_SEARCH_ATTEMPTS')
        for row in provenance:
            tid = provenance_trial_mapping[row['trial_id']]
            record = load_json_without_duplicate_keys(row['record_json'].encode())
            candidate = pairs[(row['research_session_id'], row['config_hash'])]
            require(row['config_hash'] == native_trial_mapping[tid]
                and record['started_at'] <= history[tid][0]['at']
                and candidate['registered_at'] <= history[tid][0]['at'], 'MAPPING_ATTEMPT')
    else:
        # A v2 config set deduplicates attempts. Without original per-attempt
        # provenance, only a one-to-one first search is provable here.
        require(len(candidates) == len(trials) == len(history)
            and all(canonical(row['search_identity']) == canonical(search_identity) for row in candidates),
            'UNMAPPED_SEARCH_ATTEMPTS')
        times = {row['config_hash']: row['registered_at'] for row in candidates}
        require(all(times[native_trial_mapping[tid]] <= rows[0]['at'] for tid,rows in history.items()),
                'MAPPING_SEARCH_TIME')
    return docs


def artifact_id(subject, search_identity, config_hash):
    return digest({'domain': 'QUANTOS_RESEARCH_EVIDENCE_ARTIFACT_ID_V1',
                   'subject': subject, 'search_identity': search_identity, 'config_hash': config_hash})


def delivery_time(authenticated_snapshot, *, checked_at_ms, clock_ms):
    """Final clock on an already authenticated snapshot; no independent grant.

    The population verifier may be slower than a signature check. Its caller
    must run this after that verification, using the actual snapshot just read.
    """
    now = projection._integer(clock_ms())
    require(now >= checked_at_ms, 'CLOCK_ROLLBACK')
    for envelope in (authenticated_snapshot['source_envelope'], authenticated_snapshot['result_envelope']):
        key = next(k for k in authenticated_snapshot['trust_registry']['keys'].values()
                   if k['authority_id'] == envelope['authority_id'] and k['key_id'] == envelope['key_id'])
        require(key['not_before_ms'] <= now <= key['not_after_ms'], 'KEY_EXPIRED')
        statement = envelope['statement']
        require(statement['issued_at_ms'] <= now <= statement['expires_at_ms']
            and 0 <= now-statement['revocation_checked_at_ms'] <= authority.MAX_LIFETIME_MS, 'STATEMENT_EXPIRED')
    return now


def prepare(*, snapshot, assessment, population, search_identity, config_hash,
            native_trial_mapping, provenance_trial_mapping):
    value = dict(schema=SCHEMA, snapshot=pack_snapshot(snapshot), assessment=copy.deepcopy(assessment),
        population=copy.deepcopy(population), search_identity=copy.deepcopy(search_identity),
        config_hash=config_hash, native_trial_mapping=copy.deepcopy(native_trial_mapping),
        provenance_trial_mapping=copy.deepcopy(provenance_trial_mapping))
    validate(value)
    return value


def validate(value):
    projection._fields(value, FIELDS, 'ARTIFACT_FIELDS')
    require(value['schema'] == SCHEMA, 'SCHEMA')
    canonical(value)
    snapshot = unpack_snapshot(value['snapshot'])
    assessment = value['assessment']
    projection._fields(assessment, authority.ASSESSMENT_FIELDS, 'ASSESSMENT_FIELDS')
    actual = authority.verify_evidence(**{k:v for k,v in snapshot.items()
        if k not in ('schema', 'read_identity_sha256')}, now_ms=assessment['checked_at_ms'])
    require(canonical(actual) == canonical(assessment), 'ASSESSMENT')
    validate_binding(snapshot=snapshot, population=value['population'],
        search_identity=value['search_identity'], config_hash=value['config_hash'],
        native_trial_mapping=value['native_trial_mapping'],
        provenance_trial_mapping=value['provenance_trial_mapping'], projection_value=actual['projection'])
    require(max(row['at'] for events in projection._decode(snapshot['originals']['trial_history']).values()
                for row in events) <= assessment['checked_at_ms'], 'TRIAL_REVIEW_TIME')
    return value


def row_fields(value):
    validate(value)
    assessment = value['assessment']
    raw = canonical(value)
    return dict(run_id=artifact_id(assessment['subject'], value['search_identity'], value['config_hash']),
        alpha_id=assessment['subject']['candidate_id'], dataset_version=value['search_identity']['dataset_version'],
        recorded_ts=assessment['checked_at_ms'], verdict=assessment['qualification'],
        content_hash=digest(b'QUANTOS_RESEARCH_EVIDENCE_ARTIFACT_CONTENT_V1\0'+raw),
        evaluation_json=raw.decode())


def validate_row(row):
    value = decode(row['evaluation_json'].encode())
    expected = row_fields(value)
    require(canonical({k:row[k] for k in expected}) == canonical(expected), 'ROW_BINDING')
    return value
