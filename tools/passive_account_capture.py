"""One-shot public rule/ADV bootstrap followed by raw passive market capture."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

from services.governance.json_integrity import load_json_without_duplicate_keys
from services.execution.passive_account_environment import endpoint, DAY_MS, SCHEMA
from services.research.passive_execution_study import prepare_input, prepare_account
from tools.passive_market_capture import capture as capture_market, now_ms
from tools.perpetual_replay_research import source_identity
from tools.profitability_study import write_new


async def _fetch(url,timeout):
    child=await asyncio.create_subprocess_exec(sys.executable,
        str(Path(__file__).with_name('profitability_public_http.py')),url,str(timeout),'--include-raw',
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    try:
        raw,_=await asyncio.wait_for(child.communicate(),timeout)
        if child.returncode or len(raw)>12000000:raise ValueError('bounded metadata child failed')
        row=load_json_without_duplicate_keys(raw)
        if row.get('ok') is not True or not isinstance(row.get('raw_utf8'),str):
            raise ValueError('public metadata response unavailable')
        return row['raw_utf8']
    finally:
        if child.returncode is None:
            child.kill();await child.wait()


def capture(output,symbol,*,initial_collateral,fee_rate,max_order_notional,duration_seconds=30):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    started=now_ms();day=started//DAY_MS*DAY_MS
    plan={'schema':'PASSIVE_ACCOUNT_CAPTURE_PLAN_V1','source':source_identity(),'symbol':symbol,
        'started_at_ms':started,'duration_seconds':duration_seconds,'metadata_requests':5,
        'per_request_deadline_seconds':10,'initial_collateral':initial_collateral,'fee_rate':fee_rate,
        'max_order_notional':max_order_notional,'economic_eligible':False,'scope':'PUBLIC_DATA_ONLY'}
    write_new(output/'preregistration.json',plan)
    try:
        def request(kind,name):
            url=endpoint(kind,symbol,day);requested=now_ms()
            raw=asyncio.run(_fetch(url,10));received=now_ms()
            row={'kind':kind,'endpoint':url,'requested_at':requested,'received_at':received,
                'raw_utf8':raw,'response_sha256':hashlib.sha256(raw.encode()).hexdigest()}
            write_new(output/f'metadata-{name}.json',row)
            # write_new adds an artifact seal; the replay binds the raw response.
            return {k:v for k,v in row.items() if k!='result_hash'}
        before={kind:request(kind,kind) for kind in ('exchange','premium','funding','candles')}
        market=capture_market(output/'market',symbol,duration_seconds=duration_seconds,max_bytes=2000000)
        after=request('exchange','exchange-after')
        environment={'schema':SCHEMA,'symbol':symbol,'before':before,'exchange_after':after}
        write_new(output/'account-environment.json',environment)
        environment.pop('result_hash',None)
        raw_tape=load_json_without_duplicate_keys((output/'market/raw-tape.json').read_bytes())
        source={'tape':raw_tape,'actions':[],'account':{'environment':environment,
            'initial_collateral':initial_collateral,'fee_rate':fee_rate,'max_order_notional':max_order_notional}}
        serialized=json.dumps(source,ensure_ascii=False,allow_nan=False).encode()
        if len(serialized)>4000000:raise ValueError('combined input byte budget exceeded')
        # Preserve failed input too; validation cannot erase a capture attempt.
        with (output/'input.json').open('xb') as handle:handle.write(serialized)
        tape,_=prepare_input(source);account=prepare_account(source,tape)
        result={'status':'PUBLIC_PASSIVE_ACCOUNT_INPUT_READY','symbol':symbol,
            'start_ms':tape.start_ms,'end_ms':tape.end_ms,'market_status':market['status'],
            'environment_hash':account['manifest']['environment_hash'],
            'actual_venue_fees':None,'economic_eligible':False,'actions_count':0}
        write_new(output/'result.json',result);return result
    except BaseException as exc:
        write_new(output/'failure.json',{'reason':type(exc).__name__,'economic_eligible':False})
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--symbol',choices=('BTCUSDC','BTCUSDT'),required=True)
    parser.add_argument('--duration',type=int,default=30)
    for name in ('initial-collateral','fee-rate','max-order-notional'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    print(json.dumps(capture(args.output,args.symbol,duration_seconds=args.duration,
        initial_collateral=args.initial_collateral,fee_rate=args.fee_rate,max_order_notional=args.max_order_notional)))


if __name__=='__main__':main()
