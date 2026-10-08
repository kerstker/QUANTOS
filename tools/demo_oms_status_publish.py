"""Copy a validated unverified OMS display snapshot to a separate research cache.

The protected publisher owns this operation; the console only reads its copy.
This command cannot collect receipts, resolve orders, access keys or submit.
"""
import argparse
import json
from pathlib import Path

from services.research import demo_oms_status,demo_cancel_status,demo_resident_status,demo_executor_observation,demo_issuer_status,demo_market_inputs


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--kind',choices=('oms','cancellation','resident','executor-observation','issuer','market'),default='oms')
    parser.add_argument('--symbol',choices=('BTCUSDC','BTCUSDT'))
    args=parser.parse_args(argv)
    model={'oms':demo_oms_status,'cancellation':demo_cancel_status,'resident':demo_resident_status,
        'executor-observation':demo_executor_observation,'issuer':demo_issuer_status,'market':demo_market_inputs}[args.kind]
    try:
        if (args.kind=='market')!=(args.symbol is not None):raise ValueError('DISPLAY_SYMBOL_SCOPE')
        source=args.source_dir.resolve(strict=True);output=args.output_dir.resolve()
        if source==output or source in output.parents or output in source.parents:
            raise ValueError('OMS_DISPLAY_OVERLAPPING_DIRECTORIES')
        raw=model.load_latest(source)
        if args.kind=='market' and (raw['symbol']!=args.symbol or output.name!=args.symbol):
            raise ValueError('DISPLAY_MARKET_QUOTE')
        time_key='completed_at_ms' if args.kind=='market' else 'observed_at_ms'
        if (output/'latest.json').exists() or (output/'latest.json').is_symlink():
            previous=model.load_latest(output)
            if previous[time_key]>raw[time_key]:raise ValueError('OMS_DISPLAY_TIME_REVERSED')
        model.save(output,raw)
        print(json.dumps({'status':'PUBLISHED_UNVERIFIED_DISPLAY','phase':raw['status'] if args.kind=='market' else raw['phase'],
            'source_verified':False,'submission_enabled':False,'cash_reconciled':False}))
        return 0
    except Exception:
        print(json.dumps({'status':'FAILED','reason':'OMS_DISPLAY_INPUT_OR_DESTINATION_INVALID',
            'source_verified':False,'submission_enabled':False,'cash_reconciled':False}))
        return 3


if __name__=='__main__':raise SystemExit(main())
