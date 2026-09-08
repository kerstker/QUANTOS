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

LOCAL={'QUANTOS_FACTORY_ENDPOINT':'http://127.0.0.1:8001/v1/chat/completions',
       'QUANTOS_FACTORY_MODEL':'qwen3.8-27b','QUANTOS_FACTORY_TRANSPORT':'LOCAL_HTTP'}

def test_local_qwen_needs_no_credential_and_disables_thinking():
    t=Transport({'candidate_id':1,'reason':'bounded comparison'})
    result=propose('BTC',[0,1],[],environ=LOCAL,transport=t)
    assert result['model']=='qwen3.8-27b'
    assert len(result['provider_id'])==64 and result['received_at_ms']>0
    assert t.request['chat_template_kwargs']=={'enable_thinking':False}
    assert t.request['response_format']['type']=='json_schema'

@pytest.mark.parametrize('endpoint',[
    'http://192.168.5.68:8001/v1/chat/completions','http://localhost:8001/v1/chat/completions',
    'http://127.0.0.1.example.com:8001/v1/chat/completions','http://2130706433:8001/v1/chat/completions',
    'http://127.0.0.1:8001/v1/chat/completions?token=x','http://u:p@127.0.0.1:8001/v1/chat/completions',
    'http://127.0.0.1:bad/v1/chat/completions',
])
def test_local_model_rejects_nonliteral_or_ambiguous_endpoints(endpoint):
    assert provider_status({**LOCAL,'QUANTOS_FACTORY_ENDPOINT':endpoint})['status']=='NOT_CONFIGURED'

def test_http_requires_explicit_local_transport():
    assert provider_status({k:v for k,v in LOCAL.items() if k!='QUANTOS_FACTORY_TRANSPORT'})['status']=='NOT_CONFIGURED'

def test_local_transport_bypasses_proxy_sends_no_key_and_does_not_follow_redirect(monkeypatch):
    from http.server import BaseHTTPRequestHandler,HTTPServer
    from threading import Thread
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            requests.append(dict(self.headers))
            self.rfile.read(int(self.headers['Content-Length']))
            if len(requests)==1:
                self.send_response(302);self.send_header('Location','http://example.test/secret');self.end_headers()
            else:
                body=json.dumps({'choices':[{'message':{'content':'{"candidate_id":0,"reason":"test"}'}}]}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=HTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        monkeypatch.setenv('HTTP_PROXY','http://127.0.0.1:1')
        env={**LOCAL,'QUANTOS_FACTORY_ENDPOINT':f'http://127.0.0.1:{server.server_port}/v1/chat/completions','QUANTOS_FACTORY_API_KEY':'must-not-leave-local-process'}
        with pytest.raises(ValueError,match='response rejected'):propose('BTC',[0],[],environ=env)
        assert propose('BTC',[0],[],environ=env)['candidate_id']==0
        assert len(requests)==2 and all('Authorization' not in r for r in requests)
    finally:server.shutdown();server.server_close();thread.join()


@pytest.mark.parametrize('endpoint',[
    'https://example.test:0/v1/chat/completions',
    'https://example.test:65536/v1/chat/completions',
    'https://user@example.test/v1/chat/completions',
    'https://example.test\\@another.test/v1/chat/completions',
    'https://example.test/v1/chat/completions#fragment',
    'https://example.test/v1/chat/completions\x00',
    'https://[bad::address]/v1/chat/completions',
])
def test_offline_endpoint_grammar_rejects_ambiguous_authorities(endpoint):
    assert provider_status({**ENV,'QUANTOS_FACTORY_ENDPOINT':endpoint})['status']=='NOT_CONFIGURED'


@pytest.mark.parametrize('endpoint',[
    'https://example.test/v1/chat/completions',
    'https://api.example.test:8443/v1/chat/completions',
    'https://[::1]:8443/v1/chat/completions',
])
def test_offline_endpoint_grammar_accepts_supported_https(endpoint):
    assert provider_status({**ENV,'QUANTOS_FACTORY_ENDPOINT':endpoint})['status']=='CONFIGURED_UNVERIFIED'


def test_offline_endpoint_grammar_supports_explicit_ipv6_loopback():
    assert provider_status({**LOCAL,'QUANTOS_FACTORY_ENDPOINT':'http://[::1]:8001/v1/chat/completions'})['status']=='CONFIGURED_UNVERIFIED'
