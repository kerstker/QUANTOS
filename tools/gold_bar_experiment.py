"""Append-once three-candidate technical trials and read-only recomputation."""
import base64
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time
import uuid
import tempfile
import fcntl
import os
from contextlib import contextmanager

from quantos.contracts.gold_trial_identity import gold_trial_definition_id
from services.research.gold_bar_candidates import CANDIDATES
from services.research.trial_ledger import TrialLedger, TrialRecord
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools.gold_bar_cost_replay import compare_public
from tools.perpetual_replay_research import source_identity


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _hash(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _write(path, value):
    raw = _bytes(value)
    with path.open('xb') as handle:
        handle.write(raw)
    return hashlib.sha256(raw).hexdigest()


def _encode(args):
    result = dict(args)
    if 'raw_bars' in args:
        result['raw_bars'] = base64.b64encode(args['raw_bars']).decode()
    for field in ('quantity', 'initial_collateral', 'fee_rate'):
        result[field] = str(args[field])
    return result


def _decode(value):
    result = dict(value)
    if 'raw_bars' in value:
        result['raw_bars'] = base64.b64decode(value['raw_bars'], validate=True)
    for field in ('quantity', 'initial_collateral', 'fee_rate'):
        result[field] = Decimal(value[field])
    return result


JOURNAL_FILES = ('summary.json', 'plan.json', 'warmup.json', 'warmup-receipt.json',
                 'messages.jsonl', 'decisions.jsonl')


def trial_candidates(arguments):
    if 'portfolio_plan' not in arguments:
        return CANDIDATES
    plan = arguments['portfolio_plan']
    from quantos.contracts.gold_native_source import INPUT_SCHEMAS
    if (('journal_files' not in arguments and arguments.get('input_schema') not in INPUT_SCHEMAS) or type(plan) is not dict
            or set(plan) != {'quantities', 'max_notional'}
            or type(plan['quantities']) is not dict or set(plan['quantities']) != set(CANDIDATES)
            or any(type(v) is not str for v in (*plan['quantities'].values(), plan['max_notional']))):
        raise ValueError('INVALID_GOLD_PORTFOLIO_EXPERIMENT_PLAN')
    numbers = [Decimal(v) for v in (*plan['quantities'].values(), plan['max_notional'])]
    if any(not v.is_finite() or v <= 0 for v in numbers):
        raise ValueError('INVALID_GOLD_PORTFOLIO_EXPERIMENT_BUDGET')
    return (*CANDIDATES, 'net_portfolio')


def _execute(arguments, *, history=None, source_context=None):
    from quantos.contracts.gold_native_source import INPUT_SCHEMAS
    if arguments.get('input_schema') in INPUT_SCHEMAS:
        trial_candidates(arguments)
        if source_context is None or history is None:
            raise ValueError('GOLD_NATIVE_SOURCE_CONTEXT_REQUIRED')
        source, preparation = source_context
        return source.execute(arguments, preparation=preparation, history=history)
    if 'input_schema' in arguments or 'continuation_segments' in arguments:
        from tools.gold_segmented_replay import compare
        trial_candidates(arguments)
        return compare(arguments, **({} if history is None else {'history': history}))
    if 'journal_files' not in arguments:
        return compare_public(**arguments, **({} if history is None else {'history': history}))
    from tools.gold_bar_observed_replay import compare_observed
    trial_candidates(arguments)
    args = dict(arguments)
    portfolio_plan = args.pop('portfolio_plan', None)
    files = args.pop('journal_files')
    if type(files) is not dict or set(files) != set(JOURNAL_FILES):
        raise ValueError('BAR_EXPERIMENT_JOURNAL_FIELDS')
    with tempfile.TemporaryDirectory(prefix='quantos-bar-recompute-') as directory:
        root = Path(directory)
        for name, body in files.items():
            with (root / name).open('xb') as handle:
                handle.write(base64.b64decode(body, validate=True))
        context = {} if history is None else {'history': history}
        result = compare_observed(journal=root, **args, **context)
        if portfolio_plan is not None:
            from tools.gold_bar_observed_replay import compare_observed_portfolio
            from services.execution.backtest_authority import exact_identity_hash
            portfolio_args = {k:v for k,v in args.items() if k != 'quantity'}
            portfolio = compare_observed_portfolio(journal=root, **portfolio_args, **context,
                quantities={n:Decimal(v) for n,v in portfolio_plan['quantities'].items()},
                max_notional=Decimal(portfolio_plan['max_notional']))
            result['portfolio'] = portfolio
            from quantos.streaming_identity import exact_stream_identity_hash
            hash_result = exact_identity_hash if history is None else exact_stream_identity_hash
            result['result_hash'] = hash_result(
                {k:v for k,v in result.items() if k != 'result_hash'}, name='gold_bar_public_comparison')
        return result


@contextmanager
def _writer_lock(destination):
    dest = Path(destination).absolute()
    if dest.resolve() != dest:
        raise ValueError('BAR_EXPERIMENT_OUTPUT_PATH')
    fd = os.open(dest.parent / ('.' + dest.name + '.writer.lock'),
                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def archive(arguments, destination):
    with _writer_lock(destination):
        return _archive(arguments, destination)


def registered_archive(arguments, destination, *, registry, clock_ms=None):
    """Explicit registered path; standalone archive and its identities are unchanged."""
    from tools.registered_research_run import run_gold
    return run_gold(arguments, destination=destination, registry=registry, clock_ms=clock_ms)


def _archive(arguments, destination):
    source = source_identity()
    if arguments['release_identity'] != source:
        raise ValueError('BAR_EXPERIMENT_SOURCE_MISMATCH')
    document = _encode(arguments)
    # Own all nested inputs before execution.
    arguments = _decode(load_json_without_duplicate_keys(_bytes(document)))
    dest = Path(destination).absolute()
    if dest.resolve() != dest:
        raise ValueError('BAR_EXPERIMENT_OUTPUT_PATH')
    dest.mkdir(mode=0o700)
    input_hash = _write(dest / 'input.json', document)
    policy = _hash({'mode': 'TECHNICAL_COMPUTATION_ONLY', 'automatic_selection': False})
    identities = []
    for candidate in trial_candidates(arguments):
        definition = gold_trial_definition_id(input_sha256=input_hash, candidate=candidate, source=source)
        identities.append(dict(trial_id=_hash(dict(definition=definition, invocation=uuid.uuid4().hex)),
            alpha_id=definition, exact_definition_id=definition,
            family_id=_hash('GOLD_BAR_' + candidate.upper()), validation_policy_digest=policy))
    manifest = dict(schema='GOLD_BAR_EXPERIMENT_V1', source=source,
        trial_ids=[i['trial_id'] for i in identities], input_sha256=input_hash,
        submission_enabled=False, economic_eligible=False)
    with TrialLedger(str(dest / 'trials.db')) as ledger:
        for index, state in enumerate(('PLANNED', 'RUNNING')):
            for identity in identities:
                ledger.append(TrialRecord(**identity, event_index=index, status=state,
                                           at=time.time_ns() // 1_000_000))
        _write(dest / 'running.json', dict(schema='GOLD_BAR_RUNNING_ARCHIVE_V1',
            manifest=manifest, identities=identities))
        try:
            result = _execute(arguments)
            if source_identity() != source:
                raise ValueError('BAR_EXPERIMENT_SOURCE_CHANGED')
            manifest['report_sha256'] = _write(dest / 'report.json', result)
        except BaseException as exc:
            manifest.update(terminal='FAILED', error_type=type(exc).__name__)
            for identity in identities:
                ledger.append(TrialRecord(**identity, event_index=2, status='FAILED',
                                           at=time.time_ns() // 1_000_000))
            _write(dest / 'manifest.json', manifest)
            raise
        for identity in identities:
            ledger.append(TrialRecord(**identity, event_index=2, status='PASSED',
                                       at=time.time_ns() // 1_000_000))
        manifest['terminal'] = 'PASSED'
        _write(dest / 'manifest.json', manifest)
    return dict(status='TECHNICAL_COMPUTATION_COMPLETE', trial_ids=manifest['trial_ids'],
                result_hash=result['result_hash'], submission_enabled=False, economic_eligible=False)


def resume_archive(arguments, destination):
    """Reconstruct an interrupted computation under its original trial identity.

    Failed terminal experiments stay failed. A live writer cannot be recovered
    concurrently. This replays retained inputs; it never resumes real orders.
    """
    from tools.gold_cost_review import _read
    with _writer_lock(destination):
        dest = Path(destination).absolute()
        source = source_identity()
        if (arguments['release_identity'] != source
                or _read(dest, 'input.json') != _bytes(_encode(arguments))):
            raise ValueError('BAR_ARCHIVE_RECOVERY_INPUT_CHANGED')
        if (dest / 'manifest.json').exists():
            manifest = load_json_without_duplicate_keys(_read(dest, 'manifest.json'))
            if manifest.get('terminal') != 'PASSED':
                raise ValueError('BAR_ARCHIVE_TERMINAL_FAILURE')
            checked = verify(dest)
            return dict(status='TECHNICAL_COMPUTATION_COMPLETE', trial_ids=manifest['trial_ids'],
                result_hash=checked['result_hash'], submission_enabled=False, economic_eligible=False)
        registration = load_json_without_duplicate_keys(_read(dest, 'running.json'))
        if (type(registration) is not dict
                or set(registration) != {'schema', 'manifest', 'identities'}
                or registration['schema'] != 'GOLD_BAR_RUNNING_ARCHIVE_V1'):
            raise ValueError('BAR_ARCHIVE_RECOVERY_REGISTRATION')
        manifest, identities = registration['manifest'], registration['identities']
        if (manifest.get('schema') != 'GOLD_BAR_EXPERIMENT_V1' or manifest.get('source') != source
                or manifest.get('submission_enabled') is not False
                or manifest.get('economic_eligible') is not False
                or manifest.get('input_sha256') != hashlib.sha256(_read(dest, 'input.json')).hexdigest()
                or len(identities) != len(trial_candidates(arguments))
                or len(set(manifest['trial_ids'])) != len(trial_candidates(arguments))
                or [i['trial_id'] for i in identities] != manifest['trial_ids']):
            raise ValueError('BAR_ARCHIVE_RECOVERY_BINDING')
        with TrialLedger(str(dest / 'trials.db'), create=False) as ledger:
            ledger.verify()
            for candidate, identity in zip(trial_candidates(arguments), identities):
                definition = gold_trial_definition_id(input_sha256=manifest['input_sha256'],
                                        candidate=candidate, source=source)
                expected = dict(trial_id=identity['trial_id'], alpha_id=definition,
                    exact_definition_id=definition, family_id=_hash('GOLD_BAR_' + candidate.upper()),
                    validation_policy_digest=_hash({'mode': 'TECHNICAL_COMPUTATION_ONLY',
                                                    'automatic_selection': False}))
                record = ledger.load(identity['trial_id'])
                if (identity != expected or any(getattr(record, k) != v for k, v in expected.items())
                        or record.status not in ('RUNNING', 'PASSED')):
                    raise ValueError('BAR_ARCHIVE_RECOVERY_TRIAL_CHANGED')
            result = _execute(_decode(load_json_without_duplicate_keys(_bytes(_encode(arguments)))))
            if source_identity() != source:
                raise ValueError('BAR_EXPERIMENT_SOURCE_CHANGED')
            raw = _bytes(result)
            if (dest / 'report.json').exists():
                if _read(dest, 'report.json') != raw:
                    raise ValueError('BAR_ARCHIVE_RECOVERY_REPORT_CHANGED')
                manifest['report_sha256'] = hashlib.sha256(raw).hexdigest()
            else:
                manifest['report_sha256'] = _write(dest / 'report.json', result)
            for identity in identities:
                if ledger.load(identity['trial_id']).status == 'RUNNING':
                    ledger.append(TrialRecord(**identity, event_index=2, status='PASSED',
                                               at=time.time_ns() // 1_000_000))
            manifest['terminal'] = 'PASSED'
            _write(dest / 'manifest.json', manifest)
        return dict(status='TECHNICAL_COMPUTATION_COMPLETE', trial_ids=manifest['trial_ids'],
            result_hash=result['result_hash'], submission_enabled=False, economic_eligible=False)


def bound_archive(destination):
    """Read original bindings without replaying or claiming source reproduction."""
    dest = Path(destination).absolute()
    if dest.resolve() != dest:
        raise ValueError('BAR_EXPERIMENT_OUTPUT_PATH')
    from tools.gold_cost_review import _read
    manifest = load_json_without_duplicate_keys(_read(dest, 'manifest.json'))
    if (manifest.get('schema') not in ('GOLD_BAR_EXPERIMENT_V1', 'QUANTOS_REGISTERED_GOLD_BAR_EXPERIMENT_V2') or manifest['terminal'] != 'PASSED'
            or manifest.get('submission_enabled') is not False
            or manifest.get('economic_eligible') is not False):
        raise ValueError('BAR_EXPERIMENT_NOT_RECOMPUTABLE')
    bodies = {name: _read(dest, name + '.json') for name in ('input', 'report')}
    if any(hashlib.sha256(raw).hexdigest() != manifest[name + '_sha256']
           for name, raw in bodies.items()):
        raise ValueError('BAR_EXPERIMENT_BYTES_CHANGED')
    args = _decode(load_json_without_duplicate_keys(bodies['input']))
    from quantos.contracts.registered_research_registration import gold_archive_policy
    policy = gold_archive_policy(manifest, load_json_without_duplicate_keys(bodies['input']))
    candidates = trial_candidates(args)
    with TrialLedger(str(dest / 'trials.db'), create=False, read_only=True) as ledger:
        ledger.verify()
        if len(manifest['trial_ids']) != len(candidates) or len(set(manifest['trial_ids'])) != len(candidates) or any(
                ledger.load(t).status != 'PASSED' for t in manifest['trial_ids']):
            raise ValueError('BAR_EXPERIMENT_TRIALS_INCOMPLETE')
        for candidate, trial_id in zip(candidates, manifest['trial_ids']):
            record = ledger.load(trial_id)
            definition = gold_trial_definition_id(input_sha256=manifest['input_sha256'],
                                    candidate=candidate, source=manifest['source'])
            if (record.alpha_id != definition or record.exact_definition_id != definition
                    or record.family_id != _hash('GOLD_BAR_' + candidate.upper())
                    or record.validation_policy_digest != policy):
                raise ValueError('BAR_EXPERIMENT_TRIAL_BINDING')
    args = _decode(load_json_without_duplicate_keys(bodies['input']))
    if args['release_identity'] != manifest['source']:
        raise ValueError('BAR_EXPERIMENT_SOURCE_BINDING')
    return manifest, bodies, args


def verify(destination):
    manifest, bodies, args = bound_archive(destination)
    if manifest['source'] != source_identity():
        raise ValueError('BAR_EXPERIMENT_NOT_RECOMPUTABLE')
    recomputed = _execute(args)
    if _bytes(recomputed) != bodies['report'] or source_identity() != manifest['source']:
        raise ValueError('BAR_EXPERIMENT_RECOMPUTATION_MISMATCH')
    return dict(status='RECOMPUTED_NOT_INDEPENDENTLY_APPROVED',
                result_hash=recomputed['result_hash'], submission_enabled=False,
                economic_eligible=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument('--verify-dir', type=Path)
    operation.add_argument('--output-dir', type=Path)
    parser.add_argument('--history-dir', type=Path)
    parser.add_argument('--quotes', type=Path)
    parser.add_argument('--quantity', type=Decimal, default=Decimal('.02'))
    parser.add_argument('--collateral', type=Decimal, default=Decimal('1000'))
    parser.add_argument('--assumed-fee-rate', type=Decimal, default=Decimal('.0005'))
    args = parser.parse_args(argv)
    if args.verify_dir:
        result = verify(args.verify_dir)
    else:
        if args.history_dir is None or args.quotes is None:
            parser.error('--history-dir and --quotes required when archiving')
        from tools.gold_cost_review import _read
        arguments = dict(raw_bars=_read(args.history_dir, 'response.json'),
            bar_receipt=load_json_without_duplicate_keys(_read(args.history_dir, 'receipt.json')),
            capture=load_json_without_duplicate_keys(_read(args.quotes.parent, args.quotes.name)),
            release_identity=source_identity(), quantity=args.quantity,
            initial_collateral=args.collateral, fee_rate=args.assumed_fee_rate)
        result = archive(arguments, args.output_dir)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
