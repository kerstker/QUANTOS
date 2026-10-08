"""Causal environment changes for the original persistent simulation account.

This is an execution-core helper, not a second ledger or a public-data collector.
Each snapshot stays immutable in the complete input identity. Received forecasts
retain their original timing assumptions. Unknown schedules and contract-unit
conversions remain unsupported rather than guessed.
"""

from dataclasses import asdict

from services.execution.authoritative_perpetual_backtest import FundingPolicySnapshot
from services.execution.perpetual_ledger_v2 import MarkEvent
from services.execution.perpetual_simulation import (
    ForecastFundingPolicySnapshot, RollingForecastFundingPolicySnapshot, SimulationFrame,
    _advance_simulation_frame, _canonical, _validate_market_environment,
)

MAX_ENVIRONMENT_SEGMENTS = 1000
POLICY = 'CAUSAL_ENVIRONMENT_CANCEL_PENDING_RETAIN_ACCOUNT_V1'


def segment_limit(session):
    budget = getattr(session, '_replay_budget', None)
    return MAX_ENVIRONMENT_SEGMENTS if budget is None else budget['maximum_environment_segments']


def segment(state, *, first_frame, timestamp):
    return _canonical(dict(first_frame=first_frame, timestamp=timestamp,
        contract_rules=[r.as_canonical_dict() for r in state['rule_items']],
        funding_policies=[p.as_canonical_dict() for p in state['policy_items']],
        order_rule_profiles=[asdict(p) for p in state['profile_items']]))


def _immutable_ids(records, proposed):
    for field, identity in (('contract_rules', 'rule_id'),
                            ('funding_policies', 'policy_epoch_id'),
                            ('order_rule_profiles', 'rule_id')):
        existing = {}
        for record in records:
            for value in record[field]:
                key = value.get('instrument_id', value.get('spec', {}).get('instrument_id')), value[identity]
                existing[key] = value
        for value in proposed[field]:
            key = value.get('instrument_id', value.get('spec', {}).get('instrument_id')), value[identity]
            if key in existing and value != existing[key]:
                raise ValueError('ENVIRONMENT_SNAPSHOT_ID_REUSED')


def advance_environment(session, frames, *, contract_rules, funding_policies, order_rule_profiles):
    state = session._state
    if state is None:
        raise ValueError('ENVIRONMENT_TRANSITION_REQUIRES_EXISTING_ACCOUNT')
    batch = tuple(frames)
    if not batch or any(type(frame) is not SimulationFrame for frame in batch):
        raise ValueError('EMPTY_ENVIRONMENT_BATCH')
    last = state['frame_items'][-1].timestamp
    times = tuple(f.timestamp for f in batch)
    if times != tuple(sorted(set(times))) or times[0] <= last:
        raise ValueError('ENVIRONMENT_FRAME_ORDER')
    rules, policies = tuple(contract_rules), tuple(funding_policies)
    if (order_rule_profiles is None or state['profile_items'] is None
            or any(type(p) not in (FundingPolicySnapshot, ForecastFundingPolicySnapshot,
                                  RollingForecastFundingPolicySnapshot)
                   for p in (*state['policy_items'], *policies))):
        raise ValueError('EXPLICIT_ORDER_RULES_AND_SUPPORTED_FUNDING_REQUIRED')
    profiles = tuple(order_rule_profiles)
    rule_map, policy_map, profile_map = _validate_market_environment(rules, policies, profiles, batch)
    account = state['account']
    if set(rule_map) != set(account.rules):
        raise ValueError('ENVIRONMENT_UNIVERSE_CHANGED')
    if {e.instrument_id for e in batch[0].market_events if type(e) is MarkEvent} != set(rule_map):
        raise ValueError('ENVIRONMENT_TRANSITION_REQUIRES_FRESH_MARKS')
    for name, policy in policy_map.items():
        old = account.policies[name]
        if policy.venue_id != old.venue_id:
            raise ValueError('ENVIRONMENT_VENUE_CHANGED')
        if old.coverage_end + 1 < policy.coverage_start:
            raise ValueError('ENVIRONMENT_FUNDING_COVERAGE_GAP')
        if any(last < t < times[0] for t in (*old.settlement_times, *policy.settlement_times)):
            raise ValueError('ENVIRONMENT_TRANSITION_SKIPPED_FUNDING')
        if any(last < t <= policy.ingestion_time and t not in policy.settlement_times
               for t in old.settlement_times):
            raise ValueError('ENVIRONMENT_DUE_FUNDING_REVISED_TOO_LATE')
        # A later receipt may revise a future schedule, never an observed prefix.
        start = max(old.coverage_start, policy.coverage_start)
        end = min(last, old.coverage_end, policy.coverage_end)
        if ({t for t in old.settlement_times if start <= t <= end}
                != {t for t in policy.settlement_times if start <= t <= end}):
            raise ValueError('ENVIRONMENT_PAST_FUNDING_REVISED')
        previous_rule = account.rules[name]
        if previous_rule.effective_until < rule_map[name].effective_from:
            raise ValueError('ENVIRONMENT_CONTRACT_COVERAGE_GAP')
    records = state.get('environment_segments')
    if records is None:
        records = [segment(state, first_frame=0, timestamp=state['frame_items'][0].timestamp)]
    if len(records) >= segment_limit(session):
        raise ValueError('ENVIRONMENT_SEGMENT_BUDGET')
    proposed_state = dict(rule_items=rules, policy_items=policies, profile_items=profiles)
    proposed = segment(proposed_state, first_frame=len(state['frame_items']), timestamp=times[0])
    _immutable_ids(records, proposed)
    # The canonical ledger validates every instrument before changing any spec.
    account.ledger.transition_contract_specs(tuple(r.spec for r in rules), timestamp=times[0])
    account.rules, account.policies, account.profiles = rule_map, policy_map, profile_map
    for pending in tuple(account.pending.values()):
        account.transition(pending, times[0], 'CANCELLED', 'ENVIRONMENT_EPOCH_CHANGED')
    # An old quote/capacity receipt cannot authorize a fill under a new profile.
    # The original ledger retains all consumed capacity and contract periods.
    account.quotes.clear()
    account.capacities.clear()
    account.mark_times.clear()
    account.mark_prices.clear()
    state.update(rule_items=rules, policy_items=policies, profile_items=profiles,
                 environment_segments=[*records, proposed])
    for frame in batch:
        _advance_simulation_frame(account, frame, session._configuration['strategy'])
    state['frame_items'].extend(batch)


def advance_segments(session, prefix, updates):
    from collections.abc import Sequence
    if not isinstance(updates, Sequence) or not updates or len(updates) >= segment_limit(session):
        raise ValueError('INVALID_ENVIRONMENT_CHECKPOINT_UPDATES')
    boundaries = []
    for update in updates:
        if (type(update) is not dict
                or set(update) != {'first_frame', 'contract_rules', 'funding_policies', 'order_rule_profiles'}
                or type(update['first_frame']) is not int
                or not 0 < update['first_frame'] < len(prefix)):
            raise ValueError('INVALID_ENVIRONMENT_CHECKPOINT_BOUNDARY')
        boundaries.append(update['first_frame'])
    if boundaries != sorted(set(boundaries)):
        raise ValueError('INVALID_ENVIRONMENT_CHECKPOINT_ORDER')
    session.advance(prefix[:boundaries[0]])
    for index, update in enumerate(updates):
        end = boundaries[index + 1] if index + 1 < len(boundaries) else len(prefix)
        configuration = {key: value for key, value in update.items() if key != 'first_frame'}
        session.advance_environment(prefix[boundaries[index]:end], **configuration)
