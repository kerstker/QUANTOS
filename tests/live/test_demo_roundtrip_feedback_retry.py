"""Feedback orchestration: resolved empty IOC reductions remain in the chain.

Resolution and cash adapters are controlled here; original receipt/ledger
integration is covered by test_gold_demo_receipt_binding.
"""
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest

from live.execution import demo_research_feedback as feedback
from live.execution.demo_batch_risk import _digest


@pytest.mark.parametrize('damage', [None, 'unknown', 'empty_open', 'empty_reductions', 'cash', 'nonflat', 'changed'])
def test_resolved_unfilled_reduction_does_not_interrupt_feedback(monkeypatch, tmp_path, damage):
    snapshot={'reservations':[], 'wire':[]}
    dispatch=SimpleNamespace(evidence_snapshot=lambda: snapshot)
    batches=[];plans={};proofs={}
    for index,quantity in enumerate(('1','0','1')):
        key=str(index+1)*64
        prepared={'reservation_sha256':key,'proposal_sha256':str(index+4)*64}
        batches.append(dict(prepared=prepared,start_ms=100+index*10,end_ms=110+index*10))
        proofs[key]={'strategy_id':'original-strategy','context':{'account_ref_sha256':'a'*64},
                     'orders':[{'symbol':'XAUUSDT','reduceOnly':index>0}]}
        plans[key]={'status':'READY_FOR_OMS_PROJECTION','orders':[{
            'client_order_id':f'original-{index}', 'filled_quantity':quantity,
            'terminal_status':'EXPIRED' if index==1 else 'FILLED'}],
            'journal_path':str(tmp_path/f'{index}.db'),'manifest_hash':str(index+7)*64,
            'journal_tail':str(index+1)*64,'resolution_at_ms':140+index,
            'dispatch_evidence_sha256':_digest(snapshot)}
    if damage=='unknown':plans['2'*64]['status']='UNRESOLVED'
    if damage=='empty_open':plans['1'*64]['orders'][0]['filled_quantity']='0'
    if damage=='empty_reductions':plans['3'*64]['orders'][0]['filled_quantity']='0'
    if damage=='changed':snapshot['wire'].append({'late':'unknown'})
    cash={'status':'MATCHED_REPORTED_INPUTS','observed_at_ms':150,
          'positions':{'XAUUSDT':{'quantity':'1' if damage=='nonflat' else '0'}},
          'reconciliation':{'fee':'0.02'},'canonical_wallet':'4999.98','wallet_residual':'0'}
    if damage=='cash':cash['status']='UNRESOLVED'
    monkeypatch.setattr(feedback,'_load_proof',lambda p:proofs[p['reservation_sha256']])
    monkeypatch.setattr(feedback,'prepare_resolution',lambda p,**kw:deepcopy(plans[p['reservation_sha256']]))
    monkeypatch.setattr(feedback,'DemoExecutionJournal',lambda p,**kw:nullcontext(p))
    observed=[]
    def project(journals,capture):
        observed.extend(journals)
        return deepcopy(cash)
    monkeypatch.setattr(feedback,'project_cash_chain',project)
    def build():
        return feedback.build_roundtrip_feedback(batches,dispatch=dispatch,stream=object(),capture={},
            source_commit='a'*40,output_directory=tmp_path,as_of_ms=160)
    if damage:
        reason={'unknown':'ORDER_EVIDENCE_UNRESOLVED','empty_open':'ROUNDTRIP_FILL_REQUIRED',
                'empty_reductions':'ROUNDTRIP_FILL_REQUIRED',
                'cash':'CASH_EVIDENCE_UNRESOLVED','nonflat':'ROUNDTRIP_NOT_FLAT',
                'changed':'DISPATCH_CHANGED'}[damage]
        with pytest.raises(ValueError,match=reason):build()
        return
    result=build()
    assert [row['reservation_sha256'] for row in result['batches']]==[str(i)*64 for i in (1,2,3)]
    assert [row['filled_quantity'] for row in result['orders']]==['1','0','1']
    assert result['orders'][1]['terminal_status']=='EXPIRED'
    assert observed==[str(tmp_path/f'{i}.db') for i in range(3)]
    assert result['position_flat'] and result['cash_reconciliation']['fee']=='0.02'
    assert not result['submission_enabled'] and not result['economic_eligible']
    assert not result['research_trigger_enabled'] and result['strategy_pnl'] is None
