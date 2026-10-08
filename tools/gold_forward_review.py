"""Recompute retained forward decisions for strategy handoff review.

This checks local artifacts, not trusted time, independent admission or current
tradability. It never creates an order or grants execution permission.
"""
import argparse
import hashlib
import json
from pathlib import Path

from services.data.gold_execution_tape import new_gold_trade_cursor
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from services.research.gold_forward import ForwardObservation
from tools.gold_cost_review import _read
from tools.gold_execution_identity import retain_strategy_definition, PreparedStrategy
from tools.gold_forward_observe import encoded
from tools.gold_public_capture import PublicCapture
from tools.gold_signal_replay import ROOT, _source_files


def require(condition, reason):
    if not condition:
        raise ValueError('GOLD_FORWARD_REVIEW_'+reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def review(directory, *, strategy_trial, prepared_strategy=None):
    root = Path(directory).absolute()
    names = ('registration.json', 'events.jsonl', 'decisions.jsonl', 'result.json')
    raw = {name: _read(root, name) for name in names}
    registration, result = (decode(raw[name]) for name in ('registration.json', 'result.json'))
    require(type(registration) is dict and type(result) is dict, 'DOCUMENT')
    require(registration.get('schema') == 'GOLD_LOCAL_FORWARD_REGISTRATION_V1'
            and result.get('schema') == 'GOLD_LOCAL_FORWARD_RESULT_V1'
            and registration.get('authoritative') is False
            and registration.get('local_registration_only') is True
            and result.get('authoritative') is False
            and result.get('local_registration_only') is True
            and result.get('submission_enabled') is False
            and result.get('net_pnl') is None, 'SCOPE')
    for name, key in (('registration.json', 'registration_sha256'),
                      ('events.jsonl', 'events_sha256'), ('decisions.jsonl', 'decisions_sha256')):
        require(result.get(key) == sha(raw[name]), 'FILE_BINDING')
    # Require the retained definition before calling its original verifier, so
    # this read-only entrypoint cannot create a missing derivative in the trial.
    trial = Path(strategy_trial).absolute()
    retained = _read(trial, 'strategy-definition.json')
    if prepared_strategy is not None:
        require(type(prepared_strategy) is PreparedStrategy, 'PREPARED_STRATEGY_TYPE')
        definition = prepared_strategy.definition(trial)
    else:
        definition = retain_strategy_definition(trial)
    require(encoded(decode(retained)) == encoded(definition)
            and encoded(registration.get('strategy_definition')) == encoded(definition)
            and result.get('strategy_definition_sha256') == definition['result_hash'], 'STRATEGY_BINDING')
    sources = _source_files()
    for name in ('tools/gold_forward_observe.py', 'tools/gold_public_capture.py',
                 'services/research/gold_forward.py', 'tools/gold_execution_identity.py',
                 'tools/gold_ai_origin.py', 'services/research/gold_execution_identity.py'):
        sources[name] = (ROOT/name).read_bytes()
    require('network_mode' not in registration or registration['network_mode'] == 'PROTECTED_DEMO_NETWORK_V1',
            'NETWORK_MODE')
    if registration.get('network_mode') == 'PROTECTED_DEMO_NETWORK_V1':
        for name in ('live/exchange/demo_network.py', 'live/exchange/binance_futures.py'):
            sources[name] = (ROOT/name).read_bytes()
    require(registration.get('source_sha256') == {name: sha(body) for name, body in sources.items()},
            'SOURCE_BINDING')
    plan = registration['plan']
    registered = registration['registered_at_ms']
    require(type(registered) is int
            and definition['research_identity']['available_at_ms'] < registered
            and encoded(plan['rule']) == encoded(definition['entry_rule'])
            and all(plan['zone'][k] == definition['historical_zone'][k] for k in ('lower', 'upper')),
            'PLAN_BINDING')
    engine = ForwardObservation(plan, registered_at_ms=registered, cursor_factory=new_gold_trade_cursor)
    require(registration.get('plan_id') == result.get('plan_id') == engine.plan_id, 'PLAN_ID')
    require(result.get('status') in ('COMPLETE_OBSERVATION', 'INPUT_INCOMPLETE'), 'INCOMPLETE_STREAM')
    events = [decode(line) for line in raw['events.jsonl'].splitlines()]
    rows = [decode(line) for line in raw['decisions.jsonl'].splitlines()]
    require(type(result.get('recorded_decisions')) is int
            and result['recorded_decisions'] == len(rows) and 1 <= len(rows) <= 60, 'DECISION_COUNT')
    capture = PublicCapture(started_at_ms=registered)
    for event in events:
        require(type(event) is dict and set(event) == {'received_at_ms', 'raw'}, 'EVENT')
        capture.ingest(event['raw'], received_at_ms=event['received_at_ms'])
    signals = []
    previous_available = registered
    for row in rows:
        require(type(row) is dict, 'DECISION')
        observed, available = row.get('observed_at_ms'), row.get('decision_available_at_ms')
        require(type(observed) is int and type(available) is int
                and previous_available <= observed <= available, 'DECISION_TIME')
        packets = [packet for packet in capture.packets if packet['received_at_ms'] <= observed]
        actual = engine.advance(packets, now_ms=observed)
        require(actual is not None, 'EARLY_DECISION')
        actual['decision_available_at_ms'] = available
        if available > actual['scheduled_at_ms']+plan['max_lateness_ms']:
            actual['diagnostic_decision'] = actual['decision']
            actual['decision'] = None
            actual['reason'] = 'COMPUTATION_DEADLINE_EXCEEDED'
            engine.status = 'INPUT_INCOMPLETE'
        require(encoded(actual) == encoded(row), 'DECISION_REPLAY')
        if actual['decision'] is not None and actual['decision'].get('intent') == 'LONG_RESEARCH_SIGNAL':
            signals.append({'decision': actual['decision'], 'available_at_ms': available,
                            'exit_and_size_plan': definition['exit_and_size_plan']})
        previous_available = available
    require(engine.status == result['status'], 'TERMINAL_REPLAY')
    require(capture.last_received <= rows[-1]['observed_at_ms'], 'EVENT_AFTER_TERMINAL')
    require(all(_read(root, name) == body for name, body in raw.items())
            and _read(trial, 'strategy-definition.json') == retained
            and all((ROOT/name).read_bytes() == body for name, body in sources.items()), 'INPUT_CHANGED')
    if prepared_strategy is not None:
        require(prepared_strategy.definition(trial) == definition, 'PREPARED_STRATEGY_CHANGED')
    return {'schema': 'GOLD_FORWARD_STRATEGY_REVIEW_V1', 'status': 'RECOMPUTED_LOCAL_OBSERVATION',
            'observation_status': engine.status, 'files': {name: sha(body) for name, body in raw.items()},
            'strategy_definition_sha256': definition['result_hash'],
            'research_identity': definition['research_identity'],
            'signals': signals if engine.status == 'COMPLETE_OBSERVATION' else [],
            'authoritative': False, 'economic_eligible': False, 'submission_enabled': False,
            'scope': 'RETAINED_DECISION_REVIEW_ONLY',
            'limitations': ['LOCAL_CLOCK_NOT_ATTESTED', 'NOT_INDEPENDENT_ADMISSION',
                            'CURRENT_MARKET_AND_ACCOUNT_NOT_CHECKED']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation-dir', type=Path, required=True)
    parser.add_argument('--strategy-trial', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = review(args.observation_dir, strategy_trial=args.strategy_trial)
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({'status': 'REJECTED', 'submission_enabled': False}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
