"""Independent fraction/VectorBT reference. Imports no QUANTOS engine code."""
from __future__ import annotations
import argparse
from fractions import Fraction
import importlib.metadata
import json
from pathlib import Path
import platform


def reference(case,initial,fee,engine):
    if engine=='fraction':
        cash=Fraction(initial);position=Fraction(0);costs=Fraction(0);rows=[]
        for price,quantity in zip(case['prices'],case['fills'],strict=True):
            p,q=Fraction(price),Fraction(quantity)
            cost=abs(q)*p*Fraction(fee)
            cash-=q*p+cost;position+=q;costs+=cost
            rows.append({'equity':str(cash+position*p),'position':str(position),'fees':str(costs)})
        return rows
    if engine!='vectorbt': raise ValueError('unknown reference engine')
    import numpy as np
    import pandas as pd
    import vectorbt as vbt
    prices=pd.Series([float(p) for p in case['prices']])
    portfolio=vbt.Portfolio.from_orders(prices,size=np.array([float(q) for q in case['fills']]),
        price=prices,init_cash=float(initial),fees=float(fee),direction='both',freq='1s')
    fees=portfolio.orders.records
    running=np.zeros(len(prices))
    for _,order in fees.iterrows(): running[int(order['idx'])]+=float(order['fees'])
    return [{'equity':str(e),'position':str(p),'fees':str(f)}
            for e,p,f in zip(portfolio.value(),portfolio.assets(),np.cumsum(running),strict=True)]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--engine',choices=['fraction','vectorbt'],required=True)
    args=parser.parse_args(argv)
    fixture=json.loads(args.cases.read_text())
    result={'engine':args.engine,'python':platform.python_version(),'architecture':platform.machine(),
        'dependencies':{d.metadata['Name']:d.version for d in importlib.metadata.distributions()},
        'cases':{c['name']:reference(c,fixture['initial_collateral'],fixture['fee_rate'],args.engine)
                 for c in fixture['cases']}}
    with args.output.open('x') as f: json.dump(result,f,sort_keys=True,indent=2)
    return 0


if __name__=='__main__': raise SystemExit(main())
