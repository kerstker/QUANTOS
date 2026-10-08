"""Read-only verifier for a saved RF2 bounded twelve-case run (technical receipts only).

This module inspects the artifacts published by
``tools.rf2_bounded_strategy_experiment`` and re-runs only the cheap,
deterministic consistency checks over what was *saved*: the trial-ledger hash
chain, the recomputed planned-trial identities, the recomputed summary entries
and pre-registered screening, and the canonical
``profitability_attribution.attribute_replay`` explanation of every saved
successful replay.  Nothing here replays raw market data, rebuilds a price path
or re-derives any source digest from the current repository, because this batch
is historical evidence.

It therefore must not be read as an independent validation of the raw input's
point-in-time availability, of the strategy's execution, or of any economic
result.  A verified report only says the saved receipts are internally
consistent with the frozen RF2 schemas and carry no authority.

Reads exactly ``summary.json``, ``identity.json``, ``registration.json``, the
twelve fixed ``case-{candidate}-{slip}.json`` files and ``trials.sqlite``: no
recursive scan, no symlinks, bounded file size, strict JSON (duplicate keys and
non-finite numbers rejected), and the SQLite ledger is opened read-only so no
``-wal``/``-journal`` companion is created.  Files are hashed before and after
consumption and the directory listing is re-checked, so a report only describes
bytes that did not move while being read.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

from services.research.profitability_attribution import attribute_replay
from services.execution.perpetual_ledger_v2 import ledger_decimal_context
from services.research.trial_ledger import TrialLedger, TrialLedgerError
from tools import rf2_bounded_strategy_experiment as driver

EVIDENCE_SCHEMA = 'RF2_RESEARCH_EVIDENCE_V1'
EVIDENCE_STATUS = 'VERIFIED_SAVED_RECEIPTS_TECHNICAL_ONLY'
RECONCILED = 'RECONCILED_TECHNICAL_ONLY'
MAX_FILE_BYTES = 16 * 1024 * 1024
FIXED_FILES = ('summary.json', 'identity.json', 'registration.json', 'trials.sqlite')
LEDGER_TRIAL_STATES = ('PLANNED', 'RUNNING')
LEDGER_TERMINAL_STATES = ('PASSED', 'FAILED')
AUTHORITY_FLAGS = ('authoritative', 'authority_admitted', 'live_eligible', 'paper_admitted')
SUCCESS_COMPUTATION = 'COMPLETED_NOT_ECONOMIC_SUCCESS_OR_AUTHORITY'
FAILED_COMPUTATION = 'INCOMPLETE_FAILED'
LIMITATIONS = [
    'SAVED_RECEIPTS_ONLY_NOT_AN_INDEPENDENT_REPLAY',
    'RAW_INPUT_POINT_IN_TIME_AVAILABILITY_NOT_INDEPENDENTLY_VERIFIED',
    'STRATEGY_EXECUTION_NOT_INDEPENDENTLY_VALIDATED',
    'NO_UNTOUCHED_OOS_SEEN_HISTORY_ONLY',
    'NO_FORWARD_OR_TESTNET_EVIDENCE',
    'NO_EXTERNAL_ECONOMIC_OR_OPERATOR_AUTHORITY',
    'NO_SIGNED_PREREGISTRATION_TIMESTAMP',
]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _same(left, right):
    """JSON identity preserves bool/int distinctions unlike Python equality."""
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def _iso(at_ms: int) -> str:
    return datetime.fromtimestamp(at_ms // 1000, timezone.utc).isoformat()


def _pairs(name: str):
    def hook(items):
        seen = {}
        for key, value in items:
            if key in seen:
                raise ValueError(f'{name}: duplicate JSON key {key!r}')
            seen[key] = value
        return seen
    return hook


def _reject_constant(token):
    raise ValueError(f'non-finite JSON constant {token!r}')


def _reject_nonfinite(value, name: str) -> None:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError(f'{name}: non-finite number in evidence')
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def _validated_root(root) -> Path:
    if isinstance(root, Path):
        candidate = root
    elif isinstance(root, str):
        candidate = Path(root)
    else:
        raise ValueError('evidence root must be a filesystem path')
    if not candidate.is_absolute():
        raise ValueError('evidence root must be an absolute path')
    base = Path(os.path.abspath(candidate))
    current = base
    while True:
        if current.is_symlink():
            raise ValueError(f'evidence root path may not contain a symlink: {current}')
        parent = current.parent
        if parent == current:
            break
        current = parent
    if not base.is_dir():
        raise ValueError(f'evidence root is not a directory: {base}')
    return base


def _expected_entries() -> set:
    names = set(FIXED_FILES)
    for candidate in driver.CANDIDATES:
        for slip in driver.SLIPPAGES:
            names.add(driver.case_filename(candidate, slip))
    return names


def _listing(base: Path) -> set:
    entries = set(os.listdir(base))
    for name in sorted(entries):
        lowered = name.lower()
        if lowered.startswith('invalidated'):
            raise ValueError(f'run was invalidated: {name} is present')
        if 'fail' in lowered or lowered in ('error.json', 'failure.json'):
            raise ValueError(f'failure marker is present: {name}')
    unknown = entries - _expected_entries()
    if unknown:
        raise ValueError(f'unexpected evidence entries: {sorted(unknown)}')
    return entries


def _consume(base: Path, name: str, consumed: dict) -> bytes:
    path = base / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'evidence file missing or unsafe: {name}')
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f'evidence file exceeds {MAX_FILE_BYTES} bytes: {name}')
    with path.open('rb') as stream:
        data = stream.read(MAX_FILE_BYTES+1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f'evidence file grew beyond {MAX_FILE_BYTES} bytes: {name}')
    consumed[name] = (_sha256(data), len(data))
    return data


def _load_json(base: Path, name: str, consumed: dict):
    data = _consume(base, name, consumed)
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError(f'evidence file is not UTF-8: {name}') from exc
    try:
        value = json.loads(text, object_pairs_hook=_pairs(name), parse_constant=_reject_constant)
    except ValueError as exc:
        raise ValueError(f'invalid evidence JSON in {name}: {exc}') from exc
    _reject_nonfinite(value, name)
    if not isinstance(value, dict):
        raise ValueError(f'evidence document must be an object: {name}')
    return value


def _verify_dataset(dataset, name: str) -> None:
    if not isinstance(dataset, dict):
        raise ValueError(f'{name}: dataset identity is missing')
    start, end = dataset.get('start_ms'), dataset.get('end_ms')
    if (isinstance(start, bool) or not isinstance(start, int)
            or isinstance(end, bool) or not isinstance(end, int) or start >= end):
        raise ValueError(f'{name}: dataset window is invalid')
    if dataset.get('start_iso') != _iso(start) or dataset.get('end_iso') != _iso(end):
        raise ValueError(f'{name}: dataset ISO timestamps disagree with the millisecond window')
    aligned = dataset.get('aligned_minutes')
    if isinstance(aligned, bool) or not isinstance(aligned, int) or aligned <= 0:
        raise ValueError(f'{name}: dataset aligned_minutes is invalid')
    provenance = dataset.get('provenance_sha256')
    if not isinstance(provenance, str) or len(provenance) != 64:
        raise ValueError(f'{name}: dataset provenance digest is invalid')


def _verify_frozen_identity(identity, registration, summary) -> None:
    if identity.get('schema') != 'RF2_BATCH_IDENTITY_V1':
        raise ValueError('identity.json is not a frozen RF2 batch identity')
    if not _same(identity.get('risk_cost_profile'), driver.RISK_COST_PROFILE):
        raise ValueError('identity risk/cost profile differs from the frozen RF2 profile')
    source = identity.get('source')
    if not isinstance(source, dict) or not source:
        raise ValueError('identity source receipt is missing')
    _verify_dataset(identity.get('dataset'), 'identity.dataset')
    plan_pointer = identity.get('plan')
    if (not isinstance(plan_pointer, str) or not plan_pointer
            or Path(plan_pointer).is_absolute() or '..' in Path(plan_pointer).parts):
        raise ValueError('identity plan pointer is not a safe relative path')
    digest = registration.get('plan_sha256')
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError('registration plan digest is missing')
    if digest != identity.get('plan_sha256'):
        raise ValueError('registration and identity plan digests differ')
    plan_text = registration.get('plan_text')
    if not isinstance(plan_text, str) or not plan_text:
        raise ValueError('registration plan text is missing')
    if _sha256(plan_text.encode('utf-8')) != digest:
        raise ValueError('registration plan text does not hash to the frozen plan digest')
    if not _same(summary.get('identity'), identity):
        raise ValueError('summary identity differs from identity.json')


def _verify_summary_header(summary) -> None:
    if summary.get('schema') != driver.SUMMARY_SCHEMA:
        raise ValueError('summary.json is not an RF2 experiment summary')
    if summary.get('status') != driver.EVIDENCE_CLASS or summary.get('evidence_class') != driver.EVIDENCE_CLASS:
        raise ValueError('summary evidence class is not TECHNICAL_VALIDATION_ONLY')
    if summary.get('history_class') != driver.HISTORY_CLASS:
        raise ValueError('summary history class is not the seen-history diagnostic class')
    for flag in AUTHORITY_FLAGS:
        if summary.get(flag) is not False:
            raise ValueError(f'summary {flag} must be exactly false')
    if summary.get('trial_ledger_verified') is not True:
        raise ValueError('summary does not declare a verified trial ledger')
    if summary.get('replay_engine') != 'services.research.perpetual_alpha.replay':
        raise ValueError('summary replay engine is not the canonical replay')
    if summary.get('attribution_engine') != 'services.research.profitability_attribution.attribute_replay':
        raise ValueError('summary attribution engine is not the canonical attribution')
    if summary.get('optimization') != 'NONE_NO_RETRY_NO_AUTOTUNING_SINGLE_FROZEN_SPECIFICATION':
        raise ValueError('summary optimization policy is not the frozen single specification')
    if summary.get('trial_success_meaning') != 'COMPUTATION_ONLY_NOT_PROFITABILITY_OR_AUTHORITY':
        raise ValueError('summary trial success meaning overstates computation success')
    if summary.get('limitations') != driver.LIMITATIONS:
        raise ValueError('summary limitations differ from the frozen RF2 limitations')


def _verify_planned(plans, summary) -> None:
    if len(plans) != driver.EXPECTED_CASE_COUNT:
        raise ValueError('recomputed case family is not exactly twelve')
    ids = [plan['trial_id'] for plan in plans]
    if len(set(ids)) != len(ids):
        raise ValueError('recomputed case family has duplicate trial ids')
    if summary.get('planned_trials') != driver.EXPECTED_CASE_COUNT:
        raise ValueError('summary planned trial count is not twelve')
    if summary.get('planned_trial_ids') != ids:
        raise ValueError('summary planned trial ids differ from the recomputed identities')


def _finite_elapsed(value, name: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value)) or float(value) < 0):
        raise ValueError(f'{name}: elapsed_seconds must be a finite non-negative number')
    return float(value)


def _verify_case_identity(plan, artifact, identity, name: str) -> None:
    if artifact.get('schema') != driver.CASE_SCHEMA:
        raise ValueError(f'{name}: not an RF2 case artifact')
    if artifact.get('history_class') != driver.HISTORY_CLASS:
        raise ValueError(f'{name}: history class is not the seen-history diagnostic class')
    if artifact.get('evidence_class') != driver.EVIDENCE_CLASS:
        raise ValueError(f'{name}: evidence class is not TECHNICAL_VALIDATION_ONLY')
    if not _same(artifact.get('identity'), identity):
        raise ValueError(f'{name}: case identity differs from identity.json')
    for field, expected in (('candidate', plan['candidate']), ('slip_bps', plan['slip_bps']),
                            ('signal', driver.SIGNAL_IDS[plan['candidate']]), ('trial_id', plan['trial_id'])):
        if artifact.get(field) != expected:
            raise ValueError(f'{name}: case {field} disagrees with the pre-registered case')
    cost_profile = {'slippage_bps': plan['slip_bps'], 'taker_fee_bps': driver.RISK_COST_PROFILE['taker_fee_bps']}
    if not _same(artifact.get('cost_profile'), cost_profile):
        raise ValueError(f'{name}: case cost profile is not the frozen slippage/fee scenario')
    if not _same(artifact.get('risk_profile'), driver.RISK_COST_PROFILE):
        raise ValueError(f'{name}: case risk profile differs from the frozen RF2 profile')
    for flag in AUTHORITY_FLAGS:
        if artifact.get(flag) is not False:
            raise ValueError(f'{name}: {flag} must be exactly false')


@ledger_decimal_context()
def _verify_success_case(plan, artifact, name: str):
    if artifact.get('computation') != SUCCESS_COMPUTATION:
        raise ValueError(f'{name}: computation success is overstated')
    replay = artifact.get('replay')
    if not isinstance(replay, dict):
        raise ValueError(f'{name}: saved replay receipts are missing')
    if (replay.get('cash_model') != 'CANONICAL_LINEAR_USDC_PERPETUAL'
            or replay.get('authoritative') is not False or replay.get('live_eligible') is not False):
        raise ValueError(f'{name}: replay currency or authority conflicts with the case')
    reconciliation = replay.get('reconciliation')
    if not isinstance(reconciliation, dict):
        raise ValueError(f'{name}: saved reconciliation is missing')
    if reconciliation.get('initial_equity') != driver.EXPECTED_INITIAL_EQUITY:
        raise ValueError(f'{name}: initial collateral is not the frozen {driver.EXPECTED_INITIAL_EQUITY} USDC profile')
    if reconciliation.get('residual') != '0':
        raise ValueError(f'{name}: saved reconciliation residual is not zero')
    recomputed = attribute_replay(replay)
    if recomputed.get('status') != RECONCILED:
        raise ValueError(f'{name}: canonical attribution is not reconciled ({recomputed.get("reason")})')
    if not _same(recomputed, artifact.get('attribution')):
        raise ValueError(f'{name}: saved attribution differs from the canonical recomputation')
    metrics = artifact.get('metrics')
    if not isinstance(metrics, dict):
        raise ValueError(f'{name}: saved metrics are missing')
    for key in ('net_pnl', 'gross_reference_pnl', 'fees', 'funding', 'embedded_slippage',
                'turnover_notional', 'turnover_initial_equity', 'diagnosis'):
        if metrics.get(key) != recomputed.get(key):
            raise ValueError(f'{name}: metrics.{key} disagrees with the canonical attribution')
    fills, orders = replay.get('fills'), replay.get('orders')
    decisions, daily = replay.get('decisions'), replay.get('daily')
    if not isinstance(fills, list) or not isinstance(orders, list) or not isinstance(decisions, list):
        raise ValueError(f'{name}: saved replay receipts are incomplete')
    for fill in fills:
        quantity, price, reference, fee = (Decimal(fill[key]) for key in ('quantity', 'price', 'reference', 'fee'))
        if fee != abs(quantity)*price*Decimal('0.0004'):
            raise ValueError(f'{name}: saved fill fee is not the frozen four-bp scenario')
        factor = Decimal(plan['slip_bps'])/10000
        expected = reference*(1+factor if quantity > 0 else 1-factor)
        expected = (expected/Decimal('.1')).to_integral_value(
            rounding=ROUND_CEILING if quantity > 0 else ROUND_FLOOR)*Decimal('.1')
        if price != expected:
            raise ValueError(f'{name}: saved fill price is not the registered adverse-slippage scenario')
    if plan['candidate'] == 'cash' and fills:
        raise ValueError(f'{name}: cash control cannot contain executed trades')
    if not isinstance(daily, list) or not daily:
        raise ValueError(f'{name}: saved daily marks are missing')
    if metrics.get('fills') != len(fills):
        raise ValueError(f'{name}: metrics.fills disagrees with the saved fills')
    if metrics.get('orders') != len(orders):
        raise ValueError(f'{name}: metrics.orders disagrees with the saved orders')
    rejected = sum(1 for order in orders if isinstance(order, dict)
                   and str(order.get('status', '')).startswith('REJECTED'))
    if metrics.get('rejected_orders') != rejected:
        raise ValueError(f'{name}: metrics.rejected_orders disagrees with the saved orders')
    if metrics.get('annual_pnl') != driver._annual_pnl(daily):
        raise ValueError(f'{name}: metrics.annual_pnl disagrees with the saved daily marks')
    if metrics.get('max_drawdown') != replay.get('minute_max_drawdown'):
        raise ValueError(f'{name}: metrics.max_drawdown disagrees with the saved replay')
    if metrics.get('average_exposure') != replay.get('average_exposure'):
        raise ValueError(f'{name}: metrics.average_exposure disagrees with the saved replay')
    if metrics.get('halted_at') != replay.get('halted_at'):
        raise ValueError(f'{name}: metrics.halted_at disagrees with the saved replay')
    documented = artifact.get('decisions')
    if not isinstance(documented, list) or len(documented) != len(decisions):
        raise ValueError(f'{name}: documented decisions disagree with the saved replay decisions')
    if metrics.get('decision_count') != len(decisions):
        raise ValueError(f'{name}: metrics.decision_count disagrees with the saved decisions')
    for index, (record, receipt) in enumerate(zip(documented, decisions)):
        if not isinstance(record, dict) or not isinstance(receipt, dict):
            raise ValueError(f'{name}: decision receipt {index} is malformed')
        if record.get('time') != receipt.get('time') or record.get('actual_quantity') != receipt.get('actual_quantity'):
            raise ValueError(f'{name}: decision receipt {index} disagrees with the saved replay')
        target = receipt.get('target')
        if target is None or record.get('replay_target') != repr(float(target)):
            raise ValueError(f'{name}: decision receipt {index} target disagrees with the saved replay')
    elapsed = _finite_elapsed(metrics.get('elapsed_seconds'), name)
    return driver._summary_entry(plan, artifact), 'PASSED', elapsed


def _verify_failed_case(plan, artifact, name: str):
    if artifact.get('computation') != FAILED_COMPUTATION:
        raise ValueError(f'{name}: failed case does not declare an incomplete computation')
    error = artifact.get('error')
    if not isinstance(error, str) or not error:
        raise ValueError(f'{name}: failed case lacks an error receipt')
    elapsed = _finite_elapsed(artifact.get('elapsed_seconds'), name)
    entry = driver._summary_entry(plan, artifact)
    if entry.get('status') != driver.CASE_STATUS_FAILED:
        raise ValueError(f'{name}: failed case is not retained as a failed entry')
    return entry, 'FAILED', elapsed


def _verify_case(plan, artifact, identity, name: str):
    if not isinstance(artifact, dict):
        raise ValueError(f'{name}: case artifact is not an object')
    _verify_case_identity(plan, artifact, identity, name)
    status = artifact.get('status')
    if status == driver.CASE_STATUS_FAILED:
        return _verify_failed_case(plan, artifact, name)
    if status != driver.CASE_STATUS_OK:
        raise ValueError(f'{name}: unsupported case status {status!r}')
    return _verify_success_case(plan, artifact, name)


def _ledger_trial_ids(path: Path):
    uri = path.resolve().as_uri() + '?mode=ro'
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=30)
    except sqlite3.Error as exc:
        raise ValueError(f'trial ledger unavailable: {exc}') from exc
    try:
        connection.execute('PRAGMA query_only=ON')
        rows = connection.execute('SELECT DISTINCT trial_id FROM trial_events ORDER BY trial_id').fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f'trial ledger unreadable: {exc}') from exc
    finally:
        connection.close()
    return [row[0] for row in rows]


def _verify_ledger(base: Path, plans, expected_status: dict) -> dict:
    path = base / 'trials.sqlite'
    expected_ids = [plan['trial_id'] for plan in plans]
    if sorted(_ledger_trial_ids(path)) != sorted(expected_ids):
        raise ValueError('trial ledger trials differ from the pre-registered twelve-case family')
    ledger = None
    trial_status = {}
    try:
        try:
            ledger = TrialLedger(str(path), create=False, read_only=True)
            if ledger.verify() is not True:
                raise ValueError('trial ledger hash chain did not verify')
            planned_sequences, running_sequences = [], []
            for plan in plans:
                aggregate = ledger.load(plan['trial_id'])
                statuses = tuple(event.status for event in aggregate.events)
                if (len(aggregate.events) != 3 or statuses[:2] != LEDGER_TRIAL_STATES
                        or statuses[2] not in LEDGER_TERMINAL_STATES):
                    raise ValueError(f'trial {plan["trial_id"]}: unexpected event states {statuses}')
                if statuses[2] != expected_status[plan['trial_id']]:
                    raise ValueError(f'trial {plan["trial_id"]}: ledger status disagrees with its case artifact')
                record = plan['record']
                for attribute in ('alpha_id', 'family_id', 'exact_definition_id', 'validation_policy_digest'):
                    if getattr(aggregate, attribute) != record[attribute]:
                        raise ValueError(f'trial {plan["trial_id"]}: ledger identity drift')
                planned_sequences.append(aggregate.events[0].global_seq)
                running_sequences.append(aggregate.events[1].global_seq)
                trial_status[plan['trial_id']] = statuses[2]
        except TrialLedgerError as exc:
            raise ValueError(f'trial ledger invalid: {exc}') from exc
    finally:
        if ledger is not None:
            ledger.close()
    if not planned_sequences or max(planned_sequences) >= min(running_sequences):
        raise ValueError('trial ledger registration did not commit before any replay')
    return {'verified': True, 'trial_count': len(plans), 'event_count': 3 * len(plans),
            'registration_committed_first': True, 'trial_status': trial_status}


def _verify_unchanged(base: Path, before_entries: set, consumed: dict) -> None:
    for name, (digest, _size) in consumed.items():
        try:
            after = _sha256(_consume(base, name, {}))
        except OSError as exc:
            raise ValueError(f'evidence file disappeared while being read: {name}') from exc
        if after != digest:
            raise ValueError(f'evidence file changed while being read: {name}')
    if set(os.listdir(base)) != before_entries:
        raise ValueError('evidence directory changed while being read')


def inspect_rf2(root) -> dict:
    """Verify the saved receipts of an RF2 bounded run and return hashes of consumed files."""
    base = _validated_root(root)
    consumed = {}
    before_entries = _listing(base)
    summary = _load_json(base, 'summary.json', consumed)
    identity = _load_json(base, 'identity.json', consumed)
    registration = _load_json(base, 'registration.json', consumed)
    _verify_frozen_identity(identity, registration, summary)
    _verify_summary_header(summary)
    plans = driver.planned_trials(identity)
    _verify_planned(plans, summary)
    cases, expected_status = {}, {}
    elapsed_sum = 0.0
    for plan in plans:
        key = driver.case_key(plan['candidate'], plan['slip_bps'])
        name = driver.case_filename(plan['candidate'], plan['slip_bps'])
        artifact = _load_json(base, name, consumed)
        entry, terminal, elapsed = _verify_case(plan, artifact, identity, name)
        cases[key] = entry
        expected_status[plan['trial_id']] = terminal
        elapsed_sum += elapsed
    _consume(base, 'trials.sqlite', consumed)
    ledger = _verify_ledger(base, plans, expected_status)
    if not _same(summary.get('cases'), cases):
        raise ValueError('summary case entries do not match the saved case artifacts')
    screening = driver._screen(cases)
    if not _same(summary.get('screening'), screening):
        raise ValueError('summary screening does not match the recomputed pre-registered screening')
    if len(cases) != driver.EXPECTED_CASE_COUNT:
        raise ValueError('saved case artifacts are not exactly twelve')
    completed = sum(1 for entry in cases.values() if entry.get('status') == driver.CASE_STATUS_OK)
    if summary.get('completed_cases') != completed:
        raise ValueError('summary completed case count disagrees with the saved artifacts')
    if summary.get('failed_cases') != driver.EXPECTED_CASE_COUNT - completed:
        raise ValueError('summary failed case count disagrees with the saved artifacts')
    _verify_unchanged(base, before_entries, consumed)
    return {
        'schema': EVIDENCE_SCHEMA,
        'status': EVIDENCE_STATUS,
        'evidence_class': driver.EVIDENCE_CLASS,
        'history_class': driver.HISTORY_CLASS,
        'authority': False,
        'authority_admitted': False,
        'authoritative': False,
        'live_eligible': False,
        'paper_admitted': False,
        'economic_eligible': False,
        'root': str(base),
        'identity': identity,
        'planned_trials': driver.EXPECTED_CASE_COUNT,
        'planned_trial_ids': [plan['trial_id'] for plan in plans],
        'cases': cases,
        'completed_cases': completed,
        'failed_cases': driver.EXPECTED_CASE_COUNT - completed,
        'screening': screening,
        'elapsed_case_sum_seconds': elapsed_sum,
        'trial_ledger': ledger,
        'evidence_files': [
            {'file': name, 'path': str(base / name), 'sha256': digest, 'bytes': size}
            for name, (digest, size) in sorted(consumed.items())
        ],
        'limitations': LIMITATIONS,
    }


__all__ = ['inspect_rf2', 'EVIDENCE_SCHEMA', 'EVIDENCE_STATUS', 'LIMITATIONS']
