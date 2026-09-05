"""Independent Fraction reconciliation and descriptive analysis of spot artifacts.

Reads original CSV prices and saved fills; never calls the spot account/replay.
Reuses the existing HAC statistic, without rerunning or selecting strategies.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from fractions import Fraction as F
import hashlib
import io
import json
from pathlib import Path
import zipfile

import numpy as np

from tools.btc_usdc_research import alpha_hac


def reconcile(result, prices):
    cash, quantity, fees = F(1500), F(0), F(0)
    fills = [r for r in result['orders'] if r['status']=='FILLED']
    for fill in fills:
        q, p, fee = (F(fill[k]) for k in ('quantity', 'price', 'fee'))
        assert fee == q*p/1000
        reference = prices[fill['at']]
        assert reference == F(fill['reference'])
        assert p >= reference if fill['side']=='BUY' else p <= reference
        signed = q if fill['side']=='BUY' else -q
        cash -= signed*p+fee
        quantity += signed
        fees += fee
        assert cash == F(fill['cash']) and quantity == F(fill['position'])
        assert cash >= 0 and quantity >= 0
        if result['metrics']['halted_at'] is not None and fill['at'] >= result['metrics']['halted_at']:
            # A fill may itself trigger the loss threshold through costs;
            # strictly subsequent fills must only reduce position.
            if fill['at'] > result['metrics']['halted_at']:
                assert fill['side']=='SELL'
    assert fees == F(result['metrics']['fees'])
    cash, quantity, fees, cursor = F(1500), F(0), F(0), 0
    for checkpoint in result['equity'][1:]:
        at = checkpoint['at']
        while cursor < len(fills) and (fills[cursor]['at'] < at or
              (fills[cursor]['at']==at and fills[cursor]['reason']=='LOSS_BUDGET_NEXT_QUOTE')):
            row=fills[cursor]
            q,p,cost=(F(row[k]) for k in ('quantity','price','fee'))
            signed=q if row['side']=='BUY' else -q
            cash-=signed*p+cost
            quantity+=signed
            fees+=cost
            cursor+=1
        assert F(checkpoint['cash'])==cash
        assert F(checkpoint['quantity'])==quantity
        assert F(checkpoint['fees'])==fees
        assert F(checkpoint['equity'])==cash+quantity*prices[at]
    return len(fills),len(result['equity'])-1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results',required=True,type=Path)
    parser.add_argument('--archives',required=True,type=Path)
    args=parser.parse_args()
    manifest=json.loads((args.results/'screen.json').read_text())
    runs={}
    totals=dict(runs=0,fills=0,equity_checkpoints=0,exact_fraction_reconciliation=True)
    for symbol in manifest['diagnostics']:
        group=[json.loads((args.results/(r['trial_id']+'.json')).read_text())
               for r in manifest['results'] if r.get('symbol')==symbol]
        needed={r['at'] for run in group for r in run['equity']}
        needed.update(r['at'] for run in group for r in run['orders'] if r['status']=='FILLED')
        prices={}
        for path in sorted(args.archives.glob(symbol+'-1m-*.zip')):
            metadata=json.loads(path.with_suffix('.zip.json').read_text())
            raw=path.read_bytes()
            assert hashlib.sha256(raw).hexdigest()==metadata['sha256']
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                with archive.open(archive.namelist()[0]) as stream:
                    for row in csv.reader(io.TextIOWrapper(stream)):
                        raw_time=int(row[0])
                        at=raw_time//1000 if raw_time>=10**15 else raw_time
                        if at in needed:
                            assert at not in prices
                            prices[at]=F(row[1])
        assert set(prices)==needed
        for run in group:
            fills,checkpoints=reconcile(run,prices)
            totals['runs']+=1
            totals['fills']+=fills
            totals['equity_checkpoints']+=checkpoints
            runs[(symbol,run['strategy'],run['slip_bps'],run['loss_stop'])]=run
        print('Fraction verified',symbol,flush=True)
    comparisons=[]
    for (symbol,strategy,slip,stop),run in runs.items():
        if strategy not in ('A','B','C'):
            continue
        passive=runs[(symbol,'passive25',slip,stop)]
        market=runs[(symbol,'hold100',slip,False)]
        eq=np.array([float(r['equity']) for r in run['equity']])
        base=np.array([float(r['equity']) for r in market['equity']])
        ret=eq[1:]/eq[:-1]-1
        x=base[1:]/base[:-1]-1
        daily_pnl=np.diff(eq)
        best5=np.sort(daily_pnl)[-5:].sum()
        months={}
        for i,row in enumerate(run['equity'][:-1]):
            month=datetime.fromtimestamp(row['at']//1000,timezone.utc).strftime('%Y-%m')
            months[month]=months.get(month,0.)+float(daily_pnl[i])
        comparisons.append(dict(symbol=symbol,strategy=strategy,slip_bps=slip,loss_stop=stop,
                                net_pnl=run['metrics']['net_pnl'],
                                pnl_increment_vs_passive25=run['metrics']['net_pnl']-passive['metrics']['net_pnl'],
                                alpha_vs_unhalted_market=alpha_hac(ret.tolist(),x.tolist()),
                                top5_day_pnl=float(best5),pnl_without_top5_days=float(daily_pnl.sum()-best5),
                                monthly_pnl=months,positive_months=sum(p>0 for p in months.values())))
    output=dict(verification=totals,comparisons=comparisons,
                interpretation='Descriptive HAC and concentration diagnostics, not multiple-testing-adjusted admission',
                analysis_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                hac_source_sha256=hashlib.sha256((Path(__file__).with_name('btc_usdc_research.py')).read_bytes()).hexdigest())
    with (args.results/'analysis.json').open('x') as stream:
        json.dump(output,stream,indent=2,allow_nan=False)


if __name__=='__main__':
    main()
