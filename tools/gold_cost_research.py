"""Fixed-path diagnostic job retaining every started gold cost experiment."""
import hashlib
import json
import time
import uuid
from pathlib import Path

from quantos.runtime_paths import runtime_state_root
from services.execution.backtest_authority import exact_identity_hash
from services.research.trial_ledger import TrialLedger, TrialRecord
from tools.gold_cost_inputs import load_inputs, MAX_BYTES
from tools.gold_cost_replay import replay_costs
from tools.gold_cost_review import review_request
from tools.gold_cost_receipt import check_receipt
from tools.gold_public_cost_inputs import verify_assembly, SCHEMA as PUBLIC_ASSEMBLY
from tools.perpetual_replay_research import source_identity


def read_completed_experiment(destination):
    """Revalidate retained terminal evidence without executing a simulation."""
    from tools.gold_cost_review import _read
    from services.governance.json_integrity import load_json_without_duplicate_keys
    dest = Path(destination).absolute()
    request = review_request(dest)
    raw = {name: _read(dest, name) for name in ('manifest.json', 'report.json')}
    if any(hashlib.sha256(body).hexdigest() != request['files'][name] for name, body in raw.items()):
        raise ValueError('GOLD_COST_COMPLETED_ARTIFACT_CHANGED')
    manifest, report = (load_json_without_duplicate_keys(raw[name])
                        for name in ('manifest.json', 'report.json'))
    if manifest['terminal'] != 'PASSED':
        raise ValueError('GOLD_COST_FAILED_TRIAL_NOT_RECOVERABLE')
    receipt = check_receipt(dest, now_ms=time.time_ns()//1000000)
    return _completed_summary(manifest, report, receipt)


def _completed_summary(manifest, report, receipt):
    return {'schema': 'GOLD_REGISTERED_COST_REPLAY_V1', 'attempt': manifest['attempt'],
            'trial_id': manifest['trial_id'], 'input_hash': manifest['input_sha256'],
            'result_hash': report['result_hash'], 'report_sha256': manifest['report_sha256'],
            'outcome': report['outcome'], 'reconciliation': report['simulation']['reconciliation'],
            'verification_status': receipt['verification']['status'],
            'authoritative': False, 'evidence_status': 'TECHNICAL_VALIDATION_ONLY'}


def _write(path, value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    with path.open('xb') as stream:
        stream.write(raw)
    return hashlib.sha256(raw).hexdigest()


def run_replay(params):
    if (type(params) is not dict or set(params) != {'attempt'}
            or type(params['attempt']) is not int or not 1 <= params['attempt'] <= 1000):
        raise ValueError('INVALID_GOLD_COST_ATTEMPT')
    root = runtime_state_root()/'data/cache/gold_cost_replay'
    source = root/'inputs'/f"{params['attempt']}.json"
    output = root/'results'
    if (source.resolve() != source.absolute() or not source.is_file()
            or output.resolve() != output.absolute()):
        raise ValueError('INVALID_GOLD_COST_PATH')
    with source.open('rb') as stream:
        raw = stream.read(MAX_BYTES+1)
    if len(raw) > MAX_BYTES:
        raise ValueError('GOLD_COST_INPUT_TOO_LARGE')
    return archive_experiment(raw, output/str(params['attempt']), attempt=params['attempt'])


def archive_experiment(raw, destination, *, attempt, origin=None):
    if (type(raw) is not bytes or len(raw) > MAX_BYTES
            or type(attempt) is not int or not 1 <= attempt <= 1000):
        raise ValueError('INVALID_GOLD_COST_ARCHIVE_INPUT')
    dest = Path(destination).absolute()
    if dest.resolve() != dest:
        raise ValueError('INVALID_GOLD_COST_PATH')
    if origin is not None:
        from tools.gold_ai_origin import verify_origin, encode
        if len(encode(origin)) > MAX_BYTES:
            raise ValueError('GOLD_AI_ORIGIN_TOO_LARGE')
        verify_origin(origin, raw)
    release = source_identity()
    input_hash = hashlib.sha256(raw).hexdigest()
    definition = exact_identity_hash({'input': input_hash, 'release': release}, name='gold_cost_trial')
    policy = {'mode': 'RETROSPECTIVE', 'success': 'TECHNICAL_COMPUTATION_ONLY'}
    trial_id = exact_identity_hash({'definition': definition, 'invocation': uuid.uuid4().hex},
                                   name='trial')
    identity = dict(trial_id=trial_id, alpha_id=definition, exact_definition_id=definition,
                    family_id=exact_identity_hash('GOLD_ZONE_COST_REPLAY_V1', name='family'),
                    validation_policy_digest=exact_identity_hash(policy, name='policy'))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.mkdir()  # Never resume by overwriting retained evidence.
    with (dest/'input.json').open('xb') as stream:
        stream.write(raw)
    origin_hash = _write(dest/'research-origin.json', origin) if origin is not None else None
    with TrialLedger(str(dest/'trials.db')) as ledger:
        for index, status in enumerate(('PLANNED', 'RUNNING')):
            ledger.append(TrialRecord(**identity, status=status, event_index=index,
                                      at=time.time_ns()//1000000))
        try:
            arguments = load_inputs(raw)
            if arguments['release_identity'] != release:
                raise ValueError('GOLD_COST_SOURCE_VERSION_MISMATCH')
            market = arguments['market_identity']
            if isinstance(market, dict) and market.get('schema') == PUBLIC_ASSEMBLY:
                verify_assembly(arguments)
            report = replay_costs(**arguments)
            if source_identity() != release:
                raise ValueError('GOLD_COST_SOURCE_CHANGED')
            terminal = 'PASSED'
        except Exception as exc:
            report = {'status': 'REJECTED', 'error_type': type(exc).__name__,
                      'authoritative': False, 'evidence_status': 'TECHNICAL_VALIDATION_ONLY'}
            terminal = 'FAILED'
        report_hash = _write(dest/'report.json', report)
        ledger.append(TrialRecord(**identity, status=terminal, event_index=2,
                                  at=time.time_ns()//1000000))
        ledger.verify()
    manifest = {'schema': 'GOLD_COST_ARCHIVE_V1', 'attempt': attempt,
                **({'research_origin_sha256': origin_hash} if origin_hash is not None else {}),
                'trial_id': trial_id, 'definition_id': definition, 'input_sha256': input_hash,
                'release_identity': release, 'report_sha256': report_hash,
                'policy': policy, 'terminal': terminal, 'authoritative': False,
                'preregistered': False, 'evidence_status': 'TECHNICAL_VALIDATION_ONLY',
                'limitations': ['LOCAL_TRIAL_LEDGER_NOT_GLOBAL_REGISTRY',
                               'SOURCE_IDENTITY_NOT_ARCHIVED_SOURCE_BYTES',
                               'NO_ECONOMIC_ADMISSION']}
    _write(dest/'manifest.json', manifest)
    review_request(dest)
    receipt = check_receipt(dest, now_ms=time.time_ns()//1000000)
    if terminal != 'PASSED':
        raise ValueError('GOLD_COST_TRIAL_FAILED_SEE_RETAINED_ARCHIVE')
    return _completed_summary(manifest, report, receipt)
