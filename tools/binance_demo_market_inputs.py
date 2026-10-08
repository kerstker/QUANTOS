"""Four public Demo GETs for risk inputs; no credential or order endpoint."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from quantos.contracts.binance_demo import DEMO_REST_BASE
from services.research import demo_market_inputs as model
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools.binance_demo_readiness import NoRedirect,ProbeFailure

PATHS={'clock_before':'/fapi/v1/time','ticker':'/fapi/v1/ticker/24hr','klines':'/fapi/v1/klines','clock_after':'/fapi/v1/time'}


def now_ms():return time.time_ns()//1000000


def source_hash():
    root=Path(__file__).resolve().parents[1]
    names=('tools/binance_demo_market_inputs.py','services/research/demo_market_inputs.py',
        'quantos/contracts/binance_demo_clock.py','services/execution/perpetual_ledger_v2.py',
        'services/governance/json_integrity.py','services/research/demo_display_store.py',
        'services/research/demo_observation_store.py')
    return hashlib.sha256(b'\0'.join((root/name).read_bytes() for name in names)).hexdigest()


def get_response(kind,*,symbol,end_ms=None,opener=None):
    model.require(kind in PATHS and symbol in model.SYMBOLS,'MARKET_PUBLIC_REQUEST_SCOPE')
    params=[] if kind.startswith('clock') else [('symbol',symbol)]
    if kind=='klines':
        model.require(type(end_ms) is int and end_ms>61*60000 and (end_ms+1)%60000==0,'MARKET_KLINE_END')
        params.extend([('interval','1m'),('startTime',end_ms+1-61*60000),('endTime',end_ms),('limit',61)])
    else:model.require(end_ms is None,'MARKET_UNEXPECTED_END')
    url=DEMO_REST_BASE+PATHS[kind]+('?' + urllib.parse.urlencode(params) if params else '')
    request=urllib.request.Request(url,method='GET')
    try:
        with (opener or urllib.request.build_opener(NoRedirect())).open(request,timeout=6) as response:
            if response.status!=200:raise ProbeFailure('REQUEST_FAILED')
            raw=response.read(model.BODY_LIMITS[kind]+1)
    except urllib.error.HTTPError as exc:
        code=exc.code;exc.close();raise ProbeFailure('RATE_LIMITED' if code in (418,429) else 'REQUEST_FAILED') from None
    except ProbeFailure:raise
    except Exception:raise ProbeFailure('REQUEST_FAILED') from None
    if not 0<len(raw)<=model.BODY_LIMITS[kind]:raise ProbeFailure('RESPONSE_INVALID')
    return raw


def collect(*,symbol,get=None,clock_ms=None):
    model.require(symbol in model.SYMBOLS,'MARKET_SYMBOL')
    get=get or get_response;clock_ms=clock_ms or now_ms;started=clock_ms();deadline=time.monotonic()+30
    report={'schema':model.schema_for_symbol(symbol),'environment':'BINANCE_USDM_DEMO','symbol':symbol,'status':'CAPTURED',
        'source_sha256':source_hash(),'observed_at_ms':started,'completed_at_ms':started,'reads':[],'failure':None,
        'source_verified':False,'submission_enabled':False,'economic_authority':False}
    kind='worker';end=None
    try:
        for kind in model.KINDS:
            if time.monotonic()>deadline:raise ProbeFailure('CAPTURE_BUDGET_EXHAUSTED')
            sent=started if not report['reads'] else clock_ms()
            raw=get(kind,symbol=symbol,end_ms=end if kind=='klines' else None)
            received=clock_ms();report['completed_at_ms']=received
            model.require(type(raw) is bytes and 0<len(raw)<=model.BODY_LIMITS[kind],'MARKET_RESPONSE_SIZE')
            report['reads'].append({'kind':kind,'requested_at_ms':sent,'received_at_ms':received,
                'response_sha256':hashlib.sha256(raw).hexdigest(),'body':raw.decode('utf-8')})
            if kind=='clock_before':
                server=load_json_without_duplicate_keys(raw)['serverTime'];model._time(server)
                end=server//60000*60000-1
        kind='validation';model.validate(report)
    except Exception as exc:
        reason=str(exc) if isinstance(exc,ProbeFailure) and str(exc) in model.REASONS else 'INPUTS_INVALID' if kind=='validation' else 'REQUEST_FAILED'
        report.update(status='FAILED',completed_at_ms=clock_ms(),failure={'kind':kind,'reason':reason})
    return model.validate(report)


def collect_bounded(*,symbol,protected=False):
    """Terminate a stalled public read without retrying or inventing observations."""
    schema=model.schema_for_symbol(symbol)
    started=now_ms()
    names=('PATH','LANG','LC_ALL','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
        'http_proxy','https_proxy','all_proxy','no_proxy','SSL_CERT_FILE','SSL_CERT_DIR')
    command=[sys.executable,'-m','tools.binance_demo_market_inputs','--symbol',symbol,'--capture-worker']
    if protected:command.append('--protected-network')
    child_env={name:os.environ[name] for name in names if name in os.environ and (not protected or 'proxy' not in name.lower())}
    child_env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1')
    reason='REQUEST_FAILED'
    try:
        result=subprocess.run(command,cwd=Path(__file__).resolve().parents[1],
            env=child_env,
            capture_output=True,timeout=35,check=True)
        model.require(len(result.stdout)<=65536,'MARKET_WORKER_RESPONSE_SIZE')
        report=model.validate(load_json_without_duplicate_keys(result.stdout))
        model.require(report['symbol']==symbol and report['source_sha256']==source_hash() and
            started<=report['observed_at_ms']<=report['completed_at_ms']<=now_ms(),'MARKET_WORKER_IDENTITY')
        return report
    except subprocess.TimeoutExpired:
        reason='CAPTURE_BUDGET_EXHAUSTED'
    except Exception:
        pass
    return model.validate({'schema':schema,'environment':'BINANCE_USDM_DEMO','symbol':symbol,
        'status':'FAILED','source_sha256':source_hash(),'observed_at_ms':started,'completed_at_ms':now_ms(),
        'reads':[],'failure':{'kind':'worker','reason':reason},'source_verified':False,
        'submission_enabled':False,'economic_authority':False})


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol',choices=model.SYMBOLS,required=True)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--capture-worker',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--protected-network',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--archive-observations',action='store_true',help='Append public research observations to lossless daily segments')
    args=parser.parse_args(argv)
    if args.archive_observations and (args.protected_network or args.capture_worker):
        parser.error('--archive-observations is only available for the public research collector')
    if args.capture_worker:
        try:
            get=None
            if args.protected_network:
                from live.exchange import demo_network
                opener=demo_network.opener()
                get=lambda kind,**kwargs:get_response(kind,opener=opener,**kwargs)
            print(json.dumps(collect(symbol=args.symbol,get=get),separators=(',',':'),allow_nan=False));return 0
        except Exception:return 3
    if args.output_dir is None:parser.error('--output-dir is required')
    try:
        raw=collect_bounded(symbol=args.symbol,protected=args.protected_network)
        path=model.save(args.output_dir,raw,archive=True) if args.archive_observations else model.save(args.output_dir,raw)
        print(json.dumps({'status':raw['status'],'path':str(path),'assessment':model.assess(raw,as_of_ms=now_ms())}))
        return 0 if raw['status']=='CAPTURED' else 3
    except Exception:
        print(json.dumps({'status':'FAILED','reason':'MARKET_INPUT_CAPTURE_OR_PERSISTENCE','submission_enabled':False}));return 3


if __name__=='__main__':raise SystemExit(main())
