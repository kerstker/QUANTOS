"""Attach an original bar episode to canonical Demo cash feedback.

Consumes original prepared batches and an already collected cash window. Does
not manufacture balances, extend cash coverage, register identity, or trade.
"""
from decimal import Decimal

from live.execution.demo_batch_risk import _digest
from live.execution.demo_prepared_execution import _load_proof
from live.execution.demo_research_feedback import build_feedback, build_roundtrip_feedback
from services.research.gold_execution_feedback import is_completed_episode
from services.validation.demo_strategy_constraints import check_proposal
from tools.gold_bar_demo_entry import BarDemoEntry


def require(condition, reason):
    if not condition:
        raise ValueError('GOLD_BAR_FEEDBACK_'+reason)


def build(*, engine, entry_decision_ms, batches, dispatch, stream, capture,
          source_commit, output_directory, as_of_ms):
    require(type(engine) is BarDemoEntry and type(batches) in (list, tuple)
        and 1 <= len(batches) <= 32, 'INPUTS')
    definition = engine.verified_definition()
    signal = engine.entry_signal(entry_decision_ms)
    account = capture['account_ref_sha256']
    side = signal['entry_side']
    require(side in ('BUY', 'SELL'), 'ENTRY_SIDE')
    client = 'QD'+_digest(dict(account_ref_sha256=account, definition_hash=definition['definition_hash'],
        decision_id=signal['decision_id'], action='OPEN_'+side))[:32]
    before = dispatch.evidence_snapshot()
    strategy = None
    proofs = []
    for index, item in enumerate(batches):
        require(type(item) is dict and set(item) == {'prepared', 'start_ms', 'end_ms'}, 'BATCH_FIELDS')
        proof = _load_proof(item['prepared'])
        constraints = proof.get('execution_constraints')
        require(type(constraints) is dict, 'CONSTRAINTS_REQUIRED')
        check_proposal(proof, now_ms=constraints.get('not_before_ms'))
        require(constraints['strategy_definition_sha256'] == definition['definition_hash']
            and proof['context']['account_ref_sha256'] == account, 'DEFINITION_OR_ACCOUNT')
        if strategy is None:
            strategy = proof['strategy_id']
        require(proof['strategy_id'] == strategy and len(proof['orders']) == 1, 'STRATEGY')
        order, = proof['orders']
        require(order['symbol'] == 'XAUUSDT' and order['reduceOnly'] is (index > 0)
            and order['side'] == (side if index == 0 else ('SELL' if side == 'BUY' else 'BUY')), 'ORDER_SEQUENCE')
        if index == 0:
            require(order['newClientOrderId'] == client
                and Decimal(order['quantity']) == Decimal(definition['size_and_timing']['quantity']), 'ORIGINAL_ENTRY')
        proofs.append(proof)
    arguments = dict(dispatch=dispatch, stream=stream, capture=capture, source_commit=source_commit,
        output_directory=output_directory, as_of_ms=as_of_ms)
    cash = build_feedback(**batches[0], **arguments) if len(batches) == 1 else build_roundtrip_feedback(batches, **arguments)
    require(cash.get('result_hash') == _digest({k:v for k,v in cash.items() if k != 'result_hash'})
        and cash.get('account_ref_sha256') == account and cash.get('strategy_id') == strategy
        and cash.get('dispatch_evidence_sha256') == _digest(before)
        and cash.get('position_flat') is True and Decimal(cash['wallet_residual']) == 0
        and all(cash.get(k) is False for k in ('submission_enabled', 'economic_eligible', 'source_verified', 'coverage_verified'))
        and is_completed_episode(cash), 'CASH_NOT_RECONCILED')
    require(dispatch.evidence_snapshot() == before and engine.verified_definition() == definition
        and engine.entry_signal(entry_decision_ms) == signal
        and all(_load_proof(item['prepared']) == proof for item, proof in zip(batches, proofs, strict=True)), 'ORIGINALS_CHANGED')
    result = dict(schema='GOLD_BAR_CASH_RESEARCH_FEEDBACK_V1', environment='BINANCE_USDM_DEMO',
        scope='TECHNICAL_REVIEW_ONLY', definition=definition, definition_hash=definition['definition_hash'],
        entry_decision_ms=entry_decision_ms, entry_client_order_id=client, bar_signal_sha256=_digest(signal),
        cash_feedback=cash, cash_feedback_sha256=cash['result_hash'],
        candidate_attribution='BAR_DEFINITION_BOUND_TO_SIGNED_CONSTRAINTS',
        source_verified=False, coverage_verified=False, economic_eligible=False,
        submission_enabled=False, research_trigger_enabled=False, strategy_pnl=None)
    return dict(result, result_hash=_digest(result))


def export_retained(root, *, engine, entry_decision_ms, bundle_sha256, account,
                    source_commit, stores, as_of_ms):
    """Persist feedback only from original native receipt windows and cash.

    Existing native coverage gaps and cross-day limits remain rejections. Never
    stretch an order's window to fill the time between opening and reduction.
    """
    from pathlib import Path
    import re
    from live.execution.demo_pre_submit import _state, _retain
    from services.research.demo_cash_window import load_capture, assess
    from tools.demo_reported_recovery import _read
    from tools.demo_resident_recovery import _check_path

    require(type(bundle_sha256) is str and re.fullmatch('[a-f0-9]{64}', bundle_sha256) is not None, 'RUNTIME_ID')
    root = Path(root).absolute()
    require(root.resolve() == root, 'ROOT')
    area = root/'strategy-cash'/bundle_sha256
    for directory in (root, area.parent, area):
        _check_path(directory, root.stat().st_uid, directory=True)
    capture = load_capture(area)
    require(capture['account_ref_sha256'] == account and assess(capture)['status'] == 'MATCHED_REPORTED_WINDOW', 'CASH_WINDOW_REQUIRED')
    state, batches, originals = _retained_episode(root, engine=engine,
        entry_decision_ms=entry_decision_ms, account=account, stores=stores, as_of_ms=as_of_ms)
    target = root/'strategy-bar-feedback'/bundle_sha256
    for directory in (target.parent, target, target/'reconciliation', target/'research-feedback'):
        directory.mkdir(mode=0o700, exist_ok=True)
        _check_path(directory, root.stat().st_uid, directory=True)
    stream_before = stores['stream'].verify()
    value = build(engine=engine, entry_decision_ms=entry_decision_ms, batches=batches,
        dispatch=stores['dispatch'], stream=stores['stream'], capture=capture,
        source_commit=source_commit, output_directory=target/'reconciliation', as_of_ms=as_of_ms)
    require(_state(stores['dispatch'], stores['recovery'], stores['oms']) == state
        and stores['stream'].verify() == stream_before and load_capture(area) == capture
        and all(_read(path, root, 16_000_000) == original for path, original in originals), 'ORIGINALS_CHANGED')
    path = _retain(target/'research-feedback', value)
    return dict(status='BAR_STRATEGY_FEEDBACK_RETAINED', feedback_sha256=value['result_hash'],
        artifact_sha256=_digest(value), path=str(path), cash_reconciled=True,
        submission_enabled=False, economic_authority=False)


def _episode_sources(*, engine, entry_decision_ms, account, context, reservation_sources):
    require(context['quantity'] == '0', 'FLAT_REQUIRED')
    definition = engine.verified_definition()
    signal = engine.entry_signal(entry_decision_ms)
    client = 'QD'+_digest(dict(account_ref_sha256=account, definition_hash=definition['definition_hash'],
        decision_id=signal['decision_id'], action='OPEN_'+signal['entry_side']))[:32]
    selected = []
    for reservation, source in reservation_sources:
        require(source['reservation_sha256'] == reservation['record_hash'], 'RESERVATION_BINDING')
        orders = reservation['payload']['orders']
        if not selected:
            if not any(order['newClientOrderId'] == client for order in orders):
                continue
        elif any(not order['reduceOnly'] for order in orders):
            break
        selected.append((reservation, source))
    require(1 <= len(selected) <= 32, 'EPISODE_BATCHES_REQUIRED')
    return selected


def validate_retained_episode_records(*, state, context, reservation_sources, records,
                                      engine, entry_decision_ms, account):
    """Check retained episode contents after independent native context replay.

    This does not establish custody or make a caller-provided context trusted.
    Production obtains context/sources through their original fixed readers;
    checkpoint verification must replay the captured full book independently.
    Journals must already have passed the original read-only journal validator.
    No saved absolute path is rewritten, and the output contains no path.
    """
    require(type(reservation_sources) in (list, tuple)
        and [source for _, source in reservation_sources] == context['sources']
        and all(reservation in state['reservations'] for reservation, _ in reservation_sources),
        'RESERVATION_BINDING')
    selected = _episode_sources(engine=engine, entry_decision_ms=entry_decision_ms, account=account,
        context=context, reservation_sources=reservation_sources)
    batches = []
    for reservation, source in selected:
        row = records[source['plan_sha256']]
        require(type(row) is dict and set(row) == {'plan', 'proof', 'journal'}, 'ORIGINAL_NATIVE_BINDING')
        plan, proof, journal = row['plan'], row['proof'], row['journal']
        orders = reservation['payload']['orders']
        require(_digest(plan) == source['plan_sha256'] and _digest(proof) == plan['proposal_sha256']
            and proof['orders'] == orders and proof['context'] == reservation['payload']['context']
            and plan['reservation_sha256'] == reservation['record_hash']
            and plan['status'] == 'READY_FOR_OMS_PROJECTION'
            and source['manifest_hash'] is not None, 'ORIGINAL_NATIVE_BINDING')
        require(type(journal) is dict and set(journal) == {'manifest', 'manifest_sha256', 'tail'}
            and journal['manifest_sha256'] == source['manifest_hash'] == plan['manifest_hash']
            and journal['tail'] == plan['journal_tail'], 'NATIVE_JOURNAL_BINDING')
        window = journal['manifest']['native']['coverage']
        prepared = dict(schema='DEMO_PREPARED_BATCH_V1', status='INTENTS_PERSISTED',
            reservation_sha256=reservation['record_hash'], proposal_sha256=plan['proposal_sha256'],
            orders=orders, context=proof['context'], submission_enabled=False, economic_authority=False)
        batches.append(dict(prepared=prepared, start_ms=window['start_ms'], end_ms=window['end_ms']))
    return batches


def _retained_episode(root, *, engine, entry_decision_ms, account, stores, as_of_ms):
    """Select the original flat episode and verify every native journal binding."""
    from pathlib import Path
    from live.execution.demo_pre_submit import _state
    from live.execution.demo_position_context import load_context
    from live.execution.demo_market_partition_runtime import context_reservations
    from services.research.demo_execution_journal import DemoExecutionJournal
    from tools.demo_reported_recovery import _read
    from tools.demo_resident_recovery import _check_path

    state = _state(stores['dispatch'], stores['recovery'], stores['oms'])
    context = load_context(state, symbol='XAUUSDT', account_ref=account, as_of_ms=as_of_ms)
    sources = context_reservations(state=state, context=context, as_of_ms=as_of_ms)
    selected = _episode_sources(engine=engine, entry_decision_ms=entry_decision_ms, account=account,
        context=context, reservation_sources=sources)
    records = {}; originals = []
    for reservation, source in selected:
        plan_path = root/'receipt-reconciliation'/'plans'/(source['plan_sha256']+'.json')
        plan = _read(plan_path, root, 16_000_000)
        proof_path = root/'evidence'/(plan['proposal_sha256']+'.json')
        proof = _read(proof_path, root, 16_000_000)
        journal_path = Path(plan['journal_path'])
        require(root in journal_path.parents and journal_path.resolve() == journal_path, 'JOURNAL_PATH')
        for path in reversed(journal_path.parents):
            if path == root or root in path.parents:
                _check_path(path, root.stat().st_uid, directory=True)
        _check_path(journal_path, root.stat().st_uid, directory=False)
        with DemoExecutionJournal(journal_path, read_only=True) as journal:
            _, tail, _ = journal.read()
            records[source['plan_sha256']] = dict(plan=plan, proof=proof,
                journal=dict(manifest=journal.manifest, manifest_sha256=journal.manifest_hash, tail=tail))
        originals.extend(((plan_path, plan), (proof_path, proof)))
    batches = validate_retained_episode_records(state=state, context=context, reservation_sources=sources,
        records=records, engine=engine, entry_decision_ms=entry_decision_ms, account=account)
    for batch in batches:
        batch['prepared']['proposal_path'] = str(root/'evidence'/(batch['prepared']['proposal_sha256']+'.json'))
    return state, batches, originals


def export_completed(root, *, bundle, engine, entry_decision_ms, bundle_sha256,
                     account, source_commit, stores, as_of_ms):
    """Under the original preparation lease, capture and export a settled episode.

    A no-fill opening uses the original native cash path. An opening plus
    reductions uses reported-account reconciliation; missing fills remain errors.
    """
    from pathlib import Path
    import re
    import time
    from live.execution.demo_pre_submit import _state, _retain
    from tools.demo_resident_recovery import _check_path
    from tools.demo_reported_recovery import _read
    from tools.gold_bar_cash import close_window
    from tools.gold_bar_report_capture import capture_once
    from tools.gold_bar_reported_feedback import build as build_reported

    require(type(bundle_sha256) is str and re.fullmatch('[a-f0-9]{64}', bundle_sha256) is not None, 'RUNTIME_ID')
    root = Path(root).absolute()
    require(root.resolve() == root, 'ROOT')
    _check_path(root, root.stat().st_uid, directory=True)
    require(type(as_of_ms) is int and time.time_ns()//1000000 >= as_of_ms, 'CLOCK_ROLLBACK')
    opening_arguments = {}
    if bundle.get('schema') in ('GOLD_BAR_FRESH_EPISODE_INPUT_V1', 'GOLD_BAR_AUTO_EPISODE_INPUT_V1'):
        from tools.gold_bar_cash import load_account_opening
        opening_arguments['opening_record'] = load_account_opening(root, bundle=bundle, account=account,
            source_commit=source_commit)
    state, batches, originals = _retained_episode(root, engine=engine,
        entry_decision_ms=entry_decision_ms, account=account, stores=stores, as_of_ms=as_of_ms)
    if len(batches) == 1:
        close_window(root, bundle=bundle, account=account, source_commit=source_commit)
        return export_retained(root, engine=engine, entry_decision_ms=entry_decision_ms,
            bundle_sha256=bundle_sha256, account=account, source_commit=source_commit,
            stores=stores, as_of_ms=time.time_ns()//1000000)
    target = root/'strategy-bar-feedback'/bundle_sha256
    for directory in (target.parent, target, target/'research-feedback'):
        directory.mkdir(mode=0o700, exist_ok=True)
        _check_path(directory, root.stat().st_uid, directory=True)
    prepared = [b['prepared'] for b in batches]
    inputs = capture_once(root, bundle_sha256=bundle_sha256, prepared=prepared,
        dispatch=stores['dispatch'], stream=stores['stream'], account=account, source_commit=source_commit,
        order_windows=[{k: b[k] for k in ('start_ms', 'end_ms')} for b in batches], **opening_arguments)
    value = build_reported(engine=engine, entry_decision_ms=entry_decision_ms,
        prepared=prepared, dispatch=stores['dispatch'], inputs=inputs,
        source_commit=source_commit, as_of_ms=time.time_ns()//1000000,
        **(dict(opening_arguments, bundle_sha256=bundle_sha256) if opening_arguments else {}))
    require(_state(stores['dispatch'], stores['recovery'], stores['oms']) == state
        and all(_read(path, root, 16_000_000) == original for path, original in originals), 'ORIGINALS_CHANGED')
    if opening_arguments:
        require(load_account_opening(root, bundle=bundle, account=account, source_commit=source_commit)
            == opening_arguments['opening_record'], 'OPENING_CHANGED')
    path = _retain(target/'research-feedback', value)
    return dict(status='BAR_STRATEGY_FEEDBACK_RETAINED', feedback_sha256=value['result_hash'],
        artifact_sha256=_digest(value), path=str(path), cash_reconciled=True,
        submission_enabled=False, economic_authority=False)
