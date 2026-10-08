import json
import pytest
from tools.binance_wallet_capture import capture,reader
from tools.gold_account_capture import WRITE_PERMISSIONS


def test_read_only_guard_prevents_all_wallet_queries_and_failures_are_not_zero():
 calls=[]
 def bad(kind):calls.append(kind);return {'enableReading':True}
 d=capture(bad,clock_ms=lambda:1000)
 assert calls==['permission']
 assert all(v['status']=='UNAVAILABLE' and v['balances']==[] for v in d['accounts'].values())
 def read(kind):
  if kind=='permission':return {**dict.fromkeys(WRITE_PERMISSIONS,False),'enableReading':True,'ipRestrict':True}
  if kind=='spot':raise ValueError('private message')
  if kind=='futures':return {'assets':[]}
  return []
 d=capture(read,clock_ms=lambda:1000)
 assert d['accounts']['spot']['status']=='UNAVAILABLE'
 assert d['accounts']['funding']['status']=='OBSERVED'
 assert 'private' not in json.dumps(d)


def test_only_documented_funding_read_query_can_use_post():
 requests=[]
 class Response:
  status=200
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def read(self,*a):return b'[]'
 class Opener:
  def open(self,r,timeout):requests.append(r);return Response()
 read=reader('fake','fake',opener=Opener(),clock_ms=lambda:1000)
 read('funding');read('spot')
 assert requests[0].method=='POST' and requests[0].full_url=='https://api.binance.com/sapi/v1/asset/get-funding-asset'
 assert requests[1].method=='GET' and requests[1].full_url.startswith('https://api.binance.com/api/v3/account?')
 for kind in ('transfer','withdraw','order','https://bad'):
  with pytest.raises(ValueError):read(kind)
