"""Rebuild technical research feedback from original Demo execution evidence.

This internal executor-side exporter binds original orders and cash observations.
Optional trial identity is content-bound to every signed strategy; it supplies
neither source authentication nor economic admission.
"""
from copy import deepcopy
from contextlib import ExitStack
from decimal import Decimal

from live.execution.demo_batch_risk import _digest
from live.execution.demo_prepared_execution import _load_proof
from live.execution.demo_oms_reconciliation import prepare_resolution
from services.research.demo_execution_journal import DemoExecutionJournal
from services.research.demo_cash_chain import project_cash_chain


def build_feedback(prepared, *, dispatch, stream, capture, start_ms, end_ms,
                   source_commit, output_directory, as_of_ms, research_identity=None):
    return _build([dict(prepared=prepared,start_ms=start_ms,end_ms=end_ms)],dispatch=dispatch,
        stream=stream,capture=capture,source_commit=source_commit,output_directory=output_directory,
        as_of_ms=as_of_ms,research_identity=research_identity,roundtrip=False)


def build_roundtrip_feedback(batches, *, dispatch, stream, capture, source_commit,
                            output_directory, as_of_ms, research_identity=None):
    """Bind a flat-ended opening/reduction chain; never grant economic admission."""
    if not isinstance(batches,(list,tuple)) or not 2<=len(batches)<=32:
        raise ValueError('FEEDBACK_ROUNDTRIP_BATCH_COUNT')
    return _build(batches,dispatch=dispatch,stream=stream,capture=capture,source_commit=source_commit,
        output_directory=output_directory,as_of_ms=as_of_ms,research_identity=research_identity,roundtrip=True)


def _build(batches, *, dispatch, stream, capture, source_commit, output_directory,
           as_of_ms, research_identity, roundtrip):
    if type(as_of_ms) is not int or as_of_ms <= 0:
        raise ValueError('FEEDBACK_AS_OF_REQUIRED')
    capture=deepcopy(capture)
    proofs=[];plans=[];members=[];seen=set()
    for index,item in enumerate(batches):
        if type(item) is not dict or set(item)!={'prepared','start_ms','end_ms'}:
            raise ValueError('FEEDBACK_BATCH_FIELDS')
        prepared=item['prepared'];proof=_load_proof(prepared)
        key=prepared['reservation_sha256']
        if key in seen:raise ValueError('FEEDBACK_DUPLICATE_BATCH')
        seen.add(key)
        if proofs and (proof['strategy_id']!=proofs[0]['strategy_id'] or
            proof['context']['account_ref_sha256']!=proofs[0]['context']['account_ref_sha256'] or
            {o['symbol'] for o in proof['orders']}!={proofs[0]['orders'][0]['symbol']}):
            raise ValueError('FEEDBACK_BATCH_SCOPE')
        if roundtrip and any(o['reduceOnly'] is not (index>0) for o in proof['orders']):
            raise ValueError('FEEDBACK_OPENING_REDUCTION_SEQUENCE')
        plan=prepare_resolution(prepared,dispatch=dispatch,stream=stream,start_ms=item['start_ms'],end_ms=item['end_ms'],
            source_commit=source_commit,output_directory=output_directory,as_of_ms=as_of_ms)
        if plan['status']!='READY_FOR_OMS_PROJECTION':
            raise ValueError('FEEDBACK_ORDER_EVIDENCE_UNRESOLVED')
        # A resolved IOC reduction may expire without a fill before a later
        # authorized reduction closes the position. Keep its evidence in the
        # cash chain; only the opening must itself establish an exposure.
        if roundtrip and index==0 and not any(Decimal(o['filled_quantity'])>0 for o in plan['orders']):
            raise ValueError('FEEDBACK_ROUNDTRIP_FILL_REQUIRED')
        proofs.append(proof);plans.append(plan)
        members.append(dict(reservation_sha256=key,proposal_sha256=prepared['proposal_sha256'],
            resolution_plan_sha256=_digest(plan),manifest_hash=plan['manifest_hash'],journal_tail=plan['journal_tail']))
    if roundtrip and not any(Decimal(o['filled_quantity'])>0 for p in plans[1:] for o in p['orders']):
        raise ValueError('FEEDBACK_ROUNDTRIP_FILL_REQUIRED')
    with ExitStack() as stack:
        journals=[stack.enter_context(DemoExecutionJournal(p['journal_path'],read_only=True)) for p in plans]
        cash=project_cash_chain(journals,capture)
    if cash['status']!='MATCHED_REPORTED_INPUTS':
        raise ValueError('FEEDBACK_CASH_EVIDENCE_UNRESOLVED')
    available=max(cash['observed_at_ms'],*(p['resolution_at_ms'] for p in plans))
    if available>as_of_ms:
        raise ValueError('FEEDBACK_NOT_YET_AVAILABLE')
    snapshot=dispatch.evidence_snapshot()
    if any(_digest(snapshot)!=p['dispatch_evidence_sha256'] for p in plans):
        raise ValueError('FEEDBACK_DISPATCH_CHANGED')
    proof=proofs[0];plan=plans[0];prepared=batches[0]['prepared']
    symbol=proof['orders'][0]['symbol']
    result={'schema':'DEMO_EXECUTION_RESEARCH_FEEDBACK_V1','environment':'BINANCE_USDM_DEMO',
        'scope':'TECHNICAL_REVIEW_ONLY','strategy_id':proof['strategy_id'],'symbol':symbol,
        'account_ref_sha256':proof['context']['account_ref_sha256'],
        'reservation_sha256':prepared['reservation_sha256'],'proposal_sha256':prepared['proposal_sha256'],
        'resolution_plan_sha256':_digest(plan),'dispatch_evidence_sha256':plan['dispatch_evidence_sha256'],
        'manifest_hash':plan['manifest_hash'],'journal_tail':plan['journal_tail'],
        'cash_capture_sha256':_digest(capture),'cash_report_sha256':_digest(cash),
        'available_at_ms':available,'source_commit':source_commit,
        'orders':deepcopy([o for p in plans for o in p['orders']]),'positions':deepcopy(cash['positions']),
        'position_flat':all(Decimal(p['quantity'])==0 for p in cash['positions'].values()),
        'cash_reconciliation':deepcopy(cash['reconciliation']),
        'canonical_wallet':cash['canonical_wallet'],'wallet_residual':cash['wallet_residual'],
        'candidate_attribution':'UNRESOLVED','research_trigger_enabled':False,
        'strategy_pnl':None,'source_verified':False,'coverage_verified':False,
        'economic_eligible':False,'submission_enabled':False}
    if roundtrip:
        if not result['position_flat']:raise ValueError('FEEDBACK_ROUNDTRIP_NOT_FLAT')
        for field in members[0]:result.pop(field)
        result.update(schema='DEMO_ROUNDTRIP_RESEARCH_FEEDBACK_V1',batches=members)
    if research_identity is not None:
        if symbol!='XAUUSDT':raise ValueError('FEEDBACK_RESEARCH_MARKET_MISMATCH')
        from services.research.gold_execution_identity import validate_identity
        for member in members:
            reserved=next(r for r in snapshot['reservations'] if r['record_hash']==member['reservation_sha256'])
            identity=validate_identity(research_identity,as_of_ms=reserved['payload']['reserved_at_ms'],
                                       strategy_id=proof['strategy_id'])
        result.update(research_identity=identity,candidate_attribution='CONTENT_BOUND_TO_SIGNED_STRATEGY')
    return result|{'result_hash':_digest(result)}
