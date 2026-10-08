"""Human-operated, one-product fee observation; no order submission capability."""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import time

from services.data import kraken_fees as fees
from services.data.kraken_account import MAX_BYTES
from services.data.kraken_public import require
from tools.kraken_account_snapshot import ReadOnlyClient, private_root
from tools.kraken_inventory_report import load, sealed
from tools import kraken_public_capture as capture

digest=capture.digest


def _market(market, now):
    raw=capture.validate(market)
    require(raw['status']=='CAPTURED' and type(now) is int and
            0<=now-raw['completed_at_ms']<=120000,'KRAKEN_FEE_MARKET_STALE')
    return raw


def collect(client, market, *, clock_ms=None):
    clock_ms=clock_ms or (lambda:time.time_ns()//1_000_000)
    raw=_market(market,clock_ms());params=fees.params(raw['instrument'])
    sent=clock_ms();body=client.read('TradeVolume',params);received=clock_ms()
    require(isinstance(body,bytes) and len(body)<=MAX_BYTES,'KRAKEN_FEE_RESPONSE_SIZE')
    read={'endpoint':'TradeVolume','params':params,'requested_at_ms':sent,'received_at_ms':received,
          'account_ref_sha256':client.account_ref_sha256,'body':body.decode('utf-8')}
    now=clock_ms();_market(raw,now)
    report=fees.assess(read,spec=raw['instrument'],as_of_ms=now)
    evidence={'schema':'KRAKEN_FEE_EVIDENCE_V1','market_capture_hash':raw['result_hash'],
              'read':read,'report':report,'submission_enabled':False,'authoritative':False}
    evidence['result_hash']=digest(evidence)
    return evidence


def replay(evidence, *, market, account_ref, as_of_ms):
    sealed(evidence);raw=_market(market,as_of_ms)
    require(evidence['schema']=='KRAKEN_FEE_EVIDENCE_V1' and
            evidence['market_capture_hash']==raw['result_hash'] and
            evidence['read']['account_ref_sha256']==account_ref,'KRAKEN_FEE_EVIDENCE_BINDING')
    prior=fees.assess(evidence['read'],spec=raw['instrument'],as_of_ms=evidence['report']['as_of_ms'])
    require(prior==evidence['report'],'KRAKEN_FEE_EVIDENCE_REPLAY')
    return fees.assess(evidence['read'],spec=raw['instrument'],as_of_ms=as_of_ms)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root',type=Path,required=True)
    parser.add_argument('--market-capture',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    require(sys.stdin.isatty(),'INTERACTIVE_OPERATOR_REQUIRED')
    require(not args.output.exists() and not args.output.is_symlink(),'KRAKEN_FEE_OUTPUT_EXISTS')
    market=_market(load(args.market_capture),time.time_ns()//1_000_000)
    root=private_root(args.state_root)
    print('仅查询一个产品的账户费率；不下单。凭证不写入文件。')
    key=getpass.getpass('Kraken API public key（不回显）: ')
    secret=getpass.getpass('Kraken API secret（不回显）: ')
    client=ReadOnlyClient(key,secret,root);key=secret=None
    evidence=collect(client,market)
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(evidence,stream,sort_keys=True,allow_nan=False);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    print(json.dumps({'status':evidence['report']['status'],'output':str(args.output),'submission_enabled':False}))
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,KeyError,TypeError) as exc:
        print('KRAKEN_FEE_OBSERVATION_FAILED: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(3)
