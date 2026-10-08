import hashlib
import json

import pytest

from tests.tools.test_gold_bar_observe import recorded
from tools.gold_bar_journal import restore


def test_original_observer_journal_rebuilds_bars_and_identical_decisions(tmp_path, monkeypatch):
    _, out = recorded(tmp_path, monkeypatch)
    cursor, review = restore(out)
    assert len(cursor.bars) == 1001
    assert review['closed_bars'] == 1
    assert review['status'] == 'RECONSTRUCTED_NOT_INDEPENDENTLY_APPROVED'
    assert review['submission_enabled'] is False


def test_rehashed_decision_tamper_is_rejected_by_actual_recomputation(tmp_path, monkeypatch):
    _, out = recorded(tmp_path, monkeypatch)
    decision = json.loads((out / 'decisions.jsonl').read_text())
    decision['decisions'][0]['entry_side'] = 'SELL'
    raw = (json.dumps(decision) + '\n').encode()
    (out / 'decisions.jsonl').write_bytes(raw)
    summary = json.loads((out / 'summary.json').read_text())
    summary['decisions_sha256'] = hashlib.sha256(raw).hexdigest()
    (out / 'summary.json').write_text(json.dumps(summary))
    with pytest.raises(ValueError, match='DECISIONS_CHANGED'):
        restore(out)


def test_incomplete_journal_cannot_be_restored_as_complete(tmp_path, monkeypatch):
    _, out = recorded(tmp_path, monkeypatch)
    summary = json.loads((out / 'summary.json').read_text())
    summary['status'] = 'INCOMPLETE'
    (out / 'summary.json').write_text(json.dumps(summary))
    with pytest.raises(ValueError):
        restore(out)
