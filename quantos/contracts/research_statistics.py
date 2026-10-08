"""Deterministic fixed-frequency net equity; event density never sets annualization."""
from __future__ import annotations
from collections.abc import Iterable, Mapping
from decimal import Decimal
from quantos.contracts.decimal_arithmetic import canonical_decimal, ledger_decimal_context
from quantos.contracts import gold_long_statistics_budget as long_budget

DAY_MS = 86_400_000


@ledger_decimal_context()
def fixed_frequency_equity(history: Iterable[Mapping], *, start_ms: int, end_ms: int,
                           frequency_ms: int, budget=None) -> dict:
    for value in (start_ms,end_ms,frequency_ms):
        if type(value) is not int or value < 0:
            raise ValueError('grid values must be nonnegative integers')
    maximum_intervals = 100_000 if budget is None else long_budget.validate(budget)['maximum_points'] - 1
    if frequency_ms == 0 or end_ms < start_ms or (end_ms-start_ms)//frequency_ms > maximum_intervals:
        raise ValueError('invalid or oversized fixed-frequency grid')
    def observations():
        previous=None
        for row in history:
            timestamp,sequence=row['timestamp'],row['sequence']
            if type(timestamp) is not int or type(sequence) is not int or min(timestamp,sequence)<0:
                raise ValueError('invalid equity observation identity')
            key=(timestamp,sequence)
            if previous is not None and key<=previous:
                raise ValueError('equity observations must be strictly ordered')
            value=row['equity']
            if not isinstance(value,(str,Decimal)):
                raise ValueError('equity must preserve decimal precision')
            equity=Decimal(value)
            if not equity.is_finite(): raise ValueError('equity must be finite')
            previous=key
            yield timestamp,equity
    source=iter(observations())
    current=next(source,None)
    if current is None or current[0]>start_ms:
        raise ValueError('initial equity observation missing')
    points=[]
    equity=None
    prior=None
    returns=[]
    nonpositive=False
    for timestamp in range(start_ms,end_ms+1,frequency_ms):
        while current is not None and current[0]<=timestamp:
            equity=current[1]
            current=next(source,None)
        nonpositive=nonpositive or equity<=0
        net=None if prior is None or prior<=0 else equity/prior-1
        if net is not None: returns.append(net)
        points.append({'timestamp':timestamp,'equity':canonical_decimal(equity),
                       'net_return':None if net is None else canonical_decimal(net)})
        prior=equity
    # Future/tail observations remain part of the original validation contract.
    # Validate them all before returning a grid, while retaining only one row.
    for _ in source:
        pass
    sharpe=None
    status='NONPOSITIVE_EQUITY' if nonpositive else 'INSUFFICIENT_DURATION'
    if not nonpositive and len(returns)>=30 and points[-1]['timestamp']-start_ms>=30*DAY_MS:
        mean=sum(returns)/len(returns)
        variance=sum((x-mean)**2 for x in returns)/(len(returns)-1)
        status='ZERO_VARIANCE' if variance==0 else 'DIAGNOSTIC_ONLY'
        if variance>0:
            sharpe=canonical_decimal(mean/variance.sqrt()*(Decimal(365*DAY_MS)/frequency_ms).sqrt())
    return {'schema_version':'PERPETUAL_FIXED_FREQUENCY_EQUITY_V1',
            'calendar':'ELAPSED_UTC_MILLISECONDS_365_DAY_YEAR','frequency_ms':frequency_ms,
            'anchor_ms':start_ms,'points':points,'excluded_tail_ms':end_ms-points[-1]['timestamp'],
            'annualized_sharpe':sharpe,'statistics_status':status,
            'external_cash_flows':'UNSUPPORTED_NO_EXTERNAL_FLOWS_IN_SIMULATION'}
