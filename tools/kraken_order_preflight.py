"""Prepare a reviewable offline Kraken validate-only request from fresh public evidence."""
import argparse
import json
import os
from pathlib import Path
import time

from live.exchange.kraken_wire import prepare
from services.data.kraken_public import require
from tools.kraken_inventory_report import load
from tools import kraken_public_capture as capture
from tools.kraken_fee_observation import replay as replay_fees


def build(draft, market_capture, *, as_of_ms, fee_evidence=None, account_ref=None):
    raw=capture.validate(market_capture)
    require(raw['status']=='CAPTURED' and type(as_of_ms) is int and
            0<=as_of_ms-raw['completed_at_ms']<=120000,'KRAKEN_PREFLIGHT_MARKET_TIME')
    wire=prepare(draft,spec=raw['instrument'],now_ms=as_of_ms,nonce=as_of_ms)
    require((fee_evidence is None)==(account_ref is None),'KRAKEN_PREFLIGHT_FEE_ACCOUNT_REQUIRED')
    observed_fees=None
    if fee_evidence is not None:
        observed_fees=replay_fees(fee_evidence,market=raw,account_ref=account_ref,as_of_ms=as_of_ms)
    result={'schema':'KRAKEN_ORDER_PREFLIGHT_V1','as_of_ms':as_of_ms,'draft':draft,
            'draft_sha256':capture.digest(draft),'market_capture_hash':raw['result_hash'],
            'request_body':wire['body'].decode('ascii'),'request_body_sha256':wire['body_sha256'],
            'status':'OFFLINE_PROTOCOL_CHECKED','venue_validated':False,'submission_enabled':False,'authoritative':False,
            'blockers':['ORDER_QUANTITY_SEMANTICS_UNVERIFIED','ACCOUNT_FEE_AND_FUNDS_NOT_BOUND',
                        'HUMAN_MANDATE_AND_RISK_GATE_NOT_BOUND','LIVE_ISSUER_NOT_CONNECTED'],
            'scope':'OFFLINE_VALIDATE_ONLY_REQUEST_PREPARATION'}
    if observed_fees is not None:
        result.update(observed_fee_schedule=observed_fees,fee_evidence_hash=fee_evidence['result_hash'])
    result['result_hash']=capture.digest(result)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--draft',type=Path,required=True)
    parser.add_argument('--market-capture',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--fee-evidence',type=Path,help='Optional raw account fee observation to replay')
    parser.add_argument('--account-ref',help='Expected account hash, required with fee evidence; no credentials')
    args=parser.parse_args(argv)
    result=build(load(args.draft),load(args.market_capture),as_of_ms=time.time_ns()//1_000_000,
                 fee_evidence=load(args.fee_evidence) if args.fee_evidence is not None else None,account_ref=args.account_ref)
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(result,stream,sort_keys=True,allow_nan=False);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    print(json.dumps({'status':result['status'],'output':str(args.output),'submission_enabled':False}))
    return 0


if __name__=='__main__':raise SystemExit(main())
