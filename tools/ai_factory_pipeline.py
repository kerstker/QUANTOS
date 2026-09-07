"""One-click bounded research -> diagnosis -> independent package -> natural observation."""
import time
from quantos.runtime_paths import runtime_state_root
from tools.ai_quant_factory import run_factory
from tools.ai_factory_assessment import run_assessment
from tools.ai_factory_process_comparison import run_comparison
from tools.ai_factory_verification import run_verification
from tools.ai_factory_forward import run_observation
from tools.btc_factory_research import seal,verified
from tools.ai_factory_checkpoint import create_checkpoint
import hashlib
import os
from tools.cache_io import atomic_write_json


def run_pipeline(params):
    # The research entrypoint validates the bounded attempt before output path use.
    research=run_factory(params)
    if research['state']!='COMPLETED' or research['command']!='run':return research
    dest=runtime_state_root()/'data/cache/ai_factory'/str(params['attempt'])
    if (dest/'pipeline.json').exists() and verified(dest/'pipeline.json')['status']=='COMPLETED':return research
    stages=[]
    for name,worker,request in (
        ('ASSESSMENT',run_assessment,params),('PROCESS_COMPARISON',run_comparison,params),('INDEPENDENT_PACKAGE',run_verification,params),
        ('NATURAL_OBSERVATION',run_observation,{'attempt':params['attempt'],'action':1}),
        ('CHECKPOINT',checkpoint,params)):
        atomic_write_json(str(dest/'pipeline.json'),seal({'status':'RUNNING','stage':name,'completed':stages,
            'updated_ms':time.time_ns()//1000000,'authoritative':False}))
        try:
            result=worker(request)
            stages.append({'stage':name,'result_hash':result['result_hash']})
        except Exception as exc:
            atomic_write_json(str(dest/'pipeline.json'),seal({'status':'FAILED','stage':name,'completed':stages,
                'reason':type(exc).__name__,'updated_ms':time.time_ns()//1000000,'authoritative':False}))
            raise
    atomic_write_json(str(dest/'pipeline.json'),seal({'status':'COMPLETED','stage':'CHECKPOINT','completed':stages,
        'updated_ms':time.time_ns()//1000000,'authoritative':False}))
    return research


def checkpoint(params):
    root=runtime_state_root();output=root/'data/cache/ai_factory_checkpoints';output.mkdir(parents=True,exist_ok=True)
    path=output/f"attempt-{params['attempt']}.zip"
    if not path.exists():report=create_checkpoint(root,path)
    else:report={'status':'EXISTING_IMMUTABLE_CHECKPOINT'}
    latest=root/'data/cache/ai_factory_checkpoint.zip';temporary=latest.with_suffix('.partial')
    with path.open('rb') as source,temporary.open('wb') as target:
        os.chmod(temporary,0o600)
        for chunk in iter(lambda:source.read(1024*1024),b''):target.write(chunk)
        target.flush();os.fsync(target.fileno())
    os.replace(temporary,latest)
    return seal(report)
