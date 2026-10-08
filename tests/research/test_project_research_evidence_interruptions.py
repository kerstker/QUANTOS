"""True native producer exits after terminal trial rows, before final handoff."""
import os
import pytest
from tests.tools.test_registered_research_run import registered_case as registered_case, execute
from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader


def cut(f):
    return ProjectResearchEvidenceReader(f['root']/'project',registry_database=f['root']/'registry.db')()


@pytest.mark.parametrize('boundary',['before_manifest','before_search_terminal'])
def test_terminal_native_rows_do_not_hide_unfinished_original_finalization(registered_case,boundary):
    f=registered_case;plan=f['prepare']();destination=f['directories']['one']
    pid=os.fork()
    if pid==0:
        try:
            original=f['run']._put
            def interrupted(path,*args,**kwargs):
                if path.name=='manifest.json' and boundary=='before_manifest':
                    os._exit(77)
                value=original(path,*args,**kwargs)
                if path.name=='manifest.json' and boundary=='before_search_terminal':
                    os._exit(77)
                return value
            f['run']._put=interrupted
            execute(f)
            os._exit(99)
        except BaseException:
            import traceback;traceback.print_exc();os._exit(98)
    _,status=os.waitpid(pid,0)
    assert os.waitstatus_to_exitcode(status)==77
    assert (destination/'outcome.json').is_file()
    assert (destination/'archive/manifest.json').exists() == (boundary=='before_search_terminal')
    assert [r['status'] for r in f['registry'].search_attempt_events('one')]==['STARTED']
    value=cut(f)
    assert all(row['status']=='PASSED' for row in value['attempts'])
    observed=value['runs'][plan['invocation_id']]
    assert observed['partial'], (boundary,value['population_coverage'],value['unfinished_attempts'],observed['partial'])
    assert value['unfinished_runs'] == [plan['invocation_id']]
    assert value['all_runs_finalized'] is False


def test_original_completed_producer_is_not_partial(registered_case):
    f=registered_case;plan=f['prepare']();execute(f)
    value=cut(f)
    assert value['runs'][plan['invocation_id']]['partial']==[]
    assert value['unfinished_attempts']==[]
    assert value['all_runs_finalized'] is True
