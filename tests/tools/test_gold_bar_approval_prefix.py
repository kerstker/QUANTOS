from types import SimpleNamespace
import os

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_bar_episode_tail import engine, message, STEP
from tools.gold_bar_episode_tail import EpisodeTail


@pytest.mark.parametrize('damage', [None, 'definition', 'signal', 'future', 'future_gap'])
def test_approval_prefix_reads_original_episode_and_fixed_tail(tmp_path, monkeypatch, damage):
    from tools import gold_strategy_approval_request as approval, gold_bar_fresh_inputs as fresh
    original, entry = engine(monkeypatch)
    runtime = {'schema': 'GOLD_BAR_AUTO_EPISODE_INPUT_V1'}
    tail = EpisodeTail(tmp_path, bundle_sha256=_digest(runtime), engine=original, entry_decision_ms=entry)
    raw, at = message(1001*STEP, '90')
    tail.append(raw, received_at_ms=at)
    if damage == 'future_gap':
        tail.gap(received_at_ms=at+100, reason='fixture')
    _, prefix, _ = tail.snapshot()
    decision = dict(bar_continuation_sha256=prefix, strategy_definition_sha256='d'*64,
        bar_signal_sha256=_digest(original.signal_at(entry)), observed_at_ms=at+1)
    calls = []
    def episode(root, bundle, **kwargs):
        assert kwargs['read_only'] is True and 'cash_account' not in kwargs
        calls.append(1)
        return SimpleNamespace(engine=original, entry_decision_ms=entry, recheck=lambda: {'original':True})
    monkeypatch.setattr(fresh, 'FreshEpisode', episode)
    if damage == 'definition':
        decision['strategy_definition_sha256'] = 'e'*64
    elif damage == 'signal':
        decision['bar_signal_sha256'] = 'e'*64
    elif damage == 'future':
        decision['observed_at_ms'] = entry
    def check():
        return approval.review_bar_prefix(root=tmp_path, runtime=runtime,
            decision=decision, source_commit='c'*40, uid=os.getuid())
    before = {p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    if damage:
        with pytest.raises(ValueError):
            check()
    else:
        assert check()['snapshot_sha256'] == prefix
        raw, later = message(1002*STEP)
        tail.append(raw, received_at_ms=later)
        assert check()['snapshot_sha256'] == prefix
        before = {p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        check()
    assert before == {p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert calls
