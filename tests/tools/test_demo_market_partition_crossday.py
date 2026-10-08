"""An interrupted approved cut survives a new observation day unchanged."""
from copy import deepcopy
import json

import pytest

from tests.tools.test_demo_market_partition_recovery import recovery_env as recovery_env, env as env, book as book, interrupt


@pytest.fixture
def crossday_env(recovery_env,book,monkeypatch):
    return recovery_env


def test_next_day_recovery_uses_fresh_observations_without_resealing(crossday_env,monkeypatch):
    tool,value,_,controller=crossday_env
    original=interrupt(tool,monkeypatch,'executor')
    issuer=tool.ISSUER_RECORD.read_bytes()
    next_day=tool.supervisor.now_ms()+86400000
    monkeypatch.setattr(tool.supervisor,'now_ms',lambda:next_day)
    monkeypatch.setattr(tool,'fresh_candidate',lambda *_:pytest.fail('old cut was regenerated'))
    def approval(intent,operator):
        return dict(schema='DEMO_PARTITION_LOCAL_RECOVERY_APPROVAL_V1',intent_sha256=tool._digest(intent),
            candidate_sha256=intent['candidate_sha256'],operator_id=operator,approved_at_ms=tool.supervisor.now_ms(),
            purpose='COMPLETE_ORIGINAL_PARTITION_INSTALL_ONLY')
    monkeypatch.setattr(tool,'confirm_recovery',approval)
    review=tool.review_recovery(source_commit='c'*40)
    assert review['installed'] is None and not tool.ACTIVATION.exists()
    result=tool.recover_approved(source_commit='c'*40)
    assert result['installed'] is True and tool.INTENT.read_bytes()==original
    assert tool.ISSUER_RECORD.read_bytes()==issuer==tool.EXECUTOR_RECORD.read_bytes()
    assert json.loads(tool.EXECUTOR_RECORD.read_bytes())['candidate']==value
    reports=list(controller.rglob('pre-activation-revalidations/*.json'))
    assert reports and any(json.loads(p.read_bytes())['observed_day_id']==next_day//86400000 for p in reports)


@pytest.mark.parametrize('change',['wallet','stop','state','registry','time','authority'])
def test_recovery_validates_fresh_report_before_any_new_role_file(crossday_env,monkeypatch,change):
    tool,_,_,_=crossday_env
    original=interrupt(tool,monkeypatch,'audit')
    fresh=tool.fresh_revalidation
    def damaged(*args):
        report=deepcopy(fresh(*args))
        field={'wallet':'gold_initial_wallet','stop':'kill_switch_sha256','state':'full_state_sha256',
            'registry':'registry_sha256','time':'reviewed_at_ms','authority':'economic_authority'}[change]
        report[field]='4300' if change=='wallet' else (True if change=='authority' else (True if change=='time' else 'e'*64))
        return report
    monkeypatch.setattr(tool,'fresh_revalidation',damaged)
    with pytest.raises(ValueError):tool.recover_approved(source_commit='c'*40)
    assert tool.INTENT.read_bytes()==original
    assert not tool.ISSUER_RECORD.exists() and not tool.ACTIVATION.exists()


def test_elapsed_observation_deadline_after_ready_result_never_activates(crossday_env,monkeypatch):
    tool,_,_,_=crossday_env
    original=tool.installer.write_new
    def delayed(path,*args,**kwargs):
        result=original(path,*args,**kwargs)
        if path.name=='result.json':
            expired=tool.supervisor.now_ms()+40000
            monkeypatch.setattr(tool.supervisor,'now_ms',lambda:expired)
        return result
    monkeypatch.setattr(tool.installer,'write_new',delayed)
    with pytest.raises(tool.InstallIncomplete) as error:tool.install_approved(source_commit='c'*40)
    assert error.value.result['installed'] is None
    assert tool.ISSUER_RECORD.exists() and tool.EXECUTOR_RECORD.exists()
    assert not tool.ACTIVATION.exists()
