"""Reconcile consecutive Demo batches in ONE canonical account ledger.

Bounded, retrospective, imported observations only. Later opening captures are
checkpoints, never fresh collateral or a replacement cost basis. This does not
admit a proposal, authenticate a source, prove coverage, or modify an input.
"""
from decimal import Decimal
import hashlib
from pathlib import Path

from quantos.contracts.binance_demo_clock import event_local_interval
from services.execution.perpetual_ledger_v2 import (
    ReportedCashFlowEvent, canonical_decimal, ledger_decimal_context,
)
from services.research.ai_factory_store import digest
from services.research.demo_cash_window import assess, _sweep
from services.research.demo_execution_journal import (
    observations, prepare, replay_ledger, reported_fill_events, apply_reported_events,
    REPLAY_SOURCE_SHA256,
)
from services.research.demo_opening_projection import prepare_opening
from services.research.demo_income_time import association, POLICY as INCOME_TIME_POLICY

SCHEMA = 'DEMO_CANONICAL_CASH_CHAIN_V1'
MAX_BATCHES = 32
SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class ChainUnresolved(ValueError):
    pass


def _require(condition, issue):
    if not condition:
        raise ChainUnresolved(issue)


def _interval(at, clock):
    return event_local_interval(at, clock) if clock else (at, at)


def _read(journal):
    # Revalidate frozen content instead of trusting a mutable cached parse.
    manifest = journal.manifest
    _require(digest(manifest) == journal.manifest_hash, 'MANIFEST_IDENTITY_CHANGED')
    parsed = prepare(manifest)
    records, tail, _ = journal.read()
    evidence = observations(manifest, records, parsed)
    native = evidence['native_observations']
    spec = parsed[0]
    specs, seed, opening = prepare_opening(manifest, spec)
    _require(len(specs) == 1, 'SINGLE_SYMBOL_QUOTE_BOOK_REQUIRED')
    _require(opening is None or opening['status'] == 'DECLARED_INPUTS_READY', 'OPENING_INPUTS_INCOMPLETE')
    _require(records and not native['coverage']['gaps'], 'RECEIPT_COVERAGE_UNRESOLVED')
    _require(all(o['events'] and o['events'][-1]['state'] in ('FILLED', 'CANCELLED', 'EXPIRED')
                 for o in evidence['observed']['orders']), 'ORDERS_NOT_TERMINAL')
    _require(all(t['reported_commission'] is not None and t['commission_asset'] == spec.margin_asset
                 and Decimal(t['reported_commission']) >= 0 for t in native['trade_rows']), 'COMMISSION_NOT_SUPPORTED')
    return dict(journal=journal, manifest=manifest, parsed=parsed, evidence=native,
                records=records, tail=tail, opening=opening, seed=seed, clock=native.get('clock'))


def _cash_interval(at, batches):
    # A cash row may lie in two overlapping receipt windows. Every compatible
    # bracket contributes its uncertainty; do not cherry-pick the narrowest one.
    intervals = []
    for b in batches:
        lower, upper = _interval(at, b['clock'])
        w = b['manifest']['native']['coverage']
        if w['start_ms'] <= lower <= upper <= w['end_ms']:
            intervals.append((lower, upper))
    _require(intervals, 'CASH_CLOCK_WINDOW_UNRESOLVED')
    return min(i[0] for i in intervals), max(i[1] for i in intervals)


@ledger_decimal_context()
def project_cash_chain(journals, capture, *, account_prefix=None):
    """Check 1–32 same-day batch journals against one complete cash window.

    Each later batch needs an observed opening with exact basis tied to the
    original ledger prefix hash. Its reported wallet/quantity/basis must match
    that prefix. The account observation interval must contain no possible
    reported fill or cash event. No latency or atomic venue snapshot is assumed.
    """
    _require(isinstance(journals, (tuple, list)) and 1 <= len(journals) <= MAX_BATCHES, 'BOUNDED_BATCH_CHAIN_REQUIRED')
    cash = assess(capture)
    result = dict(schema=SCHEMA, environment='BINANCE_USDM_DEMO', scope='TECHNICAL_VALIDATION_ONLY',
        status='UNRESOLVED', account_ref_sha256=capture['account_ref_sha256'], symbol=None, quote_asset=None,
        observed_at_ms=capture['completed_at_ms'], cash_capture_sha256=digest(capture),
        replay_source_sha256=REPLAY_SOURCE_SHA256, projection_source_sha256=SOURCE_SHA256,
        cash_input_status=cash['status'], cash_input_duplicates=cash['duplicate_rows'],
        batches=[], continuations=[], issues=[], canonical_wallet=None, reported_wallet=None, wallet_residual=None,
        reconciliation=None, positions=None, ledger_state_hash=None, capacity_consumed=None,
        fee_comparison='UNKNOWN', realized_pnl_comparison='UNKNOWN',
        source_verified=False, coverage_verified=False, authoritative=False, submission_enabled=False, strategy_pnl=None,
        limitations=['IMPORTED_SOURCE_UNVERIFIED','DECLARED_STREAM_COVERAGE_ONLY','NONATOMIC_WALLET_OBSERVATIONS',
            'FIRST_FLAT_SINGLE_SYMBOL_QUOTE_START_ONLY','SAME_DAY_BOUNDED_CASH_WINDOW_ONLY',
            'VALUATION_MARKS_ARE_INPUTS_NOT_TERMINAL_VENUE_MARKS','RETROSPECTIVE_CONTINUITY_NOT_EXECUTION_AUTHORITY'])
    try:
        bs = [_read(j) for j in journals]
        first = bs[0]; fm = first['manifest']; spec = first['parsed'][0]
        quote = spec.margin_asset; symbol = spec.instrument_id
        result.update(symbol=symbol, quote_asset=quote)
        result['batches'] = [dict(manifest_hash=b['journal'].manifest_hash, journal_tail=b['tail'],
            source_commit=b['manifest']['source_commit'], orders_count=len(b['manifest']['actions']),
            trades_count=b['evidence']['trades_count'], coverage=b['manifest']['native']['coverage'],
            clock=b['clock']) for b in bs]
        result['observed_at_ms'] = max(result['observed_at_ms'], *(max(b['manifest']['native']['coverage']['end_ms'],
            b['clock']['available_at_ms'] if b['clock'] else max(r['received_at_ms'] for r in b['records'])) for b in bs))
        _require(not cash['issues'], 'CASH_' + (cash['issues'][0] if cash['issues'] else 'UNRESOLVED'))
        _require(Decimal(fm['initial_position']) == 0 and (first['opening'] is None or
                 first['opening']['positions_count'] == 0), 'FIRST_FLAT_OPENING_REQUIRED')
        _require(all(b['manifest']['native']['account_ref_sha256'] == capture['account_ref_sha256'] for b in bs), 'ACCOUNT_NAMESPACE_MISMATCH')
        _require(all(b['manifest']['contract'] == fm['contract'] for b in bs), 'CONTRACT_CHANGED_IN_CHAIN')
        _require(len({b['clock'] is None for b in bs}) == 1, 'MIXED_CLOCK_VERSIONS_UNRESOLVED')
        capacities = [{k:v for k,v in b['parsed'][3].as_canonical_dict().items() if k != 'window_id'} for b in bs]
        _require(all(c == capacities[0] for c in capacities), 'CAPACITY_CHANGED_IN_CHAIN')
        for field in ('order_id', 'client_order_id', 'venue_order_id'):
            ids = [o[field] for b in bs for o in b['manifest']['native']['bindings']]
            _require(len(ids) == len(set(ids)), 'ORDER_IDENTITY_REUSED_IN_CHAIN')
        _require(sum(len(b['records']) for b in bs) <= 10000, 'CHAIN_RECEIPT_BUDGET')
        bounds = cash['bounds']; _require(bounds is not None, 'CASH_BOUNDS_MISSING')
        required_start = max(bounds['query_start_ms'], first['opening']['opening_at_ms']+1
                             if first['opening'] else bounds['query_start_ms'])
        first_window = fm['native']['coverage']; covered = first_window['end_ms']
        _require(first_window['start_ms'] <= required_start, 'CHAIN_DOES_NOT_COVER_CASH')
        previous_start = first_window['start_ms']
        for b in bs[1:]:
            w = b['manifest']['native']['coverage']
            _require(previous_start < w['start_ms'] <= covered+1 and w['end_ms'] > covered, 'RECEIPT_CHAIN_GAP_OR_ORDER')
            previous_start, covered = w['start_ms'], w['end_ms']
            _require(b['opening'] is not None, 'CONTINUATION_OBSERVATION_REQUIRED')
        _require(covered >= bounds['query_end_ms'], 'CHAIN_DOES_NOT_COVER_CASH')
        wallet = next((a for a in cash['assets'] if a['asset'] == quote), None)
        _require(wallet is not None and wallet['opening_wallet'] is not None and wallet['closing_wallet'] is not None, 'QUOTE_WALLET_MISSING')
        result['reported_wallet'] = wallet['closing_wallet']
        _require(Decimal(wallet['opening_wallet']) == Decimal(fm['initial_collateral']), 'OPENING_WALLET_MISMATCH')
        trades = [t for b in bs for t in b['evidence']['trade_rows']]
        by_trade = {t['trade_id']: t for t in trades}
        _require(len(by_trade) == len(trades), 'TRADE_IDENTITY_REUSED_IN_CHAIN')
        flows = [r for r in _sweep([r for r in capture['reads'] if r['kind'] == 'income' and r['sweep'] == 2])[0].values() if r['asset'] == quote]
        _require(all(r['incomeType'] in ('COMMISSION', 'REALIZED_PNL', 'FUNDING_FEE', 'TRANSFER') for r in flows), 'UNSUPPORTED_INCOME_TYPE')
        _require(all(r['incomeType'] == 'TRANSFER' or r['symbol'] == symbol for r in flows), 'UNBOUND_SAME_QUOTE_SYMBOL')
        fees = [r for r in flows if r['incomeType'] == 'COMMISSION']
        _require(len({r['tradeId'] for r in fees}) == len(fees) and {r['tradeId'] for r in fees} == set(by_trade), 'COMMISSION_TRADE_SCOPE_MISMATCH')
        _require(all(Decimal(r['income']) == -Decimal(by_trade[r['tradeId']]['reported_commission']) for r in fees), 'REPORTED_COMMISSION_DIFFERENCE')
        pnl = [r for r in flows if r['incomeType'] == 'REALIZED_PNL']
        _require(len({r['tradeId'] for r in pnl}) == len(pnl) and all(r['tradeId'] in by_trade for r in pnl), 'REALIZED_PNL_TRADE_SCOPE_MISMATCH')
        # This also checks receipt/cash duplicate identity clocks, not only money.
        _require(all(association(r['time'],by_trade[r['tradeId']]['venue_trade_ms']) is not None for r in fees+pnl), 'INCOME_TRADE_TIME_MISMATCH')
        result['income_time_policy']=INCOME_TIME_POLICY
        events, intervals, reduce_only = [], [], set()
        for b in bs:
            _require(all(bounds['inner_start_ms'] < a['timestamp'] < bounds['inner_end_ms'] for a in b['manifest']['actions']), 'ORDER_OUTSIDE_CASH_INTERIOR')
            fills, reductions = reported_fill_events(b['manifest'], b['parsed'], b['evidence'])
            events.extend(fills); reduce_only.update(reductions)
            intervals.extend((e.timestamp, *_interval(e.timestamp, b['clock'])) for e in fills)
        for r in flows:
            low, high = _cash_interval(r['time'], bs)
            intervals.append((r['time'], low, high))
            if r['incomeType'] in ('FUNDING_FEE', 'TRANSFER'):
                events.append(ReportedCashFlowEvent(timestamp=r['time'], account_ref_sha256=capture['account_ref_sha256'],
                    asset=quote, income_type=r['incomeType'], transaction_id=r['tranId'], amount=Decimal(r['income']),
                    instrument_id=symbol if r['incomeType'] == 'FUNDING_FEE' else None, source_sha256=digest(capture)))
        _require(all(bounds['inner_start_ms'] < low <= high < bounds['inner_end_ms'] for _,low,high in intervals), 'EVENT_OUTSIDE_CASH_INTERIOR')
        if first['opening'] is not None:
            origin_end = first['opening']['opening_at_ms']
            _require(all(low > origin_end for _,low,_ in intervals), 'EVENT_BEFORE_OR_OVERLAPPING_FIRST_OPENING')
        ordered = sorted(events, key=lambda e:e.timestamp)
        # Create once from the original account origin, then retain all state.
        ledger = replay_ledger(fm, first['parsed'], {'trade_rows':[]}, cash_events=[])
        prior_fee = prior_funding = prior_cash = Decimal(0)
        if account_prefix is not None:
            from services.research.demo_account_recovery import reconcile as replay_account
            from services.research.demo_origin_contract import contract_from_origin
            prefix_report, original_ledger = replay_account(**account_prefix)
            _require(prefix_report['status'] == 'REPORTED_ACCOUNT_MATCHED' and original_ledger is not None,
                     'ACCOUNT_PREFIX_UNRESOLVED')
            _require(prefix_report['account_ref_sha256'] == capture['account_ref_sha256'] and
                prefix_report['symbol'] == symbol and contract_from_origin(account_prefix['opening'])[0] == fm['contract'],
                'ACCOUNT_PREFIX_CONTRACT_OR_ACCOUNT')
            _require(account_prefix['statements'][-1]['completed_at_ms'] < capture['observed_at_ms'] and
                account_prefix['closing']['completed_at_ms'] < capture['observed_at_ms'] and
                (first['opening'] is None or account_prefix['statements'][-1]['completed_at_ms'] <
                 fm['opening']['capture']['observed_at_ms']), 'ACCOUNT_PREFIX_FUTURE')
            _require(Decimal(prefix_report['canonical_wallet']) == Decimal(fm['initial_collateral']) and
                Decimal(account_prefix['adv_notional']) == Decimal(fm['adv_notional']) and
                Decimal(account_prefix['participation_rate']) == Decimal(fm['participation_rate']) and
                account_prefix['opening']['observed_at_ms']//86400000 == first['parsed'][3].period_index,
                'ACCOUNT_PREFIX_WALLET_OR_CAPACITY')
            ledger = original_ledger
            prefix_rec = ledger.reconciliation_snapshot().as_canonical_dict()
            prior_fee, prior_funding, prior_cash = (Decimal(prefix_rec[k]) for k in ('fee','funding','external_cash_flow'))
            result['account_prefix_sha256'] = digest(account_prefix)
            result['limitations'].append('MANUAL_PREFIX_RETAINED_STREAM_GAPS_NOT_REPAIRED')
        cursor = 0
        for index, b in enumerate(bs[1:], 1):
            cap = b['manifest']['opening']['capture']; start, end = cap['observed_at_ms'], cap['completed_at_ms']
            _require(all(high < start or low > end for _,low,high in intervals), 'CONTINUATION_EVENT_OVERLAPS_OBSERVATION')
            before_times = {t for t,_,high in intervals if high < start}
            after_times = {t for t,low,_ in intervals if low > end}
            _require(not before_times or not after_times or max(before_times) < min(after_times), 'CONTINUATION_CLOCK_ORDER_UNRESOLVED')
            _require(max(r['received_at_ms'] for r in bs[index-1]['records']) < start, 'CONTINUATION_PRECEDES_PREVIOUS_RECEIPTS')
            _require(all(a['timestamp'] > end for a in b['manifest']['actions']), 'CONTINUATION_ACTION_BEFORE_OBSERVATION')
            limit = cursor
            while limit < len(ordered) and ordered[limit].timestamp in before_times:
                limit += 1
            apply_reported_events(ledger, ordered[cursor:limit], reduce_only); cursor = limit
            state = ledger.state_as_canonical_dict(); prefix_hash = digest(state)
            _require(Decimal(b['manifest']['initial_collateral']) == Decimal(state['wallet_balance']), 'CONTINUATION_WALLET_MISMATCH')
            actual = {k:v for k,v in state['positions'].items() if Decimal(v['quantity']) != 0}
            declared = {p['symbol']:p for p in b['opening']['positions']}
            _require(set(actual) == set(declared), 'CONTINUATION_POSITION_MISMATCH')
            _require(all(Decimal(v['quantity']) == Decimal(declared[k]['quantity']) and
                         Decimal(v['entry_notional']) == Decimal(declared[k]['entry_notional']) for k,v in actual.items()), 'CONTINUATION_EXACT_BASIS_MISMATCH')
            _require(all(p['basis_source_sha256'] == prefix_hash for p in declared.values()), 'CONTINUATION_BASIS_SOURCE_MISMATCH')
            previous_expected = bs[index-1]['manifest']['expected_end_position']
            quantity = ledger.position(symbol).quantity if ledger.position(symbol) else Decimal(0)
            _require(previous_expected is None or Decimal(previous_expected) == quantity, 'DECLARED_POSITION_MISMATCH')
            result['continuations'].append(dict(index=index, manifest_hash=b['journal'].manifest_hash,
                capture_hash=b['manifest']['opening']['capture_hash'], ledger_prefix_hash=prefix_hash,
                observed_start_ms=start, observed_end_ms=end, wallet=state['wallet_balance'],
                positions=actual, status='MATCHED_CANONICAL_PREFIX'))
        apply_reported_events(ledger, ordered[cursor:], reduce_only)
        state = ledger.state_as_canonical_dict(); rec = ledger.reconciliation_snapshot().as_canonical_dict()
        quantity = ledger.position(symbol).quantity if ledger.position(symbol) else Decimal(0)
        expected = bs[-1]['manifest']['expected_end_position']
        _require(expected is None or Decimal(expected) == quantity, 'DECLARED_POSITION_MISMATCH')
        # Re-read every input before publishing; a changed receipt tail is unknown.
        _require(all(b['journal'].read()[1] == b['tail'] for b in bs), 'RECEIPT_SNAPSHOT_CHANGED')
        window_fee = Decimal(rec['fee'])-prior_fee
        realized = Decimal(state['wallet_balance'])-Decimal(fm['initial_collateral'])+window_fee-(Decimal(rec['funding'])-prior_funding)-(Decimal(rec['external_cash_flow'])-prior_cash)
        if account_prefix is not None:
            result.update(window_fee=canonical_decimal(window_fee),window_realized_pnl=canonical_decimal(realized))
        reported_realized = sum((Decimal(r['income']) for r in pnl), Decimal(0))
        residual = Decimal(wallet['closing_wallet'])-Decimal(state['wallet_balance'])
        result.update(canonical_wallet=state['wallet_balance'], wallet_residual=canonical_decimal(residual),
            reconciliation=rec, positions=state['positions'], ledger_state_hash=digest(state), capacity_consumed=state['capacity_consumed'],
            fee_comparison='MATCHED_REPORTED_TRADES' if trades else 'NO_REPORTED_TRADES',
            realized_pnl_comparison='MATCHED_REPORTED_TOTAL' if realized == reported_realized else 'DIFFERENCE_FOUND')
        if residual != 0:result['issues'].append('CANONICAL_WALLET_DIFFERENCE')
        if realized != reported_realized:result['issues'].append('REPORTED_REALIZED_PNL_DIFFERENCE')
        result['status'] = 'DIFFERENCE_FOUND' if result['issues'] else 'MATCHED_REPORTED_INPUTS'
    except ChainUnresolved as exc:
        result['issues'].append(str(exc))
    except (ValueError, TypeError, ArithmeticError, RuntimeError, KeyError) as exc:
        result['issues'].append('CHAIN_REPLAY_'+type(exc).__name__.upper())
    result['issues'] = sorted(set(result['issues']))
    return result
