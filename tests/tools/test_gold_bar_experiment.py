import json

import pytest

from tests.tools.test_gold_bar_cost_replay import inputs
from tools import gold_bar_experiment as experiment


def test_archive_and_offline_recompute_bind_all_three_candidates(tmp_path, monkeypatch):
    args = inputs()
    monkeypatch.setattr(experiment, 'source_identity', lambda: args['release_identity'])
    target = tmp_path / 'trial'
    result = experiment.archive(args, target)
    assert result['status'] == 'TECHNICAL_COMPUTATION_COMPLETE'
    assert len(result['trial_ids']) == 3
    assert experiment.verify(target)['status'] == 'RECOMPUTED_NOT_INDEPENDENTLY_APPROVED'
    with pytest.raises(FileExistsError):
        experiment.archive(args, target)
    report = json.loads((target / 'report.json').read_bytes())
    report['winner'] = 'channel'
    (target / 'report.json').write_text(json.dumps(report))
    with pytest.raises(ValueError):
        experiment.verify(target)


def test_failure_is_retained_in_original_trial_ledger(tmp_path, monkeypatch):
    args = inputs()
    monkeypatch.setattr(experiment, 'source_identity', lambda: args['release_identity'])
    def fail(**kwargs):
        raise ValueError('SYNTHETIC_INPUT_FAILURE')
    monkeypatch.setattr(experiment, 'compare_public', fail)
    target = tmp_path / 'failed'
    with pytest.raises(ValueError):
        experiment.archive(args, target)
    manifest = json.loads((target / 'manifest.json').read_bytes())
    assert manifest['terminal'] == 'FAILED'
    assert manifest['error_type'] == 'ValueError'
    from services.research.trial_ledger import TrialLedger
    with TrialLedger(str(target / 'trials.db'), create=False, read_only=True) as ledger:
        ledger.verify()
        statuses = [ledger.load(t).status for t in manifest['trial_ids']]
    assert statuses == ['FAILED'] * 3
    with pytest.raises(ValueError, match='TERMINAL_FAILURE'):
        experiment.resume_archive(args, target)


def test_review_request_binds_three_trials_and_rejects_changed_request(tmp_path, monkeypatch):
    from tools.gold_bar_review import review_request
    args = inputs()
    monkeypatch.setattr(experiment, 'source_identity', lambda: args['release_identity'])
    target = tmp_path / 'review'
    archived = experiment.archive(args, target)
    request = review_request(target)
    assert request['trial_ids'] == archived['trial_ids']
    assert len(request['trial_history']) == 3
    assert request['status'] == 'INDEPENDENT_REVIEW_REQUIRED'
    assert request['economic_eligible'] is False
    assert request == review_request(target)
    saved = target / 'independent-request.json'
    saved.write_text('{}')
    with pytest.raises(ValueError, match='CHANGED'):
        review_request(target)


@pytest.mark.parametrize('crash_point', ['running.json', 'report.json', 'first_passed'])
def test_killed_worker_recovers_original_three_trials(tmp_path, monkeypatch, crash_point):
    import subprocess
    import sys
    from pathlib import Path
    from services.research.trial_ledger import TrialLedger
    target = tmp_path / 'interrupted'
    code = '''
import os,sys
from pathlib import Path
from tools import gold_bar_experiment as e
from tests.tools.test_gold_bar_cost_replay import inputs
args=inputs();e.source_identity=lambda:args['release_identity']
write=e._write
def crash_write(path,value):
 result=write(path,value)
 if path.name==sys.argv[2]:os._exit(23)
 return result
e._write=crash_write
append=e.TrialLedger.append
def crash_append(self,record):
 result=append(self,record)
 if sys.argv[2]=='first_passed' and record.status=='PASSED':os._exit(23)
 return result
e.TrialLedger.append=crash_append
e.archive(args,Path(sys.argv[1]))
'''
    child = subprocess.run([sys.executable, '-c', code, str(target), crash_point],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=20)
    assert child.returncode == 23, child.stderr
    assert not (target / 'manifest.json').exists()
    registration = json.loads((target / 'running.json').read_text())
    ids = registration['manifest']['trial_ids']
    with TrialLedger(str(target / 'trials.db'), create=False, read_only=True) as ledger:
        before = {tid: ledger.load(tid).events for tid in ids}
    args = inputs()
    monkeypatch.setattr(experiment, 'source_identity', lambda: args['release_identity'])
    result = experiment.resume_archive(args, target)
    assert result['trial_ids'] == ids
    assert experiment.verify(target)['result_hash'] == result['result_hash']
    with TrialLedger(str(target / 'trials.db'), create=False, read_only=True) as ledger:
        for tid in ids:
            events = ledger.load(tid).events
            assert events[:len(before[tid])] == before[tid]
            assert [e.status for e in events] == ['PLANNED', 'RUNNING', 'PASSED']


def test_recovery_cannot_write_while_original_writer_holds_lock(tmp_path, monkeypatch):
    args = inputs()
    monkeypatch.setattr(experiment, 'source_identity', lambda: args['release_identity'])
    target = tmp_path / 'locked'
    experiment.archive(args, target)
    with experiment._writer_lock(target), pytest.raises(BlockingIOError):
        experiment.resume_archive(args, target)
