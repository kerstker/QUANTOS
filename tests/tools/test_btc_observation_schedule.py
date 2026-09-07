from pathlib import Path
from tools import btc_observation_schedule as worker


def test_no_freeze_is_explicitly_not_configured(tmp_path):
    result=worker.advance(tmp_path,now_ms=123000)
    assert result['status']=='NOT_CONFIGURED'
    assert result['attempts']==[]


def test_one_attempt_failure_does_not_hide_other_results(tmp_path,monkeypatch):
    for attempt in (1,2):
        path=tmp_path/f'data/cache/btc_forward/{attempt}/freeze.json'
        path.parent.mkdir(parents=True);path.write_text('{}')
    called=[]
    def run(params):
        called.append(params)
        if params['attempt']==1:raise ValueError('retained failure')
        return {'status':'WAITING_WINDOW','observed_at_ms':123000}
    monkeypatch.setattr(worker,'run_forward',run)
    result=worker.advance(tmp_path,now_ms=123000)
    assert called==[{'attempt':1,'action':2},{'attempt':2,'action':2}]
    assert result['status']=='ERROR'
    assert result['attempts'][0]['error']=='retained failure'
    assert result['attempts'][1]['status']=='WAITING_WINDOW'
    assert len(list((tmp_path/'data/cache/btc_forward_schedule/history').glob('*.json')))==1


def test_symlinked_attempt_is_rejected(tmp_path):
    base=tmp_path/'data/cache/btc_forward';base.mkdir(parents=True)
    outside=tmp_path/'outside';outside.mkdir();(outside/'freeze.json').write_text('{}')
    (base/'1').symlink_to(outside,target_is_directory=True)
    result=worker.advance(tmp_path,now_ms=123000)
    assert result['status']=='ERROR'
    assert result['attempts']==[]


def test_scheduler_does_not_hold_attempts_in_process_mutex(tmp_path,monkeypatch):
    from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
    path=tmp_path/'data/cache/btc_forward/1';path.mkdir(parents=True);(path/'freeze.json').write_text('{}')
    def run(params):
        with acquire_runtime_lock(path/'worker.lock') as acquired:
            assert acquired, 'the scheduler must not lock out its own attempt'
        return {'status':'WAITING_WINDOW','observed_at_ms':123000}
    monkeypatch.setattr(worker,'run_forward',run)
    assert worker.advance(tmp_path,now_ms=123000)['status']=='OBSERVING'
