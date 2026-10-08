"""Preregister one retained bar definition and observe public future closes.

Uses the original three-candidate stream and reconstruction. Selected signals
are a projection of durable online decisions, not orders or economic authority.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import time

from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.gold_bar_capture import _write
from tools.gold_bar_definition import define, PreparedBarDefinition
from tools.gold_bar_journal import restore
from tools.gold_bar_observe import observe
from tools.gold_cost_review import _read
from tools.perpetual_replay_research import source_identity



def bound_definition(archive, *, candidate, source_directory, prepared_definition=None):
    if prepared_definition is None:
        return define(archive, candidate=candidate, source_directory=source_directory)
    if type(prepared_definition) is not PreparedBarDefinition:
        raise ValueError('GOLD_BAR_FORWARD_PREPARED_TYPE')
    return prepared_definition.definition(archive, candidate=candidate, source_directory=source_directory)


def now_ms():
    return time.time_ns() // 1_000_000


def project(root, registration):
    _, checked = restore(root / 'journal')
    if checked['source'] != registration['observation_source']:
        raise ValueError('GOLD_BAR_FORWARD_SOURCE_CHANGED')
    definition = registration['definition']
    receipt = decode(_read(root / 'journal', 'warmup-receipt.json'))
    plan = decode(_read(root / 'journal', 'plan.json'))
    if (receipt['received_at_ms'] > registration['registered_at_ms']
            or plan['seconds'] != registration['seconds']):
        raise ValueError('GOLD_BAR_FORWARD_REGISTRATION_CLOCK_OR_DURATION')
    if (registration.get('first_close', False) is not False
            and registration.get('first_close') is not True):
        raise ValueError('GOLD_BAR_FORWARD_FIRST_CLOSE')
    if plan.get('stop_after_close_ms') != (registration['registered_at_ms'] if registration.get('first_close') else None):
        raise ValueError('GOLD_BAR_FORWARD_FIRST_CLOSE_BINDING')
    if (registration.get('network_mode') not in (None, 'PROTECTED_DEMO_NETWORK_V1')
            or plan.get('network_mode') != registration.get('network_mode')):
        raise ValueError('GOLD_BAR_FORWARD_NETWORK_BINDING')
    raw = _read(root / 'journal', 'decisions.jsonl')
    if hashlib.sha256(raw).hexdigest() != checked['hashes']['decisions.jsonl']:
        raise ValueError('GOLD_BAR_FORWARD_JOURNAL_CHANGED')
    selected = []
    for line in raw.splitlines():
        row = decode(line)
        if row['close_ms'] <= registration['registered_at_ms']:
            continue
        if row['received_at_ms'] < row['close_ms']:
            raise ValueError('GOLD_BAR_FORWARD_CLOCK')
        signal, = [s for s in row['decisions'] if s['candidate'] == definition['candidate']]
        selected.append(dict(definition_hash=definition['definition_hash'],
            close_ms=row['close_ms'], received_at_ms=row['received_at_ms'], signal=signal,
            submission_enabled=False))
    return selected, checked


def encode_rows(rows):
    return b''.join(json.dumps(row, sort_keys=True, allow_nan=False).encode() + b'\n' for row in rows)


def live_row(row, registration, prepared_at_ms):
    definition = registration['definition']
    signal, = [s for s in row['decisions'] if s['candidate'] == definition['candidate']]
    if type(prepared_at_ms) is not int or prepared_at_ms < row['received_at_ms']:
        raise ValueError('GOLD_BAR_FORWARD_PREPARATION_CLOCK')
    deadline = signal['decision_ms'] + definition['size_and_timing']['entry_timeout_ms']
    return dict(definition_hash=definition['definition_hash'], close_ms=row['close_ms'],
        received_at_ms=row['received_at_ms'], signal=signal, submission_enabled=False,
        prepared_at_ms=prepared_at_ms, signal_valid_until_ms=deadline,
        entry_window_status='TIMELY' if prepared_at_ms < deadline else 'EXPIRED',
        scope='SIGNAL_ONLY_RECHECK_CURRENT_TIME_AND_ACCOUNT_BEFORE_ANY_EXECUTION')


def verify_live(root, registration, rows):
    raw = _read(root, 'live-signals.jsonl')
    saved = [decode(line) for line in raw.splitlines()]
    if len(saved) != len(rows):
        raise ValueError('GOLD_BAR_FORWARD_LIVE_COUNT')
    for retained, projected in zip(saved, rows):
        original = dict(projected, decisions=[projected['signal']])
        if retained != live_row(original, registration, retained['prepared_at_ms']):
            raise ValueError('GOLD_BAR_FORWARD_LIVE_CHANGED')
    return hashlib.sha256(raw).hexdigest()


def completed(root, registration, rows, checked):
    return dict(schema='GOLD_BAR_BOUND_FORWARD_RESULT_V1',
        status='FORWARD_SIGNALS_RECORDED' if rows else 'NO_NEW_CLOSED_BAR',
        definition_hash=registration['definition']['definition_hash'],
        registration_sha256=hashlib.sha256(_read(root, 'registration.json')).hexdigest(),
        selected_decisions_sha256=hashlib.sha256(encode_rows(rows)).hexdigest(),
        journal_hashes=checked['hashes'], new_closed_bars=len(rows),
        live_signals_sha256=verify_live(root, registration, rows),
        authoritative=False, economic_eligible=False, submission_enabled=False, net_pnl=None,
        execution_adapter_status='BAR_DEMO_ADAPTER_NOT_IMPLEMENTED')


async def run(archive, *, candidate, source_directory, history, output, seconds, first_close=False, prepared_definition=None, protected_network=False):
    if type(seconds) is not int or not 1 <= seconds <= 1800:
        raise ValueError('GOLD_BAR_FORWARD_DURATION')
    if type(protected_network) is not bool:
        raise ValueError('GOLD_BAR_FORWARD_NETWORK_MODE')
    if type(first_close) is not bool:
        raise ValueError('GOLD_BAR_FORWARD_FIRST_CLOSE')
    definition = bound_definition(archive, candidate=candidate, source_directory=source_directory,
        prepared_definition=prepared_definition)
    root = Path(output).absolute()
    if root.parent.resolve() != root.parent:
        raise ValueError('GOLD_BAR_FORWARD_OUTPUT_PATH')
    registration = dict(schema='GOLD_BAR_BOUND_FORWARD_REGISTRATION_V1', definition=definition,
        observation_source=source_identity(), registered_at_ms=now_ms(), seconds=seconds,
        submission_enabled=False, local_registration_only=True,
        **({'first_close': True} if first_close else {}),
        **({'network_mode':'PROTECTED_DEMO_NETWORK_V1'} if protected_network else {}))
    receipt = decode(_read(Path(history), 'receipt.json'))
    if receipt['received_at_ms'] > registration['registered_at_ms']:
        raise ValueError('GOLD_BAR_FORWARD_FUTURE_WARMUP')
    root.mkdir(mode=0o700)
    _write(root / 'registration.json', registration)
    with (root / 'live-signals.jsonl').open('xb') as live:
        def on_closed(row):
            if row['close_ms'] <= registration['registered_at_ms']:
                return
            record = live_row(row, registration, now_ms())
            live.write(encode_rows([record]))
            live.flush()
            os.fsync(live.fileno())

        observed = await observe(history, root / 'journal', seconds=seconds, on_closed=on_closed,
            **({'stop_after_close_ms': registration['registered_at_ms']} if first_close else {}),
            **({'protected_network':True} if protected_network else {}))
    if observed['status'] != 'OBSERVED':
        result = dict(schema='GOLD_BAR_BOUND_FORWARD_RESULT_V1', status='INCOMPLETE',
            reason=observed['reason'], definition_hash=definition['definition_hash'],
            live_signals_sha256=hashlib.sha256(_read(root, 'live-signals.jsonl')).hexdigest(),
            submission_enabled=False, economic_eligible=False)
    else:
        rows, checked = project(root, registration)
        if source_identity() != registration['observation_source']:
            raise ValueError('GOLD_BAR_FORWARD_SOURCE_CHANGED')
        with (root / 'selected-decisions.jsonl').open('xb') as handle:
            handle.write(encode_rows(rows))
        result = completed(root, registration, rows, checked)
    _write(root / 'result.json', result)
    return result


def review(directory, *, archive, source_directory, prepared_definition=None):
    root = Path(directory).absolute()
    raw = _read(root, 'registration.json')
    registration = decode(raw)
    if (registration.get('schema') != 'GOLD_BAR_BOUND_FORWARD_REGISTRATION_V1'
            or registration.get('submission_enabled') is not False
            or type(registration.get('registered_at_ms')) is not int
            or registration['registered_at_ms'] < 0):
        raise ValueError('GOLD_BAR_FORWARD_REGISTRATION')
    definition = bound_definition(archive, candidate=registration['definition']['candidate'],
                        source_directory=source_directory, prepared_definition=prepared_definition)
    if definition != registration['definition']:
        raise ValueError('GOLD_BAR_FORWARD_DEFINITION_CHANGED')
    rows, checked = project(root, registration)
    if _read(root, 'selected-decisions.jsonl') != encode_rows(rows):
        raise ValueError('GOLD_BAR_FORWARD_DECISIONS_CHANGED')
    result = completed(root, registration, rows, checked)
    if decode(_read(root, 'result.json')) != result or _read(root, 'registration.json') != raw:
        raise ValueError('GOLD_BAR_FORWARD_RESULT_CHANGED')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--candidate', required=True, choices=('channel', 'trend', 'reversion'))
    parser.add_argument('--source-directory', required=True, type=Path)
    parser.add_argument('--history', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--seconds', required=True, type=int)
    parser.add_argument('--first-close', action='store_true')
    args = parser.parse_args(argv)
    result = asyncio.run(run(args.archive, candidate=args.candidate,
        source_directory=args.source_directory, history=args.history,
        output=args.output, seconds=args.seconds, first_close=args.first_close))
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'FORWARD_SIGNALS_RECORDED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
