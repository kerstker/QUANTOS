"""Prepare immutable review material from retained gold trials, without signing."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from services.execution.backtest_authority import exact_identity_hash
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.trial_ledger import TrialLedger
from tools.gold_cost_inputs import load_inputs, MAX_BYTES


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _read(root, name):
    path = root/name
    if path.resolve() != path.absolute() or not path.is_file():
        raise ValueError('GOLD_REVIEW_UNSAFE_PATH')
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES+1)
    if len(raw) > MAX_BYTES:
        raise ValueError('GOLD_REVIEW_FILE_TOO_LARGE')
    return raw


def review_request(directory):
    root = Path(directory).absolute()
    raw = {name: _read(root, name) for name in ('input.json', 'report.json', 'manifest.json')}
    hashes = {name: hashlib.sha256(body).hexdigest() for name, body in raw.items()}
    manifest = load_json_without_duplicate_keys(raw['manifest.json'])
    report = load_json_without_duplicate_keys(raw['report.json'])
    if not isinstance(manifest, dict) or not isinstance(report, dict):
        raise ValueError('GOLD_REVIEW_DOCUMENT_SHAPE')
    if 'research_origin_sha256' in manifest:
        from tools.gold_ai_origin import verify_origin
        origin_raw = _read(root, 'research-origin.json')
        origin_hash = hashlib.sha256(origin_raw).hexdigest()
        if origin_hash != manifest['research_origin_sha256']:
            raise ValueError('GOLD_REVIEW_ORIGIN_BINDING')
        verify_origin(load_json_without_duplicate_keys(origin_raw), raw['input.json'])
        hashes['research-origin.json'] = origin_hash
    policy = {'mode': 'RETROSPECTIVE', 'success': 'TECHNICAL_COMPUTATION_ONLY'}
    if (manifest.get('schema') != 'GOLD_COST_ARCHIVE_V1'
            or manifest.get('authoritative') is not False or manifest.get('preregistered') is not False
            or manifest.get('evidence_status') != 'TECHNICAL_VALIDATION_ONLY'
            or manifest.get('terminal') not in ('PASSED', 'FAILED')
            or type(manifest.get('attempt')) is not int or not 1 <= manifest['attempt'] <= 1000
            or manifest.get('policy') != policy
            or manifest.get('input_sha256') != hashes['input.json']
            or manifest.get('report_sha256') != hashes['report.json']):
        raise ValueError('GOLD_REVIEW_MANIFEST_BINDING')
    definition = exact_identity_hash({'input': hashes['input.json'],
                                     'release': manifest['release_identity']}, name='gold_cost_trial')
    if manifest['definition_id'] != definition:
        raise ValueError('GOLD_REVIEW_DEFINITION_BINDING')
    ledger_path = root/'trials.db'
    if ledger_path.resolve() != ledger_path or not ledger_path.is_file():
        raise ValueError('GOLD_REVIEW_UNSAFE_LEDGER')
    with TrialLedger(str(ledger_path), create=False, read_only=True) as ledger:
        ledger.verify()
        trial = ledger.load(manifest['trial_id'])
        if (trial is None or trial.alpha_id != definition or trial.exact_definition_id != definition
                or trial.family_id != exact_identity_hash('GOLD_ZONE_COST_REPLAY_V1', name='family')
                or trial.validation_policy_digest != exact_identity_hash(policy, name='policy')
                or [e.status for e in trial.events] != ['PLANNED', 'RUNNING', manifest['terminal']]):
            raise ValueError('GOLD_REVIEW_TRIAL_BINDING')
        history = [asdict(event) for event in trial.events]
    if (report.get('authoritative') is not False
            or report.get('evidence_status') != 'TECHNICAL_VALIDATION_ONLY'):
        raise ValueError('GOLD_REVIEW_REPORT_SCOPE')
    if manifest['terminal'] == 'PASSED':
        arguments = load_inputs(raw['input.json'])
        body = {k: v for k, v in report.items() if k != 'result_hash'}
        engine = report['simulation']
        if (report.get('schema') != 'GOLD_COST_REPLAY_V1'
                or report['result_hash'] != exact_identity_hash(body, name='gold_cost_replay')
                or arguments['release_identity'] != manifest['release_identity']
                or report['signal_input_sha256'] != hashlib.sha256(_encode(arguments['document'])).hexdigest()
                or engine.get('authoritative') is not False
                or engine.get('evidence_status') != 'TECHNICAL_VALIDATION_ONLY'
                or engine['result_hash'] != exact_identity_hash(
                    {k: v for k, v in engine.items() if k != 'result_hash'}, name='perpetual_simulation_result')):
            raise ValueError('GOLD_REVIEW_REPORT_BINDING')
    elif report.get('status') != 'REJECTED':
        raise ValueError('GOLD_REVIEW_FAILED_REPORT')
    request = {'schema': 'GOLD_INDEPENDENT_REVIEW_REQUEST_V1',
        'status': 'INDEPENDENT_REVIEW_REQUIRED', 'attempt': manifest['attempt'],
        'trial_id': manifest['trial_id'], 'terminal': manifest['terminal'], 'files': hashes,
        'trial_history': history, 'release_identity': manifest['release_identity'],
        'authoritative': False, 'economic_eligible': False,
        'requested_checks': ['SOURCE_AUTHENTICATION', 'ARCHIVED_SOURCE_REPRODUCTION',
            'CAUSAL_TIMES', 'ACCOUNT_COST_AND_VENUE_RULES', 'LOCKED_OOS', 'GLOBAL_TRIAL_HISTORY'],
        'limitations': ['LOCAL_STRUCTURAL_BINDING_ONLY', 'NOT_AN_INDEPENDENT_VERDICT',
                        'ONE_TRIAL_NOT_GLOBAL_TRIAL_HISTORY', 'SOURCE_BYTES_NOT_IN_THIS_ARCHIVE']}
    encoded = _encode(request)
    target = root/'independent-request.json'
    if target.exists() or target.is_symlink():
        if _read(root, target.name) != encoded:
            raise ValueError('GOLD_REVIEW_REQUEST_CHANGED')
    else:
        with target.open('xb') as stream:
            stream.write(encoded)
    return request
