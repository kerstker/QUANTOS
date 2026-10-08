"""Declared, retrospective trade/signal links; no inferred ownership or PnL.

The caller must obtain the report by verified archive replay or current-source
recomputation of bound input, and distinguish those evidence modes in its output.
This pure layer checks bindings, not source or annotator authenticity.
"""
import hashlib
import json
import re
from collections import Counter

from services.research.gold_trade_observation import assess, IDENTIFIER, MAX_FILLS


def report_digest(report):
    return hashlib.sha256(json.dumps(report, default=str, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _time(value):
    return type(value) is int and 0 <= value <= 2**63-1


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def associate(fills, replay, annotations, *, as_of_ms):
    facts = assess(fills)
    if not _time(as_of_ms) or facts['exported_at_ms'] > as_of_ms:
        raise ValueError('INVALID_AS_OF')
    if not (isinstance(replay, dict) and replay.get('schema') == 'GOLD_SIGNAL_REPLAY_V1'
            and replay.get('status') in ('RESEARCH_SIGNALS_ONLY', 'INPUT_INCOMPLETE')
            and replay.get('authoritative') is False and replay.get('net_pnl') is None
            and _hash(replay.get('rule_id')) and isinstance(replay.get('records'), list)
            and len(replay['records']) <= 1000):
        raise ValueError('INVALID_REPLAY_REPORT')
    signals = {}
    for row in replay['records']:
        d = row.get('decision') if isinstance(row, dict) else None
        if not (isinstance(d, dict) and d.get('schema') == 'GOLD_RESEARCH_SIGNAL_V1'
                and d.get('symbol') == 'XAUUSDT' and d.get('rule_id') == replay['rule_id']
                and _hash(d.get('decision_id')) and _time(d.get('decision_ms'))
                and d['decision_ms'] <= as_of_ms and d.get('authoritative') is False):
            raise ValueError('INVALID_DECISION')
        if d.get('intent') == 'LONG_RESEARCH_SIGNAL':
            if (d.get('state') != 'SIGNALLED' or d.get('entry_price') is not None
                    or d.get('earliest_execution_after_ms') != d['decision_ms']
                    or d['decision_id'] in signals):
                raise ValueError('INVALID_ENTRY_SIGNAL')
            signals[d['decision_id']] = d
    if not (isinstance(annotations, dict) and set(annotations) == {
            'schema', 'fills_facts_sha256', 'replay_sha256', 'links'}
            and annotations['schema'] == 'GOLD_TRADE_LINKS_V1'
            and annotations['fills_facts_sha256'] == facts['facts_sha256']
            and annotations['replay_sha256'] == report_digest(replay)
            and isinstance(annotations['links'], list) and len(annotations['links']) <= MAX_FILLS):
        raise ValueError('ANNOTATION_BINDING_MISMATCH')
    links = {}
    unique = {row['trade_id']: row for row in fills['fills']}  # assess checked conflicts.
    for link in annotations['links']:
        if not (isinstance(link, dict) and set(link) == {
                'trade_id', 'decision_id', 'role', 'annotated_at_ms'}
                and isinstance(link['trade_id'], str) and IDENTIFIER.fullmatch(link['trade_id'])
                and _hash(link['decision_id']) and link['role'] in ('ENTRY', 'EXIT')
                and _time(link['annotated_at_ms']) and link['annotated_at_ms'] <= as_of_ms):
            raise ValueError('INVALID_ANNOTATION')
        trade, signal = unique.get(link['trade_id']), signals.get(link['decision_id'])
        if ((trade is not None and link['annotated_at_ms'] < trade['time_ms'])
                or (signal is not None and link['annotated_at_ms'] < signal['decision_ms'])):
            raise ValueError('ANNOTATION_PRECEDES_EVIDENCE')
        previous = links.get(link['trade_id'])
        if previous is not None and previous != link:
            raise ValueError('CONFLICTING_ANNOTATION')
        links[link['trade_id']] = link
    trades, linked, entry_linked = [], set(), set()
    for trade in sorted(unique.values(), key=lambda row: (row['time_ms'], int(row['trade_id']))):
        link = links.get(trade['trade_id'])
        signal = signals.get(link['decision_id']) if link else None
        status = 'UNLINKED'
        if link is not None:
            if signal is None:
                status = 'UNKNOWN_ENTRY_SIGNAL'
            elif trade['time_ms'] < signal['decision_ms']:
                status = 'TRADE_PRECEDES_SIGNAL'
            elif trade['time_ms'] == signal['decision_ms']:
                status = 'SAME_MILLISECOND_UNRESOLVED'
            elif (trade['position_side'] == 'SHORT'
                    or trade['side'] != ('BUY' if link['role'] == 'ENTRY' else 'SELL')):
                status = 'DIRECTION_CONFLICT'
            else:
                status = 'DECLARED_LINK'
                linked.add(signal['decision_id'])
                if link['role'] == 'ENTRY':
                    entry_linked.add(signal['decision_id'])
        trades.append({'trade_id': trade['trade_id'], 'time_ms': trade['time_ms'],
            'status': status, 'decision_id': link['decision_id'] if link else None,
            'role': link['role'] if link else None,
            'annotated_at_ms': link['annotated_at_ms'] if link else None,
            'rule_id': signal['rule_id'] if signal else None,
            'trade_minus_signal_ms': trade['time_ms'] - signal['decision_ms'] if signal else None,
            # No starting inventory or position reconciliation is available.
            'position_effect': 'UNKNOWN'})
    orphan_ids = sorted(set(links) - set(unique), key=int)
    review_counts = {
        'fills_by_status': dict(sorted(Counter(t['status'] for t in trades).items())),
        'declared_entry_fills': sum(t['status'] == 'DECLARED_LINK' and t['role'] == 'ENTRY' for t in trades),
        'declared_exit_fills': sum(t['status'] == 'DECLARED_LINK' and t['role'] == 'EXIT' for t in trades),
        'entry_signals': len(signals), 'signals_with_declared_entry': len(entry_linked),
        'orphan_annotations': len(orphan_ids)}
    return {'schema': 'GOLD_TRADE_LINK_REPORT_V1', 'status': 'DECLARED_ASSOCIATIONS_ONLY',
        'symbol': 'XAUUSDT', 'as_of_ms': as_of_ms, 'fills_facts_sha256': facts['facts_sha256'],
        'replay_sha256': report_digest(replay), 'annotations_sha256': report_digest(annotations),
        'unique_fills': facts['unique_fills'], 'trades': trades,
        'orphan_trade_ids': orphan_ids, 'review_counts': review_counts,
        'signals_without_declared_trade': sorted(set(signals) - linked),
        'signals_without_declared_entry': sorted(set(signals) - entry_linked),
        'strategy_pnl': None, 'actor_authenticated': False, 'causal_attribution': False,
        'authoritative': False, 'limitations': ['RETROSPECTIVE_DECLARATIONS_ONLY',
            'NO_POSITION_OR_EXIT_RECONSTRUCTION', 'NO_PROOF_OF_SIGNAL_DELIVERY',
            'NO_ACCOUNT_OR_ANNOTATOR_AUTHENTICATION', 'NO_CLOCK_SYNCHRONIZATION_PROOF',
            'NO_EXECUTION_LATENCY_MEASUREMENT']}
