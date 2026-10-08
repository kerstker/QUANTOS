"""Bind a strategy's observed signal to original Demo cash feedback.

Retrospective only: callers supply original prepared batches and a cash capture.
The existing receipt and canonical cash exporters remain responsible for fills,
continuity and money. No functional-test cycle, new ledger or approval is made.
"""
from decimal import Decimal

from live.execution.demo_batch_risk import _digest
from live.execution.demo_prepared_execution import _load_proof
from live.execution.demo_research_feedback import build_feedback, build_roundtrip_feedback
from services.research.gold_execution_feedback import is_completed_episode
from services.validation.demo_strategy_constraints import check_proposal
from tools.gold_forward_review import review
from tools.gold_strategy_entry import entry_client_order_id


def require(condition, reason):
    if not condition:
        raise ValueError('GOLD_STRATEGY_FEEDBACK_'+reason)


def build(observation_directory, *, strategy_trial, batches, dispatch, stream,
          capture, source_commit, output_directory, as_of_ms):
    """Export only this signal's opening and subsequent strategy reductions.

    Historical constraints are checked at their declared decision time, never
    renewed. The original exporter independently checks receipt windows, signed
    strategy identity, flat closure and exact cash movement.
    """
    require(isinstance(batches, (list, tuple)) and 1 <= len(batches) <= 32,
            'BATCH_COUNT')
    checked = review(observation_directory, strategy_trial=strategy_trial)
    require(checked['observation_status'] == 'COMPLETE_OBSERVATION'
            and len(checked['signals']) == 1, 'COMPLETE_SIGNAL_REQUIRED')
    identity = checked['research_identity']
    account = capture['account_ref_sha256']
    client = entry_client_order_id(checked, account)
    quantity = Decimal(checked['signals'][0]['exit_and_size_plan']['quantity'])
    before = dispatch.evidence_snapshot()
    for index, item in enumerate(batches):
        require(type(item) is dict and set(item) == {'prepared', 'start_ms', 'end_ms'},
                'BATCH_FIELDS')
        proof = _load_proof(item['prepared'])
        constraints = proof.get('execution_constraints')
        require(type(constraints) is dict, 'STRATEGY_CONSTRAINTS_REQUIRED')
        check_proposal(proof, now_ms=constraints.get('not_before_ms'))
        require(constraints['strategy_definition_sha256'] == checked['strategy_definition_sha256']
                and proof['strategy_id'] == identity['strategy_id']
                and proof['context']['account_ref_sha256'] == account, 'STRATEGY_BINDING')
        orders = proof['orders']
        require(len(orders) == 1, 'SINGLE_STRATEGY_ORDER')
        order = orders[0]
        require(order['symbol'] == 'XAUUSDT' and order['side'] == ('BUY' if index == 0 else 'SELL')
                and order['reduceOnly'] is (index > 0), 'OPENING_REDUCTION_SEQUENCE')
        if index == 0:
            require(order['newClientOrderId'] == client and Decimal(order['quantity']) == quantity,
                    'SIGNAL_ENTRY_BINDING')
    arguments = dict(dispatch=dispatch, stream=stream, capture=capture, source_commit=source_commit,
        output_directory=output_directory, as_of_ms=as_of_ms, research_identity=identity)
    if len(batches)==1:
        value = build_feedback(**batches[0], **arguments)
        require(is_completed_episode(value), 'UNFILLED_TERMINAL_ENTRY_REQUIRED')
    else:
        value = build_roundtrip_feedback(batches, **arguments)
    require(dispatch.evidence_snapshot() == before
            and review(observation_directory, strategy_trial=strategy_trial) == checked,
            'ORIGINALS_CHANGED')
    require(value['dispatch_evidence_sha256'] == _digest(before), 'DISPATCH_BINDING')
    return value


def export_retained(root, *, observation_directory, strategy_trial, bundle_sha256,
                    account, source_commit, stores, as_of_ms):
    """Use executor-owned originals for a settled strategy's retained cash window.

    Cash must have been captured across execution at the fixed runtime location.
    Absence is a pending handoff, never a retroactively invented opening wallet.
    Call under the original preparation lease, as the strategy worker does.
    """
    import re
    from live.execution.demo_pre_submit import _state, _retain
    from live.execution.demo_position_context import load_context
    from live.execution.demo_market_partition_runtime import context_reservations
    from services.research.demo_cash_window import load_capture, assess
    from tools.demo_reported_recovery import _read
    from tools.demo_resident_recovery import _check_path

    require(type(bundle_sha256) is str and re.fullmatch('[a-f0-9]{64}', bundle_sha256), 'RUNTIME_ID')
    area = root/'strategy-cash'/bundle_sha256
    if not area.exists() and not area.is_symlink():
        return dict(status='WAITING_STRATEGY_CASH_WINDOW', submission_enabled=False, economic_authority=False)
    for directory in (root, area.parent, area):
        _check_path(directory, root.stat().st_uid, directory=True)
    capture = load_capture(area)
    cash = assess(capture)
    require(capture['account_ref_sha256'] == account and cash['status'] == 'MATCHED_REPORTED_WINDOW',
            'CASH_WINDOW_REQUIRED')
    state = _state(stores['dispatch'], stores['recovery'], stores['oms'])
    context = load_context(state, symbol='XAUUSDT', account_ref=account, as_of_ms=as_of_ms)
    require(context['quantity'] == '0', 'FLAT_REQUIRED')
    checked = review(observation_directory, strategy_trial=strategy_trial)
    batches = []
    for reservation, source in context_reservations(state=state,context=context,as_of_ms=as_of_ms):
        require(source['reservation_sha256'] == reservation['record_hash'], 'RESERVATION_BINDING')
        plan = _read(root/'receipt-reconciliation/plans'/(source['plan_sha256']+'.json'), root, 16_000_000)
        path = root/'evidence'/(plan['proposal_sha256']+'.json')
        proof = _read(path, root, 16_000_000)
        require(proof['orders'] == reservation['payload']['orders']
                and proof['context'] == reservation['payload']['context'], 'ORIGINAL_BINDING')
        if proof['strategy_id'] != checked['research_identity']['strategy_id']:
            continue
        require(source['manifest_hash'] is not None, 'NATIVE_RECEIPTS_REQUIRED')
        prepared = dict(schema='DEMO_PREPARED_BATCH_V1', status='INTENTS_PERSISTED',
            reservation_sha256=reservation['record_hash'], proposal_sha256=plan['proposal_sha256'],
            proposal_path=str(path), orders=proof['orders'], context=proof['context'],
            submission_enabled=False, economic_authority=False)
        batches.append(dict(prepared=prepared, start_ms=proof['origin']['completed_at_ms']+1, end_ms=0))
    require(1 <= len(batches) <= 32, 'BATCH_COUNT')
    for index, item in enumerate(batches):
        item['end_ms'] = batches[index+1]['start_ms'] if index+1 < len(batches) else cash['bounds']['query_end_ms']
        require(2 <= item['end_ms']-item['start_ms'] <= 150000, 'RECEIPT_WINDOW_BUDGET')
    target = root/'strategy-feedback'/bundle_sha256
    for directory in (target.parent, target, target/'reconciliation', target/'research-feedback'):
        directory.mkdir(mode=0o700, exist_ok=True)
        _check_path(directory, root.stat().st_uid, directory=True)
    stream_before = stores['stream'].verify()
    value = build(observation_directory, strategy_trial=strategy_trial, batches=batches,
        dispatch=stores['dispatch'], stream=stores['stream'], capture=capture, source_commit=source_commit,
        output_directory=target/'reconciliation', as_of_ms=as_of_ms)
    require(_state(stores['dispatch'], stores['recovery'], stores['oms']) == state
            and stores['stream'].verify() == stream_before and load_capture(area) == capture,
            'ORIGINALS_CHANGED')
    path = _retain(target/'research-feedback', value)
    return dict(status='STRATEGY_FEEDBACK_RETAINED', feedback_sha256=value['result_hash'],
        artifact_sha256=_digest(value), path=str(path), submission_enabled=False, economic_authority=False)
