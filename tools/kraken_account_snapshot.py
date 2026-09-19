"""Human-operated Kraken balance/open-order export; never run by an AI with real keys.

Credentials are entered through getpass, held in memory, never exported. The
transport is structurally limited to two read-only account endpoints. This is
not a live adapter, credential installer, or trading-approval mechanism.
"""
import argparse
import base64
import fcntl
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import urllib.parse
import urllib.request

from services.data import kraken_account as model
from services.data.kraken_public import require
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools.kraken_public_capture import NoRedirect, atomic_json, digest

ENDPOINTS=('BalanceEx','OpenOrders')


def private_root(root):
    root=Path(root).absolute();require(root.resolve()==root,'KRAKEN_ACCOUNT_STATE_SYMLINK')
    root.mkdir(parents=True,mode=0o700,exist_ok=True)
    info=root.stat()
    require(info.st_uid==os.getuid() and stat.S_IMODE(info.st_mode)&0o077==0,'KRAKEN_ACCOUNT_STATE_PERMISSIONS')
    return root


def transport(url,body,headers):
    require(url in tuple('https://api.kraken.com/0/private/'+name for name in ENDPOINTS),'KRAKEN_READ_ONLY_ENDPOINT')
    request=urllib.request.Request(url,data=body,headers=headers,method='POST')
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=15) as response:
        require(response.status==200 and response.geturl()==url,'KRAKEN_ACCOUNT_HTTP')
        payload=response.read(model.MAX_BYTES+1)
    require(len(payload)<=model.MAX_BYTES,'KRAKEN_ACCOUNT_RESPONSE_SIZE')
    return payload


class ReadOnlyClient:
    def __init__(self,key,secret,root,*,transport=None,clock_ms=None):
        require(isinstance(key,str) and bool(re.fullmatch(r'[A-Za-z0-9+/=]{16,256}',key)),'KRAKEN_KEY_FORMAT')
        self._secret=base64.b64decode(secret,validate=True)
        require(16<=len(self._secret)<=256,'KRAKEN_SECRET_FORMAT')
        self._key=key;self.root=private_root(root)
        self.account_ref_sha256=hashlib.sha256(key.encode()).hexdigest()
        self._transport=transport or globals()['transport']
        self._clock=clock_ms or (lambda:time.time_ns()//1_000_000)

    def read(self,endpoint,params):
        require(endpoint in ENDPOINTS and params=={'rebase_multiplier':'base'},'KRAKEN_READ_ONLY_ENDPOINT')
        # Serialize allocation AND request dispatch, not only nonce increment.
        lock_path=self.root/(self.account_ref_sha256+'.lock')
        fd=os.open(lock_path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            path=self.root/(self.account_ref_sha256+'.nonce.json')
            require(not path.is_symlink(),'KRAKEN_NONCE_SYMLINK')
            previous=0
            if path.exists():
                record=load_json_without_duplicate_keys(path.read_bytes())
                require(isinstance(record,dict) and set(record)=={'nonce'} and type(record['nonce']) is int,'KRAKEN_NONCE_STATE')
                previous=record['nonce']
                require(0<previous<2**63,'KRAKEN_NONCE_STATE')
            now=self._clock();require(type(now) is int and 0<now<2**63,'KRAKEN_NONCE_CLOCK')
            nonce=max(previous+1,now);require(0<nonce<2**63,'KRAKEN_NONCE_RANGE')
            atomic_json(path,{'nonce':nonce})
            body=urllib.parse.urlencode({'nonce':str(nonce),**params}).encode()
            uri='/0/private/'+endpoint
            message=uri.encode()+hashlib.sha256(str(nonce).encode()+body).digest()
            signature=base64.b64encode(hmac.new(self._secret,message,hashlib.sha512).digest()).decode()
            return self._transport('https://api.kraken.com'+uri,body,
                {'API-Key':self._key,'API-Sign':signature,'Content-Type':'application/x-www-form-urlencoded',
                 'Accept':'application/json','User-Agent':'QUANTOS-account-observation/1'})


def collect(client,*,clock_ms=None):
    clock_ms=clock_ms or (lambda:time.time_ns()//1_000_000)
    reads=[];started=clock_ms()
    try:
        for endpoint in ('BalanceEx','OpenOrders','BalanceEx','OpenOrders'):
            params={'rebase_multiplier':'base'};sent=clock_ms();body=client.read(endpoint,params);received=clock_ms()
            require(isinstance(body,bytes) and len(body)<=model.MAX_BYTES,'KRAKEN_ACCOUNT_RESPONSE_SIZE')
            model.payload(body.decode('utf-8'))
            reads.append({'endpoint':endpoint,'params':params,'requested_at_ms':sent,'received_at_ms':received,
                          'body':body.decode('utf-8'),'account_ref_sha256':client.account_ref_sha256})
        report=model.assess(reads,as_of_ms=clock_ms())
    except (OSError,ValueError,KeyError,TypeError) as exc:
        report={'schema':'KRAKEN_ACCOUNT_OBSERVATION_V1','status':'FAILED','observed_at_ms':clock_ms(),
                'started_at_ms':started,'account_ref_sha256':client.account_ref_sha256,
                'failure':{'code':'ACCOUNT_READ_FAILED','error_type':type(exc).__name__,'endpoint':endpoint},
                'submission_enabled':False,'authoritative':False}
    report['result_hash']=digest(report)
    return report


def save(root,report):
    require(report.get('result_hash')==digest({k:v for k,v in report.items() if k!='result_hash'}),'KRAKEN_ACCOUNT_REPORT_HASH')
    require(report.get('schema')=='KRAKEN_ACCOUNT_OBSERVATION_V1' and report.get('submission_enabled') is False and
            report.get('authoritative') is False,'KRAKEN_ACCOUNT_REPORT_BOUNDARY')
    root=private_root(root)
    fd=os.open(root/'.export.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        for name in ('observations','latest.json','account-reference.json'):require(not (root/name).is_symlink(),'KRAKEN_ACCOUNT_EXPORT_SYMLINK')
        identity={'account_ref_sha256':report['account_ref_sha256']}
        path=root/'account-reference.json'
        if path.exists():require(load_json_without_duplicate_keys(path.read_bytes())==identity,'KRAKEN_ACCOUNT_NAMESPACE_CHANGED')
        else:atomic_json(path,identity)
        directory=root/'observations';directory.mkdir(mode=0o700,exist_ok=True)
        target=directory/(report['result_hash']+'.json')
        if target.exists():require(not target.is_symlink() and load_json_without_duplicate_keys(target.read_bytes())==report,'KRAKEN_ACCOUNT_EXPORT_CONFLICT')
        else:atomic_json(target,report)
        path=root/'latest.json'
        if not path.exists() or load_json_without_duplicate_keys(path.read_bytes())['observed_at_ms']<report['observed_at_ms']:
            atomic_json(path,report)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root',required=True,type=Path)
    args=parser.parse_args(argv)
    require(sys.stdin.isatty(),'INTERACTIVE_OPERATOR_REQUIRED')
    root=private_root(args.state_root)
    print('仅查询余额与挂单；不下单、不撤单、不转账。凭证仅保留在本进程内存。')
    key=getpass.getpass('Kraken API public key（不回显）: ')
    secret=getpass.getpass('Kraken API secret（不回显）: ')
    client=ReadOnlyClient(key,secret,root);key=secret=None
    report=collect(client);save(root,report)
    print(json.dumps({'status':report['status'],'report':str(root/'latest.json'),'submission_enabled':False}))
    return 0 if report['status']=='OBSERVED_STABLE' else 3


if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,TypeError):
        print('ACCOUNT_EXPORT_FAILED: check input format, private state permissions and read-only API access.',file=sys.stderr)
        raise SystemExit(3)
