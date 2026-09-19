import base64
import json

import pytest

from tools import kraken_account_snapshot as tool
from tests.data.test_kraken_account import observations, NOW


def test_only_readonly_signed_requests_and_monotonic_nonce_survive_restart(tmp_path):
    calls=[]
    def transport(url,body,headers):calls.append((url,body,headers));return b'{"error":[],"result":{}}'
    key=base64.b64encode(b'public fixture only').decode();secret=base64.b64encode(b'non-secret fixture only').decode()
    client=tool.ReadOnlyClient(key,secret,tmp_path,transport=transport,clock_ms=lambda:NOW)
    for endpoint in ('BalanceEx','OpenOrders'):client.read(endpoint,{'rebase_multiplier':'base'})
    restarted=tool.ReadOnlyClient(key,secret,tmp_path,transport=transport,clock_ms=lambda:NOW-100)
    restarted.read('BalanceEx',{'rebase_multiplier':'base'})
    from urllib.parse import parse_qs
    nonces=[int(parse_qs(body.decode())['nonce'][0]) for _,body,_ in calls]
    assert nonces==[NOW,NOW+1,NOW+2]
    for endpoint,params in [('AddOrder',{}),('Withdraw',{}),('BalanceEx',{'rebase_multiplier':'base','pair':'AAPLxUSD'})]:
        with pytest.raises(ValueError):client.read(endpoint,params)
    assert len(calls)==3 and all('/0/private/' in url for url,_,_ in calls)
    for p in tmp_path.iterdir():
        if p.is_file():assert key not in p.read_text() and secret not in p.read_text()


def test_collect_and_export_only_allowlisted_account_fields(tmp_path):
    reads=observations();sequence=iter(reads);ticks=iter(range(NOW,NOW+100))
    class Reader:
        account_ref_sha256='a'*64
        def read(self,endpoint,params):
            row=next(sequence);assert endpoint==row['endpoint'];return row['body'].encode()
    result=tool.collect(Reader(),clock_ms=lambda:next(ticks))
    assert result['status']=='OBSERVED_STABLE' and len(result['open_orders'])==1
    tool.save(tmp_path,result)
    content=(tmp_path/'latest.json').read_text()
    assert 'API-Key' not in content and 'API-Sign' not in content and '"body"' not in content
    assert json.loads(content)['cost_basis_status']=='UNKNOWN'
    assert (tmp_path/'latest.json').stat().st_mode & 0o777==0o600


def test_failure_exports_no_transport_details_or_secrets(tmp_path):
    class Reader:
        account_ref_sha256='a'*64
        def read(self,*_):raise OSError('sensitive transport detail')
    result=tool.collect(Reader(),clock_ms=lambda:NOW)
    assert result['status']=='FAILED' and 'sensitive' not in json.dumps(result)
    tool.save(tmp_path,result)
    assert json.loads((tmp_path/'latest.json').read_text())['status']=='FAILED'


def test_main_refuses_unattended_credential_prompt(monkeypatch):
    monkeypatch.setattr(tool.sys.stdin,'isatty',lambda:False)
    with pytest.raises(ValueError,match='INTERACTIVE_OPERATOR_REQUIRED'):tool.main(['--state-root','/unused'])


def test_api_rejection_stops_before_repeating_authenticated_requests():
    class Reader:
        account_ref_sha256='a'*64
        calls=0
        def read(self,*_):
            self.calls+=1
            return b'{"error":["EAPI:Invalid nonce"],"result":{}}'
    client=Reader();report=tool.collect(client,clock_ms=lambda:NOW)
    assert client.calls==1 and report['status']=='FAILED'
    assert report['failure']['endpoint']=='BalanceEx'
