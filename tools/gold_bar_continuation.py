"""Reconstruct cumulative prefixes through one canonical account per candidate.

The full frozen public capture is validated first. This provides offline prefix
continuation, not cross-epoch data collection or an online checkpoint service.
"""
from services.execution.backtest_authority import exact_identity_hash
from tools.gold_bar_cost_replay import compare_public


def _consistent(before, after):
    # Orders are mutable aggregates; compare their append-only ledger, order,
    # decision, risk and equity histories instead of terminal order statuses.
    for key in ('ledger_events', 'order_events', 'decision_events', 'risk_events', 'equity_history'):
        old, new = before[key], after[key]
        if new[:len(old)] != old:
            raise ValueError('BAR_CONTINUATION_PREFIX_CHANGED')


def compare_prefixes(*, frame_counts, **arguments):
    if (not isinstance(frame_counts, (tuple, list)) or not 2 <= len(frame_counts) <= 60
            or any(type(n) is not int or n < 1 for n in frame_counts)
            or list(frame_counts) != sorted(set(frame_counts))
            or 'frame_limit' in arguments):
        raise ValueError('INVALID_BAR_CONTINUATION_PREFIXES')
    prefixes = []
    for count in frame_counts:
        result = compare_public(**arguments, frame_limit=count)
        if prefixes:
            previous = prefixes[-1]
            _consistent(previous['cash_baseline'], result['cash_baseline'])
            if [t['candidate'] for t in previous['trials']] != [t['candidate'] for t in result['trials']]:
                raise ValueError('BAR_CONTINUATION_CANDIDATES_CHANGED')
            for before, after in zip(previous['trials'], result['trials']):
                _consistent(before['simulation'], after['simulation'])
                for key in ('strategy_events', 'signal_observations'):
                    if after[key][:len(before[key])] != before[key]:
                        raise ValueError('BAR_CONTINUATION_PREFIX_CHANGED')
        prefixes.append(result)
    report = dict(schema='GOLD_BAR_PREFIX_CONTINUATION_V1', frame_counts=list(frame_counts),
        prefixes=prefixes, prefix_consistency_verified=True, independent_windows_summed=False,
        authoritative=False, economic_eligible=False, submission_enabled=False,
        limitation='SAME_FROZEN_RULE_EPOCH_OFFLINE_RECONSTRUCTION_ONLY')
    report['result_hash'] = exact_identity_hash(report, name='gold_bar_prefix_continuation')
    return report
