"""Offline gold signal replay; stdout report, no network or account access.

python -m tools.gold_signal_replay --input /path/to/normalized-archive.json
"""
import argparse
from decimal import InvalidOperation
import hashlib
import json
from pathlib import Path
import platform
import time
import uuid

from services.research.gold_trade_observation import load_document, MAX_BYTES, ObservationError
from services.research.gold_signal import SignalError
from functools import partial
from services.data.gold_execution_tape import new_gold_trade_cursor
from services.research.gold_replay import replay as _replay

replay = partial(_replay, cursor_factory=new_gold_trade_cursor)
from services.research.trial_ledger import TrialLedger, TrialRecord, TrialLedgerError

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = (
    'tools/gold_signal_replay.py', 'services/research/gold_replay.py',
    'services/research/gold_signal.py', 'services/research/ports/gold_tape.py',
    'services/research/gold_trade_windows.py', 'services/research/gold_trade_observation.py',
    'services/data/gold_execution_tape.py', 'services/data/binance_execution_tape.py',
    'services/research/trial_ledger.py', 'requirements-ci-linux-aarch64.lock',
)


def _source_files():
    return {name: (ROOT / name).read_bytes() for name in SOURCE_PATHS}


def _encode(value):
    return json.dumps(value, default=str, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode()


def _digest(value):
    return hashlib.sha256(_encode(value)).hexdigest()


def _write(path, raw):
    with path.open('xb') as output:
        output.write(raw)


def _evaluate(raw):
    try:
        report = replay(load_document(raw))
        report['input_sha256'] = hashlib.sha256(raw).hexdigest()
        return report
    except (ValueError, TypeError, InvalidOperation, ObservationError, SignalError):
        return {'status': 'REJECTED', 'reason': 'INVALID_OR_UNREADABLE_REPLAY_INPUT'}


def archive_replay(raw, destination):
    """Retain retrospective attempts; PASSED means completed computation only.

    Existing directories are never reused. An interrupted run can retain a
    RUNNING ledger without manifest; neither is a completed replay.
    """
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ValueError('INVALID_INPUT_SIZE')
    destination = Path(destination)
    destination.mkdir()  # Exclusive reservation before any replay.
    _write(destination / 'input.json', raw)
    source = _source_files()
    for name, content in source.items():
        path = destination / 'source' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _write(path, content)
    source_hashes = {name: hashlib.sha256(content).hexdigest() for name, content in source.items()}
    input_hash = hashlib.sha256(raw).hexdigest()
    policy = {'mode': 'RETROSPECTIVE_REPLAY', 'success': 'COMPLETE_SIGNAL_COMPUTATION_ONLY',
              'python': platform.python_version(), 'architecture': platform.machine()}
    definition = _digest({'input_sha256': input_hash, 'source_sha256': source_hashes})
    trial_id = _digest({'definition': definition, 'invocation': uuid.uuid4().hex})
    record = dict(trial_id=trial_id, alpha_id=definition,
        family_id=_digest('GOLD_LOCAL_ABSORPTION_PROTOTYPE'), exact_definition_id=definition,
        validation_policy_digest=_digest(policy))
    with TrialLedger(str(destination / 'trials.db')) as ledger:
        def event(status, index):
            ledger.append(TrialRecord(**record, status=status, event_index=index,
                                      at=time.time_ns() // 1000000))
        event('PLANNED', 0)
        event('RUNNING', 1)
        report = _evaluate(raw)
        if source != _source_files():
            report = {'status': 'REJECTED', 'reason': 'SOURCE_CHANGED_DURING_REPLAY'}
        terminal = 'PASSED' if report['status'] == 'RESEARCH_SIGNALS_ONLY' else 'FAILED'
        report_raw = _encode(report)
        _write(destination / 'report.json', report_raw)
        event(terminal, 2)
        ledger.verify()
    manifest = {'schema': 'GOLD_SIGNAL_ARCHIVE_V1', 'trial_id': trial_id,
        'definition_id': definition, 'input_sha256': input_hash, 'source_sha256': source_hashes,
        'report_sha256': hashlib.sha256(report_raw).hexdigest(), 'policy': policy,
        'mode': 'RETROSPECTIVE_REPLAY', 'preregistered': False, 'authoritative': False,
        'terminal': terminal, 'net_pnl': None,
        'limitations': ['NO_PROSPECTIVE_REGISTRATION', 'NO_SOURCE_AUTHENTICATION',
            'SOURCE_FILES_ARE_DIAGNOSTIC_NOT_RELEASE_ATTESTATION',
            'LOCAL_TRIAL_LEDGER_NOT_GLOBAL_TRIAL_REGISTRY', 'NO_PROFITABILITY_EVIDENCE']}
    _write(destination / 'manifest.json', _encode(manifest))
    return report


def verify_archive(directory, *, include_report=False):
    """Recompute under matching current sources; never execute archived code."""
    root = Path(directory).absolute()
    if root.resolve() != root:
        raise ValueError('LINKED_ARCHIVE')
    def read(name):
        path = root / name
        if path.resolve() != path or not path.is_file():
            raise ValueError('INVALID_ARCHIVE_FILE')
        with path.open('rb') as file:
            raw = file.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('ARCHIVE_FILE_TOO_LARGE')
        return raw
    manifest = load_document(read('manifest.json'))
    if not isinstance(manifest, dict):
        raise ValueError('INVALID_ARCHIVE_MANIFEST')
    source = _source_files()
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in source.items()}
    if manifest.get('source_sha256') != hashes:
        raise ValueError('SOURCE_VERSION_MISMATCH')
    if any(read('source/' + name) != value for name, value in source.items()):
        raise ValueError('SOURCE_ARCHIVE_MISMATCH')
    raw = read('input.json')
    input_hash = hashlib.sha256(raw).hexdigest()
    report_raw = read('report.json')
    recomputed = _evaluate(raw)
    if _encode(load_document(report_raw)) != _encode(recomputed):
        raise ValueError('REPLAY_RESULT_MISMATCH')
    policy = {'mode': 'RETROSPECTIVE_REPLAY', 'success': 'COMPLETE_SIGNAL_COMPUTATION_ONLY',
              'python': platform.python_version(), 'architecture': platform.machine()}
    definition = _digest({'input_sha256': input_hash, 'source_sha256': hashes})
    terminal = 'PASSED' if recomputed['status'] == 'RESEARCH_SIGNALS_ONLY' else 'FAILED'
    expected = {'schema': 'GOLD_SIGNAL_ARCHIVE_V1', 'trial_id': manifest.get('trial_id'),
        'definition_id': definition, 'input_sha256': input_hash, 'source_sha256': hashes,
        'report_sha256': hashlib.sha256(report_raw).hexdigest(), 'policy': policy,
        'mode': 'RETROSPECTIVE_REPLAY', 'preregistered': False, 'authoritative': False,
        'terminal': terminal, 'net_pnl': None,
        'limitations': ['NO_PROSPECTIVE_REGISTRATION', 'NO_SOURCE_AUTHENTICATION',
            'SOURCE_FILES_ARE_DIAGNOSTIC_NOT_RELEASE_ATTESTATION',
            'LOCAL_TRIAL_LEDGER_NOT_GLOBAL_TRIAL_REGISTRY', 'NO_PROFITABILITY_EVIDENCE']}
    if _encode(manifest) != _encode(expected):
        raise ValueError('ARCHIVE_MANIFEST_MISMATCH')
    ledger_path = root / 'trials.db'
    if ledger_path.resolve() != ledger_path or not ledger_path.is_file():
        raise ValueError('INVALID_LEDGER_PATH')
    with TrialLedger(str(ledger_path), create=False, read_only=True) as ledger:
        ledger.verify()
        trial = ledger.load(manifest['trial_id'])
        if (trial is None or trial.alpha_id != definition or trial.exact_definition_id != definition
                or trial.family_id != _digest('GOLD_LOCAL_ABSORPTION_PROTOTYPE')
                or trial.validation_policy_digest != _digest(policy)
                or [e.status for e in trial.events] != ['PLANNED', 'RUNNING', terminal]):
            raise ValueError('TRIAL_BINDING_MISMATCH')
    if source != _source_files():
        raise ValueError('SOURCE_CHANGED_DURING_VERIFICATION')
    result = {'status': 'VERIFIED_LOCAL_REPLAY', 'trial_id': manifest['trial_id'],
        'replay_status': recomputed['status'], 'authoritative': False, 'preregistered': False}
    if include_report:
        result['report'] = recomputed
    return result



def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument('--input',type=Path)
    operation.add_argument('--verify-dir',type=Path,help='Read-only recomputation of a saved archive')
    parser.add_argument('--output-dir',type=Path,
                        help='New directory for retrospective input/source/result archive')
    args=parser.parse_args(argv)
    if args.verify_dir is not None:
        if args.output_dir is not None:
            parser.error('--output-dir cannot be combined with --verify-dir')
        try:
            result = verify_archive(args.verify_dir)
        except (OSError, ValueError, TypeError, KeyError, TrialLedgerError):
            print(json.dumps({'status': 'REJECTED', 'reason': 'INVALID_OR_UNVERIFIABLE_ARCHIVE'}))
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0
    try:
        with args.input.open('rb') as source:
            raw=source.read(MAX_BYTES+1)
        if args.output_dir is not None:
            report=archive_replay(raw,args.output_dir)
        else:
            report=replay(load_document(raw))
            report['input_sha256']=hashlib.sha256(raw).hexdigest()
    except (OSError,ValueError,TypeError,InvalidOperation,ObservationError,SignalError,TrialLedgerError):
        print(json.dumps({'status':'REJECTED','reason':'INVALID_OR_UNREADABLE_REPLAY_INPUT'}))
        return 2
    print(json.dumps(report,default=str,sort_keys=True,indent=2))
    return 0 if report['status']=='RESEARCH_SIGNALS_ONLY' else 2


if __name__=='__main__':
    raise SystemExit(main())
