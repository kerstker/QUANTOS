"""Replay frozen client actions through the existing canonical simulation engine."""
from dataclasses import asdict,dataclass
from decimal import Decimal
from bisect import bisect_left
from types import MappingProxyType

from services.execution.perpetual_simulation import OrderIntent,CancelOrder
from services.execution.perpetual_simulation import run_linear_perpetual_simulation
from services.execution.perpetual_ledger_v2 import canonical_decimal
from services.research.ai_factory_store import digest


def _identity(schedule):
    return [{'at_ms':at,'kind':type(action).__name__,
             'action':{k:canonical_decimal(v) if isinstance(v,Decimal) else v
                       for k,v in asdict(action).items()}} for at,action in schedule]


@dataclass(frozen=True)
class FixedActions:
    action_hash:str
    actions:object
    cancel_latency_ms:int

    def __call__(self,context):
        return self.actions.get(context.timestamp,())


def fixed_action_strategy(schedule,frames,*,cancel_latency_ms=0):
    if type(cancel_latency_ms) is not int or not 0<=cancel_latency_ms<=60000:
        raise ValueError('bounded cancellation latency required')
    schedule=tuple(schedule);times=[f.timestamp for f in frames]
    if not times or times!=sorted(set(times)) or not 0<len(schedule)<=2000:
        raise ValueError('bounded actions and increasing frames required')
    previous=-1;orders=set();actions={}
    for at,action in schedule:
        if type(at) is not int or at<previous or at not in times:
            raise ValueError('client action needs its original chronological frame')
        previous=at
        if isinstance(action,OrderIntent):
            if action.order_id in orders:raise ValueError('duplicate client order')
            orders.add(action.order_id);due=at
        elif isinstance(action,CancelOrder):
            if action.order_id not in orders:raise ValueError('cancel references unknown order')
            due=at+cancel_latency_ms
        else:raise ValueError('only frozen order/cancel actions accepted')
        index=bisect_left(times,due)
        if index==len(times):raise ValueError('trace ends before action delivery; extend observation window')
        actions.setdefault(times[index],[]).append(action)
    return FixedActions(digest(_identity(schedule)),MappingProxyType({k:tuple(v) for k,v in actions.items()}),cancel_latency_ms)


def normalized_trajectory(result,schedule,action_hash):
    """Keep simulator receipts separate from any observed venue trajectory."""
    if action_hash!=digest(_identity(schedule)):raise ValueError('fixed action digest mismatch')
    intents=[(at,a) for at,a in schedule if isinstance(a,OrderIntent)]
    symbols={a.instrument_id for _,a in intents}
    if len(symbols)!=1 or not symbols<={'BTCUSDC','BTCUSDT'}:raise ValueError('single supported contract required')
    states={'OPEN':'ACK','PARTIALLY_FILLED':'PARTIAL','FILLED':'FILLED',
            'CANCELLED':'CANCELLED','EXPIRED':'CANCELLED','REJECTED':'REJECTED'}
    rows=[]
    for at,intent in intents:
        events=[{'at_ms':at,'state':'SENT','filled':'0'}]
        for event in result['order_events']:
            if event['order_id']!=intent.order_id or event['status']=='CANCEL_REJECTED':continue
            events.append({'at_ms':event['timestamp'],'state':states[event['status']],
                           'filled':canonical_decimal(abs(Decimal(event['cumulative_filled_quantity']))),
                           'simulator_reason':event['reason']})
        if len(events)==1:raise ValueError('missing simulator order receipt')
        # Client send time is distinct from delayed venue cancellation.
        # Venue fills at a shared timestamp precede client actions; cancellation
        # acknowledgements at that timestamp follow their client request.
        for sent,action in schedule:
            if not isinstance(action,CancelOrder) or action.order_id!=intent.order_id:continue
            index=next((i for i,e in enumerate(events) if e['at_ms']>sent or
                        e['at_ms']==sent and e['state']=='CANCELLED'),len(events))
            prior=events[:index]
            if any(e['state'] in ('FILLED','CANCELLED','REJECTED') for e in prior):continue
            events.insert(index,{'at_ms':sent,'state':'CANCEL_SENT','filled':prior[-1]['filled'] if prior else '0'})
        rows.append({'id':intent.order_id,'side':'BUY' if intent.quantity_delta>0 else 'SELL',
                     'quantity':canonical_decimal(abs(intent.quantity_delta)),'events':events})
    return {'action_hash':action_hash,'symbol':next(iter(symbols)),'orders':rows,
            'client_cancel_requests':[{'order_id':a.order_id,'at_ms':at} for at,a in schedule if isinstance(a,CancelOrder)],
            'cancel_responses':[e for e in result['order_events'] if e['status']=='CANCEL_REJECTED'],
            'source':'CANONICAL_SIMULATOR_NOT_VENUE','economic_eligible':False}


def run_fixed_actions(*,schedule,cancel_latency_ms=0,**simulation):
    if 'strategy' in simulation or 'strategy_identity' in simulation:
        raise ValueError('fixed client actions own the strategy and its identity')
    schedule=tuple(schedule)
    strategy=fixed_action_strategy(schedule,simulation['frames'],cancel_latency_ms=cancel_latency_ms)
    result=run_linear_perpetual_simulation(**simulation,strategy=strategy,
        strategy_identity={'kind':'FIXED_CLIENT_ACTIONS_V1','action_hash':strategy.action_hash,
                           'cancel_latency_ms':cancel_latency_ms,
                           'same_frame_order':'VENUE_FILL_BEFORE_CANCEL_DELIVERY'})
    return {'status':'TECHNICAL_VALIDATION_ONLY','economic_eligible':False,
            'simulation':result,'trajectory':normalized_trajectory(result,schedule,strategy.action_hash),
            'calibration_objective':'QUANTITY_TIME_STATE_ERROR_NOT_PNL'}
