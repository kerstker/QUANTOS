"""Original durable producer witnesses cannot become optional read metadata."""
import json
import os
import pytest
from tests.tools.test_registered_research_run import registered_case as registered_case, execute
from quantos.contracts import registered_research_run as contract
from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader


def cut(f):
    return ProjectResearchEvidenceReader(f['root']/'project',registry_database=f['root']/'registry.db')()


def test_running_before_invocation_cannot_downgrade_a_lost_original_ledger(registered_case):
    f=registered_case;f['prepare']();destination=f['directories']['one']
    pid=os.fork()
    if pid==0:
        try:
            original=f['run']._put
            def interrupted(path,*args,**kwargs):
                value=original(path,*args,**kwargs)
                if path.name=='running.json':os._exit(77)
                return value
            f['run']._put=interrupted
            execute(f);os._exit(99)
        except BaseException:
            import traceback;traceback.print_exc();os._exit(98)
    _,status=os.waitpid(pid,0)
    assert os.waitstatus_to_exitcode(status)==77
    assert (destination/'archive/running.json').is_file()
    assert not (destination/'invocation.json').exists()
    original=cut(f)
    assert all(row['status']=='RUNNING' for row in original['attempts'])
    for path in (destination/'archive').glob('trials.db*'):path.unlink()
    with pytest.raises(ValueError,match='LEDGER_MISSING'):
        execute(f)
    with pytest.raises(ValueError):
        cut(f)


@pytest.mark.parametrize('leaf',['running.json','manifest.json'])
def test_native_completed_witness_must_match_original_plan(registered_case,leaf):
    f=registered_case;f['prepare']();execute(f)
    assert cut(f)['unfinished_attempts']==[]
    path=f['directories']['one']/'archive'/leaf
    value=json.loads(path.read_bytes())
    target=value['manifest'] if leaf=='running.json' else value
    assert target['input_sha256']!='0'*64
    target['input_sha256']='0'*64
    path.write_bytes(contract.canonical(value))
    with pytest.raises(ValueError):cut(f)
