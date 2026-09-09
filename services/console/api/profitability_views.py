"""Read-only optional study receipts, including unfinished and failed attempts."""
from services.console.api.forward_dashboard import _read


def _metrics_summary(value):
    result={k:value[k] for k in ('score','net_pnl','drawdown','fees','funding','fills','decision_policy') if k in value}
    feedback=value.get('execution_feedback')
    if isinstance(feedback,dict):
        result['execution_feedback']={k:feedback[k] for k in ('profitability_attribution','reconciliation','fill_count','order_count') if k in feedback}
    return result


def _summary(value):
    # Keep the file hash as a reference, not as a digest of this smaller projection.
    result={k:v for k,v in value.items() if k not in ('result_hash','metrics','trials','cases','pairs','rows')}
    result.update(artifact_hash=value['result_hash'],projection='CONSOLE_SUMMARY')
    if 'metrics' in value:result['metrics']=_metrics_summary(value['metrics'])
    for key in ('trials','cases'):
        if key in value:
            result[key]=[{**{k:v for k,v in row.items() if k!='metrics'},
                          **({'metrics':_metrics_summary(row['metrics'])} if 'metrics' in row else {})} for row in value[key]]
    if 'pairs' in value:
        result['pairs']=[{k:row[k] for k in ('fee_bps','slippage_bps','effect')} for row in value['pairs']]
    return result


def profitability_payload(root, attempt):
    if attempt is None:return None
    directory=root/'data/cache/profitability'/str(attempt)
    if not directory.exists():return None
    result={'p0':None,'p1':None,'p2':None,'p3':None,'p4':None,'p5':None,'failures':[],'authoritative':False}
    for stage in ('p0','p1','p2','p3','p4','p5'):
        path=directory/stage/'result.json'
        try:
            if path.exists():
                value=_read(path)
                if value.get('economic_eligible') is not False:raise ValueError('invalid economic boundary')
                result[stage]=_summary(value)
            elif (directory/stage/'failure.json').exists():
                failure=_read(directory/stage/'failure.json')
                result['failures'].append({'stage':stage,'reason':failure.get('reason','UNKNOWN')})
            elif (directory/stage).exists():
                result['failures'].append({'stage':stage,'reason':'INCOMPLETE_ATTEMPT'})
        except (OSError,ValueError,TypeError,KeyError):
            result['failures'].append({'stage':stage,'reason':'UNREADABLE_STUDY_RECEIPT'})
    return result
