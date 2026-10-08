"""Prove an expired pilot made no execution without discarding prior history.

This pure check consumes already custody-verified enrollment records, the full
original ``_state`` snapshot, and the installed partition record returned by its
fixed reader. It does not establish filesystem custody, issue authority, record
a stop, allocate a claim, or capture another pre-signal opening. The closure
worker owns those boundaries and must recheck its originals under its lease.
"""
from decimal import Decimal
import re

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import require
from services.execution.perpetual_ledger_v2 import ledger_decimal_context

SCHEMA = 'GOLD_PILOT_IDLE_EVIDENCE_V1'
MODE = 'NO_EXECUTION_SINCE_ENROLLMENT'
CORE = frozenset(('reservations', 'recovery', 'oms', 'wire'))


@ledger_decimal_context()
def validate_idle(*, request, configuration, receipt, intent, state, origin,
                  partition_record, source_commit, now_ms):
    """Validate the original no-Gold-execution case, including a real BTC cut.

    An old enrollment without its original intent is not evidence of no
    execution. Nor is a caller-provided empty Gold projection: every original
    execution component must still equal the original enrollment commitment.
    Prior Gold executions belong to the separate cash/feedback closure path.
    """
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from live.execution import demo_market_partition_runtime as partition
    from live.execution.demo_market_partition import partition_state
    from live.execution.demo_pre_submit import _require_origin

    require(type(source_commit) is str and re.fullmatch('[a-f0-9]{40}', source_commit)
        and type(request) is dict and request.get('source_commit') == source_commit,
        'PILOT_IDLE_SOURCE')
    require(type(intent) is dict, 'PILOT_IDLE_ORIGINAL_INTENT_REQUIRED')
    config = enrollment.completed_configuration_records(configuration=configuration,
        receipt=receipt, intent=intent, request=request)
    require(type(now_ms) is int and config['ends_at_ms'] <= now_ms <= 2**63-1,
        'PILOT_IDLE_ORIGINAL_WINDOW')
    require(type(state) is dict and set(state) == CORE
        and all(type(state[key]) is list for key in CORE), 'PILOT_IDLE_FULL_EXECUTION_STATE')
    require(type(origin) is dict and origin.get('symbol') == 'XAUUSDT'
        and origin.get('account_ref_sha256') == config['account_ref_sha256']
        and origin.get('source_sha256') == intent['prepared']['opening_origin']['source_sha256'],
        'PILOT_IDLE_ORIGIN_BINDING')
    report = _require_origin(origin, now_ms=now_ms)
    require(config['ends_at_ms'] <= origin['observed_at_ms'] <= origin['completed_at_ms'] <= now_ms,
        'PILOT_IDLE_FRESH_END_OBSERVATION')
    require(Decimal(report['wallet']) == Decimal(config['initial_equity']), 'PILOT_IDLE_EQUITY_CHANGED')
    descriptor = None
    committed = dict(state)
    if partition_record is not None:
        require(type(partition_record) is dict
            and partition_record.get('source_commit') == source_commit, 'PILOT_IDLE_PARTITION_SOURCE')
        descriptor = partition._descriptor(partition_record)
        committed['market_partition'] = descriptor
    require(_digest(committed) == intent['prepared']['execution_state_sha256']
        == receipt['execution_state_sha256'], 'PILOT_IDLE_EXECUTION_CHANGED')
    if partition_record is None:
        require(all(not state[key] for key in CORE), 'PILOT_IDLE_GOLD_HISTORY_REQUIRES_FEEDBACK')
    else:
        projected = partition_state(state=state, candidate=partition_record['candidate'], as_of_ms=now_ms)
        require(all(not projected['target'][key] for key in CORE), 'PILOT_IDLE_GOLD_HISTORY_REQUIRES_FEEDBACK')
        # This original pure inventory check keeps the sealed BTC cash and all
        # other collateral while verifying that Gold remains flat at its cut.
        partition._inventory(origin, partition_record, now_ms, None)
    return dict(schema=SCHEMA, mode=MODE, source_commit=source_commit,
        account_ref_sha256=config['account_ref_sha256'], request_sha256=_digest(request),
        configuration_sha256=_digest(config), enrollment_intent_sha256=_digest(intent),
        enrollment_receipt_sha256=_digest(receipt), opening_origin_sha256=config['opening_origin_sha256'],
        origin_sha256=_digest(origin), execution_state_sha256=_digest(committed),
        market_partition_sha256=None if descriptor is None else _digest(descriptor),
        started_at_ms=config['started_at_ms'], ends_at_ms=config['ends_at_ms'], observed_at_ms=now_ms,
        position_flat=True, observed_equity=report['wallet'], submission_enabled=False, economic_authority=False)
