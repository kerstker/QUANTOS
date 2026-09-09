"""Bounded read-only projection of portfolio research artifacts."""
from services.console.api.forward_dashboard import _read


def portfolio_payload(directory):
    result_path=directory/'result.json';status_path=directory/'status.json'
    if not result_path.exists() and not status_path.exists():return None
    value=_read(result_path if result_path.exists() else status_path)
    if value.get('authoritative') is not False or value.get('economic_eligible') is not False:
        raise ValueError('invalid portfolio research boundary')
    fields=('schema','status','stage','reason','decision','selected','baseline','components','inner_trials_attempted',
            'plan_hash','model_hash','comparisons','timings','quote','authoritative','economic_eligible','observed_at_ms')
    result={k:value[k] for k in fields if k in value}
    result['artifact_hash']=value['result_hash']
    forward_path=directory/'forward/status.json'
    cost_path=directory.parents[2]/'profitability'/directory.parent.name/'portfolio-forward/status.json'
    result['forward']=_read(forward_path) if forward_path.exists() else None
    result['forward_cost']=_read(cost_path) if cost_path.exists() else None
    for row in (result['forward'],result['forward_cost']):
        if row is not None and row.get('authoritative') is not False:raise ValueError('invalid portfolio observation boundary')
    for name in ('inner_trials','outer_cases'):
        result[name]=[]
        if len(value.get(name,[]))>(16 if name=='inner_trials' else 6):raise ValueError('portfolio projection exceeds budget')
        for row in value.get(name,[]):
            selected={k:row[k] for k in ('name','spec','status','reason','fee_bps','slippage_bps','trial_id','elapsed_ms') if k in row}
            if 'metrics' in row:
                keys=('net_pnl','drawdown','fees','funding','fills')+(('equity',) if name=='outer_cases' else ())
                selected['metrics']={k:row['metrics'][k] for k in keys if k in row['metrics']}
            result[name].append(selected)
    return result
