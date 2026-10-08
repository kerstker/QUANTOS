"""Process-local bar signal to exact Demo entry draft; no dispatch or approval.

Construction verifies the retained trial, then owns the public bar cursor.
Serialized signal claims cannot be supplied to prepare. Trusted code remains
subject to review; this class is not a security boundary against trusted code.
"""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from live.execution import demo_gold_pilot
from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _venue_rules, _venue_account_cap
from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context
from services.research.demo_execution_origin import assess, GOLD_ACCOUNT_TERMS_SCHEMA
from services.research.gold_bar_candidates import CANDIDATES, INTERVAL_MS, evaluate
from services.research.gold_bar_stream import GoldBarStream
from services.validation.demo_submission import validate_orders, GOLD_BATCH_SCHEMA
from tools.gold_bar_definition import define, PreparedBarDefinition


def require(condition, reason):
    if not condition:
        raise ValueError('GOLD_BAR_DEMO_ENTRY_' + reason)


class BarDemoEntry:
    def __init__(self, archive, *, candidate, source_directory, warmup, registered_at_ms, prepared_definition=None):
        require(type(registered_at_ms) is int and 0 <= registered_at_ms <= 2**63-1, 'REGISTRATION')
        self._archive = Path(archive)
        self._source = Path(source_directory)
        require(prepared_definition is None or type(prepared_definition) is PreparedBarDefinition, 'PREPARED_TYPE')
        self._prepared_definition = prepared_definition
        self._definition = (define(archive, candidate=candidate, source_directory=source_directory)
            if prepared_definition is None else prepared_definition.definition(archive, candidate=candidate,
                source_directory=source_directory))
        self._cursor = GoldBarStream(tuple(warmup))
        require(self._cursor.last_received <= registered_at_ms, 'FUTURE_WARMUP')
        self._registered = registered_at_ms
        self._last = None

    def ingest(self, raw, *, received_at_ms):
        require(type(received_at_ms) is int and received_at_ms >= self._registered, 'RECEIPT_BEFORE_REGISTRATION')
        row = self._cursor.ingest(raw, received_at_ms=received_at_ms)
        if row['status'] == 'CLOSED_BAR' and row['close_ms'] > self._registered:
            self._last = deepcopy(row)
        return row

    def verified_definition(self):
        verifier = define if self._prepared_definition is None else self._prepared_definition.definition
        definition = verifier(self._archive, candidate=self._definition['candidate'],
                              source_directory=self._source)
        require(definition == self._definition, 'DEFINITION_CHANGED')
        return definition

    def signal_at(self, decision_ms):
        require(type(decision_ms) is int and any(b.available_ms == decision_ms
            and b.close_ms > self._registered for b in self._cursor.bars), 'ORIGINAL_ENTRY_BAR')
        signal = evaluate(self._cursor.bars, candidate=self._definition['candidate'], as_of_ms=decision_ms)
        return signal

    def entry_signal(self, decision_ms):
        signal = self.signal_at(decision_ms)
        require(signal['status'] == 'READY' and signal['entry_side'] in ('BUY', 'SELL'), 'ORIGINAL_ENTRY_SIGNAL')
        return signal

    @ledger_decimal_context()
    def prepare(self, *, origin, now_ms):
        require(type(now_ms) is int and 0 <= now_ms <= 2**63-1, 'TIME')
        require(not self._cursor.blocked, 'STREAM_BLOCKED')
        require(now_ms >= self._cursor.last_received, 'FUTURE_STREAM')
        definition = self.verified_definition()
        result = dict(schema='GOLD_BAR_DEMO_ENTRY_DRAFT_V1', environment='BINANCE_USDM_DEMO',
            definition_hash=definition['definition_hash'], trial_id=definition['trial_id'],
            observed_at_ms=now_ms, orders=[], submission_enabled=False, economic_authority=False,
            risk_status='NOT_RUN', authorization_status='BAR_STRATEGY_ENROLLMENT_REQUIRED')
        if self._last is None:
            return dict(result, status='NO_NEW_CLOSED_BAR')
        signal = self._last['decisions'][CANDIDATES.index(definition['candidate'])]
        require(signal['decision_ms'] <= now_ms, 'FUTURE_SIGNAL')
        if signal['status'] != 'READY' or signal['entry_side'] is None:
            return dict(result, status='NO_ENTRY_SIGNAL')
        plan = definition['size_and_timing']
        deadline = signal['decision_ms'] + plan['entry_timeout_ms']
        require(signal['decision_ms'] < now_ms < deadline, 'SIGNAL_EXPIRED_OR_FUTURE')
        snapshot = deepcopy(origin)
        require(type(snapshot) is dict and snapshot.get('schema') == GOLD_ACCOUNT_TERMS_SCHEMA
                and snapshot.get('symbol') == 'XAUUSDT', 'DEMO_ORIGIN_REQUIRED')
        assessment = assess(snapshot, as_of_ms=now_ms)
        require(assessment['status'] == 'OBSERVED_FLAT_QUOTE', 'ACCOUNT_NOT_FLAT_OR_UNRESOLVED')
        # Only this assessed flat account has wallet == margin equity. Do not
        # use the research initial collateral or install/reset pilot control.
        pilot = demo_gold_pilot.observe(dict(account_ref_sha256=snapshot['account_ref_sha256'],
            equity=assessment['wallet']), now_ms=now_ms, origin_sha256=_digest(snapshot))
        require(pilot is not None, 'PILOT_REQUIRED')
        result['pilot_control'] = pilot
        if pilot['status'] == 'STOPPED':
            return dict(result, status='PILOT_STOPPED', reason=pilot['reason'])
        reads = {row['kind']: row for row in snapshot['reads']}
        book_read = reads['book']
        book = book_read['data']
        clocks = [reads[k] for k in ('clock_before', 'clock_after')]
        lower_offset = min(r['data']['serverTime']-r['received_at_ms'] for r in clocks)
        upper_offset = max(r['data']['serverTime']-r['requested_at_ms'] for r in clocks)
        quote_lower, quote_upper = book['time']-upper_offset, book['time']-lower_offset
        require(signal['decision_ms'] <= quote_lower <= quote_upper <= book_read['received_at_ms'] <= now_ms
                and now_ms-quote_lower < plan['max_quote_age_ms'], 'QUOTE_NOT_CURRENT_FOR_SIGNAL')
        side = signal['entry_side']
        price = Decimal(book['askPrice' if side == 'BUY' else 'bidPrice'])
        distance = Decimal(signal['stop_distance'])
        require(distance.is_finite() and 0 < distance < price, 'STOP_DISTANCE')
        identity = dict(account_ref_sha256=snapshot['account_ref_sha256'],
                        definition_hash=definition['definition_hash'],
                        decision_id=signal['decision_id'], action='OPEN_' + side)
        order = dict(symbol='XAUUSDT', side=side, type='LIMIT', timeInForce='IOC',
            quantity=canonical_decimal(Decimal(plan['quantity'])), price=canonical_decimal(price),
            reduceOnly=False, positionSide='BOTH', newClientOrderId='QD'+_digest(identity)[:32],
            newOrderRespType='ACK')
        demo_gold_pilot.require_opening_allowed([order], pilot)
        validate_orders([order], schema=GOLD_BATCH_SCHEMA)
        _venue_rules([order], snapshot)
        _venue_account_cap([order], snapshot)
        result.update(status='BAR_ENTRY_DRAFT', orders=[order], decision=signal,
            signal_valid_until_ms=deadline, valid_until_ms=min(deadline, quote_lower+plan['max_quote_age_ms']),
            origin_sha256=_digest(snapshot), account_ref_sha256=snapshot['account_ref_sha256'],
            exit_plan=dict(stop_distance=canonical_decimal(distance), stop_basis='ACTUAL_FILLED_AVERAGE_PRICE',
                           max_holding_ms=signal['max_holding_bars']*INTERVAL_MS,
                           signal_exit_side='exit_long' if side == 'BUY' else 'exit_short'),
            required_before_execution=['BAR_STRATEGY_ENROLLMENT', 'INDEPENDENT_ADMISSION_AND_RISK',
                'RECHECK_PERSISTENT_5_PERCENT_STOP_AT_SUBMISSION', 'SIGNED_ENTRY_DEADLINE',
                'ORIGINAL_DISPATCH_DEDUPLICATION', 'OMS_BOUND_BIDIRECTIONAL_EXIT_CONTROLLER'])
        return dict(result, result_hash=_digest(result))


def restore_engine(directory, *, archive, source_directory, prepared_definition=None):
    """Rebuild a completed, reviewed public bar cursor, never an entry renewal.

    Does not restore an interrupted stream, execution
    permissions or positions. Original OMS/cash reconstruction remains separate.
    """
    import hashlib
    from services.governance.json_integrity import load_json_without_duplicate_keys as decode
    from services.research.gold_bar_dataset import load_bars
    from tools.gold_bar_forward import review
    from tools.gold_cost_review import _read

    root = Path(directory).absolute()
    checked = review(root, archive=archive, source_directory=source_directory, prepared_definition=prepared_definition)
    registration_raw = _read(root, 'registration.json')
    require(hashlib.sha256(registration_raw).hexdigest() == checked['registration_sha256'], 'RESTORE_REGISTRATION')
    registration = decode(registration_raw)
    names = ('warmup.json', 'warmup-receipt.json', 'messages.jsonl')
    raw = {name: _read(root / 'journal', name) for name in names}
    require(all(hashlib.sha256(value).hexdigest() == checked['journal_hashes'][name]
                for name, value in raw.items()), 'RESTORE_JOURNAL')
    receipt = decode(raw['warmup-receipt.json'])
    bars = load_bars(raw['warmup.json'], requested_at_ms=receipt['requested_at_ms'],
                     received_at_ms=receipt['received_at_ms'])
    engine = BarDemoEntry(archive, candidate=registration['definition']['candidate'],
        source_directory=source_directory, warmup=bars, registered_at_ms=registration['registered_at_ms'],
        prepared_definition=prepared_definition)
    require(engine.verified_definition() == registration['definition'], 'RESTORE_DEFINITION')
    for line in raw['messages.jsonl'].splitlines():
        event = decode(line)
        engine.ingest(event['raw'], received_at_ms=event['received_at_ms'])
    require(review(root, archive=archive, source_directory=source_directory, prepared_definition=prepared_definition) == checked
            and _read(root, 'registration.json') == registration_raw, 'RESTORE_CHANGED')
    return engine
