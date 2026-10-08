"""Bounded, non-authorizing identity of one pre-registered native invocation.

The plan is an immutable description, not proof of registration or execution.
Consumers must read the original registry rows and the native ledger separately.
"""
import hashlib
import json
import re

from quantos.contracts.backtest_identity import decode_identity_payload, exact_identity_hash
from quantos.contracts.gold_trial_identity import gold_trial_definition_id

SCHEMA = 'QUANTOS_REGISTERED_RESEARCH_RUN_V1'
EVALUATED_SCHEMA = 'QUANTOS_REGISTERED_RESEARCH_RUN_V2'
RISK_EVALUATED_SCHEMA = 'QUANTOS_REGISTERED_RESEARCH_RUN_V3'
FORWARD_DECLARED_SCHEMA = 'QUANTOS_REGISTERED_GOLD_FORWARD_RUN_V1'
CHUNKED_GOLD_SCHEMA = 'QUANTOS_REGISTERED_GOLD_CHUNKED_RUN_V1'
CHUNKED_GOLD_FORWARD_SCHEMA = 'QUANTOS_REGISTERED_GOLD_CHUNKED_FORWARD_RUN_V1'
STREAMED_GOLD_SCHEMA = 'QUANTOS_REGISTERED_GOLD_CHUNKED_RUN_V2'
STREAMED_GOLD_FORWARD_SCHEMA = 'QUANTOS_REGISTERED_GOLD_CHUNKED_FORWARD_RUN_V2'
SOURCE_GOLD_SCHEMA = 'QUANTOS_REGISTERED_GOLD_CHUNKED_RUN_V3'
SOURCE_GOLD_FORWARD_SCHEMA = 'QUANTOS_REGISTERED_GOLD_CHUNKED_FORWARD_RUN_V3'
BUDGETED_SOURCE_GOLD_SCHEMA = 'QUANTOS_REGISTERED_GOLD_CHUNKED_RUN_V4'
BUDGETED_SOURCE_GOLD_FORWARD_SCHEMA = 'QUANTOS_REGISTERED_GOLD_CHUNKED_FORWARD_RUN_V4'
SOURCE_REANALYSIS_SCHEMA = 'QUANTOS_REGISTERED_GOLD_REANALYSIS_RUN_V1'
BUDGETED_SOURCE_REANALYSIS_SCHEMA = 'QUANTOS_REGISTERED_GOLD_REANALYSIS_RUN_V2'
EVALUATED_SOURCE_REANALYSIS_SCHEMA = 'QUANTOS_REGISTERED_GOLD_REANALYSIS_RUN_V3'
BUDGETED_EVALUATED_SOURCE_REANALYSIS_SCHEMA = 'QUANTOS_REGISTERED_GOLD_REANALYSIS_RUN_V4'
EVALUATED_SOURCE_REANALYSIS_SCHEMAS = (EVALUATED_SOURCE_REANALYSIS_SCHEMA,
                                     BUDGETED_EVALUATED_SOURCE_REANALYSIS_SCHEMA)
SOURCE_REANALYSIS_SCHEMAS = (SOURCE_REANALYSIS_SCHEMA, BUDGETED_SOURCE_REANALYSIS_SCHEMA,
                           *EVALUATED_SOURCE_REANALYSIS_SCHEMAS)
BUDGETED_SOURCE_GOLD_SCHEMAS = (BUDGETED_SOURCE_GOLD_SCHEMA, BUDGETED_SOURCE_GOLD_FORWARD_SCHEMA,
                              BUDGETED_SOURCE_REANALYSIS_SCHEMA, BUDGETED_EVALUATED_SOURCE_REANALYSIS_SCHEMA)
SOURCE_GOLD_SCHEMAS = (SOURCE_GOLD_SCHEMA, SOURCE_GOLD_FORWARD_SCHEMA, SOURCE_REANALYSIS_SCHEMA,
                      EVALUATED_SOURCE_REANALYSIS_SCHEMA, *BUDGETED_SOURCE_GOLD_SCHEMAS)
STREAMED_GOLD_SCHEMAS = (STREAMED_GOLD_SCHEMA, STREAMED_GOLD_FORWARD_SCHEMA, *SOURCE_GOLD_SCHEMAS)
CHUNKED_GOLD_SCHEMAS = (CHUNKED_GOLD_SCHEMA, CHUNKED_GOLD_FORWARD_SCHEMA, *STREAMED_GOLD_SCHEMAS)
FORWARD_GOLD_SCHEMAS = (FORWARD_DECLARED_SCHEMA, CHUNKED_GOLD_FORWARD_SCHEMA, STREAMED_GOLD_FORWARD_SCHEMA,
                       SOURCE_GOLD_FORWARD_SCHEMA, BUDGETED_SOURCE_GOLD_FORWARD_SCHEMA)
EVALUATED_SCHEMAS = (EVALUATED_SCHEMA, RISK_EVALUATED_SCHEMA, *EVALUATED_SOURCE_REANALYSIS_SCHEMAS)
GOLD = 'GOLD_BAR_FORWARD_V1'
TECHNICAL = 'TECHNICAL_LINEAR_PERPETUAL_V2'
MAX_PLAN_BYTES = 65_536
MAX_INPUT_BYTES = 16_000_000
MAX_OUTPUT_BYTES = 16_000_000
MAX_RUN_BYTES = 64_000_000
FIELDS = frozenset('schema invocation_id adapter search actor_id input_sha256 source_identity trials submission_enabled economic_authority'.split())
TRIAL_FIELDS = frozenset('candidate_id config_hash native_trial_id provenance_trial_id alpha_id family_id validation_policy_digest definition_preimage'.split())


def require(condition, code):
    if not condition:
        raise ValueError('REGISTERED_RESEARCH_'+code)


def canonical(value, *, maximum=MAX_INPUT_BYTES, max_nodes=100_000):
    from quantos.contracts.json_integrity import ascii_json_string_size
    require(type(max_nodes) is int and 1 <= max_nodes <= 1_000_000, 'COMPLEXITY_LIMIT')
    nodes=0
    budget=0
    def bounded(item,depth=0):
        nonlocal nodes,budget
        nodes+=1
        if depth>64 or nodes>max_nodes:
            require(False,'COMPLEXITY')
        kind=type(item)
        if kind is bool:
            budget+=5 if item is False else 4
        elif item is None:
            budget+=4
        elif kind is str:
            if not len(item)<=maximum:
                require(False,'BYTES')
            ascii_size=ascii_json_string_size(item,maximum_bytes=maximum-budget)
            if ascii_size is not None:
                budget+=ascii_size
            else:
                budget+=len(item.encode('utf-8'))+2
                for character in item:
                    if character in ('"','\\','\b','\f','\n','\r','\t'):
                        budget+=1
                    elif ord(character)<32:
                        budget+=5
        elif kind is dict:
            budget+=2*len(item)+2
            for k,v in item.items():
                if type(k) is not str:
                    require(False,'JSON_KEY')
                bounded(k,depth+1)
                bounded(v,depth+1)
        elif kind is list:
            budget+=len(item)+2
            for child in item:
                bounded(child,depth+1)
        elif kind is int:
            if item.bit_length()>4096:
                require(False,'INTEGER')
            budget+=len(str(item))
        elif kind is float:
            budget+=len(repr(item))
        else:
            require(False,'JSON_TYPE')
        if not budget<=maximum:
            require(False,'BYTES')
    bounded(value)
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError('REGISTERED_RESEARCH_JSON') from exc
    require(len(raw) <= maximum, 'BYTES')
    return raw


def digest(value):
    return hashlib.sha256(value if type(value) is bytes else canonical(value)).hexdigest()


def native_trial_id(invocation_id, candidate_id, config_hash):
    return digest(dict(schema='QUANTOS_REGISTERED_NATIVE_TRIAL_ID_V1', invocation_id=invocation_id,
                       candidate_id=candidate_id, config_hash=config_hash))


def _text(value):
    return type(value) is str and 0 < len(value) <= 200 and value == value.strip()


def _hash(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def validate_history_storage(value):
    require(type(value) is dict and set(value) == {'schema', 'maximum_bytes'}, 'GOLD_HISTORY_FIELDS')
    require(value['schema'] == 'QUANTOS_GOLD_HISTORY_STORAGE_V1'
        and type(value['maximum_bytes']) is int and 0 < value['maximum_bytes'] <= 32*1024**3,
        'GOLD_HISTORY_BUDGET')
    return value


def validate_plan(value):
    require(type(value) is dict, 'PLAN_FIELDS')
    evaluated = value.get('schema') in EVALUATED_SCHEMAS
    risk_evaluated = value.get('schema') == RISK_EVALUATED_SCHEMA
    forward_declared = value.get('schema') in FORWARD_GOLD_SCHEMAS
    chunked_gold = value.get('schema') in CHUNKED_GOLD_SCHEMAS
    streamed_gold = value.get('schema') in STREAMED_GOLD_SCHEMAS
    source_gold = value.get('schema') in SOURCE_GOLD_SCHEMAS
    expected = FIELDS | ({'evaluation_core'} if evaluated else set()) | ({'risk_review_policy'} if risk_evaluated else set()) | ({'forward_design'} if forward_declared else set()) | ({'result_storage'} if chunked_gold else set()) | ({'history_storage'} if streamed_gold else set()) | ({'source_storage'} if source_gold else set())
    require(set(value) == expected, 'PLAN_FIELDS')
    canonical(value, maximum=MAX_PLAN_BYTES)
    require(value['schema'] in (SCHEMA, FORWARD_DECLARED_SCHEMA, *EVALUATED_SCHEMAS, *CHUNKED_GOLD_SCHEMAS)
        and _hash(value['invocation_id']) and _hash(value['input_sha256']), 'PLAN_IDENTITY')
    require(value['adapter'] in (GOLD, TECHNICAL), 'ADAPTER')
    if chunked_gold:
        from quantos.contracts.gold_chunked_result import storage
        require(value['adapter'] == GOLD, 'GOLD_RESULT_ADAPTER')
        storage(value['result_storage'])
    if streamed_gold:
        validate_history_storage(value['history_storage'])
    if source_gold:
        from quantos.contracts.gold_native_source import storage, BUDGETED_STORAGE_SCHEMA
        storage(value['source_storage'])
        require((value['schema'] in BUDGETED_SOURCE_GOLD_SCHEMAS)
            == (value['source_storage']['schema'] == BUDGETED_STORAGE_SCHEMA), 'SOURCE_BUDGET_VERSION')
    require(value['submission_enabled'] is False and value['economic_authority'] is False, 'AUTHORITY')
    require(_text(value['actor_id']), 'ACTOR')
    search = value['search']
    require(type(search) is dict and set(search) == set('search_id search_kind source dataset_version recorded_at_ms'.split()), 'SEARCH_FIELDS')
    require(all(_text(search[k]) for k in ('search_id','search_kind','source','dataset_version'))
            and type(search['recorded_at_ms']) is int and 0 <= search['recorded_at_ms'] <= 2**63-1, 'SEARCH')
    require(search['search_kind'] == ('FORWARD' if value['adapter'] == GOLD
        and value['schema'] not in SOURCE_REANALYSIS_SCHEMAS else 'BACKTEST'), 'SEARCH_KIND')
    source=value['source_identity']
    require(type(source) is dict and set(source)==set('commit python_source_sha256 dependency_lock_sha256 python_version architecture'.split())
        and type(source['commit']) is str and re.fullmatch('[0-9a-f]{40}',source['commit']) is not None
        and _hash(source['python_source_sha256']) and _hash(source['dependency_lock_sha256'])
        and _text(source['python_version']) and _text(source['architecture']),'SOURCE')
    trials = value['trials']
    require(type(trials) is list and 0 < len(trials) <= (4 if value['adapter'] == GOLD else 1), 'TRIALS')
    seen = set()
    for trial in trials:
        require(type(trial) is dict and set(trial) == TRIAL_FIELDS, 'TRIAL_FIELDS')
        require(_text(trial['candidate_id']) and trial['candidate_id'] not in seen, 'CANDIDATE')
        seen.add(trial['candidate_id'])
        require(all(_hash(trial[k]) for k in ('config_hash','native_trial_id','alpha_id','family_id','validation_policy_digest')), 'TRIAL_HASHES')
        require(trial['native_trial_id'] == native_trial_id(value['invocation_id'],trial['candidate_id'],trial['config_hash'])
                and trial['provenance_trial_id'] == search['search_id']+':'+trial['config_hash'], 'TRIAL_BINDING')
        preimage = trial['definition_preimage']
        if value['adapter'] == GOLD:
            require(type(preimage) is dict and set(preimage) == {'input_sha256','candidate','source'}, 'GOLD_PREIMAGE')
            require(canonical(preimage) == canonical(dict(input_sha256=value['input_sha256'], candidate=trial['candidate_id'], source=value['source_identity'])), 'GOLD_PREIMAGE_BINDING')
            expected = gold_trial_definition_id(**preimage)
        else:
            require(type(preimage) is dict and set(preimage) == {'candidate_definition','alpha_identity','alpha_hash'}, 'TECHNICAL_PREIMAGE')
            alpha = decode_identity_payload(preimage['alpha_identity'])
            require(type(alpha) is dict and alpha.get('alpha_id') == trial['candidate_id']
                    and exact_identity_hash(alpha) == preimage['alpha_hash'], 'TECHNICAL_ALPHA')
            # Preserve native Decimal spelling; the original projection checks
            # these same two explicit direct-target quantity fields.
            definition = dict(alpha)
            mapping = dict(definition.get('position_mapping', {}))
            for field in ('quantity_scale','max_abs_quantity'):
                require(field in mapping, 'TECHNICAL_MAPPING')
                mapping[field] = str(mapping[field])
            definition['position_mapping'] = mapping
            require(canonical(definition) == canonical(preimage['candidate_definition']), 'TECHNICAL_DEFINITION')
            expected = digest(preimage['candidate_definition'])
        require(trial['config_hash'] == expected and trial['alpha_id'] == expected, 'DEFINITION')
    if forward_declared:
        from quantos.contracts.gold_forward_evaluation_design import bind_plan
        bind_plan(value['forward_design'], value)
    if evaluated:
        from quantos.contracts.research_evaluation_plan import validate_core, core_sha256, GOLD_REANALYSIS_SCHEMA
        core = validate_core(value['evaluation_core'])
        gold_evaluated = value['schema'] in EVALUATED_SOURCE_REANALYSIS_SCHEMAS
        require(value['adapter'] == (GOLD if gold_evaluated else TECHNICAL)
            and (not gold_evaluated or core['schema'] == GOLD_REANALYSIS_SCHEMA), 'EVALUATION_ADAPTER')
        require(core['native_adapter'] == value['adapter']
            and core['evidence_kind'] == search['search_kind']
            and core['registered_at_ms'] == search['recorded_at_ms']
            and core['input_sha256'] == value['input_sha256']
            and core['source_identity_sha256'] == digest(source), 'EVALUATION_BINDING')
        require(core['cohort'] == [{k:t[k] for k in ('candidate_id', 'config_hash', 'native_trial_id')}
                for t in trials] and core['budget']['max_invocations'] == 1, 'EVALUATION_COHORT')
        policy_digest = core_sha256(core)
        if risk_evaluated:
            from quantos.contracts.registered_risk_replay import validate_policy, validation_policy_sha256
            validate_policy(value['risk_review_policy'], input_sha256=value['input_sha256'],
                source_identity_sha256=digest(source), evaluation_core_sha256=core_sha256(core),
                native_trial_id=trials[0]['native_trial_id'])
            policy_digest = validation_policy_sha256(core, value['risk_review_policy'])
        require(all(t['validation_policy_digest'] == policy_digest for t in trials), 'EVALUATION_POLICY')
    return value
