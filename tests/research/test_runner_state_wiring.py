import os
from pathlib import Path
import subprocess
import sys


def test_runner_and_console_use_same_explicit_state_from_other_cwd(tmp_path):
    repo=Path(__file__).resolve().parents[2]
    state=tmp_path/'state';cwd=tmp_path/'unrelated';cwd.mkdir()
    env={**os.environ,'PYTHONPATH':str(repo),'QUANTOS_STATE_ROOT':str(state)}
    env.pop('QUANTOS_REQUIRE_EXISTING_RUNNER_TOKEN',None)
    code='''from services.research_runner import main as runner
from services.console.api import research_run_views as views
from pathlib import Path
assert Path(runner._RUNS_DB)==Path(views._RUNS_DB)
assert Path(runner._AUDIT_DB).parent==Path(views._RUNS_DB).parent
assert Path(runner._TOKEN_FILE).is_relative_to(Path(views._RUNS_DB).parents[2])
'''
    result=subprocess.run([sys.executable,'-c',code],cwd=cwd,env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert not (cwd/'data').exists() and not (cwd/'key').exists()
