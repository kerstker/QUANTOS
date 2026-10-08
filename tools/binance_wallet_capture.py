"""User-authorized read-only wallet capture. Fixed queries only; no transfers."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from services.research.gold_trade_observation import load_document,_require
from services.research.binance_wallet_view import normalize_wallet
from tools.gold_account_capture import NoRedirect,permission_diagnostics
from tools.gold_credentials import get_credentials
from tools.gold_account_monitor import atomic_json
from tools.gold_trade_observation import _write_private,_encode

ROUTES={
 'permission':('https://api.binance.com','/sapi/v1/account/apiRestrictions','GET'),
 'futures':('https://fapi.binance.com','/fapi/v3/account','GET'),
 'spot':('https://api.binance.com','/api/v3/account','GET'),
 # Binance documents this POST as Funding Wallet (USER_DATA), a read query.
 'funding':('https://api.binance.com','/sapi/v1/asset/get-funding-asset','POST'),
}


def reader(key,secret,*,opener=None,clock_ms=None):
    opener=opener or urllib.request.build_opener(NoRedirect());clock_ms=clock_ms or (lambda:time.time_ns()//1000000)
    def read(kind):
        _require(kind in ROUTES,'WALLET_ENDPOINT_NOT_ALLOWED')
        host,path,method=ROUTES[kind]
        q=urllib.parse.urlencode({'timestamp':clock_ms(),'recvWindow':5000})
        q+='&signature='+hmac.new(secret.encode(),q.encode(),hashlib.sha256).hexdigest()
        request=urllib.request.Request(host+path+('?' +q if method=='GET' else ''),
            data=q.encode() if method=='POST' else None,
            headers={'X-MBX-APIKEY':key,'Content-Type':'application/x-www-form-urlencoded'},method=method)
        try:
            with opener.open(request,timeout=10) as response:
                raw=response.read(2000001);_require(response.status==200 and len(raw)<=2000000,'WALLET_RESPONSE_INVALID')
            return load_document(raw)
        except urllib.error.HTTPError as exc:
            exc.close();raise ValueError('WALLET_REQUEST_REJECTED') from None
        except Exception:raise ValueError('WALLET_REQUEST_FAILED') from None
    return read


def capture(read,*,clock_ms=None):
    clock_ms=clock_ms or (lambda:time.time_ns()//1000000)
    # Every collection refreshes the read-only+IP-restricted permission guard.
    try:accepted=permission_diagnostics(_encode(read('permission')))['status']=='ACCEPTED'
    except Exception:accepted=False
    result={'schema':'BINANCE_WALLET_OBSERVATIONS_V1','observed_at_ms':clock_ms(),'accounts':{},'submission_enabled':False}
    for kind in ('futures','spot','funding'):
        try:
            _require(accepted,'READ_ONLY_PERMISSION_REQUIRED')
            rows=normalize_wallet(kind,read(kind))
            value={'status':'OBSERVED','observed_at_ms':clock_ms(),'balances':rows}
        except Exception:value={'status':'UNAVAILABLE','observed_at_ms':clock_ms(),'balances':[]}
        result['accounts'][kind]=value
    result['observed_at_ms']=clock_ms()
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',action='store_true');p.add_argument('--state-dir',type=Path);args=p.parse_args()
    if not args.run:print('{"status":"OFFLINE_DEFAULT","submission_enabled":false}');return 0
    if args.state_dir is None:p.error('--state-dir required')
    try:
        state=args.state_dir.absolute();_require(state.resolve()==state and state.is_dir(),'INVALID_STATE_ROOT')
        key,secret=get_credentials(prompt=lambda:(_ for _ in ()).throw(ValueError('CREDENTIAL_FILE_UNAVAILABLE')))
        identity=load_document((state/'identity.json').read_bytes())
        expected=hmac.new(key.encode(),identity['salt'].encode(),hashlib.sha256).hexdigest()
        _require(hmac.compare_digest(expected,identity['key_binding']),'ACCOUNT_BINDING_MISMATCH')
        result=capture(reader(key,secret));del key,secret
        archive=state/'wallet-observations';archive.mkdir(mode=0o700,exist_ok=True);_require(not archive.is_symlink(),'LINKED_ARCHIVE')
        _write_private(archive/(str(result['observed_at_ms'])+'-'+uuid.uuid4().hex+'.json'),_encode(result))
        atomic_json(state/'account-wallets.json',result)
        print(json.dumps({'status':'RECORDED','accounts':{k:v['status'] for k,v in result['accounts'].items()},'submission_enabled':False}),flush=True)
        return 0
    except Exception:print('{"status":"WALLET_CAPTURE_FAILED","submission_enabled":false}');return 78

if __name__=='__main__':raise SystemExit(main())
