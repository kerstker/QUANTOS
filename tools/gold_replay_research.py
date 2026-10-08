"""Fixed-path offline gold signal task for the guarded research runner."""
import hashlib
import json
from pathlib import Path
from quantos.runtime_paths import runtime_state_root

from services.research.gold_trade_observation import MAX_BYTES, load_document
from tools.gold_signal_replay import archive_replay, verify_archive

INPUTS=Path('data/cache/gold_replay/inputs')
OUTPUT=Path('data/cache/gold_replay/results')


def run_replay(params):
    if (not isinstance(params,dict) or set(params)!={'attempt'}
            or type(params['attempt']) is not int or not 1<=params['attempt']<=1000):
        raise ValueError('INVALID_GOLD_ATTEMPT')
    root = runtime_state_root()
    source=root/INPUTS/(str(params['attempt'])+'.json')
    output=root/OUTPUT
    if (source.resolve()!=source.absolute() or not source.is_file()
            or output.resolve()!=output.absolute()):
        raise ValueError('LINKED_GOLD_RESEARCH_PATH')
    with source.open('rb') as file:raw=file.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('GOLD_INPUT_TOO_LARGE')
    output.mkdir(parents=True,exist_ok=True)
    destination=output/str(params['attempt'])
    archive_replay(raw,destination)
    verified=verify_archive(destination,include_report=True)
    report=verified['report']
    if report['status']!='RESEARCH_SIGNALS_ONLY':
        raise ValueError('GOLD_REPLAY_INCOMPLETE_SEE_RETAINED_ARCHIVE')
    manifest=load_document((destination/'manifest.json').read_bytes())
    summary={'schema':'GOLD_REGISTERED_REPLAY_V1','authoritative':False,
        'evidence_status':'TECHNICAL_VALIDATION_ONLY','attempt':params['attempt'],
        'trial_id':verified['trial_id'],'rule_id':report['rule_id'],
        'input_hash':hashlib.sha256(raw).hexdigest(),
        'result_hash':manifest['report_sha256'],
        'decision_count':len(report['records']),
        'signal_count':sum(r['decision']['intent']=='LONG_RESEARCH_SIGNAL' for r in report['records']),
        'net_pnl':None,'preregistered':False}
    print('Retrospective gold replay retained for attempt '+str(params['attempt']))
    return summary
