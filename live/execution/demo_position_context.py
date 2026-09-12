"""Rebuild completed executor-owned receipt prefixes in the original V2 ledger.

No caller-selected source, observed-average basis, cash authority or order action.
This bounded prefix contains reported fills only. Any continuation must compare
its independently observed wallet and exact position with this prefix; unknown
funding/transfers cannot be repaired by replacing the original initial equity.
"""
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path
import stat
import math

from live.execution import demo_authority
from live.execution.demo_batch_risk import _digest
from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context
from services.research.demo_cash_chain import _read, _interval
from services.research.demo_execution_journal import (
    DemoExecutionJournal, replay_report, replay_ledger, reported_fill_events, apply_reported_events,
)
from services.research.demo_account_baseline import build_baseline
from services.research.demo_execution_origin import assess, account_capture, ACCOUNT_TERMS_SCHEMA

_ROOT = Path('/var/lib/quantos-demo-executor')
_POLICY = 'REPORTED_FILLS_ONLY_CONTINUATION_WALLET_MUST_MATCH_V1'


class PositionContextRejected(ValueError):
    pass


@ledger_decimal_context()
def continuation_risk(risk_inputs, context):
    """Derive a conservative turnover floor from already reported fills."""
    if context is None:
        return risk_inputs
    equity = Decimal(risk_inputs['account']['equity'])
    _require(equity.is_finite() and equity > 0, 'POSITION_CONTEXT_EQUITY')
    consumed = sum((Decimal(v) for v in context['capacity_consumed'].values()), Decimal(0))
    floor = Decimal(str(context['initial_plan_turnover'])) + consumed/equity
    value = float(max(Decimal(str(risk_inputs.get('plan_turnover', 0.0))), floor))
    _require(math.isfinite(value), 'POSITION_CONTEXT_TURNOVER')
    if Decimal(str(value)) < floor:
        value = math.nextafter(value, math.inf)
    return dict(risk_inputs, plan_turnover=value)


@ledger_decimal_context()
def validate_origin(origin, context, *, as_of_ms):
    """Check a later observed held book; no source or approval is inferred."""
    report = assess(origin, as_of_ms=as_of_ms)
    _require(origin['schema'] == ACCOUNT_TERMS_SCHEMA and
        context['schema'] == 'DEMO_CANONICAL_POSITION_CONTEXT_V1' and
        context['account_ref_sha256'] == origin['account_ref_sha256'] and context['symbol'] == origin['symbol'] and
        context['available_at_ms'] < origin['observed_at_ms'] and
        context['day_id'] == origin['observed_at_ms']//86400000 and
        context['cash_flow_policy'] == _POLICY and context['source_verified'] is False,
        'POSITION_CONTEXT_ORIGIN_BINDING')
    _require(set(report['issues']) <= {'QUOTE_BOOK_NOT_FLAT', 'QUOTE_WALLET_NOT_FREE_AND_FLAT'},
             'POSITION_CONTEXT_ORIGIN_UNRESOLVED')
    baseline = build_baseline(account_capture(origin), as_of_ms=as_of_ms)
    positions = [p for p in baseline['positions'] if p['margin_asset'] == origin['symbol'][3:]]
    held = Decimal(context['quantity']) != 0
    _require(len(positions) == (1 if held else 0), 'POSITION_CONTEXT_SINGLE_OWNED_POSITION')
    if held:
        owned = positions[0]
        _require(owned['symbol'] == context['symbol'] and owned['position_side'] == 'BOTH' and
            owned['margin_mode'] == 'CROSSED' and Decimal(owned['isolated_wallet']) == 0 and
            Decimal(owned['quantity']) == Decimal(context['quantity']), 'POSITION_CONTEXT_POSITION_DIFFERENCE')
    wallet = next(a for a in baseline['assets'] if a['asset'] == origin['symbol'][3:])
    _require(Decimal(wallet['observed_wallet']) == Decimal(wallet['observed_cross_wallet']) == Decimal(context['canonical_wallet']),
             'POSITION_CONTEXT_UNEXPLAINED_CASH_CHANGE')
    _require(Decimal(wallet['account_identity_residual']) == 0, 'POSITION_CONTEXT_ACCOUNT_IDENTITY')
    return {'positions': [{'symbol': owned['symbol'], 'quantity': owned['quantity'], 'mark_price': owned['observed_mark_price']}] if held else [],
        'cash': wallet['observed_available'], 'equity': wallet['observed_margin_balance'],
        'day_pnl': canonical_decimal(Decimal(context['initial_day_pnl'])+
            Decimal(wallet['observed_margin_balance'])-Decimal(context['initial_equity']))}


def _require(condition, reason):
    if not condition:
        raise PositionContextRejected(reason)


def _custody(path, *, directory):
    uid = demo_authority.load_registry_for_executor()['executor_uid']
    for ancestor in reversed(_ROOT.parents):
        demo_authority._require_protected(ancestor.lstat(), directory=True)
    _require(path == _ROOT or _ROOT in path.parents, 'POSITION_CONTEXT_PATH')
    info = _ROOT.lstat()
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and not info.st_mode & 0o022,
             'POSITION_CONTEXT_ROOT_CUSTODY')
    relative = path.relative_to(_ROOT)
    current = _ROOT
    for part in relative.parts:
        current = current/part
        info = current.lstat()
        is_directory = current != path or directory
        _require(info.st_uid == uid and not info.st_mode & 0o022 and
            (stat.S_ISDIR(info.st_mode) if is_directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1),
            'POSITION_CONTEXT_CUSTODY')


def _records(directory):
    # Shared bounded hash-file reader; imported at call time to preserve the
    # existing executor/preparation initialization order.
    from live.execution.demo_resident_recovery import _records as read_records
    _custody(directory, directory=True)
    records = read_records(directory, count_limit=1000, byte_limit=64*1024*1024)
    for path, _ in records:
        _custody(path, directory=False)
    return {path.stem: value for path, value in records}


@ledger_decimal_context()
def load_context(state, *, symbol, account_ref, as_of_ms):
    try:
        _require(type(as_of_ms) is int and as_of_ms > 0, 'POSITION_CONTEXT_TIME')
        reservations = state['reservations']
        _require(1 <= len(reservations) <= 32, 'POSITION_CONTEXT_BATCH_BUDGET')
        _require(all(r['state'] == 'RESOLVED' and r['recorded_at_ms'] <= as_of_ms for r in state['recovery']),
                 'POSITION_CONTEXT_RECOVERY_UNRESOLVED')
        _require(all(o['status'] in ('FILLED', 'CANCELED', 'REJECTED', 'EXPIRED') for o in state['oms']),
                 'POSITION_CONTEXT_OMS_UNRESOLVED')
        area = _ROOT/'receipt-reconciliation'
        proofs = _records(_ROOT/'evidence')
        plans = _records(area/'plans')
        outcomes = _records(area/'outcomes')
        imports = _records(area/'receipts/imports')
        completed = {v['plan_sha256'] for v in outcomes.values()
            if v.get('schema') == 'DEMO_OMS_RECEIPT_RESOLUTION_V1' and v.get('status') == 'OMS_TERMINAL_PROJECTED'}
        models = {o['client_order_id']: o for o in state['oms']}
        recovered = {r['client_order_id']: r for r in state['recovery']}
        with ExitStack() as stack:
            bs = []; sources = []; original_proofs = []; available = 0; total_records = 0; file_facts = {}; total_bytes = 0
            for reservation in reservations:
                body = reservation['payload']; context = body['context']; fixed = body['orders']
                _require(context['account_ref_sha256'] == account_ref and all(o['symbol'] == symbol for o in fixed),
                         'POSITION_CONTEXT_ACCOUNT_SCOPE')
                candidates = [(key, p) for key, p in plans.items() if key in completed and
                    p.get('reservation_sha256') == reservation['record_hash'] and
                    p.get('status') == 'READY_FOR_OMS_PROJECTION' and not p.get('issues')]
                _require(candidates, 'POSITION_CONTEXT_COMPLETION_REQUIRED')
                latest = max(p['resolution_at_ms'] for _, p in candidates)
                candidates = [(key, p) for key, p in candidates if p['resolution_at_ms'] == latest]
                _require(len(candidates) == 1, 'POSITION_CONTEXT_COMPLETION_AMBIGUOUS')
                plan_hash, plan = candidates[0]
                proof = proofs[plan['proposal_sha256']]
                _require(proof['context'] == context and proof['orders'] == fixed,
                         'POSITION_CONTEXT_ORIGINAL_PROPOSAL')
                imported = imports[plan['import_sha256']]
                _require(imported['status'] == 'IMPORTED_TECHNICAL_RECEIPTS' and
                    imported['reservation_sha256'] == reservation['record_hash'] and
                    imported['proposal_sha256'] == plan['proposal_sha256'], 'POSITION_CONTEXT_IMPORT_BINDING')
                path = area/'receipts/journals'/(plan['manifest_hash']+'.sqlite3')
                _require(str(path) == imported['journal_path'] == plan['journal_path'], 'POSITION_CONTEXT_JOURNAL_PATH')
                _custody(path, directory=False)
                for suffix in ('', '-wal', '-shm', '-journal'):
                    member = Path(str(path)+suffix)
                    if member.exists() or member.is_symlink():
                        _custody(member, directory=False)
                        info = member.lstat()
                        file_facts[member] = tuple(getattr(info,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns'))
                        total_bytes += info.st_size
                        _require(total_bytes <= 64*1024*1024, 'POSITION_CONTEXT_JOURNAL_BYTE_BUDGET')
                journal = stack.enter_context(DemoExecutionJournal(path, read_only=True))
                _require(replay_report(journal) == imported['replay'], 'POSITION_CONTEXT_REPLAY_CHANGED')
                batch = _read(journal); manifest = batch['manifest']
                total_records += len(batch['records'])
                _require(total_records <= 10000, 'POSITION_CONTEXT_RECEIPT_BUDGET')
                _require(journal.manifest_hash == plan['manifest_hash'] and batch['tail'] == plan['journal_tail'],
                         'POSITION_CONTEXT_RECEIPT_IDENTITY')
                _require(manifest['native']['account_ref_sha256'] == account_ref and batch['parsed'][0].instrument_id == symbol,
                         'POSITION_CONTEXT_JOURNAL_SCOPE')
                bindings = {b['client_order_id']: b for b in manifest['native']['bindings']}
                resolutions = {o['client_order_id']: o for o in plan['orders']}
                _require(set(bindings) == set(resolutions) == {o['newClientOrderId'] for o in fixed},
                         'POSITION_CONTEXT_ORDER_SET')
                for client, binding in bindings.items():
                    resolution = resolutions[client]; model = models[client]; recovery = recovered[client]
                    _require(model['status'] == recovery['observed_status'] == resolution['terminal_status'] and
                        model['venue_order_id'] == recovery['exchange_order_id'] == binding['venue_order_id'] == resolution['venue_order_id'],
                        'POSITION_CONTEXT_ORDER_RECOVERY')
                    queries = [w for w in state['wire'] if w['kind'] == 'QUERY_RESULT' and
                        w['reservation_sha256'] == reservation['record_hash'] and w['client_order_id'] == client]
                    _require(queries and queries[-1]['record_hash'] == resolution['query_record_sha256'],
                             'POSITION_CONTEXT_QUERY_CHANGED')
                    _require(Decimal(queries[-1]['payload']['outcome']['order']['executedQty']) == Decimal(resolution['filled_quantity']),
                             'POSITION_CONTEXT_QUERY_QUANTITY')
                available = max(available, plan['resolution_at_ms'], manifest['native']['coverage']['end_ms'],
                    batch['clock']['available_at_ms'] if batch['clock'] else 0,
                    max(r['received_at_ms'] for r in batch['records']))
                _require(available <= as_of_ms, 'POSITION_CONTEXT_INPUT_FROM_FUTURE')
                bs.append(batch)
                original_proofs.append(proof)
                sources.append({'reservation_sha256': reservation['record_hash'], 'plan_sha256': plan_hash,
                    'manifest_hash': journal.manifest_hash, 'journal_tail': batch['tail']})
            first = bs[0]; original = first['manifest']
            _require(Decimal(original['initial_position']) == 0 and
                (first['opening'] is None or first['opening']['positions_count'] == 0), 'POSITION_CONTEXT_FIRST_FLAT')
            capacities = [{k:v for k,v in b['parsed'][3].as_canonical_dict().items() if k != 'window_id'} for b in bs]
            _require(all(c == capacities[0] for c in capacities), 'POSITION_CONTEXT_CAPACITY_CHANGED')
            _require(all(b['manifest']['contract'] == original['contract'] for b in bs), 'POSITION_CONTEXT_CONTRACT_CHANGED')
            _require(len({b['clock'] is None for b in bs}) == 1, 'POSITION_CONTEXT_MIXED_CLOCKS')
            for field in ('order_id', 'client_order_id', 'venue_order_id'):
                ids = [o[field] for b in bs for o in b['manifest']['native']['bindings']]
                _require(len(ids) == len(set(ids)), 'POSITION_CONTEXT_REUSED_ORDER_ID')
            ledger = replay_ledger(original, first['parsed'], {'trade_rows': []}, cash_events=[])
            last_event = None; previous_available = None
            for i, b in enumerate(bs):
                if i:
                    opening = b['opening']; m = b['manifest']; before = ledger.state_as_canonical_dict()
                    _require(opening is not None, 'POSITION_CONTEXT_CONTINUATION_REQUIRED')
                    start = m['opening']['capture']['observed_at_ms']
                    _require(previous_available < start, 'POSITION_CONTEXT_CONTINUATION_TIME')
                    _require(Decimal(m['initial_collateral']) == Decimal(before['wallet_balance']), 'POSITION_CONTEXT_WALLET_CHANGED')
                    actual = {k:v for k,v in before['positions'].items() if Decimal(v['quantity']) != 0}
                    declared = {p['symbol']:p for p in opening['positions']}
                    _require(set(actual) == set(declared) and all(
                        Decimal(v['quantity']) == Decimal(declared[k]['quantity']) and
                        Decimal(v['entry_notional']) == Decimal(declared[k]['entry_notional']) and
                        declared[k]['basis_source_sha256'] == _digest(before) for k,v in actual.items()),
                        'POSITION_CONTEXT_BASIS_CHANGED')
                fills, reductions = reported_fill_events(b['manifest'], b['parsed'], b['evidence'])
                fills = sorted(fills, key=lambda event: event.timestamp)
                for event in fills:
                    _require(last_event is None or event.timestamp >= last_event, 'POSITION_CONTEXT_EVENT_ORDER')
                    lower, upper = _interval(event.timestamp, b['clock'])
                    _require(b['opening'] is None or lower > b['opening']['opening_at_ms'], 'POSITION_CONTEXT_OPENING_CLOCK')
                    _require(upper <= as_of_ms, 'POSITION_CONTEXT_EVENT_FROM_FUTURE')
                    last_event = event.timestamp
                apply_reported_events(ledger, fills, reductions)
                previous_available = max(b['manifest']['native']['coverage']['end_ms'],
                    b['clock']['available_at_ms'] if b['clock'] else 0, max(r['received_at_ms'] for r in b['records']))
            _require(all(b['journal'].read()[1] == b['tail'] for b in bs), 'POSITION_CONTEXT_RECEIPTS_CHANGED')
            for path, before in file_facts.items():
                _custody(path, directory=False)
                after = path.lstat()
                _require(before == tuple(getattr(after,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')),
                         'POSITION_CONTEXT_JOURNAL_CHANGED')
            final = ledger.state_as_canonical_dict(); owned = ledger.position(symbol)
            return {'schema': 'DEMO_CANONICAL_POSITION_CONTEXT_V1', 'symbol': symbol,
                'account_ref_sha256': account_ref, 'quantity': canonical_decimal(owned.quantity if owned else Decimal(0)),
                'entry_notional': final['positions'][symbol]['entry_notional'] if owned else '0',
                'canonical_wallet': final['wallet_balance'], 'initial_equity': original['initial_collateral'],
                'initial_day_pnl': original_proofs[0]['risk']['input_snapshot']['account']['day_pnl'],
                'initial_plan_turnover': original_proofs[0]['risk']['input_snapshot']['context']['plan_turnover'],
                'peak_equity_floor': canonical_decimal(max(Decimal(p['risk']['input_snapshot']['account']['peak_equity']) for p in original_proofs)),
                'day_id': original['native']['coverage']['start_ms']//86400000,
                'ledger_state_hash': _digest(final), 'available_at_ms': available, 'sources': sources,
                'contract': original['contract'], 'adv_notional': original['adv_notional'],
                'participation_rate': original['participation_rate'], 'capacity_consumed': final['capacity_consumed'],
                'cash_flow_policy': _POLICY, 'cash_reconciled': False, 'source_verified': False,
                'submission_enabled': False, 'economic_authority': False}
    except PositionContextRejected:
        raise
    except Exception:
        raise PositionContextRejected('POSITION_CONTEXT_UNAVAILABLE') from None
