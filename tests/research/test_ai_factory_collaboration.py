import pytest


def test_cached_role_result_rejects_a_changed_source_receipt(tmp_path):
    from tools.ai_factory_collaboration import bounded_proposal
    from tools.btc_factory_research import verified,seal
    import json
    kwargs={'protocol':'SINGLE_V1','output':tmp_path,'options':{},
        'propose_fn':lambda *a,**k:{'candidate_id':0,'model':'fixture'}}
    bounded_proposal('BTC',[0,1],[],**kwargs)
    path=tmp_path/'roles/0.json';record=verified(path);record.pop('result_hash')
    record['proposal']['candidate_id']=1;path.write_text(json.dumps(seal(record)))
    with pytest.raises(ValueError,match='role'):
        bounded_proposal('BTC',[0,1],[],**kwargs)


def test_three_roles_share_one_candidate_budget_and_inner_context(tmp_path):
    from tools.ai_factory_collaboration import bounded_proposal
    calls=[]
    def propose(objective,remaining,history,**options):
        calls.append((list(remaining),history,options))
        return {'candidate_id':[1,2,1][len(calls)-1],'model':'local-qwen',
            'request_hash':str(len(calls))*64,'response_hash':'b'*64,'elapsed_ms':4,'usage':{'total_tokens':12}}
    result=bounded_proposal('BTC',[0,1,2],[],protocol='THREE_ROLES_V1',output=tmp_path,
        options={'research_context':{'context_hash':'a'*64}},propose_fn=propose)
    assert result['candidate_id']==1 and len(calls)==3
    assert [c[2]['role'] for c in calls]==['PROPOSE','COUNTER_PROPOSE','SYNTHESIZE']
    assert all(c[0]==[0,1,2] for c in calls)
    assert calls[1][2]['peer_candidates']==[1] and calls[2][2]['peer_candidates']==[1,2]
    assert result['usage']['total_tokens']==36 and len(result['roles'])==3
    assert bounded_proposal('BTC',[0,1,2],[],protocol='THREE_ROLES_V1',output=tmp_path,
        options={'research_context':{'context_hash':'a'*64}},propose_fn=propose)==result
    assert len(calls)==3


def test_failed_role_is_retained_without_silent_fallback_or_retry(tmp_path):
    from tools.ai_factory_collaboration import bounded_proposal
    calls=[]
    def fail(*args,**kwargs):calls.append(1);raise ValueError('provider failed')
    for _ in range(2):
        with pytest.raises(ValueError,match='role'):
            bounded_proposal('BTC',[0],[],protocol='THREE_ROLES_V1',output=tmp_path,options={},propose_fn=fail)
    assert len(calls)==1
    assert (tmp_path/'roles/0.json').exists()


def test_interrupted_role_remains_consumed_and_does_not_repeat_model_call(tmp_path):
    from tools.ai_factory_collaboration import bounded_proposal
    def stop(*args,**kwargs):raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        bounded_proposal('BTC',[0],[],protocol='SINGLE_V1',output=tmp_path,options={},propose_fn=stop)
    with pytest.raises(ValueError,match='role'):
        bounded_proposal('BTC',[0],[],protocol='SINGLE_V1',output=tmp_path,options={},propose_fn=lambda *a,**k:pytest.fail('must not retry'))


def test_resume_does_not_reset_total_role_time_budget(tmp_path,monkeypatch):
    from tools import ai_factory_collaboration as module
    clock=[0.];calls=[];interrupted=[False];real_write=module.write_new
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    def write(path,value):
        real_write(path,value)
        if path.name=='0.json' and not interrupted[0]:interrupted[0]=True;raise KeyboardInterrupt()
    monkeypatch.setattr(module,'write_new',write)
    def propose(*args,**kwargs):
        calls.append(kwargs['timeout_seconds']);clock[0]+=[20,30,10][len(calls)-1]
        return {'candidate_id':0,'model':'fixture','elapsed_ms':1}
    with pytest.raises(KeyboardInterrupt):
        module.bounded_proposal('BTC',[0],[],protocol='THREE_ROLES_V1',output=tmp_path,options={},propose_fn=propose)
    clock[0]=500
    module.bounded_proposal('BTC',[0],[],protocol='THREE_ROLES_V1',output=tmp_path,options={},propose_fn=propose)
    assert calls==[30,30,10]
