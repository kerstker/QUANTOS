"""Read-only optional study receipts, including unfinished and failed attempts."""
from services.console.api.forward_dashboard import _read
from services.research.ai_factory_store import FactoryStore
import re
import sqlite3


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
    input_error=False
    try:inputs=FactoryStore.profitability_inputs(root/'data/cache/ai_factory/factory.db',attempt)
    except (OSError,ValueError,sqlite3.Error):inputs=[];input_error=True
    if not directory.exists() and not inputs and not input_error:return None
    latest={}
    stages={'basis':'p3_study','information':'p4','trajectory':'p5','calibration':'p5_calibration'}
    for row in inputs:
        if row['kind'] not in stages or not re.fullmatch('[0-9a-f]{64}',row['input_hash']):raise ValueError('invalid input index')
        folder=directory/'inputs'/row['kind']/row['input_hash']
        latest[stages[row['kind']]]=folder
        row['result_status']='NOT_RUN'
        for file in ('result.json','failure.json'):
            if (folder/file).exists():
                try:
                    report=_read(folder/file);row['result_status']=report.get('status','FAILED' if file=='failure.json' else 'UNKNOWN')
                    row['trials_attempted']=report.get('trials_attempted',0)
                except (OSError,ValueError,KeyError,TypeError):row['result_status']='UNREADABLE'
                break
        if row['result_status']=='NOT_RUN' and folder.exists():row['result_status']='INCOMPLETE'
    result={'p0':None,'p1':None,'p2':None,'p3':None,'p3_study':None,'p6':None,'p4':None,'p5':None,'p5_calibration':None,'inputs':inputs,'failures':([{'stage':'inputs','reason':'INPUT_INDEX_UNAVAILABLE'}] if input_error else []),'authoritative':False}
    for stage in ('p0','p1','p2','p3','p3_study','p4','p5','p5_calibration','p6'):
        stage_directory=latest.get(stage,directory/stage)
        path=stage_directory/('status.json' if stage=='p6' else 'result.json')
        try:
            if path.exists():
                value=_read(path)
                if value.get('economic_eligible') is not False:raise ValueError('invalid economic boundary')
                result[stage]=_summary(value)
            elif (stage_directory/'failure.json').exists():
                failure=_read(stage_directory/'failure.json')
                result['failures'].append({'stage':stage,'reason':failure.get('reason','UNKNOWN')})
            elif stage_directory.exists():
                result['failures'].append({'stage':stage,'reason':'INCOMPLETE_ATTEMPT'})
        except (OSError,ValueError,TypeError,KeyError):
            result['failures'].append({'stage':stage,'reason':'UNREADABLE_STUDY_RECEIPT'})
    return result
