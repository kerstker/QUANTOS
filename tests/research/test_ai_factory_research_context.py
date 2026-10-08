import pytest
from services.research.ai_factory_store import digest


def context(now=100,scope='INNER_SEARCH',partition='inner'):
    from services.research.ai_factory_context import ResearchContext
    return ResearchContext(experiment_hash=digest('experiment'),snapshot_hash=digest('data'),
        partition_hash=digest(partition),as_of_ms=now,market_as_of_ms=80,scope=scope)


def episode(key,*,scope='INNER_SEARCH',known=90,partition='inner',market=75):
    return {'episode_id':digest(key),'experiment_hash':digest('experiment'),'snapshot_hash':digest('data'),
        'partition_hash':digest(partition),'scope':scope,'known_at_ms':known,'market_max_ms':market,
        'evidence_hash':digest(['receipt',key]),'parent_ids':[],
        'summary':{'candidate_id':0,'status':'FAILED','score':None,'reason':'NO_INCREMENT'}}


def test_context_separates_knowledge_time_market_time_and_validation_partition():
    c=context()
    assert c.allows(episode('past'))
    assert not c.allows(episode('future',known=101))
    assert not c.allows(episode('market_future',market=81))
    assert not c.allows(episode('outer',scope='OUTER_EXPLANATION'))
    assert not c.allows(episode('other_fold',partition='other'))
    assert c.cache_key!=context(101).cache_key
    assert c.cache_key!=context(partition='other').cache_key
    with pytest.raises(ValueError):context(now=-1)
    with pytest.raises(ValueError):context(now=50)


def test_append_only_episodes_keep_failures_and_future_changes_out_of_past_reads(tmp_path):
    from services.research.ai_factory_episodes import ResearchEpisodes
    path=tmp_path/'episodes.db'
    with ResearchEpisodes(path) as store:
        a=episode('a');store.append(a);before=store.read(context())
        store.append(episode('outer',scope='OUTER_EXPLANATION'));store.append(episode('b',known=101))
        assert store.read(context())==before and before[0]['summary']['status']=='FAILED'
        assert store.append(a)==store.append(a)
        with pytest.raises(ValueError,match='immutable'):store.append({**a,'summary':{'candidate_id':0,'status':'PASSED','score':10}})
        with pytest.raises(ValueError,match='parent'):store.append({**episode('orphan',known=102),'parent_ids':[digest('absent')]})
    with ResearchEpisodes(path,read_only=True) as restored:
        restored.verify();assert restored.read(context())==before
        with pytest.raises(ValueError,match='read.only'):restored.append(episode('write'))


def test_episode_chain_tampering_is_rejected(tmp_path):
    from services.research.ai_factory_episodes import ResearchEpisodes
    import sqlite3
    path=tmp_path/'episodes.db'
    with ResearchEpisodes(path) as store:store.append(episode('a'));store.append(episode('b'))
    with sqlite3.connect(path) as conn:conn.execute('UPDATE episodes SET previous_hash=? WHERE seq=2',('0'*64,))
    with ResearchEpisodes(path,read_only=True) as store:
        with pytest.raises(ValueError,match='chain'):store.verify()


def test_factory_proposals_receive_only_time_bound_inner_episode_summaries(tmp_path):
    import numpy as np
    from services.research.ai_factory import HOUR
    from services.research.ai_factory_store import FactoryStore
    from tools.ai_quant_factory import execute
    from tools.btc_factory_research import verified
    calls=[];t=np.arange(600,dtype=np.int64)*HOUR;x=np.ones((600,5));y=np.zeros(600)
    def proposal(objective,remaining,history,**kwargs):
        calls.append((history,kwargs))
        return {'candidate_id':remaining[0],'model':'fixture'}
    with FactoryStore(tmp_path/'factory.db') as store:
        store.create({'attempt':1,'mode':'ai','objective':'BTC','budget':3})
        result=execute(store,1,(t,x,y),lambda *a:{'score':0.,'net_pnl':0.,'drawdown':0.,'fills':0},
            {'input_hash':digest('data')},tmp_path,proposal=proposal)
    assert result['state']=='COMPLETED'
    assert [len(call[0]) for call in calls]==[0,1,2]
    for history,kwargs in calls:
        assert kwargs['research_context']['scope']=='INNER_SEARCH'
        assert len(kwargs['history_evidence'])==len(history)
        assert all(r['scope']=='INNER_SEARCH' for r in kwargs['history_evidence'])
    requests=list((tmp_path/'1/research-requests').glob('*.json'))
    assert len(requests)==3 and all(verified(p)['context']['context_hash'] for p in requests)
