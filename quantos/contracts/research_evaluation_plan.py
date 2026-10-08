"""Non-authorizing, pre-execution evaluation declarations.

The core is hashed before the registered run plan, registry provenance and
invocation exist. It contains none of their hashes and never authenticates its
own registration time. The producer and fixed reader must bind this exact core
to the original PLANNED prefix before computation. Historical sample timestamps
are independent of that real registration time; FORWARD keeps the later window.

OOS access identifiers are declarations to check against retained originals.
Even an apparently unseen boundary is UNKNOWN here, never independently locked
or eligible. Statistics and validation policy execution remain in their original
implementations; this module only binds their declared identities.
"""
import hashlib
import re

from quantos.contracts.registered_research_run import canonical

SCHEMA = 'QUANTOS_RESEARCH_EVALUATION_CORE_V2'
GOLD_REANALYSIS_SCHEMA = 'QUANTOS_GOLD_REANALYSIS_EVALUATION_CORE_V1'
GOLD_REANALYSIS_OOS_SCHEMA = 'QUANTOS_GOLD_REANALYSIS_EVALUATION_CORE_V2'
GOLD_REANALYSIS_SCHEMAS = (GOLD_REANALYSIS_SCHEMA, GOLD_REANALYSIS_OOS_SCHEMA)
GOLD_OOS_MATERIAL_METHOD = 'ORIGINAL_RECEIPT_AVAILABLE_NET_ACCOUNT_AND_CASH_BASELINE_GRID_V1'
STATUS_SCHEMA = 'QUANTOS_RESEARCH_EVALUATION_CORE_STATUS_V2'
MAX_BYTES = 65_536
MAX_COHORT = 1000
MAX_POINTS = 100_000
ADAPTERS = {'TECHNICAL_LINEAR_PERPETUAL_V2': 'BACKTEST', 'GOLD_BAR_FORWARD_V1': 'FORWARD'}
FIELDS = frozenset('schema evidence_kind native_adapter registered_at_ms input_sha256 '
    'source_identity_sha256 dataset_manifest_sha256 cohort budget sample splits selection '
    'metrics benchmark termination oos_access submission_enabled economic_authority'.split())


def _require(value, reason):
    if not value:
        raise ValueError('RESEARCH_EVALUATION_CORE_' + reason)


def _fields(value, fields, reason):
    _require(type(value) is dict and set(value) == set(fields), reason)


def _integer(value, *, minimum=0, maximum=2**63-1):
    _require(type(value) is int and minimum <= value <= maximum, 'INTEGER')


def _text(value):
    _require(type(value) is str and 0 < len(value) <= 200
             and value == value.strip() and '\x00' not in value, 'TEXT')


def _hash(value):
    _require(type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None, 'HASH')


def validate_core(value):
    """Validate declarations only; preserve the caller's original bytes and time."""
    _require(type(value) is dict, 'FIELDS')
    oos_material = value.get('schema') == GOLD_REANALYSIS_OOS_SCHEMA
    _fields(value, FIELDS | ({'oos_material_method'} if oos_material else set()), 'FIELDS')
    canonical(value, maximum=MAX_BYTES)
    gold_reanalysis = value['schema'] in GOLD_REANALYSIS_SCHEMAS
    _require(type(value['native_adapter']) is str and type(value['evidence_kind']) is str
        and ((gold_reanalysis and value['native_adapter'] == 'GOLD_BAR_FORWARD_V1'
              and value['evidence_kind'] == 'BACKTEST')
             or (value['schema'] == SCHEMA and value['native_adapter'] in ADAPTERS
                 and ADAPTERS[value['native_adapter']] == value['evidence_kind'])), 'SCHEMA')
    _require(value['submission_enabled'] is False and value['economic_authority'] is False, 'AUTHORITY')
    _integer(value['registered_at_ms'])
    for key in ('input_sha256', 'source_identity_sha256', 'dataset_manifest_sha256'):
        _hash(value[key])

    budget = value['budget']
    _fields(budget, ('max_trials', 'max_invocations'), 'BUDGET_FIELDS')
    _integer(budget['max_trials'], minimum=1, maximum=MAX_COHORT)
    _integer(budget['max_invocations'], minimum=1, maximum=budget['max_trials'])
    cohort = value['cohort']
    _require(type(cohort) is list and 0 < len(cohort) <= budget['max_trials'], 'COHORT')
    definitions, trial_ids = {}, set()
    for row in cohort:
        _fields(row, ('candidate_id', 'config_hash', 'native_trial_id'), 'COHORT_FIELDS')
        _text(row['candidate_id']); _hash(row['config_hash']); _hash(row['native_trial_id'])
        _require(row['native_trial_id'] not in trial_ids, 'DUPLICATE_TRIAL')
        trial_ids.add(row['native_trial_id'])
        _require(definitions.setdefault(row['candidate_id'], row['config_hash']) == row['config_hash'],
                 'CANDIDATE_DEFINITION')

    sample = value['sample']
    _fields(sample, ('start_ms', 'end_ms', 'frequency_ms'), 'SAMPLE_FIELDS')
    _integer(sample['start_ms']); _integer(sample['end_ms'])
    _integer(sample['frequency_ms'], minimum=1, maximum=86_400_000)
    _require(sample['start_ms'] <= sample['end_ms'] and
        (sample['end_ms'] - sample['start_ms']) // sample['frequency_ms'] + 1 <= MAX_POINTS, 'SAMPLE')
    if value['evidence_kind'] == 'FORWARD':
        _require(value['registered_at_ms'] <= sample['start_ms'], 'FORWARD_REGISTRATION')
    splits = value['splits']
    _fields(splits, ('train', 'validation', 'locked_oos'), 'SPLIT_FIELDS')
    previous = None
    for name in ('train', 'validation', 'locked_oos'):
        window = splits[name]
        if window is None:
            continue
        _fields(window, ('start_ms', 'end_ms'), 'WINDOW_FIELDS')
        _integer(window['start_ms']); _integer(window['end_ms'])
        _require(sample['start_ms'] <= window['start_ms'] <= window['end_ms'] <= sample['end_ms'], 'WINDOW')
        _require(all((window[k] - sample['start_ms']) % sample['frequency_ms'] == 0
                     for k in ('start_ms', 'end_ms')), 'WINDOW_GRID')
        _require(previous is None or previous < window['start_ms'], 'WINDOW_ORDER')
        previous = window['end_ms']

    selection = value['selection']
    _fields(selection, ('method', 'candidate_id', 'validation_policy_sha256'), 'SELECTION_FIELDS')
    _hash(selection['validation_policy_sha256'])
    _require(selection['method'] in ('PREDECLARED_CANDIDATE', 'TRAIN_ONLY'), 'SELECTION_METHOD')
    if selection['method'] == 'PREDECLARED_CANDIDATE':
        _text(selection['candidate_id'])
        _require(selection['candidate_id'] in definitions, 'SELECTION_CANDIDATE')
    else:
        _require(selection['candidate_id'] is None and splits['train'] is not None, 'TRAIN_SELECTION')
    # These identifiers are the actual output of fixed_frequency_equity.
    _fields(value['metrics'], ('schema', 'calendar'), 'METRIC_FIELDS')
    _require(value['metrics'] == dict(schema='PERPETUAL_FIXED_FREQUENCY_EQUITY_V1',
        calendar='ELAPSED_UTC_MILLISECONDS_365_DAY_YEAR'), 'METRIC_POLICY')
    benchmark = value['benchmark']
    _fields(benchmark, ('id', 'definition_sha256', 'input_sha256'), 'BENCHMARK_FIELDS')
    _text(benchmark['id']); _hash(benchmark['definition_sha256']); _hash(benchmark['input_sha256'])
    termination = value['termination']
    _fields(termination, ('policy_sha256', 'on_invalid_inputs', 'on_missing_evidence', 'retain_eliminated'),
            'TERMINATION_FIELDS')
    _hash(termination['policy_sha256'])
    _require(termination['on_invalid_inputs'] == 'REJECT'
        and termination['on_missing_evidence'] == 'INSUFFICIENT'
        and termination['retain_eliminated'] is True, 'TERMINATION_POLICY')
    access = value['oos_access']
    _fields(access, ('boundary_id', 'history_sha256', 'viewed_boundary_ids'), 'OOS_ACCESS_FIELDS')
    if access['boundary_id'] is not None:
        _text(access['boundary_id'])
    if access['history_sha256'] is not None:
        _hash(access['history_sha256'])
    viewed = access['viewed_boundary_ids']
    _require(type(viewed) is list and len(viewed) <= MAX_COHORT, 'OOS_ACCESS_BOUND')
    for boundary in viewed:
        _text(boundary)
    _require(len(set(viewed)) == len(viewed), 'OOS_ACCESS_DUPLICATE')
    if gold_reanalysis:
        _statistical_sample(value)
    if oos_material:
        _require(value['oos_material_method'] == GOLD_OOS_MATERIAL_METHOD
            and value['splits']['locked_oos'] is not None
            and value['selection']['method'] == 'PREDECLARED_CANDIDATE', 'GOLD_OOS_MATERIAL')
    return value


def _statistical_sample(value):
    if value['splits']['locked_oos'] is None:
        return dict(value['sample'])
    windows = [value['splits'][name] for name in ('train', 'validation')
               if value['splits'][name] is not None]
    _require(bool(windows), 'STATISTICAL_WINDOW_MISSING')
    frequency = value['sample']['frequency_ms']
    _require(all(left['end_ms'] + frequency == right['start_ms']
                 for left, right in zip(windows[:-1], windows[1:], strict=True)),
             'STATISTICAL_CONTIGUITY')
    return dict(start_ms=windows[0]['start_ms'], end_ms=windows[-1]['end_ms'], frequency_ms=frequency)


def statistical_sample(value):
    """Predeclared continuous train/validation grid; never include locked OOS."""
    validate_core(value)
    return _statistical_sample(value)


def core_sha256(value):
    return hashlib.sha256(canonical(validate_core(value), maximum=MAX_BYTES)).hexdigest()


def core_status(value):
    """Report missing/declared OOS evidence, never an independently verified PASS."""
    validate_core(value)
    access = value['oos_access']
    reasons = ['PRE_REGISTRATION_NOT_INDEPENDENTLY_VERIFIED', 'NO_RESEARCH_OR_EXECUTION_AUTHORITY']
    insufficient = False
    for absent, reason in ((value['splits']['locked_oos'] is None, 'LOCKED_OOS_WINDOW_MISSING'),
            (access['boundary_id'] is None, 'OOS_BOUNDARY_MISSING'),
            (access['history_sha256'] is None, 'OOS_ACCESS_HISTORY_MISSING'),
            (access['boundary_id'] in access['viewed_boundary_ids'], 'LOCKED_OOS_ALREADY_VIEWED')):
        if absent:
            insufficient = True
            reasons.append(reason)
    reasons.append('OOS_ACCESS_NOT_INDEPENDENTLY_VERIFIED')
    return dict(schema=STATUS_SCHEMA, core_sha256=core_sha256(value),
        oos_status='INSUFFICIENT' if insufficient else 'UNKNOWN', reasons=sorted(reasons),
        submission_enabled=False, economic_authority=False)
