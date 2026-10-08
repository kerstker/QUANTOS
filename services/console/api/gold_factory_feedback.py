"""Project retained execution reviews without claiming authenticated execution."""
from itertools import islice
import re
import time

from services.console.api.state_artifact import VALID, read_json_artifact
from services.research.gold_execution_feedback import research_trigger


def read_feedback(root):
    directory=root/'data/cache/ai_factory/execution-feedback'
    if not directory.exists() and not directory.is_symlink():return [],False
    rows=[]
    try:
        if directory.resolve()!=directory.absolute():raise ValueError('unsafe feedback directory')
        paths=list(islice(directory.iterdir(),1001))
        if len(paths)>1000:raise ValueError('feedback scan limit')
        size=0
        for path in paths:
            if path.name.startswith('.feedback-') and path.suffix=='.tmp':continue
            if not re.fullmatch('[a-f0-9]{64}\\.json',path.name):raise ValueError('unexpected feedback')
            size+=path.lstat().st_size
            if size>16_000_000:raise ValueError('feedback byte limit')
            state,value=read_json_artifact(path,trusted_root=root)
            if state!=VALID or value.get('result_hash')!=path.stem:raise ValueError('unavailable feedback')
            identity=value['research_identity']
            decision=research_trigger({'input_sha256':identity['base_input_sha256'],
                                      'execution_feedback_sha256':path.stem},value,now=time.time_ns()//1000000)
            rows.append((value,decision))
        return rows,False
    except (OSError,ValueError,KeyError,TypeError):return [],True


def project_feedback(session,rows,unavailable,sessions):
    if unavailable:return {'status':'UNAVAILABLE','records':[]}
    records=[]
    try:
        summary=session['result']
        for value,decision in rows:
            identity=value['research_identity']
            if identity['attempt']!=session['attempt']:continue
            if (identity['trial_id']!=summary['trial_id'] or identity['candidate_id']!=summary['candidate_id']
                    or identity['report_sha256']!=summary['report_sha256']
                    or identity['base_input_sha256']!=session['config']['input_sha256']):
                raise ValueError('feedback original trial mismatch')
            records.append({'feedback_sha256':value['result_hash'],'schema':value['schema'],
                            'available_at_ms':value['available_at_ms'],'status':decision['reason'],
                            'source_verified':False,'position_flat':value.get('position_flat'),
                            'next_attempts':[{'attempt':s['attempt'],'state':s['state']} for s in sessions
                                if s['config'].get('execution_feedback_sha256')==value['result_hash']]})
        records.sort(key=lambda r:(r['available_at_ms'],r['feedback_sha256']),reverse=True)
        return {'status':'AVAILABLE' if records else 'NO_RECORD','records':records[:20]}
    except (ValueError,KeyError,TypeError):return {'status':'UNAVAILABLE','records':[]}
