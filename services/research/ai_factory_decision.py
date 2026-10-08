"""Frozen single-asset cost/risk utility policy; pure research intent only."""
import math

POLICY_ID='DISCRETE_COST_RISK_V1'

def decide(*,prediction,variance,current,fee_bps,slip_bps,funding):
    values=(prediction,variance,current,fee_bps,slip_bps,funding)
    if any(type(v) not in (float,int) or not math.isfinite(v) for v in values) or variance<0 or fee_bps<0 or slip_bps<0:
        raise ValueError('finite cost/risk evidence required')
    cost=(fee_bps+slip_bps)/10000
    options=[]
    for weight in (0.,-.25,.25):
        if weight and 0<abs(weight-current)<.01:continue
        turnover=abs(weight-current)
        options.append({'target':weight,'weight':weight,'expected_return':prediction*weight,
                        'risk_penalty':10*variance*weight*weight,'trade_cost':turnover*cost,'funding_cost':funding*weight})
    if current and abs(current)<=.30:
        options.append({'target':None,'weight':current,'expected_return':prediction*current,
                        'risk_penalty':10*variance*current*current,'trade_cost':0.,'funding_cost':funding*current})
    for row in options:row['utility']=row['expected_return']-row['risk_penalty']-row['trade_cost']-row['funding_cost']
    best=max(options,key=lambda row:row['utility'])
    return {**best,'policy':POLICY_ID,'current_weight':current,'prediction':prediction,'variance':variance,
            'fee_bps':fee_bps,'slippage_bps':slip_bps,'reason':'HOLD_COST_BAND' if best['target'] is None else 'NO_EDGE_OR_REDUCE_RISK' if best['target']==0 else 'NET_UTILITY',
            'alternatives':options,'unit':'equity_fraction_over_four_hours'}
