"""Bind original bar orders to reported-account reconciliation, retaining gaps."""
from copy import deepcopy
from decimal import Decimal
import re

from live.execution.demo_batch_risk import _digest
from live.execution.demo_prepared_execution import _load_proof
from services.research.demo_account_recovery import reconcile, reconcile_episode
from services.validation.demo_strategy_constraints import check_proposal
from tools.gold_bar_demo_entry import BarDemoEntry


def require(condition, reason):
    if not condition:
        raise ValueError('GOLD_BAR_REPORTED_'+reason)


def build(*, engine, entry_decision_ms, prepared, dispatch, inputs, source_commit, as_of_ms, opening_record=None, bundle_sha256=None):
    require(type(engine) is BarDemoEntry and type(prepared) in (list, tuple)
        and 2 <= len(prepared) <= 32, 'INPUTS')
    before = dispatch.evidence_snapshot()
    proofs = [_load_proof(item) for item in prepared]
    result = build_from_records(engine=engine, entry_decision_ms=entry_decision_ms, prepared=prepared,
        proofs=proofs, dispatch_snapshot=before, inputs=inputs, source_commit=source_commit, as_of_ms=as_of_ms,
        opening_record=opening_record, bundle_sha256=bundle_sha256)
    require(dispatch.evidence_snapshot() == before and
        all(_load_proof(item) == proof for item, proof in zip(prepared, proofs, strict=True)), 'INPUTS_CHANGED')
    return result


def build_from_records(*, engine, entry_decision_ms, prepared, proofs, dispatch_snapshot,
                       inputs, source_commit, as_of_ms, opening_record=None, bundle_sha256=None):
    """Replay supplied receipts/proofs without directly opening their stores.

    A historical snapshot must be proved to be the exact native dispatch-chain
    prefix committed by its original capture. This function does not establish
    that ancestry, replace custody checks or create a parallel account ledger.
    The original engine still independently verifies its archive and source.
    """
    require(type(engine) is BarDemoEntry and type(prepared) in (list, tuple)
        and 2 <= len(prepared) <= 32, 'INPUTS')
    require(type(source_commit) is str and re.fullmatch('[a-f0-9]{40}', source_commit) is not None
        and type(entry_decision_ms) is int and type(as_of_ms) is int
        and 0 <= entry_decision_ms <= as_of_ms, 'SOURCE_OR_TIME')
    original_inputs = deepcopy(inputs)
    definition = engine.verified_definition()
    signal = engine.entry_signal(entry_decision_ms)
    account = inputs['opening']['account_ref_sha256']
    side = signal['entry_side']
    require(side in ('BUY', 'SELL'), 'SIDE')
    client = 'QD'+_digest(dict(account_ref_sha256=account, definition_hash=definition['definition_hash'],
        decision_id=signal['decision_id'], action='OPEN_'+side))[:32]
    before = deepcopy(dispatch_snapshot)
    original_proofs = deepcopy(proofs)
    require(type(proofs) in (list, tuple) and len(proofs) == len(prepared), 'PROOFS')
    proof_records = proofs
    proofs, orders, seen = [], [], set()
    for index, item in enumerate(prepared):
        proof = proof_records[index]
        require(type(item) is dict and {'proposal_sha256', 'reservation_sha256', 'schema', 'status',
            'orders', 'context', 'submission_enabled', 'economic_authority'} <= set(item), 'PREPARED_PROOF_BINDING')
        require(_digest(proof) == item['proposal_sha256'] and item['schema'] == 'DEMO_PREPARED_BATCH_V1'
            and item['status'] == 'INTENTS_PERSISTED' and item['orders'] == proof['orders']
            and item['context'] == proof['context'] and item['submission_enabled'] is False
            and item['economic_authority'] is False, 'PREPARED_PROOF_BINDING')
        constraints = proof.get('execution_constraints')
        require(type(constraints) is dict, 'CONSTRAINTS')
        check_proposal(proof, now_ms=constraints.get('not_before_ms'))
        require(constraints['strategy_definition_sha256'] == definition['definition_hash']
            and proof['context']['account_ref_sha256'] == account
            and len(proof['orders']) == 1, 'DEFINITION_OR_ACCOUNT')
        require(not proofs or proof['strategy_id'] == proofs[0]['strategy_id'], 'STRATEGY')
        reservation = item['reservation_sha256']
        require(reservation not in seen, 'DUPLICATE_RESERVATION')
        seen.add(reservation)
        matching = [r for r in before['reservations'] if r['record_hash'] == reservation]
        require(len(matching) == 1 and matching[0]['payload']['orders'] == proof['orders']
            and matching[0]['payload']['context'] == proof['context'], 'RESERVATION')
        order, = proof['orders']
        require(order['symbol'] == 'XAUUSDT' and order['reduceOnly'] is (index > 0)
            and order['side'] == (side if not index else ('SELL' if side == 'BUY' else 'BUY')), 'SEQUENCE')
        if not index:
            require(order['newClientOrderId'] == client
                and Decimal(order['quantity']) == Decimal(definition['size_and_timing']['quantity']), 'ENTRY')
        orders.append(order)
        proofs.append(proof)
    risk = proofs[0]['risk']['input_snapshot']
    opening = proofs[0]['origin']
    if opening_record is not None:
        from tools.gold_bar_cash import check_account_opening
        require(type(bundle_sha256) is str and re.fullmatch('[a-f0-9]{64}', bundle_sha256) is not None,
            'EXTERNAL_RUNTIME_BINDING_REQUIRED')
        opening = check_account_opening(opening_record, proof=proofs[0],
            bundle_sha256=bundle_sha256, source_commit=source_commit,
            entry_decision_ms=entry_decision_ms)
    require(inputs['opening'] == opening and inputs['system_orders'] == orders
        and Decimal(inputs['adv_notional']) == Decimal(str(risk['market']['daily_volume_base']))*
            Decimal(str(risk['market']['reference_price']))
        and Decimal(inputs['participation_rate']) == Decimal(str(risk['native_limits']['max_participation_rate'])),
        'ORIGINAL_ACCOUNT_INPUTS')
    available = max(inputs['opening']['completed_at_ms'], inputs['closing']['completed_at_ms'],
        *(s['completed_at_ms'] for s in inputs['statements']),
        *(r['received_at_ms'] for w in inputs['windows'] for r in w['messages']),
        *(g['at_ms'] for w in inputs['windows'] for g in w['gaps']))
    require(available <= as_of_ms, 'FUTURE_INPUTS')
    cross_day = inputs['opening']['completed_at_ms']//86400000 != inputs['closing']['completed_at_ms']//86400000
    replay = reconcile_episode if cross_day else reconcile
    report, ledger = replay(**deepcopy(inputs))
    require(report['status'] == 'REPORTED_ACCOUNT_MATCHED' and ledger is not None
        and report['manual_order_ids'] == [] and report['final_quantity'] == '0'
        and report['wallet_residual'] == '0'
        and ledger.state_as_canonical_dict() == report['ledger_state'], 'ACCOUNT_UNRESOLVED')
    require(inputs == original_inputs and dispatch_snapshot == before and proof_records == original_proofs
        and engine.verified_definition() == definition and engine.entry_signal(entry_decision_ms) == signal, 'INPUTS_CHANGED')
    value = dict(schema='GOLD_BAR_REPORTED_ACCOUNT_FEEDBACK_V1', environment='BINANCE_USDM_DEMO',
        scope='TECHNICAL_REVIEW_ONLY', source_commit=source_commit, available_at_ms=available,
        definition=definition, definition_hash=definition['definition_hash'],
        entry_decision_ms=entry_decision_ms, entry_client_order_id=client, bar_signal_sha256=_digest(signal),
        inputs_sha256=_digest(original_inputs), dispatch_evidence_sha256=_digest(before),
        reservations=[p['reservation_sha256'] for p in prepared],
        account_report=report, account_report_sha256=_digest(report),
        candidate_attribution='BAR_DEFINITION_BOUND_TO_SIGNED_CONSTRAINTS',
        stream_coverage_repaired=False, source_verified=False, coverage_verified=False,
        economic_eligible=False, submission_enabled=False, research_trigger_enabled=False, strategy_pnl=None)
    if opening_record is not None:
        value.update(schema='GOLD_BAR_REPORTED_ACCOUNT_FEEDBACK_V2',
            opening_record_sha256=_digest(opening_record), bundle_sha256=bundle_sha256)
    return dict(value, result_hash=_digest(value))
