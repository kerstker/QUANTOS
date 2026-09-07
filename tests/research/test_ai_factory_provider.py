import json
import pytest
from tools.ai_factory_provider import propose, provider_status
from tools.external_model_provider import TransportResponse

ENV={'QUANTOS_FACTORY_ENDPOINT':'https://example.test/v1/chat/completions','QUANTOS_FACTORY_MODEL':'test-model','QUANTOS_FACTORY_API_KEY':'private-test-token'}
class Transport:
    def __init__(self, content): self.content=content; self.request=None
    def post(self, **kwargs):
        self.request=json.loads(kwargs['body'])
        return TransportResponse(200,{},json.dumps({'choices':[{'message':{'content':json.dumps(self.content)}}]}).encode(),b'cert')

def test_disconnected_is_explicit():
    assert provider_status({})['status']=='NOT_CONFIGURED'
    with pytest.raises(ValueError,match='not configured'): propose('BTC',[0,1],[],environ={})

def test_typed_proposal_and_redacted_receipt():
    t=Transport({'candidate_id':1,'reason':'Compare linear model'})
    result=propose('BTC',[0,1],[{'candidate_id':0,'score':-1}],environ=ENV,transport=t)
    assert result['candidate_id']==1
    assert 'private-test-token' not in json.dumps(result)
    assert len(result['request_hash'])==64
    assert 'outer' not in json.dumps(t.request)

@pytest.mark.parametrize('content',[{'candidate_id':99,'reason':'x'},{'candidate_id':True,'reason':'x'},{'candidate_id':1,'reason':'x','code':'print(1)'}])
def test_unknown_or_executable_proposals_rejected(content):
    with pytest.raises(ValueError): propose('BTC',[0,1],[],environ=ENV,transport=Transport(content))

def test_no_outer_results_can_be_sent():
    with pytest.raises(ValueError):propose('BTC',[0,1],[{'outer_score':12}],environ=ENV,transport=Transport({}))
