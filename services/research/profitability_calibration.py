"""Fixed public-trace calibration contracts; imported traces never prove venue origin."""
from decimal import Decimal
from services.execution.perpetual_simulation import OrderIntent,CancelOrder,SimulationFrame
from services.research.profitability_trajectory import _orders,compare_trajectories

PROFILES=((0,0),(25,25),(100,100))


def decode_actions(rows):
    if not isinstance(rows,list) or not 1<=len(rows)<=1000:raise ValueError('bounded client action list required')
    actions=[]
    for row in rows:
        if not isinstance(row,dict) or type(row.get('at_ms')) is not int or row['at_ms']<0:
            raise ValueError('client action clock required')
        if row.get('kind')=='CANCEL' and set(row)=={'at_ms','kind','id'}:
            action=CancelOrder(row['id'])
        elif row.get('kind')=='ORDER' and set(row)<={'at_ms','kind','id','symbol','quantity','limit_price','reduce_only'}:
            if not isinstance(row['quantity'],str) or len(row['quantity'])>64:raise ValueError('decimal action quantity required')
            limit=row.get('limit_price')
            if limit is not None and (not isinstance(limit,str) or len(limit)>64):raise ValueError('decimal limit required')
            action=OrderIntent(row['id'],row['symbol'],Decimal(row['quantity']),
                               limit_price=Decimal(limit) if limit is not None else None,
                               reduce_only=row.get('reduce_only',False))
        else:raise ValueError('unsupported frozen action')
        actions.append((row['at_ms'],action))
    if [at for at,_ in actions]!=sorted(at for at,_ in actions):raise ValueError('unordered client actions')
    return actions


def action_frames(frames,actions,*,cancel_latency_ms):
    """Add action delivery clocks without inventing new market observations."""
    indexed={f.timestamp:f for f in frames};start=min(indexed);end=max(indexed)
    for at,action in actions:
        due=at+cancel_latency_ms if isinstance(action,CancelOrder) else at
        if not start<=at<=end or due>end:raise ValueError('action delivery outside captured window')
        indexed.setdefault(at,SimulationFrame(at))
        indexed.setdefault(due,SimulationFrame(due))
    return tuple(indexed[t] for t in sorted(indexed))


def prefix(trajectory,cutoff):
    _orders(trajectory)
    rows=[]
    for row in trajectory['orders']:
        events=[event for event in row['events'] if event['at_ms']<=cutoff]
        if events:rows.append({**row,'events':events})
    return {**trajectory,'orders':rows}


def calibration_score(report):
    return (len(report['unmatched_orders']),Decimal(report['quantity_absolute_error']),
            report['unmatched_events'],report['state_time_absolute_error_ms'])


def compare_prefix(observed,simulated,cutoff):
    report=compare_trajectories(prefix(observed,cutoff),prefix(simulated,cutoff))
    if not report['paired_orders']:raise ValueError('no paired calibration orders before cutoff')
    return report
