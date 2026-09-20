"""RF2 bounded 12-case offline driver (research only, no execution authority).

Drives the pre-registered RF2 batch
(``docs/engineering/plans/2026-09-20-rf2-bounded-strategy-experiment.md``):
four fixed candidates (cash, constant 25% passive long, the no-hysteresis
90-day return-sign trend, and ``LOG_SLOPE_90_HYSTERESIS_V1``) times three
adverse slippage levels (2/5/10 bp) = twelve cases, all with the existing 4 bp
taker fee, the existing 150 USDC cumulative-loss stop and the existing capacity
rule.  Outcomes are diagnostic: ``TECHNICAL_VALIDATION_ONLY`` on
``SEEN_HISTORY_NOT_UNTOUCHED_OOS`` history, never authority, paper or live.

Boundaries.  Read-only input, exclusive new output directory (no overwrite and
no resume), no network, no credentials, no order/broker path and no real-data
run of this driver.  The single PnL authority is the canonical
``services.research.perpetual_alpha.replay`` ledger plus the existing
``profitability_attribution.attribute_replay`` explanation; this module adds no
second PnL ledger and no retry, search or auto-tuning loop.  A trial's terminal
status means only whether its computation completed durably.

Ordering guarantees.  The whole 12-case family is registered in a TrialLedger
inside the output directory *before* the first replay; source identity and the
plan digest are frozen before any case, re-checked after loading and
before/after every case and before the summary.  Any drift (or any error while
checking identity) cancels the remaining trials, preserves an invalidated
artifact and suppresses the summary entirely.  Each case's PASSED/FAILED status
is durable in the ledger *before* its artifact JSON is published.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

import numpy as np

from services.execution.perpetual_ledger_v2 import canonical_decimal
from services.research.perpetual_alpha import replay
from services.research.profitability_attribution import attribute_replay
from services.research.rf2_bounded_strategy import (
    DailyClose, SignalStatus, calculate_decision, held_sign_from_weight,
)
from services.research.trial_ledger import TrialLedger, TrialRecord
from tools.perpetual_alpha_research import load_data
from tools.perpetual_replay_research import source_identity

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/engineering/plans/2026-09-20-rf2-bounded-strategy-experiment.md'

CASE_SCHEMA = 'QUANTOS_RF2_BOUNDED_CASE_V1'
SUMMARY_SCHEMA = 'QUANTOS_RF2_BOUNDED_STRATEGY_EXPERIMENT_V1'
EVIDENCE_CLASS = 'TECHNICAL_VALIDATION_ONLY'
HISTORY_CLASS = 'SEEN_HISTORY_NOT_UNTOUCHED_OOS'
CASE_STATUS_OK = 'TECHNICAL_VALIDATION_ONLY'
CASE_STATUS_FAILED = 'FAILED'
INVALIDATED_IDENTITY_DRIFT = 'INVALIDATED_IDENTITY_DRIFT'
INVALIDATED_IDENTITY_FREEZE_FAILED = 'INVALIDATED_IDENTITY_FREEZE_FAILED'
INVALIDATED_INPUT_LOAD_FAILED = 'INVALIDATED_INPUT_LOAD_FAILED'

CANDIDATES = ('cash', 'passive25', 'return_sign90', 'log_slope90')
SLIPPAGES = (2, 5, 10)
EXPECTED_CASE_COUNT = len(CANDIDATES) * len(SLIPPAGES)
SIGNAL_IDS = {'cash': 'CASH_V1',
              'passive25': 'DAILY_25PCT_PASSIVE_LONG_V1',
              'return_sign90': 'RETURN_SIGN_90_SAME_WINDOW_NO_HYSTERESIS_V1',
              'log_slope90': 'LOG_SLOPE_90_HYSTERESIS_V1'}
FAMILY_ID = hashlib.sha256(b'RF2_BOUNDED_STRATEGY_FAMILY_V1').hexdigest()

TAKER_FEE_BPS = D('4')
TARGET_WEIGHT = D('.25')
MAX_POSITION_WEIGHT = D('.30')
EXPECTED_INITIAL_EQUITY = '1500'
FORBIDDEN_OUTPUT_PREFIXES = ('/srv', '/opt')

RISK_COST_PROFILE = {
    'market': 'BTCUSDC_LINEAR_PERPETUAL',
    'settlement': 'USDC',
    'initial_collateral': '1500',
    'cumulative_loss_budget': '150',
    'stop_enabled': True,
    'taker_fee_bps': '4',
    'slippage_bps': [2, 5, 10],
    'target_weight': '0.25',
    'max_position_weight': '0.30',
    'daily_capacity_fraction': '0.01',
    'execution_proxy': 'MINUTE_OPEN_ADVERSE_SLIPPAGE_NO_QUEUE_PRIORITY',
}
LIMITATIONS = [
    HISTORY_CLASS,
    'TECHNICAL_VALIDATION_ONLY_NO_ECONOMIC_AUTHORITY',
    'CURRENT_RULE_SCENARIO_ONLY',
    'MINUTE_OPEN_EXECUTION_PROXY_NO_MAKER_QUEUE',
    'NO_FORWARD_OR_TESTNET_EVIDENCE',
    'COMPUTATION_SUCCESS_IS_NOT_PROFITABILITY',
    'ENDING_EQUITY_INCLUDES_MARKED_OPEN_POSITIONS_NO_FORCED_TERMINAL_EXIT',
    'RETROSPECTIVE_BAR_AVAILABILITY_ASSUMPTION',
    'HYPOTHETICAL_MARGIN_PROFILE_NOT_VENUE_CALIBRATION',
]


class PathRejection(ValueError):
    """Rejected input/output path arguments (nothing was created or overwritten)."""


class IdentityDriftError(RuntimeError):
    """Frozen source/plan identity changed (or could not be checked) during the batch."""


class CaseFailure(RuntimeError):
    """A single case could not be computed; the remaining cases still run."""


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _plan_digest() -> str:
    """Digest of the frozen pre-registration plan bytes (patchable test seam)."""
    return hashlib.sha256(PLAN.read_bytes()).hexdigest()


def _normalise(value):
    if isinstance(value, dict):
        return {str(key): _normalise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, D):
        return str(value)
    if hasattr(value, 'item'):
        return _normalise(value.item())
    raise TypeError(f'identity payload is not canonical JSON: {type(value).__name__}')


def _digest(value) -> str:
    encoded = json.dumps(_normalise(value), sort_keys=True, allow_nan=False, separators=(',', ':'))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _write_new(path: Path, payload) -> None:
    """Exclusive, non-finite-free publication: never overwrite an existing artifact."""
    with path.open('x') as handle:
        json.dump(payload, handle, allow_nan=False, indent=2)
        handle.write('\n')


def case_key(candidate, slip) -> str:
    return f'{candidate}/{slip}'


def case_filename(candidate, slip) -> str:
    return f'case-{candidate}-{slip}.json'


def _reject_symlink_chain(path: Path, label: str) -> None:
    current = path
    while True:
        if current.is_symlink():
            raise PathRejection(f'{label} path may not be a symlink: {current}')
        parent = current.parent
        if parent == current:
            return
        current = parent


def _validate_paths(input_directory, output_directory):
    source, destination = Path(input_directory), Path(output_directory)
    if not source.is_absolute() or not destination.is_absolute():
        raise PathRejection('absolute --input and --output paths are required')
    _reject_symlink_chain(source, 'input')
    _reject_symlink_chain(destination, 'output')
    source_resolved, destination_resolved = source.resolve(), destination.resolve()
    for forbidden in FORBIDDEN_OUTPUT_PREFIXES:
        prefix = Path(forbidden).resolve()
        if destination_resolved == prefix or prefix in destination_resolved.parents:
            raise PathRejection(f'output under {forbidden} is forbidden')
    if not source.is_dir():
        raise PathRejection(f'input directory does not exist: {source}')
    source_resolved, destination_resolved = source.resolve(), destination.resolve()
    if (source_resolved == destination_resolved or source_resolved in destination_resolved.parents
            or destination_resolved in source_resolved.parents):
        raise PathRejection(f'input/output overlap is forbidden: {source_resolved} / {destination_resolved}')
    if destination.exists():
        raise PathRejection(f'output must not exist (no overwrite and no resume): {destination}')
    if not destination.parent.is_dir():
        raise PathRejection(f'output parent directory does not exist: {destination.parent}')
    return source, destination


def _freeze_identity():
    source = source_identity()
    if not isinstance(source, dict) or not source:
        raise IdentityDriftError('release identity unavailable: empty source identity')
    return {'source': source, 'plan_sha256': _plan_digest()}


def _check_source_and_plan(frozen) -> None:
    """Fail closed on drift *and* on any failure of the identity probes themselves."""
    try:
        current_source = source_identity()
        current_plan = _plan_digest()
    except Exception as exc:  # noqa: BLE001 - an uncheckable identity is an invalid batch
        raise IdentityDriftError(
            f'source/plan identity check failed: {type(exc).__name__}: {exc}') from exc
    if current_source != frozen['source']:
        raise IdentityDriftError('source identity drift: frozen source differs from current source')
    if current_plan != frozen['plan_sha256']:
        raise IdentityDriftError('plan digest drift: plan bytes changed during the batch')


def _dataset_identity(market, provenance):
    start, end = int(market['start']), int(market['end'])
    return {'start_ms': start, 'end_ms': end,
            'start_iso': _moment(start).isoformat(), 'end_iso': _moment(end).isoformat(),
            'aligned_minutes': int(len(market['times'])),
            'provenance_sha256': _digest(provenance)}


def _moment(at_ms: int) -> datetime:
    return datetime.fromtimestamp(int(at_ms) // 1000, timezone.utc)


def _daily_close_records(days, daily):
    """One DailyClose per loaded UTC day; missing closes stay NaN for the window validator.

    Nothing is validated here: warmup gaps and any day outside a required window
    must not reject the batch, and a gap *inside* a required window must reach
    the signal's own validator instead of being silently dropped.
    """
    records = []
    for index, stamp in enumerate(np.asarray(days)):
        value = float(np.asarray(daily)[index, 3])
        records.append(DailyClose(day=_moment(int(stamp)).date(),
                                  close=D(repr(value)) if np.isfinite(value) else D('NaN')))
    return tuple(records)


def planned_trials(identity):
    """The twelve pre-registered cases, in fixed order, as ledger payloads."""
    trials = []
    for candidate in CANDIDATES:
        for slip in SLIPPAGES:
            definition = {'schema': 'RF2_CASE_DEFINITION_V1', 'candidate': candidate,
                          'signal': SIGNAL_IDS[candidate], 'slip_bps': slip,
                          'source': identity['source'], 'plan_sha256': identity['plan_sha256'],
                          'taker_fee_bps': RISK_COST_PROFILE['taker_fee_bps'],
                          'dataset': identity['dataset'], 'risk_cost_profile': RISK_COST_PROFILE}
            exact_definition_id = _digest(definition)
            trials.append({
                'candidate': candidate, 'slip_bps': slip,
                'trial_id': _digest({'schema': 'RF2_TRIAL_V1', 'family_id': FAMILY_ID,
                                     'exact_definition_id': exact_definition_id}),
                'record': {'alpha_id': _digest(f'RF2_ALPHA_{candidate}_V1'), 'family_id': FAMILY_ID,
                           'exact_definition_id': exact_definition_id,
                           'validation_policy_digest': identity['plan_sha256']}})
    return trials


def _append(ledger, plan, status, counters):
    trial_id = plan['trial_id']
    ledger.append(TrialRecord(**plan['record'], trial_id=trial_id, status=status,
                              event_index=counters[trial_id], at=_now_ms()))
    counters[trial_id] += 1


def _register(ledger, plans, counters, identity):
    for plan in plans:
        counters[plan['trial_id']] = 0
        _append(ledger, plan, 'PLANNED', counters)
    if len(plans) != EXPECTED_CASE_COUNT or len({plan['trial_id'] for plan in plans}) != EXPECTED_CASE_COUNT:
        raise CaseFailure('pre-registered case family is not exactly twelve distinct trials')
    ledger.verify()
    for plan in plans:
        if ledger.load(plan['trial_id']).status != 'PLANNED':
            raise CaseFailure('case family registration did not commit before any replay')


def _invalidated(status, identity, *, reason=None, error=None, trial_id=None, candidate=None, slip=None):
    payload = {'schema': CASE_SCHEMA, 'status': status, 'evidence_class': EVIDENCE_CLASS,
               'history_class': HISTORY_CLASS, 'identity': identity, 'batch_complete': False,
               'authority_admitted': False, 'authoritative': False, 'live_eligible': False,
               'paper_admitted': False, 'limitations': LIMITATIONS}
    for key, value in (('reason', reason), ('error', error), ('trial_id', trial_id),
                       ('candidate', candidate), ('slip_bps', slip)):
        if value is not None:
            payload[key] = value
    return payload


def _abort(dst, ledger, plans, counters, identity, index, running, reason):
    """Drift: preserve an invalidated artifact, cancel what remains, publish no summary."""
    if running:
        _append(ledger, plans[index], 'FAILED', counters)
        remaining = plans[index + 1:]
    else:
        remaining = plans[index:]
    for plan in remaining:
        _append(ledger, plan, 'CANCELLED', counters)
    plan = plans[index]
    _write_new(dst / f'invalidated-{plan["trial_id"]}.json',
               _invalidated(INVALIDATED_IDENTITY_DRIFT, identity, reason=reason,
                            trial_id=plan['trial_id'], candidate=plan['candidate'], slip=plan['slip_bps']))
    raise IdentityDriftError(reason)


def _annual_pnl(marks):
    """Annual equity differences from the same daily marks; no separate PnL ledger."""
    values = [float(row['equity']) for row in marks]
    periods = {}
    for year in (2025, 2026):
        indices = [index for index, row in enumerate(marks[:-1])
                   if _moment(row['time']).year == year]
        periods[str(year)] = float(values[indices[-1] + 1] - values[indices[0]]) if indices else None
    return periods


def _case_decisions(candidate, signal_records, replay_decisions):
    if len(signal_records) != len(replay_decisions):
        raise CaseFailure(f'{candidate}: decision receipt count mismatch '
                          f'({len(signal_records)} signal / {len(replay_decisions)} replay)')
    merged = []
    for record, receipt in zip(signal_records, replay_decisions):
        at = int(receipt['time'])
        if int(record['time']) != at:
            raise CaseFailure(f'{candidate}: decision receipt time mismatch at {at}')
        quantity = D(receipt['actual_quantity'])
        actual_sign = held_sign_from_weight(quantity)
        if record['held_sign_actual'] != actual_sign:
            raise CaseFailure(f'{candidate}: documented held sign disagrees with the actual ledger '
                              f'quantity at {at}')
        target = receipt['target']
        if target is None:
            raise CaseFailure(f'{candidate}: engine recorded no target at {at}')
        if D(str(target)) != D(record['target_weight']):
            raise CaseFailure(f'{candidate}: engine target {target!r} differs from the pre-registered '
                              f'target {record["target_weight"]} at {at}')
        merged.append({**record, 'actual_quantity': receipt['actual_quantity'],
                       'actual_held_sign': actual_sign, 'halted': bool(receipt['halted']),
                       'replay_target': repr(float(target))})
    return merged


def _run_case(candidate, slip, trial_id, market, records, identity):
    """One case: real replay, real ledger, existing attribution, no retry."""
    signal_records = []

    def context_decision(at, context):
        moment = _moment(int(at))
        held = held_sign_from_weight(D(context['quantity']))
        if candidate in ('cash', 'passive25'):
            target = D(0) if candidate == 'cash' else TARGET_WEIGHT
            record = {'signal_status': 'NOT_APPLICABLE',
                      'signal_reason': f'fixed constant target for {candidate}: no signal consulted',
                      'statistic': None, 'window_last_day': None, 'window_size': 0, 'available_days': 0}
        else:
            decision = calculate_decision(records, moment, held)
            if decision.status is SignalStatus.MALFORMED_INPUT:
                raise CaseFailure(f'{candidate} decision {moment.isoformat()} rejected: '
                                  f'MALFORMED_INPUT: {decision.reason}')
            target = decision.target_weight if candidate == 'log_slope90' else decision.benchmark_target_weight
            record = {'signal_status': decision.status.value, 'signal_reason': decision.reason,
                      'statistic': decision.statistic, 'window_size': decision.window_size,
                      'available_days': decision.available_days,
                      'window_last_day': (decision.window_last_day.isoformat()
                                          if decision.window_last_day else None)}
        record.update({'time': int(at), 'time_iso': moment.isoformat(),
                       'target_weight': canonical_decimal(target),
                       'held_sign_actual': held, 'held_sign_intended': held_sign_from_weight(target)})
        signal_records.append(record)
        return float(target)  # the replay checks finiteness with numpy, so never hand it a Decimal

    started = time.monotonic()
    result = replay(times=market['times'], prices=market['prices'], marks=market['marks'],
                    funding=market['funding'], start=market['start'], end=market['end'],
                    daily_volume=market['daily_volume'], decision=None,
                    context_decision=context_decision, slip_bps=slip, stop=True,
                    maximum_quantity=market['maximum_quantity'], taker_fee_bps=TAKER_FEE_BPS,
                    target_weight=TARGET_WEIGHT, max_position_weight=MAX_POSITION_WEIGHT)
    elapsed = round(time.monotonic() - started, 6)
    reconciliation = result['reconciliation']
    if reconciliation.get('residual') != '0':
        raise CaseFailure('canonical ledger reconciliation residual is not zero')
    if reconciliation.get('initial_equity') != EXPECTED_INITIAL_EQUITY:
        raise CaseFailure(f'initial collateral is not the frozen {EXPECTED_INITIAL_EQUITY} USDC profile')
    attribution = attribute_replay(result)
    if attribution['status'] != 'RECONCILED_TECHNICAL_ONLY' or attribution.get('reconciliation_residual') != '0':
        raise CaseFailure(f'profitability attribution not reconciled: {attribution.get("reason")}')
    decisions = _case_decisions(candidate, signal_records, result['decisions'])
    metrics = {'net_pnl': attribution['net_pnl'], 'gross_reference_pnl': attribution['gross_reference_pnl'],
               'fees': attribution['fees'], 'funding': attribution['funding'],
               'embedded_slippage': attribution['embedded_slippage'],
               'turnover_notional': attribution['turnover_notional'],
               'turnover_initial_equity': attribution['turnover_initial_equity'],
               'diagnosis': attribution['diagnosis'], 'fills': len(result['fills']),
               'orders': len(result['orders']),
               'rejected_orders': sum(1 for order in result['orders']
                                      if str(order.get('status', '')).startswith('REJECTED')),
               'decision_count': len(decisions), 'annual_pnl': _annual_pnl(result['daily']),
               'max_drawdown': float(result['minute_max_drawdown']),
               'average_exposure': float(result['average_exposure']),
               'halted_at': result['halted_at'], 'elapsed_seconds': elapsed}
    return {'schema': CASE_SCHEMA, 'status': CASE_STATUS_OK, 'evidence_class': EVIDENCE_CLASS,
            'history_class': HISTORY_CLASS, 'identity': identity, 'candidate': candidate,
            'signal': SIGNAL_IDS[candidate], 'slip_bps': slip, 'trial_id': trial_id,
            'cost_profile': {'slippage_bps': slip, 'taker_fee_bps': RISK_COST_PROFILE['taker_fee_bps']},
            'risk_profile': RISK_COST_PROFILE, 'metrics': metrics, 'attribution': attribution,
            'decisions': decisions, 'replay': result, 'authority_admitted': False,
            'authoritative': False, 'live_eligible': False, 'paper_admitted': False,
            'computation': 'COMPLETED_NOT_ECONOMIC_SUCCESS_OR_AUTHORITY',
            'limitations': LIMITATIONS}


def _failed_case(candidate, slip, trial_id, identity, error, elapsed):
    return {'schema': CASE_SCHEMA, 'status': CASE_STATUS_FAILED, 'evidence_class': EVIDENCE_CLASS,
            'history_class': HISTORY_CLASS, 'identity': identity, 'candidate': candidate,
            'signal': SIGNAL_IDS[candidate], 'slip_bps': slip, 'trial_id': trial_id,
            'cost_profile': {'slippage_bps': slip, 'taker_fee_bps': RISK_COST_PROFILE['taker_fee_bps']},
            'risk_profile': RISK_COST_PROFILE, 'error': error, 'elapsed_seconds': elapsed,
            'authority_admitted': False, 'authoritative': False, 'live_eligible': False,
            'paper_admitted': False, 'computation': 'INCOMPLETE_FAILED', 'limitations': LIMITATIONS,
            'note': 'COMPUTATION FAILURE ONLY: not an economic rejection of the candidate'}


def _summary_entry(plan, artifact):
    metrics = artifact.get('metrics', {})
    return {'trial_id': plan['trial_id'], 'candidate': plan['candidate'], 'slip_bps': plan['slip_bps'],
            'status': artifact['status'], 'net_pnl': metrics.get('net_pnl'),
            'gross_reference_pnl': metrics.get('gross_reference_pnl'), 'fees': metrics.get('fees'),
            'funding': metrics.get('funding'), 'turnover_notional': metrics.get('turnover_notional'),
            'fills': metrics.get('fills'), 'max_drawdown': metrics.get('max_drawdown'),
            'annual_pnl': metrics.get('annual_pnl'), 'halted_at': metrics.get('halted_at'),
            'elapsed_seconds': metrics.get('elapsed_seconds'), 'diagnosis': metrics.get('diagnosis'),
            'error': artifact.get('error')}


def _screen(cases):
    """The pre-registered admission condition, evaluated exactly as written; no selection."""
    required = [case_key(candidate, slip) for candidate in CANDIDATES
                for slip in SLIPPAGES]
    if set(cases) != set(required) or any(cases[key].get('status') != CASE_STATUS_OK for key in required):
        return {'passed': False, 'reason': 'FAILED_COMPUTATION', 'checks': {},
                'failed_checks': ['COMPLETE_TWELVE_CASE_COMPUTATION'],
                'authority_admitted': False, 'paper_admitted': False, 'live_eligible': False}
    center = cases[case_key('log_slope90', 5)]
    passive = cases[case_key('passive25', 5)]
    benchmark = cases[case_key('return_sign90', 5)]
    years = center['annual_pnl'] or {}
    checks = {
        'all_three_cost_cases_net_positive': all(
            D(cases[case_key('log_slope90', slip)]['net_pnl']) > 0 for slip in SLIPPAGES),
        'center_both_natural_years_positive': all(
            years.get(str(year)) is not None and years[str(year)] > 0 for year in (2025, 2026)),
        'center_at_least_ten_fills': center['fills'] is not None and center['fills'] >= 10,
        'center_max_drawdown_at_most_ten_percent': (center['max_drawdown'] is not None
                                                    and center['max_drawdown'] <= 0.10),
        'center_net_above_passive_center': D(center['net_pnl']) > D(passive['net_pnl']),
        'center_turnover_below_return_sign90_center': (D(center['turnover_notional'])
                                                       < D(benchmark['turnover_notional'])),
    }
    passed = all(checks.values())
    return {'passed': passed,
            'reason': 'PRE_REGISTERED_CONDITIONS_MET' if passed else 'PRE_REGISTERED_CONDITIONS_NOT_MET',
            'checks': checks, 'failed_checks': [key for key, ok in checks.items() if not ok],
            'authority_admitted': False, 'paper_admitted': False, 'live_eligible': False,
            'next_step_on_pass': 'REGISTER_NEW_FORWARD_WINDOW_AND_TESTNET_PREPARATION_ONLY'}


def _execute(source: Path, destination: Path, frozen) -> dict:
    try:
        _check_source_and_plan(frozen)
        market, days, daily, refs, funding_factor, provenance = load_data(directory=str(source))
        _check_source_and_plan(frozen)
        plan_bytes = PLAN.read_bytes()
        if hashlib.sha256(plan_bytes).hexdigest() != frozen['plan_sha256']:
            raise IdentityDriftError('plan changed before registration snapshot')
        _write_new(destination / 'registration.json', {
            'plan_text': plan_bytes.decode('utf-8'), 'plan_sha256': frozen['plan_sha256'],
        })
        dataset = _dataset_identity(market, provenance)
    except IdentityDriftError as exc:
        _write_new(destination / 'invalidated.json',
                   _invalidated(INVALIDATED_IDENTITY_DRIFT, {'frozen': frozen}, reason=str(exc)))
        raise
    except Exception as exc:  # noqa: BLE001 - an unreadable input must never look like a result
        _write_new(destination / 'invalidated.json',
                   _invalidated(INVALIDATED_INPUT_LOAD_FAILED, {'frozen': frozen},
                                error=f'{type(exc).__name__}: {exc}'))
        raise
    identity = {'schema': 'RF2_BATCH_IDENTITY_V1', 'plan': str(PLAN.relative_to(ROOT)),
                'plan_sha256': frozen['plan_sha256'], 'source': frozen['source'],
                'dataset': dataset, 'risk_cost_profile': RISK_COST_PROFILE}
    _write_new(destination / 'identity.json', identity)
    records = _daily_close_records(days, daily)
    plans = planned_trials(identity)
    counters = {}
    ledger = TrialLedger(str(destination / 'trials.sqlite'))
    try:
        _register(ledger, plans, counters, identity)
        cases = {}
        for index, plan in enumerate(plans):
            key = case_key(plan['candidate'], plan['slip_bps'])
            try:
                _check_source_and_plan(frozen)
            except IdentityDriftError as exc:
                _abort(destination, ledger, plans, counters, identity, index, False, str(exc))
            _append(ledger, plan, 'RUNNING', counters)
            started = time.monotonic()
            try:
                artifact = _run_case(plan['candidate'], plan['slip_bps'], plan['trial_id'],
                                     market, records, identity)
            except Exception as exc:  # noqa: BLE001 - one failed case must not hide the rest
                artifact = _failed_case(plan['candidate'], plan['slip_bps'], plan['trial_id'], identity,
                                        f'{type(exc).__name__}: {exc}',
                                        round(time.monotonic() - started, 6))
            try:
                _check_source_and_plan(frozen)
            except IdentityDriftError as exc:
                _abort(destination, ledger, plans, counters, identity, index, True, str(exc))
            _append(ledger, plan, 'PASSED' if artifact['status'] == CASE_STATUS_OK else 'FAILED', counters)
            _write_new(destination / case_filename(plan['candidate'], plan['slip_bps']), artifact)
            cases[key] = _summary_entry(plan, artifact)
        try:
            _check_source_and_plan(frozen)
        except IdentityDriftError as exc:
            _write_new(destination / 'invalidated.json',
                       _invalidated(INVALIDATED_IDENTITY_DRIFT, identity, reason=str(exc)))
            raise
        if ledger.verify() is not True:
            raise CaseFailure('trial ledger verification failed')
        passed = sum(1 for entry in cases.values() if entry['status'] == CASE_STATUS_OK)
        summary = {'schema': SUMMARY_SCHEMA, 'status': EVIDENCE_CLASS, 'evidence_class': EVIDENCE_CLASS,
                   'history_class': HISTORY_CLASS, 'authoritative': False, 'authority_admitted': False,
                   'live_eligible': False, 'paper_admitted': False, 'identity': identity,
                   'planned_trials': EXPECTED_CASE_COUNT,
                   'planned_trial_ids': [plan['trial_id'] for plan in plans], 'cases': cases,
                   'completed_cases': passed, 'failed_cases': EXPECTED_CASE_COUNT - passed,
                   'trial_ledger_verified': True,
                   'replay_engine': 'services.research.perpetual_alpha.replay',
                   'attribution_engine': 'services.research.profitability_attribution.attribute_replay',
                   'optimization': 'NONE_NO_RETRY_NO_AUTOTUNING_SINGLE_FROZEN_SPECIFICATION',
                   'trial_success_meaning': 'COMPUTATION_ONLY_NOT_PROFITABILITY_OR_AUTHORITY',
                   'screening': _screen(cases), 'limitations': LIMITATIONS}
        _write_new(destination / 'summary.json', summary)
        return summary
    finally:
        ledger.close()


def run_experiment(input_directory, output_directory) -> dict:
    """Run the bounded twelve-case RF2 batch into a new, exclusive output directory."""
    source, destination = _validate_paths(input_directory, output_directory)
    try:
        destination.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise PathRejection(f'output must not exist (no overwrite and no resume): {destination}') from exc
    try:
        frozen = _freeze_identity()
    except Exception as exc:  # noqa: BLE001 - an unfreezable identity invalidates the batch
        _write_new(destination / 'invalidated.json',
                   _invalidated(INVALIDATED_IDENTITY_FREEZE_FAILED, None,
                                error=f'{type(exc).__name__}: {exc}'))
        if isinstance(exc, IdentityDriftError):
            raise
        raise IdentityDriftError(f'identity freeze failed: {type(exc).__name__}: {exc}') from exc
    return _execute(source, destination, frozen)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='RF2 bounded twelve-case offline research driver (no execution authority)')
    parser.add_argument('--input', required=True, help='absolute read-only input archive directory')
    parser.add_argument('--output', required=True, help='absolute new output directory (must not exist)')
    arguments = parser.parse_args(argv)
    try:
        summary = run_experiment(arguments.input, arguments.output)
    except Exception as exc:  # noqa: BLE001 - a failed batch must exit non-zero, never silently
        print(f'RF2_RUN_FAILED {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({'status': summary['status'], 'cases': len(summary['cases']),
                      'failed_cases': summary['failed_cases'],
                      'completed_cases': summary['completed_cases'],
                      'screening_passed': summary['screening']['passed'],
                      'output': str(arguments.output)}))
    return 0 if summary['failed_cases'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
