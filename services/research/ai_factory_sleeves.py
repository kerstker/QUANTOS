"""Actual one-way quantity ownership. No second cash or P&L ledger.

Opposing intents net before execution. They do not create two filled positions.
Ownership transfers at unchanged net quantity are internal attribution only.
"""
from decimal import Decimal,ROUND_FLOOR
from copy import deepcopy
from services.execution.perpetual_ledger_v2 import canonical_decimal,ledger_decimal_context
from services.research.ai_factory_portfolio import candidate_grid

ZERO=Decimal(0)
STEP=Decimal('.001')
CONSTRUCTION='INDEPENDENT_DECISIONS_ONE_WAY_NET_ACCOUNT_V1'
SCHEMA='QUANTOS_FACTORY_SLEEVE_PORTFOLIO_V1'


def validate_contract(contract):
    from services.research.ai_factory import candidates
    if not isinstance(contract,dict) or set(contract)!={'components','quarters','baseline_quarters'}:
        raise ValueError('frozen sleeve model contract required')
    components=contract['components']
    if not isinstance(components,list) or any(s not in candidates(2) for s in components):
        raise ValueError('registered component definitions required')
    SleeveBook([s['id'] for s in components],contract['quarters'])
    SleeveBook([s['id'] for s in components],contract['baseline_quarters'])
    if contract['baseline_quarters'].count(4)!=1:raise ValueError('frozen inner single baseline required')
    return deepcopy(contract)


def project_trace(trace,artifact_hash):
    keys=('time','positions','desired_weights','gross_desired_weight','net_desired_weight','actual_quantity','target')
    choices=('component_id','reason','raw_prediction','prediction','current_weight','target','utility')
    return {'construction':trace['construction'],'ending':trace['ending'],
        'decision_count':len(trace['decisions']),'execution_count':len(trace['execution']),
        'decisions':[{**{k:r[k] for k in keys},'choices':[{k:c[k] for k in choices if k in c} for c in r['choices']]}
            for r in trace['decisions'][-96:]],
        'execution':trace['execution'][-128:],'trace_hash':artifact_hash,
        'projection':'LAST_96_DECISIONS_128_EXECUTIONS_FULL_TRACE_IN_ARTIFACT',
        'authoritative':False,'economic_eligible':False}


class SleeveBook:
    def __init__(self,component_ids,quarters):
        grid=candidate_grid(component_ids)
        if (not isinstance(quarters,list) or any(type(q) is not int for q in quarters)
                or not any(row['quarters']==quarters for row in grid)):
            raise ValueError('registered sleeve budgets required')
        self.ids=tuple(component_ids);self.quarters=tuple(quarters)
        self.positions={i:ZERO for i in self.ids};self.desired=None

    @ledger_decimal_context()
    def propose(self,weights):
        if (len(weights)!=len(self.ids) or any(not isinstance(w,Decimal) or not w.is_finite()
                or abs(w)>Decimal(q)/16 for w,q in zip(weights,self.quarters))):
            raise ValueError('finite sleeve targets within frozen budgets required')
        self.desired=dict(zip(self.ids,weights))
        return sum(weights,ZERO)

    @ledger_decimal_context()
    def _allocate(self,quantity,magnitudes):
        if quantity==ZERO:return {i:ZERO for i in self.ids}
        total=sum(magnitudes.values(),ZERO)
        if total<=0:raise ValueError('committed quantity has no attributable sleeve')
        lots=abs(quantity)/STEP
        if lots!=lots.to_integral_value():raise ValueError('whole contract quantity lots required')
        quotas={i:lots*magnitudes[i]/total for i in self.ids}
        floors={i:int(v.to_integral_value(rounding=ROUND_FLOOR)) for i,v in quotas.items()}
        remainder=int(lots)-sum(floors.values())
        ranked=sorted(self.ids,key=lambda i:(-(quotas[i]-floors[i]),i))
        for i in ranked[:remainder]:floors[i]+=1
        sign=1 if quantity>0 else -1
        allocated={i:sign*STEP*floors[i] for i in self.ids}
        if sum(allocated.values(),ZERO)!=quantity:raise ValueError('sleeve quantity reconciliation failed')
        return allocated

    @ledger_decimal_context()
    def execution(self,event):
        value=event['quantity']
        if not isinstance(value,str):raise ValueError('exact committed quantity required')
        quantity=Decimal(value)
        if not quantity.is_finite() or quantity%STEP:raise ValueError('finite committed contract lots required')
        status=event['status'];current=sum(self.positions.values(),ZERO)
        if status=='LEDGER_STATE':
            if current!=quantity:
                # A forced liquidation/exit can reduce the ledger between decisions.
                # Unknown external increases or sign changes have no owned fill.
                if quantity and (current*quantity<=0 or abs(quantity)>abs(current)):
                    raise ValueError('unattributed ledger quantity increase')
                self.positions=self._allocate(quantity,{i:abs(v) for i,v in self.positions.items()})
        elif status in ('FILLED','NO_NET_CHANGE'):
            if self.desired is None:raise ValueError('frozen sleeve intents required before allocation')
            self.positions=self._allocate(quantity,{i:abs(w) if w*quantity>0 else ZERO for i,w in self.desired.items()})
        elif status.startswith(('REJECTED_','SKIPPED_')):
            if quantity!=current:raise ValueError('rejected order changed attributed quantity')
        else:raise ValueError('unknown sleeve execution feedback')

    @ledger_decimal_context()
    def report(self):
        desired=self.desired or {i:ZERO for i in self.ids}
        return {'component_ids':list(self.ids),'quarters':list(self.quarters),
            'positions':{str(i):canonical_decimal(v) for i,v in self.positions.items()},
            'desired_weights':{str(i):canonical_decimal(v) for i,v in desired.items()},
            'gross_desired_weight':canonical_decimal(sum(map(abs,desired.values()),ZERO)),
            'net_desired_weight':canonical_decimal(sum(desired.values(),ZERO)),
            'actual_quantity':canonical_decimal(sum(self.positions.values(),ZERO)),
            'ownership_policy':'ONE_WAY_NET_POSITION_LARGEST_REMAINDER_V1',
            'separate_sleeve_pnl_computed':False,'economic_eligible':False}
