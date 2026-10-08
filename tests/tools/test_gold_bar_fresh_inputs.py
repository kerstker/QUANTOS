import hashlib
import json
import shutil
from types import SimpleNamespace

import pytest

from tests.tools.test_gold_bar_worker_inputs import setup


@pytest.mark.parametrize('automatic', [False, True])
@pytest.mark.parametrize('partial', [False, True])
def test_fresh_episode_captures_once_and_rechecks_retained_bytes(tmp_path, monkeypatch, partial, automatic):
    from tools import gold_bar_fresh_inputs as fresh
    _, retained, template, trial, source, _ = setup(tmp_path, monkeypatch)
    history_raw = b'{}'
    hid = hashlib.sha256(history_raw).hexdigest()
    history = tmp_path/'strategy-bar-history'/hid
    history.mkdir(parents=True)
    for name in ('response.json', 'receipt.json'):
        (history/name).write_bytes(history_raw)
    for path in (history.parent, history):
        path.chmod(0o700)
    for path in history.iterdir():
        path.chmod(0o600)
    definition = dict(definition_hash='d'*64,
        archive_files={'manifest.json':retained['trial_manifest_sha256']},
        source={'commit':retained['archive_source_commit']})
    monkeypatch.setattr(fresh, 'PreparedBarDefinition', lambda *a, **kw:
        SimpleNamespace(definition=lambda *a, **kw: definition))
    monkeypatch.setattr(fresh, 'restore_engine', lambda *a, **kw: object())
    calls = []
    openings = []
    from tools import gold_bar_cash
    monkeypatch.setattr(gold_bar_cash, 'open_window', lambda *a, **kw: openings.append(kw['observation_exists']))
    # This is an observation wiring fixture (engine/transport already isolated).
    # Native pre-signal capture/custody/replay is exercised by the AUTO cash fixture.
    origin_opening = {'origin': {'completed_at_ms': -5001}}
    monkeypatch.setattr(gold_bar_cash, 'open_account_origin', lambda *a, **kw: origin_opening)
    monkeypatch.setattr(gold_bar_cash, 'load_account_opening', lambda *a, **kw: origin_opening)
    monkeypatch.setattr(gold_bar_cash, 'account_opening_usage', lambda *a, **kw: None)
    async def observe(*args, **kw):
        assert openings == [False]
        calls.append(1)
        assert kw['first_close'] is True and kw['protected_network'] is True
        target = kw['output']
        shutil.copytree(template, target)
        if partial:
            (target/'result.json').unlink()
            raise ValueError('INTERRUPTED')
        (target/'registration.json').write_text(json.dumps(dict(first_close=True,
            network_mode='PROTECTED_DEMO_NETWORK_V1', seconds=30)))
        (target/'selected-decisions.jsonl').write_text(json.dumps({'received_at_ms':1000})+'\n')
        return {'status':'FORWARD_SIGNALS_RECORDED'}
    monkeypatch.setattr(fresh, 'observe', observe)
    bundle = dict(schema='GOLD_BAR_FRESH_EPISODE_INPUT_V1', source_commit='c'*40,
        trial_manifest_sha256=retained['trial_manifest_sha256'], archive_source_commit=retained['archive_source_commit'],
        history_sha256=hid, candidate='channel', seconds=30)
    if automatic:
        from tools import gold_bar_auto_history
        bundle['schema'] = 'GOLD_BAR_AUTO_EPISODE_INPUT_V1'
        bundle['episode_id'] = bundle.pop('history_sha256')
        monkeypatch.setattr(gold_bar_auto_history, 'load', lambda *a, **kw: history)
    def load():
        return fresh.FreshEpisode(tmp_path, bundle, source_commit='c'*40, uid=tmp_path.stat().st_uid, cash_account='f'*64)
    if partial:
        with pytest.raises(ValueError, match='INTERRUPTED'):
            load()
        with pytest.raises((ValueError, FileNotFoundError)):
            load()
    else:
        episode = load()
        assert episode.entry_decision_ms == 1000
        assert episode.recheck() == episode.checked
        assert load().checked == episode.checked
        before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        reviewed = fresh.FreshEpisode(tmp_path, bundle, source_commit='c'*40, uid=tmp_path.stat().st_uid, read_only=True)
        assert reviewed.checked == episode.checked
        assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        (episode.directory/'live-signals.jsonl').write_bytes(b'changed')
        with pytest.raises(ValueError, match='BAR_FRESH_CHANGED'):
            episode.recheck()
    assert calls == [1]
    assert openings == [False, True]
