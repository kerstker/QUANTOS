import io
import json
import pytest
from contextlib import contextmanager
from types import SimpleNamespace


@pytest.mark.parametrize('fresh', [False, True, 'auto'])
def test_bar_worker_reuses_locked_capture_and_retains_unreconciled_result(tmp_path, monkeypatch, fresh, session=False):
    from tools import gold_strategy_worker as worker
    from live.execution import demo_gold_pilot_enrollment
    uid = tmp_path.stat().st_uid
    bundle = dict(schema='GOLD_BAR_RETAINED_EPISODE_INPUT_V1', source_commit='c'*40,
        observation_sha256='a'*64, trial_manifest_sha256='b'*64, archive_source_commit='d'*40,
        entry_decision_ms=1000)
    if fresh:
        bundle = dict(schema='GOLD_BAR_FRESH_EPISODE_INPUT_V1', source_commit='c'*40,
            trial_manifest_sha256='b'*64, archive_source_commit='d'*40, history_sha256='a'*64,
            candidate='channel', seconds=30)
    if fresh == 'auto':
        bundle['schema'] = 'GOLD_BAR_AUTO_EPISODE_INPUT_V1'
        bundle['episode_id'] = bundle.pop('history_sha256')
    calls = []
    locked = []
    engine = object()
    monkeypatch.setattr(worker, 'verify_source', lambda *a: None)
    monkeypatch.setattr(worker, 'fixed_account', lambda: (tmp_path, 'e'*64))
    monkeypatch.setattr(worker, 'require_custody', lambda: tmp_path)
    monkeypatch.setattr(worker.demo_authority, 'load_registry_for_executor', lambda: {'executor_uid': uid})
    monkeypatch.setattr(worker.os, 'geteuid', lambda: uid)
    monkeypatch.setattr(worker.os, 'getegid', lambda: uid)
    monkeypatch.setattr(worker.os, 'getgroups', lambda: [uid])
    monkeypatch.setattr(worker, 'private_pipe', lambda *a: None)
    monkeypatch.setattr(worker, 'read_frame', lambda *a, **k: json.dumps(bundle).encode())
    monkeypatch.setattr(worker.gold_bar_worker_inputs, 'load', lambda *a, **k: (calls.append('load') or engine, {'definition_hash': 'f'*64}))
    if fresh:
        from tools import gold_bar_fresh_inputs, gold_bar_cash, gold_bar_feedback
        monkeypatch.setattr(gold_bar_cash, 'close_window', lambda *a, **kw: {'status':'CAPTURED'})
        def export(*args, **kwargs):
            assert locked == [True]
            assert kwargs['bundle'] == bundle
            assert kwargs['engine'] is engine and kwargs['entry_decision_ms'] == 1000
            return dict(status='BAR_STRATEGY_FEEDBACK_RETAINED', cash_reconciled=True,
                submission_enabled=False, economic_authority=False)
        monkeypatch.setattr(gold_bar_feedback, 'export_completed', export, raising=False)
        def prepare(*args, **kwargs):
            assert kwargs['cash_account'] == 'e'*64
            calls.append('load')
            return SimpleNamespace(engine=engine, entry_decision_ms=1000, checked={'definition_hash':'f'*64},
                recheck=lambda: calls.append('load') or {'definition_hash':'f'*64})
        monkeypatch.setattr(gold_bar_fresh_inputs, 'FreshEpisode', prepare)
    monkeypatch.setattr(worker, '_open', lambda *a: dict(dispatch=object(), recovery=SimpleNamespace(pending=lambda: []), oms=object()))
    @contextmanager
    def lease(*args):
        locked.append(True)
        try:
            yield
        finally:
            locked.pop()
    monkeypatch.setattr(worker, '_preparation_lease', lease)
    monkeypatch.setattr(worker, 'capture_origin', lambda **k: calls.append('capture') or {'account_ref_sha256': 'e'*64})
    monkeypatch.setattr(worker, 'now_ms', lambda: 2000)
    monkeypatch.setattr(demo_gold_pilot_enrollment, 'enroll_requested', lambda **k: calls.append('enroll'))

    def cycle(**kwargs):
        assert kwargs['engine'] is engine and kwargs['entry_decision_ms'] == 1000
        calls.append('cycle')
        return dict(schema='GOLD_STRATEGY_CYCLE_DECISION_V1', status='STRATEGY_EPISODE_SETTLED',
                    submission_enabled=False, economic_authority=False, cash_reconciled=False)

    monkeypatch.setattr(worker.gold_bar_demo_cycle, 'prepare', cycle)

    class Channel(io.StringIO):
        def fileno(self):
            return 123

    extra = {}
    if session:
        class Sessions:
            def frame(self, root, bundle, *, source_commit, uid, account):
                episode = gold_bar_fresh_inputs.FreshEpisode(root, bundle,
                    source_commit=source_commit, uid=uid, cash_account=account)
                checked = dict(episode.checked, continuation_sha256='9'*64)
                return episode.engine, checked, episode.entry_decision_ms, lambda: dict(episode.recheck(), continuation_sha256='9'*64)
        extra['bar_sessions'] = Sessions()
    output = Channel()
    assert worker.run(source_commit='c'*40, input_fd=122, output=output, **extra) == 0
    result = json.loads(output.getvalue())
    assert calls == ['load', 'capture', 'enroll', 'cycle', 'load']
    assert result['feedback']['status'] == ('BAR_STRATEGY_FEEDBACK_RETAINED' if fresh else 'BAR_CASH_FEEDBACK_REQUIRED')
    assert result['feedback']['cash_reconciled'] is bool(fresh)
    retained = tmp_path/'strategy-decisions'/(result['decision_sha256']+'.json')
    assert json.loads(retained.read_text()) == result['decision']
    if session:
        assert result['decision']['bar_continuation_sha256'] == '9'*64

    def reject(*a, **kw):
        raise ValueError('changed input')

    monkeypatch.setattr(worker.gold_bar_worker_inputs, 'load', reject)
    if fresh:
        monkeypatch.setattr(gold_bar_fresh_inputs, 'FreshEpisode', reject)
    output = Channel()
    assert worker.run(source_commit='c'*40, input_fd=122, output=output, **extra) == 3
    assert json.loads(output.getvalue())['phase'] == 'STRATEGY_INPUT'
    assert calls.count('capture') == 1


def test_bar_worker_decides_from_session_snapshot(tmp_path, monkeypatch):
    test_bar_worker_reuses_locked_capture_and_retains_unreconciled_result(
        tmp_path, monkeypatch, fresh='auto', session=True)
