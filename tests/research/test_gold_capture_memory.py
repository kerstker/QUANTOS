import json
import time

import pytest

from quantos.local_artifacts import with_result_hash
from services.research.ai_factory_store import FactoryStore
from tests.research.test_gold_factory_campaign import policy
from tests.research.test_gold_input_collection import setup
from tests.research.test_ai_factory_provider import LOCAL, Transport
from tools.gold_capture_design import propose_design
from tools.gold_capture_memory import collection_memory
from tools.gold_ai_origin import encode


def test_capture_facts_reach_next_design_and_frozen_sources_are_rechecked(tmp_path,monkeypatch):
    tool,base,calls=setup(tmp_path,monkeypatch)
    config=policy((base/'gold-inputs/1.json').read_bytes())
    with FactoryStore(base/'factory.db') as store:
        prior=store.register_collection(config,input_id=2,seconds=30,operator='test')
        current=store.register_collection(dict(config,campaign_id=2),input_id=3,seconds=30,
            operator='test',decision_policy='AI_DESIGNED_WATERMARK_V1')
    tool.run_collect({'attempt':2,'template_id':1,'seconds':30})
    now=time.time_ns()//1000000
    memory=collection_memory(base,current,[prior],now=now)
    row=memory['records'][0]
    assert row['capture_status']=='CAPTURED_PUBLIC_PREFIX'
    assert row['unique_aggregates']==6
    assert row['first_boundary_delay_ms'] is not None
    transport=Transport({'window_ms':5000,'max_age_ms':1000,'window_count':3})
    receipt=propose_design(seconds=30,base_rule={'window_ms':1000,'max_age_ms':100},
                          capture_memory=memory,environ=LOCAL,transport=transport)
    assert receipt['schema']=='GOLD_AI_CAPTURE_DESIGN_V2'
    assert json.loads(transport.request['messages'][1]['content'])['prior_capture_diagnostics']==memory
    area=base/'gold-collections/3';area.mkdir()
    (area/'design-memory.json').write_bytes(encode(memory))
    assert collection_memory(base,current,[prior],now=now+100)==memory
    assert collection_memory(base,current,[],now=now+100) is None
    (base/'gold-collections/2/trades.json').write_text('{}')
    with pytest.raises(ValueError):collection_memory(base,current,[prior],now=now+100)
    assert calls==[30]


@pytest.mark.parametrize('change',[{'price':'100'},{'strategy_pnl':'1'},{'ended_at_ms':10**16}])
def test_unscoped_or_future_capture_facts_never_reach_model(change):
    row={'input_id':2,'request_hash':'a'*64,'files':{},'capture_status':'NO_CAPTURE',
         **{k:None for k in ('ended_at_ms','duration_ms','unique_aggregates','window_ms',
             'max_age_ms','window_count','first_boundary_delay_ms')},**change}
    memory=with_result_hash({'schema':'GOLD_CAPTURE_MEMORY_V1','as_of_ms':10,'records':[row],'authoritative':False})
    transport=Transport({'window_ms':5000,'max_age_ms':1000,'window_count':3})
    with pytest.raises(ValueError):
        propose_design(seconds=30,base_rule={'window_ms':1000,'max_age_ms':100},
                       capture_memory=memory,environ=LOCAL,transport=transport)
    assert transport.request is None


def test_invalid_prior_stream_is_a_diagnostic_not_a_permanent_design_block(tmp_path,monkeypatch):
    from tools.gold_public_capture import PublicCapture, CaptureError
    tool,base,_=setup(tmp_path,monkeypatch)
    original=tool.collect_pair
    async def invalid(*args):
        pair=await original(*args)
        capture=PublicCapture(started_at_ms=100)
        with pytest.raises(CaptureError):capture.ingest('{"unexpected":true}',received_at_ms=101)
        pair['trades']=capture.finish(ended_at_ms=102,reason='VALIDATION_FAILED')
        return pair
    monkeypatch.setattr(tool,'collect_pair',invalid)
    config=policy((base/'gold-inputs/1.json').read_bytes())
    with FactoryStore(base/'factory.db') as store:
        prior=store.register_collection(config,input_id=2,seconds=30,operator='test')
        current=store.register_collection(dict(config,campaign_id=2),input_id=3,seconds=30,
            operator='test',decision_policy='AI_DESIGNED_WATERMARK_V1')
    with pytest.raises(ValueError):tool.run_collect({'attempt':2,'template_id':1,'seconds':30})
    retained=(base/'gold-collections/2/trades.json').read_bytes()
    assert not (base/'gold-inputs/2.json').exists()
    memory=collection_memory(base,current,[prior],now=time.time_ns()//1000000)
    row=memory['records'][0]
    assert row['capture_status']=='UNVERIFIABLE_CAPTURE'
    assert row['unique_aggregates'] is None and row['first_boundary_delay_ms'] is None
    transport=Transport({'window_ms':5000,'max_age_ms':1000,'window_count':3})
    proposal=propose_design(seconds=30,base_rule={'window_ms':1000,'max_age_ms':100},
                           capture_memory=memory,environ=LOCAL,transport=transport)
    assert proposal['capture_memory']==memory
    assert 'unexpected' not in json.dumps(transport.request)
    assert (base/'gold-collections/2/trades.json').read_bytes()==retained
