"""Reproduce retained research account prefixes without trading or promotion.

Uses the same simulation engine, so this is engineering reproduction, not an
independent economic validation. Prefix returns are never added together.
"""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
import time

from services.execution.backtest_authority import exact_identity_hash
from services.research.gold_bar_diagnostics import account_diagnostics
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.gold_account_chain import _history, _canonical
from tools.gold_session_io import read as _read
from tools.gold_session_replay import replay
from tools.perpetual_replay_research import source_identity


def review_chain(directory):
    root = Path(directory).absolute()
    source = source_identity()
    directories, head = _history(root)
    if not directories:
        raise ValueError('ACCOUNT_REVIEW_EMPTY')
    rows, bindings = [], []
    previous = None
    previous_hash = None
    total = 0
    for folder in directories:
        transition_raw = _read(folder, 'transition.json')
        transition = decode(transition_raw)
        raw = {name: _read(folder, name) for name in transition['files']}
        total += sum(len(body) for body in raw.values())
        if total > 256 * 1024 * 1024:
            raise ValueError('ACCOUNT_REVIEW_BYTE_BUDGET')
        if any(hashlib.sha256(body).hexdigest() != transition['files'][name]
               for name, body in raw.items()):
            raise ValueError('ACCOUNT_REVIEW_INPUT_CHANGED')
        config = decode(raw['configuration.json'])
        inputs = decode(raw['inputs.json'])
        expected = decode(raw['report.json'])
        if (transition['sequence'] != len(rows) + 1
                or transition['previous_transition_hash'] != previous_hash
                or transition['configuration_hash'] != exact_identity_hash(config, name='gold_account_configuration')
                or transition['as_of_ms'] != inputs['as_of_ms']
                or transition['transition_hash'] != exact_identity_hash(
                    {k: v for k, v in transition.items() if k != 'transition_hash'}, name='gold_account_transition')):
            raise ValueError('ACCOUNT_REVIEW_TRANSITION_CHANGED')
        if previous:
            before_config, before_inputs, before_report, warmup = previous
            if (config != before_config or inputs['as_of_ms'] <= before_inputs['as_of_ms']
                    or warmup != (raw['warmup.json'], raw['warmup-receipt.json'])
                    or len(inputs['samples']) <= len(before_inputs['samples'])
                    or any(inputs[k] != before_inputs[k] for k in ('bootstrap', 'quote_source', 'bar_source'))
                    or any(inputs[k][:len(before_inputs[k])] != before_inputs[k]
                           for k in ('samples', 'bar_messages'))):
                raise ValueError('ACCOUNT_REVIEW_CHAIN_CONTINUITY')
            prior_capacity = before_inputs.get('capacity_observations', [])
            if inputs.get('capacity_observations', [])[:len(prior_capacity)] != prior_capacity:
                raise ValueError('ACCOUNT_REVIEW_CAPACITY_CONTINUITY')
            for key in ('ledger_events', 'equity_history', 'order_events', 'decision_events', 'risk_events'):
                before = before_report['simulation'][key]
                if expected['simulation'][key][:len(before)] != before:
                    raise ValueError('ACCOUNT_REVIEW_LEDGER_CONTINUITY')
        arguments = dict(config)
        arguments.update(exchange_after=decode(raw['rule.json']),
                         observed_funding_records=decode(raw['funding.json']))
        for name in ('initial_collateral', 'fee_rate', 'max_notional'):
            arguments[name] = Decimal(arguments[name])
        arguments['quantities'] = {k: Decimal(v) for k, v in arguments['quantities'].items()}
        with tempfile.TemporaryDirectory(prefix='quantos-account-review-') as temp:
            staging = Path(temp)
            for name in ('inputs.json', 'sources.json', 'warmup.json', 'warmup-receipt.json'):
                (staging/name).write_bytes(raw[name])
            (staging/'summary.json').write_bytes(raw['prefix-summary.json'])
            observed = _canonical(replay(staging, **arguments))
        if (observed != expected or observed['result_hash'] != transition['result_hash']
                or observed['submission_enabled'] is not False
                or observed['economic_eligible'] is not False):
            raise ValueError('ACCOUNT_REVIEW_REPRODUCTION_MISMATCH')
        simulation = observed['simulation']
        rows.append(dict(sequence=transition['sequence'], as_of_ms=transition['as_of_ms'],
            transition_hash=transition['transition_hash'], result_hash=observed['result_hash'],
            account=account_diagnostics(simulation), order_count=len(simulation['orders']),
            filled_order_count=sum(o['filled_at'] is not None for o in simulation['orders']),
            open_position_count=len(simulation['ending_state']['positions']),
            loss_status=observed['loss_control']['status']))
        bindings.append((folder, transition_raw, transition['files']))
        previous = (config, inputs, expected, (raw['warmup.json'], raw['warmup-receipt.json']))
        previous_hash = transition['transition_hash']
    if previous_hash != head:
        raise ValueError('ACCOUNT_REVIEW_TRANSITION_CHANGED')
    for folder, transition_raw, files in bindings:
        if (_read(folder, 'transition.json') != transition_raw or any(
                hashlib.sha256(_read(folder, name)).hexdigest() != digest
                for name, digest in files.items())):
            raise ValueError('ACCOUNT_REVIEW_INPUT_CHANGED')
    if source_identity() != source:
        raise ValueError('ACCOUNT_REVIEW_SOURCE_CHANGED')
    result = dict(schema='GOLD_ACCOUNT_CHAIN_REPRODUCTION_V1',
        status='SAME_ENGINE_REPRODUCED', reviewer_source=source,
        reviewed_at_ms=time.time_ns() // 1000000, reviewed_head=head,
        transitions=rows, latest_account=rows[-1]['account'],
        aggregation='LATEST_PREFIX_ONLY_NOT_SUM_OF_PREFIX_RETURNS',
        independent_review_status='INDEPENDENT_AUTHORITY_UNCONFIGURED',
        economic_eligible=False, authoritative=False, submission_enabled=False)
    result['result_hash'] = exact_identity_hash(result, name='gold_account_chain_review')
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chain', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = review_chain(args.chain)
    with args.output.open('x') as handle:
        json.dump(result, handle, sort_keys=True, allow_nan=False)
        handle.write('\n')
    print(json.dumps(dict(status=result['status'], transitions=len(result['transitions']),
                          result_hash=result['result_hash'], submission_enabled=False)), flush=True)


if __name__ == '__main__':
    main()
